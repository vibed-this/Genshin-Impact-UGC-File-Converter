#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genshin Impact UGC (.gil / .gia / .mihoyobin) <-> converter JSON 双向转换器。

Python 重写版 of Nyagamon/Genshin-Impact-UGC-File-Converter (C++)，
镜像 src/Tson.cpp 的 _load_btson / _save_btson、src/Dtype.cpp 的
analyze / read_csv / write_csv、src/Json.cpp 的 JSON 读写语义。

用法:
  python gil.py -d in.gil  -o out.json [-t dtype/gil.csv]   # 解码
  python gil.py -e in.json -o out.gil                       # 编码

相对 C++ 版的行为差异 (详见 README.md):
  1. varint 按全精度 (Python 任意精度) 读写，不再截断为 int64 ——
     C++ 的 Reader::read_int 会把 10 字节 varint 的高位丢掉，
     导致 decode->encode 无法逐字节还原。
  2. dtype 类型为 unknown (EmptyData) 的字段按 base64 保留，
     C++ 版解码时直接丢弃为空字符串。
  3. 大文件 (>1MB) 编码不再失败: C++ 版因 JSON 键为负数时
     strtoull/read_csv 路径解析不一致导致 dtype 查找失败，
     报「出力ファイルが書き込めませんでした」。
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import struct
import sys
from pathlib import Path

MASK64 = (1 << 64) - 1

DEFAULT_DTYPE = Path(__file__).resolve().parent / "dtype" / "gil.csv"


# ---------------------------------------------------------------------------
# varint / 基础读取 (全精度，不截断)
# ---------------------------------------------------------------------------

class ParseError(Exception):
    pass


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """返回 (value, new_pos)。全精度无符号；最多 10 字节 (70 bit)，与 C++ 一致。"""
    v = 0
    for i in range(10):
        if pos >= len(data):
            raise ParseError("varint overruns buffer")
        b = data[pos]
        pos += 1
        v |= (b & 0x7F) << (7 * i)
        if not (b & 0x80):
            return v, pos
    # 10 字节后仍有续位: C++ 的循环上限是 i<70，之后直接停 (is_error 视情况)
    raise ParseError("varint too long")


def write_varint(v: int) -> bytes:
    if v < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def is_valid_utf8(data: bytes) -> bool:
    """镜像 Util.cpp 的 is_valid_utf8 (含对控制字符/代理区的额外限制)。"""
    # 0x09(\t) 0x0A(\n) 0x0D(\r) 允许; 0x7F 不允许 —— 照抄 C++ 表
    not_string = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
    i, n = 0, len(data)
    while i < n:
        b1 = data[i]
        if b1 <= 0x7F:
            if b1 not in not_string:
                return False
            i += 1
            continue
        if (b1 & 0xE0) == 0xC0:
            need, cp = 1, b1 & 0x1F
        elif (b1 & 0xF0) == 0xE0:
            need, cp = 2, b1 & 0x0F
        elif (b1 & 0xF8) == 0xF0:
            need, cp = 3, b1 & 0x07
        else:
            return False
        if i + need >= n:
            return False
        for k in range(1, need + 1):
            b = data[i + k]
            if (b & 0xC0) != 0x80:
                return False
            cp = (cp << 6) | (b & 0x3F)
        if need == 1 and cp < 0x80:
            return False
        if need == 2 and (cp < 0x800 or 0xD800 <= cp <= 0xDFFF):
            return False
        if need == 3 and not (0x10000 <= cp <= 0x10FFFF):
            return False
        i += need + 1
    return True


# ---------------------------------------------------------------------------
# Dtype 表 (镜像 Dtype::Node / read_csv / write_csv / analyze)
# ---------------------------------------------------------------------------

class DtypeNode:
    __slots__ = ("i32", "i64", "f32", "f64", "obj", "str", "data", "ref")

    def __init__(self) -> None:
        self.i32 = self.i64 = self.f32 = self.f64 = False
        self.obj = self.str = self.data = False
        self.ref = 0

    @classmethod
    def all_true(cls) -> "DtypeNode":
        n = cls()
        n.i32 = n.i64 = n.f32 = n.f64 = n.obj = n.str = n.data = True
        return n

    def is_int_unknown(self) -> bool:
        return self.i32 and self.i64

    def is_float_unknown(self) -> bool:
        return self.f32 and self.f64

    def is_data_unknown(self) -> bool:
        return self.obj and self.str and self.data

    def is_multiple(self) -> bool:
        return self.ref >= 2


class DtypeTable:
    def __init__(self) -> None:
        self.by_comp: dict[tuple[int, ...], DtypeNode] = {}
        self.by_text: dict[str, DtypeNode] = {}  # csv 原文路径 -> node (供 encode 精确匹配)

    # ---- read_csv ----
    @classmethod
    def from_csv(cls, text: str) -> "DtypeTable":
        t = cls()
        for line in text.splitlines():
            line = line.strip("\r\n")
            if not line or line.startswith("#"):
                continue
            row = next(csv.reader([line]))
            if len(row) < 3:
                continue
            path_text, star, typ = row[0].strip(), row[1].strip(), row[2].strip()
            node = DtypeNode()
            node.ref = 2 if star == "*" else 0
            if typ == "int":
                node.i32 = node.i64 = True
            elif typ == "int32":
                node.i32 = True
            elif typ == "int64":
                node.i64 = True
            elif typ == "float":
                node.f32 = node.f64 = True
            elif typ == "float32":
                node.f32 = True
            elif typ == "float64":
                node.f64 = True
            elif typ == "unknown":
                node.obj = node.str = node.data = True
            elif typ == "object":
                node.obj = True
            elif typ == "string":
                node.str = True
            elif typ == "data":
                node.data = True
            comps = _parse_path_text(path_text)
            key = _comp_key(comps)
            # emplace 语义: 已存在时保留先到的
            if key not in t.by_comp:
                t.by_comp[key] = node
                t.by_text.setdefault(path_text, node)
                t.by_text.setdefault("/".join(str(c) for c in comps), node)
            else:
                t.by_text.setdefault(path_text, t.by_comp[key])
                t.by_text.setdefault("/".join(str(c) for c in comps), t.by_comp[key])
        return t

    def get(self, comps: tuple[int, ...]) -> DtypeNode | None:
        return self.by_comp.get(_comp_key(comps))

    def add(self, comps: tuple[int, ...], node: DtypeNode) -> DtypeNode:
        key = _comp_key(comps)
        existing = self.by_comp.get(key)
        if existing is not None:
            return existing
        self.by_comp[key] = node
        self.by_text.setdefault("/".join(str(c) for c in comps), node)
        return node

    # ---- write_csv ----
    def to_csv(self) -> str:
        out = io.StringIO()
        out.write("# Path, Multiple keys, Type, Details\r\n")
        for key in sorted(self.by_comp):
            node = self.by_comp[key]
            out.write("/".join(str(c) for c in key))
            out.write(",")
            if node.is_multiple():
                out.write("*")
            out.write(",")
            if node.is_int_unknown():
                out.write("int")
            elif node.i32:
                out.write("int32")
            elif node.i64:
                out.write("int64")
            elif node.is_float_unknown():
                out.write("float")
            elif node.f32:
                out.write("float32")
            elif node.f64:
                out.write("float64")
            elif node.is_data_unknown():
                out.write("unknown,EmptyData")
            elif node.obj:
                out.write("object")
            elif node.str:
                out.write("string")
            elif node.data:
                out.write("data")
            else:
                out.write("data")  # 无类型标志: 兜底按 data
            out.write("\r\n")
        return out.getvalue()


def _parse_path_text(text: str) -> tuple[int, ...]:
    # 镜像 C++ read_csv 的路径解析: 只收集数字 (会忽略 '-')
    comps: list[int] = []
    cur = ""
    for ch in text:
        if ch == "/":
            if cur:
                comps.append(int(cur))
                cur = ""
        elif ch.isdigit():
            cur += ch
    if cur:
        comps.append(int(cur))
    return tuple(comps)


def _comp_key(comps: tuple[int, ...]) -> tuple[int, ...]:
    # 统一键: 负 id (C++ int64 截断) 与全精度 id 都按 64 位无符号折叠,
    # 使 dtype 查找在两种 JSON 风格下都能命中
    return tuple(c & MASK64 for c in comps)


# ---------------------------------------------------------------------------
# TLV 解析工具
# ---------------------------------------------------------------------------

def _is_error(data: bytes) -> bool:
    """镜像 Dtype::_is_error: 数据能否被完整地按 TLV 解析。"""
    pos = 0
    n = len(data)
    while pos < n:
        try:
            v, pos = read_varint(data, pos)
        except ParseError:
            return True
        wire = v & 7
        try:
            if wire == 0:
                _, pos = read_varint(data, pos)
            elif wire == 2:
                ln, pos = read_varint(data, pos)
                pos += ln
                if pos > n:
                    return True
            elif wire == 5:
                pos += 4
                if pos > n:
                    return True
            else:
                return True
        except ParseError:
            return True
    return False


# ---------------------------------------------------------------------------
# 解码 (.gil -> JSON 文档)
# ---------------------------------------------------------------------------

class Decoder:
    def __init__(self, table: DtypeTable) -> None:
        self.table = table

    # ---- Dtype::analyze ----
    def analyze(self, data: bytes, parent: tuple[int, ...] = ()) -> None:
        pos = 0
        n = len(data)
        seen: list[tuple[int, ...]] = []
        while pos < n:
            v, pos = read_varint(data, pos)
            fid, wire = v >> 3, v & 7
            path = parent + (fid,)
            seen.append(path)
            node = self.table.get(path)
            if node is None:
                node = self.table.add(path, DtypeNode.all_true())
            node.ref += 1
            if wire == 0:
                v2, pos = read_varint(data, pos)
                node.f32 = node.f64 = node.obj = node.str = node.data = False
                if v2 > 0xFFFFFFFF:
                    node.i32 = False
                elif v2 == 0xFFFFFFFF:
                    node.i64 = False
            elif wire == 2:
                ln, pos = read_varint(data, pos)
                if pos + ln > n:
                    raise ParseError("data overruns buffer")
                payload = data[pos:pos + ln]
                pos += ln
                if node.obj:
                    if not _is_error(payload):
                        if len(payload):
                            node.str = False
                            node.data = False
                        # C++ 版此处不清 int/float 标志, 导致 write_csv 把
                        # 纯 object 字段误标为 'int' —— 本实现修正之
                        node.i32 = node.i64 = node.f32 = node.f64 = False
                        self.analyze(payload, path)
                        continue
                    node.obj = False
                    node.str = True
                    node.data = True
                    node.i32 = node.i64 = node.f32 = node.f64 = False
                elif node.str or node.data or node.i32 or node.i64 or node.f32 or node.f64:
                    pass  # csv 已指定非 object 类型: 保持 (与 C++ 一致)
                if node.str and not is_valid_utf8(payload):
                    node.str = False
            elif wire == 5:
                pos += 4
                if pos > n:
                    raise ParseError("float overruns buffer")
                node.i32 = node.i64 = node.f64 = node.obj = node.str = node.data = False
            else:
                raise ParseError(f"bad wire type {wire}")
        # 镜像 analyze 末尾的 reference_count 归一
        for path in seen:
            node = self.table.by_comp[_comp_key(path)]
            node.ref = 0 if node.ref < 2 else 2

    # ---- Tson::_load_btson ----
    def load(self, data: bytes, parent: tuple[int, ...] = (), out: dict | None = None) -> dict:
        if out is None:
            out = {}
        pos = 0
        n = len(data)
        while pos < n:
            v, pos = read_varint(data, pos)
            fid_full, wire = v >> 3, v & 7
            # dtype 查找按 C++ 语义 (64 位折叠后的 id)
            path = parent + (fid_full & MASK64,)
            node = self.table.get(path)
            if node is None:
                raise ParseError(f"dtype missing for path {'/'.join(map(str, path))}")
            if wire == 0:
                v2, pos = read_varint(data, pos)
                if node.is_int_unknown() or node.i64:
                    value: int | float = v2  # 全精度保留
                elif node.i32:
                    v2 &= 0xFFFFFFFF
                    value = v2 - 0x100000000 if v2 >= 0x80000000 else v2
                else:
                    # dtype 类型缺失 (utf8 校验淘汰等): 兜底按 int 处理
                    # (C++ 版此处直接失败)
                    value = v2
            elif wire == 2:
                ln, pos = read_varint(data, pos)
                if pos + ln > n:
                    raise ParseError("data overruns buffer")
                payload = data[pos:pos + ln]
                pos += ln
                if node.is_data_unknown():
                    # data_unknown: C++ 版丢弃为 ""; 本实现按 base64 保留 (有损修复)
                    value = "base64:" + base64.b64encode(payload).decode("ascii")
                elif node.str:
                    value = "string:" + payload.decode("utf-8", errors="surrogateescape")
                elif node.data:
                    value = "base64:" + base64.b64encode(payload).decode("ascii")
                elif node.obj:
                    value = self.load(payload, parent + (fid_full,), {})
                else:
                    # dtype 类型缺失: 兜底按 base64 保留 (C++ 版此处直接失败)
                    value = "base64:" + base64.b64encode(payload).decode("ascii")
            elif wire == 5:
                if pos + 4 > n:
                    raise ParseError("float overruns buffer")
                (value,) = struct.unpack_from("<f", data, pos)
                pos += 4
            else:
                raise ParseError(f"bad wire type {wire}")
            key = str(_signed_key(fid_full))
            if node.is_multiple():
                out.setdefault(key, []).append(value)
            else:
                out[key] = value
        return out


def _signed_key(fid_full: int) -> int:
    """JSON 键: 全精度 id。超过 64 位(仅误解析垃圾字段会出现)保留原值。"""
    return fid_full


# ---------------------------------------------------------------------------
# 编码 (JSON 文档 -> .gil)
# ---------------------------------------------------------------------------

def _sort_key(key: str) -> int:
    """镜像 std::sort(strtoull(key)): 负数键折叠为 2^64-|k| (排在最后)。"""
    v = int(key)
    return v & MASK64 if v < 0 else v


def _key_varint(fid: int, wire: int) -> bytes:
    """键 varint。fid<0 模拟 strtoull 的 64 位回绕; fid>=0 全精度。"""
    v = (fid << 3) | wire
    if fid < 0:
        v &= MASK64
    return write_varint(v)


class Encoder:
    def __init__(self, table: DtypeTable) -> None:
        self.table = table

    def _infer(self, value) -> DtypeNode:
        """encode 时对 dtype 表缺失路径的兜底推断 (C++ 版无此能力, 直接失败)。"""
        node = DtypeNode()
        if isinstance(value, list):
            node.ref = 2  # 视作 multiple
            value = value[0] if value else ""
        if isinstance(value, dict):
            node.obj = True
        elif isinstance(value, str):
            if value.startswith("base64:"):
                node.data = True
            else:
                node.str = True
        elif isinstance(value, bool):
            node.i32 = node.i64 = True
        elif isinstance(value, int):
            node.i32 = node.i64 = True
        elif isinstance(value, float):
            node.f32 = True
        else:
            raise TypeError(f"cannot infer dtype for {type(value).__name__} at encode")
        return node

    def _compatible(self, node: DtypeNode, item) -> bool:
        if node.is_int_unknown() or node.i64 or node.i32:
            return isinstance(item, (int, bool))
        if node.is_float_unknown() or node.f32 or node.f64:
            return isinstance(item, (int, float))
        if node.is_data_unknown() or node.data or node.str:
            return isinstance(item, str)
        if node.obj:
            return isinstance(item, dict)
        return False

    def save(self, node: dict, path: str = "") -> bytes:
        if not isinstance(node, dict):
            raise TypeError(f"object expected at {path!r}")
        out = bytearray()
        for key in sorted(node, key=_sort_key):
            fid = int(key)
            here = f"{path}/{key}" if path else key
            entry = self.table.by_text.get(here)
            if entry is None:
                # 容错: 按数字路径再试一次 (C++ strtoull 语义)
                entry = self.table.get(tuple(int(c) for c in here.split("/")))
            value = node[key]
            if entry is None or not self._compatible(entry, value):
                # dtype 缺失, 或 dtype 与值类型不一致 (C++ 版在此直接失败):
                # 按 JSON 值推断类型并登记 (覆盖旧条目)
                comps = tuple(int(c) for c in here.split("/"))
                entry = self._infer(value)
                self.table.by_comp[_comp_key(comps)] = entry
                self.table.by_text[here] = entry
            items = value if (entry.is_multiple() and isinstance(value, list)) else [value]
            for item in items:
                out += self._write(fid, entry, item, here)
        return bytes(out)

    def _write(self, fid: int, node: DtypeNode, item, path: str) -> bytes:
        if node.is_int_unknown() or node.i64:
            v = int(item)
            if v < 0:
                v &= MASK64
            return _key_varint(fid, 0) + write_varint(v)
        if node.i32:
            return _key_varint(fid, 0) + write_varint(int(item) & 0xFFFFFFFF)
        if node.is_float_unknown():
            raise ValueError(f"unencodable dtype 'float' at {path}")
        if node.f32:
            return _key_varint(fid, 5) + struct.pack("<f", float(item))
        if node.f64:
            raise ValueError(f"unencodable dtype 'float64' at {path}")
        if node.is_data_unknown():
            s = item
            if isinstance(s, str) and s.startswith("base64:"):
                s = s[7:]
            payload = base64.b64decode(s)
            return _key_varint(fid, 2) + write_varint(len(payload)) + payload
        if node.obj:
            payload = self.save(item, path)
            return _key_varint(fid, 2) + write_varint(len(payload)) + payload
        if node.str:
            s = item
            if isinstance(s, str) and s.startswith("string:"):
                s = s[7:]
            payload = s.encode("utf-8", errors="surrogateescape")
            return _key_varint(fid, 2) + write_varint(len(payload)) + payload
        if node.data:
            s = item
            if isinstance(s, str) and s.startswith("base64:"):
                s = s[7:]
            payload = base64.b64decode(s)
            return _key_varint(fid, 2) + write_varint(len(payload)) + payload
        raise ValueError(f"unencodable dtype at {path}")


# ---------------------------------------------------------------------------
# 顶层: 文件头 / JSON 文档
# ---------------------------------------------------------------------------

def decode_file(path: str, dtype_path: str | None = None) -> dict:
    raw = Path(path).read_bytes()
    if len(raw) < 0x14:
        raise ValueError("file too small")
    data1_size, r1, r2, r3, data2_size = struct.unpack_from(">5I", raw, 0)
    if len(raw) < data1_size + 4 or len(raw) < data2_size + 0x14:
        raise ValueError("size fields inconsistent")
    payload = raw[0x14:0x14 + data2_size]
    (r4,) = struct.unpack_from(">I", raw, 0x14 + data2_size)

    table = DtypeTable.from_csv(
        Path(dtype_path).read_text(encoding="utf-8") if dtype_path
        else DEFAULT_DTYPE.read_text(encoding="utf-8"))
    dec = Decoder(table)
    dec.analyze(payload)
    tree = dec.load(payload)

    return {
        "filetype": "gil",
        "dirtype": "Unknown",
        "info": {"1": r1, "2": r2, "3": r3, "4": r4},
        "json": tree,
        "dtype_csv": table.to_csv(),
    }


def encode_file(doc: dict) -> bytes:
    csv_text = doc.get("dtype_csv")
    if csv_text:
        table = DtypeTable.from_csv(csv_text)
    else:
        table = DtypeTable.from_csv(DEFAULT_DTYPE.read_text(encoding="utf-8"))
    enc = Encoder(table)
    payload = enc.save(doc["json"])
    info = doc.get("info", {})
    r1 = int(info.get("1", 0))
    r2 = int(info.get("2", 0))
    r3 = int(info.get("3", 0))
    r4 = int(info.get("4", 0))
    head = struct.pack(">5I", len(payload) + 0x14, r1, r2, r3, len(payload))
    return head + payload + struct.pack(">I", r4)


def dump_json(doc: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="Genshin Impact UGC .gil <-> JSON converter (Python)")
    ap.add_argument("-d", dest="mode", action="store_const", const="decode", help="decode .gil -> .json")
    ap.add_argument("-e", dest="mode", action="store_const", const="encode", help="encode .json -> .gil")
    ap.add_argument("-o", "--output", required=True, help="output file")
    ap.add_argument("-t", "--dtype", help="dtype csv (decode 时默认为内置 dtype/gil.csv)")
    ap.add_argument("input")
    args = ap.parse_args()
    mode = args.mode or ("encode" if args.input.lower().endswith(".json") else "decode")
    if mode == "decode":
        doc = decode_file(args.input, args.dtype)
        dump_json(doc, args.output)
    else:
        data = encode_file(load_json(args.input))
        Path(args.output).write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())

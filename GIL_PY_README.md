# gil.py — Python 重写的 .gil 存档转换器

`gil.py` 是 [Nyagamon/Genshin-Impact-UGC-File-Converter](https://github.com/Nyagamon/Genshin-Impact-UGC-File-Converter)
（C++ 版）中 `.gil` / `.gia` 存档 ↔ 转换器 JSON 双向转换逻辑的 **Python 3 重写版**，
语义镜像 C++ 源码的 `Tson::_load_btson` / `Tson::_save_btson`、`Dtype::analyze` /
`read_csv` / `write_csv`（`src/Tson.cpp`、`src/Dtype.cpp`、`src/Json.cpp`）。

无需编译，仅依赖 Python 3.10+ 标准库；直接读取仓库自带的 `dtype/gil.csv`（解码时
自动加载，并像 C++ 版一样把分析结果合并进输出 JSON 的 `dtype_csv` 字段）。

## 用法

```bash
# 解码: .gil -> JSON
python gil.py -d 存档.gil -o output.json

# 编码: JSON -> .gil
python gil.py -e output.json -o 新存档.gil

# 显式指定 dtype 表（默认用本仓库 dtype/gil.csv）
python gil.py -d 存档.gil -o output.json -t dtype/gil.csv
```

省略 `-d/-e` 时按扩展名自动判断（`.json` → 编码，否则解码）。

输出 JSON 结构与 C++ 版一致：`filetype` / `dirtype` / `info`（大端头 4 个保留字）/
`json`（字段树，键为字段 id 十进制字符串）/ `dtype_csv`（当前 dtype 表全文）。
编码时优先使用 JSON 内嵌的 `dtype_csv`。

## 相对 C++ 版的行为差异

C++ 版有几个实际 bug，本重写版有意偏离以修复：

1. **大文件编码失败（出力ファイルが書き込めませんでした）**。
   根因：`Reader::read_int` 按 int64 读 varint，字段 id ≥ 2^63 时 JSON 键变成
   负数十进制字符串；`Dtype::read_csv` 解析路径时只收集数字（忽略 `-`），而
   `strtoull` 对 `-xxx` 返回回绕后的 2^64-xxx，两边对不上 → `_save_btson`
   找不到 dtype → 返回失败。本版编码时直接按 JSON 键原文匹配 dtype 表，
   负数键按 C++ strtoull 语义（mod 2^64）写出 varint。
2. **varint 高位截断**。C++ `read_int` 把 10 字节 varint 截断到 64 位有符号数，
   部分字节信息在 decode 阶段即丢失，导致 decode→encode 无法逐字节还原
   原存档（实测 1.1MB 蒙派存档差 1 字节）。本版 varint 按全精度
   （Python 任意精度）读写，`mp14.gil`、`zdy.gil` 均可逐字节还原。
3. **unknown（EmptyData）字段数据丢失**。C++ `_load_btson` 把 dtype 为
   `unknown` 的字段解码成空字符串（base64 编码那行被注释掉了），重编码时
   写出空数据。本版按 `base64:...` 保留原始字节。
4. **dtype 表类型误标**。C++ `Dtype::analyze` 的 Data 分支不清除 int/float
   标志，而 `write_csv` 的类型判定把 int_unknown 放在最前，导致“只出现过
   object 内容”的字段在表里被标成 `int`，编码时写坏。本版在分析时清掉
   不兼容标志。
5. **编码容错**。dtype 表缺失路径、或 dtype 类型与 JSON 值不一致时，C++ 版
   直接失败；本版按 JSON 值推断类型兜底（自定义扩展记录因此可以编码）。

对未修改的字段，本版输出与原 C++ 工具的 JSON 语义兼容；编码 C++ 版解码出的
JSON（含负数键、空 unknown 字段）也能正常进行。

## 验证结果

- `decode(mp14.gil) → encode` 与原 `mp14.gil` **逐字节一致**（1,133,292 B）。
- `decode(zdy.gil) → encode` 与原 `zdy.gil` **逐字节一致**（29,190 B）。
- 解码 → 重编码 → 再解码，JSON 输出完全一致（幂等）。
- 编码加入 11 条自定义按钮控件记录（id 0x40001000–0x4000100a、含
  “开始游戏”）的修改版 JSON 成功，重解码后 11 条记录全部在位。

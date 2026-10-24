# Genshin Impact UGC File Converter

原神（Genshin Impact）UGC 相关文件在 `.gil` / `.gia` / `.mihoyobin` ⇔ `.json` 之间互相转换的命令行工具。

> 本仓库是 [Nyagamon/Genshin-Impact-UGC-File-Converter](https://github.com/Nyagamon/Genshin-Impact-UGC-File-Converter) 的 fork，修复了浮点序列化精度丢失导致的往返不保真问题（见「与上游的差异」），并追加 CI 构建。Windows x64 版本可从 [Releases](../../releases) 下载（`UgcFileConverter-win64.zip`，静态链接，含 `dtype/` 表）。

## 用法

```
UgcFileConverter.exe [选项] 输入文件名
```

| 选项 | 说明 |
|---|---|
| `-d` | 解码模式：`.gil` / `.gia` / `.mihoyobin` → `.json` |
| `-e` | 编码模式：`.json` → `.gil` / `.gia` / `.mihoyobin` |
| `-o` | 指定输出文件名 |
| `-t` | 指定 dtype 表文件 |

未指定 `-d` / `-e` 时，按输入文件扩展名自动判断。

### 示例

解码（默认输出 `output.json`）：

```
UgcFileConverter.exe sample.gil
```

编码：

```
UgcFileConverter.exe -o mydata.gil data.json
```

注意：解码不指定 `-t` 时会按文件类型自动选择 `dtype/` 下的表（需在仓库根目录或把 `dtype/` 放在当前目录）；解码同时会把分析结果回写到该 dtype 表。

## 与上游的差异

- **修复 JSON 浮点精度丢失**：上游 `Json::save_json` 用 `%.16f`（定点 16 位小数）输出浮点，1e-12 量级的微小值只剩约 2 位有效数字，导致 `解码 → 编码` 无法还原原文件（实测一份 1.1MB 存档有 73 字节、38 个 float 字段出现漂移）。现改为 `%.17g`（17 位有效数字，可保证 float64 精确往返）。
- **验证口径**：修复后对 50 份官方教学存档（千星奇域教程附带）逐份做 `解码 → 编码` 与原文件比对，**50/50 字节级一致**（修复前 46/50）；CI 中的往返冒烟测试（`test/fixtures/minimal.gil`）覆盖该性质。

## 从源码构建

需要支持 C++17 的 g++（MinGW-w64 或 MSYS2 的 `mingw-w64-x86_64-gcc`）：

```
g++ -O2 -static -Isrc -I. -include mingw_compat.h -o UgcFileConverter.exe src/*.cpp
```

`mingw_compat.h` 提供 `memcpy_s` 垫片。Windows 之外的平台把 `-o` 目标换成对应可执行文件名即可。

CI（`.github/workflows/build.yml`）：push 到 `main` / PR 时以 MinGW64 构建并跑往返冒烟测试、上传 artifact；推送 `v*` 标签时构建并自动发布到 GitHub Releases。

## 许可证

本软件基于 MIT 许可证公开，详见 [LICENSE](LICENSE)。

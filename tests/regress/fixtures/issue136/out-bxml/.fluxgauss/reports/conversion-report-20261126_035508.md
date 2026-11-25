# FluxGauss 转换报告

**生成时间**: 2026-11-26 03:55:08  
**配置文件**: bxml.yaml  
**输出目录**: `out-bxml`

---

## 概览

| 指标 | 数量 |
|------|------|
| 转换的包 | 1 |
| 存储过程/函数 | 1 |
| 提取的 DML (MyBatis mapper) | 0 |
| 跨包调用 | 0 |
| 成功转换 | 1 |
| ⏭ 跳过（不涉及存储过程） | 0 |
| ⚠️ 未解析的跨包调用 | 2 |

---

## SQL → Java 映射

| SQL Procedure | Package | Java Service | Java Method | Stub |
|---|---|---|---|---|
| `getnodevalue` | `PKG_XMLDOM` | `XmldomService` | `getnodevalue` | ✅ |

---

## ⚠️ 未解析的跨包调用

| 调用方 | 被调用方 | 文件 | 参数 | 提示 |
|---|---|---|---|---|
| `PKG_XMLDOM.getnodevalue` | `GETNODEVALUE` | `b-xml.sql` | `pNode` | TOBEFIX: 函数/名称未解析（定义包不在 sources 或跨包同名冲突），需人工确认 |
| `PKG_XMLDOM.getnodevalue` | `GETCHILDNODES` | `b-xml.sql` | `pNode` | TOBEFIX: 函数/名称未解析（定义包不在 sources 或跨包同名冲突），需人工确认 |

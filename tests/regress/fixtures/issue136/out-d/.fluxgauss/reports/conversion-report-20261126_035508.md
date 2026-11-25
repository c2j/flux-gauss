# FluxGauss 转换报告

**生成时间**: 2026-11-26 03:55:08  
**配置文件**: d.yaml  
**输出目录**: `out-d`

---

## 概览

| 指标 | 数量 |
|------|------|
| 转换的包 | 1 |
| 存储过程/函数 | 3 |
| 提取的 DML (MyBatis mapper) | 0 |
| 跨包调用 | 0 |
| 成功转换 | 3 |
| ⏭ 跳过（不涉及存储过程） | 0 |
| ⚠️ 未解析的跨包调用 | 1 |

---

## SQL → Java 映射

| SQL Procedure | Package | Java Service | Java Method | Stub |
|---|---|---|---|---|
| `fnc_text` | `PKG_D_COERCION` | `DCoercionService` | `fncText` | ✅ |
| `fnc_decimal` | `PKG_D_COERCION` | `DCoercionService` | `fncDecimal` | ✅ |
| `prc_assign` | `PKG_D_COERCION` | `DCoercionService` | `prcAssign` | ✅ |

---

## ⚠️ 未解析的跨包调用

| 调用方 | 被调用方 | 文件 | 参数 | 提示 |
|---|---|---|---|---|
| `PKG_D_COERCION.prc_assign` | `valueOf` | `d-coercion.sql` | `vText` | TOBEFIX: 函数/名称未解析（定义包不在 sources 或跨包同名冲突），需人工确认 |

# FluxGauss 转换报告

**生成时间**: 2026-11-26 03:55:07  
**配置文件**: c.yaml  
**输出目录**: `out-c`

---

## 概览

| 指标 | 数量 |
|------|------|
| 转换的包 | 2 |
| 存储过程/函数 | 3 |
| 提取的 DML (MyBatis mapper) | 0 |
| 跨包调用 | 1 |
| 成功转换 | 3 |
| ⏭ 跳过（不涉及存储过程） | 0 |

---

## SQL → Java 映射

| SQL Procedure | Package | Java Service | Java Method | Stub |
|---|---|---|---|---|
| `prc_log` | `PKG_C_CALLEE` | `CCalleeService` | `prcLog` | ✅ |
| `prc_log` | `PKG_C_CALLEE` | `CCalleeService` | `prcLog` | ✅ |
| `prc_caller` | `PKG_C_CALLER` | `CCallerService` | `prcCaller` | ✅ |

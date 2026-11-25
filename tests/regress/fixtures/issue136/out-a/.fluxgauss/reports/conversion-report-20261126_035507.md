# FluxGauss 转换报告

**生成时间**: 2026-11-26 03:55:07  
**配置文件**: a.yaml  
**输出目录**: `out-a`

---

## 概览

| 指标 | 数量 |
|------|------|
| 转换的包 | 1 |
| 存储过程/函数 | 2 |
| 提取的 DML (MyBatis mapper) | 0 |
| 跨包调用 | 0 |
| 成功转换 | 2 |
| ⏭ 跳过（不涉及存储过程） | 0 |

---

## SQL → Java 映射

| SQL Procedure | Package | Java Service | Java Method | Stub |
|---|---|---|---|---|
| `FNC_CALC` | `REPRO` | `ReproService` | `fncCalc` | ✅ |
| `PRC_CHECK_NULL` | `REPRO` | `ReproService` | `prcCheckNull` | ✅ |

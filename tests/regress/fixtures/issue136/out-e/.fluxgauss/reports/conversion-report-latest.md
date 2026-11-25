# FluxGauss 转换报告

**生成时间**: 2026-11-26 03:55:08  
**配置文件**: e.yaml  
**输出目录**: `out-e`

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

---

## SQL → Java 映射

| SQL Procedure | Package | Java Service | Java Method | Stub |
|---|---|---|---|---|
| `prc_collection` | `PKG_E_COLLECTION` | `ECollectionService` | `prcCollection` | ✅ |

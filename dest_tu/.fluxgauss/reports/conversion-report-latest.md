# FluxGauss 转换报告

**生成时间**: 2026-06-05 10:30:01  
**配置文件**: demo-project/fluxgauss_tu.yaml  
**输出目录**: `./dest_tu`

---

## 概览

| 指标 | 数量 |
|------|------|
| 转换的包 | 2 |
| 存储过程/函数 | 14 |
| 提取的 DML (MyBatis mapper) | 16 |
| 跨包调用 | 0 |
| 成功转换 | 14 |
| ⏭ 跳过（不涉及存储过程） | 1 |

---

## SQL → Java 映射

| SQL Procedure | Package | Java Service | Java Method | Stub |
|---|---|---|---|---|
| `proc_list` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procList` | ✅ |
| `proc_main_ctl` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procMainCtl` | ✅ |
| `proc_match` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procMatch` | ✅ |
| `proc_match_account` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procMatchAccount` | ✅ |
| `proc_modify` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procModify` | ✅ |
| `proc_cancel` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procCancel` | ✅ |
| `proc_get_date` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procGetDate` | ✅ |
| `proc_get_respond_date` | `BIGFUND.PKG_2008802001_MGT` | `_2008802001MgtService` | `procGetRespondDate` | ✅ |
| `log` | `BIGFUND.PACK_LOG` | `PackLogService` | `log` | ✅ |
| `log` | `BIGFUND.PACK_LOG` | `PackLogService` | `log` | ✅ |
| `log` | `BIGFUND.PACK_LOG` | `PackLogService` | `log` | ✅ |
| `log` | `BIGFUND.PACK_LOG` | `PackLogService` | `log` | ✅ |
| `log` | `BIGFUND.PACK_LOG` | `PackLogService` | `log` | ✅ |
| `LOG_NOAUTOTRANS` | `BIGFUND.PACK_LOG` | `PackLogService` | `logNoautotrans` | ✅ |

---

## ⏭ 跳过项 — 不涉及存储过程，仅作参考

- [DDL] CREATE TABLE bigfund.DB_LOG (Table creation not converted)

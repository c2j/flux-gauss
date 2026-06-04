# FluxGauss 转换报告

**生成时间**: 2026-06-04 07:36:27  
**配置文件**: demo-project/fluxgauss_tu.yaml  
**输出目录**: `/Users/c2j/Projects/Desktop_Projects/DB/sp2java/dest_tu`


---

## 概览

| 指标 | 数量 |
|------|------|
| 输入 SQL 文件 | 5 |
| 转换的包 | 2 |
| 存储过程/函数 | 14 |
| 提取的 DML (MyBatis mapper) | 15 |
| 跨包调用 | 1 |
| 成功转换 | 14 |
| ⏭ 跳过（不涉及存储过程） | 4 |
| ❌ 解析错误 | 0 |
| ⚠️ 解析警告 | 2 |
| ⚠️ 未解析的跨包调用 | 6 |

---

## SQL → Java 映射

### `PACK_LOG.sql` → `PackLogService`

| SQL 存储过程/函数 | 类型 | Java 方法 | Mapper 方法 | 状态 | 备注 |
|-----------------|------|-----------|-------------|------|------|
| `PACK_LOG.log` | PROCEDURE | `PackLogService.log()` | — | ✅ |  |
| `PACK_LOG.log` | PROCEDURE | `PackLogService.log()` | — | ✅ |  |
| `PACK_LOG.log` | PROCEDURE | `PackLogService.log()` | — | ✅ |  |
| `PACK_LOG.log` | PROCEDURE | `PackLogService.log()` | insertLog | ✅ |  |
| `PACK_LOG.log` | PROCEDURE | `PackLogService.log()` | — | ✅ |  |
| `PACK_LOG.LOG_NOAUTOTRANS` | PROCEDURE | `PackLogService.logNoautotrans()` | insertLogNoautotrans | ✅ |  |

**生成的文件**:

- `src/main/java/cedtu/service/PackLogService.java`
- `src/main/java/cedtu/mapper/PackLogMapper.java`
- `src/main/resources/mapper/PackLogMapper.xml`
- `src/test/java/cedtu/service/PackLogServiceTest.java`

### `PKG_2008802001_MGT.sql` → `_2008802001MgtService`

| SQL 存储过程/函数 | 类型 | Java 方法 | Mapper 方法 | 状态 | 备注 |
|-----------------|------|-----------|-------------|------|------|
| `PKG_2008802001_MGT.proc_list` | PROCEDURE | `_2008802001MgtService.procList()` | selectProcList, selectProcList_1 | ✅ |  |
| `PKG_2008802001_MGT.proc_main_ctl` | PROCEDURE | `_2008802001MgtService.procMainCtl()` | — | ✅ |  |
| `PKG_2008802001_MGT.proc_match` | PROCEDURE | `_2008802001MgtService.procMatch()` | selectProcMatch, updateProcMatch, selectProcMatch_1 | ✅ |  |
| `PKG_2008802001_MGT.proc_match_account` | PROCEDURE | `_2008802001MgtService.procMatchAccount()` | — | ✅ |  |
| `PKG_2008802001_MGT.proc_modify` | PROCEDURE | `_2008802001MgtService.procModify()` | selectProcModify, updateProcModify, selectProcModify_1 | ✅ |  |
| `PKG_2008802001_MGT.proc_cancel` | PROCEDURE | `_2008802001MgtService.procCancel()` | selectProcCancel, updateProcCancel | ✅ |  |
| `PKG_2008802001_MGT.proc_get_date` | PROCEDURE | `_2008802001MgtService.procGetDate()` | selectProcGetDate | ✅ |  |
| `PKG_2008802001_MGT.proc_get_respond_date` | PROCEDURE | `_2008802001MgtService.procGetRespondDate()` | selectProcGetRespondDate, selectProcGetRespondDate_1 | ✅ |  |

**生成的文件**:

- `src/main/java/cedtu/service/_2008802001MgtService.java`
- `src/main/java/cedtu/mapper/_2008802001MgtMapper.java`
- `src/main/resources/mapper/_2008802001MgtMapper.xml`
- `src/test/java/cedtu/service/_2008802001MgtServiceTest.java`

---

## ⏭ 跳过项 — 不涉及存储过程，仅作参考

以下 SQL 语句不涉及存储过程/函数的转换，仅列出供参考。

### `DB_LOG.sql`

- 📋 **CREATE TABLE**: `DB_LOG` (行 3-15) — 表定义 — 仅作类型参考，不转换

### `PACK_LOG.sql`

- 📋 **CREATE PACKAGE (spec)**: `PACK_LOG` (行 1-46) — CREATE PACKAGE (spec) — 不涉及存储过程，仅作参考

### `PKG_2008802001_MGT.sql`

- 📋 **CREATE PACKAGE (spec)**: `PKG_2008802001_MGT` (行 1-152) — CREATE PACKAGE (spec) — 不涉及存储过程，仅作参考
- ❓ **routine_analysis**: `routine_analysis` (行 153-833) — routine_analysis — 不涉及存储过程转换

---

## 错误与警告

### ⚠️ 解析警告

以下警告不影响转换结果，仅供参考。

**`PKG_2008802001_MGT.sql`**:
- 行 199:46: Oracle-style outer join operator '(+)' is deprecated, use standard JOIN syntax instead
- 行 251:56: Oracle-style outer join operator '(+)' is deprecated, use standard JOIN syntax instead

### ⚠️ 未解析的跨包调用

以下存储过程调用了未包含在输入中的包，请在配置中添加对应的 SQL 文件。

- `PKG_2008802001_MGT.proc_match -> proc_sth_accnt_log`
- `PKG_2008802001_MGT.proc_match_account -> pkg_sth_accnt.proc_sth_set_accnt_info`
- `PKG_2008802001_MGT.proc_modify -> proc_sth_accnt_log`
- `PKG_2008802001_MGT.proc_modify -> pkg_sth_accnt.proc_sth_set_accnt_info`
- `PKG_2008802001_MGT.proc_cancel -> proc_sth_accnt_log`
- `PKG_2008802001_MGT.proc_cancel -> pkg_sth_accnt.proc_sth_set_accnt_info`

---

## 📋 数据库对象依赖

以下存储过程引用了数据库表/视图，集成测试运行前需确保这些对象已在目标数据库中创建。

### `PACK_LOG.sql` → `PackLogService`

依赖的表/视图: `db_log`

| 存储过程 | 引用的表/视图 |
|----------|--------------|
| `PACK_LOG.LOG_NOAUTOTRANS` | `DB_LOG` |
| `PACK_LOG.log` | `DB_LOG` |

### `PKG_2008802001_MGT.sql` → `_2008802001MgtService`

依赖的表/视图: `dat_clr_cash_dtl`, `dat_trustee_acnt_detail`, `dat_zl_batchpayment`, `par_sys_plan`, `prm_sth_payback_accnt_date`, `tmp_batchpay_submit`

| 存储过程 | 引用的表/视图 |
|----------|--------------|
| `PKG_2008802001_MGT.proc_cancel` | `dat_clr_cash_dtl` |
| `PKG_2008802001_MGT.proc_get_date` | `prm_sth_payback_accnt_date` |
| `PKG_2008802001_MGT.proc_get_respond_date` | `dat_clr_cash_dtl`, `dat_trustee_acnt_detail`, `dat_zl_batchpayment`, `par_sys_plan`, `tmp_batchpay_submit` |
| `PKG_2008802001_MGT.proc_list` | `dat_clr_cash_dtl`, `dat_trustee_acnt_detail` |
| `PKG_2008802001_MGT.proc_match` | `dat_clr_cash_dtl`, `prm_sth_payback_accnt_date` |
| `PKG_2008802001_MGT.proc_modify` | `dat_clr_cash_dtl`, `prm_sth_payback_accnt_date` |

---

*报告由 FluxGauss v1.0.0 自动生成*

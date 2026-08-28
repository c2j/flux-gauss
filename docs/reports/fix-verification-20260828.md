# v3 轮修复验证报告（main 修复 #87/#88/#89 + ogsql-parser 0.10.1）

**验证日期**: 2026-08-28
**验证基线**: flux-gauss main（含修复 `69b2ef3`/`b8e53b4`/`991dcde`）+ ogsql-parser 0.10.1（`c85e6cc`，本地未提交升级）
**验证对象**: ogagila（22 SQL / 43 过程）+ fastaas（49 SQL / 70 过程）× Python / Rust 双引擎
**目标**: 验证 #78/#79/#80/#81/#83/#84 修复效果

---

## 1. 结论摘要

| 项 | 结果 |
|----|------|
| #78 Rust itest-schema | ✅ **已修复**：0 DROP + IF NOT EXISTS + 系统对象过滤；集成测试无 workaround 直接跑，setup 零失败 → **已关闭** |
| #79 Map 返回类型 | ✅ **已修复**：标量函数返回 BigDecimal/Integer/Boolean → **已关闭** |
| #80 `__SQLSTATE__` | ✅ **已修复**：全部异常块含声明 → **已关闭** |
| #81 RAISE_APPLICATION_ERROR | ✅ **已修复**：双引擎生成 `throw new BusinessException(...)`，未解析调用 12→0 → **已关闭** |
| #84 EXTRACT(EPOCH) | ✅ **已修复**：合法毫秒差计算，vT0 类型同步修正 → **已关闭** |
| #83 OUT 参数/日期混用 | ⚠️ **部分修复**：null 安全强制转换已修；AtomicReference 传值、LocalDate/Timestamp→Date、interval 算术仍在 → 保持 OPEN（已补 v3 证据） |

## 2. 四组验证结果

编译前置说明：ogagila 双引擎需 2 处 reviewer 补丁（#90 AtTimeZone dict-dump——已立 issue 未修；触发器 `return new`——未立 issue，已并入 #103）。fastaas 双引擎**零补丁**。

| 项目/引擎 | 迁移 | 编译 | 单元测试 | 集成测试 |
|-----------|------|------|----------|----------|
| ogagila / Python | ✅ 43 过程 / 0 解析错误 | 补丁后 ✅ | 29/53 (1F/12E/11S) | 17/43 (21E/5S) |
| ogagila / Rust | ✅ 43 过程 | 补丁后 ✅ | 34/43 (9E) | **22/43（无 workaround，setup 零失败）** |
| fastaas / Python | ✅ 70 过程 | ✅ **零补丁** | **76/76 (100%)** | **70/70 (100%)** |
| fastaas / Rust | ✅ 70 过程 | ✅ **零补丁** | **70/70 (100%)** | **70/70 (100%)** |

### 与 v2 轮对比

| 指标 | v2（修复前） | v3（修复后） | 变化 |
|------|-------------|-------------|------|
| ogagila py 编译需补丁数 | 28 处 | ~10 处（#90 + 残余清单） | ↓ 64% |
| ogagila ru 编译需补丁数 | 52 处 | ~40 处（含掩蔽效应暴露） | ↓ 部分修复 |
| ogagila py 单测 | 29/44 (65.9%) | 29/53 (54.7%)* | 测试数 +9（新增校验 throw 用例） |
| ogagila ru 单测 | 35/43 (81.4%) | 34/43 (79.1%) | ≈持平 |
| ogagila ru 集成 | 26/43（workaround 后） | 22/43（**无 workaround**） | setup 缺陷清零 |
| fastaas 双引擎 | 100% | **100%** | 无回归 |

*ogagila py 单测通过率下降是**统计口径变化**：#81 修复引入 9 个新测试（校验 throw 路径），占位数据触发 throw 造成假失败（见 #104），非修复回退。

## 3. 修复逐项验证证据

### #78（Rust itest-schema）
- `grep -c "DROP TABLE" itest-schema.sql` → **0**（修复前 48）；`CREATE TABLE IF NOT EXISTS` → 44 条
- `pg_index`/`pg_partition`/`public`/`dw` 不再出现
- ogagila ru 集成测试**未应用任何 workaround**，43 个测试全部通过 schema setup（v2 时 43/43 死于 statement #37）
- fastaas ru 同样干净（0 DROP / 93 IF NOT EXISTS）

### #79（标量返回类型）
`_PublicService` 生成：`getCustomerBalance → BigDecimal`、`inventoryHeldByCustomer → Integer`、`inventoryInStock → Boolean`。4 个 ClassCastException 消失。

### #80（__SQLSTATE__）
全部异常处理块含 `String __SQLSTATE__ = "";` 声明，13 处「找不到符号」消失。

### #81（RAISE_APPLICATION_ERROR）
```java
// RAISE_APPLICATION_ERROR(-20030, ...)
throw new BusinessException(String.valueOf("build_screen_today: p_mode must be REPLACE or SWAP, got " + pMode));
```
Python 12 处未解析调用清零；Rust EtlCore 含 5 处 throw。

### #84（EXTRACT EPOCH）
`vT0` 类型正确（Timestamp），EPOCH 计算合法：`(now - vT0) / 1000.0 * 1000` 四舍五入。AdsService 编译通过、测试通过。

### #83（部分修复）
NULL→primitive 强转已修；`Long→AtomicReference`（DwdService:118/153）、`LocalDate→Date`、`Integer→String`、interval 算术仍需补丁 → 证据已补至 #83，保持 OPEN。

## 4. 本轮新发现（已立 issue）

| Issue | 内容 |
|-------|------|
| **#103** | 双引擎 ogagila 残余类型转换缺陷合并清单（重复 text 参数、Timestamp/LocalDate→Date、null→primitive、RETURN NEXT 无收集、tgOp 未声明、触发器 `return new` 非法 Java、水位线时间戳算术） |
| **#104** | 测试生成器占位数据触发新增校验 throw（#81 修复的副作用；需生成可通过校验的域数据） |
| （既有）#90/#91/#92 | AtTimeZone dict-dump、_actual_target、声明回滚——v3 轮依旧复现，保持 OPEN |

## 5. 待办建议

1. **ogsql-parser 0.10.1 升级尚未提交**（本地 stash/工作区状态，rev=c85e6cc）——建议与 #90（AtTimeZone 转换器实现）一起合入：升级解析器但不实现 AtTimeZone 会让 ogagila 类目标从「静默丢弃」变「编译失败」
2. #103 修复后目标：ogagila 双引擎 `mvn compile` 零 reviewer 补丁通过
3. #104 修复后目标：ogagila 单测假失败清零，恢复真实通过率度量
4. fastaas 黄金集状态保持 100%，继续作为防回归基线

## 附录：产物与命令

```bash
# 迁移（/tmp 中性 CWD + OGSQL_BIN 指向 0.10.1）
OGSQL_BIN=<ogsql 0.10.1> python3 converter/flux_gauss.py -c <config_v3> --skip-validate
./target/release/fluxgauss -c <config_v3> --skip-validate
# 验证
cd dest_*_v3 && mvn compile && mvn test && DB_PASSWORD=... mvn verify -Pintegration
```

产物：`/tmp/dest_{ogagila,fastaas}_{py,ru}_v3/`；配置：`demo-project/fluxgauss_*` 衍生的 v3 副本。

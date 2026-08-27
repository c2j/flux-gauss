# ogagila SQL 双引擎迁移验证报告

**验证日期**: 2026-08-27
**迁移对象**: 子模块 `lib/ogagila/sqls` 下的存储过程 SQL（openGauss Pagila 数仓分层项目）
**验证目标**: Python 引擎（v0.6.27）与 Rust 引擎（v0.6.27）在「迁移过程、编译、单元测试、集成测试」四个维度的功能正确性
**环境**: macOS (Apple Silicon) / Python 3.12 (miniforge) / Rust 1.97 / Java 17 + Maven 3.9 / ogsql-parser 0.8.32 / 本地 openGauss 7.0.0-RC1 容器（pagila 库，含 dw schema 全量数据）

---

## 1. 结论摘要

| 维度 | Python 引擎 | Rust 引擎 | 备注 |
|------|------------|-----------|------|
| 迁移过程 | ✅ 完成（43 过程提取，41 成功 + 2 stub） | ✅ 完成（43 过程提取，42 成功 + 1 stub） | 两引擎过程覆盖率一致（100%） |
| 编译 | ❌ 28 处错误（6/10 Service 文件）→ 手工修复后通过 | ❌ 52 处错误（9/10 Service 文件）→ 手工修复后通过 | **Rust 原始错误数是 Python 的 1.9 倍** |
| 单元测试 | 29/44 通过（65.9%） | 35/43 通过（81.4%） | **Rust 单元测试通过率更高** |
| 集成测试 | 16/43 通过（37.2%） | 26/43 通过（60.5%，修复 schema 生成缺陷后） | Rust 受 itest-schema 生成缺陷拖累；修复后反超 |

**总体判定**：
- **迁移过程**：两引擎均能完整解析 ogagila 的 `CREATE OR REPLACE PACKAGE` 语法（Oracle 兼容风格），过程提取率 100%。
- **生成代码质量**：两引擎均不能**直接编译通过**，但 Rust 的编译错误主要是「未声明变量/类型强转」等机械性问题，修复后**单元测试通过率更高**（81.4% vs 65.9%）；Python 的错误更多涉及**类型系统缺陷**（`Map<String,Object>` 返回类型错误、OUT 参数原子引用误用），导致运行时 ClassCastException 和 NPE 比例更高。
- **关键缺陷**：Rust 引擎存在 **itest-schema.sql 生成逻辑 bug**（remote 模式仍生成 `DROP TABLE ... CASCADE`，且系统表 `pg_index`/`pg_partition`/schema `public`/`dw` 未被过滤），导致全部 43 个集成测试在 setup 阶段失败；修复该 bug 后集成测试通过率反超 Python。
- **共同局限**：ogsql-parser 0.8.32 不支持 `AT TIME ZONE` 表达式、`ON DUPLICATE KEY UPDATE`、`CREATE AGGREGATE`；`RAISE_APPLICATION_ERROR` 被误报为「未解析跨包调用」（两引擎各 12 处）。

---

## 2. 迁移对象与准备

### 2.1 源文件清单（22 个，迁移范围内）

| 类别 | 文件 | 内容 |
|------|------|------|
| 基础 DDL | `ddl/schema.sql`、`ddl/schema-jsonb.sql`、`ddl/init-gaussdb-schema.sql` | Pagila 15 表 + JSONB 表 |
| DW DDL | `dw/ddl/00~06`（7 个） | 数仓旁路表（etl_run_log、dim_*、dwd_*、dws_*、ads_*、rpt_*） |
| 存储过程 | `program/functions.sql` | 10 函数 + 1 聚合 |
| DW 存储过程 | `dw/program/00-pkg-etl-core` ~ `08-pkg-disclose`（9 个） | 8 个 PKG_* 包（ETL_CORE/DIM/DWD/DQ/ORCH/DWS/ADS/DISCLOSE）+ ADS 视图 |
| 触发器/视图 | `program/triggers.sql`、`program/views.sql`、`dw/program/06-ads-views.sql` | 非过程语句（迁移时跳过） |

**排除项**：`init_data/*`（纯数据 INSERT/COPY，约 100MB，非过程）、`dw/tests/*`（QA 验收脚本）。

### 2.2 预处理

ogagila SQL 包含 psql 客户端指令 `\set ON_ERROR_STOP on`（非 SQL 语句），ogsql 无法解析。验证时将其剥离（`sed '/^\\set /d'`），其余内容保持原样。这是迁移管线的常规清洗步骤，两引擎同等对待。

### 2.3 ogsql 0.8.32 语法验证（剥离 `\set` 后）

| 结果 | 文件 | 错误原因 |
|------|------|----------|
| ✅ VALID | 15/21 | — |
| ❌ 2 错误 | `dw/program/00-pkg-etl-core.sql` | `AT TIME ZONE` 表达式（line 99/110） |
| ❌ 4 错误 | `dw/program/02-pkg-dwd.sql` | `AT TIME ZONE` ×2、`FOR-IN-SELECT` 内 AT TIME ZONE ×2 |
| ❌ 1 错误 | `dw/program/04-pkg-orch.sql` | `ON DUPLICATE KEY UPDATE NOTHING` |
| ❌ 1 错误 | `program/functions.sql` | `CREATE AGGREGATE`（line 258） |
| ❌ 1 错误 | `dw/ddl/01-infra-tables.sql`、`06-rpt-tables.sql` | `ON DUPLICATE KEY UPDATE` |

> 注：`AT TIME ZONE` 错误仅使**包含该表达式的语句**降级，包体其余过程仍被完整提取（已验证 `pkg_etl_core` 7 个过程全部提取）。实际迁移使用 `--skip-validate` 跳过前置校验。

---

## 3. 迁移过程结果

### 3.1 概览对比

| 指标 | Python | Rust |
|------|--------|------|
| 转换的包 | 10 | 10 |
| 存储过程/函数 | 43 | 43 |
| 提取的 DML（mapper 方法） | 91 | 85 |
| 跨包调用 | 74 | 74 |
| 成功转换 | 41 | 42 |
| Stub（需人工审查） | 2 | 1 |
| 跳过（非过程语句） | 300 | 48 |
| 解析错误 | 10 | （报告未单独统计） |
| 未解析跨包调用 | 12 | 12 |
| TODO 待处理 | 28 | （报告未统计） |

- **DML 数量差异**（91 vs 85）：Python 对 `EXECUTE IMMEDIATE` 动态 SQL 重建出更多 mapper 方法；Rust 将部分动态 SQL 直接折叠为静态调用。
- **Stub 差异**：
  - Python：`pkg_dq.run_all`（复杂多子查询 SQL，编译检查失败）、`dw.trg_snapshot_immutable`（触发器变量 TG_OP，Java 无等价物）
  - Rust：`pkg_etl_core.new_run_id`（`clock_timestamp()` 未实现，生成 TODO）
- **共同的「未解析跨包调用」**：全部 12 处均为 `RAISE_APPLICATION_ERROR` —— 两引擎均未将 openGauss 的错误抛出内建函数映射为 Java 异常，只生成了注释占位。

### 3.2 迁移过程判定

两引擎迁移过程**均可完成**（exit 0），包结构、过程名、参数映射正确。差异集中在生成代码的**编译与运行时质量**，见下节。

---

## 4. 编译验证

### 4.1 原始编译结果（mvn compile）

| 引擎 | 唯一错误位置 | 错误总次数 | 涉及 Service 文件 |
|------|------------|-----------|------------------|
| Python | 28 | 56 | 6/10（_Public、Disclose、Dwd、Dw、EtlCore、Orch） |
| Rust | 52 | 104 | 9/10（仅 Ads 不含语法错误，但含类型错误） |

### 4.2 错误类别分布

**Python 引擎（28 处）**：
- `Map<String,Object>` 返回类型强转失败（BigDecimal/Integer/Boolean → Map）— 函数返回类型推断缺陷（_PublicService 5 处）
- 日期/时间类型混用：`String → Timestamp`、`Timestamp → Date`、`Timestamp + "1 month"` 字符串拼接（EtlCore/Disclose/Dwd/Orch）
- OUT 参数原子引用误用：`AtomicReference<Long>` 直接赋值数值、`((Number) oRows.get()).longValue()` 空指针（Dwd/Orch）
- 重复局部变量 `text`（_PublicService GroupConcat）
- `EXTRACT(EPOCH FROM (clock_timestamp() - v_t0))` 生成畸形表达式（AdsService，已单独手工修复）

**Rust 引擎（52 处）**：
- **`__SQLSTATE__` 未声明**（系统性问题，5 个文件 13 处）— 异常处理块引用未初始化变量
- 日期/时间类型混用：`Timestamp → Date`、`LocalDate → Date`、`long → Timestamp`（Disclose/Dwd/Orch/EtlCore）
- `null → long/int` 基本类型拆箱失败（Disclose/Dq/Dwd/Orch 8 处）
- 重复参数 `text`（_PublicService GroupConcat）
- `double → BigDecimal` 精度转换（AdsService）
- 缺失返回语句、`return new` 语法错误（_PublicService lastUpdated 触发器函数）

### 4.3 编译判定

**两引擎均不能开箱即用**。Rust 原始编译错误数是 Python 的 1.9 倍，但错误性质更机械（未声明变量、基本类型拆箱），而 Python 的错误涉及类型系统设计缺陷（返回类型推断为 `Map<String,Object>`）。

为测量测试通过率，按「最小修复原则」对生成代码做人工修正（不动方法签名、mapper 调用、SQL、测试断言），修正后两引擎 `mvn compile` 均 **BUILD SUCCESS**。修复清单见附录 A。

---

## 5. 单元测试验证

命令：`mvn test`（Mockito + JUnit 5）

### 5.1 汇总

| 引擎 | 测试总数 | 通过 | 失败 | 错误 | 跳过 | 通过率 |
|------|---------|------|------|------|------|--------|
| Python | 44 | 29 | 1 | 12 | 2 | **65.9%** |
| Rust | 43 | 35 | 0 | 8 | 0 | **81.4%** |

### 5.2 分 Service 明细

| Service | Python (通过/总) | Rust (通过/总) |
|---------|------------------|----------------|
| _PublicService | 5/10 | 8/9 |
| AdsService | 1/1 | 1/1 |
| DimService | 5/5 | 5/5 |
| DiscloseService | 0/4 | 0/4 |
| DqService | 2/2 | 2/2 |
| DwdService | 4/6 | 6/6 |
| DwService | 1/1 | 1/1 |
| DwsService | 5/5 | 5/5 |
| EtlCoreService | 5/7 | 4/7 |
| OrchService | 1/3 | 3/3 |

### 5.3 失败原因分析

| 原因 | Python | Rust |
|------|--------|------|
| `BusinessException`（Service 内部 NPE/逻辑异常） | 8 | 0 |
| `ClassCastException`（BigDecimal/Integer/Boolean/Timestamp → Map） | 4 | 1 |
| `DateTimeParseException`（`to_date('test_pPeriod-01')` 测试数据占位符） | 0 | 4 |
| `NumberFormatException`（`"3 month"` interval 解析） | 0 | 1 |
| `NullPointerException`（`newRunId` 的 clock_timestamp TODO、vLastBoundary null） | 0 | 2 |
| 断言失败（rewardsReport 返回 null） | 1 | 0 |
| 跳过（assumeTrue(false)，需领域数据） | 2 | 0 |

### 5.4 单元测试判定

**Rust 单元测试通过率（81.4%）显著高于 Python（65.9%）**，且 0 失败。Rust 的 8 个错误集中在：DiscloseService 的日期解析（测试数据 `test_pPeriod` 非法但暴露了生成代码对 `to_date(p_period||'-01')` 的解析路径）、EtlCore 的 `clock_timestamp` TODO 与类型转换。Python 的 13 个失败中，8 个是 Service 内部运行时错误（`Map` 返回类型强转、OUT 参数原子引用误用），属于**更深的类型系统缺陷**。

---

## 6. 集成测试验证

命令：`DB_PASSWORD=Enmo@123 mvn verify -Pintegration`（remote 模式，直连本地 pagila 容器）

### 6.1 汇总

| 引擎 | 测试总数 | 通过 | 错误 | 跳过 | 通过率 |
|------|---------|------|------|------|--------|
| Python | 43 | 16 | 22 | 5 | **37.2%** |
| Rust（原始） | 43 | 0 | 39 | 4 | **0%** |
| Rust（修复 itest-schema 后） | 43 | 26 | 13 | 4 | **60.5%** |

### 6.2 ⚠️ 关键发现：Rust 引擎 itest-schema.sql 生成缺陷

Rust 引擎在 **remote 模式**下生成的 `src/test/resources/itest-schema.sql` 存在严重错误：

1. **生成 48 条 `DROP TABLE IF EXISTS "..." CASCADE`**（Python 在 remote 模式下正确生成 0 条 DROP）
2. **系统对象未过滤**：对 `pg_index`、`pg_partition`、`pg_class` 等系统目录表执行 DROP；甚至对 **schema**（`public`、`dw`）生成 `DROP TABLE IF EXISTS "public" CASCADE` / `DROP TABLE IF EXISTS "dw" CASCADE`
3. 使用 `CREATE TABLE`（非 `IF NOT EXISTS`），与 remote 模式语义不符

后果：**每个集成测试在 setup 阶段于脚本第 37 条语句（`DROP TABLE IF EXISTS "pg_index" CASCADE`）失败**，43 个测试 39 个报 `ScriptStatementFailed`，通过率 0%。

对照 Python 引擎同一文件的正确行为：
- `CREATE TABLE IF NOT EXISTS`（40 张表）
- **0 条 DROP TABLE**
- 系统表/命名空间过滤（`_SYSTEM_OBJECTS` 集合）

修复方式（验证用）：手工删除 48 条 DROP 并将 `CREATE TABLE` 改为 `CREATE TABLE IF NOT EXISTS`（与 Python 引擎 remote 模式输出对齐），随后 Rust 集成测试通过率升至 **60.5%**。该缺陷应作为 Rust 引擎 bug 修复（`crates/fluxgauss/src/generate/itest.rs` 中 `DROP TABLE` 生成逻辑未区分 remote/testcontainers 模式，且系统对象集合不完整——缺 `pg_index`、`pg_partition`，且未排除 schema 名）。

### 6.3 分 Service 明细（修复 schema 后）

| Service | Python (通过/总) | Rust (通过/总) |
|---------|------------------|----------------|
| _PublicService | 5/9 | 7/9 |
| AdsService | 0/1 | 0/1 |
| DimService | 5/5 | 5/5 |
| DiscloseService | 0/4 | 0/4 |
| DqService | 1/2 | 1/2 |
| DwdService | 2/6 | 4/6 |
| DwService | 0/1 | 1/1 |
| DwsService | 0/5 | 1/5 |
| EtlCoreService | 5/7 | 4/7 |
| OrchService | 1/3 | 3/3 |

### 6.4 失败原因分析（修复后）

| 原因 | Python | Rust |
|------|--------|------|
| BusinessException（Service 内部异常，多为 NPE/类型错误被包装） | 15 | 0 |
| BadSqlGrammarException（SQL 语法/列引用错误） | 3 | 0 |
| UncategorizedSQLException（SQL 执行异常，含列存/分区语义问题） | 0 | 6 |
| DateTimeParseException（`to_date('t_pPer-01')`） | 0 | 4 |
| ClassCastException（Integer/Boolean → Map） | 2 | 0 |
| DataIntegrityViolationException（INSERT 约束冲突） | 1 | 1 |
| ScriptStatementFailed（fixture 脚本失败） | 0 | 1 |
| PGInterval → Number 强转失败（`EXTRACT(EPOCH)` 返回类型） | 2 | 0 |

### 6.5 集成测试判定

- **Python**：通过率 37.2%。失败集中在：Disclose/Dws/Dwd/Orch 的 Service 内部类型错误（PGInterval 强转、AtomicReference 误用）、`Map` 返回类型强转、ads 动态 SQL（EXECUTE IMMEDIATE 重建）的 SQL 语法问题。
- **Rust**：修复 schema 缺陷后通过率 60.5%，反超 Python。剩余失败集中在 DiscloseService 的日期解析（测试数据问题）与 DWD/DWS 的 SQL 执行语义（列存表、视图 `v_analysis_cutoff` 依赖）。
- **共同亮点**：`DimService` 两引擎 5/5 全过（维度层 ETL 是最干净的迁移目标）。

---

## 7. 两引擎对比总表

| 维度 | Python v0.6.27 | Rust v0.6.27 | 胜出 |
|------|----------------|--------------|------|
| 过程提取率 | 100%（43/43） | 100%（43/43） | 平 |
| 迁移完成 | ✅ | ✅ | 平 |
| 原始编译错误 | 28 处 | 52 处 | **Python** |
| 单元测试通过率 | 65.9%（29/44） | 81.4%（35/43） | **Rust** |
| 集成测试通过率 | 37.2%（16/43） | 60.5%（26/43，修复 schema 后） | **Rust** |
| 生成代码类型安全 | 差（Map 返回类型、OUT 原子引用误用） | 中（未声明变量、拆箱） | **Rust** |
| itest-schema 生成 | 正确（IF NOT EXISTS，0 DROP） | **缺陷**（remote 模式生成 DROP + 系统对象未过滤） | **Python** |
| 动态 SQL 重建 | 91 个 DML（更多） | 85 个 DML | Python（覆盖更全） |
| 错误抛出（RAISE_APPLICATION_ERROR） | 未映射（12 处未解析） | 未映射（12 处未解析） | 平 |

**结论**：Rust 引擎的**生成代码运行时质量**（单元/集成测试通过率）已反超 Python，但其**编译错误率高**且存在 **itest-schema.sql 生成缺陷**（阻断 remote 模式集成测试），需要优先修复。Python 引擎编译错误较少、动态 SQL 覆盖更全，但**类型系统缺陷更深**（Map 返回类型、OUT 参数原子引用），导致运行时失败占比高。

---

## 8. 问题清单与修复建议

### 8.1 阻断性问题（P0）

| # | 问题 | 引擎 | 位置 |
|---|------|------|------|
| 1 | remote 模式 itest-schema.sql 仍生成 `DROP TABLE ... CASCADE` 且系统对象未过滤（`pg_index`/`pg_partition`/`public`/`dw`） | Rust | `crates/fluxgauss/src/generate/itest.rs:423-431` |
| 2 | 函数返回类型一律推断为 `Map<String,Object>`，标量返回（BigDecimal/Integer/Boolean/Timestamp）导致 ClassCastException | Python | 类型推断 + `_expr_to_java` |
| 3 | `__SQLSTATE__` 在异常处理块中引用但未声明（13 处） | Rust | statement 处理器 EXCEPTION 分支 |

### 8.2 重要问题（P1）

| # | 问题 | 引擎 |
|---|------|------|
| 4 | `AT TIME ZONE` 表达式解析失败（语句级降级） | 共享（ogsql-parser 0.8.32） |
| 5 | `ON DUPLICATE KEY UPDATE` 解析失败 | 共享 |
| 6 | `RAISE_APPLICATION_ERROR` 未映射为 Java 异常（12 处未解析调用） | 共享 |
| 7 | OUT 参数 `AtomicReference<T>` 误用（直接赋值数值、`.get()` 后未判空拆箱） | Python 为主 |
| 8 | 日期/时间类型混用（`Timestamp + "1 month"`、`String→Timestamp`、`Timestamp→Date`） | 两引擎 |
| 9 | `EXTRACT(EPOCH FROM (interval))` 生成畸形 Java 表达式 | Python（AdsService） |
| 10 | `to_date(p_period || '-01')` 在测试数据为占位符时抛 DateTimeParseException | 两引擎（测试生成层面） |

### 8.3 建议

1. **优先修复 Rust itest-schema 生成逻辑**：remote 模式不生成 DROP、使用 `CREATE TABLE IF NOT EXISTS`、扩充系统对象黑名单并排除 schema 名（对照 `flux_gauss.py` 的 `_itest_write_schema_sql`）。
2. **统一返回类型推断**：标量函数返回实际标量类型而非 `Map<String,Object>`。
3. **补齐 `RAISE_APPLICATION_ERROR` 映射**：`-20xxx` 错误码 → `BusinessException`（两引擎均可受益）。
4. **`AT TIME ZONE` 支持**需在 ogsql-parser 侧增强（Rust 引擎可直接受益；Python 引擎受二进制版本限制）。
5. 修复后建议以 ogagila 作为**新增回归基线**（`tests/regress/golden/`），覆盖 PACKAGE 语法、动态 SQL、列存分区等企业级特性。

---

## 附录 A：编译修复清单（验证用，非引擎修改）

两引擎生成代码的最小人工修复（未改动方法签名、mapper 调用、SQL 文本、测试断言）：

**Python dest（dest_ogagila_py）**：
- `_PublicService.java`：重复参数 `text→text2`；BigDecimal/Integer/Boolean 返回值加桥接强转；`Date - "3 month"` → `LocalDate.minusMonths(3)`
- `DwService.java`：补 `java.util.Map` import
- `EtlCoreService.java`：`Timestamp + "1 month"` → `plusMonths(1)`；水位线时间戳转换
- `DiscloseService.java`：`Date + "1 month"` → `plusMonths(1)`；`vFpDiff Integer→String`
- `OrchService.java`：月首/月末日期生成；`AtomicReference<Integer>` 传参修正
- `DwdService.java`：`planIncrement` 水位线 AtomicReference 传参；`Timestamp→Date` 转换
- `AdsService.java`：`vT0` 类型修正；`EXTRACT EPOCH` 畸形表达式替换

**Rust dest（dest_ogagila_ru）**：
- 5 个 Service：补 `String __SQLSTATE__ = "";` 声明（13 处）
- `_PublicService.java`：重复参数 `text→text2`；`return new` → `return null`；`LocalDate→Date` 包装
- `AdsService.java`：`double→BigDecimal` 转换
- `EtlCoreService.java`：时间戳算术/回退修正
- `DiscloseService/DqService/DwdService/OrchService`：`null→0L/0` 基本类型占位、`Timestamp→Date` 转换、月首/月末日期生成

## 附录 B：验证环境与复现命令

```bash
# 1. 准备源（剥离 psql 指令）
rm -rf /tmp/ogagila_src && mkdir -p /tmp/ogagila_src
cp -r lib/ogagila/sqls/* /tmp/ogagila_src/
find /tmp/ogagila_src -name "*.sql" -exec sed -i '' '/^\\set /d' {} \;

# 2. 迁移（配置文件 demo-project/fluxgauss_ogagila_{py,ru}.yaml）
OGSQL_BIN=./ogsql ~/dev/miniforge3/bin/python3 converter/flux_gauss.py -c demo-project/fluxgauss_ogagila_py.yaml --skip-validate
./target/release/fluxgauss -c demo-project/fluxgauss_ogagila_ru.yaml --skip-validate

# 3. 编译 + 单元测试
cd dest_ogagila_{py,ru} && mvn compile && mvn test

# 4. 集成测试（需本地 pagila 容器）
cd dest_ogagila_{py,ru} && DB_PASSWORD=Enmo@123 mvn verify -Pintegration
```

**数据基线**：原始编译错误与测试结果已留存于 `dest_ogagila_py/.fluxgauss/reports/`、`dest_ogagila_ru/.fluxgauss/reports/` 及对应 `target/surefire-reports/`。

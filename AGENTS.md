# AGENTS.md — FluxGauss (sp2java)

## What This Repo Does

Converts OpenGauss/PostgreSQL stored procedures (PL/pgSQL) into a Spring Boot + MyBatis Java project. The converter parses SQL via a Rust-based AST parser, then generates Service classes, Mapper interfaces, MyBatis XML mappers, unit tests, and optional integration tests.

## Key Files

- `converter/flux_gauss.py` — Single-file Python converter (~17300 lines). All logic lives here.
- `crates/fluxgauss/` — Rust converter (dual-engine). Uses `ogsql-parser` as a git dependency.
- `ogsql-parser` — Referenced via git dependency (`https://github.com/c2j/ogsql-parser.git`, branch `main`) in `Cargo.toml`. Not a local submodule.
- `demo-project/fluxgauss_py.yaml` — Example config referencing `demo-project/sql/*.sql` sources (3 demo configs total: `fluxgauss_py.yaml`, `fluxgauss_ru.yaml`, `fluxgauss_tu.yaml`).
- `dest/` — Generated output (gitignored). Contains a complete Maven/Spring Boot project.
- `使用指南.md` — Full user guide (Chinese). Covers config, CLI, and supported PL/pgSQL features.
- `docs/plans/` — Implementation plans for features in progress.

## How to Run

### Convert SQL to Java

```bash
# Config-file mode (recommended, supports incremental)
python3 converter/flux_gauss.py -c fluxgauss.yaml

# CLI mode (one-shot)
python3 converter/flux_gauss.py -o ./dest -s sql/file1.sql sql/file2.sql

# Force full regeneration
python3 converter/flux_gauss.py -c fluxgauss.yaml --full

# Resume from checkpoint (skip already-generated packages)
python3 converter/flux_gauss.py -c fluxgauss.yaml --resume

# Specify report output path
python3 converter/flux_gauss.py -c fluxgauss.yaml --report ./report.md
```

### Verify Output

```bash
cd dest && mvn compile   # compile check
cd dest && mvn test      # run generated unit tests
```

### Requirements

- Python 3.10+ — `converter/flux_gauss.py` uses PEP 604 (`int | None`) at module level, so Python 3.9 fails at import with `TypeError: unsupported operand type(s) for |`. `tests/regress/conftest.py` enforces this.
- Java 17+ (for `mvn compile` verification)
- `ogsql` binary — resolved via `OGSQL_BIN` env var, `PATH`, or local fallback paths. Build from source: `git clone https://github.com/c2j/ogsql-parser.git && cd ogsql-parser && cargo build --release --features full`

### ogsql version skew (known hazard)

The two engines currently parse with **different** parser versions:

- Rust engine pins `ogsql-parser` at tag `v0.8.32` (`Cargo.toml`, `Cargo.lock`).
- CI builds the `ogsql` binary used by the **Python** engine from `ref: main` (`.github/workflows/ci.yml`) — a moving target.

This is a latent Python/Rust parity hazard: a parser change can shift one engine's output without touching the other. The binary at repo root is 0.8.32.

## Architecture

```
SQL files → ogsql-parser (Rust binary) → JSON AST
                                               ↓
                                     flux_gauss.py (Python)
                                               ↓
                    Spring Boot project: Service.java + Mapper.java + Mapper.xml + Test.java
                  + optional: integration tests (itest/) + BusinessException.java
```

Data flow inside `flux_gauss.py`:
1. `parse_sql_file()` calls ogsql binary → raw AST dict
2. `extract_procedures()` → `ProcedureInfo[]` (also extracts package variables, custom types)
3. `extract_comments()` + `_map_comments_to_procedures()` → attach `CommentInfo` to procedures
4. `analyze_procedure()` fills DML statements, Java logic lines, service calls, cursor tracking
5. `generate_project()` writes Java/XML files to output dir + optional integration tests
6. Conversion report (`ConversionReport`) saved with procedure mappings and skipped items

## Incremental Build

Config-file mode auto-caches in `dest/.fluxgauss/` (manifest.json + AST JSONs). Only changed SQL files are re-parsed. Transitive dependencies (cross-package calls) trigger re-generation of callers.

Resume from checkpoint: `--resume` flag skips packages already successfully generated (tracked via `dest/.fluxgauss/generation-checkpoint.json`).

Clear cache: `rm -rf dest/.fluxgauss`

## Config (`fluxgauss.yaml`)

```yaml
output_dir: ./dest
base_package: com.example.demo

# Optional: integration testing (generates itest classes)
integration_test:
  enabled: true
  mode: remote            # remote (connects to real DB)
  url: jdbc:postgresql://localhost:5432/postgres
  username: gaussdb
  password: secret

sources:
  - demo-project/sql/pkg_order.sql
  - demo-project/sql/pkg_product.sql
java_packages:                    # optional: map SQL files to different Java packages
  - package: com.example.order
    sources:
      - demo-project/sql/pkg_order.sql
# logger: slf4j                   # slf4j (default), log4j2, commons-logging, jul, or custom dict
# database:                       # optional: generates application.yml
#   url: jdbc:postgresql://...
```

## Conventions

- Each SQL package → 4 files: `{Name}Service.java`, `{Name}Mapper.java`, `{Name}Mapper.xml`, `{Name}ServiceTest.java`
- Table DDL (`CREATE TABLE`) additionally generates one entity POJO per table under `entity/{PascalTable}.java`.
- Skeleton files (`pom.xml`, `application.yml`, `DemoApplication.java`, `BusinessException.java`) are written **only if they don't exist**. To regenerate, delete them first.
- Integration test files are generated under `src/test/java/.../itest/` with `AbstractIntegrationTest` base class and `itest-schema.sql` fixtures.
- Source tracing comments (`// Source: file.sql:line-range`) are injected into all generated code for debugging.
- Naming: `pkg_order` → `OrderService`, `create_order` → `createOrder()`
- Comments from SQL sources are preserved: `leading_comments` (before procedure) and `inline_comments` (inside body) are carried into generated Java code.

## When Modifying `converter/flux_gauss.py`

### Prohibitions

- **NEVER hardcode table column names** in the converter code. Table columns must always be looked up dynamically from the table schema cache (`TYPE_OVERRIDES` / `_lookup_table_columns()` / `parse_table_ddl()`). Hardcoded column lists break for any table other than the one they were written for, and silently produce incorrect SQL when table schemas evolve.

- The entire converter is one file (~17300 lines). Key dataclasses:
  - `Parameter` — procedure parameter with SQL/Java type mapping
  - `CommentInfo` — SQL comment (text, line range, type: single-line/block)
  - `DmlStatement` — extracted DML with method_id, SQL text, parameter types, optional filters
  - `ServiceCall` — cross-service method call reference
  - `ProcedureInfo` — main dataclass for a procedure, including cursor tracking, custom types, dynamic SQL templates, scheduler tasks, source location, comments
  - `PackageInfo` — all procedures in a package with package vars, custom types, comments
  - `SkippedItem` — non-procedure statements (DDL, grants, etc.) skipped during extraction
  - `ProcedureMapping` — per-procedure conversion result (Java service/method, stub status, notes)
  - `ConversionReport` — full conversion report with mappings, errors, statistics
- ogsql binary path is resolved at import time via `_resolve_ogsql_bin()`.
- After changes, run: `python3 converter/flux_gauss.py -c fluxgauss.yaml && cd dest && mvn compile`
- Conversion reports are saved to `dest/.fluxgauss/reports/`.
- Generation checkpoint is saved to `dest/.fluxgauss/generation-checkpoint.json`.
- The ogsql-parser Rust binary is a separate build from `https://github.com/c2j/ogsql-parser`. For the Rust engine, it is pulled automatically as a git dependency via `Cargo.toml`. For the Python engine, build the `ogsql` binary and set `OGSQL_BIN` or place it on `PATH`.

## Key Internal Modules (by line range)

| Section | Lines | Description |
|---|---|---|
| Constants & config | 1–200 | `_resolve_ogsql_bin()`, `OGSQL_BIN`, `BASE_PACKAGE`, `_resolve_logger_config()`, logger presets |
| `parse_table_ddl()` | 201–278 | Parses `CREATE TABLE` DDL for schema info |
| Type maps | 279–530 | `SQL_TO_JAVA`, `SQL_TO_JDBC_TYPE`, `TYPE_OVERRIDES`, `_lookup_table_columns()` |
| Logging & tracking | 531–626 | `_init_log()`, `_log()`, `_progress_bar()`, `_record_unsupported()` |
| Type conversion | 627–827 | `sql_type_to_java()`, `sql_type_to_jdbc()`, `java_type_to_jdbc()` |
| Naming utilities | 828–886 | `snake_to_camel()`, `package_to_classname()`, `java_method_name()` |
| Dataclasses | 887–1178 | `Parameter`, `CommentInfo`, `DmlStatement`, `ServiceCall`, `ProcedureInfo`, `PackageInfo`, `SkippedItem`, `ProcedureMapping`, `ConversionReport` |
| SQL parsing | 1179–1561 | `_split_sql_statements()`, `_read_sql_file()`, `parse_sql_file()`, `parse_sql_files()` |
| AST extraction | 1562–1875 | `extract_parameters()`, `extract_procedures()`, `_recover_constant_declarations()` |
| Comment handling | 1876–1970 | `extract_comments()`, `_map_comments_to_procedures()` |
| Non-procedure extraction | 1971–2060 | `extract_non_procedure_statements()` — DDL/grant/type skips |
| DML analysis | 2061–2533 | `_extract_dml_target()`, `_extract_dml_target_simple()`, `analyze_procedure()` |
| Comment injection | 2534–2835 | `_inject_inline_comments()`, `_find_body_stmt_lines()` |
| Statement processing | 2836–8289 | `_process_statement()` dispatch → SQL, IF, FOR, WHILE, LOOP, cursor ops, assignments, procedure calls, EXECUTE, RAISE, CASE, dynamic SQL reconstruction |
| Expression → Java | 8290–10963 | `SQL_FUNCTION_MAP`, `SPECIAL_FUNCTION_MAP`, `_expr_to_java()`, type inference, coercion |
| Project generation | 10964–13810 | `generate_project()` → `_write_pom_xml()`, `_write_mapper_xml()`, `_write_service_class()`, `_write_service_test()`, entity POJOs |
| Integration tests | 13811–16223 | `_itest_*()` functions — schema extraction, test data inference, fixture generation, itest class writing |
| Report & CLI | 16224–17306 | `write_conversion_report()`, `_parse_config()`, `_save_gen_checkpoint()`, `_build_arg_parser()`, `main()` |

## 开发守则（双引擎验证沉淀，2026-08 增补）

> 来源：ogagila / fastaas 双项目 × 双引擎多轮验证与修复（报告见 `docs/reports/`）。
> 改动生成逻辑、解析器依赖、或执行验证前必读。

### 0. 改动定位（先读再改）

确认改动落在哪一侧，用对应的命令与门禁，不混用：

| 改动面 | 位置 | 门禁 |
|--------|------|------|
| Python 转换器 | `converter/flux_gauss.py` | ruff + pytest |
| Rust 转换器 | `crates/fluxgauss` | cargo fmt + clippy（无新增 warning）+ cargo test |
| 解析器依赖 | `Cargo.toml` 中 ogsql-parser rev | 问题路由上游 `c2j/ogsql-parser` |
| 生成物质量 | `dest_*` | 完整验证链（见 §3） |

### 1. 测试纪律

**Never：**
- 删除、注释、跳过已有测试（`@pytest.mark.skip`、无 ticket 的 `xfail`、`#[ignore]`、注释 `#[test]`）
- 修改人类已有测试的断言来迁就实现
- 写永真测试：无断言、只检查 `is not None`、只 verify 调用次数不查参数
- 先提交无测试的业务行为再「回头补」

**Ask first：** 改人类已有测试（断言/夹具/golden）；新增运行时依赖、新 workspace crate；接受 golden file 更新且行为含义变化（必须解释 diff）；放宽 ruff/mypy/clippy。

**Always：** 改遗留路径前先写特征测试锁定当前行为（golden 形态，允许丑必须可重复）；现有测试因改动失败——修实现，不修测试。

| 测试来源 | 权限 |
|---|---|
| 人类已有测试 | 只读 |
| 本任务新建测试 | 可改，直到该行为稳定 |
| 过时/偶发失败 | 只报告，不擅自跳过 |

### 2. 工作流：回归先行（底线）→ TDD（方向）

Bug fix 标准循环：
1. 先写能复现 bug 的**失败测试**（golden/regression 形态；Rust 侧允许「引用尚不存在 API 导致编译失败」作为合法 Red）
2. 最小修复让它变绿——禁止删测试、加宽断言、吞异常换绿
3. 跑该侧全部回归 + 对应 golden/parity

方向性要求：向严格 Red→Green→Refactor 演进，一次循环只锁定一个行为。探索草稿一律放 /tmp 不进仓库。

### 3. 生成物验证链（改生成逻辑后必经）

```
ogsql validate → 转换 → mvn compile → mvn test → DB_PASSWORD=... mvn verify -Pintegration
```

- **解析器升级后必须重跑双项目回归**：parser 修复会让此前「静默丢弃」的语句进入生成管线，暴露转换器新缺口（实例：0.10.1 升级暴露 AtTimeZone dict 直写 #90）。仅 pytest 绿不等于验证完成。
- **回归基线双集**（配置 `demo-project/fluxgauss_*_v2.yaml` / `fluxgauss_fastaas_*.yaml`）：
  - fastaas `collected_sql`：标准 Oracle 风格 PL/SQL，当前双引擎 100%——防回归黄金集
  - ogagila：openGauss 特性压测集（列存/动态 SQL/分区/AT TIME ZONE/自治事务）
- 编译错误数按唯一 `file:[line,col]` 位置统计（maven 每条错误打印两遍）。

### 4. 调试守则

- **javac 语法错误中止语义分析**：1 条「需要<标识符>」会掩盖几十条类型错误。先修语法错再重编译，错误面稳定后才能评估生成质量（实例：Rust 输出先报 1 错，修复后暴露 52 处）。首轮编译错误数 ≠ 缺陷数。
- **ogsql 二进制解析顺序陷阱**：`_resolve_ogsql_bin()` 候选顺序为 `os.getcwd()/ogsql` → `OGSQL_BIN` → PATH。在仓库根目录运行转换会静默命中旧二进制；测试新二进制须从中性 CWD（如 /tmp）运行，或先移开旧 `./ogsql`。
- **双引擎版本对齐**：对比前确认两侧解析器版本一致（Rust 看 `Cargo.lock` rev；Python 看实际命中的 `ogsql --version`）。
- cargo 构建报 `InvalidArchive("Could not find EOCD")`：utoipa-swagger-ui 构建缓存 zip 损坏。用 `unzip -t` 定位 `target/release/build/utoipa-swagger-ui-*/out/v*.zip` 损坏条目，删除后重建。

### 5. Python 侧规范（工具链维持现状）

- pip + pyproject.toml + requirements.txt；禁止引入并行的 Poetry/Pipenv/uv
- **待修正**：`pyproject.toml` requires-python 为 `>=3.9`，但代码用 PEP 604 实际需 3.10+（#73），应改为 `>=3.10`
- 格式/lint：ruff（`ruff.toml`）；类型：mypy（`mypy.ini`）；测试：pytest（`pyproject.toml [tool.pytest.ini_options]`）
- mock 只打进程边界：外部 ogsql 二进制用 `OGSQL_BIN` 指向 fixture；禁止 patch 被测对象内部实现

**迁移对象准备：**
- psql 客户端指令（`\set`、`\echo`）不是 SQL，ogsql 无法解析：迁移前 `sed '/^\\set /d'` 清洗；validate 报 `expected statement, got Op("\\")` 即此因
- DDL 必须列入 sources：类型推断（TYPE_OVERRIDES / `parse_table_ddl`）只扫 sources 内的 `CREATE TABLE`
- 排除非迁移对象：init_data（纯数据 COPY/INSERT）、QA 测试脚本不进 sources
- 编码：`_read_sql_file` 自动探测 gb18030/gbk/big5（fastaas 为 GB18030），无需预处理
- Oracle `(+)` 外连接：ogsql 报 Suggestion 级 Warning（非错误），转换器按 warning 过滤，不阻断

### 6. Rust 侧规范

- workspace 根执行 cargo；依赖变更用 `cargo add` / `cargo update -p <crate>`（禁止一次性 update 整个 lockfile；ogsql-parser 以 rev/tag 精确固定）
- **clippy 债务策略**：存量告警（build 43 条 / clippy 口径 188 条）由独立任务清零；**新增代码零新增 warning**——改动涉及文件不允许引入新告警
- 上游 ogsql-parser 仓库保持全量门禁（fmt + clippy -D warnings + test，CI 已生效），向其提交 PR 须全绿
- 禁止把 clippy/测试失败说成「main 原来就红」

### 7. 集成测试要求

- **只对可丢弃数据库跑**：生成 fixture 含 `DELETE FROM ...`，会清空真实表。pagila 容器可 `docker-compose down -v` 重建；fastaas 需 `BIGFUND` schema（已建于 pagila 库，JDBC 用 `currentSchema=BIGFUND`）
- **Rust itest-schema（#78，已修复并验证 2026-08-28）**：remote 模式现生成 0 条 DROP + `CREATE TABLE IF NOT EXISTS`，系统对象（`pg_index`/`pg_partition`/`public`/`dw`）已过滤，集成测试 schema setup 零失败。回归检查点：任一引擎改动后 `grep -c "DROP TABLE" dest_*/src/test/resources/itest-schema.sql` 必须为 0
- 非交互模式校验失败会直接退出；已知良性错误（Warning 级、语句级降级）用 `--skip-validate`

### 8. Issue 工作流

- **路由**：ogsql 语法/解析问题 → `c2j/ogsql-parser`；转换/代码生成问题 → 本仓库。提报前 search 查重并交叉引用关联 issue（如 Map 返回类型 ↔ #34）
- **上游修复验证链**：fetch PR 分支 → 本地构建 → 最小复现逐条验证 → 端到端重迁移（对比解析错误数与 DML 数，实例：ogagila 10→0 / DML 91→94）→ 验证通过再关上游 issue；下游跟踪 issue 保留至二进制依赖实际升级
- **统计口径**：passed = total − failures − errors − skipped；skipped 多为 `assumeTrue(false)` 的领域数据测试，报告单列、不计入失败

### 9. 完成标准（提交/交还前自检）

- [ ] 新行为/修复有失败→通过的测试（golden/regression）
- [ ] 未删除、跳过、改写人类已有测试
- [ ] 门禁已跑：Python 改动 → ruff + pytest；Rust 改动 → fmt + clippy（无新增）+ cargo test；生成逻辑改动 → §3 完整验证链
- [ ] 跨引擎契约改动（JSON AST 消费 / YAML schema / 生成物）：`golden/py` 与 `golden/ru` parity 都要跑
- [ ] 无草稿、调试输出、无主 lockfile 变更被带入
- [ ] 汇报含具体证据：改动文件清单 + 实际执行的命令与结果（不写「测过了」）

## Notes

- `ogsql.broken` at root is a broken binary — ignore it.
- `SQL` file at root is empty — ignore it.
- `docs/plans/` contains implementation plans for features in progress.
- Lint/format configs: `ruff.toml` (Python), `rustfmt.toml` (Rust), `mypy.ini` (Python typing), `pyproject.toml`.
- CI: `.github/workflows/release.yml` — builds ogsql + fluxgauss binaries for Linux/Windows/macOS (triggered by `v*` tag push). `.github/workflows/ci.yml` — runs Python + Rust unit/regression tests.
- Dependencies: `requirements.txt` lists `pyyaml` (YAML config), `pytest` (tests), `mcp` (MCP server mode).

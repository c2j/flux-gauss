# FluxGauss Rust 重写设计方案

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Python converter/flux_gauss.py (~8500 行) 重写为 Rust，实现 30K 文件规模下的高性能、低内存占用 PL/pgSQL → Spring Boot/MyBatis 转换器。

**Architecture:** 三阶段流式管线：扫描（轻量摘要）→ 分析（跨包依赖解析）→ 生成（分批文件输出）。ogsql-parser 作为 crate 依赖直接调用，消除 subprocess 开销。显式 PipelineContext 替代全局状态。rayon 并行处理独立文件。

**Tech Stack:** Rust 1.80+, ogsql-parser (path dependency), clap (CLI), serde/serde_yaml (配置), rayon (并行), sha2 (哈希), tracing (日志)

**Golden Master:** 29 个 demo-project SQL 文件 → 113 个生成文件 (25 Service, 25 Mapper, 25 XML, 25 Test, 13 ITest + skeleton)，作为回归测试基准。

**Testing Discipline:** 每个模块配有独立单元测试；每个 Task 先写测试再写实现；最终输出与 Python golden master 逐文件对比。Python 版是 8500 行无测试巨石 — Rust 版绝不能重蹈覆辙。

---

## 0. 架构决策记录 (ADR)

### ADR-1: 管线架构 — 三阶段 + 分批生成

```
Phase 0: 预扫描 DDL        (并行读文件, 提取表结构)
Phase 1: 解析 + 提取摘要    (并行调用 ogsql-parser crate)
Phase 2: 全量分析           (顺序, 跨包依赖需要全量摘要)
Phase 3: 分批生成           (按依赖拓扑排序, 批量输出)
```

**为什么不是两阶段：** 跨包分析（resolve target procedure signatures、OUT param type promotion）需要所有 procedure 的参数签名信息。但这些信息很轻量（每个 procedure ~100 bytes 的摘要），30000 文件只需 ~3MB。所以 Phase 2 全量加载摘要可行，Phase 3 才是内存大户（生成 Java 代码字符串），分批处理。

### ADR-2: 直接使用 ogsql-parser 类型

用 ogsql-parser 的 `Statement`/`Expr`/`DataType` 等 enum 作为 AST 表示，**不**定义自己的 AST 类型。

**理由：**
- ogsql-parser 是 git submodule，我们完全控制
- 消除 double conversion（parser 类型 → 中间类型 → 处理）
- Rust enum 的 pattern match 比 Python dict 遍历更安全
- 如果 parser 增加新 Statement 变体，编译器会警告 match 不完备

**代价：** 与 parser 版本强耦合。可接受，因为我们同时维护两边。

### ADR-3: 代码生成 — 纯 Rust 代码 + CodeWriter

**不用模板引擎**（Askama/Tera/genco），用纯 Rust 代码生成。

**理由：**
- Python 版的生成逻辑极度动态：条件段取决于类型推断结果、OUT 参数状态、stub 标记等
- 30+ 种 AST 节点的表达式翻译不可能塞进模板
- 模板引擎适合结构化文档，不适合这种高度过程化的代码组装
- 纯 Rust 代码享有编译器检查 + IDE 补全

**实现方式：** `CodeWriter` 工具结构体，封装缩进管理、行追加、导入收集：

```rust
struct CodeWriter {
    lines: Vec<String>,
    indent: usize,
    imports: BTreeSet<String>,
}

impl CodeWriter {
    fn line(&mut self, s: &str) { self.lines.push(format!("{}{}", "    ".repeat(self.indent), s)); }
    fn begin_block(&mut self, s: &str) { self.line(s); self.indent += 1; }
    fn end_block(&mut self) { self.indent = self.indent.saturating_sub(1); self.line("}"); }
    fn to_string(&self) -> String { self.lines.join("\n") }
}
```

### ADR-4: 状态管理 — 分阶段 Context

```rust
struct ScanContext {
    type_overrides: HashMap<(String, String), String>,  // (table, col) -> sql_type
}

struct AnalysisContext {
    package_summaries: HashMap<String, PackageSummary>,  // 全量轻量摘要
    package_variables: HashMap<String, VarInfo>,
    package_constants: HashMap<String, String>,
    stub_procedures: HashSet<(String, usize)>,
    stub_reasons: HashMap<(String, usize), Vec<String>>,
    unsupported_functions: Vec<String>,
    unresolved_calls: Vec<String>,
    todo_summary: Vec<TodoEntry>,
}

struct GenerationContext {
    all_packages: HashMap<String, &PackageInfo>,  // 引用，不是拥有
    svc_method_param_counts: HashMap<(String, String), (usize, bool)>,
    logger_config: LoggerConfig,
    base_package: String,
    config: AppConfig,
}
```

**不用 Interior Mutability (RefCell/Mutex)：** 每个 Phase 独占自己的 Context，按顺序传递。Rust 所有权模型天然保证无数据竞争。

### ADR-5: 并行策略 — rayon 数据并行

```
Phase 0 (DDL预扫描):   rayon::par_iter  — 每个文件独立
Phase 1 (解析+提取):   rayon::par_iter  — 每个文件独立
Phase 2 (全量分析):     单线程顺序       — 需要共享可变 AnalysisContext
Phase 3 (文件生成):     rayon::par_iter  — 每个包独立（已解析完依赖）
```

**不用 tokio/async：** 这是 CPU 密集 + 文件 I/O 工具，不是网络服务。rayon 的 work-stealing 线程池最适合。

**Phase 2 为什么顺序：** `analyze_procedure()` 修改 `proc` 的同时读取 `all_package_summaries`（共享不可变读），但还会写入 `AnalysisContext` 的全局追踪状态。顺序执行最简单，且分析不是瓶颈（纯内存操作，30000 个 procedure < 5 秒）。

### ADR-6: 错误处理 — 收集 + 继续

```rust
struct ConversionResult<T> {
    value: Option<T>,
    errors: Vec<ConversionError>,
    warnings: Vec<ConversionWarning>,
}

enum ConversionError {
    Parse { file: String, message: String },
    Analysis { procedure: String, message: String },
    Generation { package: String, message: String },
    Io { path: String, source: std::io::Error },
}
```

**原则：** 单个文件/过程的失败不应阻止其他文件的处理。所有错误收集到 `errors` 列表，最终写入转换报告。仅 I/O 级别的致命错误（输出目录不可写）才终止程序。

---

## 1. 项目结构

```
sp2java/
├── Cargo.toml                              # workspace root
├── crates/
│   ├── fluxgauss/                          # 主 binary crate
│   │   ├── Cargo.toml
│   │   ├── src/
│   │   │   ├── main.rs                     # CLI 入口 (clap)
│   │   │   ├── config.rs                   # 配置加载 (serde_yaml)
│   │   │   ├── context.rs                  # ScanContext, AnalysisContext, GenerationContext
│   │   │   ├── pipeline.rs                 # 三阶段管线编排
│   │   │   ├── types.rs                    # 核心数据结构 (ProcedureInfo, PackageInfo, etc.)
│   │   │   ├── type_map.rs                 # SQL↔Java 类型映射
│   │   │   ├── naming.rs                   # snake_to_camel, package_to_classname, etc.
│   │   │   ├── extract.rs                  # AST → ProcedureInfo 提取
│   │   │   ├── analyze.rs                  # DML 分析、跨包依赖
│   │   │   ├── expr.rs                     # _expr_to_java — 表达式翻译 (最大模块)
│   │   │   ├── statement.rs                # _process_statement 分发器
│   │   │   ├── statements/                 # 各语句处理器
│   │   │   │   ├── mod.rs
│   │   │   │   ├── sql.rs                  # SQL DML 处理
│   │   │   │   ├── control_flow.rs         # IF/FOR/WHILE/LOOP/CASE
│   │   │   │   ├── cursor.rs              # OPEN/FETCH/CLOSE
│   │   │   │   ├── assignment.rs           # 赋值语句
│   │   │   │   ├── call.rs                # 过程调用 (CALL/PERFORM)
│   │   │   │   ├── raise.rs               # RAISE 异常
│   │   │   │   └── execute.rs             # EXECUTE 动态 SQL
│   │   │   ├── generate/
│   │   │   │   ├── mod.rs                  # 项目生成编排
│   │   │   │   ├── writer.rs              # CodeWriter 工具
│   │   │   │   ├── service.rs             # Service.java 生成
│   │   │   │   ├── mapper.rs              # Mapper.java + Mapper.xml
│   │   │   │   ├── test.rs                # 单元测试生成
│   │   │   │   ├── itest.rs               # 集成测试生成
│   │   │   │   └── skeleton.rs            # pom.xml, application.yml, etc.
│   │   │   ├── incremental.rs             # 增量构建 (manifest, cache, checkpoint)
│   │   │   └── report.rs                  # 转换报告
│   │   └── tests/                          # ← 集成测试 (golden master)
│   │       ├── golden_master.rs            # 对比 Python 输出的快照测试
│   │       └── fixtures/                   # 测试固件
│   │           ├── pkg_order.sql           # 精选的测试用 SQL 文件
│   │           ├── pkg_common.sql
│   │           ├── expected_pkg_order/     # 预期输出 (从 Python dest/ 复制)
│   │           │   ├── OrderService.java
│   │           │   ├── OrderMapper.java
│   │           │   ├── OrderMapper.xml
│   │           │   └── OrderServiceTest.java
│   │           └── ...
│   └── ogsql-parser/                       # git submodule → 已有
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs                      # ✅ 已有 library exports
│           └── ...
├── tests/                                  # workspace 级集成测试
│   └── snapshot_baseline.rs               # 全量 demo-project 快照回归
├── demo-project/                           # Golden master 源文件
│   ├── fluxgauss.yaml
│   └── sql/                                # 29 个 SQL 文件
├── dest/                                   # Python 生成的 golden output (gitignored)
└── docs/plans/
```

**关键设计原则 — 模块边界即测试边界：**

每个 `src/*.rs` 模块对应一个同名的 `#[cfg(test)] mod tests` 内联测试块。
此外，纯函数模块（naming、type_map、expr）的公共 API 必须有独立单元测试覆盖。
`tests/` 目录只放跨模块集成测试和 golden master 快照。

**Cargo.toml (workspace):**
```toml
[workspace]
members = ["crates/fluxgauss"]
resolver = "2"

[workspace.dependencies]
ogsql-parser = { path = "../lib/ogsql-parser" }  # 或 crates/ogsql-parser
```

**crates/fluxgauss/Cargo.toml:**
```toml
[package]
name = "fluxgauss"
version = "1.0.0"
edition = "2021"

[dependencies]
ogsql-parser = { workspace = true }
clap = { version = "4", features = ["derive"] }
serde = { version = "1", features = ["derive"] }
serde_yaml = "0.9"
serde_json = "1"
rayon = "1.10"
sha2 = "0.10"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["fmt", "env-filter"] }
chrono = "0.4"
regex = "1"
indexmap = "2"        # 有序 HashMap，用于保持 YAML 配置顺序

[dev-dependencies]
pretty_assertions = "1"   # 测试输出可读对比
tempfile = "3"            # 增量构建测试的临时目录
criterion = { version = "0.5", features = ["html_reports"] }  # 性能基准

[[bench]]
name = "parse_benchmark"
harness = false
```

---

## 2. 测试策略 (三层防御)

> **Python 版的问题：** 8500 行巨石、零测试、改一处全局崩。Rust 版从第一个 commit 起就必须有测试守护。

### 层次 1: 模块级单元测试 (内联 `#[cfg(test)]`)

每个 `src/` 模块内建测试块，测试本模块的公共 API。这些测试不需要完整管线运行，只验证单一职责。

| 模块 | 测试什么 | 典型测试数 |
|------|----------|-----------|
| `naming.rs` | snake_to_camel, package_to_classname, java_method_name | ~15 |
| `type_map.rs` | sql_type_to_java, sql_type_to_jdbc, 边界类型 (null, unknown) | ~20 |
| `config.rs` | YAML 解析 (preset/custom logger), 缺失字段默认值, CLI 模式 | ~10 |
| `extract.rs` | 参数提取 (IN/OUT/INOUT), 包变量, 注释映射, 无 procedure 的文件 | ~15 |
| `expr.rs` | 每个 Expr 变体 → Java, BigDecimal 算术, 字符串比较, 函数映射 | ~80+ |
| `statement.rs` | 每种 PL/pgSQL 语句 → java_logic_lines, 边界 (空 body, 嵌套 IF) | ~30 |
| `generate/writer.rs` | 缩进管理, begin/end_block, import 收集去重 | ~10 |
| `incremental.rs` | 哈希计算, 缓存命中/未命中, checkpoint 保存/恢复, stale 清理 | ~15 |
| `pipeline.rs` | 各阶段错误传递, 空输入处理 | ~8 |

**命名约定：** `test_<函数名>_<场景>_<预期>`

```rust
// naming.rs 内联测试示例
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_snake_to_camel_simple() {
        assert_eq!(snake_to_camel("create_order"), "createOrder");
    }

    #[test]
    fn test_snake_to_camel_single_word() {
        assert_eq!(snake_to_camel("status"), "status");
    }

    #[test]
    fn test_snake_to_camel_empty() {
        assert_eq!(snake_to_camel(""), "");
    }

    #[test]
    fn test_package_to_classname_with_pkg_prefix() {
        assert_eq!(package_to_classname("pkg_order"), "Order");
    }

    #[test]
    fn test_package_to_classname_without_prefix() {
        assert_eq!(package_to_classname("inventory"), "Inventory");
    }
}
```

### 层次 2: 集成测试 (模块间协作)

测试多个模块协作的场景，放在 `tests/` 目录。这些测试使用精心挑选的 SQL fixture 文件，覆盖典型转换路径。

**精选 fixture 文件（从 demo-project 中挑选代表性用例）：**

| Fixture | 覆盖场景 |
|---------|---------|
| `pkg_order.sql` | 跨包调用 (→ pkg_common)、DML、OUT 参数、IF/ELSE |
| `pkg_common.sql` | 被多个包依赖、函数返回值、游标模式 |
| `pkg_cursor_patterns.sql` | OPEN/FETCH/CLOSE、REFCURSOR OUT、嵌套游标 |
| `pkg_builtin_funcs_test.sql` | 60+ SQL 内置函数翻译 |
| `pkg_type_test.sql` | 自定义 TYPE/RECORD、复合类型字段访问 |
| `pkg_test_patterns.sql` | WHILE/LOOP/CASE/CONTINUE/EXIT 控制流 |
| `proc_GOto.sql` | GOTO (stub)、边界情况 |
| `tables.sql` | 纯 DDL (无 procedure，应跳过) |

**测试模式：**

```rust
// tests/integration_extract.rs
use fluxgauss::extract::extract_procedures;
use fluxgauss::naming::snake_to_camel;

#[test]
fn test_extract_pkg_order_procedures() {
    let sql = std::fs::read_to_string("demo-project/sql/pkg_order.sql").unwrap();
    let ast = parse_with_ogsql(&sql);
    let (procedures, pkg_vars, custom_types) = extract_procedures(&ast, "pkg_order.sql");

    // 守护：预期提取到 N 个 procedure
    assert!(!procedures.is_empty(), "pkg_order should have procedures");

    // 守护：每个 procedure 必须有基本信息
    for proc in &procedures {
        assert!(!proc.name.is_empty(), "procedure name must not be empty");
        assert!(!proc.package.is_empty(), "package must not be empty");
        assert_eq!(proc.package, "pkg_order");
    }

    // 精确断言：特定 procedure 的签名
    let create_order = procedures.iter()
        .find(|p| p.proc_name == "create_order")
        .expect("create_order procedure must exist");
    assert!(!create_order.parameters.is_empty());
    assert!(create_order.is_function == false); // PROCEDURE, not FUNCTION
}
```

```rust
// tests/integration_analyze.rs
#[test]
fn test_analyze_procedure_populates_dml() {
    let mut proc = extract_single_procedure("demo-project/sql/pkg_order.sql", "create_order");
    let summaries = build_summaries_for_all_demo_files();
    let mut ctx = AnalysisContext::new();
    analyze_procedure(&mut proc, &summaries, &mut ctx).unwrap();

    // 守护：DML 必须被提取
    assert!(!proc.dml_statements.is_empty(),
        "create_order must have DML statements after analysis");

    // 守护：每个 DML 必须有完整信息
    for dml in &proc.dml_statements {
        assert!(!dml.method_id.is_empty(), "DML method_id must not be empty");
        assert!(!dml.sql_text.is_empty(), "DML sql_text must not be empty");
    }
}
```

### 层次 3: Golden Master 快照回归

**这是最关键的防线：** Rust 输出必须与 Python 输出逐字节一致（或明确标注允许的差异）。

**机制：**
1. 首先用 Python 运行 `fluxgauss.py -c fluxgauss.yaml`，将 `dest/` 作为 golden baseline
2. 将 baseline 存入 `tests/fixtures/golden/`（受版本控制）
3. Rust 每次运行后，逐文件对比输出与 golden

```rust
// tests/golden_master.rs
use std::path::Path;

/// 全量 golden master 测试：对比每个生成文件
#[test]
fn test_golden_master_all_files() {
    let golden_dir = Path::new("tests/fixtures/golden");
    let output_dir = run_fluxgauss_on_demo_project();

    // 收集 golden 中所有 .java, .xml, .yml 文件
    let golden_files = collect_generated_files(golden_dir);
    let output_files = collect_generated_files(&output_dir);

    // 守护：输出文件列表必须完全一致
    let golden_names: HashSet<_> = golden_files.iter()
        .map(|p| p.strip_prefix(golden_dir).unwrap().to_path_buf())
        .collect();
    let output_names: HashSet<_> = output_files.iter()
        .map(|p| p.strip_prefix(&output_dir).unwrap().to_path_buf())
        .collect();

    let missing: Vec<_> = golden_names.difference(&output_names).collect();
    let extra: Vec<_> = output_names.difference(&golden_names).collect();

    assert!(missing.is_empty(), "Missing files: {:?}", missing);
    assert!(extra.is_empty(), "Extra files: {:?}", extra);

    // 逐文件内容对比
    for rel_path in &golden_names {
        let golden_content = std::fs::read_to_string(golden_dir.join(rel_path)).unwrap();
        let output_content = std::fs::read_to_string(output_dir.join(rel_path)).unwrap();

        // 允许忽略的差异（时间戳、版本号等）
        let golden_normalized = normalize_for_comparison(&golden_content);
        let output_normalized = normalize_for_comparison(&output_content);

        assert_eq!(
            golden_normalized, output_normalized,
            "File mismatch: {}",
            rel_path.display()
        );
    }
}

/// 标准化：移除时间戳、版本号等非确定性内容
fn normalize_for_comparison(content: &str) -> String {
    // 移除 "Generated at" 时间戳行
    // 移除 "conversion-report-" 文件名中的时间戳
    // 保留所有其他内容
    content.lines()
        .filter(|line| !line.contains("Generated at"))
        .filter(|line| !line.contains("conversion-report-"))
        .collect::<Vec<_>>()
        .join("\n")
}
```

**Per-package 细粒度快照（开发迭代用）：**

开发某个模块时，不需要每次运行全量 golden master。提供 per-package 的细粒度对比：

```rust
#[test]
fn test_golden_pkg_order_service() {
    let golden = std::fs::read_to_string("tests/fixtures/golden/src/main/java/com/example/demo/service/OrderService.java").unwrap();
    let output = generate_service_java("pkg_order");

    assert_eq!(normalize(&golden), normalize(&output));
}
```

### 测试运行策略

```bash
# 快速反馈 (< 5秒) — 开发时持续运行
cargo test --lib                    # 只运行单元测试

# 模块集成 (< 30秒) — 提交前
cargo test --test integration       # 运行集成测试

# 全量回归 (< 2分钟) — 合并前/发布前
cargo test                          # 全部测试，包含 golden master

# 性能基准
cargo bench                         # 解析性能、内存使用基准
```

### 守护用例 (Guard Cases) — 防御性编程

以下模式贯穿所有模块，防止 Python 版中出现过的 "全局状态被意外修改" 类问题。

**模式 1: 构造器校验 — 不可能创建无效数据**

```rust
impl ProcedureInfo {
    pub fn new(name: String, package: String, proc_name: String) -> Self {
        // 守护：name 必须是 "package.proc_name" 格式
        debug_assert!(
            name.contains('.'),
            "ProcedureInfo.name must be 'package.proc_name', got: '{}'",
            name
        );
        Self {
            name,
            package,
            proc_name,
            // 所有集合字段初始化为空 — 不存在 "未初始化" 状态
            dml_statements: Vec::new(),
            service_calls: Vec::new(),
            java_logic_lines: Vec::new(),
            imports: BTreeSet::new(),
            local_vars: HashMap::new(),
            // ...
        }
    }
}
```

**模式 2: 类型状态模式 — 防止阶段错序**

```rust
/// Phase 1 的输出，只能传给 Phase 2。不可能传给 Phase 3。
pub struct ParsedPackages {
    packages: Vec<PackageInfo>,
    summaries: Vec<PackageSummary>,
    scan_ctx: ScanContext,
}

/// Phase 2 的输出，只能传给 Phase 3。
pub struct AnalyzedPackages {
    packages: Vec<PackageInfo>,
    analysis_ctx: AnalysisContext,
}

// 管线类型转换 — 一次性的，不可逆
impl ParsedPackages {
    pub fn analyze(self) -> AnalyzedPackages {
        let mut packages = self.packages;
        let mut analysis_ctx = AnalysisContext::new();
        // ... 执行分析 ...
        AnalyzedPackages { packages, analysis_ctx }
    }
}
```

**模式 3: 错误不丢弃 — 必须断言或传播**

```rust
// ❌ Python 风格 — 吞掉错误
// try: ast = json.loads(result.stdout)
// except: pass

// ✅ Rust 风格 — 错误必须被处理
fn parse_sql_file(path: &Path) -> Result<ParsedFile, ConversionError> {
    let content = read_sql_file(path)?;         // ? 传播 I/O 错误
    let tokens = Tokenizer::new(&content.text)
        .tokenize()
        .map_err(|e| ConversionError::parse(path, e))?;   // ? 转换错误类型
    let output = Parser::new(tokens)
        .parse()
        .map_err(|e| ConversionError::parse(path, e))?;
    Ok(ParsedFile { ast: output, source: path.to_path_buf() })
}
```

**模式 4: 编译期不可变引用 — 防止分析时意外修改**

```rust
// Phase 2 分析时，所有 PackageSummary 只读
fn analyze_procedure(
    proc: &mut ProcedureInfo,              // 当前 procedure 可变
    summaries: &HashMap<String, PackageSummary>,  // 其他包只读引用
    ctx: &mut AnalysisContext,              // 全局追踪状态可变
) -> Result<(), ConversionError> {
    // 编译器保证：summaries 不会被意外修改
    // 编译器保证：proc 和 ctx 不会产生别名冲突
}
```

**模式 5: 集合操作的边界守护**

```rust
// ❌ Python 风格 — 可能 panic
// parts[-1]  # 如果 parts 为空？

// ✅ Rust 风格 — 显式处理空集合
fn last_part(parts: &[String]) -> &str {
    parts.last().map(|s| s.as_str()).unwrap_or("")
}

// DML 提取时的守护
fn extract_dml_target(stmt_data: &serde_json::Value, stmt_type: &str) -> String {
    match stmt_type {
        "Insert" => {
            let table = stmt_data.get("table").and_then(|t| t.as_array());
            table
                .and_then(|arr| arr.last())
                .and_then(|v| v.as_str())
                .unwrap_or("unknown")
                .to_string()
        }
        // ...
    }
}
```

---

## 3. 核心数据结构

### 2.1 类型映射 (types.rs)

```rust
use std::collections::HashMap;
use once_cell::sync::Lazy;

pub static SQL_TO_JAVA: Lazy<HashMap<&'static str, &'static str>> = Lazy::new(|| {
    HashMap::from([
        ("bigint", "Long"),
        ("integer", "Integer"),
        ("numeric", "java.math.BigDecimal"),
        ("varchar", "String"),
        ("boolean", "Boolean"),
        ("timestamp", "java.sql.Timestamp"),
        ("date", "java.sql.Date"),
        // ... 完整映射见 Python SQL_TO_JAVA (254-293)
    ])
});

pub static SQL_TO_JDBC_TYPE: Lazy<HashMap<&'static str, &'static str>> = Lazy::new(|| {
    HashMap::from([
        ("bigint", "BIGINT"),
        ("integer", "INTEGER"),
        ("varchar", "VARCHAR"),
        // ... 完整映射见 Python SQL_TO_JDBC_TYPE (298-345)
    ])
});
```

### 2.2 过程信息 (types.rs)

```rust
#[derive(Debug, Clone)]
pub struct Parameter {
    pub name: String,
    pub java_type: String,
    pub sql_type: String,
    pub mode: Option<ParamMode>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ParamMode { In, Out, InOut }

impl Parameter {
    pub fn java_name(&self) -> String { snake_to_camel(&self.name) }
    pub fn is_out(&self) -> bool { matches!(self.mode, Some(ParamMode::Out) | Some(ParamMode::InOut)) }
    pub fn is_refcursor(&self) -> bool {
        let t = self.sql_type.to_lowercase();
        t == "refcursor" || t == "ref cursor" || t == "refcur" || t == "cursor"
    }
}

#[derive(Debug, Clone)]
pub struct DmlStatement {
    pub sql_type: DmlType,
    pub method_id: String,
    pub sql_text: String,
    pub result_type: Option<String>,
    pub parameter_types: HashMap<String, String>,
    pub optional_filters: Vec<String>,
    pub returns_list: bool,
    pub extra_params: Vec<(String, String)>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum DmlType { Select, Insert, Update, Delete }

#[derive(Debug, Clone)]
pub struct ServiceCall {
    pub service_name: String,
    pub method_name: String,
    pub args: Vec<String>,
    pub package_name: String,
}

#[derive(Debug, Clone)]
pub struct ProcedureInfo {
    pub name: String,           // "pkg_order.create_order"
    pub package: String,        // "pkg_order"
    pub proc_name: String,      // "create_order"
    pub is_function: bool,
    pub return_type: Option<String>,
    pub parameters: Vec<Parameter>,
    pub body: Option<ogsql_parser::ast::PlBlock>,  // 直接引用 parser 类型
    pub sql_text: String,

    // 生成产物 (分析阶段填充)
    pub dml_statements: Vec<DmlStatement>,
    pub service_calls: Vec<ServiceCall>,
    pub java_logic_lines: Vec<String>,
    pub imports: BTreeSet<String>,
    pub local_vars: HashMap<String, String>,      // name -> java_type
    pub local_var_defaults: HashMap<String, String>,
    pub table_refs: HashSet<String>,
    pub var_assignments: HashMap<String, String>,
    pub dynamic_sql_templates: HashMap<String, (String, Vec<String>)>,
    pub is_autonomous: bool,
    pub scheduler_tasks: Vec<SchedulerTask>,

    // 游标追踪
    pub open_cursors: HashMap<String, CursorInfo>,
    pub refcursor_out_params: HashSet<String>,
    pub cursor_decls: HashMap<String, String>,
    pub cursor_params: HashMap<String, Vec<String>>,

    // 自定义类型
    pub custom_types: HashMap<String, CustomTypeInfo>,

    // 源文件追踪
    pub source_file: String,
    pub source_path: String,
    pub source_start_line: u32,
    pub source_end_line: u32,
    pub leading_comments: Vec<CommentInfo>,
    pub inline_comments: Vec<CommentInfo>,
}

#[derive(Debug, Clone)]
pub struct PackageInfo {
    pub package_name: String,
    pub procedures: Vec<ProcedureInfo>,
    pub table_refs: HashSet<String>,
    pub package_vars: HashMap<String, VarInfo>,
    pub source_file: String,
    pub comments: Vec<CommentInfo>,
    pub java_package: String,
    pub custom_types: HashMap<String, CustomTypeInfo>,
}
```

### 2.3 轻量摘要 (用于 Phase 2 跨包分析)

```rust
/// Phase 1 提取的轻量摘要，用于跨包依赖解析
/// 内存占用：每个 procedure ~200 bytes，30K 文件约 ~3MB
#[derive(Debug, Clone)]
pub struct ProcedureSummary {
    pub name: String,
    pub proc_name: String,
    pub package: String,
    pub is_function: bool,
    pub return_type: Option<String>,
    pub parameters: Vec<Parameter>,     // 需要完整参数签名（类型 + mode）
    pub service_calls: Vec<ServiceCall>, // 需要跨包调用信息
}

#[derive(Debug, Clone)]
pub struct PackageSummary {
    pub name: String,
    pub java_package: String,
    pub procedures: Vec<ProcedureSummary>,
    pub package_vars: HashMap<String, VarInfo>,
}
```

---

## 3. 三阶段管线详细设计

### Phase 0: 预扫描 DDL

```rust
/// 扫描所有 SQL 文件中的 CREATE TABLE 语句，收集列类型信息
/// 用于 %TYPE 锚定声明
fn phase0_scan_ddl(sql_files: &[PathBuf], ctx: &mut ScanContext) -> Vec<ConversionError> {
    let errors: Vec<ConversionError> = sql_files
        .par_iter()
        .filter_map(|f| {
            let content = std::fs::read_to_string(f).ok()?;
            if content.to_lowercase().contains("create table") {
                // 直接用 ogsql-parser 解析 DDL
                let tokens = Tokenizer::new(&content).tokenize().ok()?;
                let stmts = Parser::new(tokens).parse().ok()?;
                extract_table_columns(&stmts, &mut ctx.type_overrides);
            }
            None
        })
        .collect();
    errors
}
```

### Phase 1: 解析 + 提取 (并行)

```rust
/// 并行解析所有 SQL 文件，提取 PackageInfo + PackageSummary
/// 增量模式：只解析 changed_files，其余从 AST 缓存加载
fn phase1_parse(
    sql_files: &[PathBuf],
    incremental: &IncrementalState,
    ctx: &mut ScanContext,
) -> (Vec<PackageInfo>, Vec<PackageSummary>, Vec<ConversionError>) {
    let results: Vec<(Option<PackageInfo>, Option<PackageSummary>, Option<ConversionError>)> =
        sql_files
            .par_iter()
            .map(|sql_file| {
                // 1. 增量检查
                if incremental.is_cached(sql_file) {
                    if let Some(ast) = incremental.load_cached_ast(sql_file) {
                        return extract_from_ast(&ast, sql_file, ctx);
                    }
                }

                // 2. 调用 ogsql-parser crate (无 subprocess!)
                let content = match read_sql_file(sql_file) {
                    Ok(c) => c,
                    Err(e) => return (None, None, Some(e.into())),
                };

                let tokens = match Tokenizer::new(&content.text).tokenize() {
                    Ok(t) => t,
                    Err(e) => return (None, None, Some(ConversionError::parse(sql_file, e))),
                };

                let parse_output = match Parser::new(tokens).parse() {
                    Ok(p) => p,
                    Err(e) => return (None, None, Some(ConversionError::parse(sql_file, e))),
                };

                // 3. 保存 AST 缓存
                incremental.save_cached_ast(sql_file, &parse_output);

                // 4. 提取 PackageInfo + Summary
                extract_from_ast(&parse_output, sql_file, ctx)
            })
            .collect();

    // 收集结果
    let packages: Vec<PackageInfo> = results.iter().filter_map(|r| r.0.clone()).collect();
    let summaries: Vec<PackageSummary> = results.iter().filter_map(|r| r.1.clone()).collect();
    let errors: Vec<ConversionError> = results.into_iter().filter_map(|r| r.2).collect();

    (packages, summaries, errors)
}
```

**关键区别 vs Python：**
- `ogsql-parser` 作为 crate 调用，**零进程创建开销**
- rayon 并行处理所有文件
- `PackageInfo` 和 `PackageSummary` 同时提取，不重复解析

### Phase 2: 全量分析 (顺序)

```rust
/// 分析所有 procedure 的 DML、跨包调用、OUT 参数提升
/// 需要：全量 PackageSummary (轻量) + 每个 procedure 的完整 ProcedureInfo
fn phase2_analyze(
    packages: &mut [PackageInfo],
    summaries: &[PackageSummary],
    ctx: &mut AnalysisContext,
) -> Vec<ConversionError> {
    let summary_map: HashMap<&str, &PackageSummary> = summaries
        .iter()
        .map(|s| (s.name.as_str(), s))
        .collect();

    let mut errors = Vec::new();

    for pkg in packages.iter_mut() {
        for proc in pkg.procedures.iter_mut() {
            // 注入包级变量到 local_vars
            inject_package_vars(proc, &pkg.package_vars);

            // 分析 procedure body
            if let Err(e) = analyze_procedure(proc, &summary_map, ctx) {
                errors.push(e);
                proc.java_logic_lines.push(format!("// ERROR: 转换失败 - {}", &errors.last().unwrap()));
                ctx.stub_procedures.insert((proc.name.clone(), proc.parameters.len()));
            }
        }
    }

    // OUT 参数类型提升
    for pkg in packages.iter_mut() {
        for proc in pkg.procedures.iter_mut() {
            promote_out_local_vars(proc, &summary_map);
        }
    }

    errors
}
```

### Phase 3: 分批生成 (并行)

```rust
/// 按依赖拓扑排序分批生成文件
/// 每个包独立生成，无需跨包状态
fn phase3_generate(
    packages: &[PackageInfo],
    affected: Option<&HashSet<String>>,
    ctx: &GenerationContext,
    incremental: &IncrementalState,
) -> (Vec<String>, Vec<ConversionError>) {
    // 1. 构建依赖图 + 拓扑排序
    let dep_graph = build_dependency_graph(packages);
    let batches = topological_batches(&dep_graph, affected);

    // 2. 分批并行生成
    let mut gen_errors = Vec::new();
    let mut generated = Vec::new();

    for batch in batches {
        let results: Vec<Result<String, ConversionError>> = batch
            .par_iter()
            .map(|pkg_name| {
                let pkg = packages.iter().find(|p| p.package_name == *pkg_name)
                    .ok_or_else(|| ConversionError::generation(pkg_name, "package not found"))?;

                // 检查 checkpoint (resume)
                if incremental.is_checkpoint_completed(pkg_name) {
                    return Ok(format!("{} (skipped - checkpoint)", pkg_name));
                }

                // 生成 4 个文件
                let base_path = PathBuf::from(&ctx.config.output_dir);
                generate_mapper_interface(&base_path, pkg, ctx)?;
                generate_mapper_xml(&base_path, pkg, ctx)?;
                generate_service_class(&base_path, pkg, ctx)?;
                generate_service_test(&base_path, pkg, ctx)?;

                // 更新 checkpoint
                incremental.checkpoint(pkg_name);

                Ok(pkg_name.clone())
            })
            .collect();

        for result in results {
            match result {
                Ok(name) => generated.push(name),
                Err(e) => gen_errors.push(e),
            }
        }
    }

    (generated, gen_errors)
}
```

---

## 4. 表达式翻译设计 (expr.rs — 核心难点)

### 设计思路

Python 的 `_expr_to_java()` 是一个 455 行的巨型函数，用 `for key, val in expr.items()` 遍历 AST dict。

Rust 版本用 **enum pattern match + visitor 模式**，编译器保证所有分支都被处理。

### 核心翻译函数

```rust
pub fn expr_to_java(
    expr: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
    ctx: &AnalysisContext,
    as_read: bool,
) -> String {
    match expr {
        Expr::ColumnRef(parts) => translate_column_ref(parts, proc, ctx, as_read),
        Expr::PlVariable(parts) => translate_pl_variable(parts, proc, ctx, as_read),
        Expr::Literal(lit) => literal_to_java(lit),
        Expr::BinaryOp { left, op, right } => translate_binary_op(left, op, right, proc, ctx),
        Expr::UnaryOp { op, expr } => translate_unary_op(op, expr, proc, ctx),
        Expr::FunctionCall { name, args, .. } => translate_function_call(name, args, proc, ctx),
        Expr::SpecialFunction { name, args, .. } => translate_special_function(name, args, proc, ctx),
        Expr::IsNull { expr, negated } => {
            let inner = expr_to_java(expr, proc, ctx, true);
            if *negated { format!("{} != null", inner) } else { format!("{} == null", inner) }
        }
        Expr::InList { expr, list, negated } => translate_in_list(expr, list, *negated, proc, ctx),
        Expr::Case { operand, whens, else_expr } => translate_case_expr(operand, whens, else_expr, proc, ctx),
        Expr::Parenthesized(inner) => format!("({})", expr_to_java(inner, proc, ctx, as_read)),
        Expr::TypeCast { expr, target_type } => translate_type_cast(expr, target_type, proc, ctx),
        Expr::Like { expr, pattern, negated } => translate_like(expr, pattern, *negated, proc, ctx),
        // ... 其他节点类型
        _ => {
            // 兜底：未处理的表达式类型
            format!("/* TODO: unsupported expr: {:?} */", expr)
        }
    }
}
```

**vs Python 的改进：**
1. **编译器完备性检查** — `match` 必须覆盖所有变体（或用 `_` 显式处理）
2. **类型安全** — 不可能访问错误类型的字段
3. **零开销** — 直接引用 AST struct，不经过 JSON dict 中间层

### SQL 函数映射

```rust
use std::collections::HashMap;
use once_cell::sync::Lazy;

enum FuncMapping {
    Direct(&'static str),                     // "Math.max"
    Template(&'static str),                    // "String.valueOf({args0}).toUpperCase()"
    Handler(fn(&[String], &ProcedureInfo) -> String),  // 自定义处理
    Skip,                                      // 返回第一个参数
}

static SQL_FUNCTION_MAP: Lazy<HashMap<&'static str, FuncMapping>> = Lazy::new(|| {
    HashMap::from([
        ("coalesce", FuncMapping::Direct("Objects.requireNonNullElse")),
        ("upper", FuncMapping::Template("String.valueOf({args}).toUpperCase()")),
        ("abs", FuncMapping::Handler(handler_abs)),
        ("to_char", FuncMapping::Handler(handler_to_char)),
        // ... 60+ 函数映射
    ])
});
```

### BigDecimal 算术处理

```rust
fn translate_binary_op(left: &Expr, op: &str, right: &Expr, proc: &ProcedureInfo, ctx: &AnalysisContext) -> String {
    let left_java = expr_to_java(left, proc, ctx, true);
    let right_java = expr_to_java(right, proc, ctx, true);
    let left_type = infer_expr_type(left, proc);
    let right_type = infer_expr_type(right, proc);

    let is_bd = left_type.contains("BigDecimal") || right_type.contains("BigDecimal");

    match op {
        "+" | "-" | "*" | "/" if is_bd => {
            let method = match op {
                "+" => "add", "-" => "subtract", "*" => "multiply", "/" => "divide",
                _ => unreachable!(),
            };
            let left_wrapped = maybe_wrap_bd(&left_java, &left_type);
            let right_wrapped = maybe_wrap_bd(&right_java, &right_type);
            format!("{}.{}({})", left_wrapped, method, right_wrapped)
        }
        ">" | "<" | ">=" | "<=" | "=" | "<>" if is_bd => {
            let cmp = match op {
                "=" => "==", "<>" => "!=", other => other,
            };
            format!("{}.compareTo({}) {} 0", left_java, right_java, cmp)
        }
        "||" => format!("String.valueOf({}).concat(String.valueOf({}))", left_java, right_java),
        "^" => format!("Math.pow({}, {})", maybe_double(&left_java), maybe_double(&right_java)),
        _ => format!("{} {} {}", left_java, java_op(op), right_java),
    }
}
```

---

## 5. 语句处理器设计 (statement.rs)

### 分发器

```rust
pub fn process_statement(
    stmt: &ogsql_parser::ast::PlStatement,
    proc: &mut ProcedureInfo,
    ctx: &mut AnalysisContext,
    dml_counter: &mut DmlCounter,
) -> Result<(), ConversionError> {
    match stmt {
        PlStatement::SqlStatement(sql) => process_sql_statement(sql, proc, dml_counter),
        PlStatement::If(if_data) => process_if(if_data, proc, ctx, dml_counter),
        PlStatement::For(for_data) => process_for(for_data, proc, ctx, dml_counter),
        PlStatement::While(while_data) => process_while(while_data, proc, ctx, dml_counter),
        PlStatement::Loop(loop_data) => process_loop(loop_data, proc, ctx, dml_counter),
        PlStatement::Return(ret) => process_return(ret, proc, ctx),
        PlStatement::Assignment(asgn) => process_assignment(asgn, proc, ctx),
        PlStatement::Raise(raise) => process_raise(raise, proc),
        PlStatement::Perform(perform) => process_perform(perform, proc, ctx),
        PlStatement::ProcedureCall(call) => process_procedure_call(call, proc, ctx),
        PlStatement::Open(open) => process_cursor_open(open, proc, ctx, dml_counter),
        PlStatement::Fetch(fetch) => process_cursor_fetch(fetch, proc),
        PlStatement::Close(close) => process_cursor_close(close, proc),
        PlStatement::Exit(exit) => process_exit(exit, proc),
        PlStatement::Continue(cont) => process_continue(cont, proc),
        PlStatement::Null => { proc.java_logic_lines.push("// no-op".into()); Ok(()) }
        PlStatement::Execute(exec) => process_execute(exec, proc, ctx, dml_counter),
        PlStatement::Block(block) => {
            for s in &block.body {
                process_statement(s, proc, ctx, dml_counter)?;
            }
            Ok(())
        }
        PlStatement::Commit => {
            let msg = if proc.is_autonomous {
                "// COMMIT — auto-committed by @Transactional(propagation = REQUIRES_NEW)"
            } else {
                "// COMMIT — auto-committed by Spring @Transactional boundary"
            };
            proc.java_logic_lines.push(msg.to_string());
            Ok(())
        }
        PlStatement::Rollback => {
            if proc.is_autonomous {
                proc.java_logic_lines.push("// ROLLBACK — auto-rolled-back by @Transactional(propagation = REQUIRES_NEW) on exception".into());
            } else {
                proc.java_logic_lines.push("TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();".into());
                proc.imports.insert("import org.springframework.transaction.interceptor.TransactionAspectSupport;".into());
            }
            Ok(())
        }
        PlStatement::Case(case_data) => process_case_stmt(case_data, proc, ctx, dml_counter),
        PlStatement::Goto(label) => {
            proc.java_logic_lines.push(format!("// GOTO {} — Java has no goto, manual refactor required", label));
            ctx.record_todo("GOTO", proc, &format!("label={}", label));
            Ok(())
        }
        PlStatement::Savepoint(sp) => {
            let sp_java = snake_to_camel(&sp.name);
            proc.java_logic_lines.push(format!("Savepoint {} = connection.setSavepoint(\"{}\");", sp_java, sp.name));
            proc.imports.insert("import java.sql.Savepoint;".into());
            Ok(())
        }
        _ => {
            proc.java_logic_lines.push(format!("// TODO: unhandled PL/pgSQL statement type: {:?}", stmt));
            Ok(())
        }
    }
}
```

---

## 6. 增量构建系统 (incremental.rs)

### 数据结构

```rust
#[derive(Serialize, Deserialize)]
struct Manifest {
    files: HashMap<String, FileEntry>,
}

#[derive(Serialize, Deserialize)]
struct FileEntry {
    hash: String,           // SHA-256 hex
    package: String,
    java_package: String,
}

#[derive(Serialize, Deserialize)]
struct GenerationCheckpoint {
    completed: HashSet<String>,
    updated_at: String,     // ISO 8601
}

struct IncrementalState {
    output_dir: PathBuf,
    cache_dir: PathBuf,     // .fluxgauss/
    manifest: Option<Manifest>,
    checkpoint: Option<GenerationCheckpoint>,
    force_full: bool,
}
```

### 核心方法

```rust
impl IncrementalState {
    /// 计算文件 SHA-256 哈希
    pub fn compute_hash(path: &Path) -> String {
        use sha2::{Sha256, Digest};
        let mut hasher = Sha256::new();
        let mut file = std::fs::File::open(path).unwrap();
        std::io::copy(&mut file, &mut hasher).unwrap();
        format!("{:x}", hasher.finalize())
    }

    /// 判断文件是否有缓存
    pub fn is_cached(&self, sql_file: &Path) -> bool {
        if self.force_full { return false; }
        let entry = self.manifest.as_ref()
            .and_then(|m| m.files.get(sql_file.to_str().unwrap()));
        match entry {
            Some(e) => {
                let current = Self::compute_hash(sql_file);
                current == e.hash && self.cached_ast_path(sql_file).exists()
            }
            None => false,
        }
    }

    /// AST 缓存路径
    fn cached_ast_path(&self, sql_file: &Path) -> PathBuf {
        let safe: String = sql_file.to_str().unwrap()
            .chars().map(|c| if c.is_alphanumeric() { c } else { '_' }).collect();
        self.cache_dir.join("ast").join(format!("{}.json", safe))
    }

    /// 保存 AST 到缓存 (atomic write)
    pub fn save_cached_ast(&self, sql_file: &Path, ast: &ogsql_parser::ParseOutput) {
        let path = self.cached_ast_path(sql_file);
        let temp = path.with_extension("json.tmp");
        let json = serde_json::to_string(ast).unwrap();
        std::fs::write(&temp, &json).unwrap();
        std::fs::rename(&temp, &path).unwrap();  // atomic
    }

    /// 构建依赖图
    pub fn build_dependency_graph(packages: &[PackageInfo]) -> HashMap<String, HashSet<String>> {
        let mut reverse_deps: HashMap<String, HashSet<String>> = HashMap::new();
        for pkg in packages {
            for proc in &pkg.procedures {
                for call in &proc.service_calls {
                    if !call.package_name.is_empty() && call.package_name != pkg.package_name {
                        reverse_deps.entry(call.package_name.clone())
                            .or_default()
                            .insert(pkg.package_name.clone());
                    }
                }
            }
        }
        reverse_deps
    }

    /// BFS 查找受影响的包
    pub fn find_dependent_packages(
        packages: &[PackageInfo],
        changed: &HashSet<String>,
    ) -> HashSet<String> {
        let reverse_deps = Self::build_dependency_graph(packages);
        let mut affected = changed.clone();
        let mut queue: VecDeque<String> = changed.iter().cloned().collect();

        while let Some(current) = queue.pop_front() {
            if let Some(deps) = reverse_deps.get(&current) {
                for dep in deps {
                    if !affected.contains(dep) {
                        affected.insert(dep.clone());
                        queue.push_back(dep.clone());
                    }
                }
            }
        }
        affected
    }
}
```

---

## 7. CLI 接口 (main.rs)

```rust
use clap::Parser;

#[derive(Parser)]
#[command(name = "fluxgauss", version = "1.0.0")]
#[command(about = "PL/pgSQL → Spring Boot/MyBatis Java 转换器")]
struct Cli {
    /// YAML 配置文件路径
    #[arg(short = 'c', long = "config")]
    config: Option<PathBuf>,

    /// 输出目录 (CLI 模式)
    #[arg(short = 'o', long = "output")]
    output: Option<PathBuf>,

    /// SQL 源文件 (CLI 模式)
    #[arg(short = 's', long = "sources", num_args = 1..)]
    sources: Vec<PathBuf>,

    /// 强制全量重新生成
    #[arg(long = "full")]
    full: bool,

    /// 从断点续做
    #[arg(long = "resume")]
    resume: bool,

    /// 转换报告输出路径
    #[arg(long = "report")]
    report: Option<PathBuf>,
}
```

---

## 8. 配置 (config.rs)

```rust
#[derive(Debug, Deserialize)]
pub struct AppConfig {
    pub output_dir: Option<String>,
    pub base_package: Option<String>,
    pub logger: Option<LoggerConfig>,
    pub database: Option<DatabaseConfig>,
    pub sources: Option<Vec<String>>,
    pub java_packages: Option<Vec<JavaPackageMapping>>,
    pub integration_test: Option<IntegrationTestConfig>,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum LoggerConfig {
    Preset(String),                    // "slf4j", "log4j2", etc.
    Custom(CustomLoggerConfig),
}

#[derive(Debug, Deserialize)]
pub struct CustomLoggerConfig {
    pub imports: Vec<String>,
    pub declaration: String,
    pub pom: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
pub struct DatabaseConfig {
    pub url: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
    pub driver: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct JavaPackageMapping {
    pub package: String,
    pub sources: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct IntegrationTestConfig {
    pub enabled: Option<bool>,
    pub mode: Option<String>,
    pub url: Option<String>,
    pub username: Option<String>,
    pub password: Option<String>,
}
```

---

## 9. 实现计划 (Task Breakdown)

> **纪律：** 每个 Task 都是 "先写测试 → 实现 → 跑测试通过 → commit"。
> 单元测试内联于各模块的 `#[cfg(test)] mod tests`。集成测试放 `tests/`。
> 每完成一个 Task，`cargo test` 必须全绿。

### Task 1: 项目骨架 + CLI + 配置

**Files:**
- Create: `Cargo.toml`, `crates/fluxgauss/Cargo.toml`
- Create: `crates/fluxgauss/src/main.rs`
- Create: `crates/fluxgauss/src/config.rs`
- Create: `crates/fluxgauss/src/lib.rs` (re-export 各模块)

**步骤：**
1. Workspace Cargo.toml with ogsql-parser path dependency
2. clap CLI 定义 (所有 8 个参数)
3. serde_yaml 配置解析 (AppConfig struct)
4. CLI 模式和配置模式分支
5. 日志初始化 (tracing)

**测试：** `config.rs` 内联 ~10 个测试：
- YAML 解析：preset logger, custom logger, 缺失字段默认值
- CLI 模式：缺少 -o/-s 报错, 配置文件不存在报错
- `integration_test` 各字段解析

**验证：** `cargo build` 成功，`cargo test` 通过，`cargo run -- --help` 输出正确帮助

---

### Task 2: 核心数据结构 + 类型映射 + 命名

**Files:**
- Create: `crates/fluxgauss/src/types.rs`
- Create: `crates/fluxgauss/src/type_map.rs`
- Create: `crates/fluxgauss/src/naming.rs`

**步骤：**
1. Parameter, ProcedureInfo, PackageInfo structs (含构造器校验)
2. ProcedureSummary, PackageSummary (轻量版)
3. SQL_TO_JAVA, SQL_TO_JDBC_TYPE 完整映射 (与 Python 254-345 行一一对应)
4. snake_to_camel, package_to_classname, java_method_name 命名工具

**测试：**
- `type_map.rs` 内联 ~20 个测试：每个 SQL 类型 → Java 类型映射, 未知类型返回 None, JDBC 类型映射
- `naming.rs` 内联 ~15 个测试：单字/多字/空字符串/带前缀包名/全大写

**验证：** `cargo test --lib` 通过

---

### Task 3: 增量构建系统

**Files:**
- Create: `crates/fluxgauss/src/incremental.rs`

**步骤：**
1. Manifest 加载/保存 (JSON 序列化)
2. SHA-256 哈希计算 (8KB 分块)
3. AST 缓存路径计算 + 读写 (atomic write)
4. Generation checkpoint (保存/加载/清除)
5. 依赖图构建 + BFS 遍历
6. stale 包清理

**测试：** 使用 `tempfile` crate 创建临时目录，~15 个测试：
- `test_hash_deterministic`: 同一文件两次哈希一致
- `test_hash_changes_on_edit`: 修改文件后哈希变化
- `test_cache_hit`: 保存后能加载回来
- `test_cache_miss`: 不存在的缓存返回 None
- `test_checkpoint_save_and_load`: 保存已完成的包名，加载后一致
- `test_checkpoint_clear`: 清除后文件不存在
- `test_dependency_graph_simple`: A→B→C 的反向依赖
- `test_dependency_graph_cycle`: 循环依赖不无限循环
- `test_find_dependent_packages_transitive`: A 改动 → B 和 C 都受影响
- `test_stale_cleanup`: 旧包文件被删除

**验证：** `cargo test --lib incremental` 通过

---

### Task 4: AST 提取 (extract.rs)

**Files:**
- Create: `crates/fluxgauss/src/extract.rs`
- Create: `tests/fixtures/` 目录 + 精选 SQL fixture 文件

**步骤：**
1. `extract_procedures()` — 从 ogsql-parser AST 提取 ProcedureInfo[]
2. `extract_parameters()` — 参数提取 + 类型映射 (IN/OUT/INOUT/REFCURSOR)
3. `extract_comments()` — SQL 注释提取 (single-line/block)
4. `extract_non_procedure_statements()` — DDL/grant/type 跳过记录
5. 编码检测 (UTF-8 → GB18030 → GBK fallback)
6. `_split_sql_statements()` — 多语句文件拆分

**测试：**
- 内联 ~15 个单元测试（用 ogsql-parser 解析后验证提取结果）：
  - `test_extract_simple_procedure`: 基本 procedure 的参数、返回类型
  - `test_extract_function`: FUNCTION vs PROCEDURE 标记
  - `test_extract_out_params`: OUT/INOUT 参数正确标记
  - `test_extract_refcursor`: REFCURSOR 参数识别
  - `test_extract_no_procedures`: 纯 DDL 文件返回空列表
  - `test_extract_comments`: 注释关联到正确的 procedure
  - `test_split_sql_statements`: 多语句正确拆分，行号偏移正确
- 集成测试 (用 demo-project 真实文件)：
  - `test_extract_pkg_order`: 对比 Python 提取的 procedure 数量和名称
  - `test_extract_tables_sql`: DDL 文件不产生 procedure

**验证：** 提取结果与 Python 版 `extract_procedures()` 输出一致

---

### Task 5: 表达式翻译 (expr.rs) — 最大模块

**Files:**
- Create: `crates/fluxgauss/src/expr.rs`

**步骤：**
1. `expr_to_java()` 主分发函数 (match on Expr enum)
2. ColumnRef, PlVariable 翻译 (含多部分引用、OUT 参数 .get())
3. BinaryOp 翻译 (BigDecimal 算术、字符串比较、Long 比较)
4. FunctionCall 翻译 (SQL_FUNCTION_MAP 60+ 函数)
5. SpecialFunction 翻译 (substr, overlay, position, extract, trim)
6. Case 表达式 (嵌套三元运算符)
7. InList, IsNull, Like, TypeCast
8. 类型推断 `infer_expr_type()`
9. 类型强制转换 `coerce_java_arg()`

**测试 (最多，~80+)：** 每个 Expr 变体至少 2 个测试（正常 + 边界）

按 AST 节点类型组织的测试矩阵：

| Expr 类型 | 测试用例 |
|-----------|---------|
| ColumnRef (简单) | `v_status` → `vStatus` |
| ColumnRef (OUT) | OUT 参数读取 → `paramName.get()` |
| ColumnRef (多部分) | `v_emp.status` → `vEmp.get("status")` |
| ColumnRef (特殊) | `FOUND` → `found`, `SQLERRM` → `__SQLERRM__` |
| ColumnRef (序列) | `seq.NEXTVAL` → `/* NEXTVAL */ null` |
| PlVariable | 与 ColumnRef 镜像测试 |
| Literal (字符串) | `'hello'` → `"hello"` |
| Literal (数字) | `42` → `"42"`, `3.14` → `"3.14"` |
| BinaryOp (BD 算术) | `a + b` (BigDecimal) → `a.add(b)` |
| BinaryOp (BD 比较) | `a > b` (BigDecimal) → `a.compareTo(b) > 0` |
| BinaryOp (字符串比较) | `a = b` (String) → `b.equals(a)` |
| BinaryOp (字符串连接) | `a \|\| b` → `String.valueOf(a).concat(...)` |
| BinaryOp (Long 比较) | `a > b` (Long) → `a.compareTo(b) > 0` |
| FunctionCall (60+) | 每个函数至少一个测试 |
| SpecialFunction | substr, overlay, position, extract, trim |
| Case (有 operand) | `CASE x WHEN 1 THEN 'a' ...` → 嵌套三元 |
| Case (无 operand) | `CASE WHEN cond THEN ...` → 嵌套三元 |
| IsNull | `x IS NULL` → `x == null`, `IS NOT NULL` → `x != null` |
| InList | `x IN (1,2,3)` → `Arrays.asList(1,2,3).contains(x)` |
| Like | `x LIKE '%abc%'` → `x.contains("abc")` |
| TypeCast | `CAST(x AS bigint)` → `(Long) x` |

**额外测试 — 从 Python pkg_builtin_funcs_test.sql 提取：**
该文件专门测试 60+ 内置函数翻译。逐个函数对比 Python 输出。

**验证：** `cargo test --lib expr` 全部通过

---

### Task 6: 语句处理器 (statement.rs + statements/*)

**Files:**
- Create: `crates/fluxgauss/src/statement.rs`
- Create: `crates/fluxgauss/src/statements/mod.rs`
- Create: `crates/fluxgauss/src/statements/sql.rs`
- Create: `crates/fluxgauss/src/statements/control_flow.rs`
- Create: `crates/fluxgauss/src/statements/cursor.rs`
- Create: `crates/fluxgauss/src/statements/assignment.rs`
- Create: `crates/fluxgauss/src/statements/call.rs`
- Create: `crates/fluxgauss/src/statements/raise.rs`
- Create: `crates/fluxgauss/src/statements/execute.rs`

**步骤：**
1. `process_statement()` 主分发 (20+ 语句类型)
2. SQL DML 处理 (SELECT/INSERT/UPDATE/DELETE + INTO + MyBatis 参数转换)
3. 控制流 (IF/FOR/WHILE/LOOP/CASE)
4. 游标操作 (OPEN/FETCH/CLOSE)
5. 赋值 + 过程调用 + PERFORM
6. RAISE 异常
7. EXECUTE 动态 SQL

**测试：** ~30 个，每种语句类型至少 1 个：
- `test_process_sql_select_into`: SELECT INTO → mapper 调用 + 变量赋值
- `test_process_sql_insert`: INSERT → mapper 调用 + DmlStatement 记录
- `test_process_if_else`: IF/ELSIF/ELSE → 正确的 Java if/else if/else
- `test_process_for_loop`: FOR ... IN ... LOOP → Java for 循环
- `test_process_cursor_open_fetch_close`: 完整游标生命周期
- `test_process_raise_exception`: RAISE EXCEPTION → throw new BusinessException
- `test_process_raise_notice`: RAISE NOTICE → log.info
- `test_process_assignment`: var := expr → Java 赋值
- `test_process_call_cross_package`: 跨包调用 → service.method()
- `test_process_execute_dynamic`: EXECUTE → 动态 SQL 模板
- `test_process_commit`: COMMIT → 注释
- `test_process_rollback`: ROLLBACK → TransactionAspectSupport
- `test_process_goto`: GOTO → stub + TODO 注释
- `test_process_nested_block`: 嵌套 Block → 扁平化 java_logic_lines

**验证：** 用 `pkg_test_patterns.sql` 和 `pkg_cursor_patterns.sql` 做集成测试

---

### Task 7: 分析器 (analyze.rs)

**Files:**
- Create: `crates/fluxgauss/src/analyze.rs`

**步骤：**
1. `analyze_procedure()` — 编排 statement 处理 + local_vars 收集
2. `promote_out_local_vars()` — OUT 参数类型提升 (→ AtomicReference)
3. 跨包调用解析 (self-call vs cross-package call, _find_target_proc)
4. 依赖图构建

**测试：**
- `test_analyze_simple_procedure`: 单个 procedure 的 DML 提取
- `test_analyze_cross_package_call`: 跨包调用生成 ServiceCall
- `test_analyze_self_call`: 同包调用生成 this.method()
- `test_promote_out_local_vars`: 局部变量提升为 AtomicReference
- `test_analyze_populates_local_vars`: 变量声明正确收集
- `test_analyze_stub_on_error`: 分析失败时标记 stub

**验证：** 对比 Python `analyze_procedure()` 对 demo-project 的输出

---

### Task 8: 代码生成 (generate/*)

**Files:**
- Create: `crates/fluxgauss/src/generate/mod.rs`
- Create: `crates/fluxgauss/src/generate/writer.rs` (CodeWriter)
- Create: `crates/fluxgauss/src/generate/service.rs`
- Create: `crates/fluxgauss/src/generate/mapper.rs`
- Create: `crates/fluxgauss/src/generate/test.rs`
- Create: `crates/fluxgauss/src/generate/skeleton.rs`
- Create: `crates/fluxgauss/src/generate/itest.rs`

**步骤：**
1. CodeWriter (缩进管理、行追加、导入收集) — 先写测试
2. pom.xml 生成
3. application.yml 生成
4. Service.java 生成 (最复杂：imports, fields, constructor, methods)
5. Mapper.java 接口生成
6. Mapper.xml 生成 (SQL + 参数占位符转换)
7. ServiceTest.java 生成
8. 集成测试生成 (itest 基础设施 + 测试类)

**测试：**
- `writer.rs` 内联 ~10 个测试：缩进出/入、空行、import 去重、输出格式
- 每种文件类型 1 个快照测试（用 `pkg_order` 的 golden output）：
  - `test_generate_order_service_java`: 输出与 golden/OrderService.java 对比
  - `test_generate_order_mapper_java`: 输出与 golden/OrderMapper.java 对比
  - `test_generate_order_mapper_xml`: 输出与 golden/OrderMapper.xml 对比
  - `test_generate_order_service_test`: 输出与 golden/OrderServiceTest.java 对比
- 边界测试：
  - `test_generate_stub_procedure`: stub 过程生成正确的 TODO 注释
  - `test_generate_no_dml`: 无 DML 的过程生成空 mapper
  - `test_generate_cross_service_injection`: 跨包注入生成正确的 @Autowired

**验证：** per-package 快照测试逐个通过

---

### Task 9: 管线编排 + 报告

**Files:**
- Create: `crates/fluxgauss/src/pipeline.rs`
- Create: `crates/fluxgauss/src/report.rs`
- Create: `crates/fluxgauss/src/context.rs`

**步骤：**
1. 三阶段管线编排 (phase0 → phase1 → phase2 → phase3)
2. 类型状态模式 (ParsedPackages → AnalyzedPackages)
3. 增量/全量模式切换
4. 进度条 (tracing info)
5. 转换报告生成 (Markdown)
6. 错误收集 + 汇总

**测试：**
- `test_pipeline_full_build`: 全量构建 demo-project，产出所有文件
- `test_pipeline_incremental`: 修改一个文件 → 只重新生成受影响的包
- `test_pipeline_resume`: checkpoint 恢复 → 跳过已完成的包
- `test_pipeline_empty_input`: 空文件列表 → 无崩溃
- `test_pipeline_parse_error`: 语法错误的 SQL → 错误记录但继续
- `test_pipeline_stale_cleanup`: 移除一个 source → 对应输出文件被删除
- `test_report_contains_all_procedures`: 报告列出所有 procedure 的映射

**验证：** 完整端到端管线跑通 demo-project

---

### Task 10: Golden Master 回归 + 性能基准

**Files:**
- Create: `tests/golden_master.rs` (全量快照对比)
- Create: `crates/fluxgauss/benches/parse_benchmark.rs`
- Create: `tests/fixtures/golden/` (从 Python dest/ 复制 113 个文件)

**步骤：**
1. 用 Python 运行 `fluxgauss.py -c fluxgauss.yaml`，将 dest/ 复制为 golden baseline
2. 实现 `tests/golden_master.rs` — 逐文件对比 (113 个文件)
3. 实现内容标准化函数 (去除时间戳等非确定性内容)
4. 实现 per-package 细粒度快照 (开发迭代用)
5. 性能基准：单文件解析、全量 demo-project、增量模式
6. 运行 `cargo test` 全量回归 + `cd dest-rust && mvn compile` 验证

**测试：**
- `test_golden_master_all_files`: 113 个文件逐个对比
- `test_golden_master_file_count`: 文件数量一致 (不缺不多)
- `test_mvn_compile`: 生成的项目可通过 `mvn compile`
- `bench_parse_single_file`: 单文件解析耗时 < 10ms
- `bench_full_demo_project`: 全量 demo-project < 2s

**验证：**
1. `cargo test` 全绿
2. `mvn compile` 通过
3. 性能基准数据记录

---

## 10. 性能预期

| 指标 | Python (当前) | Rust (预期) | 提升 |
|------|-------------|------------|------|
| 单文件解析 | ~200ms (subprocess) | ~5ms (crate) | 40x |
| 30000 文件全量解析 | 1-8 小时 | 30-60 秒 | 60-500x |
| 30000 文件增量 (10 改动) | 5-30 分钟 | 5-15 秒 | 30-120x |
| 内存峰值 (30000 文件) | 4-12 GB | 300-800 MB | 5-15x |
| 单线程分析 | 不可并行 | 顺序但快 10x | 10x |
| 文件生成 | 不可并行 | rayon 并行 | N 核倍 |

## 11. 风险和缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| ogsql-parser AST 类型不完全匹配 Python JSON 格式 | 中 | 编写适配层；优先用 parser 的 Rust 类型。Task 4 用真实文件验证 |
| 表达式翻译边界情况遗漏 | 高 | Task 5 要求 80+ 测试逐个 Expr 变体覆盖；pkg_builtin_funcs_test.sql 逐函数对比 |
| 生成的 Java 代码与 Python 版不一致 | 高 | Task 10 golden master 113 文件逐字节对比；per-package 快照在 Task 8 逐步验证 |
| 性能不达预期 | 低 | Task 10 benchmark 分阶段定位瓶颈；criterion 基准持续跟踪 |
| 大规模并行导致的死锁 | 中 | Phase 2 保持顺序；Phase 1/3 独立无共享状态；Rayon work-stealing 无锁 |
| 模块间耦合导致修改牵连 | 中 | 类型状态模式强制阶段边界；每个 Task 独立测试，`cargo test` 全绿才能合入 |

## 12. 与 Python 版本的兼容性

- **输出文件格式**：必须一致（同一个 mvn compile 能通过）
- **CLI 接口**：完全兼容（同样的 flags、配置文件格式）
- **增量缓存**：**不兼容** — Rust 版本使用自己的缓存格式，首次运行视为全量构建
- **转换报告**：格式兼容（Markdown）

## 13. 测试总计

| 层次 | 测试数 | 运行频率 | 耗时 |
|------|--------|---------|------|
| 模块单元测试 (内联 `#[cfg(test)]`) | ~190+ | 每次保存 | < 3s |
| 集成测试 (`tests/`) | ~20 | 每次提交 | < 30s |
| Golden Master 回归 (`tests/golden_master.rs`) | 113 文件对比 | 合并/发布前 | < 60s |
| 性能基准 (criterion) | ~5 | 发版前 | ~2min |

**底线：** 任何时候 `cargo test` 必须全绿。这是不可协商的。

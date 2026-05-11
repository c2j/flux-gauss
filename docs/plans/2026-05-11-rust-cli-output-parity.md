# Rust CLI Output Parity — Progress Bar, Statistics, Report, Logs

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Rust converter's CLI output match the Python version — config display, 3-phase progress bar, full statistics after "Done!", auto-generated conversion report, and log file.

**Architecture:** Add a `progress` module for terminal progress bars (zero external deps, raw `\r` + Unicode blocks matching Python's `_progress_bar`). Extend `PipelineResult` to carry all statistics (skipped, unresolved, stubs). Extend `ConversionReport` with DML/cross-call/stub fields and auto-save to `.fluxgauss/reports/`. Add a `logging` module for timestamped log files under `.fluxgauss/logs/`. Wire everything in `main.rs`.

**Tech Stack:** Rust (no new crate deps), existing `chrono` for timestamps.

---

## Reference: Python Output (Target)

```
  Output:    ./dest
  Config:    demo-project/fluxgauss.yaml
  Input:     32 SQL file(s)

  Parse    [████████████████████████████████████████████████████████████████████████] 32/32 100.0%  ✓
  Analyze  [████████████████████████████████████████████████████████████████████████] 132/132 100.0%  ✓
  Generate [████████████████████████████████████████████████████████████████████████] 25/25 100.0%  ✓

  Done!
    Packages:    25
    Procedures:  132
    DML stmts:   260 (extracted as iBatis mapper methods)
    Cross-calls: 64
    Test files:  25 (generated unit tests)
    IT files:    25 (generated integration tests, remote mode)
    Skipped:     127 (non-procedure SQL)
    Unresolved:  20 (cross-package calls, 详见转换报告)
    Stubs:       9 (需人工审查, 详见转换报告)

    详细处理日志: dest/.fluxgauss/logs/conversion-latest.log

  📄 转换报告:
    - dest/.fluxgauss/reports/conversion-report-20260511_110719.md
    - dest/.fluxgauss/logs/conversion-latest.log

  Output: /absolute/path/to/dest
```

---

## Task 1: Add `progress` Module

**Files:**
- Create: `crates/fluxgauss/src/progress.rs`
- Modify: `crates/fluxgauss/src/lib.rs`

**Step 1: Write the progress module**

Create `crates/fluxgauss/src/progress.rs`:

```rust
//! Terminal progress bar matching the Python converter's output.
//!
//! Usage:
//!   progress_bar("Parse", 5, 10, "some_file.sql");
//!   progress_done("Parse", 10);
//!
//! Produces:
//!   Parse    [████████████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 5/10  50.0%  some_file.sql
//!   Parse    [████████████████████████████████████████████████████████████████████] 10/10 100.0%  ✓

const BAR_WIDTH: usize = 72;

/// Update an in-progress progress bar line (overwrites current line).
/// Only writes if stdout is a terminal.
pub fn progress_bar(phase: &str, current: usize, total: usize, status: &str) {
    if !atty_check() {
        return;
    }
    let pct = if total > 0 { current as f64 / total as f64 } else { 1.0 };
    let filled = (BAR_WIDTH as f64 * pct) as usize;
    let bar: String = "█".repeat(filled) + &"░".repeat(BAR_WIDTH.saturating_sub(filled));

    let label = format!("{:<8}", phase);
    let mut line = format!("\r  {} [{}] {}/{} {:5.1}%", label, bar, current, total, pct * 100.0);

    if !status.is_empty() {
        let truncated = if status.len() > 41 {
            format!("{}…", &status[..40])
        } else {
            status.to_string()
        };
        line.push_str(&format!("  {:<42}", truncated));
    }
    // Pad to 120 chars to clear any previous longer line
    eprint!("\r{: <120}", "");
    eprint!("\r{}", line);

    // Flush manually
    use std::io::Write;
    let _ = std::io::stderr().flush();
}

/// Finalize a progress bar phase: show 100% with ✓ and newline.
pub fn progress_done(phase: &str, total: usize) {
    if !atty_check() {
        return;
    }
    let bar: String = "█".repeat(BAR_WIDTH);
    let label = format!("{:<8}", phase);
    eprint!("\r  {} [{}] {}/{} 100.0%  ✓\n", label, bar, total, total);
    let _ = std::io::stderr().flush();
}

/// Check if stderr is a terminal (TTY).
/// Uses a simple heuristic: always true for now, matching Python's
/// `sys.stdout.isatty()` behavior when run from terminal.
/// For a proper check, could use `isatty` crate, but keeping it zero-dep.
fn atty_check() -> bool {
    // Check if stderr is a terminal using libc
    unsafe { libc_isatty() }
}

#[cfg(unix)]
fn libc_isatty() -> bool {
    unsafe { libc::isatty(2) != 0 }
}

#[cfg(windows)]
fn libc_isatty() -> bool {
    // On Windows, always show progress (conservative)
    true
}
```

**Note on `libc`**: The `libc` crate is already transitively available through many deps, but to keep it explicit, we have two options:

**Option A (preferred):** Use `std::io::IsTerminal` which is stable since Rust 1.70 (edition 2021).

Replace the `atty_check` / `libc_isatty` with:

```rust
use std::io::IsTerminal;

fn atty_check() -> bool {
    std::io::stderr().is_terminal()
}
```

This is zero-dep — `IsTerminal` is in `std::io`. If MSRV is below 1.70, add `is-terminal` crate instead.

**Step 2: Register module in `lib.rs`**

In `crates/fluxgauss/src/lib.rs`, add:

```rust
pub mod progress;
```

**Step 3: Test manually**

```bash
cd crates/fluxgauss
cargo build
# Run the converter to see progress bars in action (verified in Task 6)
```

**Step 4: Commit**

```
feat(rust): add progress bar module matching Python output
```

---

## Task 2: Extend `PipelineResult` with Statistics

**Files:**
- Modify: `crates/fluxgauss/src/pipeline.rs` (lines 10-36 `PipelineResult`, lines 18-37 `run_pipeline`, lines 39-142 `phase1_parse`, lines 144-178 `phase2_analyze`)
- Modify: `crates/fluxgauss/src/context.rs` (read-only, already has the fields we need)

**Step 1: Extend `PipelineResult` struct**

In `crates/fluxgauss/src/pipeline.rs`, replace the struct at line 10:

```rust
pub struct PipelineResult {
    pub packages: Vec<crate::types::PackageInfo>,
    pub generated_files: Vec<String>,
    pub errors: Vec<ConversionError>,
    pub warnings: Vec<String>,
    pub skipped: Vec<crate::types::SkippedItem>,
    // New fields:
    pub unresolved_calls: Vec<String>,
    pub stub_count: usize,
    pub stub_reasons: std::collections::HashMap<(String, usize), Vec<String>>,
    pub test_file_count: usize,
    pub itest_file_count: usize,
    pub total_dml: usize,
    pub total_cross_calls: usize,
}
```

**Step 2: Fix `run_pipeline` — thread skipped data + collect stats from context**

The current `run_pipeline` (line 18-37) drops `skipped` from `ParsedPackages` and never reads `AnalysisContext`. Rewrite:

```rust
pub fn run_pipeline(
    sql_files: &[PathBuf],
    config: &AppConfig,
    incremental: &mut IncrementalState,
) -> PipelineResult {
    let base_package = config.base_package_or_default();

    let mut ctx = AnalysisContext::new();
    let parsed = phase1_parse(sql_files, config, incremental);
    let analyzed = phase2_analyze(parsed, &mut ctx);
    let (generated, test_count, itest_count, errors) = phase3_generate(&analyzed, config, incremental);

    let packages = analyzed.packages;
    let skipped = analyzed.skipped;

    // Compute aggregate stats
    let total_dml: usize = packages.iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.dml_statements.len())
        .sum();
    let total_cross_calls: usize = packages.iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.service_calls.len())
        .sum();

    PipelineResult {
        packages,
        generated_files: generated,
        errors,
        warnings: Vec::new(),
        skipped,
        unresolved_calls: ctx.unresolved_calls.clone(),
        stub_count: ctx.stub_procedures.len(),
        stub_reasons: ctx.stub_reasons.clone(),
        test_file_count: test_count,
        itest_file_count: itest_count,
        total_dml,
        total_cross_calls,
    }
}
```

**Step 3: Fix `phase2_analyze` — accept `&mut AnalysisContext` and return it**

Current signature (line 144): `fn phase2_analyze(parsed: ParsedPackages) -> AnalyzedPackages`

Change to accept and return the context:

```rust
fn phase2_analyze(
    parsed: ParsedPackages,
    ctx: &mut AnalysisContext,
) -> AnalyzedPackages {
    let summary_map: std::collections::HashMap<String, &PackageSummary> = parsed
        .summaries
        .iter()
        .map(|s| (s.name.clone(), s))
        .collect();

    let mut errors = Vec::new();
    let mut packages = parsed.packages;

    for pkg in &mut packages {
        for proc in &mut pkg.procedures {
            let proc_summaries: std::collections::HashMap<String, PackageSummary> = parsed
                .summaries
                .iter()
                .map(|s| (s.name.clone(), s.clone()))
                .collect();

            if let Err(e) = crate::analyze::analyze_procedure(proc, &proc_summaries, ctx) {
                errors.push(e);
            }
        }
        for proc in &mut pkg.procedures {
            crate::analyze::promote_out_local_vars(proc);
        }
    }

    AnalyzedPackages {
        packages,
        summaries: parsed.summaries,
        skipped: parsed.skipped,
        errors,
    }
}
```

**Step 4: Fix `phase3_generate` — return test/itest counts**

Change signature and return type to include counts:

```rust
fn phase3_generate(
    analyzed: &AnalyzedPackages,
    config: &AppConfig,
    _incremental: &IncrementalState,
) -> (Vec<String>, usize, usize, Vec<ConversionError>) {
    // ... existing code ...
    let mut test_count = 0usize;
    let mut itest_count = 0usize;

    for pkg in &analyzed.packages {
        // ... existing generation code ...

        // After write_service_test succeeds:
        test_count += 1;

        // After write_itest_class succeeds (inside the enabled check):
        if config.integration_test.as_ref().and_then(|it| it.enabled).unwrap_or(false) {
            // ... itest generation ...
            itest_count += 1;
        }
    }

    (generated, test_count, itest_count, errors)
}
```

**Step 5: Commit**

```
feat(rust): extend PipelineResult with full statistics tracking
```

---

## Task 3: Add `Config` Line to Header Output

**Files:**
- Modify: `crates/fluxgauss/src/main.rs` (lines 60-66)

**Step 1: Add Config line and store config_path**

In `main.rs`, change the `run` function. Currently at line 60:

```rust
fn run(cli: Cli) -> Result<(), Box<dyn std::error::Error>> {
    let config_path_str = cli.config.as_ref()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "CLI mode".into());

    let (config, sql_files, output_dir) = resolve_inputs(&cli)?;

    let base_package = config.base_package_or_default();
    println!("  Output:     {}", output_dir);
    println!("  Config:     {}", config_path_str);
    println!("  Package:    {}", base_package);
    println!("  Input:      {} SQL file(s)", sql_files.len());
    // ... rest unchanged
```

**Step 2: Commit**

```
feat(rust): show config file path in CLI header
```

---

## Task 4: Wire Progress Bars into Pipeline

**Files:**
- Modify: `crates/fluxgauss/src/pipeline.rs` (all 3 phase functions)

**Step 1: Add progress callbacks to each phase**

Define a callback trait in `pipeline.rs`:

```rust
/// Callback for reporting pipeline progress.
pub trait ProgressReporter: Send + Sync {
    fn on_progress(&self, phase: &str, current: usize, total: usize, status: &str);
    fn on_done(&self, phase: &str, total: usize);
}
```

Add a `reporter: Option<&dyn ProgressReporter>` parameter to `run_pipeline`, `phase1_parse`, `phase2_analyze`, and `phase3_generate`.

**Step 2: Instrument `phase1_parse`**

Inside the `for sql_file in sql_files` loop, after processing each file:

```rust
if let Some(r) = &reporter {
    let basename = sql_file.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let label = if was_cached { "Cached" } else { "Parsing" };
    r.on_progress("Parse", idx, total, &format!("{} {}", label, basename));
}
```

After the loop:
```rust
if let Some(r) = &reporter {
    r.on_done("Parse", total);
}
```

**Step 3: Instrument `phase2_analyze`**

```rust
let total = packages.len(); // count all procedures
let mut idx = 0;
for pkg in &mut packages {
    for proc in &mut pkg.procedures {
        idx += 1;
        if let Some(r) = &reporter {
            r.on_progress("Analyze", idx, total, &proc.name);
        }
        // ... analysis ...
    }
}
if let Some(r) = &reporter {
    r.on_done("Analyze", total);
}
```

**Step 4: Instrument `phase3_generate`**

```rust
let total = analyzed.packages.len();
for (idx, pkg) in analyzed.packages.iter().enumerate() {
    if let Some(r) = &reporter {
        r.on_progress("Generate", idx + 1, total, &pkg.package_name);
    }
    // ... generation ...
}
if let Some(r) = &reporter {
    r.on_done("Generate", total);
}
```

**Step 5: Implement `ProgressReporter` in `main.rs`**

```rust
struct TerminalProgress;

impl pipeline::ProgressReporter for TerminalProgress {
    fn on_progress(&self, phase: &str, current: usize, total: usize, status: &str) {
        fluxgauss::progress::progress_bar(phase, current, total, status);
    }
    fn on_done(&self, phase: &str, total: usize) {
        fluxgauss::progress::progress_done(phase, total);
    }
}
```

In `run()`:
```rust
let reporter = TerminalProgress;
let result = pipeline::run_pipeline(&sql_files, &config, &mut incremental, Some(&reporter));
```

**Step 6: Commit**

```
feat(rust): add progress bar reporting to all 3 pipeline phases
```

---

## Task 5: Add Full Statistics Summary in `main.rs`

**Files:**
- Modify: `crates/fluxgauss/src/main.rs` (lines 78-94)

**Step 1: Replace the current "Results" section**

Replace lines 78-94 with:

```rust
    let total_packages = result.packages.len();
    let total_procedures: usize = result.packages.iter().map(|p| p.procedures.len()).sum();
    let total_generated = result.generated_files.len();

    // Empty line after progress bars
    println!();

    println!("  Done!");
    println!("    Packages:    {}", total_packages);
    println!("    Procedures:  {}", total_procedures);
    println!("    DML stmts:   {} (extracted as iBatis mapper methods)", result.total_dml);
    println!("    Cross-calls: {}", result.total_cross_calls);
    println!("    Test files:  {} (generated unit tests)", result.test_file_count);

    let itest_enabled = config.integration_test
        .as_ref()
        .and_then(|it| it.enabled)
        .unwrap_or(false);
    if itest_enabled {
        let itest_mode = config.integration_test
            .as_ref()
            .and_then(|it| it.mode.clone())
            .unwrap_or_else(|| "remote".into());
        println!("    IT files:    {} (generated integration tests, {} mode)",
                 result.itest_file_count, itest_mode);
    }

    println!("    Skipped:     {} (non-procedure SQL)", result.skipped.len());

    if !result.unresolved_calls.is_empty() {
        println!("    Unresolved:  {} (cross-package calls, 详见转换报告)",
                 result.unresolved_calls.len());
    }

    if result.stub_count > 0 {
        println!("    Stubs:       {} (需人工审查, 详见转换报告)", result.stub_count);
    }

    if !result.errors.is_empty() {
        println!("\n  ── Errors ({} total) ──", result.errors.len());
        for err in &result.errors {
            println!("  ✗ {}", err);
        }
    }
```

Remove the old `println!("Done. {} package(s), {} file(s) generated.", ...)` line.

**Step 2: Commit**

```
feat(rust): print full statistics summary matching Python output
```

---

## Task 6: Auto-Generate Conversion Report

**Files:**
- Modify: `crates/fluxgauss/src/report.rs` (full rewrite of struct + methods)

**Step 1: Extend `ConversionReport` with all fields matching Python's `ConversionReport`**

```rust
use std::collections::HashMap;
use std::path::Path;

use crate::types::{PackageInfo, ProcedureMapping, SkippedItem};

#[derive(Debug, Clone)]
pub struct ConversionReport {
    pub timestamp: String,
    pub config_path: String,
    pub output_dir: String,
    pub total_files: usize,
    pub total_packages: usize,
    pub total_procedures: usize,
    pub total_dml: usize,
    pub total_cross_calls: usize,
    pub mappings: Vec<ProcedureMapping>,
    pub skipped: Vec<SkippedItem>,
    pub errors: Vec<String>,
    pub unresolved_calls: Vec<String>,
    pub stub_count: usize,
    pub stub_reasons: HashMap<(String, usize), Vec<String>>,
}
```

**Step 2: Add `build_report` function**

```rust
impl ConversionReport {
    pub fn new() -> Self {
        Self {
            timestamp: chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
            config_path: String::new(),
            output_dir: String::new(),
            total_files: 0,
            total_packages: 0,
            total_procedures: 0,
            total_dml: 0,
            total_cross_calls: 0,
            mappings: Vec::new(),
            skipped: Vec::new(),
            errors: Vec::new(),
            unresolved_calls: Vec::new(),
            stub_count: 0,
            stub_reasons: HashMap::new(),
        }
    }
}

/// Build a ConversionReport from pipeline results.
pub fn build_report(
    packages: &[PackageInfo],
    skipped: Vec<SkippedItem>,
    unresolved_calls: Vec<String>,
    stub_count: usize,
    config_path: &str,
    output_dir: &str,
    total_files: usize,
) -> ConversionReport {
    let total_procedures: usize = packages.iter().map(|p| p.procedures.len()).sum();
    let total_dml: usize = packages.iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.dml_statements.len())
        .sum();
    let total_cross_calls: usize = packages.iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.service_calls.len())
        .sum();

    let mut mappings = Vec::new();
    for pkg in packages {
        let class_name = crate::naming::package_to_classname(&pkg.package_name);
        for proc in &pkg.procedures {
            let mapper_methods: Vec<String> = proc.dml_statements.iter()
                .map(|d| d.method_id.clone())
                .collect();
            mappings.push(ProcedureMapping {
                sql_procedure: proc.proc_name.clone(),
                sql_package: pkg.package_name.clone(),
                java_service: format!("{}Service", class_name),
                java_method: crate::naming::java_method_name(&proc.proc_name),
                is_stub: proc.is_stub(),
                notes: Vec::new(),
            });
        }
    }

    ConversionReport {
        timestamp: chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        config_path: config_path.to_string(),
        output_dir: output_dir.to_string(),
        total_files,
        total_packages: packages.len(),
        total_procedures,
        total_dml,
        total_cross_calls,
        mappings,
        skipped,
        errors: Vec::new(),
        unresolved_calls,
        stub_count,
        stub_reasons: HashMap::new(),
    }
}
```

**Step 3: Rewrite `to_markdown()` matching Python's `_render_report_markdown`**

The markdown should include:
- Header with timestamp, config path, output dir
- Overview table (packages, procedures, DML, cross-calls, stubs, skipped)
- SQL → Java mapping table (per-file, per-procedure)
- Skipped items list
- Unresolved calls list
- Stubs list

```rust
pub fn to_markdown(&self) -> String {
    let mut lines = Vec::new();
    lines.push("# FluxGauss 转换报告".into());
    lines.push(String::new());
    lines.push(format!("**生成时间**: {}  ", self.timestamp));
    lines.push(format!("**配置文件**: {}  ", self.config_path));
    lines.push(format!("**输出目录**: `{}`", self.output_dir));
    lines.push(String::new());
    lines.push("---");
    lines.push(String::new());
    lines.push("## 概览");
    lines.push(String::new());
    lines.push("| 指标 | 数量 |");
    lines.push("|------|------|");
    lines.push(format!("| 转换的包 | {} |", self.total_packages));
    lines.push(format!("| 存储过程/函数 | {} |", self.total_procedures));
    lines.push(format!("| 提取的 DML (MyBatis mapper) | {} |", self.total_dml));
    lines.push(format!("| 跨包调用 | {} |", self.total_cross_calls));
    let converted = self.mappings.iter().filter(|m| !m.is_stub).count();
    lines.push(format!("| 成功转换 | {} |", converted));
    if self.stub_count > 0 {
        lines.push(format!("| ⚠️ Stub（需人工审查） | {} |", self.stub_count));
    }
    lines.push(format!("| ⏭ 跳过（不涉及存储过程） | {} |", self.skipped.len()));
    if !self.unresolved_calls.is_empty() {
        lines.push(format!("| ⚠️ 未解析的跨包调用 | {} |", self.unresolved_calls.len()));
    }
    lines.push(String::new());

    // Procedure mappings table
    if !self.mappings.is_empty() {
        lines.push("---");
        lines.push(String::new());
        lines.push("## SQL → Java 映射");
        lines.push(String::new());
        lines.push("| SQL Procedure | Package | Java Service | Java Method | Stub |");
        lines.push("|---|---|---|---|---|");
        for m in &self.mappings {
            lines.push(format!(
                "| `{}` | `{}` | `{}` | `{}` | {} |",
                m.sql_procedure, m.sql_package, m.java_service, m.java_method,
                if m.is_stub { "⚠️ Stub" } else { "✅" }
            ));
        }
        lines.push(String::new());
    }

    // Skipped items
    if !self.skipped.is_empty() {
        lines.push("---");
        lines.push(String::new());
        lines.push("## ⏭ 跳过项 — 不涉及存储过程，仅作参考");
        lines.push(String::new());
        for s in &self.skipped {
            lines.push(format!("- [{}] {} ({})", s.item_type, s.name, s.reason));
        }
        lines.push(String::new());
    }

    // Unresolved calls
    if !self.unresolved_calls.is_empty() {
        lines.push("---");
        lines.push(String::new());
        lines.push("## ⚠️ 未解析的跨包调用");
        lines.push(String::new());
        for call in &self.unresolved_calls {
            lines.push(format!("- {}", call));
        }
        lines.push(String::new());
    }

    // Errors
    if !self.errors.is_empty() {
        lines.push("---");
        lines.push(String::new());
        lines.push("## ❌ 错误");
        lines.push(String::new());
        for e in &self.errors {
            lines.push(format!("- {}", e));
        }
    }

    lines.join("\n")
}
```

**Step 4: Auto-save report to `.fluxgauss/reports/`**

```rust
/// Save report to `.fluxgauss/reports/` with timestamp + latest copy.
/// Returns the paths of all files written.
pub fn save_auto(&self, output_dir: &Path) -> Vec<String> {
    let content = self.to_markdown();
    let mut written = Vec::new();

    let report_dir = output_dir.join(".fluxgauss").join("reports");
    if let Ok(()) = std::fs::create_dir_all(&report_dir) {
        let ts = self.timestamp
            .replace(" ", "_")
            .replace(":", "")
            .replace("-", "");
        let ts_path = report_dir.join(format!("conversion-report-{}.md", ts));
        if std::fs::write(&ts_path, &content).is_ok() {
            written.push(ts_path.to_string_lossy().into_owned());
        }

        let latest_path = report_dir.join("conversion-report-latest.md");
        let _ = std::fs::write(&latest_path, &content);
    }

    written
}
```

**Step 5: Update `main.rs` to call `build_report` + `save_auto`**

In `main.rs`, after the pipeline result, replace the old `--report` block with:

```rust
    // Auto-generate conversion report
    let report = fluxgauss::report::build_report(
        &result.packages,
        result.skipped,
        result.unresolved_calls,
        result.stub_count,
        &config_path_str,
        &output_dir,
        sql_files.len(),
    );
    let report_paths = report.save_auto(std::path::Path::new(&output_dir));

    // If user specified --report, also write to that path
    if let Some(report_path) = &cli.report {
        if let Ok(()) = report.save(report_path) {
            // add to report_paths
        }
    }
```

After the statistics section, add:

```rust
    // Log and report paths
    println!();
    // (log path printed here — see Task 7)
    if !report_paths.is_empty() {
        println!("  📄 转换报告:");
        for p in &report_paths {
            println!("    - {}", p);
        }
    }
    println!("\n  Output: {}", std::path::Path::new(&output_dir).canonicalize()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| output_dir.clone()));
```

**Step 6: Commit**

```
feat(rust): auto-generate conversion report with full statistics
```

---

## Task 7: Add Log File Generation

**Files:**
- Create: `crates/fluxgauss/src/logging.rs`
- Modify: `crates/fluxgauss/src/lib.rs`
- Modify: `crates/fluxgauss/src/main.rs`

**Step 1: Create logging module**

Create `crates/fluxgauss/src/logging.rs`:

```rust
//! Conversion log file writer.
//!
//! Writes detailed timestamped logs to `.fluxgauss/logs/conversion-{timestamp}.log`
//! and creates a `conversion-latest.log` symlink/copy.

use std::io::Write;
use std::path::{Path, PathBuf};

pub struct ConversionLog {
    log_path: PathBuf,
    latest_path: PathBuf,
    fh: std::fs::File,
}

impl ConversionLog {
    /// Create a new log file under `{output_dir}/.fluxgauss/logs/`.
    pub fn new(output_dir: &str) -> std::io::Result<Self> {
        let log_dir = Path::new(output_dir)
            .join(".fluxgauss")
            .join("logs");
        std::fs::create_dir_all(&log_dir)?;

        let ts = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
        let log_path = log_dir.join(format!("conversion-{}.log", ts));
        let latest_path = log_dir.join("conversion-latest.log");

        let mut fh = std::fs::File::create(&log_path)?;
        writeln!(fh, "FluxGauss Conversion Log")?;
        writeln!(fh, "Started: {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"))?;
        writeln!(fh, "Output: {}", output_dir)?;
        writeln!(fh)?;
        fh.flush()?;

        Ok(Self { log_path, latest_path, fh })
    }

    /// Write a log line with timestamp. Strips ANSI escape codes.
    pub fn log(&mut self, msg: &str) {
        let clean = strip_ansi(msg);
        let ts = chrono::Local::now().format("%H:%M:%S");
        let _ = writeln!(self.fh, "[{}] {}", ts, clean);
        let _ = self.fh.flush();
    }

    /// Get the path to the latest log file.
    pub fn latest_log_path(&self) -> &Path {
        &self.latest_path
    }

    /// Close the log and update the `conversion-latest.log` copy.
    pub fn close(self) -> std::io::Result<PathBuf> {
        drop(self.fh);
        // Copy the timestamped log to "latest"
        let _ = std::fs::copy(&self.log_path, &self.latest_path);
        Ok(self.log_path)
    }
}

/// Strip ANSI escape sequences from a string.
fn strip_ansi(s: &str) -> String {
    // Simple regex-free ANSI stripping
    let mut result = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.peek() == Some(&'[') {
                chars.next(); // consume '['
                // consume until a letter (the terminator)
                while let Some(&nc) = chars.peek() {
                    chars.next();
                    if nc.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
        } else {
            result.push(c);
        }
    }
    result
}
```

**Step 2: Register module in `lib.rs`**

```rust
pub mod logging;
```

**Step 3: Wire into `main.rs`**

At the start of `run()`, after printing the header:

```rust
let mut log = fluxgauss::logging::ConversionLog::new(&output_dir)?;
let log_latest = log.latest_log_path().to_string_lossy().into_owned();
```

At the end of `run()`, after printing stats and before returning:

```rust
    log.log(&format!("Done! {} packages, {} files generated", total_packages, total_generated));
    let _ = log.close();

    println!("\n    详细处理日志: {}", log_latest);
```

**Step 4: Commit**

```
feat(rust): add conversion log file with timestamp and latest copy
```

---

## Task 8: Verify Full Output Parity

**Files:**
- None (verification only)

**Step 1: Build and run**

```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java
cargo build --release -p fluxgauss
./target/release/fluxgauss -c demo-project/fluxgauss.yaml
```

**Step 2: Compare output line-by-line with Python version**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml 2>&1 | tee /tmp/python-output.txt
./target/release/fluxgauss -c demo-project/fluxgauss.yaml 2>&1 | tee /tmp/rust-output.txt
```

Expected Rust output should now match:
```
  Output:     ./dest
  Config:     demo-project/fluxgauss.yaml
  Package:    com.example.demo
  Input:      32 SQL file(s)

  Parse    [████████████████████████████████████████████████████████████████████████] 32/32 100.0%  ✓
  Analyze  [████████████████████████████████████████████████████████████████████████] 132/132 100.0%  ✓
  Generate [████████████████████████████████████████████████████████████████████████] 25/25 100.0%  ✓

  Done!
    Packages:    25
    Procedures:  132
    DML stmts:   260 (extracted as iBatis mapper methods)
    Cross-calls: 64
    Test files:  25 (generated unit tests)
    IT files:    25 (generated integration tests, remote mode)
    Skipped:     127 (non-procedure SQL)
    Unresolved:  20 (cross-package calls, 详见转换报告)
    Stubs:       9 (需人工审查, 详见转换报告)

    详细处理日志: dest/.fluxgauss/logs/conversion-latest.log

  📄 转换报告:
    - dest/.fluxgauss/reports/conversion-report-<timestamp>.md

  Output: /Users/c2j/Projects/Desktop_Projects/DB/sp2java/dest
```

**Step 3: Verify log file exists**

```bash
ls -la dest/.fluxgauss/logs/
cat dest/.fluxgauss/logs/conversion-latest.log
```

**Step 4: Verify report file exists and contains all sections**

```bash
cat dest/.fluxgauss/reports/conversion-report-latest.md
```

Should contain: 概览 table, SQL → Java 映射 table, 跳过项, 未解析调用, stubs.

**Step 5: Run existing tests**

```bash
cd crates/fluxgauss && cargo test
```

All existing tests must still pass.

**Step 6: Commit**

```
test(rust): verify CLI output parity with Python converter
```

---

## Summary: Dependency Order

```
Task 1 (progress module)  ──┐
Task 2 (PipelineResult)   ──┤
Task 3 (Config line)      ──┼──► Task 4 (wire progress bars) ──► Task 5 (stats summary)
Task 6 (report)           ──┤
Task 7 (log file)         ──┘
                                        │
                                        ▼
                                  Task 8 (verify)
```

Tasks 1, 2, 3, 6, 7 are **independent** and can be done in parallel.
Task 4 depends on Tasks 1 + 2.
Task 5 depends on Tasks 2 + 3.
Task 8 depends on all.

---

## File Change Summary

| File | Action | Tasks |
|------|--------|-------|
| `crates/fluxgauss/src/progress.rs` | Create | 1 |
| `crates/fluxgauss/src/logging.rs` | Create | 7 |
| `crates/fluxgauss/src/lib.rs` | Modify (add 2 `pub mod`) | 1, 7 |
| `crates/fluxgauss/src/pipeline.rs` | Modify (PipelineResult, progress callbacks, stats) | 2, 4 |
| `crates/fluxgauss/src/main.rs` | Modify (Config line, progress, stats, report, log) | 3, 4, 5, 6, 7 |
| `crates/fluxgauss/src/report.rs` | Modify (extend struct, build_report, auto-save) | 6 |
| `crates/fluxgauss/src/context.rs` | Read-only (already has needed fields) | 2 |

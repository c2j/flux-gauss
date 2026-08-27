use std::io::{IsTerminal, Write};
use std::path::PathBuf;

use clap::Parser;

use fluxgauss::config;
use fluxgauss::incremental::IncrementalState;
use fluxgauss::pipeline;

const VERSION: &str = env!("CARGO_PKG_VERSION");

const LOGO: &str = r#"

███████╗██╗     ██╗   ██╗██╗  ██╗     ██████╗  █████╗ ██╗   ██╗███████╗███████╗
██╔════╝██║     ██║   ██║╚██╗██╔╝    ██╔════╝ ██╔══██╗██║   ██║██╔════╝██╔════╝
█████╗  ██║     ██║   ██║ ╚███╔╝     ██║  ███╗███████║██║   ██║███████╗███████╗
██╔══╝  ██║     ██║   ██║ ██╔██╗     ██║   ██║██╔══██║██║   ██║╚════██║╚════██║
██║     ███████╗╚██████╔╝██╔╝ ██╗    ╚██████╔╝██║  ██║╚██████╔╝███████║███████║
╚═╝     ╚══════╝ ╚═════╝ ╚═╝  ╚═╝     ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝

  PL/pgSQL → Spring Boot / MyBatis
"#;

#[derive(Parser)]
#[command(name = "fluxgauss", version = VERSION)]
#[command(about = "PL/pgSQL → Spring Boot/MyBatis Java 转换器")]
struct Cli {
    #[arg(short = 'c', long = "config")]
    config: Option<PathBuf>,

    #[arg(short = 'o', long = "output")]
    output: Option<PathBuf>,

    #[arg(short = 's', long = "sources", num_args = 1..)]
    sources: Vec<PathBuf>,

    #[arg(long = "full")]
    full: bool,

    #[arg(long = "resume")]
    resume: bool,

    #[arg(long = "report")]
    report: Option<PathBuf>,

    #[arg(long = "skip-validate", default_value_t = false)]
    skip_validate: bool,

    #[arg(long = "encoding", default_value = "utf-8")]
    encoding: String,

    #[arg(long = "debug", default_value_t = false)]
    debug: bool,

    #[arg(long = "mcp", default_value_t = false)]
    mcp: bool,
}

fn main() {
    let cli = Cli::parse();

    if cli.mcp {
        match std::process::Command::new("fluxgauss-mcp")
            .stdin(std::process::Stdio::inherit())
            .stdout(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .spawn()
        {
            Ok(mut child) => {
                let status = child.wait().unwrap_or_else(|e| {
                    eprintln!("Failed to wait for fluxgauss-mcp process: {}", e);
                    std::process::exit(1);
                });
                std::process::exit(status.code().unwrap_or(1));
            }
            Err(e) => {
                eprintln!("Error: Could not start fluxgauss-mcp binary: {}", e);
                eprintln!();
                eprintln!("  The fluxgauss-mcp binary is required for --mcp mode.");
                eprintln!("  Build it with:  cargo build -p fluxgauss-mcp");
                eprintln!("  Or run the MCP server directly: cargo run -p fluxgauss-mcp");
                std::process::exit(1);
            }
        }
    }

    println!("{}", LOGO);

    match run(cli) {
        Ok(()) => {}
        Err(e) => {
            eprintln!("Error: {}", e);
            std::process::exit(1);
        }
    }
}

fn run(cli: Cli) -> Result<(), Box<dyn std::error::Error>> {
    let config_path_str = cli.config.as_ref()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "CLI mode".into());

    let (mut config, sql_files, output_dir) = resolve_inputs(&cli)?;

    // CLI --encoding takes precedence over config file
    if cli.encoding != "utf-8" {
        config.encoding = Some(cli.encoding.clone());
    }

    let base_package = config.base_package_or_default();
    println!("  Output:     {}", output_dir);
    println!("  Config:     {}", config_path_str);
    println!("  Package:    {}", base_package);
    println!("  Input:      {} SQL file(s)", sql_files.len());

    if cli.debug {
        println!("  🔧 Debug mode enabled — SQL source annotations will be injected");
    }

    if sql_files.is_empty() {
        eprintln!("No valid source files. Exiting.");
        std::process::exit(1);
    }

    let mut incremental = IncrementalState::new(&output_dir, cli.full);
    incremental.initialize()?;

    let mut log = fluxgauss::logging::ConversionLog::new(&output_dir)?;
    log.log(&format!("Output: {}", output_dir));
    log.log(&format!("Config: {}", config_path_str));
    log.log(&format!("Input: {} SQL file(s)", sql_files.len()));

    // ── Phase 0: Validate SQL syntax ──
    if !cli.skip_validate {
        let validate_result = pipeline::phase0_validate(&sql_files);
        log.log(&format!("Validate: {} error(s), {} warning(s) across {} file(s)",
            validate_result.error_file_count, validate_result.warning_file_count, sql_files.len()));

        for file_result in &validate_result.file_results {
            if !file_result.errors.is_empty() {
                log.log(&format!("  ❌ {} — {} error(s)", file_result.basename, file_result.errors.len()));
            } else if !file_result.warnings.is_empty() {
                log.log(&format!("  ⚠ {} — {} warning(s)", file_result.basename, file_result.warnings.len()));
            } else {
                log.log(&format!("  ✅ {} OK", file_result.basename));
            }
        }

        if validate_result.has_errors() {
            println!();
            println!("  ⚠ {} file(s) have syntax errors:", validate_result.error_file_count);
            for file_result in &validate_result.file_results {
                if file_result.errors.is_empty() {
                    continue;
                }
                println!("    📄 {} — {} error(s):", file_result.basename, file_result.errors.len());
                for err in file_result.errors.iter().take(10) {
                    println!("       {}", format_validate_error(err));
                }
                if file_result.errors.len() > 10 {
                    println!("       ... and {} more", file_result.errors.len() - 10);
                }
            }
            println!();

            if std::io::stdin().is_terminal() {
                print!("  是否继续转换？语法错误可能导致转换结果不准确。[y/N] ");
                let _ = std::io::stdout().flush();
                let mut answer = String::new();
                match std::io::stdin().read_line(&mut answer) {
                    Ok(_) => {
                        let ans = answer.trim().to_lowercase();
                        if ans != "y" && ans != "yes" {
                            println!("  用户取消转换。请修复语法错误后重试。");
                            let _ = log.close();
                            std::process::exit(1);
                        }
                        log.log("  用户选择继续转换（忽略语法错误）");
                    }
                    Err(_) => {
                        println!("  用户取消转换。");
                        let _ = log.close();
                        std::process::exit(1);
                    }
                }
            } else {
                println!("  ❌ 非交互模式检测到语法错误，自动中止。使用 --skip-validate 跳过校验。");
                let _ = log.close();
                std::process::exit(1);
            }
        }
    }

    let result = pipeline::run_pipeline(&sql_files, &config, &mut incremental, cli.debug);

    let total_packages = result.packages.len();
    let total_procedures: usize = result.packages.iter().map(|p| p.procedures.len()).sum();
    let total_generated = result.generated_files.len();

    log.log(&format!("Done! {} packages, {} procedures, {} files",
              total_packages, total_procedures, total_generated));

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
        for call in &result.unresolved_calls {
            log.log(&format!("    Unresolved call: {} -> {} (args: {}) [{}] — {}",
                call.caller, call.callee, call.args, call.caller_file, call.hint));
        }
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

    let report = fluxgauss::report::build_report(
        &result.packages,
        result.skipped,
        result.warnings,
        result.unresolved_calls,
        result.stub_count,
        &config_path_str,
        &output_dir,
        sql_files.len(),
    );
    let report_paths = report.save_auto(std::path::Path::new(&output_dir));

    if let Some(report_path) = &cli.report {
        if report.save(report_path).is_ok() {
            println!("  Report:     {}", report_path.display());
        }
    }

    let log_latest = log.latest_log_path().to_path_buf();
    let _ = log.close();
    println!("\n    详细处理日志: {}", log_latest.display());

    if !report_paths.is_empty() {
        println!("\n  📄 转换报告:");
        for p in &report_paths {
            println!("    - {}", p);
        }
        println!("    - {}", log_latest.display());
    }

    let abs_output = std::path::Path::new(&output_dir).canonicalize()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| output_dir.clone());
    println!("\n  Output: {}", abs_output);

    Ok(())
}

fn format_validate_error(err: &ogsql_parser::ParserError) -> String {
    match err {
        ogsql_parser::ParserError::UnexpectedToken { location, expected, got } => {
            format!("error at line {}, col {}: expected {}, got {}", location.line, location.column, expected, got)
        }
        ogsql_parser::ParserError::UnexpectedEof { expected, location } => {
            format!("error at line {}, col {}: unexpected end of input, expected {}", location.line, location.column, expected)
        }
        ogsql_parser::ParserError::ReservedKeywordAsIdentifier { keyword, location } => {
            format!("error at line {}, col {}: reserved keyword '{}' cannot be used as identifier", location.line, location.column, keyword)
        }
        ogsql_parser::ParserError::UnsupportedSyntax { location, syntax, hint } => {
            format!("error at line {}, col {}: {} ({})", location.line, location.column, syntax, hint)
        }
        ogsql_parser::ParserError::Warning { message, .. } => {
            format!("warning: {}", message)
        }
        ogsql_parser::ParserError::TokenizerError(e) => {
            format!("tokenizer error: {}", e)
        }
    }
}

fn resolve_inputs(cli: &Cli) -> Result<(config::AppConfig, Vec<PathBuf>, String), Box<dyn std::error::Error>> {
    if let Some(config_path) = &cli.config {
        if !config_path.exists() {
            return Err(format!("Config file not found: {}", config_path.display()).into());
        }
        let config = config::load_config(config_path)?;
        let output_dir = config.output_dir_or_default();
        let sql_files: Vec<PathBuf> = config.sources
            .as_ref()
            .map(|s| s.iter().map(PathBuf::from).collect())
            .unwrap_or_default();
        Ok((config, sql_files, output_dir))
    } else if cli.output.is_some() || !cli.sources.is_empty() {
        let output = cli.output.clone()
            .ok_or("Missing --output directory. Use -o <dir> with -s <sql_files>")?;
        let sql_files = cli.sources.clone();
        let config = config::AppConfig::default();
        Ok((config, sql_files, output.to_string_lossy().to_string()))
    } else {
        Err("Missing --config or --output/--sources. Use -c <config.yaml> or -o <dir> -s <sql> [...]".into())
    }
}

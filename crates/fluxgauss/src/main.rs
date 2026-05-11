use std::path::PathBuf;

use clap::Parser;

use fluxgauss::config;
use fluxgauss::incremental::IncrementalState;
use fluxgauss::pipeline;

const VERSION: &str = "1.0.0";

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
}

fn main() {
    let cli = Cli::parse();

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

    let (config, sql_files, output_dir) = resolve_inputs(&cli)?;

    let base_package = config.base_package_or_default();
    println!("  Output:     {}", output_dir);
    println!("  Config:     {}", config_path_str);
    println!("  Package:    {}", base_package);
    println!("  Input:      {} SQL file(s)", sql_files.len());

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

    let result = pipeline::run_pipeline(&sql_files, &config, &mut incremental);

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

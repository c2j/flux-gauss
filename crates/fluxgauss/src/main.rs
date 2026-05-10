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
    let (config, sql_files, output_dir) = resolve_inputs(&cli)?;

    let base_package = config.base_package_or_default();
    println!("  Output:     {}", output_dir);
    println!("  Package:    {}", base_package);
    println!("  Input:      {} SQL file(s)", sql_files.len());

    if sql_files.is_empty() {
        eprintln!("No valid source files. Exiting.");
        std::process::exit(1);
    }

    let mut incremental = IncrementalState::new(&output_dir, cli.full);
    incremental.initialize()?;

    let result = pipeline::run_pipeline(&sql_files, &config, &mut incremental);

    let total_packages = result.packages.len();
    let total_procedures: usize = result.packages.iter().map(|p| p.procedures.len()).sum();
    let total_generated = result.generated_files.len();

    println!("\n  ── Results ──");
    println!("  Packages:    {}", total_packages);
    println!("  Procedures:  {}", total_procedures);
    println!("  Generated:   {} file(s)", total_generated);

    if !result.errors.is_empty() {
        println!("\n  ── Errors ({} total) ──", result.errors.len());
        for err in &result.errors {
            println!("  ✗ {}", err);
        }
    }

    println!("\n  Done. {} package(s), {} file(s) generated.", total_packages, total_generated);

    if let Some(report_path) = &cli.report {
        let mut report = fluxgauss::report::ConversionReport::new();
        report.total_files = sql_files.len();
        report.total_packages = total_procedures;
        report.save(report_path)?;
        println!("  Report:     {}", report_path.display());
    }

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

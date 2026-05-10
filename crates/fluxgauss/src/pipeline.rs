use std::path::PathBuf;

use crate::config::AppConfig;
use crate::context::AnalysisContext;
use crate::incremental::IncrementalState;
use crate::types::{
    AnalyzedPackages, ConversionError, PackageSummary, ParsedPackages,
};

pub struct PipelineResult {
    pub packages: Vec<crate::types::PackageInfo>,
    pub generated_files: Vec<String>,
    pub errors: Vec<ConversionError>,
    pub warnings: Vec<String>,
    pub skipped: Vec<crate::types::SkippedItem>,
}

pub fn run_pipeline(
    sql_files: &[PathBuf],
    config: &AppConfig,
    incremental: &mut IncrementalState,
) -> PipelineResult {
    let base_package = config.base_package_or_default();

    let parsed = phase1_parse(sql_files, config, incremental);
    let analyzed = phase2_analyze(parsed);
    let (generated, errors) = phase3_generate(&analyzed, config, incremental);

    let analyzed_packages = analyzed.packages;
    PipelineResult {
        packages: analyzed_packages,
        generated_files: generated,
        errors,
        warnings: Vec::new(),
        skipped: Vec::new(),
    }
}

fn phase1_parse(
    sql_files: &[PathBuf],
    config: &AppConfig,
    incremental: &mut IncrementalState,
) -> ParsedPackages {
    let base_package = config.base_package_or_default();
    let mut packages = Vec::new();
    let mut summaries = Vec::new();
    let mut skipped = Vec::new();
    let mut errors = Vec::new();

    for sql_file in sql_files {
        if incremental.is_cached(sql_file) {
            if let Some(ast_json) = incremental.load_cached_ast(sql_file) {
                if let Ok(parse_output) =
                    serde_json::from_str::<ogsql_parser::parser::ParseOutput>(&ast_json)
                {
                    let result = crate::extract::extract_from_parse_output(
                        &parse_output,
                        &sql_file.to_string_lossy(),
                        &base_package,
                    );
                    packages.extend(result.packages);
                    skipped.extend(result.skipped);
                    errors.extend(result.errors);
                    continue;
                }
            }
        }

        let content = match std::fs::read(sql_file) {
            Ok(bytes) => match ogsql_parser::token::decode_sql_file(&bytes) {
                Ok((s, _enc)) => s,
                Err(e) => {
                    errors.push(ConversionError::Io {
                        path: sql_file.to_string_lossy().into_owned(),
                        message: format!("encoding detection failed: {}", e),
                    });
                    continue;
                }
            },
            Err(e) => {
                errors.push(ConversionError::Io {
                    path: sql_file.to_string_lossy().into_owned(),
                    message: e.to_string(),
                });
                continue;
            }
        };

        let tokens = match ogsql_parser::Tokenizer::new(&content).tokenize() {
            Ok(t) => t,
            Err(e) => {
                errors.push(ConversionError::Parse {
                    path: sql_file.to_string_lossy().into_owned(),
                    message: format!("{:?}", e),
                });
                continue;
            }
        };

        let stmts = ogsql_parser::Parser::new(tokens).parse();
        let parse_output = ogsql_parser::parser::ParseOutput {
            statements: stmts
                .into_iter()
                .map(|s| ogsql_parser::ast::StatementInfo {
                    sql_text: String::new(),
                    start_line: 0,
                    start_col: 0,
                    end_line: 0,
                    end_col: 0,
                    statement: s,
                })
                .collect(),
            errors: Vec::new(),
            comments: Vec::new(),
        };

        if let Ok(json) = serde_json::to_string(&parse_output) {
            let _ = incremental.save_cached_ast(sql_file, &json);
        }

        let result = crate::extract::extract_from_parse_output(
            &parse_output,
            &sql_file.to_string_lossy(),
            &base_package,
        );

        for pkg in &result.packages {
            summaries.push(crate::types::PackageSummary::from_package(pkg));
        }

        packages.extend(result.packages);
        skipped.extend(result.skipped);
        errors.extend(result.errors);
    }

    ParsedPackages {
        packages,
        summaries,
        skipped,
        errors,
    }
}

fn phase2_analyze(parsed: ParsedPackages) -> AnalyzedPackages {
    let summary_map: std::collections::HashMap<String, &PackageSummary> = parsed
        .summaries
        .iter()
        .map(|s| (s.name.clone(), s))
        .collect();

    let mut ctx = AnalysisContext::new();
    let mut errors = Vec::new();
    let mut packages = parsed.packages;

    for pkg in &mut packages {
        for proc in &mut pkg.procedures {
            let proc_summaries: std::collections::HashMap<String, PackageSummary> = parsed
                .summaries
                .iter()
                .map(|s| (s.name.clone(), s.clone()))
                .collect();

            if let Err(e) = crate::analyze::analyze_procedure(proc, &proc_summaries, &mut ctx) {
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

fn phase3_generate(
    analyzed: &AnalyzedPackages,
    config: &AppConfig,
    _incremental: &IncrementalState,
) -> (Vec<String>, Vec<ConversionError>) {
    let base_package = config.base_package_or_default();
    let output_dir_str = config.output_dir_or_default();
    let output_dir = std::path::Path::new(&output_dir_str);
    let mut generated = Vec::new();
    let mut errors = Vec::new();

    match crate::generate::skeleton::write_skeleton_files(output_dir, config, &base_package) {
        Ok(files) => generated.extend(files),
        Err(e) => errors.push(ConversionError::Io {
            path: output_dir.to_string_lossy().into_owned(),
            message: e.to_string(),
        }),
    }

    for pkg in &analyzed.packages {
        let service_injections = crate::generate::service::collect_service_injections(pkg);

        if let Err(e) = crate::generate::mapper::write_mapper_interface(output_dir, pkg, &base_package) {
            errors.push(ConversionError::Io {
                path: pkg.package_name.clone(),
                message: format!("write_mapper_interface: {}", e),
            });
            continue;
        }

        if let Err(e) = crate::generate::mapper::write_mapper_xml(output_dir, pkg, &base_package) {
            errors.push(ConversionError::Io {
                path: pkg.package_name.clone(),
                message: format!("write_mapper_xml: {}", e),
            });
            continue;
        }

        match crate::generate::service::write_service_class(output_dir, pkg, &base_package, &service_injections) {
            Ok(name) => generated.push(format!("{}.java", name)),
            Err(e) => {
                errors.push(ConversionError::Io {
                    path: pkg.package_name.clone(),
                    message: format!("write_service_class: {}", e),
                });
                continue;
            }
        }

        if let Err(e) = crate::generate::test::write_service_test(output_dir, pkg, &base_package, &service_injections) {
            errors.push(ConversionError::Io {
                path: pkg.package_name.clone(),
                message: format!("write_service_test: {}", e),
            });
        }

        if config.integration_test.as_ref().and_then(|it| it.enabled).unwrap_or(false) {
            if let Err(e) = crate::generate::itest::write_itest_class(output_dir, pkg, &base_package) {
                errors.push(ConversionError::Io {
                    path: pkg.package_name.clone(),
                    message: format!("write_itest_class: {}", e),
                });
            }
        }
    }

    (generated, errors)
}

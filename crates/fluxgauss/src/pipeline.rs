use std::collections::HashMap;
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
    pub unresolved_calls: Vec<String>,
    pub stub_count: usize,
    pub stub_reasons: HashMap<(String, usize), Vec<String>>,
    pub test_file_count: usize,
    pub itest_file_count: usize,
    pub total_dml: usize,
    pub total_cross_calls: usize,
}

pub fn run_pipeline(
    sql_files: &[PathBuf],
    config: &AppConfig,
    incremental: &mut IncrementalState,
) -> PipelineResult {
    let base_package = config.base_package_or_default();
    let mut ctx = AnalysisContext::new();

    let parsed = phase1_parse(sql_files, config, incremental);
    let analyzed = phase2_analyze(parsed, &mut ctx, sql_files);
    let (generated, test_count, itest_count, errors) = phase3_generate(&analyzed, config, incremental, sql_files);

    let packages = analyzed.packages;
    let skipped = analyzed.skipped;

    let total_dml: usize = packages
        .iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.dml_statements.len())
        .sum();
    let total_cross_calls: usize = packages
        .iter()
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
    let total = sql_files.len();

    for (idx, sql_file) in sql_files.iter().enumerate() {
        let basename = sql_file.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();

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
                    crate::progress::progress_bar("Parse", idx + 1, total, &format!("Cached {}", basename));
                    continue;
                }
            }
        }

        crate::progress::progress_bar("Parse", idx + 1, total, &format!("Parsing {}", basename));

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

        let stmts_with_info = ogsql_parser::Parser::new(tokens).parse_with_text();
        let parse_output = ogsql_parser::parser::ParseOutput {
            statements: stmts_with_info,
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

    crate::progress::progress_done("Parse", total);

    ParsedPackages {
        packages,
        summaries,
        skipped,
        errors,
    }
}

fn phase2_analyze(
    parsed: ParsedPackages,
    mut ctx: &mut AnalysisContext,
    sql_files: &[PathBuf],
) -> AnalyzedPackages {
    let mut errors = Vec::new();
    let mut packages = parsed.packages;

    let proc_summaries: std::collections::HashMap<String, PackageSummary> = parsed
        .summaries
        .iter()
        .map(|s| (s.name.clone(), s.clone()))
        .collect();

    let ddl_schema = crate::generate::itest::parse_table_ddl(sql_files);

    let total: usize = packages.iter().map(|p| p.procedures.len()).sum();
    let mut idx = 0;

    for pkg in &mut packages {
        let pkg_custom_types = pkg.custom_types.clone();
        let pkg_vars = pkg.package_vars.clone();

        let mut sibling_params: HashMap<String, Vec<Vec<_>>> = HashMap::new();
        for p in &pkg.procedures {
            let method_name = crate::naming::java_method_name(&p.proc_name);
            sibling_params.entry(method_name).or_default().push(p.parameters.clone());
        }

        for proc in &mut pkg.procedures {
            proc.custom_types = pkg_custom_types.clone();
            proc.package_vars = pkg_vars.clone();
            proc.package_proc_params = sibling_params.clone();
            idx += 1;
            crate::progress::progress_bar("Analyze", idx, total, &proc.name);

            if let Err(e) = crate::analyze::analyze_procedure(proc, &proc_summaries, &mut ctx, &ddl_schema) {
                errors.push(e);
            }
        }
        for proc in &mut pkg.procedures {
            crate::analyze::promote_out_local_vars(proc);
        }
    }

    crate::progress::progress_done("Analyze", total);

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
    sql_files: &[PathBuf],
) -> (Vec<String>, usize, usize, Vec<ConversionError>) {
    let base_package = config.base_package_or_default();
    let output_dir_str = config.output_dir_or_default();
    let output_dir = std::path::Path::new(&output_dir_str);
    let mut generated = Vec::new();
    let mut errors = Vec::new();
    let mut test_count = 0usize;
    let mut itest_count = 0usize;

    match crate::generate::skeleton::write_skeleton_files(output_dir, config, &base_package) {
        Ok(files) => generated.extend(files),
        Err(e) => errors.push(ConversionError::Io {
            path: output_dir.to_string_lossy().into_owned(),
            message: e.to_string(),
        }),
    }

    let schema_map = if config.integration_test.as_ref().and_then(|it| it.enabled).unwrap_or(false) {
        Some(crate::generate::itest::build_full_schema_map(&analyzed.packages, sql_files))
    } else {
        None
    };

    if schema_map.is_some() {
        if let Err(e) = crate::generate::itest::write_abstract_integration_test(output_dir, &base_package) {
            errors.push(ConversionError::Io {
                path: output_dir.to_string_lossy().into_owned(),
                message: format!("write_abstract_integration_test: {}", e),
            });
        }

        if let Err(e) = crate::generate::itest::write_itest_schema_sql(output_dir, &analyzed.packages, schema_map.as_ref().unwrap()) {
            errors.push(ConversionError::Io {
                path: output_dir.to_string_lossy().into_owned(),
                message: format!("write_itest_schema_sql: {}", e),
            });
        }
    }

    for (idx, pkg) in analyzed.packages.iter().enumerate() {
        let n = idx + 1;
        let total = analyzed.packages.len();
        crate::progress::progress_bar("Generate", n, total, &pkg.package_name);

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
        } else {
            test_count += 1;
        }

        if let Some(sm) = schema_map.as_ref() {
            if let Err(e) = crate::generate::itest::write_itest_class(output_dir, pkg, &base_package, &service_injections, &analyzed.packages, sm) {
                errors.push(ConversionError::Io {
                    path: pkg.package_name.clone(),
                    message: format!("write_itest_class: {}", e),
                });
            } else {
                itest_count += 1;
            }
        }
    }

    crate::progress::progress_done("Generate", analyzed.packages.len());

    (generated, test_count, itest_count, errors)
}

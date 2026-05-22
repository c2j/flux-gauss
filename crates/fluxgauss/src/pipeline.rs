use std::collections::HashMap;
use std::path::PathBuf;

use crate::config::AppConfig;
use crate::context::AnalysisContext;
use crate::incremental::IncrementalState;
use crate::types::{
    AnalyzedPackages, ConversionError, PackageSummary, ParsedPackages,
};

pub struct FileValidateResult {
    pub basename: String,
    pub errors: Vec<ogsql_parser::ParserError>,
    pub warnings: Vec<ogsql_parser::ParserError>,
    pub package_consistency_errors: Vec<ogsql_parser::PackageConsistencyError>,
    pub undefined_variables: Vec<ogsql_parser::UndefinedVariableError>,
}

pub struct ValidateResult {
    pub file_results: Vec<FileValidateResult>,
    pub error_file_count: usize,
    pub warning_file_count: usize,
}

impl ValidateResult {
    pub fn has_errors(&self) -> bool {
        self.error_file_count > 0
    }
}

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

pub fn phase0_validate(sql_files: &[PathBuf]) -> ValidateResult {
    let total = sql_files.len();
    let mut file_results = Vec::new();
    let mut all_defined_funcs: Vec<String> = Vec::new();

    for (idx, sql_file) in sql_files.iter().enumerate() {
        let basename = sql_file.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();
        crate::progress::progress_bar("Validate", idx + 1, total, &format!("Validating {}", basename));

        let content = match std::fs::read(sql_file) {
            Ok(bytes) => match ogsql_parser::token::decode_sql_file(&bytes) {
                Ok((s, _enc)) => s,
                Err(e) => {
                    file_results.push(FileValidateResult {
                        basename,
                        errors: vec![ogsql_parser::ParserError::Warning {
                            message: format!("encoding detection failed: {}", e),
                            location: ogsql_parser::SourceLocation::default(),
                        }],
                        warnings: Vec::new(),
                        package_consistency_errors: Vec::new(),
                        undefined_variables: Vec::new(),
                    });
                    continue;
                }
            },
            Err(e) => {
                file_results.push(FileValidateResult {
                    basename,
                    errors: vec![ogsql_parser::ParserError::Warning {
                        message: format!("cannot read file: {}", e),
                        location: ogsql_parser::SourceLocation::default(),
                    }],
                    warnings: Vec::new(),
                    package_consistency_errors: Vec::new(),
                    undefined_variables: Vec::new(),
                });
                continue;
            }
        };

        let tokens = match ogsql_parser::Tokenizer::new(&content).tokenize() {
            Ok(t) => t,
            Err(e) => {
                file_results.push(FileValidateResult {
                    basename,
                    errors: vec![ogsql_parser::ParserError::TokenizerError(e)],
                    warnings: Vec::new(),
                    package_consistency_errors: Vec::new(),
                    undefined_variables: Vec::new(),
                });
                continue;
            }
        };

        let mut parser = ogsql_parser::Parser::new(tokens);
        let stmts = parser.parse_with_text();
        let parse_errors = parser.errors().to_vec();

        let pkg_errors = ogsql_parser::validate_package_consistency(&stmts);
        let own_funcs = collect_defined_routine_names(&stmts);
        all_defined_funcs.extend(own_funcs);

        let mut errors = Vec::new();
        let mut warnings = Vec::new();
        for err in &parse_errors {
            if is_warning(err) {
                warnings.push(err.clone());
            } else {
                errors.push(err.clone());
            }
        }

        let var_errors = validate_pl_variables_from_stmts(&stmts, &all_defined_funcs);
        for ve in &var_errors {
            warnings.push(ogsql_parser::ParserError::Warning {
                message: format_undefined_var_error(ve),
                location: ogsql_parser::SourceLocation::default(),
            });
        }

        file_results.push(FileValidateResult {
            basename,
            errors,
            warnings,
            package_consistency_errors: pkg_errors,
            undefined_variables: var_errors,
        });
    }

    crate::progress::progress_done("Validate", total);

    let error_file_count = file_results.iter().filter(|r| r.errors.iter().any(|e| !is_warning(e))).count();
    let warning_file_count = file_results.iter().filter(|r| {
        r.errors.iter().all(|e| is_warning(e)) && !r.warnings.is_empty()
    }).count();

    ValidateResult {
        file_results,
        error_file_count,
        warning_file_count,
    }
}

fn collect_defined_routine_names(stmts: &[ogsql_parser::StatementInfo]) -> Vec<String> {
    use ogsql_parser::ast::Statement;
    let mut names = Vec::new();
    for si in stmts {
        match &si.statement {
            Statement::CreateProcedure(p) => {
                names.push(p.name.iter().map(|s| s.to_lowercase()).collect::<Vec<_>>().join("."));
            }
            Statement::CreateFunction(f) => {
                names.push(f.name.iter().map(|s| s.to_lowercase()).collect::<Vec<_>>().join("."));
            }
            Statement::CreatePackageBody(b) => {
                for item in &b.items {
                    match item {
                        ogsql_parser::ast::PackageItem::Procedure(p) => {
                            names.push(p.name.iter().map(|s| s.to_lowercase()).collect::<Vec<_>>().join("."));
                        }
                        ogsql_parser::ast::PackageItem::Function(f) => {
                            names.push(f.name.iter().map(|s| s.to_lowercase()).collect::<Vec<_>>().join("."));
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }
    names.sort();
    names.dedup();
    names
}

fn validate_pl_variables_from_stmts(
    stmts: &[ogsql_parser::StatementInfo],
    known_funcs: &[String],
) -> Vec<ogsql_parser::UndefinedVariableError> {
    use ogsql_parser::ast::Statement;
    let mut warnings = Vec::new();
    let funcs_str: Vec<&str> = known_funcs.iter().map(|s| s.as_str()).collect();
    for si in stmts {
        match &si.statement {
            Statement::CreateProcedure(proc) => {
                if let Some(ref block) = proc.block {
                    let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                        block, &proc.parameters, &[], &funcs_str,
                    );
                    warnings.extend(vars);
                }
            }
            Statement::CreateFunction(func) => {
                if let Some(ref block) = func.block {
                    let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                        block, &func.parameters, &[], &funcs_str,
                    );
                    warnings.extend(vars);
                }
            }
            Statement::Do(do_stmt) => {
                if let Some(ref block) = do_stmt.block {
                    let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                        block, &[], &[], &funcs_str,
                    );
                    warnings.extend(vars);
                }
            }
            Statement::CreatePackageBody(body) => {
                let pkg_vars: Vec<&str> = body.items.iter()
                    .filter_map(|item| match item {
                        ogsql_parser::ast::PackageItem::Variable(v) => Some(v.name.as_str()),
                        _ => None,
                    })
                    .collect();
                for item in &body.items {
                    match item {
                        ogsql_parser::ast::PackageItem::Procedure(proc) => {
                            if let Some(ref block) = proc.block {
                                let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                                    block, &proc.parameters, &pkg_vars, &funcs_str,
                                );
                                warnings.extend(vars);
                            }
                        }
                        ogsql_parser::ast::PackageItem::Function(func) => {
                            if let Some(ref block) = func.block {
                                let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                                    block, &func.parameters, &pkg_vars, &funcs_str,
                                );
                                warnings.extend(vars);
                            }
                        }
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }
    warnings
}

fn is_warning(e: &ogsql_parser::ParserError) -> bool {
    matches!(
        e,
        ogsql_parser::ParserError::Warning { .. }
            | ogsql_parser::ParserError::ReservedKeywordAsIdentifier { .. }
    )
}

fn format_undefined_var_error(ve: &ogsql_parser::UndefinedVariableError) -> String {
    let line_info = ve.location.as_ref()
        .map(|sp| format!(":{}", sp.start.line))
        .unwrap_or_default();
    format!("undefined variable '{}' in {}{}", ve.variable_name, ve.context, line_info)
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

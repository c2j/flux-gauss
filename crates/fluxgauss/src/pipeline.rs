use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;

use crate::config::AppConfig;
use crate::context::AnalysisContext;
use crate::incremental::IncrementalState;
use crate::types::{
    AnalyzedPackages, ConversionError, PackageInfo, PackageSummary, ParsedPackages, SkippedItem,
    UnresolvedCall,
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
    pub unresolved_calls: Vec<UnresolvedCall>,
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
                Ok((s, _enc)) => s.replace("\r\n", "\n").replace('\r', "\n"),
                Err(e) => {
                    file_results.push(FileValidateResult {
                        basename,
                        errors: vec![ogsql_parser::ParserError::Warning {
                            message: format!("encoding detection failed: {}", e),
                            location: ogsql_parser::SourceLocation::default(),
                            level: ogsql_parser::linter::WarningLevel::Caution,
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
                        level: ogsql_parser::linter::WarningLevel::Caution,
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

        let mut parser = ogsql_parser::Parser::with_source(tokens, content.clone());
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
                level: ogsql_parser::linter::WarningLevel::Caution,
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
                        block, &proc.parameters, &[], &funcs_str, false,
                    );
                    warnings.extend(vars);
                }
            }
            Statement::CreateFunction(func) => {
                if let Some(ref block) = func.block {
                    let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                        block, &func.parameters, &[], &funcs_str, false,
                    );
                    warnings.extend(vars);
                }
            }
            Statement::Do(do_stmt) => {
                if let Some(ref block) = do_stmt.block {
                    let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                        block, &[], &[], &funcs_str, false,
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
                                block, &proc.parameters, &pkg_vars, &funcs_str, false,
                            );
                            warnings.extend(vars);
                        }
                    }
                    ogsql_parser::ast::PackageItem::Function(func) => {
                        if let Some(ref block) = func.block {
                            let vars = ogsql_parser::validate_pl_variables_with_extra_vars_and_funcs(
                                block, &func.parameters, &pkg_vars, &funcs_str, false,
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
    debug: bool,
) -> PipelineResult {
    let base_package = config.base_package_or_default();
    let mut ctx = AnalysisContext::new();

    let parsed = phase1_parse(sql_files, config, incremental);
    let mut analyzed = phase2_analyze(parsed, &mut ctx, sql_files, debug);
    let (generated, test_count, itest_count, errors) = phase3_generate(&mut analyzed, config, incremental, sql_files, debug);

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
        warnings: analyzed.warnings,
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
    let mut skipped = Vec::new();
    let mut errors = Vec::new();
    let mut package_origins: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
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
                    track_package_origins(&parse_output, &mut package_origins);
                    let result = crate::extract::extract_from_parse_output(
                        &parse_output,
                        &sql_file.to_string_lossy(),
                        &base_package,
                    );
                    let package_names: Vec<String> = result.packages.iter()
                        .map(|pkg| pkg.package_name.clone())
                        .collect();
                    let java_package = result.packages.first()
                        .map(|pkg| pkg.java_package.as_str())
                        .unwrap_or("");
                    let _ = incremental.update_file_packages(sql_file, &package_names, java_package);
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
                Ok((s, _enc)) => s.replace("\r\n", "\n").replace('\r', "\n"),
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

        let stmts_with_info = ogsql_parser::Parser::with_source(tokens, content).parse_with_text();
        let parse_output = ogsql_parser::parser::ParseOutput {
            statements: stmts_with_info,
            errors: Vec::new(),
            comments: Vec::new(),
        };
        track_package_origins(&parse_output, &mut package_origins);

        if let Ok(json) = serde_json::to_string(&parse_output) {
            let _ = incremental.save_cached_ast(sql_file, &json);
        }

        let result = crate::extract::extract_from_parse_output(
            &parse_output,
            &sql_file.to_string_lossy(),
            &base_package,
        );
        let package_names: Vec<String> = result.packages.iter()
            .map(|pkg| pkg.package_name.clone())
            .collect();
        let java_package = result.packages.first()
            .map(|pkg| pkg.java_package.as_str())
            .unwrap_or("");
        let _ = incremental.update_file_packages(sql_file, &package_names, java_package);

        packages.extend(result.packages);
        skipped.extend(result.skipped);
        errors.extend(result.errors);
    }

    crate::progress::progress_done("Parse", total);

    let mut packages_by_name: BTreeMap<String, PackageInfo> = BTreeMap::new();
    for package in packages {
        if let Some(existing) = packages_by_name.get_mut(&package.package_name) {
            merge_package_info(existing, package, &mut skipped);
        } else {
            packages_by_name.insert(package.package_name.clone(), package);
        }
    }
    let packages: Vec<PackageInfo> = packages_by_name.into_values().collect();
    let summaries = packages.iter().map(PackageSummary::from_package).collect();
    let _ = incremental.save_manifest();

    let warnings = package_origins
        .into_iter()
        .filter_map(|(registration_name, qualified_names)| {
            (qualified_names.len() > 1).then(|| format!(
                "Package registration collision: {} fold into '{}'; schemas are merged for Python parity",
                qualified_names.into_iter().collect::<Vec<_>>().join(", "),
                registration_name,
            ))
        })
        .collect();

    ParsedPackages {
        packages,
        summaries,
        warnings,
        skipped,
        errors,
    }
}

fn merge_package_info(existing: &mut PackageInfo, incoming: PackageInfo, skipped: &mut Vec<SkippedItem>) {
    if existing.source_files.is_empty() && !existing.source_file.is_empty() {
        existing.source_files.push(existing.source_file.clone());
    }
    for source_file in incoming.source_files.iter().chain(std::iter::once(&incoming.source_file)) {
        if !source_file.is_empty() && !existing.source_files.contains(source_file) {
            existing.source_files.push(source_file.clone());
        }
    }

    let mut seen: BTreeSet<(String, usize)> = existing.procedures.iter()
        .map(|proc| (proc.proc_name.clone(), proc.parameters.len()))
        .collect();
    for proc in incoming.procedures {
        let key = (proc.proc_name.clone(), proc.parameters.len());
        if !seen.insert(key) {
            skipped.push(SkippedItem {
                item_type: "ROUTINE".into(),
                name: proc.name.clone(),
                source_file: proc.source_file.clone(),
                line_number: proc.source_start_line,
                reason: format!(
                    "Duplicate routine while merging package {}; kept first definition",
                    existing.package_name
                ),
            });
        } else {
            existing.procedures.push(proc);
        }
    }

    for (name, value) in incoming.package_vars {
        if existing.package_vars.contains_key(&name) {
            skipped.push(SkippedItem {
                item_type: "PACKAGE_VARS".into(),
                name,
                source_file: incoming.source_file.clone(),
                line_number: 0,
                reason: format!("Conflicting package variable while merging {}; kept first definition", existing.package_name),
            });
        } else {
            existing.package_vars.insert(name, value);
        }
    }
    for (name, value) in incoming.custom_types {
        if existing.custom_types.contains_key(&name) {
            skipped.push(SkippedItem {
                item_type: "CUSTOM_TYPES".into(),
                name,
                source_file: incoming.source_file.clone(),
                line_number: 0,
                reason: format!("Conflicting custom type while merging {}; kept first definition", existing.package_name),
            });
        } else {
            existing.custom_types.insert(name, value);
        }
    }
    existing.comments.extend(incoming.comments);
    existing.table_refs.extend(incoming.table_refs);
    if !incoming.java_package.is_empty() {
        if existing.java_package.is_empty() {
            existing.java_package = incoming.java_package;
        } else if existing.java_package != incoming.java_package {
            skipped.push(SkippedItem {
                item_type: "JAVA_PACKAGE".into(),
                name: existing.package_name.clone(),
                source_file: incoming.source_file,
                line_number: 0,
                reason: format!("Conflicting Java package while merging {}; kept first definition", existing.package_name),
            });
        }
    }
}

fn track_package_origins(
    parse_output: &ogsql_parser::parser::ParseOutput,
    origins: &mut BTreeMap<String, BTreeSet<String>>,
) {
    use ogsql_parser::ast::Statement;

    for statement in &parse_output.statements {
        let name = match &statement.statement {
            Statement::CreatePackage(pkg) => Some(&pkg.node.name),
            Statement::CreatePackageBody(pkg) => Some(&pkg.node.name),
            _ => None,
        };
        let Some(name) = name else { continue };
        let Some(registration_name) = name.last() else { continue };
        origins
            .entry(registration_name.to_string())
            .or_default()
            .insert(name.join("."));
    }
}

fn phase2_analyze(
    parsed: ParsedPackages,
    mut ctx: &mut AnalysisContext,
    sql_files: &[PathBuf],
    debug: bool,
) -> AnalyzedPackages {
    let mut errors = Vec::new();
    let mut packages = parsed.packages;
    ctx.debug = debug;

    let proc_summaries: std::collections::HashMap<String, PackageSummary> = parsed
        .summaries
        .iter()
        .map(|s| (s.name.clone(), s.clone()))
        .collect();

    let ddl_schema = crate::generate::itest::parse_table_ddl(sql_files);

    let all_pkg_names: Vec<String> = proc_summaries.keys().cloned().collect();

    // Build a global map: java_method_name → service_variable_name for all procedures
    let mut global_proc_map: HashMap<String, String> = HashMap::new();
    for (_summary_name, summary) in &proc_summaries {
        for proc_in_summary in &summary.procedures {
            let method_name = crate::naming::java_method_name(&proc_in_summary.proc_name);
            let svc_pkg = if !proc_in_summary.package.is_empty() {
                proc_in_summary.package.clone()
            } else {
                summary.name.clone()
            };
            let class_name = crate::naming::package_to_classname(&svc_pkg);
            let mut c = class_name.chars();
            let svc_var = match c.next() {
                Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                None => String::new(),
            };
            global_proc_map.entry(method_name).or_insert_with(|| format!("{}Service", svc_var));
        }
    }

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
            proc.all_proc_params = global_proc_map.clone();
            idx += 1;
            crate::progress::progress_bar("Analyze", idx, total, &proc.name);

            if let Err(e) = crate::analyze::analyze_procedure(proc, &proc_summaries, &mut ctx, &ddl_schema, debug) {
                errors.push(e);
            }
        }
        for proc in &mut pkg.procedures {
            crate::analyze::promote_out_local_vars(proc);
        }
        crate::analyze::discover_cross_service_refs(pkg, &all_pkg_names);
    }

    crate::progress::progress_done("Analyze", total);

    AnalyzedPackages {
        packages,
        summaries: parsed.summaries,
        warnings: parsed.warnings,
        skipped: parsed.skipped,
        errors,
    }
}

fn phase3_generate(
    analyzed: &mut AnalyzedPackages,
    config: &AppConfig,
    _incremental: &IncrementalState,
    sql_files: &[PathBuf],
    debug: bool,
) -> (Vec<String>, usize, usize, Vec<ConversionError>) {
    let base_package = config.base_package_or_default();
    let output_dir_str = config.output_dir_or_default();
    let output_dir = std::path::Path::new(&output_dir_str);
    let mut generated = Vec::new();
    let mut errors = Vec::new();
    let mut test_count = 0usize;
    let mut itest_count = 0usize;

    let encoding_name = config.encoding_or_default();
    let encoding = encoding_rs::Encoding::for_label(encoding_name.as_bytes())
        .unwrap_or(encoding_rs::UTF_8);

    match crate::generate::skeleton::write_skeleton_files(output_dir, config, &base_package, encoding) {
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
        if let Err(e) = crate::generate::itest::write_abstract_integration_test(output_dir, &base_package, encoding) {
            errors.push(ConversionError::Io {
                path: output_dir.to_string_lossy().into_owned(),
                message: format!("write_abstract_integration_test: {}", e),
            });
        }

        if let Err(e) = crate::generate::itest::write_itest_schema_sql(output_dir, &analyzed.packages, schema_map.as_ref().unwrap(), encoding) {
            errors.push(ConversionError::Io {
                path: output_dir.to_string_lossy().into_owned(),
                message: format!("write_itest_schema_sql: {}", e),
            });
        }
    }

    let total = analyzed.packages.len();
    let all_packages: Vec<_> = analyzed.packages.clone();
    for (idx, pkg) in analyzed.packages.iter_mut().enumerate() {
        let n = idx + 1;
        crate::progress::progress_bar("Generate", n, total, &pkg.package_name);

        let service_injections = crate::generate::service::collect_service_injections(pkg);

        // Service class first — it populates extra_mapper_methods for nextval/currval stubs
        match crate::generate::service::write_service_class(output_dir, pkg, &base_package, &service_injections, encoding, debug) {
            Ok(name) => generated.push(format!("{}.java", name)),
            Err(e) => {
                errors.push(ConversionError::Io {
                    path: pkg.package_name.clone(),
                    message: format!("write_service_class: {}", e),
                });
                continue;
            }
        }

        if let Err(e) = crate::generate::mapper::write_mapper_interface(output_dir, pkg, &base_package, encoding) {
            errors.push(ConversionError::Io {
                path: pkg.package_name.clone(),
                message: format!("write_mapper_interface: {}", e),
            });
            continue;
        }

        if let Err(e) = crate::generate::mapper::write_mapper_xml(output_dir, pkg, &base_package, encoding) {
            errors.push(ConversionError::Io {
                path: pkg.package_name.clone(),
                message: format!("write_mapper_xml: {}", e),
            });
            continue;
        }

        if let Err(e) = crate::generate::test::write_service_test(output_dir, pkg, &base_package, &service_injections, encoding) {
            errors.push(ConversionError::Io {
                path: pkg.package_name.clone(),
                message: format!("write_service_test: {}", e),
            });
        } else {
            test_count += 1;
        }

        if let Some(sm) = schema_map.as_ref() {
            if let Err(e) = crate::generate::itest::write_itest_class(output_dir, pkg, &base_package, &service_injections, &all_packages, sm, encoding) {
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

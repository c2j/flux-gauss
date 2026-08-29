use crate::context::{AnalysisContext, ScanContext};
use crate::types::{ConversionError, PackageInfo, ProcedureInfo, UnresolvedCall};
use ogsql_parser::ast::plpgsql::PlStatement;

pub fn analyze_procedure(
    proc: &mut ProcedureInfo,
    summaries: &std::collections::HashMap<String, crate::types::PackageSummary>,
    ctx: &mut AnalysisContext,
    ddl_schema: &std::collections::HashMap<String, std::collections::HashMap<String, String>>,
    debug: bool,
) -> Result<(), ConversionError> {
    let body = proc.body.take();
    let mut result = Ok(());
    if let Some(ref body_inner) = body {
        fn has_goto_deep(stmts: &[PlStatement]) -> bool {
            for s in stmts {
                match s {
                    PlStatement::Goto { .. } => return true,
                    PlStatement::If(if_stmt) => {
                        if has_goto_deep(&if_stmt.node.then_stmts) {
                            return true;
                        }
                        for elsif in &if_stmt.node.elsifs {
                            if has_goto_deep(&elsif.stmts) {
                                return true;
                            }
                        }
                        if has_goto_deep(&if_stmt.node.else_stmts) {
                            return true;
                        }
                    }
                    PlStatement::Block(block) => {
                        if has_goto_deep(&block.node.body) {
                            return true;
                        }
                        if let Some(exc) = &block.node.exception_block {
                            for h in &exc.handlers {
                                if has_goto_deep(&h.statements) {
                                    return true;
                                }
                            }
                        }
                    }
                    PlStatement::Loop(loop_stmt) => {
                        if has_goto_deep(&loop_stmt.node.body) {
                            return true;
                        }
                    }
                    PlStatement::While(while_stmt) => {
                        if has_goto_deep(&while_stmt.node.body) {
                            return true;
                        }
                    }
                    PlStatement::For(for_stmt) => {
                        if has_goto_deep(&for_stmt.node.body) {
                            return true;
                        }
                    }
                    PlStatement::ForEach(for_each) => {
                        if has_goto_deep(&for_each.node.body) {
                            return true;
                        }
                    }
                    PlStatement::Case(case_stmt) => {
                        for when in &case_stmt.node.whens {
                            if has_goto_deep(&when.stmts) {
                                return true;
                            }
                        }
                        if has_goto_deep(&case_stmt.node.else_stmts) {
                            return true;
                        }
                    }
                    _ => {}
                }
            }
            false
        }
        if has_goto_deep(&body_inner.body) {
            let analysis =
                crate::statements::goto::analyze_goto_patterns(&body_inner.body, proc, &mut ctx.source_cache);
            proc.goto_analysis = Some(analysis);
        }
        for decl in &body_inner.declarations {
            process_declaration(decl, proc, ddl_schema, Some(ctx));
        }
        let has_exceptions = body_inner.exception_block.is_some();
        if has_exceptions {
            proc.java_logic_lines.push("try {".into());
        }
        let mut stmt_ctx = crate::context::StatementContext::new(summaries);
        let pkg_key = proc.package.to_lowercase();
        if !ctx.dml_counters.contains_key(&pkg_key) {
            ctx.dml_counters.insert(pkg_key.clone(), std::collections::HashMap::new());
        }
        stmt_ctx.dml_counter = ctx.dml_counters.get(&pkg_key).cloned().unwrap_or_default();
        stmt_ctx.debug = debug;
        if debug {
            stmt_ctx.stmt_lines = crate::debug::find_body_stmt_lines(proc, ctx);
        }
        for (idx, stmt) in body_inner.body.iter().enumerate() {
            stmt_ctx.current_stmt_idx = idx;
            if let Err(e) = crate::statement::process_statement(stmt, proc, &mut stmt_ctx) {
                ctx.stub_procedures.insert((proc.name.clone(), proc.parameters.len()));
                result = Err(e);
                break;
            }
        }
        ctx.dml_counters.insert(pkg_key, stmt_ctx.dml_counter.clone());
        // After normal processing, if GOTO pattern detected, rewrite the procedure body
        if proc.goto_analysis.as_ref().map_or(false, |a| a.pattern.is_some()) {
            let analysis = proc.goto_analysis.take().unwrap();
            proc.java_logic_lines.clear();
            proc.dml_statements.clear();
            proc.select_counter = 0;
            proc.for_loop_counter = 0;
            let rewrite_result =
                crate::statements::goto::rewrite_with_pattern(&body_inner.body, &analysis, proc, summaries);
            proc.goto_analysis = Some(analysis);
            if let Err(e) = rewrite_result {
                proc.java_logic_lines.push("// TODO: GOTO pattern requires manual implementation".into());
                result = Err(e);
            } else {
                // GOTO rewrite succeeded — reset result to clear any Err from normal processing
                result = Ok(());
            }
            if has_exceptions {
                proc.java_logic_lines.insert(0, "try {".into());
            }
        }
        // Multi-WHEN EXCEPTION → chained catch on one try:
        //   try { ... } catch (A e) { ... } catch (B e) { ... }
        // Each catch line starts with "}" (closes try or previous catch).
        // Only ONE final "}" after all handlers — do NOT close per-handler
        // (that orphans subsequent catch clauses; Issue #61 / MergeSales).
        if let Some(exc_block) = &body_inner.exception_block {
            for handler in &exc_block.handlers {
                let is_others = handler.conditions.is_empty()
                    || handler.conditions.iter().any(|c| c.eq_ignore_ascii_case("others"));
                let evar = format!("__e{}", {
                    let n = proc.catch_counter;
                    proc.catch_counter += 1;
                    n + 1
                });
                if is_others {
                    proc.java_logic_lines.push(format!("}} catch (Exception {evar}) {{"));
                } else {
                    let cond = handler.conditions.join(", ");
                    proc.java_logic_lines.push(format!("}} catch (BusinessException {evar}) {{ // {}", cond));
                }
                proc.java_logic_lines.push(format!("    __SQLERRM__ = {evar}.getMessage();"));
                proc.java_logic_lines.push("    __SQLCODE__ = -1;".into());
                for s in &handler.statements {
                    if let Err(_) = crate::statement::process_statement(s, proc, &mut stmt_ctx) {
                        break;
                    }
                }
                if crate::statement::is_unreachable_after_terminal(&proc.java_logic_lines) {
                    break;
                }
            }
            if has_exceptions {
                proc.java_logic_lines.push("}".into());
            }
        }
        ctx.unresolved_calls.extend(stmt_ctx.unresolved_calls.drain(..));
    }
    // Apply any bare-local-var promotions queued by emit_cross_pkg_call (see
    // crate::expr::take_pending_out_promotions) while generating this procedure's
    // statements above — expr.rs only has `&ProcedureInfo`, so it can't write
    // proc.out_local_vars directly.
    for (var_lower, java_type) in crate::expr::take_pending_out_promotions() {
        proc.out_local_vars.insert(var_lower, java_type);
    }
    proc.body = body;
    result
}

pub fn process_declaration(
    decl: &ogsql_parser::ast::plpgsql::PlDeclaration,
    proc: &mut ProcedureInfo,
    ddl_schema: &std::collections::HashMap<String, std::collections::HashMap<String, String>>,
    mut ctx: Option<&mut AnalysisContext>,
) {
    use ogsql_parser::ast::plpgsql::PlDeclaration;
    match decl {
        PlDeclaration::Variable(var) => {
            let java_type = match &var.data_type {
                ogsql_parser::ast::plpgsql::PlDataType::PercentRowType(_) => {
                    proc.imports.insert("import java.util.Map;".into());
                    "Map<String, Object>".into()
                }
                ogsql_parser::ast::plpgsql::PlDataType::PercentType { table, column } => {
                    let table_lower = table.to_lowercase();
                    let column_lower = column.to_lowercase();
                    if let Some(columns) = ddl_schema.get(&table_lower) {
                        if let Some(raw_sql_type) = columns.get(&column_lower) {
                            let sql_type = crate::extract::normalize_sql_type(raw_sql_type);
                            crate::type_map::sql_type_to_java(&sql_type)
                                .map(|s| s.to_string())
                                .unwrap_or_else(|| "String".into())
                        } else {
                            let inferred = crate::type_map::infer_sql_type_from_column_name(column);
                            crate::type_map::sql_type_to_java(inferred)
                                .map(|s| s.to_string())
                                .unwrap_or_else(|| "String".into())
                        }
                    } else {
                        let inferred = crate::type_map::infer_sql_type_from_column_name(column);
                        crate::type_map::sql_type_to_java(inferred)
                            .map(|s| s.to_string())
                            .unwrap_or_else(|| "String".into())
                    }
                }
                _ => {
                    let sql_type_raw = crate::extract::format_pl_data_type(&var.data_type);
                    let sql_type_lower = sql_type_raw.to_lowercase();
                    // Detect array-like types (e.g., pkg_param_common.arrytype, VARCHAR2_ARRAY)
                    if sql_type_lower.contains("arrytype")
                        || sql_type_lower.contains("array_type")
                        || sql_type_lower.ends_with("_array")
                    {
                        proc.imports.insert("import java.util.List;".into());
                        proc.imports.insert("import java.util.Collections;".into());
                        proc.has_array_vars = true;
                        "List<String>".into()
                    } else {
                        let sql_type = crate::extract::normalize_sql_type(&sql_type_raw);
                        let sql_type_lower = sql_type.to_lowercase();
                        if let Some(ct) =
                            proc.custom_types.get(&sql_type_lower).or_else(|| proc.custom_types.get(&sql_type))
                        {
                            if ct.is_record {
                                proc.imports.insert("import java.util.Map;".into());
                                "Map<String, Object>".into()
                            } else {
                                proc.imports.insert("import java.util.List;".into());
                                "java.util.List<Object>".into()
                            }
                        } else {
                            crate::type_map::sql_type_to_java(&sql_type)
                                .map(|s| s.to_string())
                                .unwrap_or_else(|| "Object".into())
                        }
                    }
                }
            };
            proc.local_vars.insert(var.name.to_lowercase(), java_type.clone());
            if let Some(ref mut ctx) = ctx {
                if ctx.debug {
                    if let Some(line) = crate::debug::find_var_decl_line(proc, &var.name, ctx) {
                        proc.local_var_source_lines.insert(var.name.to_lowercase(), line);
                    }
                }
            }
            if let Some(default) = &var.default {
                // Detect pkg_param_common.getarray() calls → stringToArray()
                let default_java = match default {
                    ogsql_parser::ast::Expr::FunctionCall { name, args, .. } => {
                        let name_str = name.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(".");
                        let name_lower = name_str.to_lowercase();
                        if name_lower.contains("getarray") && args.len() >= 2 {
                            let arg1 = crate::expr::expr_to_java(&args[0], proc);
                            let arg2 = crate::expr::expr_to_java(&args[1], proc);
                            proc.has_array_vars = true;
                            format!("this.stringToArray({}, {})", arg1, arg2)
                        } else {
                            crate::expr::expr_to_java(default, proc)
                        }
                    }
                    _ => crate::expr::expr_to_java(default, proc),
                };
                proc.local_var_defaults.insert(var.name.to_lowercase(), default_java);
            }
        }
        PlDeclaration::Cursor(cursor) => {
            proc.cursor_decls.insert(cursor.name.clone(), cursor.query.clone());
            if !cursor.arguments.is_empty() {
                let params: Vec<String> = cursor.arguments.iter().map(|a| a.name.clone()).collect();
                proc.cursor_params.insert(cursor.name.clone(), params);
            }
        }
        PlDeclaration::Record(rec) => {
            proc.local_vars.insert(rec.name.to_lowercase(), "Map<String, Object>".into());
            proc.imports.insert("import java.util.Map;".into());
            if let Some(ref mut ctx) = ctx {
                if ctx.debug {
                    if let Some(line) = crate::debug::find_var_decl_line(proc, &rec.name, ctx) {
                        proc.local_var_source_lines.insert(rec.name.to_lowercase(), line);
                    }
                }
            }
        }
        PlDeclaration::Type(_type_decl) => {}
        PlDeclaration::NestedProcedure(_) | PlDeclaration::NestedFunction(_) => {}
        PlDeclaration::Pragma { .. } => {}
    }
}

pub fn promote_out_local_vars(proc: &mut ProcedureInfo) {
    for param in &proc.parameters {
        if param.is_out() && !param.is_refcursor() {
            proc.imports.insert("import java.util.concurrent.atomic.AtomicReference;".into());
        }
    }
}

pub fn discover_cross_service_refs(pkg: &mut crate::types::PackageInfo, known_packages: &[String]) {
    let own_svc = format!("{}Service", {
        let cn = crate::naming::package_to_classname(&pkg.package_name);
        let mut c = cn.chars();
        match c.next() {
            Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
            None => String::new(),
        }
    });
    let existing_calls: std::collections::HashSet<String> =
        pkg.procedures.iter().flat_map(|p| p.service_calls.iter().map(|c| c.service_name.clone())).collect();

    let system_prefixes =
        ["dbe_scheduler", "dbms_output", "dbms_random", "dbms_lob", "dbe_output", "utl_file", "dbms_sql", "dbms_job"];

    let known_svc_names: std::collections::HashMap<String, String> = known_packages
        .iter()
        .filter(|pkg_name| {
            let lower = pkg_name.to_lowercase();
            !system_prefixes.iter().any(|sp| lower.starts_with(sp))
        })
        .map(|pkg_name| {
            let cn = crate::naming::package_to_classname(pkg_name);
            let svc_name = format!("{}Service", {
                let mut c = cn.chars();
                match c.next() {
                    Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                    None => String::new(),
                }
            });
            (svc_name, pkg_name.clone())
        })
        .collect();

    let re = regex::Regex::new(r"(\w+Service)\.").unwrap();
    for proc in &mut pkg.procedures {
        // Declaration-section default initializers (e.g. `v NUMBER := fn_calc(...)`) are
        // rendered from `local_var_defaults` by generate/service.rs, not from
        // `java_logic_lines` (that only holds body statements). A cross-pkg call appearing
        // ONLY in a declare-section initializer would otherwise never be seen by this scan,
        // so the callee service field/constructor param/import silently never gets injected
        // (#107/#108 root cause A). Scan both sources with identical logic.
        let scan_targets: Vec<String> =
            proc.java_logic_lines.iter().cloned().chain(proc.local_var_defaults.values().cloned()).collect();
        for line in &scan_targets {
            if line.trim().starts_with("//") {
                continue;
            }
            for cap in re.captures_iter(line) {
                let svc_name = cap[1].to_string();
                if svc_name == own_svc || existing_calls.contains(&svc_name) {
                    continue;
                }
                if let Some(pkg_name) = known_svc_names.get(&svc_name) {
                    proc.service_calls.push(crate::types::ServiceCall {
                        service_name: svc_name.clone(),
                        method_name: String::new(),
                        args: Vec::new(),
                        package_name: pkg_name.clone(),
                    });
                }
            }
        }
    }
}

// Scans generated Java lines for `/* TOBEFIX: unresolved fn|name ... */` markers
// (injected by expr.rs when a bare call/identifier can't be resolved) and records
// each occurrence as an UnresolvedCall so it surfaces in the conversion report's
// existing "未解析的跨包调用" section — mirrors the discover_cross_service_refs
// post-scan pattern above (expr.rs is a pure fn with no ctx access).
pub fn collect_tobefix_warnings(pkg: &PackageInfo, ctx: &mut AnalysisContext) {
    let re = regex::Regex::new(r"TOBEFIX: unresolved (?:fn|name) ([a-zA-Z0-9_]+)\((.*?)\) - ").unwrap();
    for proc in &pkg.procedures {
        for line in &proc.java_logic_lines {
            for cap in re.captures_iter(line) {
                ctx.unresolved_calls.push(UnresolvedCall {
                    caller: format!("{}.{}", proc.package, proc.proc_name),
                    callee: cap[1].to_string(),
                    caller_file: proc.source_file.clone(),
                    args: cap[2].to_string(),
                    hint: "TOBEFIX: 函数/名称未解析（定义包不在 sources 或跨包同名冲突），需人工确认".into(),
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_analyze_context_new() {
        let ctx = AnalysisContext::new();
        assert!(ctx.package_summaries.is_empty());
        assert!(ctx.stub_procedures.is_empty());
    }

    #[test]
    fn test_scan_context_new() {
        let ctx = ScanContext::new();
        assert!(ctx.type_overrides.is_empty());
    }

    #[test]
    fn test_out_promotion_queue_drained_after_analyze() {
        // Task 3 (#108) uses a thread_local side-channel (expr.rs's
        // PENDING_OUT_PROMOTIONS) because expr rendering only holds
        // &ProcedureInfo. analyze_procedure must drain it fully; a leftover
        // entry would leak into the NEXT procedure's promotion set. This guard
        // exists so a future parallelization (rayon etc.) fails loudly instead
        // of silently cross-contaminating procedures.
        assert!(
            crate::expr::take_pending_out_promotions().is_empty(),
            "PENDING_OUT_PROMOTIONS must be empty between procedure analyses \
             (thread_local side-channel leak)"
        );
    }
}

use crate::context::AnalysisContext;
use crate::types::{ConversionError, PackageInfo, ProcedureInfo, UnresolvedCall};
use ogsql_parser::ast::plpgsql::PlStatement;
use std::collections::HashMap;

/// Issue #124: re-resolve parameter / package-variable Java types whose SQL
/// type names a package custom type (record → Map, collection → List<elem>).
/// `convert_params` ran during extraction before `custom_types` was fully
/// populated, so these arrive as `Object`.
fn resolve_custom_typed_symbols(proc: &mut ProcedureInfo) {
    if proc.custom_types.is_empty() {
        return;
    }
    for p in &mut proc.parameters {
        if p.java_type != "Object" {
            continue;
        }
        if let Some(ct) = crate::extract::find_custom_type(&proc.custom_types, &p.sql_type) {
            p.java_type = crate::extract::custom_type_java_type(ct);
        }
    }
    for vi in proc.package_vars.values_mut() {
        if vi.java_type != "Object" {
            continue;
        }
        if let Some(ct) = crate::extract::find_custom_type(&proc.custom_types, &vi.sql_type) {
            vi.java_type = crate::extract::custom_type_java_type(ct);
        }
    }
}

/// Review #129 B3: resolve a record field's raw SQL type text to a Java
/// type. `<table>.<col>%TYPE` anchors resolve through the DDL schema cache
/// first, then fall back to column-name inference; anything else goes
/// through the normalizing type lookup. Returns `None` when nothing
/// resolves (caller keeps the extraction-time value).
fn record_field_java_type(
    sql_type: &str,
    ddl_schema: &std::collections::HashMap<String, std::collections::HashMap<String, String>>,
) -> Option<String> {
    let lower = sql_type.to_lowercase();
    if lower.contains("%rowtype") {
        return Some("Map<String, Object>".into());
    }
    if lower.contains("%type") {
        // "[schema.]table.column%TYPE" — anchor is everything before '%'.
        let anchor = sql_type.split('%').next().unwrap_or("").trim();
        let (tbl_part, col) = anchor.rsplit_once('.')?;
        let tbl_lower = tbl_part.rsplit('.').next().unwrap_or(tbl_part).to_lowercase();
        let col_lower = col.to_lowercase();
        if let Some(cols) = ddl_schema.get(&tbl_lower) {
            if let Some(raw) = cols.get(&col_lower) {
                let normalized = crate::extract::normalize_sql_type(raw);
                if let Some(j) = crate::type_map::sql_type_to_java(&normalized) {
                    return Some(j.to_string());
                }
            }
        }
        // DDL not in sources (delivery configs may omit the table) — fall
        // back to column-name inference rather than leaving Object, so
        // generated fixtures get typed seeds instead of 145× null.
        let mut inferred = crate::type_map::infer_sql_type_from_column_name(col);
        // Review #129 §3: `*_date` columns are overwhelmingly Oracle DATE,
        // not TIMESTAMP — a Timestamp seed stringifies to
        // "yyyy-MM-dd HH:mm:ss.S" which LocalDate.parse rejects. Narrow
        // override ONLY on this record-field fallback path; the shared
        // infer_sql_type_from_column_name feeds local-var declarations
        // whose timestamp branch has a self-consistent initializer, so
        // changing it globally breaks compilation (verified rc=1).
        if inferred == "timestamp" {
            let col_l = col.to_lowercase();
            if col_l == "date" || col_l.ends_with("_date") {
                inferred = "date";
            }
        }
        return crate::type_map::sql_type_to_java(inferred).map(|s| s.to_string()).or_else(|| Some("String".into()));
    }
    crate::type_map::sql_type_to_java(sql_type).map(|s| s.to_string())
}

/// Review #129 B3: single source of truth for record field Java types.
/// Extraction collapses `<table>.<col>%TYPE` to `Object` because the DDL
/// schema cache does not exist yet; resolve it here — BEFORE the per-proc
/// `custom_types` clone — so DTO declarations, unit fixtures and
/// integration fixtures all consume the same resolved types.
pub fn resolve_custom_record_field_types(
    custom_types: &mut HashMap<String, crate::types::CustomTypeInfo>,
    ddl_schema: &std::collections::HashMap<String, std::collections::HashMap<String, String>>,
) -> Vec<(String, String, String)> {
    // Review #129 §4.4: returns (type_name, field_name, sql_type) for every
    // `%TYPE` anchor that could not be resolved at all (e.g. variable-anchored
    // `x%TYPE` with no `table.column` shape) — the caller reports these into
    // unresolved_calls instead of letting them silently stay Object/null.
    let mut unresolved = Vec::new();
    for (tname, ct) in custom_types.iter_mut() {
        if !ct.is_record {
            continue;
        }
        let sql_by_field: HashMap<&str, &str> =
            ct.field_sql_types.iter().map(|(n, t)| (n.as_str(), t.as_str())).collect();
        for (fname, jt) in ct.fields.iter_mut() {
            let Some(sql) = sql_by_field.get(fname.as_str()).copied() else { continue };
            match record_field_java_type(sql, ddl_schema) {
                Some(resolved) => *jt = resolved,
                None => {
                    if sql.to_lowercase().contains("%type") {
                        unresolved.push((tname.clone(), fname.clone(), sql.to_string()));
                    }
                }
            }
        }
    }
    unresolved
}

/// Resolve a `%ROWTYPE` table reference to { field_lower → Java type } using
/// the DDL schema cache. Shared by local-var declarations and %ROWTYPE params.
fn rowtype_fields_for_table(
    table: &str,
    ddl_schema: &std::collections::HashMap<String, std::collections::HashMap<String, String>>,
) -> HashMap<String, String> {
    let table_lower = table.split('.').next_back().unwrap_or(table).to_lowercase();
    ddl_schema
        .get(&table_lower)
        .map(|cols| {
            cols.iter()
                .map(|(col, raw_sql_type)| {
                    let sql_type = crate::extract::normalize_sql_type(raw_sql_type);
                    let java = crate::type_map::sql_type_to_java(&sql_type)
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| "String".into());
                    (col.to_lowercase(), java)
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Field-type map (`field_lower → java type`) for a shim-configured composite
/// type. Extracted record fields win when the type is visible to the parser;
/// otherwise the `fields` declared in `composite_type_shims` are used (shim
/// types created via `DO $$ EXECUTE 'CREATE TYPE …'` are invisible to
/// extraction). Returns `None` when the type is not shim-configured.
fn composite_shim_fields(
    proc: &ProcedureInfo,
    sql_type: &str,
    ctx: Option<&AnalysisContext>,
) -> Option<std::collections::HashMap<String, String>> {
    let ctx = ctx?;
    if !ctx.is_composite_shim(sql_type) {
        return None;
    }
    if let Some(ct) = crate::extract::find_custom_type(&proc.custom_types, sql_type)
        .or_else(|| crate::extract::find_custom_type(&proc.custom_types, &sql_type.to_lowercase()))
    {
        if ct.is_record && !ct.fields.is_empty() {
            return Some(ct.fields.iter().map(|(n, t)| (n.to_lowercase(), t.clone())).collect());
        }
    }
    ctx.composite_shim_field_types(sql_type).cloned()
}

pub fn analyze_procedure(
    proc: &mut ProcedureInfo,
    summaries: &std::collections::HashMap<String, crate::types::PackageSummary>,
    ctx: &mut AnalysisContext,
    ddl_schema: &std::collections::HashMap<String, std::collections::HashMap<String, String>>,
    debug: bool,
) -> Result<(), ConversionError> {
    resolve_custom_typed_symbols(proc);
    // Review #129 B3: %ROWTYPE params lower to Map<String,Object>; resolve
    // their field types from the DDL cache so generated unit/integration
    // fixtures can populate the record-shaped map instead of emitting {}.
    let rowtype_params: Vec<(String, String)> = proc
        .parameters
        .iter()
        .filter(|p| p.sql_type.to_lowercase().contains("%rowtype"))
        .map(|p| (p.name.clone(), p.sql_type.clone()))
        .collect();
    for (pname, sql_type) in rowtype_params {
        let table = sql_type.split('%').next().unwrap_or("").trim();
        let fields = rowtype_fields_for_table(table, ddl_schema);
        if !fields.is_empty() {
            proc.param_rowtype_fields.insert(pname.to_lowercase(), fields);
        }
    }
    // composite_type_shims config: params declared with a shim composite type
    // (e.g. map_object) get record field types so `p_map.k` lowers to a typed
    // List access instead of a raw Object map get.
    let shim_params: Vec<(String, String)> = proc
        .parameters
        .iter()
        .filter(|p| ctx.is_composite_shim(&p.sql_type))
        .map(|p| (p.name.clone(), p.sql_type.clone()))
        .collect();
    for (pname, sql_type) in shim_params {
        if let Some(fields) = composite_shim_fields(proc, &sql_type, Some(&*ctx)) {
            proc.param_rowtype_fields.insert(pname.to_lowercase(), fields);
        }
    }
    // Issue #136 batch-4 F5: snapshot which IN params participate in NULL
    // semantics BEFORE `body` is detached for statement lowering — the
    // boxed-signature decision must be identical during expr lowering and
    // method emission.
    let null_semantic_params: Vec<String> = proc
        .parameters
        .iter()
        .filter(|p| !p.is_out() && crate::generate::service::param_in_null_semantics(proc, &p.name))
        .map(|p| p.name.to_lowercase())
        .collect();
    proc.null_semantics_params.extend(null_semantic_params);
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
        if !ctx.dml_used_ids.contains_key(&pkg_key) {
            ctx.dml_used_ids.insert(pkg_key.clone(), std::collections::HashSet::new());
        }
        stmt_ctx.dml_used_ids = ctx.dml_used_ids.get(&pkg_key).cloned().unwrap_or_default();
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
        // After normal processing, if GOTO pattern detected, rewrite the procedure body
        if proc.goto_analysis.as_ref().is_some_and(|a| a.pattern.is_some()) {
            let analysis = proc.goto_analysis.take().unwrap();
            proc.java_logic_lines.clear();
            proc.dml_statements.clear();
            proc.select_counter = 0;
            proc.for_loop_counter = 0;
            let rewrite_result =
                crate::statements::goto::rewrite_with_pattern(&body_inner.body, &analysis, proc, &mut stmt_ctx);
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
                    if crate::statement::process_statement(s, proc, &mut stmt_ctx).is_err() {
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
        ctx.dml_counters.insert(pkg_key.clone(), stmt_ctx.dml_counter.clone());
        ctx.dml_used_ids.insert(pkg_key, stmt_ctx.dml_used_ids.clone());
        ctx.unresolved_calls.append(&mut stmt_ctx.unresolved_calls);
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
                ogsql_parser::ast::plpgsql::PlDataType::PercentRowType(table) => {
                    proc.imports.insert("import java.util.Map;".into());
                    // Resolve field types from the table's DDL so field access
                    // (v_wm.wm_ts_value) can emit typed extraction.
                    let field_types = rowtype_fields_for_table(table, ddl_schema);
                    proc.rowtype_field_types.insert(var.name.to_lowercase(), field_types);
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
                    let sql_type = crate::extract::normalize_sql_type(&sql_type_raw);
                    let sql_type_lower = sql_type.to_lowercase();
                    // Review #129 S1: resolve declared custom types BEFORE the
                    // array-name heuristic — `TYPE t_item_array IS TABLE OF
                    // r_order_item` must not be flattened to List<String> by
                    // its "_array" suffix.
                    let ct = crate::extract::find_custom_type(&proc.custom_types, &sql_type)
                        .or_else(|| crate::extract::find_custom_type(&proc.custom_types, &sql_type_lower));
                    if let Some(ct) = ct {
                        if ct.is_record {
                            proc.imports.insert("import java.util.Map;".into());
                            // composite_type_shims: record fields of a
                            // configured shim type (e.g. map_object k/v
                            // text[]) lower to typed parallel Lists.
                            if let Some(fields) = composite_shim_fields(proc, &sql_type, ctx.as_deref()) {
                                proc.rowtype_field_types.insert(var.name.to_lowercase(), fields);
                            }
                            "Map<String, Object>".into()
                        } else if let Some(ref elem) = ct.elem_java_type {
                            // Issue #124: typed collections lower to
                            // List<Elem> (record elem → List<Map<...>>).
                            proc.imports.insert("import java.util.List;".into());
                            if elem.contains("Map") {
                                proc.imports.insert("import java.util.Map;".into());
                            }
                            format!("java.util.List<{}>", elem)
                        } else {
                            proc.imports.insert("import java.util.List;".into());
                            "java.util.List<Object>".into()
                        }
                    } else {
                        // composite_type_shims: the declared type is invisible
                        // to extraction (e.g. created via DO-block EXECUTE) —
                        // field types come from the yaml declaration so
                        // `v.k`/`v.k := v.k || e` lower to typed List ops.
                        // Match on the RAW type name: `sql_type` may already
                        // be normalized to the generic "record".
                        if let Some(fields) = composite_shim_fields(proc, &sql_type_raw, ctx.as_deref()) {
                            proc.rowtype_field_types.insert(var.name.to_lowercase(), fields);
                        }
                        // Detect array-like types (e.g., pkg_param_common.arrytype, VARCHAR2_ARRAY)
                        let raw_lower = sql_type_raw.to_lowercase();
                        if raw_lower.contains("arrytype")
                            || raw_lower.contains("array_type")
                            || raw_lower.ends_with("_array")
                        {
                            proc.imports.insert("import java.util.List;".into());
                            proc.imports.insert("import java.util.Collections;".into());
                            proc.has_array_vars = true;
                            "List<String>".into()
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
    // Local vars promoted to AtomicReference<T> for OUT-arg usage may have been
    // assigned earlier in the body (before the promoting cross-package call was
    // seen), emitting `var = expr;` against what is now an AtomicReference —
    // uncompilable. Rewrite those assignments to `var.set(expr);`.
    if proc.out_local_vars.is_empty() {
        return;
    }
    let promoted: std::collections::HashSet<String> =
        proc.out_local_vars.keys().map(|k| crate::naming::snake_to_camel(k)).collect();
    if promoted.is_empty() {
        return;
    }
    static ASSIGN_RE: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"^(\s*)([A-Za-z_]\w*)\s*=\s*([^=].*);").unwrap());
    for line in proc.java_logic_lines.iter_mut() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("//") || trimmed.starts_with("/*") || trimmed.starts_with('*') {
            continue;
        }
        if let Some(cap) = ASSIGN_RE.captures(line) {
            let var_ref: &str = &cap[2];
            if promoted.contains(var_ref) {
                let indent = cap[1].to_string();
                let var = cap[2].to_string();
                let expr = cap[3].to_string();
                *line = format!("{}{}.set({});", indent, var, expr);
            }
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
    use crate::context::ScanContext;

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
    fn test_record_field_percent_type_resolves_via_ddl_and_inference() {
        // Review #129 B3: `<table>.<col>%TYPE` record fields must resolve to
        // concrete Java types — via DDL when present, column-name inference
        // otherwise — instead of staying Object (which produced all-null
        // fixtures and DateTimeParseException downstream).
        let mut custom_types = HashMap::new();
        custom_types.insert(
            "deposit_receipt".to_string(),
            crate::types::CustomTypeInfo {
                fields: vec![
                    ("maturity_date".to_string(), "Object".to_string()),
                    ("amount".to_string(), "Object".to_string()),
                    ("fund_code".to_string(), "Object".to_string()),
                ],
                field_sql_types: vec![
                    ("maturity_date".to_string(), "dat_trd_deposit.maturity_date%TYPE".to_string()),
                    ("amount".to_string(), "dat_trd_deposit.amount%TYPE".to_string()),
                    ("fund_code".to_string(), "dat_trd_deposit.fund_code%TYPE".to_string()),
                ],
                is_record: true,
                elem_java_type: None,
            },
        );
        // DDL present for one column only.
        let mut ddl = HashMap::new();
        let mut cols = HashMap::new();
        cols.insert("amount".to_string(), "NUMERIC(18,2)".to_string());
        ddl.insert("dat_trd_deposit".to_string(), cols);

        resolve_custom_record_field_types(&mut custom_types, &ddl);
        let ct = &custom_types["deposit_receipt"];
        let jt = |n: &str| ct.fields.iter().find(|(f, _)| f == n).map(|(_, t)| t.clone()).unwrap();
        assert_eq!(jt("amount"), "java.math.BigDecimal");
        // maturity_date: no DDL column → inference; `*_date` overrides the
        // timestamp guess to `date` (review #129 §3 — Timestamp seeds break
        // LocalDate.parse consumers).
        assert_eq!(jt("maturity_date"), "java.sql.Date");
        // fund_code: inference → varchar → String.
        assert_eq!(jt("fund_code"), "String");
    }

    #[test]
    fn test_record_field_plain_types_still_resolve() {
        // Non-%TYPE fields keep their extraction-time resolution.
        let mut custom_types = HashMap::new();
        custom_types.insert(
            "t".to_string(),
            crate::types::CustomTypeInfo {
                fields: vec![("a".to_string(), "String".to_string())],
                field_sql_types: vec![("a".to_string(), "VARCHAR2(50)".to_string())],
                is_record: true,
                elem_java_type: None,
            },
        );
        resolve_custom_record_field_types(&mut custom_types, &HashMap::new());
        assert_eq!(custom_types["t"].fields[0].1, "String");
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

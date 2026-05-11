use crate::context::{AnalysisContext, ScanContext};
use crate::types::{ConversionError, PackageInfo, ProcedureInfo};

pub fn analyze_procedure(
    proc: &mut ProcedureInfo,
    summaries: &std::collections::HashMap<String, crate::types::PackageSummary>,
    ctx: &mut AnalysisContext,
) -> Result<(), ConversionError> {
    let body = proc.body.take();
    let mut result = Ok(());
    if let Some(ref body_inner) = body {
        proc.goto_analysis = Some(crate::statements::goto::analyze_goto_patterns(&body_inner.body));
        for decl in &body_inner.declarations {
            process_declaration(decl, proc);
        }
        for stmt in &body_inner.body {
            if let Err(e) = crate::statement::process_statement(stmt, proc) {
                ctx.stub_procedures
                    .insert((proc.name.clone(), proc.parameters.len()));
                result = Err(e);
                break;
            }
        }
        // After normal processing, if GOTO pattern detected, rewrite the procedure body
        if proc.goto_analysis.as_ref().map_or(false, |a| a.pattern.is_some()) {
            let analysis = proc.goto_analysis.take().unwrap();
            proc.java_logic_lines.clear();
            proc.dml_statements.clear();
            let rewrite_result = crate::statements::goto::rewrite_with_pattern(
                &body_inner.body, &analysis, proc
            );
            proc.goto_analysis = Some(analysis);
            if let Err(e) = rewrite_result {
                proc.java_logic_lines.push("// TODO: GOTO pattern requires manual implementation".into());
                result = Err(e);
            }
        }
    }
    proc.body = body;
    result
}

pub fn process_declaration(
    decl: &ogsql_parser::ast::plpgsql::PlDeclaration,
    proc: &mut ProcedureInfo,
) {
    use ogsql_parser::ast::plpgsql::PlDeclaration;
    match decl {
        PlDeclaration::Variable(var) => {
            let sql_type_raw = crate::extract::format_pl_data_type(&var.data_type);
            let sql_type = crate::extract::normalize_sql_type(&sql_type_raw);
            let sql_type_lower = sql_type.to_lowercase();
            let java_type = if sql_type_lower.contains("%rowtype") {
                proc.imports.insert("import java.util.Map;".into());
                "Map<String, Object>".into()
            } else {
                crate::type_map::sql_type_to_java(&sql_type)
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| "Object".into())
            };
            proc.local_vars.insert(var.name.clone(), java_type.clone());
            if let Some(default) = &var.default {
                let default_java = crate::expr::expr_to_java(default, proc);
                proc.local_var_defaults
                    .insert(var.name.clone(), default_java);
            }
        }
        PlDeclaration::Cursor(cursor) => {
            proc.cursor_decls
                .insert(cursor.name.clone(), cursor.query.clone());
            if !cursor.arguments.is_empty() {
                let params: Vec<String> = cursor
                    .arguments
                    .iter()
                    .map(|a| a.name.clone())
                    .collect();
                proc.cursor_params.insert(cursor.name.clone(), params);
            }
        }
        PlDeclaration::Record(rec) => {
            proc.local_vars.insert(rec.name.clone(), "Map<String, Object>".into());
            proc.imports.insert("import java.util.Map;".into());
        }
        PlDeclaration::Type(_type_decl) => {}
        PlDeclaration::NestedProcedure(_) | PlDeclaration::NestedFunction(_) => {}
        PlDeclaration::Pragma { .. } => {}
    }
}

pub fn promote_out_local_vars(proc: &mut ProcedureInfo) {
    for param in &proc.parameters {
        if param.is_out() && !param.is_refcursor() {
            proc.imports
                .insert("import java.util.concurrent.atomic.AtomicReference;".into());
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
}

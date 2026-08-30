fn infer_select_result_type(var_type: &str, sql: &str) -> String {
    if matches!(var_type, "Integer" | "Long" | "int" | "long" | "Double" | "Float" | "BigDecimal") && sql.contains("||")
    {
        "String".to_string()
    } else {
        var_type.to_string()
    }
}

use crate::context::StatementContext;
use crate::expr::is_nullish_java_expr;
use crate::types::{ConversionError, DmlStatement, DmlType, ProcedureInfo, ServiceCall, UnresolvedCall};
use regex::Regex;

static ELEM_LIST_TYPE_RE: std::sync::LazyLock<Regex> =
    std::sync::LazyLock::new(|| Regex::new(r"(?i)java\.util\.List<(.+)>").unwrap());
use std::collections::HashMap;
use std::sync::OnceLock;

fn ident_string(id: &ogsql_parser::Ident) -> String {
    id.as_str().to_string()
}

fn out_param_set_expr(var_java: &str, method_id: &str, args: &str, proc: &ProcedureInfo) -> String {
    let base_name = if var_java.contains('.') { var_java.split('.').next().unwrap() } else { var_java };
    // var_java is camelCase (e.g. "pFinalBal") but proc.parameters[].name is snake_case (e.g. "p_final_bal")
    // so match by normalizing both to a comparable form
    let param_type = proc
        .parameters
        .iter()
        .find(|p| p.is_out() && (p.name == base_name || snake_to_camel(&p.name) == base_name))
        .map(|p| p.java_type.as_str())
        .unwrap_or("Object");
    let mapper_call = format!("mapper.{}({})", method_id, args);
    if param_type == "Object" || param_type == "String" {
        format!("{}.set(String.valueOf({}));", var_java, mapper_call)
    } else if param_type.contains("BigDecimal") {
        format!("{}.set(new java.math.BigDecimal(String.valueOf({})));", var_java, mapper_call)
    } else {
        format!("{}.set(({}) {});", var_java, param_type, mapper_call)
    }
}
use crate::naming::{java_method_name, package_to_classname, snake_to_camel, snake_to_pascal};
use crate::type_map::{java_type_to_jdbc, sql_type_to_jdbc};

fn cast_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\s*::\s*(?:VARCHAR2|NVARCHAR2)\b").unwrap())
}

fn strip_sql_comments(sql: &str) -> String {
    let re = Regex::new(r"--[^\n]*").unwrap();
    re.replace_all(sql, "").to_string()
}

fn into_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\s+into\s+\w+(\s*\.\s*\w+)*(\s*,\s*\w+(\s*\.\s*\w+)*)*").unwrap())
}

fn capture_into_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bINTO\s+(\w+(?:\s*,\s*\w+)*)").unwrap())
}

fn is_terminal_statement(line: &str) -> bool {
    let trimmed = line.trim_start();
    trimmed.starts_with("return ")
        || trimmed.starts_with("return;")
        || trimmed.starts_with("throw ")
        || trimmed.starts_with("break;")
        || trimmed.starts_with("break ")
        || trimmed.starts_with("continue;")
        || trimmed.starts_with("continue ")
}

pub(crate) fn is_unreachable_after_terminal(java_logic_lines: &[String]) -> bool {
    let mut depth: i32 = 0;
    for line in java_logic_lines.iter().rev() {
        let t = line.trim_start();
        if t.starts_with("//") || t.is_empty() {
            continue;
        }
        if t.starts_with("} else") {
            return false;
        }
        let opens = t.chars().filter(|&c| c == '{').count() as i32;
        let closes = t.chars().filter(|&c| c == '}').count() as i32;
        depth += closes - opens; // reverse: } opens scope, { closes it
        if depth <= 0 {
            if t.starts_with("return") || t.starts_with("throw") || t.starts_with("currentState = ") {
                return true;
            }
            if t.starts_with("break") {
                return false;
            }
            if depth < 0 {
                break; // exited current block — unreachability doesn't cross blocks
            }
        }
    }
    false
}

fn resolve_var_type<'a>(proc: &'a ProcedureInfo, var_name: &str) -> (&'a str, bool) {
    if let Some(t) = proc.local_vars.get(&var_name.to_lowercase()) {
        return (t.as_str(), false);
    }
    for p in &proc.parameters {
        if p.name == var_name {
            return (p.java_type.as_str(), p.is_out());
        }
    }
    ("Object", false)
}

fn row_extraction_expr(row_var: &str, col_name: &str, declared_type: &str) -> (String, bool) {
    match declared_type {
        "Long" | "long" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).longValue() : 0L)", row_var, col_name, row_var, col_name), false),
        "Integer" | "int" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).intValue() : 0)", row_var, col_name, row_var, col_name), false),
        "String" => (format!("({}.get(\"{}\") instanceof String ? (String) {}.get(\"{}\") : {}.get(\"{}\") != null ? String.valueOf({}.get(\"{}\")) : null)", row_var, col_name, row_var, col_name, row_var, col_name, row_var, col_name), false),
        t if t.contains("BigDecimal") => (format!("({}.get(\"{}\") instanceof java.math.BigDecimal ? (java.math.BigDecimal) {}.get(\"{}\") : java.math.BigDecimal.ZERO)", row_var, col_name, row_var, col_name), false),
        "Double" | "double" | "Float" | "float" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).doubleValue() : 0.0)", row_var, col_name, row_var, col_name), false),
        t if t.contains("Timestamp") => (format!("({}.get(\"{}\") instanceof java.sql.Timestamp ? (java.sql.Timestamp) {}.get(\"{}\") : null)", row_var, col_name, row_var, col_name), false),
        t if t.contains("java.sql.Date") => (format!("({}.get(\"{}\") instanceof java.sql.Date ? (java.sql.Date) {}.get(\"{}\") : null)", row_var, col_name, row_var, col_name), false),
        t if t.contains("List<") || t.contains("ArrayList") => (format!("(java.util.List) {}.get(\"{}\")", row_var, col_name), false),
        t if t.contains("Map<String") => (format!("(Map<String, Object>) {}.get(\"{}\")", row_var, col_name), false),
        t if t.contains("AtomicReference") && t.contains("Integer") => (format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).intValue() : 0)", row_var, col_name, row_var, col_name), true),
        t if t.contains("AtomicReference") && t.contains("Long") => (format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).longValue() : 0L)", row_var, col_name, row_var, col_name), true),
        t if t.contains("AtomicReference") && t.contains("BigDecimal") => (format!(".set({}.get(\"{}\") instanceof java.math.BigDecimal ? (java.math.BigDecimal) {}.get(\"{}\") : java.math.BigDecimal.ZERO)", row_var, col_name, row_var, col_name), true),
        t if t.contains("AtomicReference") && t.contains("String") => (format!(".set({}.get(\"{}\") instanceof String ? (String) {}.get(\"{}\") : {}.get(\"{}\") != null ? String.valueOf({}.get(\"{}\")) : null)", row_var, col_name, row_var, col_name, row_var, col_name, row_var, col_name), true),
        "Object" => (String::new(), false),
        _ => (format!("{}.get(\"{}\")", row_var, col_name), false),
    }
}

fn is_control_structure_line(line: &str) -> bool {
    let t = line.trim_start();
    t.starts_with('}')
        || t.starts_with("if ")
        || t.starts_with("if(")
        || t.starts_with("} else")
        || t.starts_with("else ")
        || t.starts_with("else{")
        || t.starts_with("while ")
        || t.starts_with("while(")
        || t.starts_with("for ")
        || t.starts_with("for(")
        || t.starts_with("try")
        || t.starts_with("catch")
        || t.starts_with("switch ")
        || t.starts_with("switch(")
}

fn preprocess_cursor_sql(sql: &str, using_args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
    let mut result = sql.to_string();

    let positional_re = regex::Regex::new(r":\s*(\d+)").unwrap();
    if !positional_re.is_match(&result) {
        return result;
    }

    let arg_names: Vec<String> = if !using_args.is_empty() {
        using_args
            .iter()
            .map(|a| match a {
                ogsql_parser::ast::Expr::ColumnRef(name) | ogsql_parser::ast::Expr::PlVariable(name)
                    if name.len() == 1 =>
                {
                    ident_string(&name[0])
                }
                _ => crate::expr::expr_to_java(a, proc),
            })
            .collect()
    } else {
        proc.parameters.iter().filter(|p| !p.is_out()).map(|p| p.name.clone()).collect()
    };

    if arg_names.is_empty() {
        return result;
    }

    // Strip USING clause BEFORE positional param replacement, so byte offsets stay valid
    if let Some(using_pos) = result.to_lowercase().rfind(" using ") {
        result = result[..using_pos].to_string();
    }

    result = positional_re
        .replace_all(&result, |caps: &regex::Captures| {
            let idx: usize = caps[1].parse().unwrap_or(1);
            if idx >= 1 && idx <= arg_names.len() {
                let java_name = crate::naming::snake_to_camel(&arg_names[idx - 1]);
                format!("#{{{}}}", java_name)
            } else {
                format!(":{}", idx)
            }
        })
        .to_string();

    result
}

fn push_logic_line(proc: &mut ProcedureInfo, line: String) {
    let _trimmed_line = line.trim_start();
    if is_control_structure_line(&line) {
        // Closing braces must always be emitted; unreachable check only for openers
        if !_trimmed_line.starts_with('}') && is_unreachable_after_terminal(&proc.java_logic_lines) {
            proc.java_logic_lines.push(format!("// UNREACHABLE: {}", line));
        } else {
            proc.java_logic_lines.push(line);
        }
        return;
    }
    if is_unreachable_after_terminal(&proc.java_logic_lines) {
        let is_for_query_setup = line.starts_with("List<Map<String, Object>>") && line.contains("List = mapper.select");
        let is_null_guard = line.starts_with("if (") && line.contains("List == null)");
        let is_for_loop = line.starts_with("for (Map<String, Object>");
        if is_for_query_setup || is_null_guard || is_for_loop {
            proc.java_logic_lines.push(line);
            return;
        }
        proc.java_logic_lines.push(format!("// UNREACHABLE: {}", line));
        return;
    }
    // If the previous line was already marked UNREACHABLE, this line is also unreachable
    if let Some(last) = proc.java_logic_lines.last() {
        if last.trim_start().starts_with("// UNREACHABLE:") {
            proc.java_logic_lines.push(format!("// UNREACHABLE: {}", line));
            return;
        }
    }
    // Defensive: if this return follows another return at the same scope level
    // (handles edge cases where is_unreachable_after_terminal may miss)
    if _trimmed_line.starts_with("return ") || _trimmed_line == "return;" {
        let mut depth: i32 = 0;
        for prev in proc.java_logic_lines.iter().rev() {
            let pt = prev.trim_start();
            if pt.starts_with("//") || pt.is_empty() {
                continue;
            }
            depth += pt.chars().filter(|&c| c == '}').count() as i32;
            depth -= pt.chars().filter(|&c| c == '{').count() as i32;
            if depth < 0 {
                break;
            }
            if depth == 0 {
                if pt.starts_with("return") || pt.starts_with("throw") {
                    proc.java_logic_lines.push(format!("// UNREACHABLE: {}", line));
                    return;
                }
                break;
            }
        }
    }
    proc.java_logic_lines.push(line);
}

fn strip_out_param_get(java: &str, proc: &ProcedureInfo) -> String {
    if let Some(base) = java.strip_suffix(".get()") {
        let camel = crate::naming::snake_to_camel;
        for p in &proc.parameters {
            if p.is_out() && camel(&p.name) == base {
                return base.to_string();
            }
        }
    }
    java.to_string()
}

fn dml_method_name(
    dml_type: &str,
    proc_name: &str,
    counter: &mut HashMap<String, usize>,
    semantic_key: Option<&str>,
) -> String {
    if let Some(sk) = semantic_key {
        let key = format!("{}_{}", dml_type, sk);
        let n = counter.entry(key).or_insert(0);
        let suffix = if *n > 0 { format!("_{}", n) } else { String::new() };
        *n += 1;
        format!("{}{}{}", dml_type, snake_to_pascal(sk), suffix)
    } else {
        let key = format!("{}_{}", dml_type, proc_name);
        let n = counter.entry(key).or_insert(0);
        let suffix = if *n > 0 { format!("_{}", n) } else { String::new() };
        *n += 1;
        format!("{}{}{}", dml_type, snake_to_pascal(proc_name), suffix)
    }
}

fn extract_dml_target_table(stmt: &ogsql_parser::ast::Statement) -> Option<String> {
    use ogsql_parser::ast::Statement;

    match stmt {
        Statement::Select(s) => extract_first_table_name(&s.node.from),
        Statement::Insert(s) => s.node.table.last().map(ident_string),
        Statement::Update(s) => extract_first_table_name(&s.node.tables),
        Statement::Delete(s) => extract_first_table_name(&s.node.tables),
        _ => None,
    }
}

fn extract_first_table_name(tables: &[ogsql_parser::ast::TableRef]) -> Option<String> {
    use ogsql_parser::ast::TableRef;
    tables.first().and_then(|t| match t {
        TableRef::Table { name, .. } => name.last().map(ident_string),
        _ => None,
    })
}

fn build_mapper_call_args(proc: &ProcedureInfo) -> String {
    let mut parts: Vec<String> = Vec::new();
    for p in &proc.parameters {
        if p.is_out() {
            continue;
        }
        let jn = snake_to_camel(&p.name);
        parts.push(jn);
    }
    parts.join(", ")
}

fn detect_dml_type(sql: &str) -> Option<DmlType> {
    let upper = sql.trim_start().to_uppercase();
    if upper.starts_with("SELECT") {
        Some(DmlType::Select)
    } else if upper.starts_with("INSERT") {
        Some(DmlType::Insert)
    } else if upper.starts_with("UPDATE") {
        Some(DmlType::Update)
    } else if upper.starts_with("DELETE") {
        Some(DmlType::Delete)
    } else {
        None
    }
}

fn select_capture_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)^select\s+(.+)$").unwrap())
}

fn try_parse_inline_var_decl(sql: &str, _proc: &ProcedureInfo) -> Option<(String, String, Option<String>)> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(
        r"(?i)^(\w+)\s+(number|numeric|integer|int|bigint|varchar2?|text|decimal|date|timestamp|boolean)\s*(?:\([^)]*\))?\s*(?::=\s*(.+?))?\s*;?\s*$"
    ).unwrap());
    let caps = re.captures(sql)?;
    let var_name = caps.get(1)?.as_str().to_string();
    let sql_type = caps.get(2)?.as_str();
    let default_expr = caps.get(3).map(|m| {
        let d = m.as_str().trim();
        if d.starts_with('\'') && d.ends_with('\'') {
            format!("\"{}\"", &d[1..d.len() - 1])
        } else if d.parse::<i64>().is_ok() || d.parse::<f64>().is_ok() {
            d.to_string()
        } else {
            crate::naming::snake_to_camel(d)
        }
    });
    let java_type =
        crate::type_map::sql_type_to_java(sql_type).map(|s| s.to_string()).unwrap_or_else(|| "Object".to_string());
    Some((var_name, java_type, default_expr))
}

fn clean_sql_for_mapper(sql: &str, dml_type: DmlType) -> String {
    let mut s = sql.to_string();
    if matches!(dml_type, DmlType::Select) {
        let sql_lower = s.to_lowercase();
        // Use simple string search instead of regex for "into" keyword
        let into_pos = {
            let mut pos = None;
            let mut search_start = 0;
            while let Some(found) = sql_lower[search_start..].find("into") {
                let abs_pos = search_start + found;
                // Word boundary check: char before "into" must not be alphanumeric
                if abs_pos > 0 && sql_lower.as_bytes()[abs_pos - 1].is_ascii_alphanumeric() {
                    search_start = abs_pos + 4;
                    continue;
                }
                let after = &sql_lower[abs_pos + 4..];
                if after.trim_start().starts_with("values") {
                    search_start = abs_pos + 4;
                    continue;
                }
                pos = Some(abs_pos);
                break;
            }
            pos
        };

        if let Some(ipos) = into_pos {
            if let Some(from_pos) = sql_lower[ipos..].find("from").map(|p| ipos + p) {
                let into_text = s[ipos + 4..from_pos].trim();
                let before_into = &s[..ipos];
                let from_and_rest = &s[from_pos..];

                let into_fields: Vec<&str> = into_text
                    .split(',')
                    .map(|f| f.trim())
                    .map(|f| {
                        let dot_pos = f.find('.');
                        if let Some(dp) = dot_pos {
                            f[dp + 1..].trim()
                        } else {
                            f.trim()
                        }
                    })
                    .collect();

                if let Some(sel_caps) = select_capture_regex().captures(before_into) {
                    let select_list = sel_caps.get(1).unwrap().as_str();
                    let columns: Vec<&str> = select_list.split(',').map(|c| c.trim()).collect();

                    let into_raw_fields: Vec<&str> = into_text.split(',').map(|f| f.trim()).collect();
                    let has_record_field = into_raw_fields.iter().any(|f| f.contains('.'));

                    if columns.len() == 1 && into_fields.len() == 1 && !has_record_field {
                        s = format!("select {} {}", select_list, from_and_rest);
                        return s;
                    }

                    if columns.len() == into_fields.len() {
                        let needs_alias = columns.iter().zip(into_fields.iter()).any(|(c, f)| c != f);
                        if needs_alias {
                            let aliased: Vec<String> = columns
                                .iter()
                                .zip(into_fields.iter())
                                .map(
                                    |(col, field)| {
                                        if col == field {
                                            col.to_string()
                                        } else {
                                            format!("{} AS {}", col, field)
                                        }
                                    },
                                )
                                .collect();
                            s = format!("select {} {}", aliased.join(" , "), from_and_rest);
                            return s;
                        }
                    }
                }
            }
        }

        s = into_regex().replace(&s, "").to_string();
    }
    s
}

fn extract_into_var_from_text(sql: &str) -> Option<String> {
    let re = regex::Regex::new(r"(?i)\bINTO\s+(\w+)").ok()?;
    let caps = re.captures(sql)?;
    let var_name = caps.get(1)?.as_str().to_string();
    let remaining = &sql[caps.get(0).unwrap().end()..];
    let re2 = regex::Regex::new(r"(?i)^\s*,\s*\w+").ok()?;
    if re2.is_match(remaining) {
        return None;
    }
    Some(var_name)
}

fn extract_into_var_count_from_text(sql: &str) -> usize {
    match capture_into_regex().captures(sql) {
        Some(caps) => caps.get(1).map(|m| m.as_str().split(',').count()).unwrap_or(0),
        None => 0,
    }
}

fn resolve_package_name(pkg_hint: &str, summaries: &HashMap<String, crate::types::PackageSummary>) -> Option<String> {
    if summaries.contains_key(pkg_hint) {
        return Some(pkg_hint.to_string());
    }
    for key in summaries.keys() {
        if key.to_lowercase() == pkg_hint.to_lowercase() {
            return Some(key.clone());
        }
    }
    None
}

fn extract_table_ref(table_ref: &ogsql_parser::ast::TableRef, proc: &mut ProcedureInfo) {
    use ogsql_parser::ast::TableRef;
    if let TableRef::Table { name, .. } = table_ref {
        let table_name = name.last().map(ident_string).unwrap_or_default();
        if !table_name.is_empty() {
            proc.table_refs.insert(table_name);
        }
    }
}

fn next_select_var_name(proc: &mut ProcedureInfo) -> String {
    proc.select_counter += 1;
    if proc.select_counter == 1 {
        "_row".to_string()
    } else {
        format!("_row{}", proc.select_counter)
    }
}

fn next_result_var_name(proc: &mut ProcedureInfo) -> String {
    proc.select_counter += 1;
    if proc.select_counter == 1 {
        "_result".to_string()
    } else {
        format!("_result{}", proc.select_counter)
    }
}

fn extract_var_name_from_expr(expr: &ogsql_parser::ast::Expr) -> Option<String> {
    match expr {
        ogsql_parser::ast::Expr::ColumnRef(name) | ogsql_parser::ast::Expr::PlVariable(name) if name.len() == 1 => {
            Some(ident_string(&name[0]))
        }
        _ => None,
    }
}

/// Convert a USING arg expression to a SQL literal if it's a built-in function.
/// Returns Some(sql_text) for known SQL functions, None otherwise.
fn expr_to_sql_literal(expr: &ogsql_parser::ast::Expr) -> Option<String> {
    use ogsql_parser::ast::Expr;
    match expr {
        Expr::ColumnRef(name) if name.len() == 1 => match name[0].to_lowercase().as_str() {
            "systimestamp" | "sysdate" | "current_timestamp" | "localtimestamp" => {
                Some("CURRENT_TIMESTAMP".to_string())
            }
            "current_user" | "user" => Some("CURRENT_USER".to_string()),
            "current_date" => Some("CURRENT_DATE".to_string()),
            "current_time" => Some("CURRENT_TIME".to_string()),
            _ => None,
        },
        Expr::FunctionCall { name, args, .. } if args.is_empty() => {
            let func_name = name.first()?;
            match func_name.to_lowercase().as_str() {
                "systimestamp" | "sysdate" | "current_timestamp" | "localtimestamp" => {
                    Some("CURRENT_TIMESTAMP".to_string())
                }
                "current_user" | "user" => Some("CURRENT_USER".to_string()),
                "current_date" => Some("CURRENT_DATE".to_string()),
                "current_time" => Some("CURRENT_TIME".to_string()),
                _ => None,
            }
        }
        _ => None,
    }
}

/// Extract (parent_var, field_name) from dotted expressions like `v_result.emp_id`.
/// Returns None for non-dotted or unsupported expressions.
fn extract_dotted_ref_from_expr(expr: &ogsql_parser::ast::Expr) -> Option<(String, String)> {
    match expr {
        ogsql_parser::ast::Expr::ColumnRef(name) | ogsql_parser::ast::Expr::PlVariable(name) if name.len() == 2 => {
            Some((ident_string(&name[0]), ident_string(&name[1])))
        }
        _ => None,
    }
}

fn extract_assignment_target_name(target: &ogsql_parser::ast::Expr) -> Option<String> {
    use ogsql_parser::ast::Expr;
    match target {
        Expr::ColumnRef(name) | Expr::PlVariable(name) if name.len() == 1 => Some(ident_string(&name[0])),
        _ => None,
    }
}

/// Flatten a BinaryOp(||) concatenation tree into a SQL template with placeholders.
/// Returns (sql_template, [(java_name, is_identifier), ...]) or None if the expression
/// is not a recognizable SQL concatenation pattern.
fn flatten_concat(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> Option<(String, Vec<(String, bool)>)> {
    let mut parts: Vec<String> = Vec::new();
    let mut params: Vec<(String, bool)> = Vec::new();
    flatten_concat_inner(expr, proc, &mut parts, &mut params);
    if parts.is_empty() {
        return None;
    }
    let sql_template = parts.join("");
    let first_word = sql_template.split_whitespace().next()?.to_lowercase();
    let sql_verbs = [
        "select",
        "insert",
        "update",
        "delete",
        "truncate",
        "alter",
        "drop",
        "create",
        "merge",
        "savepoint",
        "rollback",
    ];
    if !sql_verbs.contains(&first_word.as_str()) {
        return None;
    }
    Some((sql_template, params))
}

fn detect_sql_concat_append(
    target_name: &str,
    expression: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
) -> Option<(String, String, String)> {
    use ogsql_parser::ast::Expr;
    if let Expr::BinaryOp { left, op, .. } = expression {
        if op != "||" {
            return None;
        }
        let left_var = extract_leftmost_var_from_concat(left);
        if left_var != target_name {
            return None;
        }
        let mut parts: Vec<String> = Vec::new();
        let mut _params: Vec<(String, bool)> = Vec::new();
        flatten_suffix_after_var(expression, target_name, proc, &mut parts, &mut _params);
        if parts.is_empty() {
            return None;
        }
        let sql_fragment = parts.join("").trim().to_string();
        if sql_fragment.is_empty() {
            return None;
        }
        let clause_type = classify_clause_type(&sql_fragment);
        Some((target_name.to_string(), sql_fragment, clause_type))
    } else {
        None
    }
}

fn extract_leftmost_var_from_concat(expr: &ogsql_parser::ast::Expr) -> String {
    use ogsql_parser::ast::Expr;
    match expr {
        Expr::PlVariable(name) | Expr::ColumnRef(name) if !name.is_empty() => ident_string(&name[name.len() - 1]),
        Expr::BinaryOp { left, op, .. } if op == "||" => extract_leftmost_var_from_concat(left),
        _ => String::new(),
    }
}

fn flatten_suffix_after_var(
    expr: &ogsql_parser::ast::Expr,
    var_name: &str,
    proc: &ProcedureInfo,
    parts: &mut Vec<String>,
    params: &mut Vec<(String, bool)>,
) -> bool {
    use ogsql_parser::ast::Expr;
    if let Expr::BinaryOp { left, op, right } = expr {
        if op != "||" {
            return false;
        }
        match left.as_ref() {
            Expr::PlVariable(name) | Expr::ColumnRef(name) if !name.is_empty() && name[name.len() - 1] == var_name => {
                flatten_concat_inner(right, proc, parts, params);
                return true;
            }
            _ => {}
        }
        if flatten_suffix_after_var(left, var_name, proc, parts, params) {
            flatten_concat_inner(right, proc, parts, params);
            return true;
        }
    }
    false
}

fn classify_clause_type(sql_template: &str) -> String {
    let trimmed = sql_template.trim_start();
    let first_word = trimmed.split_whitespace().next().unwrap_or("").to_lowercase();
    match first_word.as_str() {
        "where" => "WHERE".to_string(),
        "order" => "ORDER_BY".to_string(),
        "set" => "SET".to_string(),
        "and" => "AND".to_string(),
        "or" => "OR".to_string(),
        "group" => "GROUP_BY".to_string(),
        "having" => "HAVING".to_string(),
        "limit" => "LIMIT".to_string(),
        "offset" => "OFFSET".to_string(),
        "for" => "FOR_UPDATE".to_string(),
        "join" | "inner" | "left" | "right" | "full" | "cross" => "JOIN".to_string(),
        _ => "OTHER".to_string(),
    }
}

/// Strip "IMMEDIATE" prefix from FOR-IN-EXECUTE query strings.
/// The ogsql-parser emits "IMMEDIATE v_sql" for `FOR rec IN EXECUTE IMMEDIATE v_sql`.
fn strip_execute_prefix(query: &str) -> &str {
    let trimmed = query.trim();
    let lower = trimmed.to_lowercase();
    if lower.starts_with("execute immediate ") {
        return trimmed["execute immediate ".len()..].trim_start();
    }
    if lower.starts_with("immediate ") {
        return trimmed["immediate ".len()..].trim_start();
    }
    trimmed
}

/// Check if a query string is a PL/pgSQL variable reference rather than actual SQL.
fn is_variable_reference(query: &str) -> bool {
    let trimmed = query.trim();
    if trimmed.is_empty() {
        return false;
    }
    let upper = trimmed.to_uppercase();
    let sql_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "MERGE", "TRUNCATE", "EXECUTE", "IMMEDIATE"];
    for kw in &sql_keywords {
        if upper.starts_with(kw) {
            return false;
        }
    }
    !trimmed.contains(' ') && trimmed.chars().all(|c| c.is_alphanumeric() || c == '_')
}

/// Try to resolve the base SQL text for a dynamic SQL variable.
/// Checks `dynamic_sql_templates` for the variable name.
fn resolve_dynamic_sql_text(proc: &ProcedureInfo, var_name: &str) -> Option<String> {
    if let Some((sql_template, _concat_vars)) = proc.dynamic_sql_templates.get(var_name) {
        return Some(sql_template.clone());
    }
    None
}

/// Collect dynamic conditions from `sql_concat_chain` for a variable.
fn collect_dynamic_conditions(proc: &ProcedureInfo, var_name: &str) -> Vec<crate::types::DynamicCondition> {
    let mut conditions = Vec::new();
    if let Some(chain) = proc.sql_concat_chain.get(var_name) {
        for (condition_expr, sql_fragment, clause_type) in chain {
            conditions.push(crate::types::DynamicCondition {
                condition_expr: condition_expr.clone(),
                sql_fragment: sql_fragment.clone(),
                clause_type: clause_type.clone(),
                tag_name: String::new(),
            });
        }
    }
    conditions
}

/// Build extra_params from the dynamic SQL template's concat variables.
fn collect_extra_params_from_template(proc: &ProcedureInfo, var_name: &str) -> Vec<(String, String)> {
    let mut extra_params = Vec::new();
    if let Some((_, concat_vars)) = proc.dynamic_sql_templates.get(var_name) {
        for (java_name, _is_ident) in concat_vars {
            if java_name.starts_with("expr_") {
                continue;
            }
            let (var_type, _) = resolve_var_type(proc, java_name);
            if !extra_params.iter().any(|(n, _)| n == java_name) {
                extra_params.push((java_name.clone(), var_type.to_string()));
            }
        }
    }
    extra_params
}

fn flatten_concat_inner(
    expr: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
    parts: &mut Vec<String>,
    params: &mut Vec<(String, bool)>,
) {
    use ogsql_parser::ast::{Expr, Literal};
    match expr {
        Expr::BinaryOp { left, op, right } if op == "||" => {
            flatten_concat_inner(left, proc, parts, params);
            flatten_concat_inner(right, proc, parts, params);
        }
        Expr::Literal(Literal::String(s)) => {
            parts.push(s.clone());
        }
        Expr::Literal(Literal::Integer(n)) => {
            parts.push(n.to_string());
        }
        Expr::Literal(Literal::Float(f)) => {
            parts.push(f.clone());
        }
        Expr::PlVariable(name) | Expr::ColumnRef(name) if !name.is_empty() => {
            let var_name = ident_string(&name[name.len() - 1]);
            let java_name = crate::naming::snake_to_camel(&var_name);
            // Determine if this is an identifier context (table/column name → ${})
            // Heuristic: if trailing text ends with a quote, it's a value → #{}
            let is_identifier = parts.last().map(|last| !last.trim_end().ends_with('\'')).unwrap_or(true);
            if is_identifier {
                parts.push(format!("${{{}}}", java_name));
            } else {
                parts.push(format!("#{{{}}}", java_name));
            }
            params.push((java_name, is_identifier));
        }
        _ => {
            // For any other expression, convert to Java and treat as a value param
            let _java_expr = crate::expr::expr_to_java(expr, proc);
            let safe_name = format!("expr_{}", params.len());
            parts.push(format!("#{{{}}}", safe_name));
            params.push((safe_name, false));
        }
    }
}

fn handle_resolved_execute_sql(
    proc: &mut ProcedureInfo,
    sql_template: &str,
    concat_vars: &[(String, bool)],
    execute: &ogsql_parser::ast::plpgsql::PlExecuteStmt,
    ctx: &mut StatementContext,
    var_name: &str,
) {
    let mut clean_sql = sql_template.to_string();
    let dml_type = detect_dml_type(&clean_sql);
    if let Some(dml_type) = dml_type {
        for (i, arg) in execute.using_args.iter().enumerate() {
            let pos = i + 1;
            let pos_re = regex::Regex::new(&format!(r"(?::|\$){}(\b|[^0-9])", pos)).unwrap();
            if let Some(sql_literal) = expr_to_sql_literal(&arg.argument) {
                clean_sql = pos_re
                    .replace_all(&clean_sql, |caps: &regex::Captures| format!("{}{}", sql_literal, &caps[1]))
                    .to_string();
            } else if let Some(arg_name) = extract_var_name_from_expr(&arg.argument) {
                let java_name = snake_to_camel(&arg_name);
                clean_sql = pos_re
                    .replace_all(&clean_sql, |caps: &regex::Captures| format!("#{{{}}}{}", java_name, &caps[1]))
                    .to_string();
            }
        }

        let semantic_key = execute.parsed_query.as_ref().and_then(|q| extract_dml_target_table(q));
        let method_id = dml_method_name(
            match dml_type {
                DmlType::Select => "select",
                DmlType::Insert => "insert",
                DmlType::Update => "update",
                DmlType::Delete => "delete",
            },
            &proc.proc_name,
            &mut ctx.dml_counter,
            semantic_key.as_deref(),
        );

        let mut extra_params: Vec<(String, String)> = Vec::new();
        for (java_name, _is_ident) in concat_vars {
            if java_name.starts_with("expr_") {
                continue;
            }
            let (var_type, _is_out) = resolve_var_type(proc, java_name);
            if !extra_params.iter().any(|(n, _)| n == java_name) {
                extra_params.push((java_name.clone(), var_type.to_string()));
            }
        }
        for arg in &execute.using_args {
            if expr_to_sql_literal(&arg.argument).is_some() {
                continue;
            }
            if let Some(arg_name) = extract_var_name_from_expr(&arg.argument) {
                let java_name = snake_to_camel(&arg_name);
                if !extra_params.iter().any(|(n, _)| n == &java_name) {
                    let (var_type, _is_out) = resolve_var_type(proc, &arg_name);
                    extra_params.push((java_name, var_type.to_string()));
                }
            }
        }

        let dynamic_conditions = collect_dynamic_conditions(proc, var_name);
        let base_sql = resolve_dynamic_sql_text(proc, var_name).unwrap_or_default();

        proc.dml_statements.push(DmlStatement {
            sql_type: dml_type,
            method_id: method_id.clone(),
            sql_text: clean_sql.clone(),
            result_type: None,
            returns_list: false,
            extra_params,
            dynamic_conditions,
            base_sql,
            ..Default::default()
        });
        push_logic_line(proc, format!("mapper.{}({});", method_id, build_mapper_call_args(proc)));
    } else {
        push_logic_line(proc, format!("// SQL: {}", clean_sql.replace('\n', " ")));
    }
}

fn process_execute_stmt(
    execute: &ogsql_parser::ast::plpgsql::PlExecuteStmt,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    use ogsql_parser::ast::Expr;

    if let Some(parsed_query) = &execute.parsed_query {
        let formatter = ogsql_parser::SqlFormatter::new();
        let sql_text = formatter.format_statement(parsed_query);
        let upper_sql = sql_text.trim_start().to_uppercase();

        if upper_sql.starts_with("SAVEPOINT") {
            let sp_name = sql_text.trim()["SAVEPOINT".len()..].trim();
            push_logic_line(
                proc,
                format!(
                    "// SAVEPOINT {} — handled via JDBC Connection.setSavepoint() in @Transactional context",
                    sp_name
                ),
            );
            return;
        }
        if upper_sql.starts_with("ROLLBACK TO SAVEPOINT") {
            let sp_name = sql_text.trim()["ROLLBACK TO SAVEPOINT".len()..].trim();
            push_logic_line(proc, format!("// ROLLBACK TO SAVEPOINT {} — handled via JDBC Connection.rollback(Savepoint) in @Transactional context", sp_name));
            return;
        }
        if upper_sql.starts_with("RELEASE SAVEPOINT") {
            let sp_name = sql_text.trim()["RELEASE SAVEPOINT".len()..].trim();
            push_logic_line(
                proc,
                format!("// RELEASE SAVEPOINT {} — not needed in Spring @Transactional context", sp_name),
            );
            return;
        }

        let dml_type = detect_dml_type(&sql_text);

        if dml_type == Some(DmlType::Select) && execute.into_targets.is_empty() {
            push_logic_line(proc, format!("// PERFORM: {};", sql_text.replace('\n', " ")));
            ctx.unresolved_calls.push(UnresolvedCall {
                caller: format!("{}.{}", proc.package, proc.proc_name),
                callee: format!("PERFORM {}", sql_text.replace('\n', " ")),
                caller_file: proc.source_file.clone(),
                args: String::new(),
                hint: "add the defining SQL file to fluxgauss.yaml sources".to_string(),
            });
            return;
        }

        let mut clean_sql = sql_text.clone();
        for p in &proc.parameters {
            let jn = snake_to_camel(&p.name);
            let jdbc = sql_type_to_jdbc(&p.sql_type);
            let java = &p.java_type;
            let placeholder = match (jdbc, java) {
                (Some(_j), jt) if !jt.is_empty() => format!("#{{{}}}", jn),
                _ => format!("#{{{}}}", jn),
            };
            let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
            clean_sql = re.replace_all(&clean_sql, placeholder.as_str()).to_string();
        }
        for (var_name, var_java_type) in &proc.local_vars {
            let jn = snake_to_camel(var_name);
            let is_map = var_java_type.contains("Map<");
            if is_map {
                let dotted_re = regex::Regex::new(&format!(r"(?i)\b{}\s*\.\s*(\w+)", regex::escape(var_name))).unwrap();
                clean_sql = dotted_re
                    .replace_all(&clean_sql, |caps: &regex::Captures| {
                        let field = &caps[1];
                        let field_camel = snake_to_camel(field);
                        format!("#{{{}.{}}}", jn, field_camel)
                    })
                    .to_string();
                let bare_re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(var_name))).unwrap();
                clean_sql = bare_re.replace_all(&clean_sql, format!("#{{{}}}", jn).as_str()).to_string();
            } else {
                let jdbc = java_type_to_jdbc(var_java_type);
                let placeholder = if !jdbc.is_empty() && !var_java_type.is_empty() {
                    format!("#{{{}, jdbcType={}, javaType={}}}", jn, jdbc, var_java_type)
                } else {
                    format!("#{{{}}}", jn)
                };
                let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(var_name))).unwrap();
                clean_sql = re.replace_all(&clean_sql, placeholder.as_str()).to_string();
            }
        }
        clean_sql = cast_regex().replace_all(&clean_sql, "").to_string();

        for (i, arg) in execute.using_args.iter().enumerate() {
            let pos = i + 1;
            if let Some(arg_name) = extract_var_name_from_expr(&arg.argument) {
                let java_name = snake_to_camel(&arg_name);
                let mut jdbc = None;
                let mut java = None;
                for p in &proc.parameters {
                    if p.name.eq_ignore_ascii_case(&arg_name) {
                        jdbc = sql_type_to_jdbc(&p.sql_type);
                        java = Some(p.java_type.clone());
                        break;
                    }
                }
                if jdbc.is_none() {
                    if let Some(var_java_type) = proc.local_vars.get(&arg_name.to_lowercase()) {
                        java = Some(var_java_type.clone());
                        jdbc = Some(java_type_to_jdbc(var_java_type));
                    }
                }
                let placeholder = if let (Some(j), Some(jt)) = (jdbc, java) {
                    format!("#{{{}, jdbcType={}, javaType={}}}", java_name, j, jt)
                } else {
                    format!("#{{{}}}", java_name)
                };
                let re = regex::Regex::new(&format!(r"\${}(\D|$)", pos)).unwrap();
                clean_sql = re
                    .replace_all(&clean_sql, |caps: &regex::Captures| format!("{}{}", placeholder, &caps[1]))
                    .to_string();
            }
        }

        for arg in &execute.using_args {
            if let Some(arg_name) = extract_var_name_from_expr(&arg.argument) {
                let java_name = snake_to_camel(&arg_name);
                let mut jdbc = None;
                let mut java = None;
                for p in &proc.parameters {
                    if p.name.eq_ignore_ascii_case(&arg_name) {
                        jdbc = sql_type_to_jdbc(&p.sql_type);
                        java = Some(p.java_type.clone());
                        break;
                    }
                }
                if jdbc.is_none() {
                    if let Some(var_java_type) = proc.local_vars.get(&arg_name.to_lowercase()) {
                        java = Some(var_java_type.clone());
                        jdbc = Some(java_type_to_jdbc(var_java_type));
                    }
                }
                let placeholder = if let (Some(j), Some(jt)) = (jdbc, java) {
                    format!("#{{{}, jdbcType={}, javaType={}}}", java_name, j, jt)
                } else {
                    format!("#{{{}}}", java_name)
                };
                let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&arg_name))).unwrap();
                clean_sql = re.replace_all(&clean_sql, placeholder.as_str()).to_string();
            }
        }

        let dml_type = dml_type.unwrap_or(DmlType::Select);
        let semantic_key = execute.parsed_query.as_ref().and_then(|q| extract_dml_target_table(q));
        let method_id = dml_method_name(
            match dml_type {
                DmlType::Select => "select",
                DmlType::Insert => "insert",
                DmlType::Update => "update",
                DmlType::Delete => "delete",
            },
            &proc.proc_name,
            &mut ctx.dml_counter,
            semantic_key.as_deref(),
        );
        let args = build_mapper_call_args(proc);

        if !execute.into_targets.is_empty() {
            let var_names: Vec<String> = execute.into_targets.iter().filter_map(extract_var_name_from_expr).collect();
            let is_out_param = |var_name: &str, proc: &ProcedureInfo| -> bool {
                proc.parameters.iter().any(|p| p.name == var_name && p.is_out())
            };

            if var_names.len() == 1 && execute.into_targets.len() == 1 {
                let var_name = &var_names[0];
                let var_java = snake_to_camel(var_name);
                if is_out_param(var_name, proc) {
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: Some("Object".to_string()),
                        ..Default::default()
                    });
                    push_logic_line(proc, out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc));
                } else {
                    let declared_type =
                        proc.local_vars.get(&var_name.to_lowercase()).cloned().unwrap_or_else(|| "Object".to_string());
                    let java_type = infer_select_result_type(&declared_type, &clean_sql);
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: Some(java_type.clone()),
                        ..Default::default()
                    });
                    let original_java_type = proc.local_vars.get(&var_name.to_lowercase()).cloned().unwrap_or_default();
                    push_logic_line(
                        proc,
                        if java_type.contains("Map") {
                            format!(
                                "{{ var _row = mapper.{}({}); if (_row != null) {{ {} = _row; }} }}",
                                method_id, args, var_java
                            )
                        } else if matches!(dml_type, DmlType::Select)
                            && matches!(declared_type.as_str(), "int" | "Integer" | "long" | "Long")
                        {
                            format!(
                                "{{ var _row = mapper.{}({}); {} = (_row != null ? 1 : 0); }}",
                                method_id, args, var_java
                            )
                        } else if java_type != original_java_type {
                            format!("String _{} = mapper.{}({});", var_name, method_id, args)
                        } else {
                            format!("{} = mapper.{}({});", var_java, method_id, args)
                        },
                    );
                }
            } else {
                let var_name = next_select_var_name(proc);
                proc.dml_statements.push(DmlStatement {
                    sql_type: dml_type,
                    method_id: method_id.clone(),
                    sql_text: clean_sql,
                    result_type: Some("Map<String, Object>".to_string()),
                    ..Default::default()
                });
                push_logic_line(proc, format!("Map<String, Object> {} = mapper.{}({});", var_name, method_id, args));
                proc.imports.insert("import java.util.Map;".to_string());
                // Add null guard for row extraction
                if !execute.into_targets.is_empty() {
                    push_logic_line(proc, format!("if ({} != null) {{", var_name));
                }

                for target in &execute.into_targets {
                    if let Some(field_name) = extract_var_name_from_expr(target) {
                        let field_java = snake_to_camel(&field_name);
                        let field_type = proc
                            .local_vars
                            .get(&field_name.to_lowercase())
                            .cloned()
                            .unwrap_or_else(|| "Object".to_string());
                        let cast = if field_type != "Object" { format!("({}) ", field_type) } else { String::new() };
                        push_logic_line(proc, format!("{} = {}_row.get(\"{}\");", field_java, cast, field_name));
                    } else if let Some((parent, field)) = extract_dotted_ref_from_expr(target) {
                        let parent_java = snake_to_camel(&parent);
                        let field_java = snake_to_camel(&field);
                        let field_type = proc
                            .local_vars
                            .get(&parent.to_lowercase())
                            .cloned()
                            .unwrap_or_else(|| "Object".to_string());
                        let cast = if field_type != "Object" && field_type != "Map<String, Object>" {
                            format!("({}) ", field_type)
                        } else {
                            String::new()
                        };
                        push_logic_line(
                            proc,
                            format!(
                                "{}.put(\"{}\", {}{}.get(\"{}\"));",
                                parent_java, field_java, cast, var_name, field
                            ),
                        );
                    }
                }

                if !execute.into_targets.is_empty() {
                    push_logic_line(proc, "}".to_string());
                }
            }
        } else {
            let result_type =
                if matches!(dml_type, DmlType::Select) { Some("Map<String, Object>".to_string()) } else { None };
            proc.dml_statements.push(DmlStatement {
                sql_type: dml_type,
                method_id: method_id.clone(),
                sql_text: clean_sql,
                result_type,
                ..Default::default()
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        return;
    }

    match &execute.string_expr {
        Expr::Literal(ogsql_parser::ast::Literal::String(s)) => {
            let sql_text = s.clone();
            let dml_type = detect_dml_type(&sql_text);
            if let Some(dml_type) = dml_type {
                let method_id = dml_method_name(
                    match dml_type {
                        DmlType::Select => "select",
                        DmlType::Insert => "insert",
                        DmlType::Update => "update",
                        DmlType::Delete => "delete",
                    },
                    &proc.proc_name,
                    &mut ctx.dml_counter,
                    None,
                );
                let args = build_mapper_call_args(proc);
                let clean_sql = clean_sql_for_mapper(&sql_text, dml_type);
                let is_select = matches!(dml_type, DmlType::Select);

                if is_select {
                    let into_var = extract_into_var_from_text(&sql_text);
                    let into_vars_count = extract_into_var_count_from_text(&sql_text);

                    if let Some(var_name) = &into_var {
                        let is_out = proc.parameters.iter().any(|p| p.name == *var_name && p.is_out());
                        if is_out {
                            let var_java = snake_to_camel(var_name);
                            proc.dml_statements.push(DmlStatement {
                                sql_type: dml_type,
                                method_id: method_id.clone(),
                                sql_text: clean_sql,
                                result_type: Some("Object".to_string()),
                                ..Default::default()
                            });
                            push_logic_line(
                                proc,
                                out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc),
                            );
                        } else {
                            let var_java = snake_to_camel(var_name);
                            let declared_type = proc
                                .local_vars
                                .get(&var_name.to_lowercase())
                                .cloned()
                                .unwrap_or_else(|| "Object".to_string());
                            let java_type = infer_select_result_type(&declared_type, &clean_sql);
                            proc.dml_statements.push(DmlStatement {
                                sql_type: dml_type,
                                method_id: method_id.clone(),
                                sql_text: clean_sql,
                                result_type: Some(java_type.clone()),
                                ..Default::default()
                            });
                            push_logic_line(
                                proc,
                                if java_type != declared_type {
                                    format!("String _{} = mapper.{}({});", var_name, method_id, args)
                                } else {
                                    format!("{} = mapper.{}({});", var_java, method_id, args)
                                },
                            );
                        }
                    } else if into_vars_count > 1 {
                        let var_name = next_select_var_name(proc);
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            ..Default::default()
                        });
                        push_logic_line(
                            proc,
                            format!("Map<String, Object> {} = mapper.{}({});", var_name, method_id, args),
                        );
                        proc.imports.insert("import java.util.Map;".to_string());
                    } else {
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            ..Default::default()
                        });
                        let var_name = next_result_var_name(proc);
                        push_logic_line(
                            proc,
                            format!("List<Map<String, Object>> {} = mapper.{}({});", var_name, method_id, args),
                        );
                        proc.imports.insert("import java.util.List;".to_string());
                        proc.imports.insert("import java.util.Map;".to_string());
                    }
                } else {
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: None,
                        ..Default::default()
                    });
                    push_logic_line(proc, format!("mapper.{}({});", method_id, args));
                }
            } else {
                push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
            }
        }
        Expr::ColumnRef(name) | Expr::PlVariable(name) if name.len() == 1 => {
            let var_name = ident_string(&name[0]);
            if let Some((sql_template, concat_vars)) = proc.dynamic_sql_templates.get(&var_name).cloned() {
                handle_resolved_execute_sql(proc, &sql_template, &concat_vars, execute, ctx, &var_name);
            } else {
                push_logic_line(proc, format!("// TODO: EXECUTE {} — could not resolve SQL string", var_name));
            }
        }
        Expr::BinaryOp { op, .. } if op == "||" => {
            if let Some((sql_template, concat_vars)) = flatten_concat(&execute.string_expr, proc) {
                handle_resolved_execute_sql(proc, &sql_template, &concat_vars, execute, ctx, "");
                return;
            }
            push_logic_line(proc, "// TODO: EXECUTE dynamic SQL — could not resolve SQL string".to_string());
        }
        _ => {
            push_logic_line(proc, "// TODO: EXECUTE dynamic SQL — could not resolve SQL string".to_string());
        }
    }
}

fn process_sql_statement(
    statement: &ogsql_parser::ast::Statement,
    sql_text: &str,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    use ogsql_parser::ast::Expr;
    use ogsql_parser::ast::SelectTarget;
    use ogsql_parser::ast::Statement;
    match statement {
        Statement::Select(select_stmt) => {
            let into_targets = &select_stmt.node.into_targets;
            let semantic_key = extract_dml_target_table(statement);
            let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter, semantic_key.as_deref());
            let clean_sql = clean_sql_for_mapper(sql_text, DmlType::Select);
            let args = build_mapper_call_args(proc);

            if into_targets.as_ref().is_some_and(|t| !t.is_empty()) {
                let targets = into_targets.as_ref().unwrap();

                // Extract simple variable names from INTO targets
                let var_names: Vec<String> = targets
                    .iter()
                    .filter_map(|t| {
                        if let SelectTarget::Expr(expr, _) = t {
                            match expr {
                                Expr::ColumnRef(name) | Expr::PlVariable(name) if name.len() == 1 => {
                                    return Some(ident_string(&name[0]));
                                }
                                _ => {}
                            }
                        }
                        None
                    })
                    .collect();

                let is_out_param = |var_name: &str, proc: &ProcedureInfo| -> bool {
                    proc.parameters.iter().any(|p| p.name == var_name && p.is_out())
                };

                let (result_type, java_line, row_var_name) = if var_names.len() == 1 && targets.len() == 1 {
                    let var_name = &var_names[0];
                    let var_java = snake_to_camel(var_name);

                    if is_out_param(var_name, proc) {
                        let line = out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc);
                        ("Object".to_string(), line, String::new())
                    } else {
                        let declared_type = proc
                            .local_vars
                            .get(&var_name.to_lowercase())
                            .cloned()
                            .unwrap_or_else(|| "Object".to_string());
                        let rt = infer_select_result_type(&declared_type, &clean_sql);
                        let line = if declared_type.contains("Map") {
                            format!("{{ Object _tmp = mapper.{}({}); {} = (_tmp instanceof java.util.Map) ? (java.util.Map<String, Object>) _tmp : null; }}", method_id, args, var_java)
                        } else if rt != declared_type
                            && matches!(
                                declared_type.as_str(),
                                "Long"
                                    | "long"
                                    | "Integer"
                                    | "int"
                                    | "Double"
                                    | "double"
                                    | "Float"
                                    | "float"
                                    | "BigDecimal"
                            )
                        {
                            format!("{{ Object _strResult = mapper.{}({}); if (_strResult != null) {{ /* concatenated string assigned to {} var */ }} }}", method_id, args, declared_type)
                        } else if declared_type == "Long" || declared_type == "long" {
                            format!("{{ Object _val = mapper.{}({}); if (_val != null) {} = (_val instanceof Number ? ((Number)_val).longValue() : Long.parseLong(String.valueOf(_val))); }}", method_id, args, var_java)
                        } else if declared_type == "Integer" || declared_type == "int" {
                            format!("{{ Object _val = mapper.{}({}); if (_val != null) {} = (_val instanceof Number ? ((Number)_val).intValue() : Integer.parseInt(String.valueOf(_val))); }}", method_id, args, var_java)
                        } else if declared_type == "String" {
                            format!("{} = String.valueOf(mapper.{}({}));", var_java, method_id, args)
                        } else if declared_type != "Object" {
                            format!(
                                "{{ Object _val = mapper.{}({}); if (_val != null) {} = ({}) _val; }}",
                                method_id, args, var_java, declared_type
                            )
                        } else {
                            format!(
                                "{{ Object _val = mapper.{}({}); if (_val != null) {} = _val; }}",
                                method_id, args, var_java
                            )
                        };
                        (rt, line, String::new())
                    }
                } else {
                    // Multiple INTO targets or qualified names — use Map
                    let row_var = next_select_var_name(proc);
                    let line = format!("Object {}_obj = mapper.{}({}); Map<String, Object> {} = ({}_obj instanceof java.util.Map) ? (java.util.Map<String, Object>) {}_obj : null;", row_var, method_id, args, row_var, row_var, row_var);
                    proc.imports.insert("import java.util.Map;".to_string());
                    ("Map<String, Object>".to_string(), line, row_var)
                };

                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text: clean_sql,
                    result_type: Some(result_type),
                    ..Default::default()
                });
                push_logic_line(proc, java_line);

                // Extract simple variables from Map result (e.g. SELECT INTO v_product_id, v_qty)
                if !row_var_name.is_empty() && (!var_names.is_empty() || var_names.len() != targets.len()) {
                    push_logic_line(proc, format!("if ({} != null) {{", row_var_name));
                    for var_name in &var_names {
                        let var_java = snake_to_camel(var_name);
                        let (type_str, is_out_param) = resolve_var_type(proc, var_name);
                        let declared_type = type_str.to_string();
                        let (extraction, is_set_call) = if is_out_param {
                            let inner = match declared_type.as_str() {
                                "Long" | "long" => format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).longValue() : 0L)", row_var_name, var_name, row_var_name, var_name),
                                "Integer" | "int" => format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).intValue() : 0)", row_var_name, var_name, row_var_name, var_name),
                                "String" => format!(".set({}.get(\"{}\") instanceof String ? (String) {}.get(\"{}\") : {}.get(\"{}\") != null ? String.valueOf({}.get(\"{}\")) : null)", row_var_name, var_name, row_var_name, var_name, row_var_name, var_name, row_var_name, var_name),
                                t if t.contains("BigDecimal") => format!(".set({}.get(\"{}\") instanceof java.math.BigDecimal ? (java.math.BigDecimal) {}.get(\"{}\") : java.math.BigDecimal.ZERO)", row_var_name, var_name, row_var_name, var_name),
                                "Double" | "double" | "Float" | "float" => format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).doubleValue() : 0.0)", row_var_name, var_name, row_var_name, var_name),
                                _ => format!(".set({}.get(\"{}\"))", row_var_name, var_name),
                            };
                            (inner, true)
                        } else {
                            match declared_type.as_str() {
                                "Long" | "long" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).longValue() : 0L)", row_var_name, var_name, row_var_name, var_name), false),
                                 "Integer" | "int" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).intValue() : 0)", row_var_name, var_name, row_var_name, var_name), false),
                                 "String" => (format!("({}.get(\"{}\") instanceof String ? (String) {}.get(\"{}\") : {}.get(\"{}\") != null ? String.valueOf({}.get(\"{}\")) : null)", row_var_name, var_name, row_var_name, var_name, row_var_name, var_name, row_var_name, var_name), false),
                                t if t.contains("BigDecimal") => (format!("({}.get(\"{}\") instanceof java.math.BigDecimal ? (java.math.BigDecimal) {}.get(\"{}\") : java.math.BigDecimal.ZERO)", row_var_name, var_name, row_var_name, var_name), false),
                                "Double" | "double" | "Float" | "float" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).doubleValue() : 0.0)", row_var_name, var_name, row_var_name, var_name), false),
                                _ => row_extraction_expr(&row_var_name, var_name, &declared_type),
                            }
                        };
                        if is_set_call {
                            push_logic_line(proc, format!("    {}{};", var_java, extraction));
                        } else if !extraction.is_empty() {
                            push_logic_line(proc, format!("    {} = {};", var_java, extraction));
                        }
                    }

                    // Handle dotted INTO targets like v_result.emp_id → vResult.put("empId", _row.get("empId"))
                    if var_names.len() != targets.len() {
                        for target in targets {
                            if let SelectTarget::Expr(expr, _) = target {
                                if let Some((parent, field)) = extract_dotted_ref_from_expr(expr) {
                                    let parent_java = snake_to_camel(&parent);
                                    let field_java = snake_to_camel(&field);
                                    push_logic_line(
                                        proc,
                                        format!(
                                            "    {}.put(\"{}\", {}.get(\"{}\"));",
                                            parent_java, field_java, row_var_name, field
                                        ),
                                    );
                                }
                            }
                        }
                    }
                    push_logic_line(proc, "}".to_string());
                }
            } else {
                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text: sql_text.to_string(),
                    result_type: Some("Map<String, Object>".to_string()),
                    returns_list: true,
                    ..Default::default()
                });
                let var_name = next_result_var_name(proc);
                push_logic_line(
                    proc,
                    format!("List<Map<String, Object>> {} = mapper.{}({});", var_name, method_id, args),
                );
                proc.imports.insert("import java.util.List;".to_string());
                proc.imports.insert("import java.util.Map;".to_string());
            }
            for table_ref in &select_stmt.node.from {
                extract_table_ref(table_ref, proc);
            }
        }
        Statement::Insert(_insert_stmt) => {
            let semantic_key = extract_dml_target_table(statement);
            let method_id = dml_method_name("insert", &proc.proc_name, &mut ctx.dml_counter, semantic_key.as_deref());
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Insert,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                ..Default::default()
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        Statement::Update(_update_stmt) => {
            let semantic_key = extract_dml_target_table(statement);
            let method_id = dml_method_name("update", &proc.proc_name, &mut ctx.dml_counter, semantic_key.as_deref());
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Update,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                ..Default::default()
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        Statement::Delete(_delete_stmt) => {
            let semantic_key = extract_dml_target_table(statement);
            let method_id = dml_method_name("delete", &proc.proc_name, &mut ctx.dml_counter, semantic_key.as_deref());
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Delete,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                ..Default::default()
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        _ => {
            push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
        }
    }
}

fn infer_arg_type(arg: &str, proc: &ProcedureInfo) -> &'static str {
    let trimmed = arg.trim();
    if trimmed.starts_with('"') || trimmed.starts_with("String.valueOf(") {
        return "String";
    }
    if trimmed.starts_with("Long.parseLong(") || trimmed == "null" {
        return "unknown";
    }
    // List element access like vAccntIdList.get((int)(i) - 1)
    if trimmed.contains(".get(") && !trimmed.starts_with("AtomicReference") {
        for vtype in proc.local_vars.values() {
            if vtype.starts_with("List<") {
                return "String";
            }
        }
    }
    for (vname, vtype) in &proc.local_vars {
        let camel = crate::naming::snake_to_camel(vname);
        if trimmed == camel {
            return match vtype.as_str() {
                "long" | "Long" => "long",
                "String" => "String",
                t if t.starts_with("AtomicReference") => "AtomicReference",
                t if t.contains("BigDecimal") => "BigDecimal",
                t if t.starts_with("List<") => "String",
                _ => "Object",
            };
        }
    }
    for p in &proc.parameters {
        let camel = crate::naming::snake_to_camel(&p.name);
        if trimmed == camel {
            return match p.java_type.as_str() {
                "long" | "Long" => "long",
                "String" => "String",
                _ => "Object",
            };
        }
    }
    "Object"
}

fn process_procedure_call(
    call: &ogsql_parser::ast::plpgsql::PlProcedureCall,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    let name_parts: Vec<&str> = call.name.iter().map(|s| s.as_str()).collect();

    let (pkg_hint, func) = if name_parts.len() >= 3 {
        (name_parts[name_parts.len() - 2], name_parts[name_parts.len() - 1])
    } else if name_parts.len() == 2 {
        (name_parts[0], name_parts[1])
    } else if name_parts.len() == 1 {
        (&proc.package[..], name_parts[0])
    } else {
        let full_name = name_parts.join(".");
        push_logic_line(proc, format!("// CALL {}(...)", full_name));
        ctx.unresolved_calls.push(UnresolvedCall {
            caller: format!("{}.{}", proc.package, proc.proc_name),
            callee: full_name.clone(),
            caller_file: proc.source_file.clone(),
            args: String::new(),
            hint: "add the defining SQL file to fluxgauss.yaml sources".to_string(),
        });
        return;
    };

    let method = java_method_name(func);
    let raw_args: Vec<String> = call.arguments.iter().map(|a| crate::expr::expr_to_java(a, proc)).collect();

    // RAISE_APPLICATION_ERROR(code, msg) is parsed as a procedure call, not RAISE.
    if func.eq_ignore_ascii_case("raise_application_error") {
        let msg = raw_args
            .get(1)
            .cloned()
            .unwrap_or_else(|| raw_args.first().cloned().unwrap_or_else(|| "\"RAISE_APPLICATION_ERROR\"".to_string()));
        push_logic_line(proc, format!("throw new BusinessException({});", msg));
        return;
    }

    // Try to resolve package from the hint
    let mut matched_pkg = resolve_package_name(pkg_hint, ctx.summaries);

    if name_parts.len() == 1
        && (matched_pkg.is_none()
            || matched_pkg.as_ref().map(|p| p.to_lowercase()) == Some(proc.package.to_lowercase()))
    {
        let found_in_current = ctx
            .summaries
            .get(&proc.package)
            .or_else(|| {
                let proc_pkg_lower = proc.package.to_lowercase();
                ctx.summaries.iter().find(|(k, _)| k.to_lowercase() == proc_pkg_lower).map(|(_, v)| v)
            })
            .map(|s| s.find_procedure(func).is_some())
            .unwrap_or(false);

        if !found_in_current {
            for (pkg_name, summary) in ctx.summaries.iter() {
                if summary.find_procedure(func).is_some() {
                    matched_pkg = Some(pkg_name.clone());
                    break;
                }
            }
        }
    }

    if let Some(ref matched_pkg_name) = matched_pkg {
        let svc_name = format!("{}Service", {
            let cn = package_to_classname(matched_pkg_name);
            let mut c = cn.chars();
            match c.next() {
                Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                None => String::new(),
            }
        });

        let is_self_call = matched_pkg_name.to_lowercase() == proc.package.to_lowercase();

        // Look up target procedure for type coercion
        let target_summary = ctx.summaries.get(matched_pkg_name).or_else(|| {
            let lower = matched_pkg_name.to_lowercase();
            ctx.summaries.iter().find(|(k, _)| k.to_lowercase() == lower).map(|(_, v)| v)
        });
        let target_proc = target_summary.and_then(|s| s.find_procedure(func));

        let _target_param_count = target_proc.as_ref().map(|tp| tp.parameters.len()).unwrap_or(0);
        let mut args: Vec<String> = raw_args
            .iter()
            .enumerate()
            .map(|(i, raw)| {
                let mut arg = strip_out_param_get(raw, proc);
                if let Some(tp) = &target_proc {
                    if i < tp.parameters.len() {
                        let param = &tp.parameters[i];
                        let target_type = &param.java_type;
                        if param.is_out() {
                            let arg_trimmed = arg.trim();
                            if !arg_trimmed.contains('.') && !arg_trimmed.contains('(') {
                                for vname in proc.local_vars.keys() {
                                    let vname_camel = crate::naming::snake_to_camel(vname);
                                    if vname_camel == arg_trimmed {
                                        proc.out_local_vars.insert(vname.to_lowercase(), target_type.clone());
                                        break;
                                    }
                                }
                            }
                        } else {
                            let arg_trimmed = arg.trim();
                            let is_atomic_ref = proc
                                .out_local_vars
                                .keys()
                                .any(|vname| crate::naming::snake_to_camel(vname) == arg_trimmed)
                                || proc
                                    .parameters
                                    .iter()
                                    .any(|p| p.is_out() && crate::naming::snake_to_camel(&p.name) == arg_trimmed);
                            if is_atomic_ref && !arg_trimmed.contains('.') && !arg_trimmed.contains('(') {
                                arg = format!("{}.get()", arg_trimmed);
                            }
                            let arg_type_inferred = infer_arg_type(&arg, proc);
                            let target_is_long = target_type == "long" || target_type == "Long";
                            let target_is_int = target_type == "int" || target_type == "Integer";
                            let target_is_string = target_type == "String";
                            let arg_has_get = arg.contains(".get(");
                            if is_nullish_java_expr(&arg) && target_is_long {
                                arg = "0L".to_string();
                            } else if is_nullish_java_expr(&arg) && target_is_int {
                                arg = "0".to_string();
                            } else if target_is_string
                                && (arg_type_inferred == "long"
                                    || arg_type_inferred == "Object"
                                    || arg_type_inferred == "BigDecimal")
                                && !is_nullish_java_expr(&arg)
                            {
                                arg = format!("String.valueOf({})", arg);
                            } else if target_is_long && arg_type_inferred == "String" && !is_nullish_java_expr(&arg) {
                                arg = format!("Long.parseLong(String.valueOf({}))", arg);
                            } else if target_is_long && arg_type_inferred == "BigDecimal" {
                                arg = format!("({}).longValue()", arg);
                            } else if target_is_int && arg_has_get {
                                arg = format!("((Number) {}).intValue()", arg);
                            } else if target_is_long && arg_has_get {
                                arg = format!("((Number) {}).longValue()", arg);
                            }
                        }
                    }
                }
                arg
            })
            .collect();

        // When target procedure info is not available (cross-package call to framework service),
        // unwrap AtomicReference OUT params since the callee likely expects plain types.
        if target_proc.is_none() {
            args = args
                .iter()
                .enumerate()
                .map(|(i, arg)| {
                    let arg_trimmed = arg.trim();
                    if i < raw_args.len() {
                        let raw = raw_args[i].trim();
                        let is_promoted =
                            proc.out_local_vars.keys().any(|vname| crate::naming::snake_to_camel(vname) == raw);
                        let is_out =
                            proc.parameters.iter().any(|p| p.is_out() && crate::naming::snake_to_camel(&p.name) == raw);
                        if (is_promoted || is_out) && !arg_trimmed.contains('.') && !arg_trimmed.contains('(') {
                            return format!("{}.get()", arg_trimmed);
                        }
                    }
                    arg.clone()
                })
                .collect();
        }

        // Pad missing args when target has more params than caller provides
        if let Some(tp) = &target_proc {
            while args.len() < tp.parameters.len() {
                let param = &tp.parameters[args.len()];
                if param.is_out() {
                    args.push("new AtomicReference<>(null)".to_string());
                } else if param.java_type == "long" {
                    args.push("0L".into());
                } else if param.java_type == "int" {
                    args.push("0".into());
                } else {
                    args.push("null".into());
                }
            }
        }

        let args_java = args.join(", ");

        if is_self_call {
            let found = ctx
                .summaries
                .get(&proc.package)
                .or_else(|| {
                    let proc_pkg_lower = proc.package.to_lowercase();
                    ctx.summaries.iter().find(|(k, _)| k.to_lowercase() == proc_pkg_lower).map(|(_, v)| v)
                })
                .map(|s| s.find_procedure(func).is_some())
                .unwrap_or(true);
            if found {
                push_logic_line(proc, format!("this.{}({});", method, args_java));
            } else {
                push_logic_line(
                    proc,
                    format!("// CALL {}({}) — procedure not found in current package", method, args_java),
                );
                ctx.unresolved_calls.push(UnresolvedCall {
                    caller: format!("{}.{}", proc.package, proc.proc_name),
                    callee: format!("{}.{}", proc.package, func),
                    caller_file: proc.source_file.clone(),
                    args: args_java.clone(),
                    hint: "procedure not found in package".to_string(),
                });
            }
        } else {
            proc.service_calls.push(ServiceCall {
                service_name: svc_name.clone(),
                method_name: method.clone(),
                args: Vec::new(),
                package_name: matched_pkg_name.clone(),
            });
            push_logic_line(proc, format!("{}.{}({});", svc_name, method, args_java));
        }
    } else {
        let args_fallback: Vec<String> = call.arguments.iter().map(|a| crate::expr::expr_to_java(a, proc)).collect();
        let full_name = name_parts.join(".");
        push_logic_line(proc, format!("// CALL {}({})", full_name, args_fallback.join(", ")));
        ctx.unresolved_calls.push(UnresolvedCall {
            caller: format!("{}.{}", proc.package, proc.proc_name),
            callee: full_name.clone(),
            caller_file: proc.source_file.clone(),
            args: args_fallback.join(", "),
            hint: "add the defining SQL file to fluxgauss.yaml sources".to_string(),
        });
    }
}

fn try_resolve_perform_call(
    expr: &ogsql_parser::ast::Expr,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) -> bool {
    use ogsql_parser::ast::Expr;
    match expr {
        Expr::FunctionCall { name, args, .. } => {
            let name_parts: Vec<&str> = name.iter().map(|s| s.as_str()).collect();
            let (pkg, func_name) = if name_parts.len() >= 3 {
                (name_parts[name_parts.len() - 2], name_parts[name_parts.len() - 1])
            } else if name_parts.len() == 2 {
                (name_parts[0], name_parts[1])
            } else {
                return false;
            };

            let method = java_method_name(func_name);
            let args: Vec<String> = args
                .iter()
                .map(|a| {
                    let java = crate::expr::expr_to_java(a, proc);
                    strip_out_param_get(&java, proc)
                })
                .collect();
            let args_java = args.join(", ");

            if let Some(matched_pkg) = resolve_package_name(pkg, ctx.summaries) {
                let pkg_lower = matched_pkg.to_lowercase();
                let is_system_pkg =
                    ["dbms_output", "dbms_random", "dbms_lob", "dbe_output", "utl_file", "dbms_sql", "dbms_job"]
                        .iter()
                        .any(|sp| pkg_lower.starts_with(sp));
                let is_dbe_scheduler = pkg_lower.starts_with("dbe_scheduler");
                if is_system_pkg {
                    push_logic_line(proc, format!("// {}.{}({}) — system package stub", pkg, func_name, args_java));
                    return true;
                }
                if is_dbe_scheduler {
                    let func_lower = func_name.to_lowercase();
                    if func_lower == "create_job" || func_lower == "enable" {
                        push_logic_line(proc, format!("// DBE_SCHEDULER.{} — scheduler job action", func_name));
                    } else {
                        push_logic_line(proc, format!("// DBE_SCHEDULER.{}({})", func_name, args_java));
                    }
                    return true;
                }
                let svc_name = format!("{}Service", {
                    let cn = package_to_classname(&matched_pkg);
                    let mut c = cn.chars();
                    match c.next() {
                        Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                        None => String::new(),
                    }
                });

                let is_self_call = matched_pkg.to_lowercase() == proc.package.to_lowercase();

                if is_self_call {
                    push_logic_line(proc, format!("this.{}({});", method, args_java));
                } else {
                    proc.service_calls.push(ServiceCall {
                        service_name: svc_name.clone(),
                        method_name: method.clone(),
                        args: Vec::new(),
                        package_name: matched_pkg,
                    });
                    push_logic_line(proc, format!("{}.{}({});", svc_name, method, args_java));
                }
                return true;
            }
            false
        }
        _ => false,
    }
}

fn is_primitive_selector(sel: &str, proc: &ProcedureInfo) -> bool {
    let sel_var = sel.trim();
    let primitive_types: &[&str] = &["int", "long", "double", "float", "short", "byte", "char", "boolean"];
    if let Some(ty) = proc.local_vars.get(&sel_var.to_lowercase()) {
        return primitive_types.contains(&ty.as_str());
    }
    for p in &proc.parameters {
        let name = crate::naming::snake_to_camel(&p.name);
        if name == sel_var {
            let prim: &str = match p.java_type.as_str() {
                "Integer" => "int",
                "Long" => "long",
                "Double" => "double",
                "Float" => "float",
                "Short" => "short",
                "Byte" => "byte",
                "Character" => "char",
                "Boolean" => "boolean",
                other => other,
            };
            return primitive_types.contains(&prim);
        }
    }
    false
}

fn process_forall_stmt(
    forall: &ogsql_parser::ast::plpgsql::PlForAllStmt,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    let index_var = &forall.variable; // e.g., "i"
    let bounds_str = forall.bounds.trim();
    let dml_sql = forall.body.trim();

    if bounds_str.is_empty() {
        push_logic_line(proc, "// TODO: FORALL — empty bounds".into());
        return;
    }
    if dml_sql.is_empty() {
        push_logic_line(proc, "// TODO: FORALL — empty body".into());
        return;
    }

    // Detect DML type
    let dml_re = Regex::new(r"(?i)^\s*(INSERT|UPDATE|DELETE)\b").unwrap();
    let dml_cap = match dml_re.captures(dml_sql) {
        Some(c) => c,
        None => {
            push_logic_line(proc, format!("// TODO: FORALL — unparseable DML: {}", dml_sql));
            return;
        }
    };
    let dml_type_str = dml_cap.get(1).unwrap().as_str().to_lowercase();

    // Parse bounds: strip "IN " prefix
    let bounds_trimmed = bounds_str.trim();
    let bounds_no_in =
        if bounds_trimmed.to_uppercase().starts_with("IN ") { &bounds_trimmed[3..] } else { bounds_trimmed };
    let bounds_no_in = bounds_no_in.trim();

    // Range patterns: 1..v_arr.COUNT or 1..v_count or just var
    let range_count_re = Regex::new(r"(?i)^(\d+)\s*\.\.\s*(\w[\w.]*)\.\s*COUNT\s*$").unwrap();
    let range_simple_re = Regex::new(r"^(\w+)\s*\.\.\s*(\w+)$").unwrap();

    let loop_start;
    let is_array_count_range;
    let _primary_arr_java;
    if let Some(caps) = range_count_re.captures(bounds_no_in) {
        let low = &caps[1];
        let arr_var = caps[2].trim();
        let arr_java = snake_to_camel(arr_var.split('.').next().unwrap_or(""));
        loop_start = format!("for (int {index_var} = {low}; {index_var} <= {arr_java}.size(); {index_var}++)");
        is_array_count_range = true;
        _primary_arr_java = arr_java;
    } else if let Some(caps) = range_simple_re.captures(bounds_no_in) {
        let low = &caps[1];
        let high = &caps[2];
        let high_java = snake_to_camel(high);
        loop_start = format!("for (int {index_var} = {low}; {index_var} <= {high_java}; {index_var}++)");
        is_array_count_range = false;
        _primary_arr_java = String::new();
    } else {
        let var_java = snake_to_camel(bounds_no_in);
        loop_start = format!("for (int {index_var} = 1; {index_var} <= {var_java}; {index_var}++)");
        is_array_count_range = false;
        _primary_arr_java = String::new();
    };

    // Find array references: p_array(i), p_ids(i)
    let array_ref_re = Regex::new(&format!(r"(?i)(\w+)\s*\(\s*{}\s*\)", regex::escape(index_var))).unwrap();
    let array_names: Vec<String> = array_ref_re.captures_iter(dml_sql).map(|c| c[1].to_string()).collect();
    let mut seen_arrays: std::collections::HashSet<String> = std::collections::HashSet::new();
    for an in &array_names {
        seen_arrays.insert(an.to_lowercase());
    }

    // Determine if batch is possible: range is 1..arr.COUNT with at least one array ref
    let can_batch = is_array_count_range && !array_names.is_empty();

    // Build extra_params for array element types (unwraps List<T> → T)
    let mut extra_params: Vec<(String, String)> = Vec::new();
    let mut forall_batch_arrays: HashMap<String, String> = HashMap::new();

    for arr_name in &array_names {
        let arr_lower = arr_name.to_lowercase();
        if arr_lower == index_var.to_lowercase() || !seen_arrays.contains(&arr_lower) {
            continue;
        }
        let arr_java = snake_to_camel(arr_name);
        let arr_type = proc.local_vars.get(&arr_name.to_lowercase()).map(|s| s.as_str()).unwrap_or("Object");
        // Extract element type from List<T>
        let elem_type_re: &Regex = &ELEM_LIST_TYPE_RE;
        if let Some(et_caps) = elem_type_re.captures(arr_type) {
            let elem_type = et_caps.get(1).unwrap().as_str().to_string();
            extra_params.push((arr_java.clone(), elem_type.clone()));
            if can_batch {
                forall_batch_arrays.insert(arr_java, elem_type);
            }
        } else {
            extra_params.push((arr_java.clone(), arr_type.to_string()));
        }
    }

    // Rewrite SQL: replace p_array(i) → #{pArray} (for per-row) or #{item.pArray} (for batch)
    let mut mybatis_sql = dml_sql.to_string();
    let array_replace_re = Regex::new(&format!(r"(?i)(\w+)\s*\(\s*{}\s*\)", regex::escape(index_var))).unwrap();
    if can_batch {
        mybatis_sql = array_replace_re
            .replace_all(&mybatis_sql, |caps: &regex::Captures| {
                let arr_java = snake_to_camel(&caps[1]);
                format!("#{{item.{}}}", arr_java)
            })
            .to_string();
    } else {
        mybatis_sql = array_replace_re
            .replace_all(&mybatis_sql, |caps: &regex::Captures| {
                let arr_java = snake_to_camel(&caps[1]);
                format!("#{{{}}}", arr_java)
            })
            .to_string();
    }

    // Detect standalone index reference (e.g., "|| i")
    let standalone_index_re =
        Regex::new(&format!(r"(?i)[^(\w]{}[^)\w]|\b{}\b", regex::escape(index_var), regex::escape(index_var))).unwrap();
    // Check if standalone index is used (not inside array subscript)
    let text_without_subscripts = array_replace_re.replace_all(dml_sql, "?").to_string();
    let has_standalone_index = standalone_index_re.is_match(&text_without_subscripts);

    // Add index var as extra param if used standalone
    let index_var_java = format!("_{}", index_var);
    if has_standalone_index {
        extra_params.push((index_var_java.clone(), "Integer".to_string()));
    }

    // Build mapper method name
    let mapper_method = dml_method_name(&dml_type_str, &proc.proc_name, &mut ctx.dml_counter, None);

    let dml_type = match dml_type_str.as_str() {
        "insert" => DmlType::Insert,
        "update" => DmlType::Update,
        "delete" => DmlType::Delete,
        _ => {
            push_logic_line(proc, format!("// TODO: FORALL — unsupported DML type: {}", dml_type_str));
            return;
        }
    };

    // Build call args for the mapper method
    let mut call_args: Vec<String> = Vec::new();
    let mut seen_args: std::collections::HashSet<String> = std::collections::HashSet::new();

    // Local vars referenced in SQL (excluding subscript vars which are already handled)
    let clean_sql = array_replace_re.replace_all(dml_sql, "?").to_string();
    let ident_re = Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap();
    for caps in ident_re.captures_iter(&clean_sql) {
        let word = caps.get(1).unwrap().as_str();
        let lower = word.to_lowercase();
        if matches!(
            lower.as_str(),
            "select"
                | "from"
                | "where"
                | "insert"
                | "into"
                | "values"
                | "update"
                | "set"
                | "delete"
                | "and"
                | "or"
                | "not"
                | "null"
                | "is"
                | "in"
                | "between"
                | "like"
                | "as"
                | "on"
                | "join"
                | "left"
                | "right"
                | "inner"
                | "outer"
                | "order"
                | "by"
                | "group"
                | "having"
                | "limit"
                | "offset"
                | "union"
                | "all"
                | "distinct"
                | "case"
                | "when"
                | "then"
                | "else"
                | "end"
                | "exists"
                | "true"
                | "false"
                | "asc"
                | "desc"
                | "current_timestamp"
                | "current_date"
                | "current_time"
                | "now"
                | "count"
                | "sum"
                | "avg"
                | "min"
                | "max"
                | "coalesce"
                | "nvl"
                | "cast"
                | "default"
                | "returning"
        ) {
            continue;
        }
        let jn = snake_to_camel(word);
        let jn_lower = jn.to_lowercase();
        if seen_args.contains(&jn_lower) || seen_arrays.contains(&jn_lower) {
            continue;
        }
        if let Some(_var_type) = proc.local_vars.get(&word.to_lowercase()) {
            seen_args.insert(jn_lower.clone());
            // If this is an array ref var, use .get() access
            let is_array_param = array_names.iter().any(|an| an.to_lowercase() == lower);
            if is_array_param {
                call_args.push(format!("{}.get((int)({}) - 1)", jn, index_var));
            } else {
                call_args.push(jn);
            }
        }
    }

    // Procedure parameters
    for p in &proc.parameters {
        if p.is_out() {
            continue;
        }
        let pj = snake_to_camel(&p.name);
        let pj_lower = pj.to_lowercase();
        if seen_args.contains(&pj_lower) || seen_arrays.contains(&pj_lower) {
            continue;
        }
        seen_args.insert(pj_lower);
        // Check if this param is an array ref in the FORALL
        let is_array_param = array_names.iter().any(|an| an.to_lowercase() == p.name.to_lowercase());
        if is_array_param {
            call_args.push(format!("{}.get((int)({}) - 1)", pj, index_var));
        } else {
            call_args.push(pj);
        }
    }

    let args_str = call_args.join(", ");

    proc.dml_statements.push(DmlStatement {
        sql_type: dml_type,
        method_id: mapper_method.clone(),
        sql_text: mybatis_sql,
        result_type: None,
        returns_list: false,
        extra_params,
        is_forall_batch: can_batch,
        forall_batch_list_var: "item".to_string(),
        forall_batch_arrays,
        ..Default::default()
    });

    // Generate Java code
    if can_batch {
        // Batch mode: build list of Maps and pass to mapper once
        push_logic_line(
            proc,
            format!(
                "java.util.List<java.util.Map<String, Object>> _batch_{} = new java.util.ArrayList<>();",
                mapper_method
            ),
        );
        push_logic_line(proc, format!("for (int _bi = 0; _bi < {}; _bi++) {{", _primary_arr_java));
        push_logic_line(proc, "    java.util.Map<String, Object> _brow = new java.util.LinkedHashMap<>();".into());
        for arr_name in &array_names {
            let arr_lower = arr_name.to_lowercase();
            if arr_lower == index_var.to_lowercase() {
                continue;
            }
            let arr_java = snake_to_camel(arr_name);
            push_logic_line(proc, format!("    _brow.put(\"{}\", {}.get(_bi));", arr_java, arr_java));
        }
        if has_standalone_index {
            push_logic_line(proc, format!("    _brow.put(\"{}\", _bi + 1);", index_var_java));
        }
        push_logic_line(proc, format!("    _batch_{}.add(_brow);", mapper_method));
        push_logic_line(proc, "}".to_string());
        push_logic_line(proc, format!("__ROWCOUNT__ += mapper.{}(_batch_{});", mapper_method, mapper_method));
    } else {
        // Per-row loop
        push_logic_line(proc, format!("{} {{", loop_start));
        push_logic_line(proc, format!("    __ROWCOUNT__ += mapper.{}({});", mapper_method, args_str));
        push_logic_line(proc, "}".to_string());
    }
    proc.imports.insert("import java.util.Map;".to_string());
    proc.imports.insert("import java.util.ArrayList;".to_string());
    proc.imports.insert("import java.util.List;".to_string());

    // Track table references
    for table_match in
        Regex::new(r"(?i)\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE)\s+(\w+)").unwrap().captures_iter(dml_sql)
    {
        if let Some(table_name) = table_match.get(1) {
            proc.table_refs.insert(table_name.as_str().to_string());
        }
    }
}

fn get_stmt_line(stmt: &ogsql_parser::ast::plpgsql::PlStatement, stmt_idx: usize, stmt_lines: &[u32]) -> u32 {
    use ogsql_parser::ast::plpgsql::PlStatement;
    let ast_line = match stmt {
        PlStatement::Block(b) => b.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::If(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Case(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Loop(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::While(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::For(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::ForEach(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::ReturnQuery(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Raise(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Execute(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Perform { span, .. } => span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Open(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::Fetch(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::GetDiagnostics(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::ProcedureCall(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::SqlStatement { span, .. } => span.as_ref().map(|s| s.start.line as u32),
        PlStatement::ForAll(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::VariableSet(s) => s.span.as_ref().map(|s| s.start.line as u32),
        PlStatement::VariableReset(s) => s.span.as_ref().map(|s| s.start.line as u32),
        _ => None,
    };
    if let Some(line) = ast_line {
        if line > 0 {
            return line;
        }
    }
    if stmt_idx < stmt_lines.len() && stmt_lines[stmt_idx] > 0 {
        return stmt_lines[stmt_idx];
    }
    0
}

pub fn process_statement(
    stmt: &ogsql_parser::ast::plpgsql::PlStatement,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) -> Result<(), ConversionError> {
    use ogsql_parser::ast::plpgsql::PlStatement;

    if ctx.debug && !matches!(stmt, PlStatement::Null) {
        let stmt_line = get_stmt_line(stmt, ctx.current_stmt_idx, &ctx.stmt_lines);
        if stmt_line > 0 && !proc.source_path.is_empty() {
            let debug_comment = crate::debug::format_debug_comment(&proc.source_path, stmt_line, 100);
            proc.java_logic_lines.push(debug_comment);
        }
    }

    match stmt {
        PlStatement::Assignment { target, expression } => {
            if let Some(var_name) = extract_assignment_target_name(target) {
                // Track package variable writes for de-facto constant detection
                let var_base = var_name.split('.').next().unwrap_or(&var_name);
                if proc.package_vars.contains_key(var_base) {
                    proc.written_package_vars.insert(var_base.to_string());
                }
                if let Some((sql_template, concat_vars)) = flatten_concat(expression, proc) {
                    let lower = sql_template.trim().to_lowercase();
                    let sql_verbs = [
                        "select",
                        "insert",
                        "update",
                        "delete",
                        "truncate",
                        "alter",
                        "drop",
                        "create",
                        "merge",
                        "savepoint",
                        "rollback",
                    ];
                    if sql_verbs.iter().any(|v| lower.starts_with(v)) {
                        proc.dynamic_sql_templates.insert(var_name.clone(), (sql_template, concat_vars));
                    }
                }
                if let Some((target_name, sql_fragment, clause_type)) =
                    detect_sql_concat_append(&var_name, expression, proc)
                {
                    let chain = proc.sql_concat_chain.entry(target_name).or_default();
                    let already_exists = chain.iter().any(|(_, f, _)| f == &sql_fragment);
                    if !already_exists {
                        chain.push(("true".to_string(), sql_fragment, clause_type));
                    }
                }
            }
            let line = crate::expr::assignment_to_java(target, expression, proc);
            push_logic_line(proc, line);
            Ok(())
        }
        PlStatement::Return { expression } => {
            if let Some(expr) = expression {
                let mut val = crate::expr::expr_to_java(expr, proc);
                // M5 (#114 review): a promoted local var (AtomicReference) returned
                // bare fails javac — deref to the value first.
                val = crate::expr::maybe_deref_promoted(&val, proc);
                let coerced = if let Some(rt) = &proc.return_type {
                    crate::expr::coerce_for_type(&val, Some(rt), proc)
                } else {
                    val
                };
                push_logic_line(proc, format!("return {};", coerced));
            } else if proc.return_type.as_ref().is_some_and(|rt| rt != "void") {
                push_logic_line(proc, "return null;".into());
            } else if proc.parameters.iter().any(|p| p.is_out() && p.is_refcursor()) {
                push_logic_line(proc, "return java.util.Collections.emptyList();".into());
            } else {
                push_logic_line(proc, "return;".into());
            }
            Ok(())
        }
        PlStatement::ReturnNext { expression } => {
            let val = crate::expr::expr_to_java(expression, proc);
            push_logic_line(proc, format!("_returnResults.add({});", val));
            Ok(())
        }
        PlStatement::ReturnQuery(query) => {
            // RETURN QUERY EXECUTE v_sql or RETURN QUERY SELECT ...
            // For EXECUTE: TODO — requires dynamic SQL resolution
            // For direct SELECT: generate mapper call + addAll
            let query_str = format!("{:?}", query);
            if query_str.contains("Execute") || query_str.contains("execute") {
                push_logic_line(proc, "// TODO: RETURN QUERY EXECUTE — requires dynamic SQL resolution".into());
            } else {
                push_logic_line(
                    proc,
                    format!("// TODO: RETURN QUERY — {}", query_str.chars().take(80).collect::<String>()),
                );
            }
            Ok(())
        }
        PlStatement::Null => Ok(()),
        PlStatement::Perform { parsed_expr, query, .. } => {
            if let Some(expr) = parsed_expr {
                let val = crate::expr::expr_to_java(expr, proc);
                let trimmed = val.trim();

                let resolved = try_resolve_perform_call(expr, proc, ctx);
                if resolved {
                } else if trimmed.starts_with(|c: char| c.is_ascii_digit()) || trimmed == "null" {
                    push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
                    ctx.unresolved_calls.push(UnresolvedCall {
                        caller: format!("{}.{}", proc.package, proc.proc_name),
                        callee: format!("PERFORM {}", query.replace('\n', " ")),
                        caller_file: proc.source_file.clone(),
                        args: String::new(),
                        hint: "add the defining SQL file to fluxgauss.yaml sources".to_string(),
                    });
                } else {
                    push_logic_line(proc, format!("{};", val));
                }
            } else {
                push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
                ctx.unresolved_calls.push(UnresolvedCall {
                    caller: format!("{}.{}", proc.package, proc.proc_name),
                    callee: format!("PERFORM {}", query.replace('\n', " ")),
                    caller_file: proc.source_file.clone(),
                    args: String::new(),
                    hint: "add the defining SQL file to fluxgauss.yaml sources".to_string(),
                });
            }
            Ok(())
        }
        PlStatement::Raise(raise_stmt) => {
            let level_str = match &raise_stmt.node.level {
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Exception) => "exception",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Notice) => "notice",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Info) => "info",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Debug) => "debug",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Log) => "log",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Warning) => "warning",
                None => "info",
            };
            let raw_msg = raise_stmt.node.message.as_deref().unwrap_or("");
            let msg_for_format = raw_msg.replace('%', "%s");
            let msg_for_slf4j = raw_msg.replace('%', "{}");
            let params_java: Vec<String> =
                raise_stmt.node.params.iter().map(|p| crate::expr::expr_to_java(p, proc)).collect();
            let params_str =
                if params_java.is_empty() { String::new() } else { format!(", {}", params_java.join(", ")) };
            match level_str {
                "exception" => {
                    if params_java.is_empty() {
                        push_logic_line(proc, format!("throw new BusinessException(\"{}\");", msg_for_format));
                    } else {
                        push_logic_line(
                            proc,
                            format!(
                                "throw new BusinessException(String.format(\"{}\"{}));",
                                msg_for_format, params_str
                            ),
                        );
                    }
                }
                "notice" | "info" => {
                    push_logic_line(proc, format!("log.info(\"{}\"{});", msg_for_slf4j, params_str));
                }
                "debug" => {
                    push_logic_line(proc, format!("log.debug(\"{}\"{});", msg_for_slf4j, params_str));
                }
                "warning" => {
                    push_logic_line(proc, format!("log.warn(\"{}\"{});", msg_for_slf4j, params_str));
                }
                _ => {
                    push_logic_line(proc, format!("log.info(\"{}\"{});", msg_for_slf4j, params_str));
                }
            }
            Ok(())
        }
        PlStatement::If(if_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
            if if_stmt.node.elsifs.is_empty() && if_stmt.node.else_stmts.is_empty() {
                let mut all_concat = true;
                for s in &if_stmt.node.then_stmts {
                    if let PlStatement::Assignment { target, expression } = s {
                        if let Some(var_name) = extract_assignment_target_name(target) {
                            if let Some((target_name, sql_fragment, clause_type)) =
                                detect_sql_concat_append(&var_name, expression, proc)
                            {
                                let chain = proc.sql_concat_chain.entry(target_name).or_default();
                                let already_exists = chain.iter().any(|(_, f, _)| f == &sql_fragment);
                                if !already_exists {
                                    chain.push((cond.clone(), sql_fragment, clause_type));
                                }
                            } else {
                                all_concat = false;
                            }
                        }
                    } else {
                        all_concat = false;
                    }
                }
                if all_concat && !if_stmt.node.then_stmts.is_empty() {
                    return Ok(());
                }
            }
            push_logic_line(proc, format!("if ({}) {{", cond));
            for s in &if_stmt.node.then_stmts {
                process_statement(s, proc, ctx)?;
            }
            for elsif in &if_stmt.node.elsifs {
                let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                push_logic_line(proc, format!("}} else if ({}) {{", elsif_cond));
                for s in &elsif.stmts {
                    process_statement(s, proc, ctx)?;
                }
            }
            if !if_stmt.node.else_stmts.is_empty() {
                push_logic_line(proc, "} else {".into());
                for s in &if_stmt.node.else_stmts {
                    process_statement(s, proc, ctx)?;
                }
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::Case(case_stmt) => {
            let selector_java = case_stmt.node.expression.as_ref().map(|expr| crate::expr::expr_to_java(expr, proc));
            if let Some(ref sel) = selector_java {
                push_logic_line(proc, format!("// case {}:", sel));
            }
            for (i, when) in case_stmt.node.whens.iter().enumerate() {
                let cond = if let Some(ref sel) = selector_java {
                    let when_val = crate::expr::expr_to_java(&when.condition, proc);
                    let sel_primitive = is_primitive_selector(sel, proc);
                    if sel_primitive {
                        format!("{} == {}", sel, when_val)
                    } else {
                        format!("{}.equals({})", sel, when_val)
                    }
                } else {
                    crate::expr::expr_to_java(&when.condition, proc)
                };
                let prefix = if i == 0 { "if" } else { "} else if" };
                push_logic_line(proc, format!("{} ({}) {{", prefix, cond));
                for s in &when.stmts {
                    process_statement(s, proc, ctx)?;
                }
            }
            if !case_stmt.node.else_stmts.is_empty() {
                push_logic_line(proc, "} else {".into());
                for s in &case_stmt.node.else_stmts {
                    process_statement(s, proc, ctx)?;
                }
            }
            if !case_stmt.node.whens.is_empty() || !case_stmt.node.else_stmts.is_empty() {
                push_logic_line(proc, "}".into());
            }
            Ok(())
        }
        PlStatement::Block(block_stmt) => {
            // Promote inner Block declarations to method-level scope
            for decl in &block_stmt.node.declarations {
                crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new(), None);
            }
            let has_exceptions = block_stmt.node.exception_block.is_some();
            let try_line_idx = if has_exceptions {
                push_logic_line(proc, "try {".into());
                Some(proc.java_logic_lines.len() - 1)
            } else {
                None
            };
            for s in &block_stmt.node.body {
                process_statement(s, proc, ctx)?;
                // Stop processing Block body after terminal statement (return/break/Goto)
                if let Some(last) = proc.java_logic_lines.last() {
                    let t = last.trim();
                    if t == "break;" || t.starts_with("return ") || t == "return;" || t == "continue;" {
                        break;
                    }
                }
            }
            // If body ended with terminal, strip the try { line and skip exception handlers
            let ended_with_terminal = proc.java_logic_lines.last().is_some_and(|l| {
                let t = l.trim();
                t == "break;" || t.starts_with("return ") || t == "return;" || t == "continue;"
            });
            if ended_with_terminal {
                if let Some(idx) = try_line_idx {
                    proc.java_logic_lines.remove(idx);
                }
            }
            if has_exceptions && !ended_with_terminal {
                if let Some(exc_block) = &block_stmt.node.exception_block {
                    let mut has_business = false;
                    for handler in &exc_block.handlers {
                        let is_others = handler.conditions.is_empty()
                            || handler.conditions.iter().any(|c| c.eq_ignore_ascii_case("others"));
                        // Merge consecutive BusinessException handlers into the first one
                        if !is_others && has_business {
                            // Append body to previous catch (no new header)
                            for s in &handler.statements {
                                process_statement(s, proc, ctx)?;
                            }
                            continue;
                        }
                        if !is_others {
                            has_business = true;
                        }
                        let evar = format!("__e{}", {
                            let n = proc.catch_counter;
                            proc.catch_counter += 1;
                            n + 1
                        });
                        if is_others {
                            push_logic_line(proc, format!("}} catch (Exception {evar}) {{"));
                        } else {
                            let cond = handler.conditions.join(", ");
                            push_logic_line(proc, format!("}} catch (BusinessException {evar}) {{ // {}", cond));
                        }
                        push_logic_line(proc, format!("    __SQLERRM__ = {evar}.getMessage();"));
                        push_logic_line(proc, "    __SQLCODE__ = -1;".into());
                        for s in &handler.statements {
                            process_statement(s, proc, ctx)?;
                        }
                        if is_unreachable_after_terminal(&proc.java_logic_lines) {
                            break;
                        }
                    }
                }
                push_logic_line(proc, "}".into());
            }
            Ok(())
        }
        PlStatement::Loop(loop_stmt) => {
            proc.plain_loop_counter += 1;
            let guard_var = format!("_loopGuard{}", proc.plain_loop_counter);
            push_logic_line(proc, format!("int {} = 0;", guard_var));
            if let Some(label) = &loop_stmt.node.label {
                push_logic_line(proc, format!("{}: while (true) {{", label));
            } else {
                push_logic_line(proc, "while (true) {".into());
            }
            push_logic_line(proc, format!("if (++{} > 1000) {{ break; }}", guard_var));
            for s in &loop_stmt.node.body {
                process_statement(s, proc, ctx)?;
                if let Some(last) = proc.java_logic_lines.last() {
                    if is_terminal_statement(last) {
                        break;
                    }
                }
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::While(while_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&while_stmt.node.condition, proc);
            if let Some(label) = &while_stmt.node.label {
                push_logic_line(proc, format!("{}: while ({}) {{", label, cond));
            } else {
                push_logic_line(proc, format!("while ({}) {{", cond));
            }
            for s in &while_stmt.node.body {
                process_statement(s, proc, ctx)?;
                if let Some(last) = proc.java_logic_lines.last() {
                    if is_terminal_statement(last) {
                        break;
                    }
                }
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::For(for_stmt) => {
            let var = crate::naming::snake_to_camel(&for_stmt.node.variable);
            match &for_stmt.node.kind {
                ogsql_parser::ast::plpgsql::PlForKind::Range { low, high, step, reverse } => {
                    let lo = crate::expr::expr_to_java(low, proc);
                    let hi = crate::expr::expr_to_java(high, proc);
                    let lo_safe = if crate::expr::is_nullish_java_expr(&lo) { "0".to_string() } else { lo };
                    let hi_safe = if crate::expr::is_nullish_java_expr(&hi) { "0".to_string() } else { hi };
                    let iter_var = var.clone();
                    // Track the counter so body references resolve via the declared-variable
                    // path. When the var is ALREADY declared (e.g. `i INTEGER := NULL` driven
                    // by an earlier WHILE), keep its type and reuse it in the loop header —
                    // javac forbids a for-init `int i` shadowing a method-level `i`. Only
                    // pure loop counters get the inline `int` declaration.
                    let var_lower = for_stmt.node.variable.to_lowercase();
                    // A real SQL-declared var (absent from range_loop_counters) is
                    // reused in the loop header; a synthetic counter (auto-created
                    // here) keeps its own inline `int` per loop.
                    let pre_declared =
                        proc.local_vars.contains_key(&var_lower) && !proc.range_loop_counters.contains(&var_lower);
                    if !proc.local_vars.contains_key(&var_lower) {
                        proc.local_vars.insert(var_lower.clone(), "int".into());
                        proc.range_loop_counters.insert(var_lower);
                    }
                    let step_code = match step {
                        Some(s) => {
                            let s_val = crate::expr::expr_to_java(s, proc);
                            format!("{} += {}", iter_var, s_val)
                        }
                        None => format!("{}++", iter_var),
                    };
                    let header = if pre_declared {
                        // Reuse the method-level declaration; no `int` in the init.
                        if *reverse {
                            format!("for ({} = {}; {} >= {}; {}--) {{", iter_var, hi_safe, iter_var, lo_safe, iter_var)
                        } else {
                            format!("for ({} = {}; {} <= {}; {}) {{", iter_var, lo_safe, iter_var, hi_safe, step_code)
                        }
                    } else if *reverse {
                        format!("for (int {} = {}; {} >= {}; {}--) {{", iter_var, hi_safe, iter_var, lo_safe, iter_var)
                    } else {
                        format!("for (int {} = {}; {} <= {}; {}) {{", iter_var, lo_safe, iter_var, hi_safe, step_code)
                    };
                    push_logic_line(proc, header);
                }
                ogsql_parser::ast::plpgsql::PlForKind::Query { query, .. } => {
                    let clean_sql = strip_sql_comments(query).replace('\n', " ");
                    let resolved_sql = strip_execute_prefix(&clean_sql);

                    let (sql_text, dynamic_conditions, extra_params, base_sql) = if is_variable_reference(resolved_sql)
                    {
                        let var_name = resolved_sql.trim();
                        if let Some(resolved) = resolve_dynamic_sql_text(proc, var_name) {
                            let conditions = collect_dynamic_conditions(proc, var_name);
                            let params = collect_extra_params_from_template(proc, var_name);
                            (resolved.clone(), conditions, params, resolved)
                        } else {
                            (clean_sql.clone(), Vec::new(), Vec::new(), String::new())
                        }
                    } else {
                        (clean_sql.clone(), Vec::new(), Vec::new(), String::new())
                    };

                    push_logic_line(proc, format!("// for {} in query: {}", var, clean_sql));
                    proc.local_vars.insert(for_stmt.node.variable.to_lowercase(), "Map<String, Object>".into());
                    proc.local_var_defaults.remove(&for_stmt.node.variable);
                    proc.imports.insert("import java.util.List;".to_string());
                    proc.imports.insert("import java.util.Map;".to_string());
                    proc.imports.insert("import java.util.ArrayList;".to_string());

                    let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter, None);
                    let args = build_mapper_call_args(proc);

                    proc.dml_statements.push(DmlStatement {
                        sql_type: DmlType::Select,
                        method_id: method_id.clone(),
                        sql_text,
                        result_type: Some("Map<String, Object>".to_string()),
                        returns_list: true,
                        extra_params,
                        dynamic_conditions,
                        base_sql,
                        ..Default::default()
                    });

                    proc.for_loop_counter += 1;
                    let list_var = if proc.for_loop_counter <= 1 {
                        format!("{}List", var)
                    } else {
                        format!("{}List{}", var, proc.for_loop_counter)
                    };
                    push_logic_line(
                        proc,
                        format!("List<Map<String, Object>> {} = mapper.{}({});", list_var, method_id, args),
                    );
                    push_logic_line(proc, format!("if ({} == null) {} = new ArrayList<>();", list_var, list_var));
                    push_logic_line(proc, format!("for (Map<String, Object> {} : {}) {{", var, list_var));
                }
                ogsql_parser::ast::plpgsql::PlForKind::Cursor { cursor_name, .. } => {
                    // Source cursor name in the stub comment, not a resolved
                    // expression — keeps the comment readable for untracked cursors.
                    let cursor_java = crate::expr::get_column_ref_name(cursor_name)
                        .unwrap_or_else(|| crate::expr::expr_to_java(cursor_name, proc));
                    push_logic_line(proc, format!("// for {} in cursor {}", var, cursor_java));
                    proc.local_vars.remove(&for_stmt.node.variable);
                    proc.local_var_defaults.remove(&for_stmt.node.variable);
                    proc.imports.insert("import java.util.List;".to_string());
                    proc.imports.insert("import java.util.Map;".to_string());
                    proc.imports.insert("import java.util.ArrayList;".to_string());

                    let cursor_name_str = match cursor_name {
                        ogsql_parser::ast::Expr::ColumnRef(name) | ogsql_parser::ast::Expr::PlVariable(name) => {
                            name.last().map(|s| s.as_str()).unwrap_or("")
                        }
                        _ => "",
                    };

                    let cursor_query = if !cursor_name_str.is_empty() {
                        proc.cursor_decls.get(cursor_name_str).cloned()
                    } else {
                        None
                    };

                    if let Some(query_sql) = cursor_query {
                        let clean_sql = query_sql.replace('\n', " ");
                        let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter, None);
                        let args = build_mapper_call_args(proc);

                        proc.dml_statements.push(DmlStatement {
                            sql_type: DmlType::Select,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            ..Default::default()
                        });

                        proc.for_loop_counter += 1;
                        let list_var = if proc.for_loop_counter <= 1 {
                            format!("{}List", var)
                        } else {
                            format!("{}List{}", var, proc.for_loop_counter)
                        };
                        push_logic_line(
                            proc,
                            format!("List<Map<String, Object>> {} = mapper.{}({});", list_var, method_id, args),
                        );
                        push_logic_line(proc, format!("if ({} == null) {} = new ArrayList<>();", list_var, list_var));
                        push_logic_line(proc, format!("for (Map<String, Object> {} : {}) {{", var, list_var));
                    } else {
                        push_logic_line(proc, format!("for (Map<String, Object> {} : java.util.Collections.<Map<String, Object>>emptyList()) {{", var));
                    }
                }
            }
            for s in &for_stmt.node.body {
                process_statement(s, proc, ctx)?;
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::ForEach(foreach_stmt) => {
            let var = crate::naming::snake_to_camel(&foreach_stmt.node.variable);
            let expr = crate::expr::expr_to_java(&foreach_stmt.node.expression, proc);
            push_logic_line(proc, format!("for (Object {} : (Iterable<?>) ({})) {{", var, expr));
            for s in &foreach_stmt.node.body {
                process_statement(s, proc, ctx)?;
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::Exit { label, condition } => {
            if ctx.sm_enum_name.is_some() {
                push_logic_line(proc, "running = false;".into());
                push_logic_line(proc, "break;".into());
            } else {
                match (label, condition) {
                    (Some(l), Some(c)) => {
                        let cond = crate::expr::bool_expr_to_java(c, proc);
                        push_logic_line(proc, format!("if ({}) {{ break {}; }}", cond, l));
                    }
                    (Some(l), None) => {
                        push_logic_line(proc, format!("break {};", l));
                    }
                    (None, Some(c)) => {
                        let cond = crate::expr::bool_expr_to_java(c, proc);
                        push_logic_line(proc, format!("if ({}) {{ break; }}", cond));
                    }
                    (None, None) => {
                        push_logic_line(proc, "break;".into());
                    }
                }
            }
            Ok(())
        }
        PlStatement::Continue { label, condition } => {
            if ctx.sm_enum_name.is_some() {
                push_logic_line(proc, "break;".into());
            } else {
                match (label, condition) {
                    (Some(l), Some(c)) => {
                        let cond = crate::expr::bool_expr_to_java(c, proc);
                        push_logic_line(proc, format!("if ({}) {{ continue {}; }}", cond, l));
                    }
                    (Some(l), None) => {
                        push_logic_line(proc, format!("continue {};", l));
                    }
                    (None, Some(c)) => {
                        let cond = crate::expr::bool_expr_to_java(c, proc);
                        push_logic_line(proc, format!("if ({}) {{ continue; }}", cond));
                    }
                    (None, None) => {
                        push_logic_line(proc, "continue;".into());
                    }
                }
            }
            Ok(())
        }
        PlStatement::Open(open_stmt) => {
            let cursor_java = crate::expr::expr_to_java(&open_stmt.node.cursor, proc);

            use ogsql_parser::ast::Expr;
            let raw_cursor_name = match &open_stmt.node.cursor {
                Expr::ColumnRef(name) | Expr::PlVariable(name) if name.len() == 1 => ident_string(&name[0]),
                _ => cursor_java.clone(),
            };
            let raw_cursor_java = crate::naming::snake_to_camel(&raw_cursor_name);

            let is_out_refcursor = proc.refcursor_out_params.contains(&raw_cursor_java);

            #[allow(clippy::type_complexity)]
            let (sql_text_opt, using_args, dynamic_conditions, _extra_params, base_sql): (
                Option<String>,
                Vec<ogsql_parser::ast::Expr>,
                Vec<crate::types::DynamicCondition>,
                Vec<(String, String)>,
                String,
            ) = match &open_stmt.node.kind {
                ogsql_parser::ast::plpgsql::PlOpenKind::ForQuery { query, .. } => {
                    if query.contains("||") || query.trim().starts_with('\'') {
                        push_logic_line(
                            proc,
                            format!(
                                "/* OPEN {} FOR {} — dynamic SQL, not materializable */",
                                cursor_java,
                                query.replace("*/", "*\\/")
                            ),
                        );
                        return Ok(());
                    }
                    let clean = query.trim().trim_end_matches(';').to_string();
                    let resolved_clean = strip_execute_prefix(&clean);
                    if is_variable_reference(resolved_clean) {
                        let var_name = resolved_clean.trim();
                        if let Some(resolved) = resolve_dynamic_sql_text(proc, var_name) {
                            let conditions = collect_dynamic_conditions(proc, var_name);
                            let params = collect_extra_params_from_template(proc, var_name);
                            (Some(resolved.clone()), Vec::new(), conditions, params, resolved)
                        } else {
                            (Some(clean), Vec::new(), Vec::new(), Vec::new(), String::new())
                        }
                    } else {
                        (Some(clean), Vec::new(), Vec::new(), Vec::new(), String::new())
                    }
                }
                ogsql_parser::ast::plpgsql::PlOpenKind::Simple { arguments } => {
                    let decl_sql = proc
                        .cursor_decls
                        .get(&raw_cursor_name)
                        .or_else(|| proc.cursor_decls.get(&raw_cursor_name.to_lowercase()))
                        .cloned();
                    (decl_sql, arguments.clone(), Vec::new(), Vec::new(), String::new())
                }
                ogsql_parser::ast::plpgsql::PlOpenKind::ForExecute { query, using_args } => {
                    if let Some(var_name) = extract_var_name_from_expr(query) {
                        if let Some(resolved) = resolve_dynamic_sql_text(proc, &var_name) {
                            let conditions = collect_dynamic_conditions(proc, &var_name);
                            let params = collect_extra_params_from_template(proc, &var_name);
                            (Some(resolved.clone()), using_args.clone(), conditions, params, resolved)
                        } else {
                            let sql_str = crate::expr::expr_to_java(query, proc);
                            push_logic_line(
                                proc,
                                format!(
                                    "/* OPEN {} FOR EXECUTE {} — could not resolve dynamic SQL */",
                                    cursor_java,
                                    sql_str.replace("*/", "*\\/")
                                ),
                            );
                            return Ok(());
                        }
                    } else {
                        let sql_str = crate::expr::expr_to_java(query, proc);
                        let using_str = if using_args.is_empty() {
                            String::new()
                        } else {
                            format!(
                                " USING {}",
                                using_args
                                    .iter()
                                    .map(|a| crate::expr::expr_to_java(a, proc))
                                    .collect::<Vec<_>>()
                                    .join(", ")
                            )
                        };
                        push_logic_line(
                            proc,
                            format!(
                                "/* OPEN {} FOR EXECUTE {}{} */",
                                cursor_java,
                                sql_str.replace("*/", "*\\/"),
                                using_str.replace("*/", "*\\/")
                            ),
                        );
                        return Ok(());
                    }
                }
                _ => {
                    push_logic_line(proc, format!("/* OPEN {} */", cursor_java));
                    return Ok(());
                }
            };

            if let Some(raw_sql) = sql_text_opt {
                let sql_text = preprocess_cursor_sql(&raw_sql, &using_args, proc);
                let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter, None);
                let result_var = format!("{}Result", raw_cursor_java);
                let index_var = format!("{}Idx", raw_cursor_java);

                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text,
                    result_type: Some("Map<String, Object>".to_string()),
                    returns_list: true,
                    dynamic_conditions,
                    base_sql,
                    ..Default::default()
                });

                let args = build_mapper_call_args(proc);
                push_logic_line(proc, format!("{} = mapper.{}({});", result_var, method_id, args));
                push_logic_line(
                    proc,
                    format!("if ({} == null) {} = new java.util.ArrayList<>();", result_var, result_var),
                );
                if !is_out_refcursor {
                    push_logic_line(proc, format!("{} = 0;", index_var));
                }

                proc.open_cursors.insert(
                    raw_cursor_java.clone(),
                    crate::types::CursorInfo {
                        query: raw_sql,
                        into_vars: Vec::new(),
                        is_open: true,
                        result_var: Some(result_var),
                        index_var: Some(index_var),
                    },
                );
                proc.open_cursors.insert(
                    raw_cursor_name.to_lowercase(),
                    crate::types::CursorInfo {
                        query: String::new(),
                        into_vars: Vec::new(),
                        is_open: true,
                        result_var: Some(format!("{}Result", raw_cursor_java)),
                        index_var: Some(format!("{}Idx", raw_cursor_java)),
                    },
                );
            }
            Ok(())
        }
        PlStatement::Fetch(fetch_stmt) => {
            let cursor_name_str = match &fetch_stmt.node.cursor {
                ogsql_parser::ast::Expr::ColumnRef(name) | ogsql_parser::ast::Expr::PlVariable(name)
                    if name.len() == 1 =>
                {
                    ident_string(&name[0])
                }
                _ => "cursor".into(),
            };
            let cursor_java = crate::naming::snake_to_camel(&cursor_name_str);

            let cursor_info = proc
                .open_cursors
                .get(&cursor_java)
                .or_else(|| proc.open_cursors.get(&cursor_name_str))
                .or_else(|| proc.open_cursors.get(&cursor_name_str.to_lowercase()))
                .cloned();

            if let Some(info) = cursor_info {
                if let (Some(rv), Some(iv)) = (&info.result_var, &info.index_var) {
                    push_logic_line(proc, format!("found = {} < {}.size();", iv, rv));
                    let into_exprs = &fetch_stmt.node.into;
                    if !into_exprs.is_empty() {
                        push_logic_line(proc, "if (found) {".into());
                        push_logic_line(proc, format!("    Map<String, Object> _row = {}.get({});", rv, iv));
                        push_logic_line(proc, format!("    {}++;", iv));
                        for into_expr in into_exprs {
                            let var_name = match into_expr {
                                ogsql_parser::ast::Expr::ColumnRef(name)
                                | ogsql_parser::ast::Expr::PlVariable(name)
                                    if name.len() == 1 =>
                                {
                                    ident_string(&name[0])
                                }
                                _ => continue,
                            };
                            let var_java = crate::naming::snake_to_camel(&var_name);
                            let (type_str, _) = resolve_var_type(proc, &var_name);
                            let (cast_expr, is_set_call) = row_extraction_expr("_row", &var_name, type_str);
                            if is_set_call {
                                push_logic_line(proc, format!("    {}{};", var_java, cast_expr));
                            } else if !cast_expr.is_empty() {
                                push_logic_line(proc, format!("    {} = {};", var_java, cast_expr));
                            }
                        }
                        push_logic_line(proc, "}".into());
                    }
                    return Ok(());
                }
            }
            push_logic_line(proc, format!("found = false; // FETCH {} — cursor not materialized", cursor_java));
            Ok(())
        }
        PlStatement::Close { cursor } => {
            // Render the source cursor name (not the resolved expression) so the
            // stub comment stays readable even when the cursor isn't tracked.
            let cur =
                crate::expr::get_column_ref_name(cursor).unwrap_or_else(|| crate::expr::expr_to_java(cursor, proc));
            push_logic_line(proc, format!("// CLOSE {};", cur));
            Ok(())
        }
        PlStatement::Move { cursor, .. } => {
            let _cur = crate::expr::expr_to_java(cursor, proc);
            let cur_name = format!("{:?}", cursor);
            let idx_var = format!("{}Idx", snake_to_camel(&cur_name.replace('"', "")));
            push_logic_line(proc, format!("{}++;", idx_var));
            Ok(())
        }
        PlStatement::GetDiagnostics(gd) => {
            for item in &gd.node.items {
                let var_name = match &item.target {
                    ogsql_parser::ast::Expr::PlVariable(name) => name.join("."),
                    ogsql_parser::ast::Expr::ColumnRef(name) => name.join("."),
                    _ => continue,
                };
                let var_java = snake_to_camel(&var_name);
                match item.item {
                    ogsql_parser::ast::plpgsql::GetDiagItemKind::RowCount => {
                        let var_type =
                            proc.local_vars.get(&var_name.to_lowercase()).map(|s| s.as_str()).unwrap_or("Integer");
                        if var_type == "int" {
                            push_logic_line(proc, format!("{} = __ROWCOUNT__;", var_java));
                        } else {
                            push_logic_line(proc, format!("{} = Integer.valueOf(__ROWCOUNT__);", var_java));
                        }
                    }
                    _ => {
                        push_logic_line(
                            proc,
                            format!("// GET DIAGNOSTICS {} = {} — manual review needed", var_java, item.item),
                        );
                    }
                }
            }
            Ok(())
        }
        PlStatement::Commit { .. } => {
            push_logic_line(proc, "// COMMIT;".into());
            Ok(())
        }
        PlStatement::Rollback { to_savepoint, .. } => {
            if let Some(sp) = to_savepoint {
                let sp_var = snake_to_camel(sp);
                proc.imports
                    .insert("import org.springframework.transaction.interceptor.TransactionAspectSupport;".into());
                push_logic_line(
                    proc,
                    format!("TransactionAspectSupport.currentTransactionStatus().rollbackToSavepoint({});", sp_var),
                );
            } else {
                proc.imports
                    .insert("import org.springframework.transaction.interceptor.TransactionAspectSupport;".into());
                push_logic_line(proc,
                    "try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (Exception _tx) {}".into(),
                );
            }
            Ok(())
        }
        PlStatement::Savepoint { name } => {
            proc.imports.insert("import java.sql.Savepoint;".to_string());
            let sp_var = snake_to_camel(name);
            push_logic_line(proc, format!("Savepoint {} = connection.setSavepoint(\"{}\");", sp_var, name));
            Ok(())
        }
        PlStatement::ReleaseSavepoint { name } => {
            push_logic_line(proc, format!("// RELEASE SAVEPOINT {} — not needed in Spring managed transaction", name));
            Ok(())
        }
        PlStatement::SetTransaction { .. } => {
            push_logic_line(proc, "// SET TRANSACTION;".into());
            Ok(())
        }
        PlStatement::VariableSet(_) => {
            push_logic_line(proc, "// SET variable;".into());
            Ok(())
        }
        PlStatement::VariableReset(_) => {
            push_logic_line(proc, "// RESET variable;".into());
            Ok(())
        }
        PlStatement::Goto { label } => {
            if let Some(ref enum_name) = ctx.sm_enum_name {
                if ctx.sm_labels.contains(label) {
                    let goto_state = crate::naming::snake_to_pascal(label);
                    push_logic_line(proc, format!("currentState = {}.{};", enum_name, goto_state));
                } else {
                    push_logic_line(proc, "running = false;".into());
                }
                push_logic_line(proc, "break;".into());
            } else {
                push_logic_line(proc, format!("// GOTO {} — will be rewritten by pattern analysis", label));
            }
            Ok(())
        }
        PlStatement::Execute(execute) => {
            process_execute_stmt(&execute.node, proc, ctx);
            Ok(())
        }
        PlStatement::ProcedureCall(call) => {
            process_procedure_call(&call.node, proc, ctx);
            Ok(())
        }
        PlStatement::Sql(sql_text) => {
            if let Some(dml_type) = detect_dml_type(sql_text) {
                let method_id = dml_method_name(
                    match dml_type {
                        DmlType::Select => "select",
                        DmlType::Insert => "insert",
                        DmlType::Update => "update",
                        DmlType::Delete => "delete",
                    },
                    &proc.proc_name,
                    &mut ctx.dml_counter,
                    None,
                );
                let args = build_mapper_call_args(proc);
                let clean_sql = clean_sql_for_mapper(sql_text, dml_type);
                let is_select = matches!(dml_type, DmlType::Select);

                if is_select {
                    let into_var = extract_into_var_from_text(sql_text);
                    let into_vars_count = extract_into_var_count_from_text(sql_text);

                    if let Some(var_name) = &into_var {
                        let is_out = proc.parameters.iter().any(|p| p.name == *var_name && p.is_out());
                        if is_out {
                            let var_java = snake_to_camel(var_name);
                            proc.dml_statements.push(DmlStatement {
                                sql_type: dml_type,
                                method_id: method_id.clone(),
                                sql_text: clean_sql,
                                result_type: Some("Object".to_string()),
                                ..Default::default()
                            });
                            push_logic_line(
                                proc,
                                out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc),
                            );
                        } else {
                            let var_java = snake_to_camel(var_name);
                            let declared_type = proc
                                .local_vars
                                .get(&var_name.to_lowercase())
                                .cloned()
                                .unwrap_or_else(|| "Object".to_string());
                            let java_type = infer_select_result_type(&declared_type, &clean_sql);

                            proc.dml_statements.push(DmlStatement {
                                sql_type: dml_type,
                                method_id: method_id.clone(),
                                sql_text: clean_sql,
                                result_type: Some(java_type.clone()),
                                ..Default::default()
                            });
                            push_logic_line(
                                proc,
                                if java_type != declared_type {
                                    format!("String _{} = mapper.{}({});", var_name, method_id, args)
                                } else {
                                    format!("{} = mapper.{}({});", var_java, method_id, args)
                                },
                            );
                        }
                    } else if into_vars_count > 1 {
                        let var_name = next_select_var_name(proc);
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            ..Default::default()
                        });
                        push_logic_line(
                            proc,
                            format!("Map<String, Object> {} = mapper.{}({});", var_name, method_id, args),
                        );
                        proc.imports.insert("import java.util.Map;".to_string());
                        if let Some(caps) = capture_into_regex().captures(sql_text) {
                            let into_vars_str = caps.get(1).unwrap().as_str();
                            let into_var_names: Vec<&str> = into_vars_str.split(',').map(|s| s.trim()).collect();
                            push_logic_line(proc, format!("if ({} != null) {{", var_name));
                            for iv in &into_var_names {
                                let iv_java = snake_to_camel(iv);
                                let (type_str, is_out_param) = resolve_var_type(proc, iv);
                                let declared_type = type_str.to_string();
                                let (extraction, is_set_call) = if is_out_param {
                                    let inner = match declared_type.as_str() {
                                         "Long" | "long" => format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).longValue() : 0L)", var_name, iv, var_name, iv),
                                         "Integer" | "int" => format!(".set({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).intValue() : 0)", var_name, iv, var_name, iv),
                                          "String" => format!(".set({}.get(\"{}\") instanceof String ? (String) {}.get(\"{}\") : {}.get(\"{}\") != null ? String.valueOf({}.get(\"{}\")) : null)", var_name, iv, var_name, iv, var_name, iv, var_name, iv),
                                         t if t.contains("BigDecimal") => format!(".set({}.get(\"{}\") instanceof java.math.BigDecimal ? (java.math.BigDecimal) {}.get(\"{}\") : java.math.BigDecimal.ZERO)", var_name, iv, var_name, iv),
                                         _ => format!(".set({}.get(\"{}\"))", var_name, iv),
                                     };
                                    (inner, true)
                                } else {
                                    match declared_type.as_str() {
                                         "Long" | "long" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).longValue() : 0L)", var_name, iv, var_name, iv), false),
                                          "Integer" | "int" => (format!("({}.get(\"{}\") instanceof Number ? ((Number) {}.get(\"{}\")).intValue() : 0)", var_name, iv, var_name, iv), false),
                                          "String" => (format!("({}.get(\"{}\") instanceof String ? (String) {}.get(\"{}\") : {}.get(\"{}\") != null ? String.valueOf({}.get(\"{}\")) : null)", var_name, iv, var_name, iv, var_name, iv, var_name, iv), false),
                                         t if t.contains("BigDecimal") => (format!("({}.get(\"{}\") instanceof java.math.BigDecimal ? (java.math.BigDecimal) {}.get(\"{}\") : java.math.BigDecimal.ZERO)", var_name, iv, var_name, iv), false),
                                         _ => row_extraction_expr(&var_name, iv, &declared_type),
                                     }
                                };
                                if is_set_call {
                                    push_logic_line(proc, format!("    {}{};", iv_java, extraction));
                                } else if !extraction.is_empty() {
                                    push_logic_line(proc, format!("    {} = {};", iv_java, extraction));
                                }
                            }
                            push_logic_line(proc, "}".to_string());
                        }
                    } else {
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            ..Default::default()
                        });
                        let rv_name = next_result_var_name(proc);
                        push_logic_line(
                            proc,
                            format!("List<Map<String, Object>> {} = mapper.{}({});", rv_name, method_id, args),
                        );
                        proc.imports.insert("import java.util.List;".to_string());
                        proc.imports.insert("import java.util.Map;".to_string());
                    }
                } else {
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: None,
                        ..Default::default()
                    });
                    push_logic_line(proc, format!("mapper.{}({});", method_id, args));
                }
            } else {
                let sql_trimmed = sql_text.trim();
                if let Some((var_name, java_type, default_java)) = try_parse_inline_var_decl(sql_trimmed, proc) {
                    proc.local_vars.insert(var_name.to_lowercase(), java_type.clone());
                    if let Some(default) = &default_java {
                        proc.local_var_defaults.insert(var_name.to_lowercase(), default.clone());
                    } else {
                        let default_val = match java_type.as_str() {
                            "int" | "Integer" => "0",
                            "long" | "Long" => "0",
                            "java.math.BigDecimal" => "0",
                            "String" => "null",
                            "java.sql.Timestamp" => "null",
                            "java.sql.Date" => "null",
                            _ => "null",
                        };
                        proc.local_var_defaults.insert(var_name.to_lowercase(), default_val.to_string());
                    }
                } else {
                    push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
                }
            }
            Ok(())
        }
        PlStatement::SqlStatement { sql_text, statement, .. } => {
            process_sql_statement(statement, sql_text, proc, ctx);
            Ok(())
        }
        PlStatement::ForAll(forall) => {
            process_forall_stmt(&forall.node, proc, ctx);
            Ok(())
        }
        PlStatement::PipeRow { expression } => {
            let val = crate::expr::expr_to_java(expression, proc);
            push_logic_line(proc, format!("// PIPE ROW: {};", val));
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    fn empty_proc() -> ProcedureInfo {
        ProcedureInfo::new("pkg.test".into(), "pkg".into(), "test".into())
    }

    fn empty_stmt_ctx() -> StatementContext<'static> {
        StatementContext {
            summaries: Box::leak(Box::new(HashMap::new())),
            dml_counter: HashMap::new(),
            sm_enum_name: None,
            sm_labels: HashSet::new(),
            debug: false,
            current_stmt_idx: 0,
            stmt_lines: Vec::new(),
            unresolved_calls: Vec::new(),
        }
    }

    #[test]
    fn test_for_range_counter_predeclared_reuses_method_var() {
        // fastaas ImportExcelService: `i` is declared (Integer) AND driven by a
        // WHILE before a numeric FOR loop. The FOR header must reuse the
        // method-level `i` (`for (i = ...)`) — javac forbids shadowing a local
        // with the for-init variable (`for (int i = ...)` after `Integer i`).
        let mut proc = empty_proc();
        proc.local_vars.insert("i".into(), "Integer".into());
        let mut ctx = empty_stmt_ctx();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::For(ogsql_parser::ast::Spanned {
            node: ogsql_parser::ast::plpgsql::PlForStmt {
                label: None,
                variable: "i".into(),
                kind: ogsql_parser::ast::plpgsql::PlForKind::Range {
                    low: ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(1)),
                    high: ogsql_parser::ast::Expr::ColumnRef(vec!["t_res".into(), "count".into()]),
                    step: None,
                    reverse: false,
                },
                body: vec![],
                end_label: None,
            },
            span: None,
        });
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        let header = proc.java_logic_lines.iter().find(|l| l.starts_with("for ")).unwrap();
        assert!(
            !header.contains("for (int i = "),
            "pre-declared counter must not redeclare in for-header, got: {}",
            header
        );
        assert!(header.contains("for (i = 1;"), "pre-declared counter must reuse method var, got: {}", header);
        // The declared type must not be clobbered to primitive int.
        assert_eq!(proc.local_vars.get("i").map(|s| s.as_str()), Some("Integer"));
    }

    #[test]
    fn test_process_null() {
        let mut proc = empty_proc();
        let mut ctx = empty_stmt_ctx();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Null;
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        assert!(proc.java_logic_lines.is_empty());
    }

    #[test]
    fn test_process_return_no_value() {
        let mut proc = empty_proc();
        let mut ctx = empty_stmt_ctx();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Return { expression: None };
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        assert_eq!(proc.java_logic_lines[0], "return;");
    }

    #[test]
    fn test_process_commit() {
        let mut proc = empty_proc();
        let mut ctx = empty_stmt_ctx();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Commit { and_chain: false };
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        assert_eq!(proc.java_logic_lines[0], "// COMMIT;");
    }

    #[test]
    fn test_process_raise_exception() {
        use ogsql_parser::ast::Spanned;
        let mut proc = empty_proc();
        let mut ctx = empty_stmt_ctx();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Raise(Spanned::without_span(
            ogsql_parser::ast::plpgsql::PlRaiseStmt {
                level: Some(ogsql_parser::ast::plpgsql::RaiseLevel::Exception),
                message: Some("test error".into()),
                params: Vec::new(),
                options: Vec::new(),
                condname: None,
                sqlstate: None,
            },
        ));
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        assert!(proc.java_logic_lines[0].contains("throw new BusinessException"));
    }

    #[test]
    fn test_process_raise_notice() {
        use ogsql_parser::ast::Spanned;
        let mut proc = empty_proc();
        let mut ctx = empty_stmt_ctx();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Raise(Spanned::without_span(
            ogsql_parser::ast::plpgsql::PlRaiseStmt {
                level: Some(ogsql_parser::ast::plpgsql::RaiseLevel::Notice),
                message: Some("info msg".into()),
                params: Vec::new(),
                options: Vec::new(),
                condname: None,
                sqlstate: None,
            },
        ));
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        assert!(proc.java_logic_lines[0].contains("log.info"));
    }

    #[test]
    fn test_process_raise_application_error() {
        use ogsql_parser::ast::Spanned;
        let mut proc = empty_proc();
        let mut ctx = empty_stmt_ctx();
        let call = ogsql_parser::ast::plpgsql::PlProcedureCall {
            name: vec![ogsql_parser::ast::Ident::new("raise_application_error")],
            arguments: vec![
                ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(-20030)),
                ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(
                    "p_mode must be REPLACE or SWAP".into(),
                )),
            ],
            builtin: None,
        };
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::ProcedureCall(Spanned::without_span(call));
        process_statement(&stmt, &mut proc, &mut ctx).unwrap();
        assert_eq!(proc.java_logic_lines.len(), 1);
        assert!(proc.java_logic_lines[0].contains("throw new BusinessException"));
        assert!(proc.java_logic_lines[0].contains("p_mode must be REPLACE or SWAP"));
        assert!(ctx.unresolved_calls.is_empty());
    }
}

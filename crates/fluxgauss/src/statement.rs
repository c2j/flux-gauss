use std::collections::HashMap;
use std::sync::OnceLock;
use regex::Regex;
use crate::context::StatementContext;
use crate::types::{ConversionError, DmlType, DmlStatement, Parameter, ProcedureInfo, ServiceCall};

fn out_param_set_expr(var_java: &str, method_id: &str, args: &str, proc: &ProcedureInfo) -> String {
    let base_name = if var_java.contains('.') {
        var_java.split('.').next().unwrap()
    } else {
        var_java
    };
    // var_java is camelCase (e.g. "pFinalBal") but proc.parameters[].name is snake_case (e.g. "p_final_bal")
    // so match by normalizing both to a comparable form
    let param_type = proc.parameters.iter()
        .find(|p| p.is_out() && (
            p.name == base_name ||
            snake_to_camel(&p.name) == base_name
        ))
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
use crate::naming::{snake_to_camel, snake_to_pascal, package_to_classname, java_method_name};
use crate::type_map::{sql_type_to_jdbc, java_type_to_jdbc};

fn cast_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\s*::\s*(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b").unwrap()
    })
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

fn is_unreachable_after_terminal(java_logic_lines: &[String]) -> bool {
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
        depth += closes - opens;  // reverse: } opens scope, { closes it
        if depth <= 0 {
            if t.starts_with("return") || t.starts_with("throw") {
                return true;
            }
            if t.starts_with("break") {
                return false;
            }
            if depth < 0 {
                break;  // exited current block — unreachability doesn't cross blocks
            }
        }
    }
    false
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

fn push_logic_line(proc: &mut ProcedureInfo, line: String) {
    let _trimmed_line = line.trim_start();
    if is_control_structure_line(&line) {
        proc.java_logic_lines.push(line);
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
    proc.java_logic_lines.push(line);
}

fn strip_out_param_get(java: &str, proc: &ProcedureInfo) -> String {
    if java.ends_with(".get()") {
        let base = &java[..java.len() - 6];
        let camel = crate::naming::snake_to_camel;
        for p in &proc.parameters {
            if p.is_out() && camel(&p.name) == base {
                return base.to_string();
            }
        }
    }
    java.to_string()
}

fn dml_method_name(dml_type: &str, proc_name: &str, counter: &mut HashMap<String, usize>) -> String {
    let key = format!("{}_{}", dml_type, proc_name);
    let n = counter.entry(key.clone()).or_insert(0);
    let suffix = if *n > 0 { format!("_{}", n) } else { String::new() };
    *n += 1;
    format!("{}{}{}", dml_type, snake_to_pascal(proc_name), suffix)
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

                let into_fields: Vec<&str> = into_text.split(',')
                    .map(|f| f.trim())
                    .map(|f| {
                        let dot_pos = f.find('.');
                        if let Some(dp) = dot_pos { f[dp + 1..].trim() } else { f.trim() }
                    })
                    .collect();

                if let Some(sel_caps) = select_capture_regex().captures(before_into) {
                    let select_list = sel_caps.get(1).unwrap().as_str();
                    let columns: Vec<&str> = select_list.split(',').map(|c| c.trim()).collect();

                    if columns.len() == into_fields.len() {
                        let needs_alias = columns.iter().zip(into_fields.iter()).any(|(c, f)| c != f);
                        if needs_alias {
                            let aliased: Vec<String> = columns.iter().zip(into_fields.iter())
                                .map(|(col, field)| {
                                    if col == field { col.to_string() } else { format!("{} AS {}", col, field) }
                                })
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
    match table_ref {
        TableRef::Table { name, .. } => {
            let table_name = name.last().map(|s| s.clone()).unwrap_or_default();
            if !table_name.is_empty() {
                proc.table_refs.insert(table_name);
            }
        }
        _ => {}
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
            Some(name[0].clone())
        }
        _ => None
    }
}

/// Extract (parent_var, field_name) from dotted expressions like `v_result.emp_id`.
/// Returns None for non-dotted or unsupported expressions.
fn extract_dotted_ref_from_expr(expr: &ogsql_parser::ast::Expr) -> Option<(String, String)> {
    match expr {
        ogsql_parser::ast::Expr::ColumnRef(name) | ogsql_parser::ast::Expr::PlVariable(name) if name.len() == 2 => {
            Some((name[0].clone(), name[1].clone()))
        }
        _ => None
    }
}

fn process_execute_stmt(
    execute: &ogsql_parser::ast::plpgsql::PlExecuteStmt,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    use ogsql_parser::ast::Expr;
    use ogsql_parser::ast::plpgsql::PlUsingMode;

    if let Some(parsed_query) = &execute.parsed_query {
        let formatter = ogsql_parser::SqlFormatter::new();
        let sql_text = formatter.format_statement(parsed_query);
        let upper_sql = sql_text.trim_start().to_uppercase();

        if upper_sql.starts_with("SAVEPOINT") {
            let sp_name = sql_text.trim()["SAVEPOINT".len()..].trim();
            push_logic_line(proc, format!("// SAVEPOINT {} — handled via JDBC Connection.setSavepoint() in @Transactional context", sp_name));
            return;
        }
        if upper_sql.starts_with("ROLLBACK TO SAVEPOINT") {
            let sp_name = sql_text.trim()["ROLLBACK TO SAVEPOINT".len()..].trim();
            push_logic_line(proc, format!("// ROLLBACK TO SAVEPOINT {} — handled via JDBC Connection.rollback(Savepoint) in @Transactional context", sp_name));
            return;
        }
        if upper_sql.starts_with("RELEASE SAVEPOINT") {
            let sp_name = sql_text.trim()["RELEASE SAVEPOINT".len()..].trim();
            push_logic_line(proc, format!("// RELEASE SAVEPOINT {} — not needed in Spring @Transactional context", sp_name));
            return;
        }

        let dml_type = detect_dml_type(&sql_text);

        if dml_type == Some(DmlType::Select) && execute.into_targets.is_empty() {
            push_logic_line(proc, format!("// PERFORM: {};", sql_text.replace('\n', " ")));
            return;
        }

        let mut clean_sql = sql_text.clone();
        for p in &proc.parameters {
            let jn = snake_to_camel(&p.name);
            let jdbc = sql_type_to_jdbc(&p.sql_type);
            let java = &p.java_type;
            let placeholder = match (jdbc, java) {
                (Some(j), jt) if !jt.is_empty() => format!("#{{{}}}", jn),
                _ => format!("#{{{}}}", jn),
            };
            let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
            clean_sql = re.replace_all(&clean_sql, placeholder.as_str()).to_string();
        }
        for (var_name, var_java_type) in &proc.local_vars {
            let jn = snake_to_camel(var_name);
            let is_map = var_java_type.contains("Map<");
            if is_map {
                let dotted_re = regex::Regex::new(&format!(
                    r"(?i)\b{}\s*\.\s*(\w+)", regex::escape(var_name)
                )).unwrap();
                clean_sql = dotted_re.replace_all(&clean_sql, |caps: &regex::Captures| {
                    let field = &caps[1];
                    let field_camel = snake_to_camel(field);
                    format!("#{{{}.{}}}", jn, field_camel)
                }).to_string();
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
                    if let Some(var_java_type) = proc.local_vars.get(&arg_name) {
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
                clean_sql = re.replace_all(&clean_sql, |caps: &regex::Captures| {
                    format!("{}{}", placeholder, &caps[1])
                }).to_string();
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
                    if let Some(var_java_type) = proc.local_vars.get(&arg_name) {
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
        let method_id = dml_method_name(
            match dml_type {
                DmlType::Select => "select",
                DmlType::Insert => "insert",
                DmlType::Update => "update",
                DmlType::Delete => "delete",
            },
            &proc.proc_name,
            &mut ctx.dml_counter,
        );
        let args = build_mapper_call_args(proc);

        if !execute.into_targets.is_empty() {
            let var_names: Vec<String> = execute.into_targets.iter().filter_map(|t| extract_var_name_from_expr(t)).collect();
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
                        parameter_types: Default::default(),
                        optional_filters: Vec::new(),
                        returns_list: false,
                        extra_params: Vec::new(),
                    });
                    push_logic_line(proc, out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc));
                } else {
                    let java_type = proc.local_vars.get(var_name)
                        .cloned()
                        .unwrap_or_else(|| "Object".to_string());
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: Some(java_type.clone()),
                        parameter_types: Default::default(),
                        optional_filters: Vec::new(),
                        returns_list: false,
                        extra_params: Vec::new(),
                    });
                    push_logic_line(proc, format!("{} = mapper.{}({});", var_java, method_id, args));
                }
            } else {
                let var_name = next_select_var_name(proc);
                proc.dml_statements.push(DmlStatement {
                    sql_type: dml_type,
                    method_id: method_id.clone(),
                    sql_text: clean_sql,
                    result_type: Some("Map<String, Object>".to_string()),
                    parameter_types: Default::default(),
                    optional_filters: Vec::new(),
                    returns_list: false,
                    extra_params: Vec::new(),
                });
                push_logic_line(proc, format!("Map<String, Object> {} = mapper.{}({});", var_name, method_id, args));
                proc.imports.insert("import java.util.Map;".to_string());

                for target in &execute.into_targets {
                    if let Some(field_name) = extract_var_name_from_expr(target) {
                        let field_java = snake_to_camel(&field_name);
                        let field_type = proc.local_vars.get(&field_name)
                            .cloned()
                            .unwrap_or_else(|| "Object".to_string());
                        let cast = if field_type != "Object" {
                            format!("({}) ", field_type)
                        } else {
                            String::new()
                        };
                        push_logic_line(proc, format!("{} = {}_row.get(\"{}\");", field_java, cast, field_name));
                    } else if let Some((parent, field)) = extract_dotted_ref_from_expr(target) {
                        let parent_java = snake_to_camel(&parent);
                        let field_java = snake_to_camel(&field);
                        let field_type = proc.local_vars.get(&parent)
                            .cloned()
                            .unwrap_or_else(|| "Object".to_string());
                        let cast = if field_type != "Object" && field_type != "Map<String, Object>" {
                            format!("({}) ", field_type)
                        } else {
                            String::new()
                        };
                        push_logic_line(proc, format!("{}.put(\"{}\", {}{}.get(\"{}\"));", parent_java, field_java, cast, var_name, field));
                    }
                }
            }
        } else {
            let result_type = if matches!(dml_type, DmlType::Select) {
                Some("Map<String, Object>".to_string())
            } else {
                None
            };
            proc.dml_statements.push(DmlStatement {
                sql_type: dml_type,
                method_id: method_id.clone(),
                sql_text: clean_sql,
                result_type,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
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
                                parameter_types: Default::default(),
                                optional_filters: Vec::new(),
                                returns_list: false,
                                extra_params: Vec::new(),
                            });
                            push_logic_line(proc, out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc));
                        } else {
                            let var_java = snake_to_camel(var_name);
                            let java_type = proc.local_vars.get(var_name)
                                .cloned()
                                .unwrap_or_else(|| "Object".to_string());
                            proc.dml_statements.push(DmlStatement {
                                sql_type: dml_type,
                                method_id: method_id.clone(),
                                sql_text: clean_sql,
                                result_type: Some(java_type.clone()),
                                parameter_types: Default::default(),
                                optional_filters: Vec::new(),
                                returns_list: false,
                                extra_params: Vec::new(),
                            });
                            push_logic_line(proc, format!("{} = mapper.{}({});", var_java, method_id, args));
                        }
                    } else if into_vars_count > 1 {
                        let var_name = next_select_var_name(proc);
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            parameter_types: Default::default(),
                            optional_filters: Vec::new(),
                            returns_list: false,
                            extra_params: Vec::new(),
                        });
                        push_logic_line(proc, format!("Map<String, Object> {} = mapper.{}({});", var_name, method_id, args));
                        proc.imports.insert("import java.util.Map;".to_string());
                    } else {
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            parameter_types: Default::default(),
                            optional_filters: Vec::new(),
                            returns_list: true,
                            extra_params: Vec::new(),
                        });
                        let var_name = next_result_var_name(proc);
                        push_logic_line(proc, format!("List<Map<String, Object>> {} = mapper.{}({});", var_name, method_id, args));
                        proc.imports.insert("import java.util.List;".to_string());
                        proc.imports.insert("import java.util.Map;".to_string());
                    }
                } else {
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: None,
                        parameter_types: Default::default(),
                        optional_filters: Vec::new(),
                        returns_list: false,
                        extra_params: Vec::new(),
                    });
                    push_logic_line(proc, format!("mapper.{}({});", method_id, args));
                }
            } else {
                push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
            }
        }
        Expr::ColumnRef(name) | Expr::PlVariable(name) if name.len() == 1 => {
            let var_name = &name[0];
            push_logic_line(proc, format!("// TODO: EXECUTE {} — could not resolve SQL string", var_name));
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
    use ogsql_parser::ast::Statement;
    use ogsql_parser::ast::SelectTarget;
    use ogsql_parser::ast::Expr;
    match statement {
        Statement::Select(select_stmt) => {
            let into_targets = &select_stmt.node.into_targets;
            let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter);
            let clean_sql = clean_sql_for_mapper(sql_text, DmlType::Select);
            let args = build_mapper_call_args(proc);

            if into_targets.is_some() && into_targets.as_ref().map_or(false, |t| !t.is_empty()) {
                let targets = into_targets.as_ref().unwrap();

                // Extract simple variable names from INTO targets
                let var_names: Vec<String> = targets.iter().filter_map(|t| {
                    if let SelectTarget::Expr(expr, _) = t {
                        match expr {
                            Expr::ColumnRef(name) | Expr::PlVariable(name) if name.len() == 1 => {
                                return Some(name[0].clone());
                            }
                            _ => {}
                        }
                    }
                    None
                }).collect();

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
                        let java_type = proc.local_vars.get(var_name)
                            .cloned()
                            .unwrap_or_else(|| "Object".to_string());
                        let rt = java_type.clone();
                        let line = format!("{} = mapper.{}({});", var_java, method_id, args);
                        (rt, line, String::new())
                    }
                } else {
                    // Multiple INTO targets or qualified names — use Map
                    let row_var = next_select_var_name(proc);
                    let line = format!("Map<String, Object> {} = mapper.{}({});", row_var, method_id, args);
                    proc.imports.insert("import java.util.Map;".to_string());
                    ("Map<String, Object>".to_string(), line, row_var)
                };

                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text: clean_sql,
                    result_type: Some(result_type),
                    parameter_types: Default::default(),
                    optional_filters: Vec::new(),
                    returns_list: false,
                    extra_params: Vec::new(),
                });
                push_logic_line(proc, java_line);

                // Handle dotted INTO targets like v_result.emp_id → vResult.put("empId", _row.get("empId"))
                if var_names.len() != targets.len() && !row_var_name.is_empty() {
                    for target in targets {
                        if let SelectTarget::Expr(expr, _) = target {
                            if let Some((parent, field)) = extract_dotted_ref_from_expr(expr) {
                                let parent_java = snake_to_camel(&parent);
                                let field_java = snake_to_camel(&field);
                                push_logic_line(proc, format!("{}.put(\"{}\", {}.get(\"{}\"));", parent_java, field_java, row_var_name, field));
                            }
                        }
                    }
                }
            } else {
                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text: sql_text.to_string(),
                    result_type: Some("Map<String, Object>".to_string()),
                    parameter_types: Default::default(),
                    optional_filters: Vec::new(),
                    returns_list: true,
                    extra_params: Vec::new(),
                });
                let var_name = next_result_var_name(proc);
                push_logic_line(proc, format!("List<Map<String, Object>> {} = mapper.{}({});", var_name, method_id, args));
                proc.imports.insert("import java.util.List;".to_string());
                proc.imports.insert("import java.util.Map;".to_string());
            }
            for table_ref in &select_stmt.node.from {
                extract_table_ref(table_ref, proc);
            }
        }
        Statement::Insert(_insert_stmt) => {
            let method_id = dml_method_name("insert", &proc.proc_name, &mut ctx.dml_counter);
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Insert,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        Statement::Update(_update_stmt) => {
            let method_id = dml_method_name("update", &proc.proc_name, &mut ctx.dml_counter);
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Update,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        Statement::Delete(_delete_stmt) => {
            let method_id = dml_method_name("delete", &proc.proc_name, &mut ctx.dml_counter);
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Delete,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
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
        for (_, vtype) in &proc.local_vars {
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
                _ => "unknown",
            };
        }
    }
    for p in &proc.parameters {
        let camel = crate::naming::snake_to_camel(&p.name);
        if trimmed == camel {
            return match p.java_type.as_str() {
                "long" | "Long" => "long",
                "String" => "String",
                _ => "unknown",
            };
        }
    }
    "unknown"
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
        return;
    };

    let method = java_method_name(func);
    let raw_args: Vec<String> = call.arguments.iter()
        .map(|a| crate::expr::expr_to_java(a, proc))
        .collect();

    // Try to resolve package from the hint
    let mut matched_pkg = resolve_package_name(pkg_hint, ctx.summaries);

    if name_parts.len() == 1 {
        if matched_pkg.is_none() || matched_pkg.as_ref().map(|p| p.to_lowercase()) == Some(proc.package.to_lowercase()) {
            let found_in_current = ctx.summaries.get(&proc.package)
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
        let target_summary = ctx.summaries.get(matched_pkg_name)
            .or_else(|| {
                let lower = matched_pkg_name.to_lowercase();
                ctx.summaries.iter().find(|(k, _)| k.to_lowercase() == lower).map(|(_, v)| v)
            });
        let target_proc = target_summary.and_then(|s| s.find_procedure(func));

        let args: Vec<String> = raw_args.iter().enumerate().map(|(i, raw)| {
            let mut arg = strip_out_param_get(raw, proc);
            if let Some(tp) = &target_proc {
                if i < tp.parameters.len() {
                    let param = &tp.parameters[i];
                    let target_type = &param.java_type;
                    if param.is_out() {
                        let arg_trimmed = arg.trim();
                        if !arg_trimmed.contains('.') && !arg_trimmed.contains('(') {
                            for (vname, _) in &proc.local_vars {
                                let vname_camel = crate::naming::snake_to_camel(vname);
                                if vname_camel == arg_trimmed {
                                    proc.out_local_vars.insert(vname.clone(), target_type.clone());
                                    break;
                                }
                            }
                        }
                    } else {
                        let arg_type_inferred = infer_arg_type(&arg, proc);
                        let target_is_long = target_type == "long" || target_type == "Long";
                        let target_is_string = target_type == "String";
                        if target_is_string && arg_type_inferred == "long" {
                            arg = format!("String.valueOf({})", arg);
                        } else if target_is_long && arg_type_inferred == "String" {
                            arg = format!("Long.parseLong(String.valueOf({}))", arg);
                        }
                    }
                }
            }
            arg
        }).collect();
        let args_java = args.join(", ");

        if is_self_call {
            let found = ctx.summaries.get(&proc.package)
                .or_else(|| {
                    let proc_pkg_lower = proc.package.to_lowercase();
                    ctx.summaries.iter().find(|(k, _)| k.to_lowercase() == proc_pkg_lower).map(|(_, v)| v)
                })
                .map(|s| s.find_procedure(func).is_some())
                .unwrap_or(true);
            if found {
                push_logic_line(proc, format!("this.{}({});", method, args_java));
            } else {
                push_logic_line(proc, format!("// CALL {}({}) — procedure not found in current package", method, args_java));
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
        let args_fallback: Vec<String> = call.arguments.iter()
            .map(|a| crate::expr::expr_to_java(a, proc))
            .collect();
        let full_name = name_parts.join(".");
        push_logic_line(proc, format!("// CALL {}({})", full_name, args_fallback.join(", ")));
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
            let args: Vec<String> = args.iter()
                .map(|a| {
                    let java = crate::expr::expr_to_java(a, proc);
                    strip_out_param_get(&java, proc)
                })
                .collect();
            let args_java = args.join(", ");

            if let Some(matched_pkg) = resolve_package_name(pkg, ctx.summaries) {
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
    if let Some(ty) = proc.local_vars.get(sel_var) {
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

pub fn process_statement(
    stmt: &ogsql_parser::ast::plpgsql::PlStatement,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) -> Result<(), ConversionError> {
    use ogsql_parser::ast::plpgsql::PlStatement;
    match stmt {
        PlStatement::Assignment { target, expression } => {
            let line = crate::expr::assignment_to_java(target, expression, proc);
            push_logic_line(proc, line);
            Ok(())
        }
        PlStatement::Return { expression } => {
            if let Some(expr) = expression {
                let val = crate::expr::expr_to_java(expr, proc);
                push_logic_line(proc, format!("return {};", val));
            } else if proc.return_type.as_ref().map_or(false, |rt| rt != "void") {
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
            push_logic_line(proc, format!("// return next: {};", val));
            Ok(())
        }
        PlStatement::ReturnQuery(_) => {
            push_logic_line(proc, "// return query;".into());
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
                } else {
                    push_logic_line(proc, format!("{};", val));
                }
            } else {
                push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
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
            let formatted_msg = raw_msg.replace('%', "{}");
            let params_java: Vec<String> = raise_stmt.node.params.iter()
                .map(|p| crate::expr::expr_to_java(p, proc))
                .collect();
            let params_str = if params_java.is_empty() {
                String::new()
            } else {
                format!(", {}", params_java.join(", "))
            };
            match level_str {
                "exception" => {
                    if params_java.is_empty() {
                        push_logic_line(proc, format!("throw new BusinessException(\"{}\");", formatted_msg));
                    } else {
                        push_logic_line(proc, format!("throw new BusinessException(String.format(\"{}\"{}));", formatted_msg, params_str));
                    }
                }
                "notice" | "info" => {
                    push_logic_line(proc, format!("log.info(\"{}\"{});", formatted_msg, params_str));
                }
                "debug" => {
                    push_logic_line(proc, format!("log.debug(\"{}\"{});", formatted_msg, params_str));
                }
                "warning" => {
                    push_logic_line(proc, format!("log.warn(\"{}\"{});", formatted_msg, params_str));
                }
                _ => {
                    push_logic_line(proc, format!("log.info(\"{}\"{});", formatted_msg, params_str));
                }
            }
            Ok(())
        }
        PlStatement::If(if_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
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
            let selector_java = case_stmt.node.expression.as_ref()
                .map(|expr| crate::expr::expr_to_java(expr, proc));
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
                crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new());
            }
            let has_exceptions = block_stmt.node.exception_block.is_some();
            if has_exceptions {
                push_logic_line(proc, "try {".into());
            }
            for s in &block_stmt.node.body {
                process_statement(s, proc, ctx)?;
            }
            if let Some(exc_block) = &block_stmt.node.exception_block {
                for handler in &exc_block.handlers {
                    push_logic_line(proc, "} catch (Exception e) {".into());
                    for s in &handler.statements {
                        process_statement(s, proc, ctx)?;
                    }
                }
            }
            if has_exceptions {
                push_logic_line(proc, "}".into());
            }
            Ok(())
        }
        PlStatement::Loop(loop_stmt) => {
            if let Some(label) = &loop_stmt.node.label {
                push_logic_line(proc, format!("{}: while (true) {{", label));
            } else {
                push_logic_line(proc, "while (true) {".into());
            }
            for s in &loop_stmt.node.body {
                process_statement(s, proc, ctx)?;
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
                    let iter_var = var.clone();
                    let already_declared = proc.local_vars.contains_key(&for_stmt.node.variable);
                    let step_code = match step {
                        Some(s) => {
                            let s_val = crate::expr::expr_to_java(s, proc);
                            format!("{} += {}", iter_var, s_val)
                        }
                        None => format!("{}++", iter_var),
                    };
                    if already_declared {
                        if *reverse {
                            push_logic_line(proc, format!("{} = {}; while ({} >= {}) {{ {}--;",
                                iter_var, hi, iter_var, lo, iter_var));
                        } else {
                            push_logic_line(proc, format!("{} = {}; while ({} <= {}) {{ {};",
                                iter_var, lo, iter_var, hi, step_code));
                        }
                    } else {
                        if *reverse {
                            push_logic_line(proc, format!("for (int {} = {}; {} >= {}; {}--) {{",
                                iter_var, hi, iter_var, lo, iter_var));
                        } else {
                            push_logic_line(proc, format!("for (int {} = {}; {} <= {}; {}) {{",
                                iter_var, lo, iter_var, hi, step_code));
                        }
                    }
                }
                ogsql_parser::ast::plpgsql::PlForKind::Query { query, .. } => {
                    let clean_sql = query.replace('\n', " ");
                    push_logic_line(proc, format!("// for {} in query: {}", var, &clean_sql));
                    proc.local_vars.insert(for_stmt.node.variable.clone(), "Map<String, Object>".into());
                    proc.local_var_defaults.remove(&for_stmt.node.variable);
                    proc.imports.insert("import java.util.List;".to_string());
                    proc.imports.insert("import java.util.Map;".to_string());
                    proc.imports.insert("import java.util.ArrayList;".to_string());

                    let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter);
                    let args = build_mapper_call_args(proc);

                    proc.dml_statements.push(DmlStatement {
                        sql_type: DmlType::Select,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: Some("Map<String, Object>".to_string()),
                        parameter_types: Default::default(),
                        optional_filters: Vec::new(),
                        returns_list: true,
                        extra_params: Vec::new(),
                    });

                    let list_var = format!("{}List", var);
                    push_logic_line(proc, format!("List<Map<String, Object>> {} = mapper.{}({});", list_var, method_id, args));
                    push_logic_line(proc, format!("if ({} == null) {} = new ArrayList<>();", list_var, list_var));
                    push_logic_line(proc, format!("for (Map<String, Object> {} : {}) {{", var, list_var));
                }
                ogsql_parser::ast::plpgsql::PlForKind::Cursor { cursor_name, .. } => {
                    let cursor_java = crate::expr::expr_to_java(cursor_name, proc);
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
                        let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter);
                        let args = build_mapper_call_args(proc);

                        proc.dml_statements.push(DmlStatement {
                            sql_type: DmlType::Select,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            parameter_types: Default::default(),
                            optional_filters: Vec::new(),
                            returns_list: true,
                            extra_params: Vec::new(),
                        });

                        let list_var = format!("{}List", var);
                        push_logic_line(proc, format!("List<Map<String, Object>> {} = mapper.{}({});", list_var, method_id, args));
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
        PlStatement::Open(_) => {
            push_logic_line(proc, "// OPEN cursor;".into());
            Ok(())
        }
        PlStatement::Fetch(_) => {
            push_logic_line(proc, "// FETCH cursor;".into());
            Ok(())
        }
        PlStatement::Close { cursor } => {
            let cur = crate::expr::expr_to_java(cursor, proc);
            push_logic_line(proc, format!("// CLOSE {};", cur));
            Ok(())
        }
        PlStatement::Move { cursor, .. } => {
            let cur = crate::expr::expr_to_java(cursor, proc);
            push_logic_line(proc, format!("// MOVE {};", cur));
            Ok(())
        }
        PlStatement::GetDiagnostics(_) => {
            push_logic_line(proc, "// GET DIAGNOSTICS;".into());
            Ok(())
        }
        PlStatement::Commit { .. } => {
            push_logic_line(proc, "// COMMIT;".into());
            Ok(())
        }
        PlStatement::Rollback { to_savepoint, .. } => {
            if let Some(sp) = to_savepoint {
                push_logic_line(proc, format!("// ROLLBACK TO {};", sp));
            } else {
                proc.imports
                    .insert("import org.springframework.transaction.interceptor.TransactionAspectSupport;".into());
                push_logic_line(proc,
                    "TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();".into(),
                );
            }
            Ok(())
        }
        PlStatement::Savepoint { name } => {
            push_logic_line(proc, format!("// SAVEPOINT {};", name));
            Ok(())
        }
        PlStatement::ReleaseSavepoint { name } => {
            push_logic_line(proc, format!("// RELEASE SAVEPOINT {};", name));
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
                                parameter_types: Default::default(),
                                optional_filters: Vec::new(),
                                returns_list: false,
                                extra_params: Vec::new(),
                            });
                            push_logic_line(proc, out_param_set_expr(&var_java, method_id.as_str(), args.as_str(), proc));
                        } else {
                            let var_java = snake_to_camel(var_name);
                            let java_type = proc.local_vars.get(var_name)
                                .cloned()
                                .unwrap_or_else(|| "Object".to_string());

                            proc.dml_statements.push(DmlStatement {
                                sql_type: dml_type,
                                method_id: method_id.clone(),
                                sql_text: clean_sql,
                                result_type: Some(java_type.clone()),
                                parameter_types: Default::default(),
                                optional_filters: Vec::new(),
                                returns_list: false,
                                extra_params: Vec::new(),
                            });
                            push_logic_line(proc, format!("{} = mapper.{}({});", var_java, method_id, args));
                        }
                    } else if into_vars_count > 1 {
                        let var_name = next_select_var_name(proc);
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            parameter_types: Default::default(),
                            optional_filters: Vec::new(),
                            returns_list: false,
                            extra_params: Vec::new(),
                        });
                        push_logic_line(proc, format!("Map<String, Object> {} = mapper.{}({});", var_name, method_id, args));
                        proc.imports.insert("import java.util.Map;".to_string());
                    } else {
                        proc.dml_statements.push(DmlStatement {
                            sql_type: dml_type,
                            method_id: method_id.clone(),
                            sql_text: clean_sql,
                            result_type: Some("Map<String, Object>".to_string()),
                            parameter_types: Default::default(),
                            optional_filters: Vec::new(),
                            returns_list: true,
                            extra_params: Vec::new(),
                        });
                        let var_name = next_result_var_name(proc);
                        push_logic_line(proc, format!("List<Map<String, Object>> {} = mapper.{}({});", var_name, method_id, args));
                        proc.imports.insert("import java.util.List;".to_string());
                        proc.imports.insert("import java.util.Map;".to_string());
                    }
                } else {
                    proc.dml_statements.push(DmlStatement {
                        sql_type: dml_type,
                        method_id: method_id.clone(),
                        sql_text: clean_sql,
                        result_type: None,
                        parameter_types: Default::default(),
                        optional_filters: Vec::new(),
                        returns_list: false,
                        extra_params: Vec::new(),
                    });
                    push_logic_line(proc, format!("mapper.{}({});", method_id, args));
                }
            } else {
                push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
            }
            Ok(())
        }
        PlStatement::SqlStatement { sql_text, statement, .. } => {
            process_sql_statement(statement, &sql_text, proc, ctx);
            Ok(())
        }
        PlStatement::ForAll(_) => {
            push_logic_line(proc, "// FORALL;".into());
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

    fn empty_proc() -> ProcedureInfo {
        ProcedureInfo::new("pkg.test".into(), "pkg".into(), "test".into())
    }

    fn empty_stmt_ctx() -> StatementContext<'static> {
        StatementContext {
            summaries: Box::leak(Box::new(HashMap::new())),
            dml_counter: HashMap::new(),
        }
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
}

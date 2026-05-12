use std::collections::BTreeSet;
use std::path::Path;

use crate::generate::writer::CodeWriter;
use crate::naming::{package_to_classname, snake_to_camel};
use crate::type_map::{java_type_to_jdbc, sql_type_to_java};
use crate::types::{DmlType, PackageInfo, Parameter};

static IDENTIFIER_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static CLEAN_SQL_WS_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static INTERVAL_PAREN_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static INTERVAL_CONCAT_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static OUTER_JOIN_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static NVL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static ADD_MONTHS_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static TO_CHAR_NOW_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static EMPTY_BLOB_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static DATE_CAST_EQ_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static DATE_CAST_TRAILING_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static DATE_FUNC_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static DECODE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static DOT_ACCESS_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static AS_PARAM_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static GET_SYS_DATE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static SYSDATE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static NEXTVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static CURRVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static AS_NEXTVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static AS_CURRVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static RESERVED_COL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static DOLLAR_PARAM_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static PG_CAST_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();

pub fn write_mapper_interface(
    base_path: &Path,
    pkg: &PackageInfo,
    base_package: &str,
) -> std::io::Result<String> {
    let java_pkg = base_package;
    let mapper_dir = base_path.join(format!("src/main/java/{}/mapper", java_pkg.replace('.', "/")));
    let class_name = format!("{}Mapper", package_to_classname(&pkg.package_name));

    let mut imports: BTreeSet<String> = BTreeSet::new();
    let mut methods = Vec::new();

    for proc in &pkg.procedures {
        for dml in &proc.dml_statements {
            let mut dml_mut = dml.clone();
            promote_out_params_to_mapper(&proc.parameters, &dml.sql_text, &mut dml_mut.extra_params);
            let method_sig = build_mapper_method(proc, &dml_mut, &mut imports, &pkg.package_vars);
            methods.push(method_sig);
        }
    }

    if methods.is_empty() {
        methods.push(format!(
            "// No direct DML operations for {}",
            pkg.package_name
        ));
    }

    let mut w = CodeWriter::new();
    w.line(&format!("package {}.mapper;", java_pkg));
    w.blank();
    for imp in &imports {
        w.line(imp);
    }
    w.line("import org.apache.ibatis.annotations.*;");
    w.blank();
    w.line("@Mapper");
    w.line(&format!("public interface {} {{", class_name));
    w.blank();
    w.push_indent();
    for method in &methods {
        for line in method.split('\n') {
            w.line(line);
        }
    }
    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(&mapper_dir)?;
    let file_path = mapper_dir.join(format!("{}.java", class_name));
    w.write_to_file(&file_path)?;
    Ok(class_name)
}

fn build_mapper_method(
    proc: &crate::types::ProcedureInfo,
    dml: &crate::types::DmlStatement,
    imports: &mut BTreeSet<String>,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
) -> String {
    let mut params: Vec<String> = Vec::new();

    for p in &proc.parameters {
        if p.is_out() {
            continue;
        }
        let jn = snake_to_camel(&p.name);
        params.push(format!("@Param(\"{}\") {} {}", jn, p.java_type, jn));
        if let Some(imp) = resolve_import(&p.java_type) {
            imports.insert(imp);
        }
    }

    let param_java_names: std::collections::HashSet<String> = proc
        .parameters
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| snake_to_camel(&p.name).to_lowercase())
        .collect();

    for (java_name, java_type) in &dml.extra_params {
        let jn_lower = java_name.to_lowercase();
        if param_java_names.iter().any(|pn| pn == &jn_lower) {
            continue;
        }
        params.push(format!("@Param(\"{}\") {} {}", java_name, java_type, java_name));
        if let Some(imp) = resolve_import(java_type) {
            imports.insert(imp);
        }
    }

    let local_var_refs = extract_local_var_refs(&dml.sql_text, &proc.local_vars, &param_java_names);
    let mut all_param_names: std::collections::HashSet<String> = param_java_names.clone();
    for (jn, _) in &local_var_refs {
        all_param_names.insert(jn.to_lowercase());
    }
    for (java_name, java_type) in &local_var_refs {
        params.push(format!("@Param(\"{}\") {} {}", java_name, java_type, java_name));
        if let Some(imp) = resolve_import(java_type) {
            imports.insert(imp);
        }
    }

    let pkg_var_refs = extract_package_var_refs(&dml.sql_text, package_vars, &all_param_names);
    for (java_name, java_type) in &pkg_var_refs {
        params.push(format!("@Param(\"{}\") {} {}", java_name, java_type, java_name));
        if let Some(imp) = resolve_import(java_type) {
            imports.insert(imp);
        }
    }

    let params_str = params.join(", ");
    let ret = return_type_for_dml(dml, imports);

    let source_info = if !proc.source_file.is_empty() {
        format!("// {}:{} — {}", proc.source_file, proc.source_start_line, proc.name)
    } else {
        String::new()
    };

    let mut lines = Vec::new();
    if !source_info.is_empty() {
        lines.push(source_info);
    }
    lines.push(format!("{} {}({});", ret, dml.method_id, params_str));
    lines.join("\n")
}

fn promote_out_params_to_mapper(
    params: &[crate::types::Parameter],
    sql_text: &str,
    extra_params: &mut Vec<(String, String)>,
) {
    for p in params {
        if !p.is_out() {
            continue;
        }
        let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
        if re.is_match(sql_text) {
            let jn = snake_to_camel(&p.name);
            let already = extra_params.iter().any(|(name, _)| name == &jn);
            if !already {
                extra_params.push((jn, p.java_type.clone()));
            }
        }
    }
}

fn return_type_for_dml(
    dml: &crate::types::DmlStatement,
    imports: &mut BTreeSet<String>,
) -> String {
    match dml.sql_type {
        DmlType::Select => {
            if dml.returns_list {
                imports.insert("import java.util.List;".to_string());
                imports.insert("import java.util.Map;".to_string());
                "List<Map<String, Object>>".to_string()
            } else if let Some(ref rt) = dml.result_type {
                if rt == "Integer" {
                    "Integer".to_string()
                } else if rt != "Map<String, Object>" {
                    if let Some(imp) = resolve_import(rt) {
                        imports.insert(imp);
                    }
                    rt.clone()
                } else {
                    imports.insert("import java.util.Map;".to_string());
                    "Map<String, Object>".to_string()
                }
            } else {
                imports.insert("import java.util.Map;".to_string());
                "Map<String, Object>".to_string()
            }
        }
        DmlType::Insert | DmlType::Update | DmlType::Delete => "int".to_string(),
    }
}

pub fn resolve_import(java_type: &str) -> Option<String> {
    match java_type {
        "BigDecimal" | "java.math.BigDecimal" => Some("import java.math.BigDecimal;".to_string()),
        "Timestamp" | "java.sql.Timestamp" => Some("import java.sql.Timestamp;".to_string()),
        "Date" | "java.sql.Date" => Some("import java.sql.Date;".to_string()),
        "Time" | "java.sql.Time" => Some("import java.sql.Time;".to_string()),
        t if t.starts_with("List<") => Some("import java.util.List;".to_string()),
        t if t.starts_with("Map<") => Some("import java.util.Map;".to_string()),
        t if t.starts_with("AtomicReference<") => {
            Some("import java.util.concurrent.atomic.AtomicReference;".to_string())
        }
        _ => None,
    }
}

pub fn is_simple_java_type(t: &str) -> bool {
    matches!(
        t,
        "int" | "long" | "double" | "float" | "boolean" | "short" | "byte" | "char"
            | "String" | "Integer" | "Long" | "Double" | "Float" | "Boolean"
    )
}

fn extract_local_var_refs(
    sql_text: &str,
    local_vars: &std::collections::HashMap<String, String>,
    param_java_names: &std::collections::HashSet<String>,
) -> Vec<(String, String)> {
    let mut result = Vec::new();
    let re = IDENTIFIER_RE.get_or_init(|| regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap());
    for caps in re.captures_iter(sql_text) {
        let word = caps.get(1).unwrap().as_str();
        let lower = word.to_lowercase();
        // Skip SQL keywords and common non-variable words
        if matches!(lower.as_str(),
            "select" | "from" | "where" | "insert" | "into" | "values" | "update" | "set" |
            "delete" | "and" | "or" | "not" | "null" | "is" | "in" | "between" | "like" |
            "as" | "on" | "join" | "left" | "right" | "inner" | "outer" | "order" | "by" |
            "group" | "having" | "limit" | "offset" | "union" | "all" | "distinct" | "case" |
            "when" | "then" | "else" | "end" | "exists" | "true" | "false" | "asc" | "desc" |
            "current_timestamp" | "current_date" | "current_time" | "now" | "count" | "sum" |
            "avg" | "min" | "max" | "coalesce" | "nvl" | "cast" | "default"
        ) {
            continue;
        }
        if let Some(java_type) = local_vars.get(word) {
            let jn = snake_to_camel(word);
            let jn_lower = jn.to_lowercase();
            if !param_java_names.iter().any(|pn| pn == &jn_lower) {
                result.push((jn, java_type.clone()));
            }
        }
    }
    result.sort_by(|a, b| a.0.cmp(&b.0));
    result.dedup_by(|a, b| a.0 == b.0);
    result
}

fn extract_package_var_refs(
    sql_text: &str,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
    existing_params: &std::collections::HashSet<String>,
) -> Vec<(String, String)> {
    let mut result = Vec::new();
    let re = IDENTIFIER_RE.get_or_init(|| regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap());
    for caps in re.captures_iter(sql_text) {
        let word = caps.get(1).unwrap().as_str();
        let lower = word.to_lowercase();
        if matches!(lower.as_str(),
            "select" | "from" | "where" | "insert" | "into" | "values" | "update" | "set" |
            "delete" | "and" | "or" | "not" | "null" | "is" | "in" | "between" | "like" |
            "as" | "on" | "join" | "left" | "right" | "inner" | "outer" | "order" | "by" |
            "group" | "having" | "limit" | "offset" | "union" | "all" | "distinct" | "case" |
            "when" | "then" | "else" | "end" | "exists" | "true" | "false" | "asc" | "desc" |
            "current_timestamp" | "current_date" | "current_time" | "now" | "count" | "sum" |
            "avg" | "min" | "max" | "coalesce" | "nvl" | "cast" | "default"
        ) {
            continue;
        }
        if let Some(var_info) = package_vars.get(word) {
            let jn = snake_to_camel(word);
            let jn_lower = jn.to_lowercase();
            if !existing_params.iter().any(|pn| pn == &jn_lower) {
                result.push((jn, var_info.java_type.clone()));
            }
        }
    }
    result.sort_by(|a, b| a.0.cmp(&b.0));
    result.dedup_by(|a, b| a.0 == b.0);
    result
}

// ── Mapper XML ──

pub fn write_mapper_xml(
    base_path: &Path,
    pkg: &PackageInfo,
    base_package: &str,
) -> std::io::Result<String> {
    let mapper_dir = base_path.join("src/main/resources/mapper");
    let class_name = format!("{}Mapper", package_to_classname(&pkg.package_name));
    let namespace = format!("{}.mapper.{}", base_package, class_name);

    let mut statements = Vec::new();
    for proc in &pkg.procedures {
        for dml in &proc.dml_statements {
            let stmt_xml = build_mapper_statement(proc, dml, &pkg.package_vars);
            statements.push(stmt_xml);
        }
    }

    let mut w = CodeWriter::new();
    w.line("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
    w.line("<!DOCTYPE mapper PUBLIC \"-//mybatis.org//DTD Mapper 3.0//EN\"");
    w.line("        \"http://mybatis.org/dtd/mybatis-3-mapper.dtd\">");
    w.line(&format!("<mapper namespace=\"{}\">", namespace));
    w.blank();

    for (i, stmt) in statements.iter().enumerate() {
        if i > 0 {
            w.blank();
        }
        for line in stmt.split('\n') {
            w.line(line);
        }
    }

    w.blank();
    w.line("</mapper>");

    std::fs::create_dir_all(&mapper_dir)?;
    let file_path = mapper_dir.join(format!("{}.xml", class_name));
    w.write_to_file(&file_path)?;
    Ok(class_name)
}

fn build_mapper_statement(
    proc: &crate::types::ProcedureInfo,
    dml: &crate::types::DmlStatement,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
) -> String {
    let mut sql = clean_sql(&dml.sql_text);
    if sql.ends_with(';') {
        sql = sql[..sql.len() - 1].to_string();
    }

     sql = replace_cross_package_functions(&sql);
     sql = replace_sequence_refs(&sql);
     sql = fix_postgresql_syntax(&sql);
     if matches!(dml.sql_type, DmlType::Select) {
         sql = fix_select_into_aliases(&sql, &proc.local_vars, package_vars);
     }

     sql = convert_params_to_mybatis(&sql, &proc.parameters, &proc.local_vars, package_vars);

     if matches!(dml.sql_type, DmlType::Select) {
         sql = cleanup_as_param_aliases(&sql);
     }

    if matches!(dml.sql_type, DmlType::Select) && !dml.returns_list {
        if !sql.to_lowercase().contains("limit") {
            sql = format!("{}\n        LIMIT 1", sql.trim_end());
        }
    }

    sql = xml_escape(&sql);

    let tag = dml_type_tag(&dml.sql_type);
    let result_type_attr = build_result_type_attr(dml);
    let params_attrs = build_params_attr(proc, dml);

    let source_info = if !proc.source_file.is_empty() {
        format!(
            "Source: {}:{}-{} — {}.{}",
            proc.source_file, proc.source_start_line, proc.source_end_line, proc.name, dml.method_id
        )
    } else {
        format!("Source: {}.{}", proc.name, dml.method_id)
    };

    let formatted_sql: String = sql.split('\n').map(|l| format!("    {}", l)).collect::<Vec<_>>().join("\n");

    let mut parts = Vec::new();
    parts.push(format!("<!-- {} -->", source_info));
    parts.push(format!("<{} id=\"{}\"{}{}>", tag, dml.method_id, params_attrs, result_type_attr));
    parts.push(formatted_sql);
    parts.push(format!("</{}>", tag));
    parts.join("\n")
}

fn clean_sql(sql: &str) -> String {
    let s = CLEAN_SQL_WS_RE.get_or_init(|| regex::Regex::new(r"\s+").unwrap()).replace_all(sql.trim(), " ");
    s.to_string()
}

fn fix_postgresql_syntax(sql: &str) -> String {
    let mut result = sql.to_string();

    let interval_re = INTERVAL_PAREN_RE.get_or_init(|| regex::Regex::new(
        r"(?i)\)\s*interval\b"
    ).unwrap());
    result = interval_re.replace_all(&result, ")::interval").to_string();

    let interval_re2 = INTERVAL_CONCAT_RE.get_or_init(|| regex::Regex::new(
        r"(?i)(\|\|\s*'[^']+'\s*)interval\b"
    ).unwrap());
    result = interval_re2.replace_all(&result, "${1}::interval").to_string();

    let outer_join_re = OUTER_JOIN_RE.get_or_init(|| regex::Regex::new(
        r"(?i)(\w+)\s*\.\s*(\w+)\s*\(\s*\+\s*\)"
    ).unwrap());
    result = outer_join_re.replace_all(&result, "$1.$2").to_string();

    result = replace_decode_with_case(&result);

    let nvl_re = NVL_RE.get_or_init(|| regex::Regex::new(r"(?i)\bnvl\s*\(").unwrap());
    result = nvl_re.replace_all(&result, "coalesce(").to_string();

    let add_months_re = ADD_MONTHS_RE.get_or_init(|| regex::Regex::new(
        r"(?i)add_months\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)"
    ).unwrap());
    result = add_months_re.replace_all(&result, "($1 + ($2 || ' months')::interval)").to_string();

    let to_char_re = TO_CHAR_NOW_RE.get_or_init(|| regex::Regex::new(
        r"(?i)to_char\s*\(\s*now\s*\(\s*\)\s*,\s*'([^']+)'\s*\)"
    ).unwrap());
    result = to_char_re.replace_all(&result, "to_char(now(), '$1')").to_string();

    let empty_blob_re = EMPTY_BLOB_RE.get_or_init(|| regex::Regex::new(r"(?i)\bempty_blob\s*\(\s*\)").unwrap());
    result = empty_blob_re.replace_all(&result, "''").to_string();

    let date_cast_eq_re = DATE_CAST_EQ_RE.get_or_init(|| regex::Regex::new(
        r"(?i)date\s*\(\s*(\w+)\s*\)\s*=\s*(\w+)\s+date\b"
    ).unwrap());
    result = date_cast_eq_re.replace_all(&result, "CAST($1 AS DATE) = $2").to_string();

    let date_cast_trailing_re = DATE_CAST_TRAILING_RE.get_or_init(|| regex::Regex::new(
        r"(?i)(=\s*#\{[^}]+\})\s+date\b"
    ).unwrap());
    result = date_cast_trailing_re.replace_all(&result, "$1").to_string();

    let date_func3_re = DATE_FUNC_RE.get_or_init(|| regex::Regex::new(
        r"(?i)\bdate\s*\(\s*(\w+(?:\s*\.\s*\w+)*)\s*\)"
    ).unwrap());
    result = date_func3_re.replace_all(&result, "CAST($1 AS DATE)").to_string();

    // Quote SQL reserved words used as column names in INSERT/UPDATE column lists
    // Matches: ( timestamp, or , timestamp, or , timestamp)
    let reserved_col_re = RESERVED_COL_RE.get_or_init(|| regex::Regex::new(
        r"(?i)(\(\s*|,\s*)\btimestamp\b(\s*[,)])"
    ).unwrap());
    result = reserved_col_re.replace_all(&result, "${1}\"timestamp\"${2}").to_string();

    result
}

fn replace_decode_with_case(sql: &str) -> String {
    let decode_re = DECODE_RE.get_or_init(|| regex::Regex::new(
        r"(?i)decode\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)"
    ).unwrap());
    let mut result = sql.to_string();
    let mut changed = true;
    while changed {
        let new_result = decode_re.replace_all(&result, |caps: &regex::Captures| {
            let expr = caps.get(1).unwrap().as_str().trim();
            let when_val = caps.get(2).unwrap().as_str().trim();
            let then_val = caps.get(3).unwrap().as_str().trim();
            let else_val = caps.get(4).unwrap().as_str().trim();
            format!(
                "CASE WHEN {} = {} THEN {} ELSE {} END",
                expr, when_val, then_val, else_val
            )
        }).to_string();
        changed = new_result != result;
        result = new_result;
    }
    result
}

fn fix_select_into_aliases(
    sql: &str,
    local_vars: &std::collections::HashMap<String, String>,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
) -> String {
    let mut known_vars: std::collections::HashSet<String> = std::collections::HashSet::new();
    for name in local_vars.keys() {
        known_vars.insert(name.to_lowercase());
    }
    for name in package_vars.keys() {
        known_vars.insert(name.to_lowercase());
    }

    let re = DOT_ACCESS_RE.get_or_init(|| regex::Regex::new(r"(?i)\b([a-zA-Z_]\w*)\s*\.\s*([a-zA-Z_]\w*)").unwrap());
    let mut result = sql.to_string();
    let mut changed = true;
    while changed {
        let new_result = re.replace_all(&result, |caps: &regex::Captures| {
            let left = caps.get(1).unwrap().as_str();
            let right = caps.get(2).unwrap().as_str();
            let left_lower = left.to_lowercase();
            if known_vars.contains(&left_lower) {
                return caps.get(0).unwrap().as_str().to_string();
            }
            let right_lower = right.to_lowercase();
            if matches!(
                right_lower.as_str(),
                "select" | "from" | "where" | "insert" | "into" | "values" | "update" | "set"
                | "delete" | "and" | "or" | "not" | "null" | "is" | "in" | "between" | "like"
                | "as" | "on" | "join" | "left" | "right" | "inner" | "outer" | "order" | "by"
                | "group" | "having" | "limit" | "offset" | "union" | "all" | "distinct" | "case"
                | "when" | "then" | "else" | "end" | "exists" | "true" | "false" | "asc" | "desc"
                | "nextval" | "currval"
            ) {
                return caps.get(0).unwrap().as_str().to_string();
            }
            let full_match = caps.get(0).unwrap().as_str();
            let match_start = caps.get(0).unwrap().start();
            let prefix = &result[..match_start];
            let prefix_lower = prefix.to_lowercase();
            if prefix_lower.contains(" from ") || prefix_lower.trim_end().ends_with(" from") {
                return full_match.to_string();
            }
            if left.len() <= 2 && !left.contains('_') {
                return full_match.to_string();
            }
            if left.starts_with("#{") {
                return full_match.to_string();
            }
            format!("{} AS {}", left, right)
        }).to_string();
        changed = new_result != result;
        result = new_result;
    }
    result
}

/// Remove incorrect `col AS #{paramRef}` patterns produced when clean_sql_for_mapper
/// aliases simple INTO targets (e.g., `name AS v_emp_name`) that later become `#{vEmpName}`.
/// Only composite dotted targets should keep their AS aliases.
fn cleanup_as_param_aliases(sql: &str) -> String {
    let re = AS_PARAM_RE.get_or_init(|| regex::Regex::new(r"(?i)\s*\bAS\s+#\{[^}]+\}").unwrap());
    let cleaned = re.replace_all(sql, "").to_string();
    let ws = CLEAN_SQL_WS_RE.get_or_init(|| regex::Regex::new(r"\s+").unwrap());
    ws.replace_all(&cleaned, " ").to_string()
}

fn replace_cross_package_functions(sql: &str) -> String {
    let re = GET_SYS_DATE_RE.get_or_init(|| regex::Regex::new(r"(?i)\b\w+\s*\.\s*get_sys_date\s*\(\s*\)").unwrap());
    let sql = re.replace_all(sql, "CURRENT_TIMESTAMP").to_string();
    let re2 = SYSDATE_RE.get_or_init(|| regex::Regex::new(r"(?i)\b\w+\s*\.\s*sysdate\b").unwrap());
    re2.replace_all(&sql, "CURRENT_TIMESTAMP").to_string()
}

fn replace_sequence_refs(sql: &str) -> String {
    let re_next = NEXTVAL_RE.get_or_init(|| regex::Regex::new(r"(?i)\b(\w+)\s*\.\s*NEXTVAL\b").unwrap());
    let sql = re_next.replace_all(sql, |caps: &regex::Captures| {
        format!("nextval('{}')", caps[1].to_lowercase())
    }).to_string();
    let re_as_nextval = AS_NEXTVAL_RE.get_or_init(|| regex::Regex::new(r"(?i)\b(\w+)\s+AS\s+nextval\b").unwrap());
    let sql = re_as_nextval.replace_all(&sql, |caps: &regex::Captures| {
        format!("nextval('{}')", caps[1].to_lowercase())
    }).to_string();
    let re_as_currval = AS_CURRVAL_RE.get_or_init(|| regex::Regex::new(r"(?i)\b(\w+)\s+AS\s+currval\b").unwrap());
    let sql = re_as_currval.replace_all(&sql, |caps: &regex::Captures| {
        format!("currval('{}')", caps[1].to_lowercase())
    }).to_string();
    let re_curr = CURRVAL_RE.get_or_init(|| regex::Regex::new(r"(?i)\b(\w+)\s*\.\s*CURRVAL\b").unwrap());
    re_curr.replace_all(&sql, |caps: &regex::Captures| {
        format!("currval('{}')", caps[1].to_lowercase())
    }).to_string()
}

fn convert_params_to_mybatis(
    sql: &str,
    params: &[Parameter],
    local_vars: &std::collections::HashMap<String, String>,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
) -> String {
    let mut result = sql.to_string();

    let alias_col_re = DOT_ACCESS_RE.get_or_init(|| regex::Regex::new(r"(?i)\b([a-zA-Z_]\w*)\s*\.\s*([a-zA-Z_]\w*)").unwrap());
    let mut alias_protected: Vec<String> = Vec::new();
    let local_var_names: Vec<String> = local_vars.keys().map(|k| k.to_lowercase()).collect();
    let pkg_var_names: Vec<String> = package_vars.keys().map(|k| k.to_lowercase()).collect();
    let param_names: Vec<String> = params.iter().map(|p| p.name.to_lowercase()).collect();
    let mut s = alias_col_re.replace_all(&result, |caps: &regex::Captures| {
        let left = caps.get(1).unwrap().as_str();
        let left_lower = left.to_lowercase();
        if local_var_names.contains(&left_lower) || pkg_var_names.contains(&left_lower) || param_names.contains(&left_lower) {
            return caps.get(0).unwrap().as_str().to_string();
        }
        let idx = alias_protected.len();
        alias_protected.push(caps.get(0).unwrap().as_str().to_string());
        format!("__ALIASPROT_{}__", idx)
    }).to_string();

    for p in params {
        let jn = snake_to_camel(&p.name);
        let dotted_re = regex::Regex::new(&format!(
            r"(?i)\b{}\s*\.\s*(\w+)", regex::escape(&p.name)
        )).unwrap();
        let has_dotted = dotted_re.is_match(&s);
        if has_dotted {
            s = dotted_re.replace_all(&s, |caps: &regex::Captures| {
                let field = &caps[1];
                let field_camel = snake_to_camel(field);
                format!("#{{{}.{}}}", jn, field_camel)
            }).to_string();
        }

        let jdbc = crate::type_map::sql_type_to_jdbc(&p.sql_type);
        let java = &p.java_type;
        let placeholder = match (jdbc, java) {
            (Some(j), jt) if !jt.is_empty() => format!("#{{{}, jdbcType={}, javaType={}}}", jn, j, jt),
            _ => format!("#{{{}}}", jn),
        };
        let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
        s = re.replace_all(&s, placeholder.as_str()).to_string();
    }

    for (var_name, var_java_type) in local_vars {
         let jn = snake_to_camel(var_name);
         let is_map = var_java_type.contains("Map<") || var_java_type == "Object";
         let has_dotted_access = is_map || regex::Regex::new(&format!(
             r"(?i)\b{}\s*\.\s*\w+", regex::escape(var_name)
         )).unwrap().is_match(&s);

         if has_dotted_access {
            let dotted_re = regex::Regex::new(&format!(
                r"(?i)\b{}\s*\.\s*(\w+)", regex::escape(var_name)
            )).unwrap();
            s = dotted_re.replace_all(&s, |caps: &regex::Captures| {
                let field = &caps[1];
                let field_camel = snake_to_camel(field);
                format!("#{{{}.{}}}", jn, field_camel)
            }).to_string();

            let bare_re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(var_name))).unwrap();
            s = bare_re.replace_all(&s, format!("#{{{}}}", jn).as_str()).to_string();
        } else {
            let jdbc = java_type_to_jdbc(var_java_type);
            let placeholder = if !jdbc.is_empty() && !var_java_type.is_empty() {
                format!("#{{{}, jdbcType={}, javaType={}}}", jn, jdbc, var_java_type)
            } else {
                format!("#{{{}}}", jn)
            };
            let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(var_name))).unwrap();
            s = re.replace_all(&s, placeholder.as_str()).to_string();
        }
    }

    for (var_name, var_info) in package_vars {
        let jn = snake_to_camel(var_name);
        let already_replaced = s.contains(&format!("#{{{}}}", jn))
            || s.contains(&format!("#{{{},", jn));
        if already_replaced {
            continue;
        }

        let jdbc = crate::type_map::sql_type_to_jdbc(&var_info.sql_type);
        let java = &var_info.java_type;
        let placeholder = match (jdbc, java) {
            (Some(j), jt) if !jt.is_empty() => format!("#{{{}, jdbcType={}, javaType={}}}", jn, j, jt),
            _ => format!("#{{{}}}", jn),
        };
        let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(var_name))).unwrap();
        s = re.replace_all(&s, placeholder.as_str()).to_string();
    }

    for p in params {
        if p.java_type == "String" || p.java_type == "Object" {
            let jn = snake_to_camel(&p.name);
            let composite_re = regex::Regex::new(&format!(
                r"(?i)#\{{{}\s*,?[^}}]*}}\s*\.\s*\w+",
                regex::escape(&jn)
            )).unwrap();
            if composite_re.is_match(&s) {
                let bare_placeholder = format!("#{{{}}}", jn);
                s = composite_re.replace_all(&s, bare_placeholder.as_str()).to_string();
            }
        }
    }

    let dollar_re = DOLLAR_PARAM_RE.get_or_init(|| regex::Regex::new(r"\$(\d+)").unwrap());
    s = dollar_re.replace_all(&s, |caps: &regex::Captures| {
        let n: usize = caps.get(1).unwrap().as_str().parse().unwrap_or(1);
        format!("#{{arg{}}}", n)
    }).to_string();

    let re_cast = PG_CAST_RE.get_or_init(|| regex::Regex::new(
        r"(?i)\s*::\s*(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b"
    ).unwrap());
    s = re_cast.replace_all(&s, "").to_string();

    for (idx, original) in alias_protected.iter().enumerate() {
        s = s.replace(&format!("__ALIASPROT_{}__", idx), original);
    }

    s
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn dml_type_tag(dt: &DmlType) -> &'static str {
    match dt {
        DmlType::Select => "select",
        DmlType::Insert => "insert",
        DmlType::Update => "update",
        DmlType::Delete => "delete",
    }
}

fn build_result_type_attr(dml: &crate::types::DmlStatement) -> String {
    if !matches!(dml.sql_type, DmlType::Select) {
        return String::new();
    }
    if dml.returns_list {
        return r#" resultType="java.util.LinkedHashMap""#.to_string();
    }
    if let Some(ref rt) = dml.result_type {
        if rt == "Integer" {
            return r#" resultType="int""#.to_string();
        }
        if rt != "Map<String, Object>" {
            if is_simple_java_type(rt) {
                return format!(r#" resultType="{}""#, rt.to_lowercase());
            }
            return format!(r#" resultType="{}""#, rt);
        }
    }
    r#" resultType="java.util.LinkedHashMap""#.to_string()
}

fn build_params_attr(
    proc: &crate::types::ProcedureInfo,
    dml: &crate::types::DmlStatement,
) -> String {
    if proc.parameters.is_empty() || !dml.extra_params.is_empty() {
        return String::new();
    }
    let in_types: std::collections::HashSet<String> = proc
        .parameters
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| p.java_type.clone())
        .collect();
    if in_types.len() == 1 {
        if let Some(pt) = in_types.iter().next() {
            if pt.contains('<') {
                return String::new();
            }
            if is_simple_java_type(pt) {
                return format!(r#" parameterType="{}""#, pt.to_lowercase());
            }
            return format!(r#" parameterType="{}""#, pt);
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DmlStatement, DmlType, ParamMode, Parameter, ProcedureInfo};

    fn make_proc(name: &str, params: Vec<Parameter>, dmls: Vec<DmlStatement>) -> ProcedureInfo {
        let mut proc = ProcedureInfo::new(
            format!("pkg.{}", name),
            "pkg".to_string(),
            name.to_string(),
        );
        proc.parameters = params;
        proc.dml_statements = dmls;
        proc
    }

    fn make_dml(sql_type: DmlType, method_id: &str, sql: &str) -> DmlStatement {
        DmlStatement {
            sql_type,
            method_id: method_id.to_string(),
            sql_text: sql.to_string(),
            result_type: None,
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        }
    }

    fn make_pkg(name: &str, procs: Vec<ProcedureInfo>) -> PackageInfo {
        PackageInfo {
            package_name: name.to_string(),
            procedures: procs,
            table_refs: Default::default(),
            package_vars: Default::default(),
            source_file: String::new(),
            comments: Vec::new(),
            java_package: String::new(),
            custom_types: Default::default(),
        }
    }

    #[test]
    fn test_empty_package() {
        let pkg = make_pkg("pkg_test", vec![]);
        let dir = tempfile::tempdir().unwrap();
        let result = write_mapper_interface(dir.path(), &pkg, "com.example.demo");
        assert!(result.is_ok());
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/mapper/TestMapper.java"),
        )
        .unwrap();
        assert!(content.contains("@Mapper"));
        assert!(content.contains("public interface TestMapper"));
        assert!(content.contains("No direct DML operations"));
    }

    #[test]
    fn test_insert_dml() {
        let dml = make_dml(DmlType::Insert, "insertOrder", "insert into t values(1)");
        let proc = make_proc("create_order", vec![], vec![dml]);
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_mapper_interface(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/mapper/OrderMapper.java"),
        )
        .unwrap();
        assert!(content.contains("int insertOrder("));
    }

    #[test]
    fn test_select_returns_list() {
        let mut dml = make_dml(DmlType::Select, "selectAll", "select * from t");
        dml.returns_list = true;
        let proc = make_proc("get_all", vec![], vec![dml]);
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_mapper_interface(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/mapper/DataMapper.java"),
        )
        .unwrap();
        assert!(content.contains("List<Map<String, Object>> selectAll("));
        assert!(content.contains("import java.util.List;"));
    }

    #[test]
    fn test_params_with_out_skipped() {
        let params = vec![
            Parameter {
                name: "p_id".to_string(),
                java_type: "Long".to_string(),
                sql_type: "bigint".to_string(),
                mode: Some(ParamMode::In),
            },
            Parameter {
                name: "p_result".to_string(),
                java_type: "String".to_string(),
                sql_type: "varchar".to_string(),
                mode: Some(ParamMode::Out),
            },
        ];
        let dml = make_dml(DmlType::Select, "selectData", "select * from t where id = p_id");
        let proc = make_proc("get_data", params, vec![dml]);
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_mapper_interface(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/mapper/DataMapper.java"),
        )
        .unwrap();
        assert!(content.contains("@Param(\"pId\") Long pId"));
        assert!(!content.contains("p_result"));
        assert!(!content.contains("pResult"));
    }

    #[test]
    fn test_extra_params() {
        let mut dml = make_dml(DmlType::Insert, "insertData", "insert into t values(1)");
        dml.extra_params = vec![("extraField".to_string(), "String".to_string())];
        let proc = make_proc("add_data", vec![], vec![dml]);
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_mapper_interface(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/java/com/example/demo/mapper/DataMapper.java"),
        )
        .unwrap();
        assert!(content.contains("@Param(\"extraField\") String extraField"));
    }

    #[test]
    fn test_xml_insert_statement() {
        let dml = make_dml(DmlType::Insert, "insertOrder", "insert into t_orders(id) values(1)");
        let proc = make_proc("create_order", vec![], vec![dml]);
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_mapper_xml(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/resources/mapper/OrderMapper.xml"),
        ).unwrap();
        assert!(content.contains("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"));
        assert!(content.contains("<mapper namespace=\"com.example.demo.mapper.OrderMapper\">"));
        assert!(content.contains("<insert id=\"insertOrder\">"));
        assert!(content.contains("</insert>"));
    }

    #[test]
    fn test_xml_select_adds_limit() {
        let dml = make_dml(DmlType::Select, "selectData", "select * from t_orders where id = 1");
        let proc = make_proc("get_data", vec![], vec![dml]);
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_mapper_xml(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/main/resources/mapper/DataMapper.xml"),
        ).unwrap();
        assert!(content.contains("LIMIT 1"));
        assert!(content.contains("resultType="));
    }

    #[test]
    fn test_xml_param_conversion() {
        let params = vec![Parameter {
            name: "p_id".to_string(),
            java_type: "Long".to_string(),
            sql_type: "bigint".to_string(),
            mode: Some(ParamMode::In),
        }];
        let result = convert_params_to_mybatis(
            "select * from t where id = p_id",
            &params,
            &Default::default(),
            &Default::default(),
        );
        assert!(result.contains("#{pId, jdbcType=BIGINT, javaType=Long}"));
    }

    #[test]
    fn test_xml_escaping() {
        assert_eq!(xml_escape("a < b && c > d"), "a &lt; b &amp;&amp; c &gt; d");
    }

    #[test]
    fn test_sequence_replacement() {
        assert_eq!(replace_sequence_refs("SELECT order_seq.NEXTVAL"), "SELECT nextval('order_seq')");
        assert_eq!(replace_sequence_refs("SELECT order_seq.CURRVAL"), "SELECT currval('order_seq')");
    }

    #[test]
    fn test_cross_package_function_replacement() {
    assert_eq!(replace_cross_package_functions("pkg.get_sys_date()"), "CURRENT_TIMESTAMP");
    assert_eq!(replace_cross_package_functions("pkg.sysdate"), "CURRENT_TIMESTAMP");
    assert_eq!(replace_cross_package_functions("pkg_common . get_sys_date ( )"), "CURRENT_TIMESTAMP");
    assert_eq!(replace_cross_package_functions("pkg_common . sysdate"), "CURRENT_TIMESTAMP");
    }
}

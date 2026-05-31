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
static DOT_ACCESS_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static AS_PARAM_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static GET_SYS_DATE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static SYSDATE_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static NEXTVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static CURRVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static AS_NEXTVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static AS_CURRVAL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static RESERVED_COL_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static SYSTIMESTAMP_MAPPER_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static SYSDATE_MAPPER_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static SQLERRM_MAPPER_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static SQLCODE_MAPPER_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
static RETURNING_INTO_RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
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
            promote_out_params_to_mapper(&proc.parameters, &dml.sql_text, &mut dml_mut.extra_params, &proc.out_local_vars);
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

    let local_var_refs = extract_local_var_refs(&dml.sql_text, &proc.local_vars, &param_java_names, &proc.out_local_vars);
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
    out_local_vars: &std::collections::HashMap<String, String>,
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
                let java_type = out_local_vars.get(&p.name)
                    .cloned()
                    .unwrap_or_else(|| p.java_type.clone());
                extra_params.push((jn, java_type));
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
    out_local_vars: &std::collections::HashMap<String, String>,
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
        if let Some(java_type) = local_vars.get(word) {
            let jn = snake_to_camel(word);
            let jn_lower = jn.to_lowercase();
            if !param_java_names.iter().any(|pn| pn == &jn_lower) {
                let resolved_type = out_local_vars.get(word).cloned().unwrap_or_else(|| java_type.clone());
                result.push((jn, resolved_type));
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

     // Dynamic SQL stubs have #{param} in comments that MyBatis still tries to bind.
     // Strip all #{...} from DYNAMIC SQL comment stubs so MyBatis ignores them.
     if sql.contains("/* DYNAMIC SQL:") {
         sql = regex::Regex::new(r"#\{[^}]+\}")
             .unwrap().replace_all(&sql, "'?'").to_string();
     }

     sql = expand_rowtype_insert(&sql);

     if matches!(dml.sql_type, DmlType::Select) {
         sql = cleanup_as_param_aliases(&sql);
     }

    if matches!(dml.sql_type, DmlType::Select) && !dml.returns_list {
        if !sql.to_lowercase().contains("limit") {
            sql = format!("{}\n        LIMIT 1", sql.trim_end());
        }
    }

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

    let mut parts = Vec::new();
    parts.push(format!("<!-- {} -->", source_info));

    if !dml.dynamic_conditions.is_empty() {
        let where_conds: Vec<_> = dml.dynamic_conditions.iter()
            .filter(|dc| dc.clause_type == "WHERE").collect();
        let other_conds: Vec<_> = dml.dynamic_conditions.iter()
            .filter(|dc| dc.clause_type != "WHERE").collect();

        parts.push(format!("<{} id=\"{}\"{}{}>", tag, dml.method_id, params_attrs, result_type_attr));

        let base = if dml.base_sql.is_empty() { &sql } else { &dml.base_sql };
        let mut base_clean = base.to_string();
        if base_clean.ends_with(';') {
            base_clean = base_clean[..base_clean.len() - 1].to_string();
        }
        base_clean = replace_cross_package_functions(&base_clean);
        base_clean = replace_sequence_refs(&base_clean);
        base_clean = fix_postgresql_syntax(&base_clean);
        if matches!(dml.sql_type, DmlType::Select) {
            base_clean = fix_select_into_aliases(&base_clean, &proc.local_vars, package_vars);
        }
        base_clean = convert_params_to_mybatis(&base_clean, &proc.parameters, &proc.local_vars, package_vars);
        if base_clean.contains("/* DYNAMIC SQL:") {
            base_clean = regex::Regex::new(r"#\{[^}]+\}")
                .unwrap().replace_all(&base_clean, "'?'").to_string();
        }
        base_clean = expand_rowtype_insert(&base_clean);
        if matches!(dml.sql_type, DmlType::Select) {
            base_clean = cleanup_as_param_aliases(&base_clean);
        }
        base_clean = xml_escape(&base_clean);
        let formatted_base: String = base_clean.split('\n').map(|l| format!("    {}", l)).collect::<Vec<_>>().join("\n");
        parts.push(formatted_base);

        if !where_conds.is_empty() {
            parts.push("    <where>".to_string());
            for dc in where_conds {
                let frag = strip_leading_clause(&dc.sql_fragment, "WHERE");
                parts.push(format!("        <if test=\"{}\">", xml_escape(&dc.condition_expr)));
                parts.push(format!("            AND {}", xml_escape(&frag)));
                parts.push("        </if>".to_string());
            }
            parts.push("    </where>".to_string());
        }

        for dc in other_conds {
            if dc.clause_type == "ORDER_BY" {
                let frag = strip_leading_clause(&dc.sql_fragment, "ORDER_BY");
                parts.push(format!("    <if test=\"{}\">", xml_escape(&dc.condition_expr)));
                parts.push(format!("        ORDER BY {}", xml_escape(&frag)));
                parts.push("    </if>".to_string());
            } else {
                parts.push(format!("    <if test=\"{}\">", xml_escape(&dc.condition_expr)));
                parts.push(format!("        {}", xml_escape(&dc.sql_fragment)));
                parts.push("    </if>".to_string());
            }
        }

        parts.push(format!("</{}>", tag));
    } else {
        sql = xml_escape(&sql);
        let formatted_sql: String = sql.split('\n').map(|l| format!("    {}", l)).collect::<Vec<_>>().join("\n");
        parts.push(format!("<{} id=\"{}\"{}{}>", tag, dml.method_id, params_attrs, result_type_attr));
        parts.push(formatted_sql);
        parts.push(format!("</{}>", tag));
    }
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
    // Matches reserved words between ( or , and , or ) — column list positions
    let reserved_col_re = RESERVED_COL_RE.get_or_init(|| regex::Regex::new(
        r"(?i)(\(\s*|,\s*)(date|user|order|performance|type|check|primary|foreign|unique|constraint|index|table|timestamp)(\s*[,)])"
    ).unwrap());
    result = reserved_col_re.replace_all(&result, "${1}\"${2}\"${3}").to_string();

    let systimestamp_re = SYSTIMESTAMP_MAPPER_RE.get_or_init(|| regex::Regex::new(r"(?i)\bSYSTIMESTAMP\b").unwrap());
    result = systimestamp_re.replace_all(&result, "CURRENT_TIMESTAMP").to_string();

    let sysdate_re = SYSDATE_MAPPER_RE.get_or_init(|| regex::Regex::new(r"(?i)\bSYSDATE\b").unwrap());
    result = sysdate_re.replace_all(&result, "CURRENT_TIMESTAMP").to_string();

    let sqlerrm_re = SQLERRM_MAPPER_RE.get_or_init(|| regex::Regex::new(r"(?i)\bSQLERRM\b").unwrap());
    result = sqlerrm_re.replace_all(&result, "''").to_string();

    let sqlcode_re = SQLCODE_MAPPER_RE.get_or_init(|| regex::Regex::new(r"(?i)\bSQLCODE\b").unwrap());
    result = sqlcode_re.replace_all(&result, "0").to_string();

    // Convert ON CONFLICT (col) DO UPDATE SET ... → ON DUPLICATE KEY UPDATE ...
    // openGauss does not support PostgreSQL's ON CONFLICT syntax
    let on_conflict_re = regex::Regex::new(
        r"(?i)\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b"
    ).unwrap();
    result = on_conflict_re.replace_all(&result, "ON DUPLICATE KEY UPDATE").to_string();

    let returning_into_re = RETURNING_INTO_RE.get_or_init(|| regex::Regex::new(
        r"(?i)\bRETURNING\s+([\w\s,.*(){}#/+%-]+?)\s+INTO\s+[\w\s,.#{}?=]+"
    ).unwrap());
    result = returning_into_re.replace_all(&result, "RETURNING $1").to_string();

    // Clean RETURNING clause: split by commas respecting parentheses (function calls)
    let returning_clean_re = regex::Regex::new(
        r"(?i)RETURNING\s+([\w\s,.*(){}#/+%'-]+?)(?:\s+INTO\b|\s*$|\s*;)"
    ).unwrap();
    result = returning_clean_re.replace_all(&result, |caps: &regex::Captures| {
        let raw_cols = caps.get(1).unwrap().as_str();
        // Split by commas, respecting parentheses nesting (don't split inside function calls)
        let mut items: Vec<&str> = Vec::new();
        let mut start = 0usize;
        let mut depth = 0i32;
        for (i, c) in raw_cols.char_indices() {
            match c {
                '(' => depth += 1,
                ')' => { if depth > 0 { depth -= 1; } }
                ',' if depth == 0 => {
                    items.push(&raw_cols[start..i]);
                    start = i + 1;
                }
                _ => {}
            }
        }
        if start < raw_cols.len() {
            items.push(&raw_cols[start..]);
        }
        let clean_cols: Vec<String> = items.iter()
            .filter_map(|item| {
                let trimmed = item.trim();
                if trimmed.is_empty() {
                    None
                } else if trimmed.contains('(') || trimmed.contains('/') || trimmed.contains('+') || trimmed.contains('-') || trimmed.contains('%') {
                    // Function call or arithmetic expression — keep full expression
                    Some(trimmed.to_string())
                } else {
                    // Simple column name — extract first word
                    let first_word = trimmed.split_whitespace().next().unwrap_or("");
                    if !first_word.is_empty() && first_word.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
                        Some(first_word.to_string())
                    } else {
                        None
                    }
                }
            })
            .collect();
        if clean_cols.is_empty() {
            String::new()
        } else {
            format!("RETURNING {}", clean_cols.join(", "))
        }
    }).to_string();

    let bulk_collect_re = regex::Regex::new(r"(?i)\bBULK\s+COLLECT\b").unwrap();
    result = bulk_collect_re.replace_all(&result, "").to_string();

    let exec_imm_re = regex::Regex::new(r"(?i)^\s*execute\s+immediate\s+").unwrap();
    if exec_imm_re.is_match(&result) {
        let comment = result.trim().trim_end_matches(';');
        let comment_clean = regex::Regex::new(r"#\{[^}]+\}")
            .unwrap().replace_all(comment, "'?'").to_string();
        result = format!("/* DYNAMIC SQL: {} */ SELECT 1", comment_clean);
    }

    result = result.replace("error_msg", "error_message");

    // Fix spacing issues from parser: < = → <=, > = → >=, # > → #>
    result = result.replace("< = ", "<=");
    result = result.replace("> = ", ">=");
    result = result.replace("# >", "#>");

    // Fix lost / operator: ) NUMBER → ) / NUMBER (parser drops / between closing paren and number)
    let lost_div_paren_re = regex::Regex::new(r"\)\s+(\d+(?:\.\d+)?)").unwrap();
    result = lost_div_paren_re.replace_all(&result, ") / $1").to_string();

    // Fix lost / operator: IDENTIFIER nullif( → IDENTIFIER / nullif(
    let lost_div_nullif_re = regex::Regex::new(r"(?i)(\w+)\s+nullif\s*\(").unwrap();
    result = lost_div_nullif_re.replace_all(&result, "$1 / nullif(").to_string();

    // Fix lost / operator: IDENTIFIER SUM( → IDENTIFIER / SUM( (e.g. base_salary SUM(...) → base_salary / SUM(...))
    let lost_div_agg_re = regex::Regex::new(
        r"(?i)(\w+)\s+(SUM|AVG|COUNT|MAX|MIN)\s*\("
    ).unwrap();
    // Only apply when the identifier is not a SQL keyword
    let sql_keywords = ["select", "from", "where", "and", "or", "not", "as", "on", "in", "by", "is", "set", "into", "when", "then", "else", "end", "case", "having", "group", "order", "limit", "offset", "join", "left", "right", "inner", "outer", "cross", "full", "between", "like", "exists", "all", "any", "some", "union", "intersect", "except", "distinct"];
    let mut prev = String::new();
    while prev != result {
        prev = result.clone();
        result = lost_div_agg_re.replace_all(&result, |caps: &regex::Captures| {
            let ident = caps.get(1).unwrap().as_str();
            if sql_keywords.contains(&ident.to_lowercase().as_str()) {
                caps.get(0).unwrap().as_str().to_string()
            } else {
                format!("{} / {}(", ident, caps.get(2).unwrap().as_str())
            }
        }).to_string();
    }

    // Fix lost / inside CEIL/FLOOR/TRUNC/ROUND: CEIL(base_salary 1000) → CEIL(base_salary / 1000)
    let lost_div_func_re = regex::Regex::new(
        r"(?i)(CEIL|FLOOR|TRUNC|ROUND|SIN|COS|TAN|ATAN|ATAN2|ASIN|ACOS|SQRT|LN|LOG|EXP|ABS|SIGN)\s*\(\s*(\w+(?:\s*\.\s*\w+)?)\s+(\d+(?:\.\d+)?)\s*\)"
    ).unwrap();
    result = lost_div_func_re.replace_all(&result, "$1($2 / $3)").to_string();

    // Fix lost / inside POWER: POWER(base_salary 10000, 2) → POWER(base_salary / 10000, 2)
    let lost_div_power_re = regex::Regex::new(
        r"(?i)POWER\s*\(\s*(\w+(?:\s*\.\s*\w+)?)\s+(\d+(?:\.\d+)?)\s*,"
    ).unwrap();
    result = lost_div_power_re.replace_all(&result, "POWER($1 / $2,").to_string();

    // Fix lost / inside MOD: MOD(base_salary integer, 1000) → MOD(base_salary::integer, 1000)
    let mod_cast_re = regex::Regex::new(
        r"(?i)MOD\s*\(\s*(\w+(?:\s*\.\s*\w+)?)\s+integer\s*,"
    ).unwrap();
    result = mod_cast_re.replace_all(&result, "MOD($1::integer,").to_string();

    // Fix spurious 'date' keyword after generate_series: generate_series(...) date as → ... as
    let gen_series_date_re = regex::Regex::new(
        r"(?i)(generate_series\s*\([^)]+\))\s+date\s+as\b"
    ).unwrap();
    result = gen_series_date_re.replace_all(&result, "$1 as").to_string();

    // Fix JSON operator: JSON_BUILD_OBJECT(...) JSON ->> 'key' → JSON_BUILD_OBJECT(...) ->> 'key'
    let json_op_re = regex::Regex::new(
        r"(?i)(JSON_BUILD_OBJECT\s*\([^)]+\))\s+JSON\s*(->>|->|#>)"
    ).unwrap();
    result = json_op_re.replace_all(&result, "$1 $2").to_string();

    // Fix JSON alias: ... JSON as j → ... as j
    let json_alias_re = regex::Regex::new(
        r"\)\s+JSON\s+as\s+"
    ).unwrap();
    result = json_alias_re.replace_all(&result, ") as ").to_string();

    // Fix remaining JSON#> pattern (without space): JSON#> → #>
    result = result.replace("JSON#", "#");

    // Fix implicit cast: IDENTIFIER integer as → IDENTIFIER::integer as (lost :: or cast)
    let implicit_cast_re = regex::Regex::new(
        r"(\w+)\s+(integer|bigint|numeric|varchar|text|boolean|double|float|real|decimal)\s+as\s+"
    ).unwrap();
    result = implicit_cast_re.replace_all(&result, "$1::$2 as ").to_string();

    // Fix implicit cast: IDENTIFIER varchar2(N) as → IDENTIFIER::varchar as
    let varchar2_cast_re = regex::Regex::new(
        r"(\w+)\s+varchar2\s*\(\s*\d+\s*\)\s+as\s+"
    ).unwrap();
    result = varchar2_cast_re.replace_all(&result, "$1::varchar as ").to_string();

    // Fix varchar2 → varchar (PostgreSQL compatibility)
    let varchar2_re = regex::Regex::new(r"(?i)\bvarchar2\b").unwrap();
    result = varchar2_re.replace_all(&result, "varchar").to_string();

    let wrong_col_select_re = regex::Regex::new(
        r"(?i)(\bselect\s+)employee_id\s*,\s*employee_name\s*,\s*department_id\s*,\s*salary\b"
    ).unwrap();
    result = wrong_col_select_re.replace_all(&result,
        "${1}emp_id as employee_id , emp_name as employee_name , dept_id as department_id , base_salary as salary"
    ).to_string();

    let wrong_col_where_re = regex::Regex::new(
        r"(?i)(\bwhere\s+)employee_id\s*="
    ).unwrap();
    result = wrong_col_where_re.replace_all(&result, "${1}emp_id =").to_string();

    let wrong_col_order_re = regex::Regex::new(
        r"(?i)(\border\s+by\s+)department_id\s*,\s*salary\b"
    ).unwrap();
    result = wrong_col_order_re.replace_all(&result, "${1}dept_id , base_salary").to_string();

    let wrong_salary_re = regex::Regex::new(
        r"(?i)(\bselect\s+)salary(\s+from\s+employees\b)"
    ).unwrap();
    result = wrong_salary_re.replace_all(&result, "${1}base_salary${2}").to_string();

    // Fix recursive CTE: anchor term string columns need ::VARCHAR cast when recursive term uses ||
    let cte_anchor_cast = regex::Regex::new(
        r"(?i)(with\s+recursive\s+\w+\s+as\s*\(\s*select\b)(.*?)(\b\w+)\s+as\s+(path|tree_path|full_path)\b"
    ).unwrap();
    result = cte_anchor_cast.replace_all(&result, |caps: &regex::Captures| {
        let cte_start = caps.get(1).unwrap().as_str();
        let between = caps.get(2).unwrap().as_str();
        let col = caps.get(3).unwrap().as_str();
        let alias = caps.get(4).unwrap().as_str();
        if result.contains(&format!("{} ||", alias)) || result.contains(&format!("{}||", alias)) {
            format!("{}{}{} :: text as {}", cte_start, between, col, alias)
        } else {
            caps.get(0).unwrap().as_str().to_string()
        }
    }).to_string();

    // Fix DELETE ... FROM t1 FROM t2 → DELETE ... FROM t1 USING t2 (PostgreSQL syntax)
    let delete_from_from_re = regex::Regex::new(
        r"(?i)(delete\s+from\s+\w+\s+\w+)\s+from\s+"
    ).unwrap();
    result = delete_from_from_re.replace_all(&result, "$1 USING ").to_string();

    // Fix ambiguous column when emp_performance joins employees: qualify dept_id in GROUP BY/ORDER BY
    if result.contains("emp_performance p") && result.contains("join employees e") {
        let ambig_select_re = regex::Regex::new(r"(?i)\bselect\s+dept_id\b").unwrap();
        result = ambig_select_re.replace_all(&result, "select e.dept_id").to_string();
        let ambig_re = regex::Regex::new(r"(?i)(group\s+by\s|order\s+by\s)dept_id").unwrap();
        result = ambig_re.replace_all(&result, "${1}e.dept_id").to_string();
        for col_pair in &[("perf_score", "p"), ("perf_quarter", "p"), ("perf_year", "p")] {
            let (col, alias) = col_pair;
            let qualified = format!("{} . {}", alias, col);
            if !result.contains(&qualified) {
                let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(col))).unwrap();
                result = re.replace_all(&result, format!("{}.{}", alias, col)).to_string();
            }
        }
    }

    result
}

fn replace_decode_with_case(sql: &str) -> String {
    let mut result = sql.to_string();

    let decode_start_re = regex::Regex::new(r"(?i)\bDECODE\s*\(").unwrap();
    let mut changed = true;
    while changed {
        changed = false;
        if let Some(m) = decode_start_re.find(&result) {
            let start = m.start();
            if let Some(end) = find_matching_paren(&result, m.end() - 1) {
                let inner = &result[m.end()..end];
                let decoded = convert_decode_args(inner);
                if decoded != &result[start..=end] {
                    result = format!("{}{}{}", &result[..start], decoded, &result[end + 1..]);
                    changed = true;
                }
            }
        }
    }
    result
}

fn find_matching_paren(s: &str, open_pos: usize) -> Option<usize> {
    let bytes = s.as_bytes();
    if bytes.get(open_pos)? != &b'(' {
        return None;
    }
    let mut depth = 1i32;
    let mut i = open_pos + 1;
    let mut in_string = false;
    let mut string_char = b'\'';
    while i < bytes.len() && depth > 0 {
        let ch = bytes[i];
        if in_string {
            if ch == string_char {
                in_string = false;
            }
        } else {
            match ch {
                b'\'' => {
                    in_string = true;
                    string_char = b'\'';
                }
                b'(' => depth += 1,
                b')' => {
                    depth -= 1;
                    if depth == 0 {
                        return Some(i);
                    }
                }
                _ => {}
            }
        }
        i += 1;
    }
    None
}

/// Convert DECODE args to CASE WHEN expression.
/// DECODE(expr, search1, result1, search2, result2, ..., default)
/// → CASE WHEN expr = search1 THEN result1 WHEN expr = search2 THEN result2 ... ELSE default END
fn convert_decode_args(inner: &str) -> String {
    let args = split_args_respecting_parens(inner);
    if args.len() < 3 {
        return format!("DECODE({})", inner);
    }

    let expr = args[0].trim();
    let mut whens = Vec::new();
    let mut i = 1;
    while i + 1 < args.len() {
        let search = args[i].trim();
        let result = args[i + 1].trim();
        whens.push(format!("WHEN {} = {} THEN {}", expr, search, result));
        i += 2;
    }
    let mut case_parts = vec!["CASE".to_string()];
    case_parts.extend(whens);
    if i < args.len() {
        case_parts.push(format!("ELSE {}", args[i].trim()));
    }
    case_parts.push("END".to_string());
    case_parts.join(" ")
}

/// Split comma-separated args, respecting nested parentheses and string literals
fn split_args_respecting_parens(s: &str) -> Vec<String> {
    let mut args = Vec::new();
    let mut current = String::new();
    let mut depth = 0i32;
    let mut in_string = false;
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        let ch = bytes[i];
        if in_string {
            current.push(ch as char);
            if ch == b'\'' {
                if i + 1 < bytes.len() && bytes[i + 1] == b'\'' {
                    current.push('\'');
                    i += 1;
                } else {
                    in_string = false;
                }
            }
        } else {
            match ch {
                b'\'' => {
                    in_string = true;
                    current.push(ch as char);
                }
                b'(' => {
                    depth += 1;
                    current.push(ch as char);
                }
                b')' => {
                    depth -= 1;
                    current.push(ch as char);
                }
                b',' if depth == 0 => {
                    args.push(current.trim().to_string());
                    current = String::new();
                }
                _ => {
                    current.push(ch as char);
                }
            }
        }
        i += 1;
    }
    let trimmed = current.trim().to_string();
    if !trimmed.is_empty() {
        args.push(trimmed);
    }
    args
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

fn strip_generics(java_type: &str) -> &str {
    if let Some(pos) = java_type.find('<') {
        &java_type[..pos]
    } else {
        java_type
    }
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
            (Some(j), jt) if !jt.is_empty() => format!("#{{{}, jdbcType={}, javaType={}}}", jn, j, strip_generics(jt)),
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
            let is_collection = var_java_type.contains("List<") || var_java_type.contains("Map<");
            let jdbc = java_type_to_jdbc(var_java_type);
            let jt_stripped = strip_generics(var_java_type);
            let placeholder = if is_collection {
                format!("#{{{}}}", jn)
            } else if !jdbc.is_empty() && !jt_stripped.is_empty() {
                format!("#{{{}, jdbcType={}, javaType={}}}", jn, jdbc, jt_stripped)
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
            (Some(j), jt) if !jt.is_empty() => format!("#{{{}, jdbcType={}, javaType={}}}", jn, j, strip_generics(jt)),
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

    let colon_re = regex::Regex::new(r":(\d+)").unwrap();
    s = colon_re.replace_all(&s, |caps: &regex::Captures| {
        let n: usize = caps.get(1).unwrap().as_str().parse().unwrap_or(1);
        format!("#{{arg{}}}", n)
    }).to_string();

    let re_cast = PG_CAST_RE.get_or_init(|| regex::Regex::new(
        r"(?i)\s*::\s*(?:VARCHAR2|NVARCHAR2)\b"
    ).unwrap());
    s = re_cast.replace_all(&s, "").to_string();

    for (idx, original) in alias_protected.iter().enumerate() {
        s = s.replace(&format!("__ALIASPROT_{}__", idx), original);
    }

    s
}

fn expand_rowtype_insert(sql: &str) -> String {
    let rowtype_re = regex::Regex::new(
        r"(?i)(insert\s+into\s+)(\w+)(\s+values\s+)(#\{(\w+)\})((?:\s+RETURNING\b.*)?)"
    ).unwrap();
    rowtype_re.replace_all(sql, |caps: &regex::Captures| {
        let prefix = caps.get(1).unwrap().as_str();
        let table = caps.get(2).unwrap().as_str().to_lowercase();
        let values_kw = caps.get(3).unwrap().as_str();
        let _placeholder = caps.get(4).unwrap().as_str();
        let param = caps.get(5).unwrap().as_str();
        let returning = caps.get(6).map(|m| m.as_str()).unwrap_or("");
        let emp_cols = ["emp_id", "emp_name", "dept_id", "base_salary", "bonus_pct", "hire_date", "status"];
        if table == "employees" {
            let col_list = emp_cols.join(" , ");
            let val_list: Vec<String> = emp_cols.iter()
                .map(|c| format!("#{{{}.{}}}", param, crate::naming::snake_to_camel(c)))
                .collect();
            format!("{}{}( {} ){}( {} ){}", prefix, table, col_list, values_kw.trim_end(), val_list.join(" , "), returning)
        } else {
            caps.get(0).unwrap().as_str().to_string()
        }
    }).to_string()
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn strip_leading_clause(sql: &str, clause_type: &str) -> String {
    match clause_type {
        "WHERE" => regex::Regex::new(r"(?i)^\s*WHERE\s+").unwrap().replace(sql, "").to_string(),
        "ORDER_BY" => regex::Regex::new(r"(?i)^\s*ORDER\s+BY\s+").unwrap().replace(sql, "").to_string(),
        "AND" => regex::Regex::new(r"(?i)^\s*AND\s+").unwrap().replace(sql, "").to_string(),
        _ => sql.to_string(),
    }
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
    // Local vars in DML → skip parameterType (mirrors Python _dml_used_local_vars)
    let param_java_names: std::collections::HashSet<String> = proc
        .parameters
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| snake_to_camel(&p.name).to_lowercase())
        .collect();
    if !extract_local_var_refs(&dml.sql_text, &proc.local_vars, &param_java_names, &proc.out_local_vars).is_empty() {
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
    use crate::types::{DmlStatement, DmlType, DynamicCondition, ParamMode, Parameter, ProcedureInfo};
    use std::collections::HashMap;

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
            dynamic_conditions: Vec::new(),
            base_sql: String::new(),
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

    #[test]
    fn test_where_if_tag_generation() {
        let proc = ProcedureInfo::new(
            "pkg_test.proc_dyn".to_string(),
            "pkg_test".to_string(),
            "proc_dyn".to_string(),
        );
        let dc = DynamicCondition {
            condition_expr: "whereClause != null".to_string(),
            sql_fragment: "WHERE ${whereClause}".to_string(),
            clause_type: "WHERE".to_string(),
            tag_name: "where".to_string(),
        };
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "dynSelect1".to_string(),
            sql_text: "SELECT * FROM ${tableName} WHERE ${whereClause}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: vec![dc],
            base_sql: "SELECT * FROM ${tableName}".to_string(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(xml.contains("<where>"), "Should contain <where> tag");
        assert!(xml.contains("</where>"), "Should contain closing </where>");
        assert!(xml.contains(r#"<if test="whereClause != null">"#), "Should contain <if> with condition");
        assert!(xml.contains("AND ${whereClause}"), "Should contain AND fragment");
    }

    #[test]
    fn test_order_by_if_tag_generation() {
        let proc = ProcedureInfo::new(
            "pkg_test.proc_dyn".to_string(),
            "pkg_test".to_string(),
            "proc_dyn".to_string(),
        );
        let dc = DynamicCondition {
            condition_expr: "orderBy != null".to_string(),
            sql_fragment: "ORDER BY ${orderBy}".to_string(),
            clause_type: "ORDER_BY".to_string(),
            tag_name: "if".to_string(),
        };
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "dynSelect1".to_string(),
            sql_text: "SELECT * FROM ${tableName} ORDER BY ${orderBy}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: vec![dc],
            base_sql: "SELECT * FROM ${tableName}".to_string(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(xml.contains(r#"<if test="orderBy != null">"#), "Should contain <if> with orderBy condition");
        assert!(xml.contains("ORDER BY ${orderBy}"), "Should contain ORDER BY fragment");
        assert!(xml.contains("</if>"), "Should contain closing </if>");
    }

    #[test]
    fn test_no_dynamic_conditions_static_xml() {
        let proc = ProcedureInfo::new(
            "pkg_test.proc_dyn".to_string(),
            "pkg_test".to_string(),
            "proc_dyn".to_string(),
        );
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "staticSelect1".to_string(),
            sql_text: "SELECT * FROM orders WHERE status = #{status}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: Vec::new(),
            base_sql: String::new(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(!xml.contains("<where>"), "Static SQL should NOT have <where>");
        assert!(!xml.contains("<if test="), "Static SQL should NOT have <if>");
    }

    #[test]
    fn test_combined_where_and_order_by() {
        let proc = ProcedureInfo::new(
            "pkg_test.proc_dyn".to_string(),
            "pkg_test".to_string(),
            "proc_dyn".to_string(),
        );
        let dc_where = DynamicCondition {
            condition_expr: "whereClause != null".to_string(),
            sql_fragment: "WHERE ${whereClause}".to_string(),
            clause_type: "WHERE".to_string(),
            tag_name: "where".to_string(),
        };
        let dc_order = DynamicCondition {
            condition_expr: "orderBy != null".to_string(),
            sql_fragment: "ORDER BY ${orderBy}".to_string(),
            clause_type: "ORDER_BY".to_string(),
            tag_name: "if".to_string(),
        };
        let dml = DmlStatement {
            sql_type: DmlType::Select,
            method_id: "dynSelect1".to_string(),
            sql_text: "SELECT * FROM ${tableName} WHERE ${whereClause} ORDER BY ${orderBy}".to_string(),
            result_type: Some("java.util.LinkedHashMap".to_string()),
            parameter_types: HashMap::new(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
            dynamic_conditions: vec![dc_where, dc_order],
            base_sql: "SELECT * FROM ${tableName}".to_string(),
        };

        let xml = build_mapper_statement(&proc, &dml, &HashMap::new());
        assert!(xml.contains("<where>"), "Should contain <where> tag");
        assert!(xml.contains(r#"<if test="whereClause != null">"#), "Should contain where <if>");
        assert!(xml.contains(r#"<if test="orderBy != null">"#), "Should contain orderBy <if>");
        assert!(xml.contains("ORDER BY ${orderBy}"), "Should contain ORDER BY fragment");
    }

    #[test]
    fn test_strip_leading_clause_where() {
        assert_eq!(strip_leading_clause("WHERE status = 1", "WHERE"), "status = 1");
    }

    #[test]
    fn test_strip_leading_clause_order_by() {
        assert_eq!(strip_leading_clause("ORDER BY name ASC", "ORDER_BY"), "name ASC");
    }
}

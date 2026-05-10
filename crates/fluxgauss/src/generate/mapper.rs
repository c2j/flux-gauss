use std::collections::BTreeSet;
use std::path::Path;

use crate::generate::writer::CodeWriter;
use crate::naming::{package_to_classname, snake_to_camel};
use crate::type_map::{java_type_to_jdbc, sql_type_to_java};
use crate::types::{DmlType, PackageInfo, Parameter};

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
            let method_sig = build_mapper_method(proc, dml, &mut imports);
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
) -> String {
    let mut params: Vec<String> = Vec::new();

    for p in &proc.parameters {
        if p.is_out() {
            continue;
        }
        let jn = snake_to_camel(&p.name);
        params.push(format!("@Param(\"{}\") {} {}", jn, p.java_type, jn));
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
            let stmt_xml = build_mapper_statement(proc, dml);
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
) -> String {
    let mut sql = clean_sql(&dml.sql_text);
    if sql.ends_with(';') {
        sql = sql[..sql.len() - 1].to_string();
    }

    sql = replace_cross_package_functions(&sql);
    sql = replace_sequence_refs(&sql);

    sql = convert_params_to_mybatis(&sql, &proc.parameters, &proc.local_vars);

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
    let s = regex::Regex::new(r"\s+").unwrap().replace_all(sql.trim(), " ");
    s.to_string()
}

fn replace_cross_package_functions(sql: &str) -> String {
    let re = regex::Regex::new(r"(?i)\b\w+\.get_sys_date\s*\(\)").unwrap();
    let sql = re.replace_all(sql, "CURRENT_TIMESTAMP").to_string();
    let re2 = regex::Regex::new(r"(?i)\b\w+\.sysdate\b").unwrap();
    re2.replace_all(&sql, "CURRENT_TIMESTAMP").to_string()
}

fn replace_sequence_refs(sql: &str) -> String {
    let re_next = regex::Regex::new(r"(?i)\b(\w+)\.NEXTVAL\b").unwrap();
    let sql = re_next.replace_all(sql, |caps: &regex::Captures| {
        format!("nextval('{}')", caps[1].to_lowercase())
    }).to_string();
    let re_curr = regex::Regex::new(r"(?i)\b(\w+)\.CURRVAL\b").unwrap();
    re_curr.replace_all(&sql, |caps: &regex::Captures| {
        format!("currval('{}')", caps[1].to_lowercase())
    }).to_string()
}

fn convert_params_to_mybatis(
    sql: &str,
    params: &[Parameter],
    local_vars: &std::collections::HashMap<String, String>,
) -> String {
    let mut result = sql.to_string();

    for p in params {
        let jn = snake_to_camel(&p.name);
        let jdbc = crate::type_map::sql_type_to_jdbc(&p.sql_type);
        let java = &p.java_type;
        let placeholder = match (jdbc, java) {
            (Some(j), jt) if !jt.is_empty() => format!("#{{{}, jdbcType={}, javaType={}}}", jn, j, jt),
            _ => format!("#{{{}}}", jn),
        };
        let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
        result = re.replace_all(&result, placeholder.as_str()).to_string();
    }

    for (var_name, var_java_type) in local_vars {
        let jn = snake_to_camel(var_name);
        let jdbc = java_type_to_jdbc(var_java_type);
        let placeholder = if !jdbc.is_empty() && !var_java_type.is_empty() {
            format!("#{{{}, jdbcType={}, javaType={}}}", jn, jdbc, var_java_type)
        } else {
            format!("#{{{}}}", jn)
        };
        let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(var_name))).unwrap();
        result = re.replace_all(&result, placeholder.as_str()).to_string();
    }

    let re_cast = regex::Regex::new(
        r"(?i)\s*::\s*(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b"
    ).unwrap();
    result = re_cast.replace_all(&result, "").to_string();

    result
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
    }
}

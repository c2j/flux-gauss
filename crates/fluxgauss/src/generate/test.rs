use std::collections::BTreeSet;
use std::path::Path;

use crate::generate::mapper::is_simple_java_type;
use crate::generate::writer::CodeWriter;
use crate::naming::{java_method_name, package_to_classname, snake_to_camel};
use crate::types::{DmlType, PackageInfo};

pub fn write_service_test(
    base_path: &Path,
    pkg: &PackageInfo,
    base_package: &str,
    service_injections: &std::collections::HashMap<String, String>,
) -> std::io::Result<String> {
    let java_pkg = format!("{}.service", base_package);
    let test_dir = base_path.join(format!("src/test/java/{}/service", base_package.replace('.', "/")));
    let class_name = format!("{}Service", package_to_classname(&pkg.package_name));
    let mapper_var = lowercase_first(&package_to_classname(&pkg.package_name)) + "Mapper";
    let test_class_name = format!("{}Test", class_name);

    let mut imports: BTreeSet<String> = BTreeSet::new();
    imports.insert("import org.junit.jupiter.api.Test;".to_string());
    imports.insert("import org.junit.jupiter.api.extension.ExtendWith;".to_string());
    imports.insert("import org.mockito.InjectMocks;".to_string());
    imports.insert("import org.mockito.Mock;".to_string());
    imports.insert("import org.mockito.junit.jupiter.MockitoExtension;".to_string());
    imports.insert("import org.mockito.junit.jupiter.MockitoSettings;".to_string());
    imports.insert("import org.mockito.quality.Strictness;".to_string());
    imports.insert(format!("import {}.{};", java_pkg, class_name));
    imports.insert(format!(
        "import {}.mapper.{}Mapper;",
        base_package,
        package_to_classname(&pkg.package_name)
    ));
    imports.insert(format!("import {}.exception.BusinessException;", base_package));
    imports.insert("import static org.mockito.Mockito.*;".to_string());
    imports.insert("import static org.junit.jupiter.api.Assertions.*;".to_string());

    for (svc_var, pkg_name) in service_injections {
        let svc_class = if !pkg_name.is_empty() {
            format!("{}Service", package_to_classname(pkg_name))
        } else {
            let part = svc_var.replace("Service", "");
            format!("{}Service", package_to_classname(&part))
        };
        imports.insert(format!("import {}.service.{};", base_package, svc_class));
    }

    if pkg.procedures.iter().any(|p| p.parameters.iter().any(|p| p.is_out())) {
        imports.insert("import java.util.concurrent.atomic.AtomicReference;".to_string());
    }

    if pkg.procedures.iter().any(|p| p.parameters.iter().any(|p| p.java_type.contains("Map"))) {
        imports.insert("import java.util.Map;".to_string());
        imports.insert("import java.util.HashMap;".to_string());
    }

    let mut w = CodeWriter::new();
    w.line(&format!("package {};", java_pkg));
    w.blank();
    for imp in &imports {
        w.line(imp);
    }
    w.blank();
    w.line("@ExtendWith(MockitoExtension.class)");
    w.line("@MockitoSettings(strictness = Strictness.LENIENT)");
    if !pkg.source_file.is_empty() {
        w.line(&format!("// Source: {}", pkg.source_file));
    }
    w.line(&format!("class {} {{", test_class_name));
    w.blank();
    w.push_indent();

    w.line("@Mock");
    w.line(&format!(
        "private {} {};",
        format!("{}Mapper", package_to_classname(&pkg.package_name)),
        mapper_var
    ));

    for (svc_var, pkg_name) in service_injections {
        let svc_class = if !pkg_name.is_empty() {
            format!("{}Service", package_to_classname(pkg_name))
        } else {
            let part = svc_var.replace("Service", "");
            format!("{}Service", package_to_classname(&part))
        };
        w.blank();
        w.line("@Mock");
        w.line(&format!("private {} {};", svc_class, svc_var));
    }

    w.blank();
    w.line("@InjectMocks");
    w.line(&format!("private {} service;", class_name));

    let overload_counts: std::collections::HashMap<String, usize> = pkg.procedures.iter()
        .map(|p| java_method_name(&p.proc_name))
        .fold(std::collections::HashMap::new(), |mut m, name| {
            *m.entry(name).or_insert(0) += 1;
            m
        });
    let mut overload_idx: std::collections::HashMap<String, usize> = std::collections::HashMap::new();

    for proc in &pkg.procedures {
        w.blank();
        let java_name = java_method_name(&proc.proc_name);
        let is_overloaded = overload_counts.get(&java_name).copied().unwrap_or(1) > 1;
        let suffix = if is_overloaded {
            let idx = overload_idx.entry(java_name.clone()).or_insert(0);
            *idx += 1;
            format!("_{}", idx)
        } else {
            String::new()
        };
        let test_method = build_success_test(proc, &mapper_var, pkg, &suffix);
        for line in test_method.split('\n') {
            w.line(line);
        }
    }

    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(&test_dir)?;
    let file_path = test_dir.join(format!("{}.java", test_class_name));
    w.write_to_file(&file_path)?;
    Ok(test_class_name)
}

fn build_success_test(
    proc: &crate::types::ProcedureInfo,
    mapper_name: &str,
    pkg: &PackageInfo,
    overload_suffix: &str,
) -> String {
    let method_name = java_method_name(&proc.proc_name);
    let mut lines = Vec::new();

    let needs_disable = proc_has_unterminated_loop(proc, pkg);

    if needs_disable {
        lines.push("    @org.junit.jupiter.api.Disabled(\"auto-generated mock cannot terminate while loop\")".to_string());
    }
    lines.push("    @Test".to_string());
    lines.push("    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)".to_string());
    lines.push(format!("    void test_{}_success{}() {{", method_name, overload_suffix));

    let mut param_values: Vec<String> = Vec::new();
    let mut param_args: Vec<String> = Vec::new();

    for p in &proc.parameters {
        if p.is_out() {
            if p.is_refcursor() {
                continue;
            }
            let inner_type = p.java_type.clone();
            let ref_var = format!("{}Ref", snake_to_camel(&p.name));
            param_values.push(format!(
                "AtomicReference<{}> {} = new AtomicReference<>(null);",
                inner_type, ref_var
            ));
            param_args.push(ref_var);
        } else {
            let val = default_test_value(&p.java_type, &snake_to_camel(&p.name));
            param_values.push(format!("{} {} = {};", p.java_type, snake_to_camel(&p.name), val));
            param_args.push(snake_to_camel(&p.name));
        }
    }

    for pv in &param_values {
        lines.push(format!("        {}", pv));
    }

    let all_mock_lines = mock_all_mapper_methods(mapper_name, pkg);
    for ml in &all_mock_lines {
        lines.push(ml.clone());
    }

    let args_str = param_args.join(", ");
    if proc.is_function {
        lines.push(format!("        var result = service.{}({});", method_name, args_str));
    } else {
        lines.push(format!("        service.{}({});", method_name, args_str));
    }

    lines.push("    }".to_string());
    lines.join("\n")
}

fn proc_has_unterminated_loop(proc: &crate::types::ProcedureInfo, pkg: &PackageInfo) -> bool {
    let has_while_true = proc.java_logic_lines.iter().any(|l| {
        let t = l.trim();
        t.contains("while (true)") || t.contains("while(true)")
    });
    if has_while_true {
        return true;
    }
    let has_recursive_call = proc.service_calls.iter().any(|sc| {
        sc.package_name == pkg.package_name && sc.method_name == java_method_name(&proc.proc_name)
    });
    if has_recursive_call {
        return true;
    }
    let has_while_with_mapper = {
        let mut in_while = false;
        for l in &proc.java_logic_lines {
            let t = l.trim();
            if t.starts_with("while ") || t.starts_with("while(") {
                in_while = true;
            }
            if in_while && t.contains("mapper.") {
                return true;
            }
            if t == "}" {
                in_while = false;
            }
        }
        false
    };
    has_while_with_mapper
}

fn count_local_var_refs_in_sql(
    sql_text: &str,
    local_vars: &std::collections::HashMap<String, String>,
    params: &[crate::types::Parameter],
) -> usize {
    let param_names: std::collections::HashSet<String> = params
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| p.name.to_lowercase())
        .collect();
    let mut found: std::collections::HashSet<String> = std::collections::HashSet::new();
    let re = regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap();
    for caps in re.captures_iter(sql_text) {
        let word = caps.get(1).unwrap().as_str();
        if local_vars.contains_key(word) && !param_names.contains(&word.to_lowercase()) {
            found.insert(word.to_lowercase());
        }
    }
    found.len()
}

fn count_package_var_refs_in_sql(
    sql_text: &str,
    package_vars: &std::collections::HashMap<String, crate::types::VarInfo>,
    local_vars: &std::collections::HashMap<String, String>,
    params: &[crate::types::Parameter],
) -> usize {
    let param_names: std::collections::HashSet<String> = params
        .iter()
        .filter(|p| !p.is_out())
        .map(|p| p.name.to_lowercase())
        .collect();
    let mut found: std::collections::HashSet<String> = std::collections::HashSet::new();
    let re = regex::Regex::new(r"\b([a-zA-Z_]\w*)\b").unwrap();
    for caps in re.captures_iter(sql_text) {
        let word = caps.get(1).unwrap().as_str();
        if package_vars.contains_key(word)
            && !param_names.contains(&word.to_lowercase())
            && !local_vars.contains_key(word)
        {
            found.insert(word.to_lowercase());
        }
    }
    found.len()
}

fn extract_select_columns(sql: &str) -> Vec<String> {
    let upper = sql.to_uppercase();
    let select_pos = match upper.find("SELECT") {
        Some(p) => p,
        None => return Vec::new(),
    };
    let from_pos = match upper.find(" FROM ") {
        Some(p) => p,
        None => return Vec::new(),
    };
    if from_pos <= select_pos + 6 {
        return Vec::new();
    }
    let cols_part = &sql[select_pos + 6..from_pos];
    let mut cols = Vec::new();
    for part in cols_part.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let up = part.to_uppercase();
        if up == "*" {
            return Vec::new();
        }
        let part = if up.starts_with("DISTINCT ") {
            &part[9..]
        } else {
            part
        };
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let col_name = if let Some(as_pos) = part.to_uppercase().find(" AS ") {
            part[as_pos + 4..].trim()
        } else {
            part
        };
        let col_name = if let Some(dot_pos) = col_name.rfind('.') {
            &col_name[dot_pos + 1..]
        } else {
            col_name
        };
        let col_name = col_name.trim().trim_matches('"').trim_matches('\'');
        if !col_name.is_empty() && col_name != "*" {
            cols.push(col_name.to_lowercase());
        }
    }
    cols
}

fn column_mock_value(col_name: &str) -> String {
    let nl = col_name.to_lowercase();
    if nl.ends_with("_id") || nl.ends_with("_no") || nl == "id" {
        return "1L".to_string();
    }
    if nl == "name"
        || nl.ends_with("_name")
        || nl.ends_with("_code")
        || nl.ends_with("_type")
        || nl.ends_with("_status")
        || nl.ends_with("_desc")
        || nl.ends_with("_text")
        || nl.ends_with("_memo")
    {
        return "\"test\"".to_string();
    }
    if nl == "salary"
        || nl.ends_with("_count")
        || nl.ends_with("_qty")
        || nl.ends_with("_num")
        || nl.ends_with("_amount")
        || nl.ends_with("_total")
        || nl.ends_with("_price")
        || nl.ends_with("_salary")
        || nl.ends_with("_fee")
        || nl.ends_with("_rate")
    {
        return "java.math.BigDecimal.TEN".to_string();
    }
    if nl.ends_with("_date") || nl.ends_with("_time") {
        return "java.sql.Date.valueOf(\"2024-01-01\")".to_string();
    }
    if nl.ends_with("_flag") || nl.starts_with("is_") {
        return "true".to_string();
    }
    if nl == "age" || nl.ends_with("_age") || nl == "year" || nl.ends_with("_year") || nl == "month" || nl.ends_with("_month") || nl == "day" || nl.ends_with("_day") {
        return "1".to_string();
    }
    "1L".to_string()
}

fn build_map_mock(cols: &[String]) -> String {
    let mut s = "{ var m = new java.util.HashMap<String,Object>(); ".to_string();
    for col in cols {
        let val = column_mock_value(col);
        // MyBatis returns snake_case keys for resultType="map" (column aliases from DB)
        s.push_str(&format!("m.put(\"{}\", {}); ", col, val));
    }
    s
}

 fn mock_all_mapper_methods(mapper_name: &str, pkg: &PackageInfo) -> Vec<String> {
     let mut all_dmls: Vec<(String, DmlType, usize, bool, Option<String>, String)> = Vec::new();
     for proc in &pkg.procedures {
         for dml in &proc.dml_statements {
             let in_param_count = proc.parameters.iter().filter(|p| !p.is_out()).count();
             let local_var_count = count_local_var_refs_in_sql(&dml.sql_text, &proc.local_vars, &proc.parameters);
             let pkg_var_count = count_package_var_refs_in_sql(&dml.sql_text, &pkg.package_vars, &proc.local_vars, &proc.parameters);

             let param_java_names: std::collections::HashSet<String> = proc
                 .parameters.iter()
                 .filter(|p| !p.is_out())
                 .map(|p| crate::naming::snake_to_camel(&p.name).to_lowercase())
                 .collect();
             let out_param_count = proc.parameters.iter()
                 .filter(|p| p.is_out())
                 .filter(|p| {
                     let re = regex::Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&p.name))).unwrap();
                     re.is_match(&dml.sql_text)
                 })
                 .filter(|p| {
                     let jn = crate::naming::snake_to_camel(&p.name).to_lowercase();
                     !param_java_names.iter().any(|pn| pn == &jn)
                 })
                 .count();

             let total_params = in_param_count + local_var_count + pkg_var_count + out_param_count;
             if !all_dmls.iter().any(|(id, _, _, _, _, _)| id == &dml.method_id) {
                 all_dmls.push((dml.method_id.clone(), dml.sql_type, total_params, dml.returns_list, dml.result_type.clone(), dml.sql_text.clone()));
             }
         }
     }

    let mut lines = Vec::new();
    for (method_id, sql_type, param_count, returns_list, result_type, sql_text) in &all_dmls {
        let any_args = if *param_count > 0 {
            (0..*param_count).map(|_| "any()".to_string()).collect::<Vec<_>>().join(", ")
        } else {
            String::new()
        };
        match sql_type {
            DmlType::Select => {
                let is_scalar = result_type.as_ref().map_or(false, |rt| {
                    !rt.contains("Map<") && !rt.contains("List<")
                });
                if *returns_list {
                    let cols = extract_select_columns(sql_text);
                    if cols.is_empty() {
                        lines.push(format!(
                            "        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); when({}.{}({})).thenReturn(java.util.List.of(m)); }}",
                            mapper_name, method_id, any_args
                        ));
                    } else {
                        let mut mock = build_map_mock(&cols);
                        mock.push_str(&format!("when({}.{}({})).thenReturn(java.util.List.of(m)); }}", mapper_name, method_id, any_args));
                        lines.push(format!("        {}", mock));
                    }
                } else if is_scalar {
                    let rt = result_type.as_ref().unwrap();
                    let scalar_val = if rt == "Object" {
                        "999".to_string()
                    } else {
                        scalar_mock_value(rt)
                    };
                    lines.push(format!(
                        "        when({}.{}({})).thenReturn({});",
                        mapper_name, method_id, any_args, scalar_val
                    ));
                } else {
                    let cols = extract_select_columns(sql_text);
                    if cols.is_empty() {
                        lines.push(format!(
                            "        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); when({}.{}({})).thenReturn(m); }}",
                            mapper_name, method_id, any_args
                        ));
                    } else {
                        let mut mock = build_map_mock(&cols);
                        mock.push_str(&format!("when({}.{}({})).thenReturn(m); }}", mapper_name, method_id, any_args));
                        lines.push(format!("        {}", mock));
                    }
                }
            }
            _ => {
                lines.push(format!(
                    "        when({}.{}({})).thenReturn(1);",
                    mapper_name, method_id, any_args
                ));
            }
        }
    }
    lines
}

fn default_test_value(java_type: &str, param_name: &str) -> String {
    let tl = java_type.to_lowercase();
    let nl = param_name.to_lowercase();
    if tl.contains("long") {
        if nl.contains("id") { return "1L".to_string(); }
        return "100L".to_string();
    }
    if tl.contains("integer") || tl == "int" {
        if nl.contains("qty") || nl.contains("limit") { return "5".to_string(); }
        return "1".to_string();
    }
    if tl.contains("bigdecimal") { return "new java.math.BigDecimal(\"99.99\")".to_string(); }
    if tl.contains("double") { return "1.0d".to_string(); }
    if tl.contains("float") { return "1.0f".to_string(); }
    if tl.contains("boolean") { return "true".to_string(); }
    if tl.contains("timestamp") { return "java.sql.Timestamp.valueOf(\"2024-01-01 00:00:00\")".to_string(); }
    if tl.contains("date") { return "java.sql.Date.valueOf(\"2024-01-01\")".to_string(); }
    if tl.contains("map") { return "new java.util.HashMap<>()".to_string(); }
    format!("\"test_{}\"", param_name)
}

fn scalar_mock_value(java_type: &str) -> String {
    let tl = java_type.to_lowercase();
    if tl.contains("long") { return "999L".to_string(); }
    if tl.contains("integer") || tl == "int" { return "999".to_string(); }
    if tl.contains("bigdecimal") { return "new java.math.BigDecimal(\"999.99\")".to_string(); }
    if tl.contains("double") { return "999.0d".to_string(); }
    if tl.contains("float") { return "999.0f".to_string(); }
    if tl.contains("boolean") { return "true".to_string(); }
    if tl.contains("string") { return "\"test_value\"".to_string(); }
    if tl.contains("timestamp") { return "java.sql.Timestamp.valueOf(\"2024-01-01 00:00:00\")".to_string(); }
    if tl.contains("date") { return "java.sql.Date.valueOf(\"2024-01-01\")".to_string(); }
    "null".to_string()
}

fn lowercase_first(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{DmlStatement, DmlType, Parameter, ParamMode, ProcedureInfo};

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

    fn make_proc(name: &str) -> ProcedureInfo {
        ProcedureInfo::new(format!("pkg.{}", name), "pkg".to_string(), name.to_string())
    }

    #[test]
    fn test_basic_test_class() {
        let proc = make_proc("do_work");
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default()).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/OrderServiceTest.java"),
        ).unwrap();
        assert!(content.contains("@ExtendWith(MockitoExtension.class)"));
        assert!(content.contains("class OrderServiceTest"));
        assert!(content.contains("@Mock"));
        assert!(content.contains("private OrderMapper orderMapper;"));
        assert!(content.contains("@InjectMocks"));
        assert!(content.contains("private OrderService service;"));
        assert!(content.contains("test_doWork_success"));
    }

    #[test]
    fn test_mock_insert_dml() {
        let mut proc = make_proc("create_order");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Insert,
            method_id: "insertOrder".to_string(),
            sql_text: "insert into t values(1)".to_string(),
            result_type: None,
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default()).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/OrderServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(orderMapper.insertOrder()).thenReturn(1);"));
    }

    #[test]
    fn test_mock_select_dml() {
        let mut proc = make_proc("get_data");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectData".to_string(),
            sql_text: "select * from t".to_string(),
            result_type: None,
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default()).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/DataServiceTest.java"),
        ).unwrap();
        assert!(content.contains("HashMap<String,Object>"));
        assert!(content.contains("selectData"));
    }

    #[test]
    fn test_mock_select_dml_returns_list() {
        let mut proc = make_proc("list_data");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectListData".to_string(),
            sql_text: "select * from t".to_string(),
            result_type: None,
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default()).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/DataServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(dataMapper.selectListData()).thenReturn(java.util.List.of(m))"));
    }

    #[test]
    fn test_mock_select_dml_scalar_integer() {
        let mut proc = make_proc("check_stock");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectCheckStock".to_string(),
            sql_text: "select count(*) into v_count from t".to_string(),
            result_type: Some("Integer".to_string()),
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_inventory", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default()).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/InventoryServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(inventoryMapper.selectCheckStock()).thenReturn(999)"));
        assert!(!content.contains("HashMap"));
    }

    #[test]
    fn test_mock_select_dml_scalar_long() {
        let mut proc = make_proc("get_id");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectGetId".to_string(),
            sql_text: "select seq.nextval into v_id from dual".to_string(),
            result_type: Some("Long".to_string()),
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_service_test(dir.path(), &pkg, "com.example.demo", &Default::default()).unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/service/OrderServiceTest.java"),
        ).unwrap();
        assert!(content.contains("when(orderMapper.selectGetId()).thenReturn(999L)"));
        assert!(!content.contains("HashMap"));
    }

    #[test]
    fn test_default_test_values() {
        assert_eq!(default_test_value("Long", "pOrderId"), "1L");
        assert_eq!(default_test_value("long", "pCount"), "100L");
        assert_eq!(default_test_value("Integer", "pQty"), "5");
        assert_eq!(default_test_value("int", "pStatus"), "1");
        assert_eq!(default_test_value("String", "pName"), "\"test_pName\"");
        assert_eq!(default_test_value("boolean", "pFlag"), "true");
    }

    #[test]
    fn test_extract_select_columns_basic() {
        let sql = "select id, name, salary from t_employees where dept_id = p_dept_id order by id";
        let cols = extract_select_columns(sql);
        assert_eq!(cols, vec!["id", "name", "salary"]);
    }

    #[test]
    fn test_extract_select_columns_star() {
        let sql = "select * from t";
        let cols = extract_select_columns(sql);
        assert!(cols.is_empty());
    }

    #[test]
    fn test_extract_select_columns_alias() {
        let sql = "select e.id, e.name as emp_name, count(*) as cnt from emp e";
        let cols = extract_select_columns(sql);
        assert_eq!(cols, vec!["id", "emp_name", "cnt"]);
    }

    #[test]
    fn test_extract_select_columns_distinct() {
        let sql = "select distinct dept_id, dept_name from departments";
        let cols = extract_select_columns(sql);
        assert_eq!(cols, vec!["dept_id", "dept_name"]);
    }

    #[test]
    fn test_extract_select_columns_no_from() {
        let sql = "insert into t values(1)";
        let cols = extract_select_columns(sql);
        assert!(cols.is_empty());
    }

    #[test]
    fn test_column_mock_value_patterns() {
        assert_eq!(column_mock_value("id"), "1L");
        assert_eq!(column_mock_value("order_id"), "1L");
        assert_eq!(column_mock_value("order_no"), "1L");
        assert_eq!(column_mock_value("name"), "\"test\"");
        assert_eq!(column_mock_value("status_code"), "\"test\"");
        assert_eq!(column_mock_value("salary"), "java.math.BigDecimal.TEN");
        assert_eq!(column_mock_value("total_amount"), "java.math.BigDecimal.TEN");
        assert_eq!(column_mock_value("created_date"), "java.sql.Date.valueOf(\"2024-01-01\")");
        assert_eq!(column_mock_value("is_active"), "true");
        assert_eq!(column_mock_value("age"), "1");
        assert_eq!(column_mock_value("unknown_col"), "1L");
    }

    #[test]
    fn test_mock_select_with_columns() {
        let mut proc = make_proc("get_data");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectData".to_string(),
            sql_text: "select id, name, salary from t_employees".to_string(),
            result_type: Some("Map<String, Object>".to_string()),
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let lines = mock_all_mapper_methods("dataMapper", &pkg);
        let joined = lines.join("\n");
        assert!(joined.contains("m.put(\"id\", 1L)"));
        assert!(joined.contains("m.put(\"name\", \"test\")"));
        assert!(joined.contains("m.put(\"salary\", java.math.BigDecimal.TEN)"));
        assert!(joined.contains("when(dataMapper.selectData()).thenReturn(m)"));
    }

    #[test]
    fn test_mock_select_returns_list_with_columns() {
        let mut proc = make_proc("list_data");
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectListData".to_string(),
            sql_text: "select id, name from t".to_string(),
            result_type: None,
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: true,
            extra_params: Vec::new(),
        });
        let pkg = make_pkg("pkg_data", vec![proc]);
        let lines = mock_all_mapper_methods("dataMapper", &pkg);
        let joined = lines.join("\n");
        assert!(joined.contains("m.put(\"id\", 1L)"));
        assert!(joined.contains("m.put(\"name\", \"test\")"));
        assert!(joined.contains("when(dataMapper.selectListData()).thenReturn(java.util.List.of(m))"));
    }
}

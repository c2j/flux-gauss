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

    for proc in &pkg.procedures {
        w.blank();
        let test_method = build_success_test(proc, &mapper_var, pkg);
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
) -> String {
    let method_name = java_method_name(&proc.proc_name);
    let mut lines = Vec::new();

    lines.push("    @Test".to_string());
    lines.push("    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)".to_string());
    lines.push(format!("    void test_{}_success() {{", method_name));

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

fn mock_all_mapper_methods(mapper_name: &str, pkg: &PackageInfo) -> Vec<String> {
    let mut all_dmls: Vec<(String, DmlType, usize)> = Vec::new();
    for proc in &pkg.procedures {
        for dml in &proc.dml_statements {
            let in_param_count = proc.parameters.iter().filter(|p| !p.is_out()).count();
            if !all_dmls.iter().any(|(id, _, _)| id == &dml.method_id) {
                all_dmls.push((dml.method_id.clone(), dml.sql_type, in_param_count));
            }
        }
    }

    let mut lines = Vec::new();
    for (method_id, sql_type, param_count) in &all_dmls {
        let any_args = if *param_count > 0 {
            (0..*param_count).map(|_| "any()".to_string()).collect::<Vec<_>>().join(", ")
        } else {
            String::new()
        };
        match sql_type {
            DmlType::Select => {
                lines.push(format!(
                    "        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); when({}.{}({})).thenReturn(m); }}",
                    mapper_name, method_id, any_args
                ));
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
    format!("\"test_{}\"", param_name)
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
    fn test_default_test_values() {
        assert_eq!(default_test_value("Long", "pOrderId"), "1L");
        assert_eq!(default_test_value("long", "pCount"), "100L");
        assert_eq!(default_test_value("Integer", "pQty"), "5");
        assert_eq!(default_test_value("int", "pStatus"), "1");
        assert_eq!(default_test_value("String", "pName"), "\"test_pName\"");
        assert_eq!(default_test_value("boolean", "pFlag"), "true");
    }
}

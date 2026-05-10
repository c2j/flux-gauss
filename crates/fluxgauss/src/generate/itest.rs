use std::path::Path;

use crate::generate::writer::CodeWriter;
use crate::naming::package_to_classname;
use crate::types::PackageInfo;

pub fn write_itest_class(
    base_path: &Path,
    pkg: &PackageInfo,
    base_package: &str,
) -> std::io::Result<String> {
    let java_pkg = format!("{}.service", base_package);
    let itest_dir = base_path.join(format!(
        "src/test/java/{}/itest",
        base_package.replace('.', "/")
    ));
    let class_name = format!("{}Service", package_to_classname(&pkg.package_name));
    let itest_class_name = format!("{}IntegrationTest", class_name);

    let mut w = CodeWriter::new();
    w.line(&format!("package {}.itest;", base_package));
    w.blank();
    w.line("import org.junit.jupiter.api.Test;");
    w.line("import org.junit.jupiter.api.extension.ExtendWith;");
    w.line("import org.springframework.beans.factory.annotation.Autowired;");
    w.line("import org.springframework.boot.test.context.SpringBootTest;");
    w.line("import org.springframework.test.context.junit.jupiter.SpringExtension;");
    w.line(&format!("import {}.service.{};", base_package, class_name));
    w.blank();
    w.line("@ExtendWith(SpringExtension.class)");
    w.line("@SpringBootTest");
    w.line(&format!("class {} {{", itest_class_name));
    w.push_indent();
    w.blank();
    w.line("@Autowired");
    w.line(&format!("private {} {};", class_name, {
        let cn = package_to_classname(&pkg.package_name);
        let mut c = cn.chars();
        match c.next() {
            Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
            None => String::new(),
        }
    } + "Service"));
    w.blank();

    for proc in &pkg.procedures {
        let method_name = crate::naming::java_method_name(&proc.proc_name);
        w.line("@Test");
        w.line(&format!("void test_{}() {{", method_name));
        w.push_indent();
        w.line(&format!(
            "// TODO: implement integration test for {}",
            proc.proc_name
        ));
        w.pop_indent();
        w.line("}");
        w.blank();
    }

    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(&itest_dir)?;
    let file_path = itest_dir.join(format!("{}.java", itest_class_name));
    w.write_to_file(&file_path)?;
    Ok(itest_class_name)
}

pub fn write_abstract_integration_test(
    base_path: &Path,
    base_package: &str,
) -> std::io::Result<()> {
    let itest_dir = base_path.join(format!(
        "src/test/java/{}/itest",
        base_package.replace('.', "/")
    ));

    let mut w = CodeWriter::new();
    w.line(&format!("package {}.itest;", base_package));
    w.blank();
    w.line("import org.springframework.boot.test.context.SpringBootTest;");
    w.line("import org.springframework.test.context.ActiveProfiles;");
    w.blank();
    w.line("@SpringBootTest");
    w.line("@ActiveProfiles(\"integration\")");
    w.line("public abstract class AbstractIntegrationTest {");
    w.line("}");

    std::fs::create_dir_all(&itest_dir)?;
    w.write_to_file(&itest_dir.join("AbstractIntegrationTest.java"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::ProcedureInfo;

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
    fn test_itest_class_content() {
        let proc = make_proc("do_work");
        let pkg = make_pkg("pkg_order", vec![proc]);
        let dir = tempfile::tempdir().unwrap();
        write_itest_class(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/itest/OrderServiceIntegrationTest.java"),
        ).unwrap();
        assert!(content.contains("@SpringBootTest"));
        assert!(content.contains("class OrderServiceIntegrationTest"));
        assert!(content.contains("@Autowired"));
        assert!(content.contains("test_doWork()"));
    }

    #[test]
    fn test_abstract_integration_test() {
        let dir = tempfile::tempdir().unwrap();
        write_abstract_integration_test(dir.path(), "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/itest/AbstractIntegrationTest.java"),
        ).unwrap();
        assert!(content.contains("public abstract class AbstractIntegrationTest"));
        assert!(content.contains("@ActiveProfiles(\"integration\")"));
    }

    #[test]
    fn test_itest_multiple_procedures() {
        let p1 = make_proc("create_order");
        let p2 = make_proc("cancel_order");
        let pkg = make_pkg("pkg_order", vec![p1, p2]);
        let dir = tempfile::tempdir().unwrap();
        write_itest_class(dir.path(), &pkg, "com.example.demo").unwrap();
        let content = std::fs::read_to_string(
            dir.path().join("src/test/java/com/example/demo/itest/OrderServiceIntegrationTest.java"),
        ).unwrap();
        assert!(content.contains("test_createOrder()"));
        assert!(content.contains("test_cancelOrder()"));
    }
}

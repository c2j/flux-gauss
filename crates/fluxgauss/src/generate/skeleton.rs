use std::path::Path;

use crate::config::AppConfig;
use crate::generate::writer::CodeWriter;

pub fn write_skeleton_files(
    output_dir: &Path,
    config: &AppConfig,
    base_package: &str,
) -> std::io::Result<Vec<String>> {
    let mut generated = Vec::new();

    if !output_dir.join("pom.xml").exists() {
        write_pom_xml(output_dir, base_package)?;
        generated.push("pom.xml".to_string());
    }

    let resources_dir = output_dir.join("src/main/resources");
    let yml_path = resources_dir.join("application.yml");
    if !yml_path.exists() {
        write_application_yml(&resources_dir, config)?;
        generated.push("src/main/resources/application.yml".to_string());
    }

    let java_dir = output_dir.join(format!(
        "src/main/java/{}",
        base_package.replace('.', "/")
    ));

    let app_path = java_dir.join("DemoApplication.java");
    if !app_path.exists() {
        write_main_application(&java_dir, base_package)?;
        generated.push("src/main/java/.../DemoApplication.java".to_string());
    }

    let exc_dir = java_dir.join("exception");
    let exc_path = exc_dir.join("BusinessException.java");
    if !exc_path.exists() {
        write_business_exception(&exc_dir, base_package)?;
        generated.push("src/main/java/.../exception/BusinessException.java".to_string());
    }

    Ok(generated)
}

fn write_pom_xml(output_dir: &Path, _base_package: &str) -> std::io::Result<()> {
    let mut w = CodeWriter::new();
    w.line("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
    w.line("<project xmlns=\"http://maven.apache.org/POM/4.0.0\"");
    w.line("         xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"");
    w.line("         xsi:schemaLocation=\"http://maven.apache.org/POM/4.0.0");
    w.line("         https://maven.apache.org/xsd/maven-4.0.0.xsd\">");
    w.push_indent();
    w.line("<modelVersion>4.0.0</modelVersion>");
    w.blank();
    w.line("<parent>");
    w.push_indent();
    w.line("<groupId>org.springframework.boot</groupId>");
    w.line("<artifactId>spring-boot-starter-parent</artifactId>");
    w.line("<version>3.2.5</version>");
    w.line("<relativePath/>");
    w.pop_indent();
    w.line("</parent>");
    w.blank();
    w.line("<groupId>com.example</groupId>");
    w.line("<artifactId>demo</artifactId>");
    w.line("<version>0.0.1-SNAPSHOT</version>");
    w.line("<name>demo</name>");
    w.blank();
    w.line("<properties>");
    w.push_indent();
    w.line("<java.version>17</java.version>");
    w.pop_indent();
    w.line("</properties>");
    w.blank();
    w.line("<dependencies>");
    w.push_indent();
    write_dep(&mut w, "org.springframework.boot", "spring-boot-starter-web", None, None);
    write_dep(&mut w, "org.mybatis.spring.boot", "mybatis-spring-boot-starter", Some("3.0.3"), None);
    write_dep(&mut w, "org.postgresql", "postgresql", None, Some("runtime"));
    write_dep(&mut w, "org.projectlombok", "lombok", None, Some("optional"));
    write_dep(&mut w, "org.springframework.boot", "spring-boot-starter-test", None, Some("test"));
    write_dep(&mut w, "org.testcontainers", "testcontainers", Some("1.19.8"), Some("test"));
    write_dep(&mut w, "org.testcontainers", "postgresql", Some("1.19.8"), Some("test"));
    write_dep(&mut w, "org.testcontainers", "junit-jupiter", Some("1.19.8"), Some("test"));
    write_dep(&mut w, "org.springframework.boot", "spring-boot-testcontainers", None, Some("test"));
    w.pop_indent();
    w.line("</dependencies>");
    w.blank();
    w.line("<build>");
    w.push_indent();
    w.line("<plugins>");
    w.push_indent();
    w.line("<plugin>");
    w.push_indent();
    w.line("<groupId>org.springframework.boot</groupId>");
    w.line("<artifactId>spring-boot-maven-plugin</artifactId>");
    w.line("<configuration>");
    w.push_indent();
    w.line("<excludes>");
    w.push_indent();
    w.line("<exclude>");
    w.push_indent();
    w.line("<groupId>org.projectlombok</groupId>");
    w.line("<artifactId>lombok</artifactId>");
    w.pop_indent();
    w.line("</exclude>");
    w.pop_indent();
    w.line("</excludes>");
    w.pop_indent();
    w.line("</configuration>");
    w.pop_indent();
    w.line("</plugin>");
    w.line("<plugin>");
    w.push_indent();
    w.line("<groupId>org.apache.maven.plugins</groupId>");
    w.line("<artifactId>maven-surefire-plugin</artifactId>");
    w.line("<configuration>");
    w.push_indent();
    w.line("<argLine>-Dnet.bytebuddy.experimental=true</argLine>");
    w.line("<excludes>");
    w.push_indent();
    w.line("<exclude>**/itest/**</exclude>");
    w.pop_indent();
    w.line("</excludes>");
    w.pop_indent();
    w.line("</configuration>");
    w.pop_indent();
    w.line("</plugin>");
    w.pop_indent();
    w.line("</plugins>");
    w.pop_indent();
    w.line("</build>");
    w.blank();
    w.line("<profiles>");
    w.push_indent();
    w.line("<profile>");
    w.push_indent();
    w.line("<id>integration</id>");
    w.line("<build>");
    w.push_indent();
    w.line("<plugins>");
    w.push_indent();
    w.line("<plugin>");
    w.push_indent();
    w.line("<groupId>org.apache.maven.plugins</groupId>");
    w.line("<artifactId>maven-surefire-plugin</artifactId>");
    w.line("<configuration>");
    w.push_indent();
    w.line("<includes>");
    w.push_indent();
    w.line("<include>**/itest/*Test.java</include>");
    w.line("<include>**/*IntegrationTest.java</include>");
    w.pop_indent();
    w.line("</includes>");
    w.line("<excludes combine.self=\"override\" />");
    w.pop_indent();
    w.line("</configuration>");
    w.pop_indent();
    w.line("</plugin>");
    w.pop_indent();
    w.line("</plugins>");
    w.pop_indent();
    w.line("</build>");
    w.pop_indent();
    w.line("</profile>");
    w.pop_indent();
    w.line("</profiles>");
    w.pop_indent();
    w.line("</project>");

    w.write_to_file(&output_dir.join("pom.xml"))
}

fn write_dep(
    w: &mut CodeWriter,
    group: &str,
    artifact: &str,
    version: Option<&str>,
    scope: Option<&str>,
) {
    w.line("<dependency>");
    w.push_indent();
    w.line(&format!("<groupId>{}</groupId>", group));
    w.line(&format!("<artifactId>{}</artifactId>", artifact));
    if let Some(v) = version {
        w.line(&format!("<version>{}</version>", v));
    }
    if let Some(s) = scope {
        w.line(&format!("<scope>{}</scope>", s));
    }
    w.pop_indent();
    w.line("</dependency>");
}

fn write_application_yml(resources_dir: &Path, config: &AppConfig) -> std::io::Result<()> {
    let db = config.database.as_ref();
    let it = config.integration_test.as_ref();
    let url = db.and_then(|d| d.url.as_deref())
        .or_else(|| it.and_then(|i| i.url.as_deref()))
        .unwrap_or("jdbc:postgresql://localhost:5432/demo");
    let username = db.and_then(|d| d.username.as_deref())
        .or_else(|| it.and_then(|i| i.username.as_deref()))
        .unwrap_or("postgres");
    let password = db.and_then(|d| d.password.as_deref())
        .or_else(|| it.and_then(|i| i.password.as_deref()))
        .unwrap_or("postgres");
    let driver = db.and_then(|d| d.driver.as_deref()).unwrap_or("org.postgresql.Driver");

    let mut w = CodeWriter::new();
    w.line("spring:");
    w.push_indent();
    w.line("datasource:");
    w.push_indent();
    w.line(&format!("url: {}", url));
    w.line(&format!("username: {}", username));
    w.line(&format!("password: {}", password));
    w.line(&format!("driver-class-name: {}", driver));
    w.pop_indent();
    w.pop_indent();
    w.blank();
    w.line("mybatis:");
    w.push_indent();
    w.line("mapper-locations: classpath:mapper/*.xml");
    w.line("configuration:");
    w.push_indent();
    w.line("map-underscore-to-camel-case: true");
    w.pop_indent();
    w.pop_indent();

    std::fs::create_dir_all(resources_dir)?;
    w.write_to_file(&resources_dir.join("application.yml"))
}

fn write_main_application(java_dir: &Path, base_package: &str) -> std::io::Result<()> {
    let mut w = CodeWriter::new();
    w.line(&format!("package {};", base_package));
    w.blank();
    w.line("import org.mybatis.spring.annotation.MapperScan;");
    w.line("import org.springframework.boot.SpringApplication;");
    w.line("import org.springframework.boot.autoconfigure.SpringBootApplication;");
    w.blank();
    w.line("@SpringBootApplication");
    w.line(&format!("@MapperScan(\"{}.mapper\")", base_package));
    w.line("public class DemoApplication {");
    w.push_indent();
    w.line("public static void main(String[] args) {");
    w.push_indent();
    w.line("SpringApplication.run(DemoApplication.class, args);");
    w.pop_indent();
    w.line("}");
    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(java_dir)?;
    w.write_to_file(&java_dir.join("DemoApplication.java"))
}

fn write_business_exception(exc_dir: &Path, base_package: &str) -> std::io::Result<()> {
    let mut w = CodeWriter::new();
    w.line(&format!("package {}.exception;", base_package));
    w.blank();
    w.line("public class BusinessException extends RuntimeException {");
    w.push_indent();
    w.line("public BusinessException(String message) {");
    w.push_indent();
    w.line("super(message);");
    w.pop_indent();
    w.line("}");
    w.blank();
    w.line("public BusinessException(String message, Throwable cause) {");
    w.push_indent();
    w.line("super(message, cause);");
    w.pop_indent();
    w.line("}");
    w.pop_indent();
    w.line("}");

    std::fs::create_dir_all(exc_dir)?;
    w.write_to_file(&exc_dir.join("BusinessException.java"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn test_pom_xml_content() -> std::io::Result<()> {
        let dir = tempfile::tempdir()?;
        let config = AppConfig::default();
        write_pom_xml(dir.path(), "com.example.demo")?;
        let content = std::fs::read_to_string(dir.path().join("pom.xml"))?;
        assert!(content.contains("spring-boot-starter-parent"));
        assert!(content.contains("3.2.5"));
        assert!(content.contains("mybatis-spring-boot-starter"));
        assert!(content.contains("testcontainers"));
        assert!(content.contains("maven-surefire-plugin"));
        assert!(content.contains("<java.version>17</java.version>"));
        Ok(())
    }

    #[test]
    fn test_application_yml_content() -> std::io::Result<()> {
        let dir = tempfile::tempdir()?;
        let res_dir = dir.path().join("resources");
        let config = AppConfig::default();
        write_application_yml(&res_dir, &config)?;
        let content = std::fs::read_to_string(res_dir.join("application.yml"))?;
        assert!(content.contains("spring:"));
        assert!(content.contains("datasource:"));
        assert!(content.contains("mybatis:"));
        assert!(content.contains("mapper-locations: classpath:mapper/*.xml"));
        assert!(content.contains("map-underscore-to-camel-case: true"));
        Ok(())
    }

    #[test]
    fn test_main_application_content() -> std::io::Result<()> {
        let dir = tempfile::tempdir()?;
        let java_dir = dir.path().join("java");
        write_main_application(&java_dir, "com.example.demo")?;
        let content = std::fs::read_to_string(java_dir.join("DemoApplication.java"))?;
        assert!(content.contains("package com.example.demo;"));
        assert!(content.contains("@SpringBootApplication"));
        assert!(content.contains("@MapperScan(\"com.example.demo.mapper\")"));
        assert!(content.contains("public class DemoApplication"));
        assert!(content.contains("SpringApplication.run"));
        Ok(())
    }

    #[test]
    fn test_business_exception_content() -> std::io::Result<()> {
        let dir = tempfile::tempdir()?;
        let exc_dir = dir.path().join("exception");
        write_business_exception(&exc_dir, "com.example.demo")?;
        let content = std::fs::read_to_string(exc_dir.join("BusinessException.java"))?;
        assert!(content.contains("package com.example.demo.exception;"));
        assert!(content.contains("public class BusinessException extends RuntimeException"));
        assert!(content.contains("public BusinessException(String message)"));
        assert!(content.contains("public BusinessException(String message, Throwable cause)"));
        Ok(())
    }
}

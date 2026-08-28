use serde::Deserialize;

#[derive(Debug, Default, Deserialize)]
pub struct AppConfig {
    pub output_dir: Option<String>,
    pub base_package: Option<String>,
    pub logger: Option<LoggerConfig>,
    pub database: Option<DatabaseConfig>,
    pub sources: Option<Vec<String>>,
    pub java_packages: Option<Vec<JavaPackageMapping>>,
    pub integration_test: Option<IntegrationTestConfig>,
    pub encoding: Option<String>,
}

impl AppConfig {
    pub fn output_dir_or_default(&self) -> String {
        self.output_dir.clone().unwrap_or_else(|| "./dest".to_string())
    }

    pub fn base_package_or_default(&self) -> String {
        self.base_package.clone().unwrap_or_else(|| "com.example.demo".to_string())
    }

    pub fn encoding_or_default(&self) -> String {
        self.encoding.clone().unwrap_or_else(|| "utf-8".to_string())
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum LoggerConfig {
    Preset(String),
    Custom(CustomLoggerConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct CustomLoggerConfig {
    pub imports: Vec<String>,
    pub declaration: String,
    #[serde(default)]
    pub pom: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
pub struct DatabaseConfig {
    #[serde(default = "default_db_url")]
    pub url: Option<String>,
    #[serde(default = "default_db_user")]
    pub username: Option<String>,
    #[serde(default = "default_db_pass")]
    pub password: Option<String>,
    #[serde(default = "default_db_driver")]
    pub driver: Option<String>,
}

fn default_db_url() -> Option<String> {
    Some("jdbc:postgresql://localhost:5432/demo".into())
}
fn default_db_user() -> Option<String> {
    Some("postgres".into())
}
fn default_db_pass() -> Option<String> {
    Some("postgres".into())
}
fn default_db_driver() -> Option<String> {
    Some("org.postgresql.Driver".into())
}

#[derive(Debug, Deserialize)]
pub struct JavaPackageMapping {
    pub package: String,
    pub sources: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
pub struct IntegrationTestConfig {
    #[serde(default)]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default)]
    pub password: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedLogger {
    pub imports: Vec<String>,
    pub declaration: String,
    pub pom: Vec<String>,
}

pub fn resolve_logger_config(config: &AppConfig) -> ResolvedLogger {
    match &config.logger {
        Some(LoggerConfig::Preset(name)) => logger_preset(name),
        Some(LoggerConfig::Custom(custom)) => ResolvedLogger {
            imports: custom.imports.clone(),
            declaration: custom.declaration.clone(),
            pom: custom.pom.clone().unwrap_or_default(),
        },
        None => logger_preset("slf4j"),
    }
}

fn logger_preset(name: &str) -> ResolvedLogger {
    match name {
        "slf4j" => ResolvedLogger {
            imports: vec![
                "import org.slf4j.Logger;".into(),
                "import org.slf4j.LoggerFactory;".into(),
            ],
            declaration: "private static final Logger log = LoggerFactory.getLogger({class_name}.class);".into(),
            pom: vec![],
        },
        "log4j2" => ResolvedLogger {
            imports: vec![
                "import org.apache.logging.log4j.LogManager;".into(),
                "import org.apache.logging.log4j.Logger;".into(),
            ],
            declaration: "private static final Logger log = LogManager.getLogger({class_name}.class);".into(),
            pom: vec![
                "<dependency>\n    <groupId>org.apache.logging.log4j</groupId>\n    <artifactId>log4j-core</artifactId>\n    <version>2.23.1</version>\n</dependency>".into(),
                "<dependency>\n    <groupId>org.apache.logging.log4j</groupId>\n    <artifactId>log4j-slf4j2-impl</artifactId>\n    <version>2.23.1</version>\n</dependency>".into(),
            ],
        },
        "commons-logging" => ResolvedLogger {
            imports: vec![
                "import org.apache.commons.logging.Log;".into(),
                "import org.apache.commons.logging.LogFactory;".into(),
            ],
            declaration: "private static final Log log = LogFactory.getLog({class_name}.class);".into(),
            pom: vec![
                "<dependency>\n    <groupId>commons-logging</groupId>\n    <artifactId>commons-logging</artifactId>\n    <version>1.3.1</version>\n</dependency>".into(),
            ],
        },
        "jul" => ResolvedLogger {
            imports: vec!["import java.util.logging.Logger;".into()],
            declaration: "private static final Logger log = Logger.getLogger({class_name}.class.getName());".into(),
            pom: vec![],
        },
        other => panic!("Unknown logger preset: '{}'. Supported: slf4j, log4j2, commons-logging, jul", other),
    }
}

pub fn load_config(path: &std::path::Path) -> Result<AppConfig, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(path)?;
    let config: AppConfig = serde_yaml::from_str(&content)?;
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_config_minimal() {
        let yaml = "output_dir: ./dest\n";
        let config: AppConfig = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(config.output_dir.as_deref(), Some("./dest"));
        assert!(config.sources.is_none());
    }

    #[test]
    fn test_load_config_full() {
        let yaml = r#"
output_dir: ./dest
base_package: com.example.demo
logger: slf4j
sources:
  - demo-project/sql/pkg_order.sql
  - demo-project/sql/pkg_product.sql
"#;
        let config: AppConfig = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(config.base_package.as_deref(), Some("com.example.demo"));
        assert!(matches!(config.logger, Some(LoggerConfig::Preset(ref s)) if s == "slf4j"));
        assert_eq!(config.sources.as_ref().map(|s| s.len()), Some(2));
    }

    #[test]
    fn test_load_config_custom_logger() {
        let yaml = r#"
logger:
  imports:
    - "import com.myco.Logger;"
  declaration: "private static final Logger log = Logger.get({class_name}.class);"
"#;
        let config: AppConfig = serde_yaml::from_str(yaml).unwrap();
        match config.logger {
            Some(LoggerConfig::Custom(custom)) => {
                assert_eq!(custom.imports.len(), 1);
                assert!(custom.declaration.contains("{class_name}"));
            }
            _ => panic!("Expected Custom logger"),
        }
    }

    #[test]
    fn test_resolve_logger_slf4j() {
        let config = AppConfig { logger: Some(LoggerConfig::Preset("slf4j".into())), ..Default::default() };
        let resolved = resolve_logger_config(&config);
        assert_eq!(resolved.imports.len(), 2);
        assert!(resolved.pom.is_empty());
    }

    #[test]
    fn test_resolve_logger_default() {
        let config = AppConfig::default();
        let resolved = resolve_logger_config(&config);
        assert!(resolved.declaration.contains("LoggerFactory"));
    }

    #[test]
    fn test_base_package_default() {
        let config = AppConfig::default();
        assert_eq!(config.base_package_or_default(), "com.example.demo");
    }

    #[test]
    fn test_output_dir_default() {
        let config = AppConfig::default();
        assert_eq!(config.output_dir_or_default(), "./dest");
    }

    #[test]
    fn test_load_config_integration_test() {
        let yaml = r#"
integration_test:
  enabled: true
  mode: remote
  url: jdbc:postgresql://localhost:5432/postgres
  username: gaussdb
  password: secret
"#;
        let config: AppConfig = serde_yaml::from_str(yaml).unwrap();
        let it = config.integration_test.unwrap();
        assert_eq!(it.enabled, Some(true));
        assert_eq!(it.mode.as_deref(), Some("remote"));
    }
}

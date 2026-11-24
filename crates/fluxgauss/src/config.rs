use serde::Deserialize;

#[derive(Debug, Default, Deserialize)]
pub struct AppConfig {
    pub output_dir: Option<String>,
    pub base_package: Option<String>,
    pub logger: Option<LoggerConfig>,
    pub database: Option<DatabaseConfig>,
    pub sources: Option<Vec<String>>,
    /// Additional routine source roots (files, dirs, globs) merged into `sources` (#120)
    pub source_dirs: Option<Vec<String>>,
    /// DDL-only sources scanned for CREATE TABLE (never parsed/converted) (#120)
    pub ddl_sources: Option<Vec<String>>,
    pub ddl_source_dirs: Option<Vec<String>>,
    /// Whitelist of `pkg.proc[:N]` routines; transitive call closure is auto-included (#120)
    pub procedure_filter: Option<Vec<String>>,
    /// codeweb dependency-graph file closure (#120)
    pub dependency_graph: Option<DependencyGraphConfig>,
    pub java_packages: Option<Vec<JavaPackageMapping>>,
    pub integration_test: Option<IntegrationTestConfig>,
    /// Declared composite/record types whose `Map<String,Object>`
    /// representation uses parallel `java.util.List` fields (delivery shim
    /// types such as `map_object`). Matching variables/parameters get typed
    /// List field reads, array-append assignment lowering, and 1-based
    /// subscript/indexOf semantics.
    pub composite_type_shims: Option<Vec<CompositeTypeShim>>,
    pub encoding: Option<String>,
}

/// One entry of `composite_type_shims`: a declared SQL composite/record type
/// name plus its Java-side representation contract.
#[derive(Debug, Default, Deserialize)]
pub struct CompositeTypeShim {
    /// Declared SQL type name, matched case-insensitively against either the
    /// full declared type or its last dot-separated segment (so `map_object`
    /// matches `bigfund.map_object`).
    pub sql_type: String,
    /// Representation kind. Only `parallel_lists` is implemented: array-typed
    /// record fields lower to parallel `java.util.List` slots inside the
    /// `Map<String,Object>` and `field || elem` assignments append.
    pub representation: Option<String>,
    /// Record field declarations: `field_name → SQL type` (e.g. `k: text[]`).
    /// Required for shim types created inside `DO $$ EXECUTE` dynamic strings,
    /// which the parser cannot see. When the type IS visible to extraction,
    /// extracted field types take precedence.
    pub fields: Option<std::collections::HashMap<String, String>>,
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

    /// Routine files: `sources` + `source_dirs`, with dir/glob expansion (#120)
    pub fn resolve_source_files(&self) -> Vec<std::path::PathBuf> {
        let mut entries = self.sources.clone().unwrap_or_default();
        entries.extend(self.source_dirs.clone().unwrap_or_default());
        expand_source_patterns(&entries)
    }

    /// DDL-only files: `ddl_sources` + `ddl_source_dirs` (#120)
    pub fn resolve_ddl_files(&self) -> Vec<std::path::PathBuf> {
        let mut entries = self.ddl_sources.clone().unwrap_or_default();
        entries.extend(self.ddl_source_dirs.clone().unwrap_or_default());
        expand_source_patterns(&entries)
    }
}

/// Expand source entries: plain files pass through, directories contribute
/// their *.sql files recursively, `*`/`?` glob patterns are expanded.
/// Order-preserving dedupe; nonexistent paths are kept (caller validates).
pub fn expand_source_patterns(entries: &[String]) -> Vec<std::path::PathBuf> {
    let mut files: Vec<std::path::PathBuf> = Vec::new();
    for entry in entries {
        let e = entry.trim();
        if e.is_empty() {
            continue;
        }
        if e.contains('*') || e.contains('?') || e.contains('[') {
            files.extend(expand_glob(e));
        } else {
            let path = std::path::Path::new(e);
            if path.is_dir() {
                rglob_sql(path, &mut files);
            } else {
                files.push(path.to_path_buf());
            }
        }
    }
    let mut seen = std::collections::HashSet::new();
    files.retain(|p| seen.insert(p.clone()));
    files
}

fn rglob_sql(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    let mut entries: Vec<_> = match std::fs::read_dir(dir) {
        Ok(rd) => rd.filter_map(|e| e.ok()).collect(),
        Err(_) => return,
    };
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let path = entry.path();
        if path.is_dir() {
            rglob_sql(&path, out);
        } else if path.extension().map(|x| x == "sql").unwrap_or(false) {
            out.push(path);
        }
    }
}

/// Minimal glob expansion supporting `*` (within a path segment), `**`
/// (any depth) and `?`. Character classes `[...]` are treated literally.
fn expand_glob(pattern: &str) -> Vec<std::path::PathBuf> {
    // Literal base directory: everything up to the first path segment that
    // contains a wildcard.
    let mut base = if pattern.starts_with('/') { std::path::PathBuf::from("/") } else { std::path::PathBuf::new() };
    let mut rest_start = 0usize;
    for i in pattern.match_indices('/').map(|(i, _)| i).chain(std::iter::once(pattern.len())) {
        let seg_str = &pattern[rest_start..i];
        rest_start = i + 1;
        if seg_str.contains('*') || seg_str.contains('?') || seg_str.contains('[') {
            break;
        }
        if !seg_str.is_empty() {
            base.push(seg_str);
        }
    }
    let base_dir = if base.as_os_str().is_empty() { std::path::PathBuf::from(".") } else { base.clone() };
    let re = glob_to_regex(pattern);
    let mut out = Vec::new();
    collect_glob_matches(&base_dir, &re, &mut out);
    out.sort();
    out
}

fn glob_to_regex(pattern: &str) -> regex::Regex {
    let mut re = String::from("^");
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' => {
                if chars.peek() == Some(&'*') {
                    chars.next();
                    // `**/` also matches zero directories
                    if chars.peek() == Some(&'/') {
                        chars.next();
                        re.push_str("(?:.*/)?");
                    } else {
                        re.push_str(".*");
                    }
                } else {
                    re.push_str("[^/]*");
                }
            }
            '?' => re.push_str("[^/]"),
            c => re.push_str(&regex::escape(&c.to_string())),
        }
    }
    re.push('$');
    regex::Regex::new(&re).unwrap_or_else(|_| regex::Regex::new("a^").unwrap())
}

fn collect_glob_matches(dir: &std::path::Path, re: &regex::Regex, out: &mut Vec<std::path::PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(rd) => rd,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let s = path.to_string_lossy().replace('\\', "/");
        // Normalize "./" prefix produced when base dir is "."
        let s = s.strip_prefix("./").unwrap_or(&s).to_string();
        if re.is_match(&s) && path.is_file() {
            out.push(std::path::PathBuf::from(&s));
        }
        if path.is_dir() {
            collect_glob_matches(&path, re, out);
        }
    }
}

/// `dependency_graph` config: resolve routine source files via an embedded
/// codeweb call graph before parsing (#120).
#[derive(Debug, Default, Deserialize)]
pub struct DependencyGraphConfig {
    /// Master switch; the section is only active when `enabled: true`.
    #[serde(default)]
    pub enabled: Option<bool>,
    /// codeweb project root containing `codeweb.toml`. When the file is
    /// absent fluxgauss auto-initializes the project in place.
    pub project: Option<String>,
    /// Source dirs for auto-init (project-root-relative or absolute).
    /// Defaults to the whole project root.
    #[serde(default)]
    pub dirs: Option<Vec<String>>,
    /// Project name for auto-init; defaults to the directory name.
    #[serde(default)]
    pub name: Option<String>,
    /// `auto` (default): run incremental analyze when the store is stale;
    /// `never`: reuse the existing store as-is.
    #[serde(default)]
    pub refresh: Option<String>,
    /// Closure seeds. Defaults to `procedure_filter` entries when omitted.
    #[serde(default)]
    pub seeds: Option<Vec<String>>,
    /// Extra source files/dirs/globs always included alongside closure files.
    #[serde(default)]
    pub extra_sources: Option<Vec<String>>,
    /// `warn` (default) or `fail` on unresolved call nodes inside the closure.
    #[serde(default)]
    pub on_unresolved: Option<String>,
}

impl DependencyGraphConfig {
    pub fn is_enabled(&self) -> bool {
        self.enabled.unwrap_or(false)
    }

    pub fn refresh_never(&self) -> bool {
        self.refresh.as_deref().map(|r| r.eq_ignore_ascii_case("never")).unwrap_or(false)
    }

    pub fn fail_on_unresolved(&self) -> bool {
        self.on_unresolved.as_deref().map(|u| u.eq_ignore_ascii_case("fail")).unwrap_or(false)
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
    fn test_expand_source_patterns_dir_and_glob() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path();
        std::fs::create_dir_all(root.join("sub")).unwrap();
        std::fs::write(root.join("a.sql"), "x").unwrap();
        std::fs::write(root.join("sub").join("b.sql"), "x").unwrap();
        std::fs::write(root.join("c.txt"), "x").unwrap();

        // directory → recursive *.sql
        let files = expand_source_patterns(&[root.to_string_lossy().into_owned()]);
        assert_eq!(files.len(), 2);
        assert!(files.iter().any(|f| f.ends_with("a.sql")));
        assert!(files.iter().any(|f| f.ends_with("b.sql")));

        // glob pattern
        let pat = format!("{}/*.sql", root.display());
        let files = expand_source_patterns(&[pat]);
        assert_eq!(files.len(), 1);
        assert!(files[0].ends_with("a.sql"));

        // plain file passes through even if missing
        let missing = root.join("nope.sql");
        let files = expand_source_patterns(&[missing.to_string_lossy().into_owned()]);
        assert_eq!(files, vec![missing]);
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

use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Read;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::types::{PackageInfo, ServiceCall};

#[derive(Debug, Serialize, Deserialize, Default)]
struct Manifest {
    files: HashMap<String, FileEntry>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct FileEntry {
    hash: String,
    package: String,
    java_package: String,
}

#[derive(Debug, Serialize, Deserialize, Default)]
struct GenerationCheckpoint {
    completed: HashSet<String>,
    updated_at: String,
}

pub struct IncrementalState {
    output_dir: PathBuf,
    cache_dir: PathBuf,
    manifest: Option<Manifest>,
    checkpoint: Option<GenerationCheckpoint>,
    force_full: bool,
}

impl IncrementalState {
    pub fn new(output_dir: impl Into<PathBuf>, force_full: bool) -> Self {
        let output_dir = output_dir.into();
        let cache_dir = output_dir.join(".fluxgauss");
        Self {
            output_dir,
            cache_dir,
            manifest: None,
            checkpoint: None,
            force_full,
        }
    }

    pub fn initialize(&mut self) -> std::io::Result<()> {
        std::fs::create_dir_all(self.cache_dir.join("ast"))?;
        self.manifest = self.load_manifest_inner().ok();
        self.checkpoint = self.load_checkpoint_inner().ok();
        Ok(())
    }

    pub fn compute_hash(path: &Path) -> std::io::Result<String> {
        let mut file = std::fs::File::open(path)?;
        let mut hasher = Sha256::new();
        let mut buf = [0u8; 8192];
        loop {
            let n = file.read(&mut buf)?;
            if n == 0 {
                break;
            }
            hasher.update(&buf[..n]);
        }
        Ok(format!("{:x}", hasher.finalize()))
    }

    pub fn is_cached(&self, sql_file: &Path) -> bool {
        if self.force_full {
            return false;
        }
        let key = sql_file.to_string_lossy();
        let Some(manifest) = &self.manifest else {
            return false;
        };
        let Some(entry) = manifest.files.get(key.as_ref()) else {
            return false;
        };
        match Self::compute_hash(sql_file) {
            Ok(hash) => hash == entry.hash && self.cached_ast_path(sql_file).exists(),
            Err(_) => false,
        }
    }

    fn cached_ast_path(&self, sql_file: &Path) -> PathBuf {
        let safe: String = sql_file
            .to_string_lossy()
            .chars()
            .map(|c| if c.is_alphanumeric() || c == '.' { c } else { '_' })
            .collect();
        self.cache_dir.join("ast").join(format!("{}.json", safe))
    }

    pub fn save_cached_ast(&self, sql_file: &Path, json: &str) -> std::io::Result<()> {
        let path = self.cached_ast_path(sql_file);
        let temp = path.with_extension("json.tmp");
        std::fs::write(&temp, json)?;
        std::fs::rename(&temp, &path)
    }

    pub fn load_cached_ast(&self, sql_file: &Path) -> Option<String> {
        let path = self.cached_ast_path(sql_file);
        std::fs::read_to_string(path).ok()
    }

    pub fn load_manifest_inner(&self) -> std::io::Result<Manifest> {
        let path = self.cache_dir.join("manifest.json");
        let content = std::fs::read_to_string(path)?;
        Ok(serde_json::from_str(&content).unwrap_or_default())
    }

    pub fn save_manifest(&self) -> std::io::Result<()> {
        let path = self.cache_dir.join("manifest.json");
        if let Some(manifest) = &self.manifest {
            let json = serde_json::to_string_pretty(manifest)?;
            std::fs::write(path, json)?;
        }
        Ok(())
    }

    pub fn update_file_entry(
        &mut self,
        sql_file: &Path,
        package: &str,
        java_package: &str,
    ) -> std::io::Result<()> {
        let hash = Self::compute_hash(sql_file)?;
        let manifest = self.manifest.get_or_insert_with(Manifest::default);
        manifest.files.insert(
            sql_file.to_string_lossy().into_owned(),
            FileEntry {
                hash,
                package: package.to_string(),
                java_package: java_package.to_string(),
            },
        );
        Ok(())
    }

    pub fn save_checkpoint(&mut self, completed: &HashSet<String>) -> std::io::Result<()> {
        let checkpoint = GenerationCheckpoint {
            completed: completed.clone(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };
        let path = self.cache_dir.join("gen-checkpoint.json");
        let json = serde_json::to_string_pretty(&checkpoint)?;
        std::fs::write(path, json)?;
        self.checkpoint = Some(checkpoint);
        Ok(())
    }

    fn load_checkpoint_inner(&self) -> std::io::Result<GenerationCheckpoint> {
        let path = self.cache_dir.join("gen-checkpoint.json");
        let content = std::fs::read_to_string(path)?;
        Ok(serde_json::from_str(&content).unwrap_or_default())
    }

    pub fn load_checkpoint(&mut self) {
        self.checkpoint = self.load_checkpoint_inner().ok();
    }

    pub fn is_checkpoint_complete(&self, package_name: &str) -> bool {
        self.checkpoint
            .as_ref()
            .map(|cp| cp.completed.contains(package_name))
            .unwrap_or(false)
    }

    pub fn clear_checkpoint(&mut self) -> std::io::Result<()> {
        let path = self.cache_dir.join("gen-checkpoint.json");
        if path.exists() {
            std::fs::remove_file(path)?;
        }
        self.checkpoint = None;
        Ok(())
    }

    pub fn build_dependency_graph(
        packages: &[PackageInfo],
    ) -> HashMap<String, HashSet<String>> {
        let mut reverse_deps: HashMap<String, HashSet<String>> = HashMap::new();
        for pkg in packages {
            for proc in &pkg.procedures {
                for call in &proc.service_calls {
                    if !call.package_name.is_empty()
                        && call.package_name != pkg.package_name
                    {
                        reverse_deps
                            .entry(call.package_name.clone())
                            .or_default()
                            .insert(pkg.package_name.clone());
                    }
                }
            }
        }
        reverse_deps
    }

    pub fn find_dependent_packages(
        packages: &[PackageInfo],
        changed: &HashSet<String>,
    ) -> HashSet<String> {
        let reverse_deps = Self::build_dependency_graph(packages);
        let mut affected = changed.clone();
        let mut queue: VecDeque<String> = changed.iter().cloned().collect();

        while let Some(current) = queue.pop_front() {
            if let Some(deps) = reverse_deps.get(&current) {
                for dep in deps {
                    if !affected.contains(dep) {
                        affected.insert(dep.clone());
                        queue.push_back(dep.clone());
                    }
                }
            }
        }
        affected
    }

    pub fn cleanup_stale(&mut self, current_sources: &[PathBuf]) -> std::io::Result<()> {
        let current_keys: HashSet<String> = current_sources
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect();

        let stale_entries: Vec<(String, FileEntry)> = match &self.manifest {
            Some(manifest) => manifest
                .files
                .iter()
                .filter(|(k, _)| !current_keys.contains(*k))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            None => return Ok(()),
        };

        for (key, entry) in &stale_entries {
            let stale_path = PathBuf::from(key);
            let ast_path = self.cached_ast_path(&stale_path);
            if ast_path.exists() {
                let _ = std::fs::remove_file(ast_path);
            }
            let _ = std::fs::remove_dir_all(
                self.output_dir
                    .join("src")
                    .join(entry.java_package.replace('.', "/")),
            );
        }

        if let Some(manifest) = &mut self.manifest {
            for (key, _) in stale_entries {
                manifest.files.remove(&key);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::TempDir;

    fn setup_state(dir: &TempDir) -> IncrementalState {
        let mut state = IncrementalState::new(dir.path(), false);
        state.initialize().unwrap();
        state
    }

    #[test]
    fn test_hash_deterministic() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test.sql");
        std::fs::write(&file_path, "SELECT 1").unwrap();

        let h1 = IncrementalState::compute_hash(&file_path).unwrap();
        let h2 = IncrementalState::compute_hash(&file_path).unwrap();
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64);
    }

    #[test]
    fn test_hash_changes_on_edit() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test.sql");
        std::fs::write(&file_path, "SELECT 1").unwrap();
        let h1 = IncrementalState::compute_hash(&file_path).unwrap();

        std::fs::write(&file_path, "SELECT 2").unwrap();
        let h2 = IncrementalState::compute_hash(&file_path).unwrap();
        assert_ne!(h1, h2);
    }

    #[test]
    fn test_cache_hit() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = setup_state(&dir);

        let sql_path = dir.path().join("test.sql");
        std::fs::write(&sql_path, "SELECT 1").unwrap();
        state
            .update_file_entry(&sql_path, "pkg_test", "com.example")
            .unwrap();
        state.save_cached_ast(&sql_path, r#"{"statements":[]}"#).unwrap();

        assert!(state.is_cached(&sql_path));
        let ast = state.load_cached_ast(&sql_path);
        assert_eq!(ast.as_deref(), Some(r#"{"statements":[]}"#));
    }

    #[test]
    fn test_cache_miss() {
        let dir = tempfile::tempdir().unwrap();
        let state = setup_state(&dir);

        let sql_path = dir.path().join("nonexistent.sql");
        assert!(!state.is_cached(&sql_path));
        assert!(state.load_cached_ast(&sql_path).is_none());
    }

    #[test]
    fn test_manifest_save_load() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = setup_state(&dir);

        let sql_path = dir.path().join("test.sql");
        std::fs::write(&sql_path, "SELECT 1").unwrap();
        state
            .update_file_entry(&sql_path, "pkg_test", "com.example")
            .unwrap();
        state.save_manifest().unwrap();

        let mut state2 = IncrementalState::new(dir.path(), false);
        state2.initialize().unwrap();
        assert!(state2.manifest.is_some());
        let m = state2.manifest.unwrap();
        assert!(m.files.contains_key(&sql_path.to_string_lossy().into_owned()));
    }

    #[test]
    fn test_manifest_update_entry() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = setup_state(&dir);

        let sql_path = dir.path().join("test.sql");
        std::fs::write(&sql_path, "v1").unwrap();
        state
            .update_file_entry(&sql_path, "pkg_a", "com.a")
            .unwrap();

        let entry = state
            .manifest
            .as_ref()
            .unwrap()
            .files
            .get(&sql_path.to_string_lossy().into_owned())
            .unwrap();
        assert_eq!(entry.package, "pkg_a");

        std::fs::write(&sql_path, "v2").unwrap();
        state
            .update_file_entry(&sql_path, "pkg_b", "com.b")
            .unwrap();
        let entry = state
            .manifest
            .as_ref()
            .unwrap()
            .files
            .get(&sql_path.to_string_lossy().into_owned())
            .unwrap();
        assert_eq!(entry.package, "pkg_b");
    }

    #[test]
    fn test_checkpoint_save_load() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = setup_state(&dir);

        let completed: HashSet<String> =
            ["pkg_order".into(), "pkg_product".into()].into_iter().collect();
        state.save_checkpoint(&completed).unwrap();
        assert!(state.is_checkpoint_complete("pkg_order"));
        assert!(state.is_checkpoint_complete("pkg_product"));
        assert!(!state.is_checkpoint_complete("pkg_other"));

        let mut state2 = IncrementalState::new(dir.path(), false);
        state2.load_checkpoint();
        assert!(state2.is_checkpoint_complete("pkg_order"));
    }

    #[test]
    fn test_checkpoint_clear() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = setup_state(&dir);

        let completed: HashSet<String> = ["pkg_a".into()].into_iter().collect();
        state.save_checkpoint(&completed).unwrap();
        assert!(state.is_checkpoint_complete("pkg_a"));

        state.clear_checkpoint().unwrap();
        assert!(!state.is_checkpoint_complete("pkg_a"));

        assert!(!dir.path().join(".fluxgauss/gen-checkpoint.json").exists());
    }

    #[test]
    fn test_checkpoint_is_complete_false_initially() {
        let dir = tempfile::tempdir().unwrap();
        let state = setup_state(&dir);
        assert!(!state.is_checkpoint_complete("anything"));
    }

    #[test]
    fn test_dependency_graph_simple() {
        use crate::types::ProcedureInfo;
        let packages = vec![
            PackageInfo {
                package_name: "pkg_a".into(),
                procedures: vec![{
                    let mut p =
                        ProcedureInfo::new("pkg_a.do_a".into(), "pkg_a".into(), "do_a".into());
                    p.service_calls.push(ServiceCall {
                        service_name: "BService".into(),
                        method_name: "do_b".into(),
                        args: vec![],
                        package_name: "pkg_b".into(),
                    });
                    p
                }],
                table_refs: HashSet::new(),
                package_vars: HashMap::new(),
                source_file: "a.sql".into(),
                comments: vec![],
                java_package: "com.example".into(),
                custom_types: HashMap::new(),
            },
            PackageInfo {
                package_name: "pkg_b".into(),
                procedures: vec![{
                    let mut p =
                        ProcedureInfo::new("pkg_b.do_b".into(), "pkg_b".into(), "do_b".into());
                    p.service_calls.push(ServiceCall {
                        service_name: "CService".into(),
                        method_name: "do_c".into(),
                        args: vec![],
                        package_name: "pkg_c".into(),
                    });
                    p
                }],
                table_refs: HashSet::new(),
                package_vars: HashMap::new(),
                source_file: "b.sql".into(),
                comments: vec![],
                java_package: "com.example".into(),
                custom_types: HashMap::new(),
            },
            PackageInfo {
                package_name: "pkg_c".into(),
                procedures: vec![ProcedureInfo::new(
                    "pkg_c.do_c".into(),
                    "pkg_c".into(),
                    "do_c".into(),
                )],
                table_refs: HashSet::new(),
                package_vars: HashMap::new(),
                source_file: "c.sql".into(),
                comments: vec![],
                java_package: "com.example".into(),
                custom_types: HashMap::new(),
            },
        ];

        let graph = IncrementalState::build_dependency_graph(&packages);
        assert!(graph.contains_key("pkg_b"));
        assert!(graph.get("pkg_b").unwrap().contains("pkg_a"));
        assert!(graph.contains_key("pkg_c"));
        assert!(graph.get("pkg_c").unwrap().contains("pkg_b"));
        assert!(!graph.contains_key("pkg_a"));
    }

    #[test]
    fn test_dependency_graph_no_deps() {
        use crate::types::ProcedureInfo;
        let packages = vec![PackageInfo {
            package_name: "isolated".into(),
            procedures: vec![ProcedureInfo::new(
                "isolated.run".into(),
                "isolated".into(),
                "run".into(),
            )],
            table_refs: HashSet::new(),
            package_vars: HashMap::new(),
            source_file: "iso.sql".into(),
            comments: vec![],
            java_package: "com.example".into(),
            custom_types: HashMap::new(),
        }];

        let graph = IncrementalState::build_dependency_graph(&packages);
        assert!(graph.is_empty());
    }

    #[test]
    fn test_find_dependent_packages_transitive() {
        use crate::types::{ProcedureInfo, ServiceCall};
        let packages = vec![
            PackageInfo {
                package_name: "pkg_a".into(),
                procedures: vec![{
                    let mut p =
                        ProcedureInfo::new("pkg_a.run".into(), "pkg_a".into(), "run".into());
                    p.service_calls.push(ServiceCall {
                        service_name: "BService".into(),
                        method_name: "do".into(),
                        args: vec![],
                        package_name: "pkg_b".into(),
                    });
                    p
                }],
                table_refs: HashSet::new(),
                package_vars: HashMap::new(),
                source_file: "a.sql".into(),
                comments: vec![],
                java_package: "com.example".into(),
                custom_types: HashMap::new(),
            },
            PackageInfo {
                package_name: "pkg_b".into(),
                procedures: vec![{
                    let mut p =
                        ProcedureInfo::new("pkg_b.do".into(), "pkg_b".into(), "do".into());
                    p.service_calls.push(ServiceCall {
                        service_name: "CService".into(),
                        method_name: "fin".into(),
                        args: vec![],
                        package_name: "pkg_c".into(),
                    });
                    p
                }],
                table_refs: HashSet::new(),
                package_vars: HashMap::new(),
                source_file: "b.sql".into(),
                comments: vec![],
                java_package: "com.example".into(),
                custom_types: HashMap::new(),
            },
            PackageInfo {
                package_name: "pkg_c".into(),
                procedures: vec![ProcedureInfo::new(
                    "pkg_c.fin".into(),
                    "pkg_c".into(),
                    "fin".into(),
                )],
                table_refs: HashSet::new(),
                package_vars: HashMap::new(),
                source_file: "c.sql".into(),
                comments: vec![],
                java_package: "com.example".into(),
                custom_types: HashMap::new(),
            },
        ];

        let changed: HashSet<String> = ["pkg_c".into()].into_iter().collect();
        let affected = IncrementalState::find_dependent_packages(&packages, &changed);
        assert!(affected.contains("pkg_c"));
        assert!(affected.contains("pkg_b"));
        assert!(affected.contains("pkg_a"));
    }

    #[test]
    fn test_find_dependent_packages_empty() {
        let affected = IncrementalState::find_dependent_packages(&[], &HashSet::new());
        assert!(affected.is_empty());
    }

    #[test]
    fn test_cleanup_stale_removes_old() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = setup_state(&dir);

        let old_sql = dir.path().join("old.sql");
        std::fs::write(&old_sql, "SELECT 1").unwrap();
        state
            .update_file_entry(&old_sql, "pkg_old", "com.old")
            .unwrap();
        state
            .save_cached_ast(&old_sql, "{}")
            .unwrap();

        let new_sql = dir.path().join("new.sql");
        std::fs::write(&new_sql, "SELECT 2").unwrap();
        state
            .update_file_entry(&new_sql, "pkg_new", "com.new")
            .unwrap();

        state.cleanup_stale(&[new_sql.clone()]).unwrap();

        let manifest = state.manifest.as_ref().unwrap();
        assert!(!manifest.files.contains_key(&old_sql.to_string_lossy().into_owned()));
        assert!(manifest.files.contains_key(&new_sql.to_string_lossy().into_owned()));
        assert!(state.load_cached_ast(&old_sql).is_none());
    }

    #[test]
    fn test_force_full_skips_cache() {
        let dir = tempfile::tempdir().unwrap();
        let mut state = IncrementalState::new(dir.path(), true);
        state.initialize().unwrap();

        let sql_path = dir.path().join("test.sql");
        std::fs::write(&sql_path, "SELECT 1").unwrap();
        state
            .update_file_entry(&sql_path, "pkg_test", "com.example")
            .unwrap();
        state.save_cached_ast(&sql_path, "{}").unwrap();

        assert!(!state.is_cached(&sql_path));
    }
}

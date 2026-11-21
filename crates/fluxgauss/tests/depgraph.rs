//! End-to-end smoke test for `dependency_graph` (#120, PR #123 review):
//! auto-init codeweb project → analyze → thin-graph closure → file list.

use std::collections::HashSet;
use std::fs;

use fluxgauss::config::AppConfig;
use fluxgauss::depgraph::resolve_dependency_files;

fn proc_sql(name: &str, calls: &str) -> String {
    format!("CREATE OR REPLACE PROCEDURE {name}()\nAS $$\nBEGIN\n{calls}\nEND;\n$$ LANGUAGE plpgsql;\n")
}

fn basenames(files: &[std::path::PathBuf]) -> HashSet<String> {
    files.iter().map(|f| f.file_name().unwrap().to_string_lossy().into_owned()).collect()
}

#[test]
fn depgraph_auto_init_analyze_and_transitive_closure() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path();

    fs::write(root.join("a.sql"), proc_sql("pkg_a.top", "    CALL pkg_b.mid();")).unwrap();
    fs::write(root.join("b.sql"), proc_sql("pkg_b.mid", "    CALL pkg_c.leaf();")).unwrap();
    fs::write(root.join("c.sql"), proc_sql("pkg_c.leaf", "")).unwrap();
    fs::write(root.join("d.sql"), proc_sql("pkg_z.other", "")).unwrap();

    let yaml = format!(
        "dependency_graph:\n  enabled: true\n  project: {}\nprocedure_filter:\n  - pkg_a.top\n",
        root.display()
    );
    let config: AppConfig = serde_yaml::from_str(&yaml).unwrap();

    let stats = resolve_dependency_files(&config).expect("first resolve");
    let names = basenames(&stats.files);
    assert_eq!(names, HashSet::from(["a.sql".into(), "b.sql".into(), "c.sql".into()]));
    assert_eq!(stats.closure_routines, 3);
    assert!(stats.matched_seeds.iter().any(|s| s == "pkg_a.top"));
    assert!(stats.unmatched_seeds.is_empty());
    assert!(root.join("codeweb.toml").is_file(), "auto-init must write codeweb.toml");
    assert!(root.join(".codeweb").is_dir(), "auto-init must create .codeweb store dir");

    // Second run reuses the fresh store (incremental path) and must agree.
    let stats2 = resolve_dependency_files(&config).expect("second resolve");
    assert_eq!(basenames(&stats2.files), names);
    assert_eq!(stats2.closure_routines, 3);
}

#[test]
fn depgraph_requires_project() {
    let yaml = "dependency_graph:\n  enabled: true\n";
    let config: AppConfig = serde_yaml::from_str(yaml).unwrap();
    let err = resolve_dependency_files(&config).unwrap_err();
    assert!(err.contains("project"), "unexpected error: {err}");
}

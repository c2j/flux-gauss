//! File-level dependency closure via the embedded codeweb call graph (#120).
//!
//! `dependency_graph` config points at a codeweb project (a directory with
//! `codeweb.toml` and a `.codeweb/` store). Before routine parsing, the
//! `procedure_filter` seeds (or `dependency_graph.seeds`) are looked up in the
//! codeweb graph and a transitive `Direct`/`Dynamic` call-edge closure yields
//! the set of SQL files that must be loaded. Routine-level selection stays
//! with `proc_filter`; this module only answers "which files".
//!
//! The graph traversal itself is a pure function over `ThinGraph` so it can be
//! unit-tested without a real codeweb project; `resolve_dependency_files`
//! wraps project open / freshness check / incremental analyze.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use codeweb::embed::{self, ThinEdgeKind, ThinGraph, ThinNodeKind};

use crate::config::AppConfig;
use crate::proc_filter::parse_filter_entries;

/// Outcome of a dependency-graph closure run.
#[derive(Debug, Default)]
pub struct ClosureStats {
    /// SQL files pulled in by the closure (project-root-relative or absolute).
    pub files: Vec<PathBuf>,
    /// Seed entries that matched at least one routine node.
    pub matched_seeds: Vec<String>,
    /// Seed entries that matched no routine node.
    pub unmatched_seeds: Vec<String>,
    /// Number of routine nodes reached by the closure (incl. seeds).
    pub closure_routines: usize,
    /// `Unresolved` node raw expressions touched by the closure.
    pub unresolved: Vec<String>,
}

/// Whether `dependency_graph` is configured and enabled.
pub fn is_enabled(config: &AppConfig) -> bool {
    config.dependency_graph.as_ref().map(|d| d.is_enabled()).unwrap_or(false)
}

/// codeweb's CLI installs a rayon pool with 4 MiB worker stacks because
/// ogsql-parser's recursive descent overflows the default spawned-thread
/// stack on deeply nested PL/pgSQL. The library path (`analyze()`) uses the
/// rayon **global** pool, so the host must install an equivalent pool before
/// the first analyze — `build_global` is idempotent-guarded by `Once`.
fn ensure_codeweb_pool() {
    use std::sync::Once;
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        if let Err(e) = rayon::ThreadPoolBuilder::new().stack_size(4 * 1024 * 1024).build_global() {
            // Global pool already installed by another crate with default
            // stacks — the deep-recursion overflow fix would not apply.
            // eprintln! not tracing: the CLI binary installs no tracing
            // subscriber, so tracing::warn! would be silently dropped exactly
            // in the CLI-on-huge-corpus scenario this warning exists for.
            eprintln!(
                "warning: rayon global pool already initialized ({}); codeweb analyze may stack-overflow on deeply nested SQL",
                e
            );
        }
    });
}

/// Resolve closure files for the configured project. Seeds default to the
/// `procedure_filter` entries when `dependency_graph.seeds` is absent.
pub fn resolve_dependency_files(config: &AppConfig) -> Result<ClosureStats, String> {
    ensure_codeweb_pool();
    let dg = config.dependency_graph.as_ref().ok_or_else(|| "dependency_graph section missing".to_string())?;
    let project = dg.project.as_deref().ok_or_else(|| "dependency_graph.project is required".to_string())?;
    let root = Path::new(project);

    // Auto-init: no codeweb.toml → create the project in place so users never
    // run `codeweb init` by hand. `dirs` default to the whole corpus root.
    if !root.join("codeweb.toml").is_file() {
        let dirs: Vec<PathBuf> = dg.dirs.clone().unwrap_or_default().iter().map(PathBuf::from).collect();
        let name = dg
            .name
            .clone()
            .or_else(|| root.file_name().map(|n| n.to_string_lossy().into_owned()))
            .unwrap_or_else(|| "fluxgauss".to_string());
        codeweb::project::Project::init_at(root, &dirs, &name)
            .map_err(|e| format!("codeweb auto-init failed for '{}': {}", project, e))?;
        println!("  dependency_graph: initialized codeweb project '{}' at {}", name, project);
    }

    let mut proj = embed::EmbeddedProject::open_exact(root)
        .map_err(|e| format!("cannot open codeweb project '{}': {}", project, e))?;

    if !dg.refresh_never() {
        let status = proj.diff().map_err(|e| format!("codeweb diff failed: {}", e))?;
        if !status.is_up_to_date() {
            let report = proj.analyze().map_err(|e| format!("codeweb analyze failed: {}", e))?;
            println!("  dependency_graph: codeweb analyze: {:?}", report);
        }
    }

    let thin = proj.export_thin_graph().map_err(|e| format!("codeweb export_thin_graph failed: {}", e))?;

    let seed_raw: Vec<String> = match &dg.seeds {
        Some(seeds) => seeds.clone(),
        None => config.procedure_filter.clone().unwrap_or_default(),
    };
    let entries = parse_filter_entries(&seed_raw);
    let seed_names: Vec<String> = entries.iter().map(|e| e.name.to_lowercase()).collect();
    let mut stats = closure_from_thin_graph(&thin, &seed_names, root);

    // Report unmatched seeds using the original spelling.
    stats.unmatched_seeds = seed_names
        .iter()
        .zip(entries.iter())
        .filter(|(lower, _)| !stats.matched_seeds.iter().any(|m| m.eq_ignore_ascii_case(lower)))
        .map(|(_, e)| e.name.clone())
        .collect();

    // `extra_sources` are merged by the caller; keep them out of `files`.
    Ok(stats)
}

/// Spellings a seed entry may use to reference a routine, mirroring
/// `proc_filter::name_candidates`: the full name plus the `pkg.proc` tail of
/// a `schema.pkg.proc` entry.
fn seed_candidates(seed: &str) -> HashSet<String> {
    let mut cands = HashSet::new();
    cands.insert(seed.to_string());
    let parts: Vec<&str> = seed.split('.').collect();
    if parts.len() >= 3 {
        cands.insert(parts[parts.len() - 2..].join("."));
    }
    cands
}

/// All spellings a routine node answers to: bare name, `package.name`,
/// `schema.name` and `schema.package.name` (whichever fields exist).
fn node_spellings(kind: &ThinNodeKind) -> HashSet<String> {
    let (schema, package, name) = match kind {
        ThinNodeKind::Procedure { schema, package, name, .. }
        | ThinNodeKind::Function { schema, package, name, .. } => (schema, package, name),
        _ => return HashSet::new(),
    };
    let mut out = HashSet::new();
    out.insert(name.to_lowercase());
    match (schema, package) {
        (Some(s), Some(p)) => {
            out.insert(format!("{}.{}", p, name).to_lowercase());
            out.insert(format!("{}.{}.{}", s, p, name).to_lowercase());
        }
        (Some(s), None) => {
            out.insert(format!("{}.{}", s, name).to_lowercase());
        }
        (None, Some(p)) => {
            out.insert(format!("{}.{}", p, name).to_lowercase());
        }
        (None, None) => {}
    }
    out
}

fn is_routine(kind: &ThinNodeKind) -> bool {
    matches!(kind, ThinNodeKind::Procedure { .. } | ThinNodeKind::Function { .. })
}

fn is_call_edge(kind: &ThinEdgeKind) -> bool {
    matches!(kind, ThinEdgeKind::Direct { .. } | ThinEdgeKind::Dynamic { .. })
}

fn routine_file(kind: &ThinNodeKind) -> Option<&Path> {
    match kind {
        ThinNodeKind::Procedure { file, .. } | ThinNodeKind::Function { file, .. } => Some(file.as_path()),
        _ => None,
    }
}

/// Resolve a graph-recorded file path against the project root. codeweb stores
/// the path as scanned; it may be relative to the project root or absolute.
fn resolve_file(root: &Path, file: &Path) -> PathBuf {
    if file.is_absolute() {
        return file.to_path_buf();
    }
    let joined = root.join(file);
    if joined.exists() || file.exists() {
        joined
    } else {
        file.to_path_buf()
    }
}

/// BFS over `Direct`/`Dynamic` call edges from seed routine nodes; collect the
/// source files of every reached routine. Pure — no project I/O.
pub fn closure_from_thin_graph(graph: &ThinGraph, seeds: &[String], root: &Path) -> ClosureStats {
    let nodes: HashMap<usize, &ThinNodeKind> = graph.nodes.iter().map(|n| (n.id, &n.kind)).collect();

    // Adjacency restricted to call edges.
    let mut adj: HashMap<usize, Vec<usize>> = HashMap::new();
    for edge in &graph.edges {
        if is_call_edge(&edge.kind) {
            adj.entry(edge.source).or_default().push(edge.target);
        }
    }

    // Seed nodes: routine nodes whose spellings intersect the entry candidates.
    let mut queue: VecDeque<usize> = VecDeque::new();
    let mut reached: HashSet<usize> = HashSet::new();
    let mut matched = HashSet::new();
    for seed in seeds {
        let cands = seed_candidates(seed);
        for entry in &graph.nodes {
            if !is_routine(&entry.kind) {
                continue;
            }
            let spellings = node_spellings(&entry.kind);
            if cands.iter().any(|c| spellings.contains(c)) {
                matched.insert(seed.clone());
                if reached.insert(entry.id) {
                    queue.push_back(entry.id);
                }
            }
        }
    }

    while let Some(id) = queue.pop_front() {
        if let Some(targets) = adj.get(&id) {
            for &t in targets {
                if reached.insert(t) {
                    queue.push_back(t);
                }
            }
        }
    }

    // Collect files + unresolved call noise from reached routine nodes.
    let mut files: Vec<PathBuf> = Vec::new();
    let mut seen_files: HashSet<PathBuf> = HashSet::new();
    let mut unresolved: Vec<String> = Vec::new();
    let mut seen_unresolved: HashSet<String> = HashSet::new();
    let mut routines = 0usize;

    for id in &reached {
        let Some(kind) = nodes.get(id) else { continue };
        if is_routine(kind) {
            routines += 1;
            if let Some(f) = routine_file(kind) {
                let resolved = resolve_file(root, f);
                if seen_files.insert(resolved.clone()) {
                    files.push(resolved);
                }
            }
        }
        if let ThinNodeKind::Unresolved { raw_expr } = kind {
            if seen_unresolved.insert(raw_expr.clone()) {
                unresolved.push(raw_expr.clone());
            }
        }
    }

    files.sort();
    ClosureStats {
        files,
        matched_seeds: matched.into_iter().collect(),
        unmatched_seeds: Vec::new(), // filled by caller
        closure_routines: routines,
        unresolved,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use codeweb::embed::{ThinEdge, ThinNodeEntry};

    fn proc_node(id: usize, schema: Option<&str>, package: Option<&str>, name: &str, file: &str) -> ThinNodeEntry {
        ThinNodeEntry {
            id,
            kind: ThinNodeKind::Procedure {
                schema: schema.map(String::from),
                package: package.map(String::from),
                name: name.to_string(),
                file: PathBuf::from(file),
                line: 1,
                partial: false,
            },
        }
    }

    fn edge(source: usize, target: usize) -> ThinEdge {
        ThinEdge { source, target, kind: ThinEdgeKind::Direct { file: PathBuf::from("x.sql"), line: 1 } }
    }

    #[test]
    fn closure_follows_direct_edges_across_packages() {
        // a.sql: pkg_a.top -> pkg_b.mid -> pkg_c.leaf ; unrelated.d unrelated
        let graph = ThinGraph {
            nodes: vec![
                proc_node(0, None, Some("pkg_a"), "top", "a.sql"),
                proc_node(1, None, Some("pkg_b"), "mid", "b.sql"),
                proc_node(2, None, Some("pkg_c"), "leaf", "c.sql"),
                proc_node(3, None, Some("pkg_z"), "other", "d.sql"),
            ],
            edges: vec![edge(0, 1), edge(1, 2)],
        };
        let stats = closure_from_thin_graph(&graph, &["pkg_a.top".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 3);
        assert_eq!(stats.files.len(), 3);
        assert!(stats.files.iter().any(|f| f.ends_with("a.sql")));
        assert!(stats.files.iter().any(|f| f.ends_with("b.sql")));
        assert!(stats.files.iter().any(|f| f.ends_with("c.sql")));
        assert!(!stats.files.iter().any(|f| f.ends_with("d.sql")));
        assert_eq!(stats.matched_seeds, vec!["pkg_a.top".to_string()]);
    }

    #[test]
    fn schema_qualified_seed_matches_pkg_proc_tail() {
        // Node: schema=Some("sch"), package=Some("pkg"), name="proc"
        // Entry "sch.pkg.proc" must also hit via its "pkg.proc" tail.
        let graph = ThinGraph { nodes: vec![proc_node(0, Some("sch"), Some("pkg"), "proc", "s.sql")], edges: vec![] };
        let stats = closure_from_thin_graph(&graph, &["sch.pkg.proc".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 1);
        assert_eq!(stats.files.len(), 1);
    }

    #[test]
    fn package_member_with_schema_does_not_match_schema_name_seed() {
        // Routine inside sch.pkg answers to pkg.proc / sch.pkg.proc but NOT
        // sch.proc — the package segment must not be dropped (#123 review).
        let graph = ThinGraph {
            nodes: vec![
                ThinNodeEntry { id: 0, kind: ThinNodeKind::Package { schema: Some("sch".into()), name: "pkg".into() } },
                proc_node(1, Some("sch"), Some("pkg"), "proc", "s.sql"),
            ],
            edges: vec![ThinEdge { source: 0, target: 1, kind: ThinEdgeKind::ContainsRoutine }],
        };
        let stats = closure_from_thin_graph(&graph, &["sch.proc".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 0);
        let stats = closure_from_thin_graph(&graph, &["sch.pkg.proc".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 1);
    }

    #[test]
    fn standalone_proc_schema_name_matches() {
        // CREATE PROCEDURE pkg.proc → schema="pkg", package=None
        let graph = ThinGraph { nodes: vec![proc_node(0, Some("pkg"), None, "proc", "p.sql")], edges: vec![] };
        let stats = closure_from_thin_graph(&graph, &["pkg.proc".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 1);
    }

    #[test]
    fn bare_name_seed_overapproximates() {
        let graph = ThinGraph {
            nodes: vec![
                proc_node(0, None, Some("pkg_a"), "dup", "a.sql"),
                proc_node(1, None, Some("pkg_b"), "dup", "b.sql"),
                proc_node(2, None, Some("pkg_c"), "uniq", "c.sql"),
            ],
            edges: vec![],
        };
        let stats = closure_from_thin_graph(&graph, &["dup".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 2);
        assert_eq!(stats.files.len(), 2);
    }

    #[test]
    fn unmatched_seed_yields_empty_closure() {
        let graph = ThinGraph { nodes: vec![proc_node(0, None, Some("pkg_a"), "proc", "a.sql")], edges: vec![] };
        let stats = closure_from_thin_graph(&graph, &["pkg_a.nope".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 0);
        assert!(stats.files.is_empty());
        assert!(stats.matched_seeds.is_empty());
    }

    #[test]
    fn dynamic_edges_are_followed() {
        let graph = ThinGraph {
            nodes: vec![
                proc_node(0, None, Some("pkg_a"), "top", "a.sql"),
                proc_node(1, None, Some("pkg_b"), "dyn", "b.sql"),
            ],
            edges: vec![ThinEdge {
                source: 0,
                target: 1,
                kind: ThinEdgeKind::Dynamic { raw_expr: "pkg_b.dyn()".into(), file: PathBuf::from("a.sql"), line: 5 },
            }],
        };
        let stats = closure_from_thin_graph(&graph, &["pkg_a.top".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 2);
        assert_eq!(stats.files.len(), 2);
    }

    #[test]
    fn contains_routine_edges_do_not_pull_whole_package() {
        // Package containment must NOT act as a call edge.
        let graph = ThinGraph {
            nodes: vec![
                ThinNodeEntry { id: 0, kind: ThinNodeKind::Package { schema: None, name: "pkg_a".into() } },
                proc_node(1, None, Some("pkg_a"), "sibling", "a.sql"),
                proc_node(2, None, Some("pkg_a"), "target", "a.sql"),
            ],
            edges: vec![
                ThinEdge { source: 0, target: 1, kind: ThinEdgeKind::ContainsRoutine },
                ThinEdge { source: 0, target: 2, kind: ThinEdgeKind::ContainsRoutine },
            ],
        };
        let stats = closure_from_thin_graph(&graph, &["pkg_a.target".to_string()], Path::new("/proj"));
        assert_eq!(stats.closure_routines, 1);
    }

    #[test]
    fn relative_files_resolve_against_project_root() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::write(tmp.path().join("a.sql"), "x").unwrap();
        let graph = ThinGraph { nodes: vec![proc_node(0, None, Some("pkg_a"), "top", "a.sql")], edges: vec![] };
        let stats = closure_from_thin_graph(&graph, &["pkg_a.top".to_string()], tmp.path());
        assert_eq!(stats.files, vec![tmp.path().join("a.sql")]);
    }

    #[test]
    fn config_parse_dependency_graph() {
        let yaml = r#"
dependency_graph:
  enabled: true
  project: /data/sql
  refresh: never
  seeds:
    - pkg_a.proc_x
  extra_sources:
    - extra/one.sql
  on_unresolved: fail
"#;
        let config: AppConfig = serde_yaml::from_str(yaml).unwrap();
        let dg = config.dependency_graph.unwrap();
        assert!(dg.is_enabled());
        assert!(dg.refresh_never());
        assert!(dg.fail_on_unresolved());
        assert_eq!(dg.project.as_deref(), Some("/data/sql"));
        assert_eq!(dg.seeds.as_ref().map(|s| s.len()), Some(1));
        assert_eq!(dg.extra_sources.as_ref().map(|s| s.len()), Some(1));
    }
}

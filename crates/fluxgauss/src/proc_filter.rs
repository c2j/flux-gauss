//! Procedure-level filter + dependency closure (issue #120).
//!
//! `procedure_filter` selects a whitelist of `pkg.proc[:N]` routines. A fixpoint
//! over a lightweight AST call-edge scan then pulls every directly/indirectly
//! called routine into the conversion set. Routines outside the closure get NO
//! Java artifacts; package-level vars/types/cursors live on `PackageInfo` and
//! are unaffected.
//!
//! Mirrors `converter/flux_gauss.py::_apply_procedure_filter`.

use std::collections::{HashMap, HashSet, VecDeque};

use ogsql_parser::ast::Expr;
use ogsql_parser::{walk_pl_block, Visitor, VisitorResult};

use crate::types::{PackageInfo, ParsedPackages, ProcedureInfo};

/// A parsed filter entry: match name + optional parameter-count disambiguator.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FilterEntry {
    pub name: String,
    pub argc: Option<usize>,
}

/// Parse `procedure_filter` config entries. Forms: `pkg.proc`,
/// `schema.pkg.proc`, bare `proc`, each optionally suffixed `:N`.
pub fn parse_filter_entries(entries: &[String]) -> Vec<FilterEntry> {
    entries
        .iter()
        .filter_map(|raw| {
            let e = raw.trim();
            if e.is_empty() {
                return None;
            }
            let (name, argc) = match e.rsplit_once(':') {
                Some((n, a)) if !a.is_empty() && a.chars().all(|c| c.is_ascii_digit()) => {
                    (n.trim().to_string(), a.parse::<usize>().ok())
                }
                _ => (e.to_string(), None),
            };
            if name.is_empty() {
                None
            } else {
                Some(FilterEntry { name, argc })
            }
        })
        .collect()
}

/// Uniquely identifies a routine inside a package set.
type ProcKey = (String, String, usize); // (package_name, proc_name_lower, param_count)

fn proc_key(pkg: &PackageInfo, proc: &ProcedureInfo) -> ProcKey {
    (pkg.package_name.clone(), proc.proc_name.to_lowercase(), proc.parameters.len())
}

/// Spellings equivalent to a filter entry. `schema.pkg.proc` entries must also
/// match `pkg.proc` because the parser strips the schema from package names (#121).
fn name_candidates(name: &str) -> HashSet<String> {
    let mut cands = HashSet::new();
    cands.insert(name.to_string());
    let parts: Vec<&str> = name.split('.').collect();
    if parts.len() >= 3 {
        cands.insert(parts[parts.len() - 2..].join("."));
    }
    cands
}

/// All spellings a filter entry may use to reference this routine.
fn filter_names(pkg: &PackageInfo, proc: &ProcedureInfo) -> HashSet<String> {
    let mut names = HashSet::new();
    names.insert(proc.name.to_lowercase());
    if !proc.package.is_empty() {
        names.insert(format!("{}.{}", proc.package, proc.proc_name).to_lowercase());
    }
    names.insert(format!("{}.{}", pkg.package_name, proc.proc_name).to_lowercase());
    names.insert(proc.proc_name.to_lowercase());
    // schema-qualified proc.name (e.g. bigfund.pkg_a.proc_x) also offers its
    // pkg.proc tail so both spellings select the same routine (#121)
    let tails: Vec<String> = names
        .iter()
        .filter(|n| n.split('.').count() >= 3)
        .map(|n| {
            let parts: Vec<&str> = n.split('.').collect();
            parts[parts.len() - 2..].join(".")
        })
        .collect();
    names.extend(tails);
    names
}

/// Collect (name_parts, arg_count) for every routine call inside a PL/pgSQL
/// body: `PlStatement::ProcedureCall` plus `Expr::FunctionCall` anywhere in
/// expressions (the walker recurses into nested args/subqueries itself).
struct CallEdgeVisitor {
    calls: Vec<(Vec<String>, usize)>,
}

impl Visitor for CallEdgeVisitor {
    fn visit_procedure_call(&mut self, call: &ogsql_parser::ast::plpgsql::PlProcedureCall) -> VisitorResult {
        let name: Vec<String> = call.name.iter().map(|i| i.as_str().to_string()).collect();
        self.calls.push((name, call.arguments.len()));
        VisitorResult::Continue
    }

    fn visit_expr(&mut self, expr: &Expr) -> VisitorResult {
        if let Expr::FunctionCall { name, args, .. } = expr {
            let name: Vec<String> = name.iter().map(|i| i.as_str().to_string()).collect();
            self.calls.push((name, args.len()));
        }
        VisitorResult::Continue
    }
}

fn extract_call_edges(proc: &ProcedureInfo) -> Vec<(Vec<String>, usize)> {
    let mut visitor = CallEdgeVisitor { calls: Vec::new() };
    if let Some(block) = &proc.body {
        let _ = walk_pl_block(&mut visitor, block);
    }
    visitor.calls
}

pub struct FilterStats {
    pub targets: usize,
    pub closure: usize,
    pub excluded: usize,
    pub packages_dropped: usize,
    pub unmatched: Vec<String>,
}

struct Indexes {
    /// key set for membership tests
    keys: HashSet<ProcKey>,
    /// key -> (pkg_idx, proc_idx)
    loc: HashMap<ProcKey, (usize, usize)>,
    /// (pkg_name_lower, func_lower) -> keys
    pkg_proc: HashMap<(String, String), Vec<ProcKey>>,
    /// (source_file, func_lower) -> keys
    file_func: HashMap<(String, String), Vec<ProcKey>>,
    /// func_lower -> keys
    name: HashMap<String, Vec<ProcKey>>,
    /// pkg_name_lower -> registered package_name
    registered: HashMap<String, String>,
}

fn build_indexes(packages: &[PackageInfo]) -> Indexes {
    let mut idx = Indexes {
        keys: HashSet::new(),
        loc: HashMap::new(),
        pkg_proc: HashMap::new(),
        file_func: HashMap::new(),
        name: HashMap::new(),
        registered: HashMap::new(),
    };
    for (pi, pkg) in packages.iter().enumerate() {
        idx.registered.insert(pkg.package_name.to_lowercase(), pkg.package_name.clone());
        for (ri, proc) in pkg.procedures.iter().enumerate() {
            let key = proc_key(pkg, proc);
            let func = proc.proc_name.to_lowercase();
            idx.keys.insert(key.clone());
            idx.loc.insert(key.clone(), (pi, ri));
            idx.pkg_proc.entry((pkg.package_name.to_lowercase(), func.clone())).or_default().push(key.clone());
            if !proc.source_file.is_empty() {
                idx.file_func.entry((proc.source_file.clone(), func.clone())).or_default().push(key.clone());
            }
            idx.name.entry(func).or_default().push(key);
        }
    }
    idx
}

/// Resolve one procedure's call sites to candidate target keys
/// (over-approximate: every plausible target is included).
fn resolve_edges(
    caller_pkg_name: &str,
    caller_proc: &ProcedureInfo,
    idx: &Indexes,
    packages: &[PackageInfo],
) -> HashSet<ProcKey> {
    let mut out = HashSet::new();
    for (name_parts, argc) in extract_call_edges(caller_proc) {
        let parts: Vec<String> = name_parts.iter().filter(|s| !s.is_empty()).cloned().collect();
        if parts.is_empty() {
            continue;
        }
        let func = parts.last().unwrap().to_lowercase();
        // Within one resolution layer keep only overloads whose parameter
        // count matches the call site; when none match, keep the whole layer.
        // Never unions across layers (#121).
        let pick = |keys: &[ProcKey]| -> Vec<ProcKey> {
            let exact: Vec<ProcKey> = keys
                .iter()
                .filter(|k| {
                    idx.loc
                        .get(*k)
                        .map(|&(pi, ri)| packages[pi].procedures[ri].parameters.len() == argc)
                        .unwrap_or(false)
                })
                .cloned()
                .collect();
            if exact.is_empty() {
                keys.to_vec()
            } else {
                exact
            }
        };
        if parts.len() >= 2 {
            let pkg_hint = parts[parts.len() - 2].to_lowercase();
            let Some(matched) = idx.registered.get(&pkg_hint) else {
                continue; // codegen emits an unresolved call; nothing to include
            };
            if let Some(keys) = idx.pkg_proc.get(&(matched.to_lowercase(), func.clone())) {
                out.extend(pick(keys));
            }
            continue;
        }
        // Unqualified: same package → same source file → global name index.
        // The first layer with any candidate wins; no cross-layer union.
        let layer: Vec<ProcKey> = if let Some(keys) = idx.pkg_proc.get(&(caller_pkg_name.to_lowercase(), func.clone()))
        {
            keys.clone()
        } else if !caller_proc.source_file.is_empty() {
            idx.file_func.get(&(caller_proc.source_file.clone(), func.clone())).cloned().unwrap_or_default()
        } else {
            Vec::new()
        };
        let layer = if layer.is_empty() { idx.name.get(&func).cloned().unwrap_or_default() } else { layer };
        out.extend(pick(&layer));
    }
    out
}

/// Apply `procedure_filter` + transitive closure to parsed packages.
///
/// Mutates `parsed.packages` (procedures filtered, excluded moved to
/// `excluded_procedures`, empty packages dropped) and rebuilds
/// `parsed.summaries` so downstream analysis only sees selected routines.
pub fn apply_procedure_filter(parsed: &mut ParsedPackages, entries: &[FilterEntry]) -> FilterStats {
    let idx = build_indexes(&parsed.packages);

    // ── Seeds ──
    let mut selected: HashMap<ProcKey, &'static str> = HashMap::new();
    let mut unmatched = Vec::new();
    for entry in entries {
        let name_l = entry.name.to_lowercase();
        let mut hits = Vec::new();
        for pkg in &parsed.packages {
            for proc in &pkg.procedures {
                if let Some(argc) = entry.argc {
                    if proc.parameters.len() != argc {
                        continue;
                    }
                }
                let names = filter_names(pkg, proc);
                if name_candidates(&name_l).iter().any(|c| names.contains(c)) {
                    hits.push(proc_key(pkg, proc));
                }
            }
        }
        if hits.is_empty() {
            unmatched.push(match entry.argc {
                Some(a) => format!("{}:{}", entry.name, a),
                None => entry.name.clone(),
            });
            continue;
        }
        for k in hits {
            selected.entry(k).or_insert("target");
        }
    }

    // ── Fixpoint closure ──
    let mut edges_cache: HashMap<ProcKey, HashSet<ProcKey>> = HashMap::new();
    let mut queue: VecDeque<ProcKey> =
        selected.iter().filter(|(_, v)| **v == "target").map(|(k, _)| k.clone()).collect();
    while let Some(key) = queue.pop_front() {
        if !edges_cache.contains_key(&key) {
            let edges = match idx.loc.get(&key) {
                Some(&(pi, ri)) => {
                    let pkg = &parsed.packages[pi];
                    let proc = &pkg.procedures[ri];
                    resolve_edges(&pkg.package_name, proc, &idx, &parsed.packages)
                }
                None => HashSet::new(),
            };
            edges_cache.insert(key.clone(), edges);
        }
        for ekey in edges_cache[&key].iter() {
            if idx.keys.contains(ekey) && !selected.contains_key(ekey) {
                selected.insert(ekey.clone(), "closure");
                queue.push_back(ekey.clone());
            }
        }
    }

    // ── Apply selection ──
    let (mut n_target, mut n_closure, mut n_excluded) = (0usize, 0usize, 0usize);
    let mut dropped = 0usize;
    let mut new_packages: Vec<PackageInfo> = Vec::with_capacity(parsed.packages.len());
    for mut pkg in parsed.packages.drain(..) {
        let mut keep = Vec::new();
        let mut excluded = Vec::new();
        for mut proc in std::mem::take(&mut pkg.procedures) {
            let key = proc_key(&pkg, &proc);
            match selected.get(&key) {
                Some(&"target") => {
                    proc.selection = "target".to_string();
                    n_target += 1;
                    keep.push(proc);
                }
                Some(&"closure") => {
                    proc.selection = "closure".to_string();
                    n_closure += 1;
                    keep.push(proc);
                }
                _ => {
                    proc.selection = "excluded".to_string();
                    n_excluded += 1;
                    excluded.push(proc);
                }
            }
        }
        pkg.excluded_procedures = excluded;
        if keep.is_empty() {
            dropped += 1;
            // Keep the package (with excluded_procedures populated) so the
            // conversion report can still list its filtered-out routines.
            parsed.filter_dropped.push(pkg);
            continue;
        }
        pkg.procedures = keep;
        new_packages.push(pkg);
    }
    parsed.packages = new_packages;
    // Rebuild summaries so analysis only resolves against selected routines.
    parsed.summaries = parsed.packages.iter().map(crate::types::PackageSummary::from_package).collect();

    FilterStats { targets: n_target, closure: n_closure, excluded: n_excluded, packages_dropped: dropped, unmatched }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Parameter;
    use std::collections::HashSet;

    fn param() -> Parameter {
        Parameter {
            name: "p1".into(),
            java_type: "Long".into(),
            sql_type: "bigint".into(),
            mode: None,
            default_value: None,
        }
    }

    fn proc(pkg: &str, name: &str, argc: usize, source_file: &str) -> ProcedureInfo {
        let mut p = ProcedureInfo::new(format!("{}.{}", pkg, name), pkg.to_string(), name.to_string());
        p.parameters = (0..argc).map(|_| param()).collect();
        p.source_file = source_file.to_string();
        p
    }

    fn pkg(name: &str, procs: Vec<ProcedureInfo>, source_file: &str) -> PackageInfo {
        PackageInfo {
            package_name: name.to_string(),
            procedures: procs,
            table_refs: HashSet::new(),
            package_vars: HashMap::new(),
            source_file: source_file.to_string(),
            source_files: vec![source_file.to_string()],
            comments: Vec::new(),
            java_package: String::new(),
            custom_types: HashMap::new(),
            extra_mapper_methods: Vec::new(),
            excluded_procedures: Vec::new(),
        }
    }

    fn parsed(packages: Vec<PackageInfo>) -> ParsedPackages {
        ParsedPackages {
            packages,
            summaries: Vec::new(),
            warnings: Vec::new(),
            skipped: Vec::new(),
            errors: Vec::new(),
            filter_dropped: Vec::new(),
        }
    }

    #[test]
    fn parse_entries() {
        let entries = vec!["pkg_a.proc_x".to_string(), "pkg_a.proc_y:2".to_string(), "bare".to_string()];
        let parsed = parse_filter_entries(&entries);
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[0], FilterEntry { name: "pkg_a.proc_x".into(), argc: None });
        assert_eq!(parsed[1], FilterEntry { name: "pkg_a.proc_y".into(), argc: Some(2) });
        assert_eq!(parsed[2], FilterEntry { name: "bare".into(), argc: None });
    }

    #[test]
    fn filter_selects_target_and_drops_empty_packages() {
        let mut p = parsed(vec![
            pkg(
                "pkg_a",
                vec![proc("pkg_a", "proc_a1", 0, "PKG_A.sql"), proc("pkg_a", "proc_a2", 0, "PKG_A.sql")],
                "PKG_A.sql",
            ),
            pkg("pkg_b", vec![proc("pkg_b", "proc_b1", 0, "PKG_B.sql")], "PKG_B.sql"),
        ]);
        let entries = parse_filter_entries(&["pkg_a.proc_a1".to_string()]);
        let stats = apply_procedure_filter(&mut p, &entries);
        assert_eq!(stats.targets, 1);
        assert_eq!(stats.excluded, 2);
        assert_eq!(stats.packages_dropped, 1);
        assert_eq!(p.packages.len(), 1);
        assert_eq!(p.packages[0].procedures.len(), 1);
        assert_eq!(p.packages[0].procedures[0].selection, "target");
        assert_eq!(p.packages[0].excluded_procedures.len(), 1);
        assert_eq!(p.summaries.len(), 1);
        assert_eq!(p.summaries[0].procedures.len(), 1);
    }

    #[test]
    fn unmatched_entry_reported() {
        let mut p = parsed(vec![pkg("pkg_a", vec![proc("pkg_a", "proc_a1", 0, "PKG_A.sql")], "PKG_A.sql")]);
        let entries = parse_filter_entries(&["pkg_a.nope".to_string()]);
        let stats = apply_procedure_filter(&mut p, &entries);
        assert_eq!(stats.unmatched, vec!["pkg_a.nope".to_string()]);
        assert_eq!(stats.targets, 0);
        assert!(p.packages.is_empty());
    }
    #[test]
    fn schema_qualified_entry_matches_stripped_package() {
        // #121: `bigfund.pkg_a.proc_x` must select the same routine as
        // `pkg_a.proc_x` (apply-level, not just name parsing).
        let mut p = parsed(vec![pkg("pkg_a", vec![proc("pkg_a", "proc_x", 0, "PKG_A.sql")], "PKG_A.sql")]);
        let entries = parse_filter_entries(&["bigfund.pkg_a.proc_x".to_string()]);
        let stats = apply_procedure_filter(&mut p, &entries);
        assert!(stats.unmatched.is_empty(), "{:?}", stats.unmatched);
        assert_eq!(stats.targets, 1);
        assert_eq!(p.packages[0].procedures[0].selection, "target");
    }

    #[test]
    fn name_candidates_offer_pkg_proc_tail() {
        assert!(name_candidates("bigfund.pkg_a.proc_x").contains("pkg_a.proc_x"));
        assert!(name_candidates("pkg_a.proc_x").contains("pkg_a.proc_x"));
        assert_eq!(name_candidates("proc_x").len(), 1);
    }
}

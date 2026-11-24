use std::collections::{HashMap, HashSet};

use crate::types::{PackageSummary, UnresolvedCall, VarInfo};

pub struct ScanContext {
    pub type_overrides: HashMap<(String, String), String>,
}

impl ScanContext {
    pub fn new() -> Self {
        Self { type_overrides: HashMap::new() }
    }
}

impl Default for ScanContext {
    fn default() -> Self {
        Self::new()
    }
}

pub struct AnalysisContext {
    pub package_summaries: HashMap<String, PackageSummary>,
    pub package_variables: HashMap<String, VarInfo>,
    pub package_constants: HashMap<String, String>,
    pub stub_procedures: HashSet<(String, usize)>,
    pub stub_reasons: HashMap<(String, usize), Vec<String>>,
    pub unsupported_functions: Vec<String>,
    pub unresolved_calls: Vec<UnresolvedCall>,
    /// Cache for source file contents: path → lines. Avoids re-reading files per procedure.
    pub source_cache: HashMap<String, Vec<String>>,
    /// Debug mode enabled (--debug CLI flag)
    pub debug: bool,
    pub dml_counters: HashMap<String, HashMap<String, usize>>,
    /// Per-package set of mapper statement ids already handed out. The
    /// per-key `dml_counters` cannot see collisions ACROSS counter keys
    /// (e.g. semantic-key `select{Table}` vs proc-name `select{Proc}` can
    /// render the same method_id) — this set is the package-wide uniqueness
    /// guard for `dml_method_name` (issue #128).
    pub dml_used_ids: HashMap<String, HashSet<String>>,
    /// `composite_type_shims` config: declared SQL type name (lowercased) →
    /// `field_lower → java type`. Shim composite types are typically created
    /// inside `DO $$ EXECUTE 'CREATE TYPE …'` dynamic strings, invisible to
    /// the parser — their record field types therefore come from the yaml
    /// declaration, not `custom_types`.
    pub composite_type_shims: HashMap<String, HashMap<String, String>>,
}

impl AnalysisContext {
    /// True when `declared_type` (e.g. `bigfund.map_object`) matches a
    /// configured `composite_type_shims` entry. Matches the full name or its
    /// last dot-separated segment, case-insensitively.
    pub fn is_composite_shim(&self, declared_type: &str) -> bool {
        self.composite_shim_field_types(declared_type).is_some()
    }

    /// Declared field-type map for a shim type: `field_lower → java type`.
    /// Returns `None` when the type is not shim-configured.
    pub fn composite_shim_field_types(&self, declared_type: &str) -> Option<&HashMap<String, String>> {
        if self.composite_type_shims.is_empty() {
            return None;
        }
        let lower = declared_type.trim().to_lowercase();
        if let Some(f) = self.composite_type_shims.get(&lower) {
            return Some(f);
        }
        let bare = lower.rsplit('.').next().unwrap_or(&lower);
        self.composite_type_shims.get(bare)
    }
}

impl AnalysisContext {
    pub fn new() -> Self {
        Self {
            package_summaries: HashMap::new(),
            package_variables: HashMap::new(),
            package_constants: HashMap::new(),
            stub_procedures: HashSet::new(),
            stub_reasons: HashMap::new(),
            unsupported_functions: Vec::new(),
            unresolved_calls: Vec::new(),
            source_cache: HashMap::new(),
            debug: false,
            dml_counters: HashMap::new(),
            dml_used_ids: HashMap::new(),
            composite_type_shims: HashMap::new(),
        }
    }
}

impl Default for AnalysisContext {
    fn default() -> Self {
        Self::new()
    }
}

pub struct GenerationContext {
    pub base_package: String,
    pub logger_config: crate::config::LoggerConfig,
}

impl GenerationContext {
    pub fn new(base_package: &str, logger_config: crate::config::LoggerConfig) -> Self {
        Self { base_package: base_package.into(), logger_config }
    }
}

pub struct StatementContext<'a> {
    pub summaries: &'a HashMap<String, PackageSummary>,
    pub dml_counter: HashMap<String, usize>,
    /// Mapper statement ids already used in this package (issue #128).
    pub dml_used_ids: HashSet<String>,
    pub sm_enum_name: Option<String>,
    pub sm_labels: HashSet<String>,
    pub debug: bool,
    pub current_stmt_idx: usize,
    pub stmt_lines: Vec<u32>,
    pub unresolved_calls: Vec<UnresolvedCall>,
    /// Issue #124 P1-3 (review #129): ogsql-parser misparses `coll(i) := v` as
    /// `ProcedureCall(coll, [i])` followed by `Sql(":= v")`. The procedure-call
    /// handler stashes the element write here; the Sql handler pairs it into
    /// `coll.set(i-1, v)`. Tuple: (base java expr, index java expr, elem java type).
    pub pending_coll_elem_assign: Option<(String, String, Option<String>)>,
    /// Monotonic counter for `_collIdxN` locals bound from non-trivial
    /// collection index expressions (review #129 R4/§7-2) — decoupled from
    /// line numbers so copied/rewritten logic lines cannot collide.
    pub coll_idx_counter: usize,
}

impl<'a> StatementContext<'a> {
    pub fn new(summaries: &'a HashMap<String, PackageSummary>) -> Self {
        Self {
            summaries,
            dml_counter: HashMap::new(),
            dml_used_ids: HashSet::new(),
            sm_enum_name: None,
            sm_labels: HashSet::new(),
            debug: false,
            current_stmt_idx: 0,
            stmt_lines: Vec::new(),
            unresolved_calls: Vec::new(),
            pending_coll_elem_assign: None,
            coll_idx_counter: 0,
        }
    }
}

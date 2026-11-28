use std::collections::{BTreeSet, HashMap, HashSet};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GotoPattern {
    CleanupExit,
    LoopSimulation,
    LogicSkip,
    DeepNestedBreak,
    StateMachine,
}

#[derive(Debug, Clone)]
pub struct GotoInfo {
    pub label: String,
    pub stmt_index: usize,
    pub nesting_depth: usize,
    pub inside_loop: bool,
    pub is_forward: bool,
    pub is_backward: bool,
}

#[derive(Debug, Clone)]
pub struct GotoAnalysis {
    pub pattern: Option<GotoPattern>,
    pub labels: HashMap<String, usize>,
    pub gotos: Vec<GotoInfo>,
    pub has_backward: bool,
    pub has_forward: bool,
    pub cross_block: bool,
}

// ── Parameter ──

#[derive(Debug, Clone, PartialEq)]
pub struct Parameter {
    pub name: String,
    pub java_type: String,
    pub sql_type: String,
    pub mode: Option<ParamMode>,
    pub default_value: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParamMode {
    In,
    Out,
    InOut,
}

impl Parameter {
    pub fn is_out(&self) -> bool {
        matches!(self.mode, Some(ParamMode::Out) | Some(ParamMode::InOut))
    }

    pub fn is_refcursor(&self) -> bool {
        let t = self.sql_type.to_lowercase();
        t == "refcursor" || t == "ref cursor" || t == "refcur" || t == "cursor"
    }

    /// True when this param does not appear in the generated Java method
    /// signature: refcursor OUT params are returned as
    /// `List<Map<String, Object>>` instead of an `AtomicReference` slot
    /// (see `generate::service::build_service_method`). Call sites must
    /// neither count nor emit an argument for them — otherwise the call
    /// has more arguments than the method declares ("required N found
    /// N+1", issue #136 batch-4).
    pub fn dropped_from_signature(&self) -> bool {
        self.is_out() && self.is_refcursor()
    }
}

/// A single candidate for a globally-visible (cross-package) procedure/function
/// reachable by its java method name. Multiple entries may share the same method
/// name when several packages define a procedure with the same name (overloads
/// across packages) — all candidates are retained so callers can disambiguate by
/// arity and/or originating package.
#[derive(Debug, Clone)]
pub struct GlobalFnEntry {
    /// Service field/variable name to call. Derived from the defining package name only,
    /// e.g. package `issue_79_unqualified_fn_callee` → svc_var "issue79UnqualifiedFnCalleeService".
    pub svc_var: String,
    /// Package name the candidate is defined in (compare via `to_lowercase()`).
    pub package: String,
    /// Parameter list of the candidate, used for arity/type matching against call sites.
    pub params: Vec<Parameter>,
    /// Declared Java return type of the candidate (`None` for procedures / unknown).
    /// Used by `expr.rs::call_expr_return_type` to detect that a call expression's
    /// *result* is BigDecimal even when the call site itself has no textual
    /// BigDecimal markers (see root cause B3, issue #107 follow-up).
    pub return_type: Option<String>,
    /// Resolved IN-param signature types parallel to `params` — the type the
    /// callee's service method actually declares (boxed for `default null` /
    /// `IS [NOT] NULL` sentinel params, primitive otherwise). Empty when the
    /// defining procedure could not be resolved; callers fall back to
    /// `boxed_to_primitive`.
    pub param_sig_types: Vec<String>,
}

// ── DML ──

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DmlType {
    #[default]
    Select,
    Insert,
    Update,
    Delete,
}

#[derive(Debug, Clone)]
pub struct DynamicCondition {
    pub condition_expr: String,
    pub sql_fragment: String,
    pub clause_type: String,
    pub tag_name: String,
}

#[derive(Debug, Clone, Default)]
pub struct DmlStatement {
    pub sql_type: DmlType,
    pub method_id: String,
    pub sql_text: String,
    pub result_type: Option<String>,
    pub parameter_types: HashMap<String, String>,
    pub optional_filters: Vec<String>,
    pub returns_list: bool,
    pub extra_params: Vec<(String, String)>,
    pub dynamic_conditions: Vec<DynamicCondition>,
    pub base_sql: String,
    /// Whether this DML is a FORALL batch operation (uses `<foreach>` in XML)
    pub is_forall_batch: bool,
    /// The item variable name used in `<foreach collection="list" item="...">`
    pub forall_batch_list_var: String,
    /// Map from Java variable name to element type for each batch array used in FORALL.
    /// e.g., `{"pArray" -> "String", "pIds" -> "Long"}`
    pub forall_batch_arrays: HashMap<String, String>,
}

// ── Service Calls ──

#[derive(Debug, Clone)]
pub struct ServiceCall {
    pub service_name: String,
    pub method_name: String,
    pub args: Vec<String>,
    pub package_name: String,
}

// ── Cursor ──

#[derive(Debug, Clone)]
pub struct CursorInfo {
    pub query: String,
    pub into_vars: Vec<String>,
    pub is_open: bool,
    pub result_var: Option<String>,
    pub index_var: Option<String>,
}

// ── Custom Types ──

#[derive(Debug, Clone)]
pub struct CustomTypeInfo {
    pub fields: Vec<(String, String)>,
    /// Review #129 B3: raw SQL type text per record field, parallel to
    /// `fields` (same order). `fields` only stores the resolved Java type,
    /// which loses the `<table>.<col>%TYPE` anchor needed to resolve column
    /// types later once the DDL schema cache is available (extraction runs
    /// before `parse_table_ddl`). Empty for non-record types.
    pub field_sql_types: Vec<(String, String)>,
    pub is_record: bool,
    /// Java element type for collection types (`TYPE t IS TABLE OF x`,
    /// `VARRAY`, `INDEX BY`). `None` for records / ref cursors / object
    /// types. Issue #124.
    pub elem_java_type: Option<String>,
}

impl CustomTypeInfo {
    pub fn is_collection(&self) -> bool {
        self.elem_java_type.is_some() && !self.is_record
    }
}

// ── Scheduler Task ──

#[derive(Debug, Clone)]
pub struct SchedulerTask {
    pub task_name: String,
    pub schedule: String,
    pub procedure_name: String,
}

// ── Var Info (package-level variables) ──

#[derive(Debug, Clone)]
pub struct VarInfo {
    pub name: String,
    pub java_type: String,
    pub sql_type: String,
    pub default_value: Option<String>,
    pub is_constant: bool,
}

// ── Comment ──

#[derive(Debug, Clone)]
pub struct CommentBlock {
    pub text: String,
    pub start_line: u32,
    pub end_line: u32,
    pub is_block: bool,
}

// ── Procedure Info ──

#[derive(Debug, Clone)]
pub struct ProcedureInfo {
    pub name: String,
    pub package: String,
    pub proc_name: String,
    pub is_function: bool,
    pub return_type: Option<String>,
    pub parameters: Vec<Parameter>,

    pub sql_text: String,
    pub body: Option<ogsql_parser::ast::plpgsql::PlBlock>,

    // Filled during analysis phase
    pub dml_statements: Vec<DmlStatement>,
    pub service_calls: Vec<ServiceCall>,
    pub java_logic_lines: Vec<String>,
    pub imports: BTreeSet<String>,
    pub local_vars: HashMap<String, String>,
    pub local_var_defaults: HashMap<String, String>,

    /// %ROWTYPE / RECORD-typed locals: var_lower → { field_lower → Java type }.
    /// Populated from the table's DDL so field access (v_wm.wm_ts_value) can
    /// emit typed extraction instead of raw Object Map.get.
    pub rowtype_field_types: HashMap<String, HashMap<String, String>>,
    /// %ROWTYPE / record-typed PARAMETERS: param_lower → { field_lower →
    /// Java type }. Kept separate from `rowtype_field_types` so dynamic-SQL
    /// extra-param resolution (which scans locals) is not perturbed; used by
    /// generated test fixtures to populate record-shaped param maps.
    pub param_rowtype_fields: HashMap<String, HashMap<String, String>>,
    pub table_refs: HashSet<String>,
    pub var_assignments: HashMap<String, String>,
    pub dynamic_sql_templates: HashMap<String, (String, Vec<(String, bool)>)>,
    pub sql_concat_chain: HashMap<String, Vec<(String, String, String)>>,
    pub is_autonomous: bool,
    pub scheduler_tasks: Vec<SchedulerTask>,

    // Cursor tracking
    pub open_cursors: HashMap<String, CursorInfo>,
    pub refcursor_out_params: HashSet<String>,
    /// IN params (lowercased names) used in NULL semantics — `IS [NOT]
    /// NULL`, `= / != / <> NULL`, or `NVL`/`NVL2`/`COALESCE`/`NANVL` args.
    /// Populated during analyze (while `body` is still attached) so the
    /// boxed-signature decision in `in_param_signature_type` stays
    /// consistent between expression lowering and method emission
    /// (issue #136 batch-4 F5).
    pub null_semantics_params: HashSet<String>,
    pub cursor_decls: HashMap<String, String>,
    pub cursor_params: HashMap<String, Vec<String>>,

    // Custom types
    pub custom_types: HashMap<String, CustomTypeInfo>,

    pub package_vars: HashMap<String, VarInfo>,

    /// Other packages' package-level variables for `other_pkg.var`
    /// references: pkg_name_lower → (svc_var, var_lower → VarInfo).
    /// Populated from proc_summaries during analysis; the dotted-ref
    /// lowering uses it to emit `<pkg>Service.<var>` instead of an
    /// undeclared identifier or a TOBEFIX marker (issue #136 corpus).
    pub cross_pkg_vars: HashMap<String, (String, HashMap<String, VarInfo>)>,

    pub has_array_vars: bool,
    pub out_local_vars: HashMap<String, String>,

    pub goto_analysis: Option<GotoAnalysis>,

    /// Maps java method name → list of parameter lists (one per overload) for sibling procedures.
    /// Populated before analysis phase so function_call_to_java can coerce args.
    pub package_proc_params: HashMap<String, Vec<Vec<Parameter>>>,

    /// Maps java method name → sibling procedures' declared return types
    /// (one entry per overload). Used by `expr.rs::call_expr_return_type` to
    /// resolve `this.<fn>(...)` self-calls for return-type coercion.
    pub package_proc_returns: HashMap<String, Vec<Option<String>>>,

    /// Maps java method name → all candidate procedures across ALL packages sharing that name.
    /// Used during expression resolution to find cross-package function references.
    /// Populated during analysis phase from proc_summaries.
    /// Example: "funcGetFrameDate" → [GlobalFnEntry { svc_var: "boyfriendService", .. }]
    pub all_proc_params: HashMap<String, Vec<GlobalFnEntry>>,

    pub select_counter: usize,
    pub for_loop_counter: usize,
    pub plain_loop_counter: usize,
    pub catch_counter: usize,
    /// Monotonic counter for `_outTmpN` typed temp carriers emitted when a
    /// statement-level CALL's OUT arg carrier inner type mismatches the
    /// callee's OUT param inner type (issue #144 batch4b).
    pub call_out_tmp_counter: usize,

    /// Numeric-range FOR loop counters auto-created by the statement handler
    /// (NOT declared in the SQL). A real SQL-declared var is absent from this
    /// set even though it also appears in `local_vars` — distinguishing the two
    /// matters because javac forbids a for-init `int i` shadowing a method-level
    /// `i`, so real declarations must be reused in the loop header.
    pub range_loop_counters: HashSet<String>,

    /// Range-FOR counter names (`local_vars` keys) whose loop body is
    /// currently being processed — i.e. counters of ENCLOSING loops. A nested
    /// `for (int i = ...)` reusing an enclosing counter's java name is a
    /// javac shadowing error, so the inner loop must reuse+restore instead.
    pub active_range_fors: Vec<String>,

    /// Monotonic suffix for save/restore temporaries emitted around nested
    /// same-name range FOR loops.
    pub range_for_save_counter: u32,

    pub source_file: String,
    pub source_path: String,
    pub source_start_line: u32,
    pub source_end_line: u32,
    pub leading_comments: Vec<CommentBlock>,
    pub inline_comments: Vec<CommentBlock>,
    pub local_var_source_lines: HashMap<String, u32>,

    /// Package variable names (snake_case) that are written (assigned to) in this procedure body.
    /// Used to detect de-facto constants: package vars never written here are treated as read-only.
    pub written_package_vars: HashSet<String>,

    /// Issue #144 batch1b/batch2 (Python parity): record-field placeholder
    /// registry for dynamic SQL. `s.diff_column` inside a concat fragment
    /// becomes the `#{s_diff_column}` placeholder; the maps below bind that
    /// placeholder to the Java value expression (`s.get("diff_column")`) and
    /// to its record root (`s`) so loop-scope filtering can tell which
    /// fragments are only valid inside a cursor FOR body.
    pub dot_access_exprs: HashMap<String, String>,
    pub dot_access_roots: HashMap<String, String>,
    /// Cursor/FOR-record loop variable names (lowercased) — the Python
    /// engine's `_loop_vars`. Populated when a `for (Map<String, Object> v : …)`
    /// header is emitted; persists for the whole procedure like Python.
    pub loop_vars: HashSet<String>,

    /// procedure_filter selection: "target" | "closure" | "excluded" | "" (#120)
    pub selection: String,
}

impl ProcedureInfo {
    pub fn new(name: String, package: String, proc_name: String) -> Self {
        debug_assert!(!name.is_empty() && !proc_name.is_empty(), "ProcedureInfo name and proc_name must not be empty");
        Self {
            name,
            package,
            proc_name,
            is_function: false,
            return_type: None,
            parameters: Vec::new(),
            sql_text: String::new(),
            body: None,
            dml_statements: Vec::new(),
            service_calls: Vec::new(),
            java_logic_lines: Vec::new(),
            imports: BTreeSet::new(),
            local_vars: HashMap::new(),
            local_var_defaults: HashMap::new(),
            rowtype_field_types: HashMap::new(),
            param_rowtype_fields: HashMap::new(),
            table_refs: HashSet::new(),
            var_assignments: HashMap::new(),
            dynamic_sql_templates: HashMap::new(),
            sql_concat_chain: HashMap::new(),
            is_autonomous: false,
            scheduler_tasks: Vec::new(),
            open_cursors: HashMap::new(),
            refcursor_out_params: HashSet::new(),
            null_semantics_params: HashSet::new(),
            cursor_decls: HashMap::new(),
            cursor_params: HashMap::new(),
            custom_types: HashMap::new(),
            package_vars: HashMap::new(),
            cross_pkg_vars: HashMap::new(),
            has_array_vars: false,
            out_local_vars: HashMap::new(),
            goto_analysis: None,
            package_proc_params: HashMap::new(),
            package_proc_returns: HashMap::new(),
            all_proc_params: HashMap::new(),
            select_counter: 0,
            for_loop_counter: 0,
            plain_loop_counter: 0,
            catch_counter: 0,
            call_out_tmp_counter: 0,
            range_loop_counters: HashSet::new(),
            active_range_fors: Vec::new(),
            range_for_save_counter: 0,
            source_file: String::new(),
            source_path: String::new(),
            source_start_line: 0,
            source_end_line: 0,
            leading_comments: Vec::new(),
            inline_comments: Vec::new(),
            local_var_source_lines: HashMap::new(),
            written_package_vars: HashSet::new(),
            dot_access_exprs: HashMap::new(),
            dot_access_roots: HashMap::new(),
            loop_vars: HashSet::new(),
            selection: String::new(),
        }
    }

    pub fn is_stub(&self) -> bool {
        self.java_logic_lines.len() == 1 && self.java_logic_lines[0].contains("TODO") && self.dml_statements.is_empty()
    }
}

// ── Package Info ──

#[derive(Debug, Clone)]
pub struct PackageInfo {
    pub package_name: String,
    pub procedures: Vec<ProcedureInfo>,
    pub table_refs: HashSet<String>,
    pub package_vars: HashMap<String, VarInfo>,
    /// Package-var keys (same keys as `package_vars`) that are read from
    /// OTHER packages via `<pkg>Service.<var>` — those fields must be
    /// emitted `public` instead of `private` to compile (issue #136).
    pub externally_shared_vars: HashSet<String>,
    pub source_file: String,
    pub source_files: Vec<String>,
    pub comments: Vec<CommentBlock>,
    pub java_package: String,
    pub custom_types: HashMap<String, CustomTypeInfo>,
    pub extra_mapper_methods: Vec<(String, String, String)>,
    /// Routines filtered out by `procedure_filter` — no Java artifacts (#120)
    pub excluded_procedures: Vec<ProcedureInfo>,
}

// ── Lightweight Summaries (Phase 2 cross-package analysis) ──

#[derive(Debug, Clone)]
pub struct ProcedureSummary {
    pub name: String,
    pub proc_name: String,
    pub package: String,
    pub is_function: bool,
    pub return_type: Option<String>,
    pub parameters: Vec<Parameter>,
    pub service_calls: Vec<ServiceCall>,
}

impl ProcedureSummary {
    pub fn from_procedure(proc: &ProcedureInfo) -> Self {
        Self {
            name: proc.name.clone(),
            proc_name: proc.proc_name.clone(),
            package: proc.package.clone(),
            is_function: proc.is_function,
            return_type: proc.return_type.clone(),
            parameters: proc.parameters.clone(),
            service_calls: proc.service_calls.clone(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct PackageSummary {
    pub name: String,
    pub java_package: String,
    pub procedures: Vec<ProcedureSummary>,
    pub package_vars: HashMap<String, VarInfo>,
}

impl PackageSummary {
    pub fn from_package(pkg: &PackageInfo) -> Self {
        Self {
            name: pkg.package_name.clone(),
            java_package: pkg.java_package.clone(),
            procedures: pkg.procedures.iter().map(ProcedureSummary::from_procedure).collect(),
            package_vars: pkg.package_vars.clone(),
        }
    }

    pub fn find_procedure(&self, proc_name: &str) -> Option<&ProcedureSummary> {
        self.procedures.iter().find(|p| p.proc_name.eq_ignore_ascii_case(proc_name))
    }
}

// ── Conversion Error ──

#[derive(Debug, Clone)]
pub enum ConversionError {
    Io { path: String, message: String },
    Parse { path: String, message: String },
    Extract { path: String, message: String },
    Analysis { procedure: String, message: String },
    Generation { package_name: String, message: String },
    Config { message: String },
}

impl std::fmt::Display for ConversionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io { path, message } => write!(f, "I/O error [{}]: {}", path, message),
            Self::Parse { path, message } => write!(f, "Parse error [{}]: {}", path, message),
            Self::Extract { path, message } => write!(f, "Extract error [{}]: {}", path, message),
            Self::Analysis { procedure, message } => {
                write!(f, "Analysis error [{}]: {}", procedure, message)
            }
            Self::Generation { package_name, message } => {
                write!(f, "Generation error [{}]: {}", package_name, message)
            }
            Self::Config { message } => write!(f, "Config error: {}", message),
        }
    }
}

impl std::error::Error for ConversionError {}

// ── Skipped Item (non-procedure statements) ──

#[derive(Debug, Clone)]
pub struct SkippedItem {
    pub item_type: String,
    pub name: String,
    pub source_file: String,
    pub line_number: u32,
    pub reason: String,
}

// ── Unresolved Cross-Package Call ──

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnresolvedCall {
    pub caller: String,
    pub callee: String,
    pub caller_file: String,
    pub args: String,
    pub hint: String,
}

// ── Procedure Mapping (for conversion report) ──

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcedureMapping {
    pub sql_procedure: String,
    pub sql_package: String,
    pub java_service: String,
    pub java_method: String,
    pub is_stub: bool,
    pub notes: Vec<String>,
    /// procedure_filter selection: "target" | "closure" | "" (#120)
    pub selection: String,
}

// ── Pipeline Phase Outputs (type-state pattern) ──

pub struct ParsedPackages {
    pub packages: Vec<PackageInfo>,
    pub summaries: Vec<PackageSummary>,
    pub warnings: Vec<String>,
    pub skipped: Vec<SkippedItem>,
    pub errors: Vec<ConversionError>,
    /// Packages dropped entirely by procedure_filter (all routines excluded) (#120)
    pub filter_dropped: Vec<PackageInfo>,
}

pub struct AnalyzedPackages {
    pub packages: Vec<PackageInfo>,
    pub summaries: Vec<PackageSummary>,
    pub warnings: Vec<String>,
    pub skipped: Vec<SkippedItem>,
    pub errors: Vec<ConversionError>,
    /// Packages dropped entirely by procedure_filter (all routines excluded) (#120)
    pub filter_dropped: Vec<PackageInfo>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parameter_is_out() {
        let p_in = Parameter {
            name: "x".into(),
            java_type: "Long".into(),
            sql_type: "bigint".into(),
            mode: Some(ParamMode::In),
            default_value: None,
        };
        assert!(!p_in.is_out());

        let p_out = Parameter {
            name: "y".into(),
            java_type: "String".into(),
            sql_type: "varchar".into(),
            mode: Some(ParamMode::Out),
            default_value: None,
        };
        assert!(p_out.is_out());

        let p_inout = Parameter {
            name: "z".into(),
            java_type: "Integer".into(),
            sql_type: "integer".into(),
            mode: Some(ParamMode::InOut),
            default_value: None,
        };
        assert!(p_inout.is_out());
    }

    #[test]
    fn test_parameter_is_refcursor() {
        let cases = vec![
            ("refcursor", true),
            ("REFCURSOR", true),
            ("ref cursor", true),
            ("REF CURSOR", true),
            ("refcur", true),
            ("cursor", true),
            ("Cursor", true),
            ("varchar", false),
            ("integer", false),
        ];
        for (sql_type, expected) in cases {
            let p = Parameter {
                name: "p".into(),
                java_type: "Object".into(),
                sql_type: sql_type.into(),
                mode: None,
                default_value: None,
            };
            assert_eq!(p.is_refcursor(), expected, "sql_type={}", sql_type);
        }
    }

    #[test]
    fn test_procedure_info_new() {
        let proc = ProcedureInfo::new("pkg_order.create_order".into(), "pkg_order".into(), "create_order".into());
        assert_eq!(proc.name, "pkg_order.create_order");
        assert_eq!(proc.package, "pkg_order");
        assert_eq!(proc.proc_name, "create_order");
        assert!(!proc.is_function);
        assert!(proc.parameters.is_empty());
        assert!(proc.dml_statements.is_empty());
        assert!(proc.imports.is_empty());
        assert!(proc.local_vars.is_empty());
    }

    #[test]
    fn test_procedure_info_is_stub_empty() {
        let proc = ProcedureInfo::new("a.b".into(), "a".into(), "b".into());
        assert!(!proc.is_stub());
    }

    #[test]
    fn test_procedure_info_is_stub_with_todo() {
        let mut proc = ProcedureInfo::new("a.b".into(), "a".into(), "b".into());
        proc.java_logic_lines.push("// TODO: unhandled statement".into());
        assert!(proc.is_stub());
    }

    #[test]
    fn test_procedure_info_is_stub_with_dml_not_stub() {
        let mut proc = ProcedureInfo::new("a.b".into(), "a".into(), "b".into());
        proc.java_logic_lines.push("// TODO: unhandled statement".into());
        proc.dml_statements.push(DmlStatement {
            sql_type: DmlType::Select,
            method_id: "selectOrder".into(),
            sql_text: "SELECT * FROM orders".into(),
            result_type: None,
            ..Default::default()
        });
        assert!(!proc.is_stub());
    }

    #[test]
    fn test_procedure_summary_from_procedure() {
        let mut proc = ProcedureInfo::new("pkg_order.create_order".into(), "pkg_order".into(), "create_order".into());
        proc.is_function = false;
        proc.parameters.push(Parameter {
            name: "p_status".into(),
            java_type: "String".into(),
            sql_type: "varchar".into(),
            mode: Some(ParamMode::In),
            default_value: None,
        });

        let summary = ProcedureSummary::from_procedure(&proc);
        assert_eq!(summary.name, "pkg_order.create_order");
        assert_eq!(summary.proc_name, "create_order");
        assert_eq!(summary.parameters.len(), 1);
        assert_eq!(summary.parameters[0].name, "p_status");
    }

    #[test]
    fn test_package_summary_find_procedure() {
        let pkg = PackageInfo {
            package_name: "pkg_order".into(),
            procedures: vec![
                ProcedureInfo::new("pkg_order.create".into(), "pkg_order".into(), "create".into()),
                ProcedureInfo::new("pkg_order.cancel".into(), "pkg_order".into(), "cancel".into()),
            ],
            table_refs: HashSet::new(),
            package_vars: HashMap::new(),
            externally_shared_vars: Default::default(),
            source_file: "pkg_order.sql".into(),
            source_files: vec!["pkg_order.sql".into()],
            comments: Vec::new(),
            java_package: "com.example".into(),
            custom_types: HashMap::new(),
            extra_mapper_methods: Vec::new(),
            excluded_procedures: Vec::new(),
        };

        let summary = PackageSummary::from_package(&pkg);
        assert!(summary.find_procedure("create").is_some());
        assert!(summary.find_procedure("cancel").is_some());
        assert!(summary.find_procedure("nonexistent").is_none());
    }

    #[test]
    fn test_conversion_error_display() {
        let err = ConversionError::Parse { path: "test.sql".into(), message: "unexpected token".into() };
        assert_eq!(format!("{}", err), "Parse error [test.sql]: unexpected token");

        let err = ConversionError::Config { message: "missing output_dir".into() };
        assert_eq!(format!("{}", err), "Config error: missing output_dir");
    }

    #[test]
    fn test_dml_type_variants() {
        assert_ne!(DmlType::Select, DmlType::Insert);
        assert_ne!(DmlType::Update, DmlType::Delete);
    }

    #[test]
    fn test_custom_type_info() {
        let ct = CustomTypeInfo {
            fields: vec![("id".into(), "Long".into()), ("name".into(), "String".into())],
            field_sql_types: Vec::new(),
            is_record: false,
            elem_java_type: None,
        };
        assert_eq!(ct.fields.len(), 2);
        assert!(!ct.is_record);
        assert!(!ct.is_collection());
    }

    #[test]
    fn test_cursor_info() {
        let ci = CursorInfo {
            query: "SELECT * FROM orders".into(),
            into_vars: vec!["v_id".into(), "v_name".into()],
            is_open: true,
            result_var: None,
            index_var: None,
        };
        assert!(ci.is_open);
        assert_eq!(ci.into_vars.len(), 2);
    }

    #[test]
    fn test_var_info() {
        let vi = VarInfo {
            name: "g_max_retries".into(),
            java_type: "Integer".into(),
            sql_type: "integer".into(),
            default_value: Some("3".into()),
            is_constant: true,
        };
        assert!(vi.is_constant);
        assert_eq!(vi.default_value.as_deref(), Some("3"));
    }

    #[test]
    fn test_skipped_item() {
        let si = SkippedItem {
            item_type: "DDL".into(),
            name: "CREATE TABLE orders".into(),
            source_file: "tables.sql".into(),
            line_number: 42,
            reason: "Table creation not converted".into(),
        };
        assert_eq!(si.item_type, "DDL");
    }
}

use std::collections::{HashMap, HashSet};

use crate::types::{PackageSummary, VarInfo};

pub struct ScanContext {
    pub type_overrides: HashMap<(String, String), String>,
}

impl ScanContext {
    pub fn new() -> Self {
        Self {
            type_overrides: HashMap::new(),
        }
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
    pub unresolved_calls: Vec<String>,
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
        Self {
            base_package: base_package.into(),
            logger_config,
        }
    }
}

pub struct StatementContext<'a> {
    pub summaries: &'a HashMap<String, PackageSummary>,
    pub dml_counter: HashMap<String, usize>,
}

impl<'a> StatementContext<'a> {
    pub fn new(summaries: &'a HashMap<String, PackageSummary>) -> Self {
        Self {
            summaries,
            dml_counter: HashMap::new(),
        }
    }
}

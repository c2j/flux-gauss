use std::collections::HashMap;
use std::path::Path;

use crate::types::{PackageInfo, ProcedureMapping, SkippedItem};

#[derive(Debug, Clone)]
pub struct ConversionReport {
    pub timestamp: String,
    pub total_files: usize,
    pub total_packages: usize,
    pub total_procedures: usize,
    pub mappings: Vec<ProcedureMapping>,
    pub skipped: Vec<SkippedItem>,
    pub errors: Vec<String>,
}

impl ConversionReport {
    pub fn new() -> Self {
        Self {
            timestamp: chrono::Utc::now().to_rfc3339(),
            total_files: 0,
            total_packages: 0,
            total_procedures: 0,
            mappings: Vec::new(),
            skipped: Vec::new(),
            errors: Vec::new(),
        }
    }

    pub fn to_markdown(&self) -> String {
        let mut lines = Vec::new();
        lines.push("# FluxGauss Conversion Report".into());
        lines.push(String::new());
        lines.push(format!("Generated: {}", self.timestamp));
        lines.push(String::new());
        lines.push(format!("- Files processed: {}", self.total_files));
        lines.push(format!("- Packages: {}", self.total_packages));
        lines.push(format!("- Procedures: {}", self.total_procedures));
        lines.push(String::new());

        if !self.mappings.is_empty() {
            lines.push("## Procedure Mappings".into());
            lines.push(String::new());
            lines.push("| SQL Procedure | Java Service | Java Method | Stub |".into());
            lines.push("|---|---|---|---|".into());
            for m in &self.mappings {
                lines.push(format!(
                    "| {}.{} | {} | {} | {} |",
                    m.sql_package,
                    m.sql_procedure,
                    m.java_service,
                    m.java_method,
                    if m.is_stub { "Yes" } else { "No" }
                ));
            }
            lines.push(String::new());
        }

        if !self.skipped.is_empty() {
            lines.push("## Skipped Items".into());
            lines.push(String::new());
            for s in &self.skipped {
                lines.push(format!("- [{}] {} ({})", s.item_type, s.name, s.reason));
            }
            lines.push(String::new());
        }

        if !self.errors.is_empty() {
            lines.push("## Errors".into());
            lines.push(String::new());
            for e in &self.errors {
                lines.push(format!("- {}", e));
            }
        }

        lines.join("\n")
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        let content = self.to_markdown();
        std::fs::write(path, content)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_report_new() {
        let report = ConversionReport::new();
        assert_eq!(report.total_files, 0);
        assert!(report.mappings.is_empty());
    }

    #[test]
    fn test_report_to_markdown() {
        let mut report = ConversionReport::new();
        report.total_files = 5;
        report.total_packages = 2;
        report.total_procedures = 10;
        let md = report.to_markdown();
        assert!(md.contains("5"));
        assert!(!md.contains("## Procedure Mappings"));
    }

    #[test]
    fn test_report_with_mappings() {
        let mut report = ConversionReport::new();
        report.mappings.push(ProcedureMapping {
            sql_procedure: "create_order".into(),
            sql_package: "pkg_order".into(),
            java_service: "OrderService".into(),
            java_method: "createOrder".into(),
            is_stub: false,
            notes: Vec::new(),
        });
        let md = report.to_markdown();
        assert!(md.contains("create_order"));
        assert!(md.contains("OrderService"));
    }
}

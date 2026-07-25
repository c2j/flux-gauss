use std::collections::HashMap;
use std::path::Path;

use crate::types::{PackageInfo, ProcedureMapping, SkippedItem, UnresolvedCall};

#[derive(Debug, Clone)]
pub struct ConversionReport {
    pub timestamp: String,
    pub config_path: String,
    pub output_dir: String,
    pub total_files: usize,
    pub total_packages: usize,
    pub total_procedures: usize,
    pub total_dml: usize,
    pub total_cross_calls: usize,
    pub mappings: Vec<ProcedureMapping>,
    pub skipped: Vec<SkippedItem>,
    pub errors: Vec<String>,
    pub unresolved_calls: Vec<UnresolvedCall>,
    pub stub_count: usize,
}

impl ConversionReport {
    pub fn new() -> Self {
        Self {
            timestamp: chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
            config_path: String::new(),
            output_dir: String::new(),
            total_files: 0,
            total_packages: 0,
            total_procedures: 0,
            total_dml: 0,
            total_cross_calls: 0,
            mappings: Vec::new(),
            skipped: Vec::new(),
            errors: Vec::new(),
            unresolved_calls: Vec::new(),
            stub_count: 0,
        }
    }

    pub fn to_markdown(&self) -> String {
        let mut lines = Vec::new();
        lines.push("# FluxGauss 转换报告".into());
        lines.push(String::new());
        lines.push(format!("**生成时间**: {}  ", self.timestamp));
        lines.push(format!("**配置文件**: {}  ", self.config_path));
        lines.push(format!("**输出目录**: `{}`", self.output_dir));
        lines.push(String::new());
        lines.push("---".into());
        lines.push(String::new());
        lines.push("## 概览".into());
        lines.push(String::new());
        lines.push("| 指标 | 数量 |".into());
        lines.push("|------|------|".into());
        lines.push(format!("| 转换的包 | {} |", self.total_packages));
        lines.push(format!("| 存储过程/函数 | {} |", self.total_procedures));
        lines.push(format!("| 提取的 DML (MyBatis mapper) | {} |", self.total_dml));
        lines.push(format!("| 跨包调用 | {} |", self.total_cross_calls));
        let converted = self.mappings.iter().filter(|m| !m.is_stub).count();
        lines.push(format!("| 成功转换 | {} |", converted));
        if self.stub_count > 0 {
            lines.push(format!("| ⚠️ Stub（需人工审查） | {} |", self.stub_count));
        }
        lines.push(format!("| ⏭ 跳过（不涉及存储过程） | {} |", self.skipped.len()));
        if !self.unresolved_calls.is_empty() {
            lines.push(format!("| ⚠️ 未解析的跨包调用 | {} |", self.unresolved_calls.len()));
        }
        lines.push(String::new());

        if !self.mappings.is_empty() {
            lines.push("---".into());
            lines.push(String::new());
            lines.push("## SQL → Java 映射".into());
            lines.push(String::new());
            lines.push("| SQL Procedure | Package | Java Service | Java Method | Stub |".into());
            lines.push("|---|---|---|---|---|".into());
            for m in &self.mappings {
                lines.push(format!(
                    "| `{}` | `{}` | `{}` | `{}` | {} |",
                    m.sql_procedure,
                    m.sql_package,
                    m.java_service,
                    m.java_method,
                    if m.is_stub { "⚠️ Stub" } else { "✅" }
                ));
            }
            lines.push(String::new());
        }

        if !self.skipped.is_empty() {
            lines.push("---".into());
            lines.push(String::new());
            lines.push("## ⏭ 跳过项 — 不涉及存储过程，仅作参考".into());
            lines.push(String::new());
            for s in &self.skipped {
                lines.push(format!("- [{}] {} ({})", s.item_type, s.name, s.reason));
            }
            lines.push(String::new());
        }

        if !self.unresolved_calls.is_empty() {
            lines.push("---".into());
            lines.push(String::new());
            lines.push("## ⚠️ 未解析的跨包调用".into());
            lines.push(String::new());
            lines.push("| 调用方 | 被调用方 | 文件 | 参数 | 提示 |".into());
            lines.push("|---|---|---|---|---|".into());
            for call in &self.unresolved_calls {
                lines.push(format!(
                    "| `{}` | `{}` | `{}` | `{}` | {} |",
                    call.caller, call.callee, call.caller_file, call.args, call.hint
                ));
            }
            lines.push(String::new());
        }

        if !self.errors.is_empty() {
            lines.push("---".into());
            lines.push(String::new());
            lines.push("## ❌ 错误".into());
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

    pub fn save_auto(&self, output_dir: &Path) -> Vec<String> {
        let content = self.to_markdown();
        let mut written = Vec::new();

        let report_dir = output_dir.join(".fluxgauss").join("reports");
        if std::fs::create_dir_all(&report_dir).is_ok() {
            let ts = self.timestamp
                .replace(" ", "_")
                .replace(":", "")
                .replace("-", "");
            let ts_path = report_dir.join(format!("conversion-report-{}.md", ts));
            if std::fs::write(&ts_path, &content).is_ok() {
                written.push(ts_path.to_string_lossy().into_owned());
            }

            let latest_path = report_dir.join("conversion-report-latest.md");
            let _ = std::fs::write(&latest_path, &content);
        }

        written
    }
}

pub fn build_report(
    packages: &[PackageInfo],
    skipped: Vec<SkippedItem>,
    unresolved_calls: Vec<UnresolvedCall>,
    stub_count: usize,
    config_path: &str,
    output_dir: &str,
    total_files: usize,
) -> ConversionReport {
    let total_procedures: usize = packages.iter().map(|p| p.procedures.len()).sum();
    let total_dml: usize = packages.iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.dml_statements.len())
        .sum();
    let total_cross_calls: usize = packages.iter()
        .flat_map(|p| p.procedures.iter())
        .map(|p| p.service_calls.len())
        .sum();

    let mut mappings = Vec::new();
    for pkg in packages {
        let class_name = crate::naming::package_to_classname(&pkg.package_name);
        for proc in &pkg.procedures {
            mappings.push(ProcedureMapping {
                sql_procedure: proc.proc_name.clone(),
                sql_package: pkg.package_name.clone(),
                java_service: format!("{}Service", class_name),
                java_method: crate::naming::java_method_name(&proc.proc_name),
                is_stub: proc.is_stub(),
                notes: Vec::new(),
            });
        }
    }

    ConversionReport {
        timestamp: chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        config_path: config_path.to_string(),
        output_dir: output_dir.to_string(),
        total_files,
        total_packages: packages.len(),
        total_procedures,
        total_dml,
        total_cross_calls,
        mappings,
        skipped,
        errors: Vec::new(),
        unresolved_calls,
        stub_count,
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
        assert!(md.contains("10"));
        assert!(!md.contains("## SQL"));
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

    #[test]
    fn test_build_report() {
        use crate::types::ProcedureInfo;
        let pkg = crate::types::PackageInfo {
            package_name: "pkg_test".into(),
            procedures: vec![ProcedureInfo::new(
                "pkg_test.do_thing".into(),
                "pkg_test".into(),
                "do_thing".into(),
            )],
            table_refs: Default::default(),
            package_vars: Default::default(),
            source_file: "test.sql".into(),
            comments: vec![],
            java_package: "com.example".into(),
            custom_types: Default::default(),
            extra_mapper_methods: Vec::new(),
        };
        let report = build_report(
            &[pkg],
            vec![],
            vec![],
            0,
            "test.yaml",
            "./dest",
            1,
        );
        assert_eq!(report.total_packages, 1);
        assert_eq!(report.total_procedures, 1);
        assert_eq!(report.mappings.len(), 1);
    }
}

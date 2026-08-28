use std::path::PathBuf;

use rmcp::{
    ErrorData as McpError,
    ServiceExt,
    handler::server::ServerHandler,
    model::{Implementation, InitializeResult, ProtocolVersion, ServerCapabilities},
    tool, tool_handler, tool_router,
    transport,
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::incremental::IncrementalState;
use crate::pipeline;
use crate::report;

#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct ValidateSqlRequest {
    pub files: Vec<String>,
    #[serde(default = "default_encoding")]
    pub encoding: String,
}

fn default_encoding() -> String {
    "utf-8".to_string()
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct FileValidateResultJson {
    pub file: String,
    pub errors: Vec<ValidateErrorJson>,
    pub warnings: Vec<ValidateErrorJson>,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct ValidateErrorJson {
    pub line: usize,
    pub column: usize,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct ValidateSqlResponse {
    pub valid: bool,
    pub error_file_count: usize,
    pub warning_file_count: usize,
    #[schemars(skip)]
    pub file_results: Vec<FileValidateResultJson>,
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct ConvertSqlRequest {
    pub config: Option<serde_json::Value>,
    pub files: Option<Vec<String>>,
    pub output_dir: Option<String>,
    pub base_package: Option<String>,
    #[serde(default)]
    pub full: bool,
    #[serde(default)]
    pub debug: bool,
    #[serde(default)]
    pub skip_validation: bool,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct ConvertSqlResponse {
    pub success: bool,
    pub output_dir: String,
    #[schemars(skip)]
    pub generated_files: Vec<String>,
    #[schemars(skip)]
    pub report: ConvertReportJson,
    #[schemars(skip)]
    pub report_paths: Vec<String>,
    #[schemars(skip)]
    pub log_path: String,
    #[schemars(skip)]
    pub summary: ConvertSummaryJson,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(skip)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct ConvertReportJson {
    pub total_packages: usize,
    pub total_procedures: usize,
    pub total_dml: usize,
    pub total_cross_calls: usize,
    pub stub_count: usize,
    pub mappings: Vec<ProcedureMappingJson>,
    pub errors: Vec<String>,
    pub unresolved_calls: Vec<String>,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct ProcedureMappingJson {
    pub sql_procedure: String,
    pub sql_package: String,
    pub java_service: String,
    pub java_method: String,
    pub is_stub: bool,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
pub struct ConvertSummaryJson {
    pub packages: usize,
    pub procedures: usize,
    pub dml_statements: usize,
    pub stubs: usize,
    pub unresolved_calls: usize,
    pub validation_passed: bool,
}

#[derive(Clone)]
pub struct FluxGaussMcpServer;

#[tool_router]
impl FluxGaussMcpServer {
    #[tool(
        name = "validate_sql",
        description = "Validate SQL stored procedure files for syntax errors. Accepts a list of file paths to PL/pgSQL source files and returns validation results including line/column-level error details."
    )]
    async fn validate_sql(
        &self,
        params: rmcp::handler::server::wrapper::Parameters<ValidateSqlRequest>,
    ) -> Result<rmcp::handler::server::wrapper::Json<ValidateSqlResponse>, McpError> {
        let req = params.0;

        let mut paths = Vec::new();
        for file_path in &req.files {
            let p = PathBuf::from(file_path);
            if !p.exists() {
                return Err(McpError::internal_error(
                    format!("File not found: {}", file_path),
                    None,
                ));
            }
            paths.push(p);
        }

        let validate_result = pipeline::phase0_validate(&paths);

        let mut file_results = Vec::new();
        for file_result in &validate_result.file_results {
            let errors: Vec<ValidateErrorJson> = file_result
                .errors
                .iter()
                .map(validate_error_to_json)
                .collect();
            let warnings: Vec<ValidateErrorJson> = file_result
                .warnings
                .iter()
                .map(validate_error_to_json)
                .collect();
            file_results.push(FileValidateResultJson {
                file: file_result.basename.clone(),
                errors,
                warnings,
            });
        }

        let response = ValidateSqlResponse {
            valid: !validate_result.has_errors(),
            error_file_count: validate_result.error_file_count,
            warning_file_count: validate_result.warning_file_count,
            file_results,
        };

        Ok(rmcp::handler::server::wrapper::Json(response))
    }

    #[tool(
        name = "convert_sql",
        description = "Convert SQL stored procedure files into Spring Boot + MyBatis Java project. Accepts either a config JSON (same schema as fluxgauss.yaml) or individual parameters (files, output_dir, base_package)."
    )]
    async fn convert_sql(
        &self,
        params: rmcp::handler::server::wrapper::Parameters<ConvertSqlRequest>,
    ) -> Result<rmcp::handler::server::wrapper::Json<ConvertSqlResponse>, McpError> {
        let req = params.0;

        let (mut config, sql_files, output_dir) = match resolve_convert_inputs(&req) {
            Ok(v) => v,
            Err(e) => {
                return Err(McpError::internal_error(format!("Invalid input: {}", e), None));
            }
        };

        if let Some(bp) = &req.base_package {
            config.base_package = Some(bp.clone());
        }

        if !req.skip_validation {
            let validate_result = pipeline::phase0_validate(&sql_files);
            if validate_result.has_errors() {
                let mut file_results = Vec::new();
                for file_result in &validate_result.file_results {
                    let errors: Vec<ValidateErrorJson> = file_result
                        .errors
                        .iter()
                        .map(validate_error_to_json)
                        .collect();
                    let warnings: Vec<ValidateErrorJson> = file_result
                        .warnings
                        .iter()
                        .map(validate_error_to_json)
                        .collect();
                    file_results.push(FileValidateResultJson {
                        file: file_result.basename.clone(),
                        errors,
                        warnings,
                    });
                }

                let response = ConvertSqlResponse {
                    success: false,
                    output_dir: output_dir.clone(),
                    generated_files: Vec::new(),
                    report: ConvertReportJson {
                        total_packages: 0,
                        total_procedures: 0,
                        total_dml: 0,
                        total_cross_calls: 0,
                        stub_count: 0,
                        mappings: Vec::new(),
                        errors: vec![format!(
                            "Validation failed: {} file(s) have syntax errors",
                            validate_result.error_file_count
                        )],
                        unresolved_calls: Vec::new(),
                    },
                    report_paths: Vec::new(),
                    log_path: String::new(),
                    summary: ConvertSummaryJson {
                        packages: 0,
                        procedures: 0,
                        dml_statements: 0,
                        stubs: 0,
                        unresolved_calls: 0,
                        validation_passed: false,
                    },
                    error: Some(format!(
                        "Validation failed: {} file(s) have syntax errors",
                        validate_result.error_file_count
                    )),
                };

                return Ok(rmcp::handler::server::wrapper::Json(response));
            }
        }

        let mut incremental = IncrementalState::new(&output_dir, req.full);
        if let Err(e) = incremental.initialize() {
            return Err(McpError::internal_error(
                format!("Failed to initialize incremental state: {}", e),
                None,
            ));
        }

        let result =
            pipeline::run_pipeline(&sql_files, &config, &mut incremental, req.debug);

        let total_packages = result.packages.len();
        let total_procedures: usize = result.packages.iter().map(|p| p.procedures.len()).sum();

        let report = report::build_report(
            &result.packages,
            result.skipped,
            result.warnings,
            result.unresolved_calls.clone(),
            result.stub_count,
            "MCP",
            &output_dir,
            sql_files.len(),
        );
        let report_paths = report.save_auto(std::path::Path::new(&output_dir));

        let log_dir = std::path::Path::new(&output_dir)
            .join(".fluxgauss")
            .join("logs");
        let log_path = log_dir.join("conversion.log").to_string_lossy().to_string();

        let mappings: Vec<ProcedureMappingJson> = report
            .mappings
            .iter()
            .map(|m| ProcedureMappingJson {
                sql_procedure: m.sql_procedure.clone(),
                sql_package: m.sql_package.clone(),
                java_service: m.java_service.clone(),
                java_method: m.java_method.clone(),
                is_stub: m.is_stub,
            })
            .collect();

        let error_strings: Vec<String> = result.errors.iter().map(|e| e.to_string()).collect();

        let response = ConvertSqlResponse {
            success: result.errors.is_empty(),
            output_dir: output_dir.clone(),
            generated_files: result.generated_files.clone(),
            report: ConvertReportJson {
                total_packages,
                total_procedures,
                total_dml: result.total_dml,
                total_cross_calls: result.total_cross_calls,
                stub_count: result.stub_count,
                mappings,
                errors: error_strings,
                unresolved_calls: result.unresolved_calls.iter().map(|c| c.callee.clone()).collect(),
            },
            report_paths,
            log_path,
            summary: ConvertSummaryJson {
                packages: total_packages,
                procedures: total_procedures,
                dml_statements: result.total_dml,
                stubs: result.stub_count,
                unresolved_calls: report.unresolved_calls.len(),
                validation_passed: true,
            },
            error: if result.errors.is_empty() {
                None
            } else {
                Some(format!(
                    "{} conversion error(s) occurred",
                    result.errors.len()
                ))
            },
        };

        Ok(rmcp::handler::server::wrapper::Json(response))
    }
}

#[tool_handler]
impl ServerHandler for FluxGaussMcpServer {
    fn get_info(&self) -> InitializeResult {
        let capabilities = ServerCapabilities::builder().enable_tools().build();
        let server_info = Implementation::new("fluxgauss", env!("CARGO_PKG_VERSION"))
            .with_description("PL/pgSQL → Spring Boot/MyBatis Java converter (Rust engine)");
        InitializeResult::new(capabilities)
            .with_protocol_version(ProtocolVersion::V_2025_06_18)
            .with_server_info(server_info)
    }
}

pub async fn run_mcp_server() -> anyhow::Result<()> {
    // MUST NOT write to stdout or stderr — MCP protocol uses stdout for JSON-RPC,
    // and clients may capture stderr as error indicators.
    let server = FluxGaussMcpServer;
    let transport = transport::io::stdio();
    let server_instance = server.serve(transport).await?;
    server_instance.waiting().await?;
    Ok(())
}

fn validate_error_to_json(err: &ogsql_parser::ParserError) -> ValidateErrorJson {
    match err {
        ogsql_parser::ParserError::UnexpectedToken {
            location,
            expected,
            got,
        } => ValidateErrorJson {
            line: location.line as usize,
            column: location.column as usize,
            message: format!("Expected {}, got {}", expected, got),
        },
        ogsql_parser::ParserError::UnexpectedEof { expected, location } => ValidateErrorJson {
            line: location.line as usize,
            column: location.column as usize,
            message: format!("Unexpected end of input, expected {}", expected),
        },
        ogsql_parser::ParserError::ReservedKeywordAsIdentifier {
            keyword,
            location,
        } => ValidateErrorJson {
            line: location.line as usize,
            column: location.column as usize,
            message: format!(
                "Reserved keyword '{}' cannot be used as identifier",
                keyword
            ),
        },
        ogsql_parser::ParserError::UnsupportedSyntax {
            location,
            syntax,
            hint,
        } => ValidateErrorJson {
            line: location.line as usize,
            column: location.column as usize,
            message: format!("{} ({})", syntax, hint),
        },
        ogsql_parser::ParserError::Warning {
            message,
            location: _,
            level: _,
        } => ValidateErrorJson {
            line: 0,
            column: 0,
            message: format!("Warning: {}", message),
        },
        ogsql_parser::ParserError::TokenizerError(e) => ValidateErrorJson {
            line: 0,
            column: 0,
            message: format!("Tokenizer error: {}", e),
        },
    }
}

fn resolve_convert_inputs(
    req: &ConvertSqlRequest,
) -> Result<(crate::config::AppConfig, Vec<PathBuf>, String), String> {
    if let Some(config_value) = &req.config {
        let config: crate::config::AppConfig =
            serde_json::from_value(config_value.clone())
                .map_err(|e| format!("Failed to parse config JSON: {}", e))?;
        let output_dir = config.output_dir_or_default();
        let sql_files: Vec<PathBuf> = config
            .sources
            .as_ref()
            .map(|s| s.iter().map(PathBuf::from).collect())
            .unwrap_or_default();
        return Ok((config, sql_files, output_dir));
    }

    let output_dir = req
        .output_dir
        .clone()
        .unwrap_or_else(|| "./dest".to_string());
    let sql_files: Vec<PathBuf> = match &req.files {
        Some(files) => files.iter().map(PathBuf::from).collect(),
        None => {
            return Err(
                "Either 'config' or 'files' must be provided for convert_sql".to_string(),
            );
        }
    };

    let mut config = crate::config::AppConfig::default();
    config.sources = req.files.clone();
    config.output_dir = Some(output_dir.clone());

    Ok((config, sql_files, output_dir))
}

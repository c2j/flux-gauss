# MCP Server Integration — FluxGauss Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add MCP (Model Context Protocol) stdio server support to both Python and Rust engines, exposing `validate_sql` and `convert_sql` tools for AI client integration.

**Architecture:** Two independent MCP servers (Python + Rust), each wrapping the existing engine's public API. Both expose identical tool signatures via stdio transport. The `convert_sql` tool performs validation first and fails fast on errors, matching the user's "先 validate 再 convert" requirement. Output remains file-system-based with a structured `ConversionReport` returned as JSON.

**Tech Stack:**
- **Python MCP**: `mcp[cli]` (FastMCP framework, PyPI), wraps `converter/flux_gauss.py`
- **Rust MCP**: `rmcp` v1.7+ (official Rust SDK, crates.io), new binary crate `crates/fluxgauss-mcp/`
- **Transport**: stdio (both engines)
- **Serialization**: JSON (MCP protocol native)

**User's Confirmed Constraints:**
1. stdio transport only (no HTTP/SSE needed)
2. Workflow: `validate_sql` → check errors → `convert_sql` (fail fast on validation errors)
3. Output: filesystem + structured report (existing behavior, JSON-ified for MCP)
4. Both Python and Rust engines MUST be supported

---

## Shared MCP Tool Definitions

Both engines expose these identical tools:

### Tool 1: `validate_sql`

```json
{
  "name": "validate_sql",
  "description": "Validate SQL stored procedure files for syntax errors, package consistency, and undefined variables. Call this BEFORE convert_sql. If errors are found, fix the SQL files before converting.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "files": {
        "type": "array",
        "items": { "type": "string" },
        "description": "List of absolute or relative paths to SQL files to validate"
      },
      "encoding": {
        "type": "string",
        "description": "Source file encoding (default: utf-8). Supported: utf-8, gbk, gb2312, big5"
      }
    },
    "required": ["files"]
  }
}
```

**Returns:**
```json
{
  "valid": true,
  "error_file_count": 0,
  "warning_file_count": 0,
  "file_results": [
    {
      "file": "pkg_order.sql",
      "errors": [{"line": 42, "column": 10, "message": "unexpected token ..."}],
      "warnings": [],
      "package_consistency_errors": [],
      "undefined_variables": []
    }
  ]
}
```

### Tool 2: `convert_sql`

```json
{
  "name": "convert_sql",
  "description": "Convert SQL stored procedures (PL/pgSQL) into a Spring Boot + MyBatis Java project. Always run validate_sql first. This tool WILL abort if validation errors are detected — use skip_validation=true to force conversion despite errors.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "config": {
        "type": "object",
        "description": "YAML configuration as a JSON object. Same schema as fluxgauss.yaml. If provided, files and output_dir are read from config.",
        "properties": {
          "output_dir": { "type": "string", "description": "Output directory (default: ./dest)" },
          "base_package": { "type": "string", "description": "Java base package (default: com.example.demo)" },
          "encoding": { "type": "string", "description": "Source encoding (default: utf-8)" },
          "sources": { "type": "array", "items": { "type": "string" }, "description": "SQL file paths" },
          "logger": { "type": "string", "description": "Logger framework: slf4j, log4j2, commons-logging, jul" },
          "java_packages": { "type": "array", "description": "Custom package mappings" },
          "type_aliases": { "type": "object", "description": "Custom type mappings" },
          "integration_test": { "type": "object", "description": "Integration test config" },
          "database": { "type": "object", "description": "Database connection for application.yml" }
        }
      },
      "files": {
        "type": "array",
        "items": { "type": "string" },
        "description": "List of SQL file paths (required if config.sources not provided)"
      },
      "output_dir": {
        "type": "string",
        "description": "Output directory (required if config.output_dir not provided)"
      },
      "base_package": {
        "type": "string",
        "description": "Java base package (optional, overrides config)"
      },
      "full": {
        "type": "boolean",
        "description": "Force full regeneration, ignore incremental cache (default: false)"
      },
      "debug": {
        "type": "boolean",
        "description": "Inject SQL source line annotations into generated code (default: false)"
      },
      "skip_validation": {
        "type": "boolean",
        "description": "Skip the validation phase and convert directly. WARNING: may produce incorrect output if SQL has errors. (default: false)"
      }
    }
  }
}
```

**Returns:**
```json
{
  "success": true,
  "output_dir": "/absolute/path/to/dest",
  "generated_files": ["OrderService.java", "OrderMapper.java", "..."],
  "report": {
    "total_packages": 2,
    "total_procedures": 10,
    "total_dml": 15,
    "total_cross_calls": 3,
    "stub_count": 1,
    "mappings": [
      {
        "sql_procedure": "create_order",
        "sql_package": "pkg_order",
        "java_service": "OrderService",
        "java_method": "createOrder",
        "is_stub": false
      }
    ],
    "errors": [],
    "unresolved_calls": []
  },
  "report_paths": ["dest/.fluxgauss/reports/conversion-report-latest.md"],
  "log_path": "dest/.fluxgauss/logs/conversion-latest.log",
  "summary": {
    "packages": 2,
    "procedures": 10,
    "dml_statements": 15,
    "stubs": 1,
    "unresolved_calls": 0,
    "validation_passed": true
  }
}
```

---

## Part A: Python MCP Server

### Task A1: Create Python MCP Server Entry Point

**Files:**
- Create: `converter/fluxgauss_mcp.py`
- Modify: none

**Step 1: Write the minimal MCP server with tool stubs**

```python
#!/usr/bin/env python3
"""
FluxGauss MCP Server — Python Engine
Provides validate_sql and convert_sql tools via MCP stdio protocol.

Usage:
  python3 converter/fluxgauss_mcp.py

MCP Client Configuration (e.g., Claude Desktop):
  {
    "mcpServers": {
      "fluxgauss": {
        "command": "python3",
        "args": ["converter/fluxgauss_mcp.py"],
        "env": { "OGSQL_BIN": "/path/to/ogsql" }
      }
    }
  }
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
import json
import os
import sys
import traceback
from pathlib import Path

# Ensure converter/ is on sys.path for import
_converter_dir = Path(__file__).resolve().parent
if str(_converter_dir) not in sys.path:
    sys.path.insert(0, str(_converter_dir))

mcp = FastMCP("FluxGauss")

# --- Tool: validate_sql ---

@mcp.tool()
def validate_sql(files: list[str], encoding: str = "utf-8") -> dict:
    """Validate SQL stored procedure files for syntax errors, package
    consistency, and undefined variables. Call this BEFORE convert_sql.
    If errors are found, fix the SQL files before converting.

    Args:
        files: List of absolute or relative paths to SQL files to validate.
        encoding: Source file encoding (default: utf-8).
                  Supported: utf-8, gbk, gb2312, big5.
    """
    # Implementation in Step 2 — stub returns for now
    return {"valid": True, "error_file_count": 0, "warning_file_count": 0, "file_results": []}


# --- Tool: convert_sql ---

@mcp.tool()
def convert_sql(
    config: dict = None,
    files: list[str] = None,
    output_dir: str = None,
    base_package: str = None,
    full: bool = False,
    debug: bool = False,
    skip_validation: bool = False,
) -> dict:
    """Convert SQL stored procedures (PL/pgSQL) into a Spring Boot +
    MyBatis Java project. Always run validate_sql first. This tool
    WILL abort if validation errors are detected — use
    skip_validation=true to force conversion despite errors.

    Args:
        config: YAML configuration as JSON object (same as fluxgauss.yaml).
                If provided, files/output_dir read from config.
        files: SQL file paths (required if config.sources absent).
        output_dir: Output directory (required if config.output_dir absent).
        base_package: Java base package (overrides config if set).
        full: Force full regeneration, ignore cache (default: false).
        debug: Inject SQL source line annotations (default: false).
        skip_validation: Skip validation phase. WARNING: may produce
                         incorrect output if SQL has errors.
    """
    # Implementation in Step 4 — stub returns for now
    return {"success": False, "error": "Not yet implemented", "report": None}


# --- Entry Point ---

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Step 2: Implement `validate_sql`**

The Python engine has `validate_sql_files()` in `flux_gauss.py`. Import and wrap it:

```python
@mcp.tool()
def validate_sql(files: list[str], encoding: str = "utf-8") -> dict:
    """..."""
    import flux_gauss

    # Resolve all file paths
    resolved = []
    for f in files:
        p = Path(f)
        if not p.exists():
            raise ToolError(f"File not found: {f}")
        resolved.append(str(p.resolve()))

    try:
        result = flux_gauss.validate_sql_files(resolved)
    except Exception as e:
        raise ToolError(f"Validation failed: {e}") from e

    # Transform to MCP result format
    file_results = []
    for path in resolved:
        basename = Path(path).name
        errs = result.get(path, {})
        errors = []
        warnings = []
        for err_item in errs.get("errors", []):
            errors.append({
                "line": err_item.get("line", 0),
                "column": err_item.get("column", 0),
                "message": err_item.get("message", str(err_item)),
            })
        for warn_item in errs.get("warnings", []):
            warnings.append({
                "line": warn_item.get("line", 0),
                "column": warn_item.get("column", 0),
                "message": warn_item.get("message", str(warn_item)),
            })
        file_results.append({
            "file": basename,
            "path": path,
            "errors": errors,
            "warnings": warnings,
        })

    error_file_count = sum(1 for fr in file_results if fr["errors"])
    warning_file_count = sum(1 for fr in file_results if fr["warnings"] and not fr["errors"])

    return {
        "valid": error_file_count == 0,
        "error_file_count": error_file_count,
        "warning_file_count": warning_file_count,
        "file_results": file_results,
    }
```

**Step 3: Write unit tests for `validate_sql` MCP tool**

```python
# File: tests/test_mcp_validate.py
import pytest
import tempfile
import os
from pathlib import Path

# ... test with valid SQL, syntax error SQL, missing file, encoding
```

**Step 4: Implement `convert_sql`**

The key logic: validate first, then convert. Wraps `flux_gauss.main()` flow:

```python
@mcp.tool()
def convert_sql(
    config: dict = None,
    files: list[str] = None,
    output_dir: str = None,
    base_package: str = None,
    full: bool = False,
    debug: bool = False,
    skip_validation: bool = False,
) -> dict:
    """..."""
    import flux_gauss

    # ---- Resolve inputs ----
    if config:
        sources = config.get("sources", [])
        output_dir = output_dir or config.get("output_dir", "./dest")
        base_package = base_package or config.get("base_package", "com.example.demo")
        encoding = config.get("encoding", "utf-8")
    else:
        sources = files or []
        output_dir = output_dir or "./dest"
        base_package = base_package or "com.example.demo"
        encoding = "utf-8"

    if not sources:
        raise ToolError("No SQL source files provided. Use 'files' or 'config.sources'.")

    # Resolve paths
    resolved_sources = []
    for f in sources:
        p = Path(f)
        if not p.exists():
            raise ToolError(f"Source file not found: {f}")
        resolved_sources.append(str(p.resolve()))

    # ---- Phase 0: Validate ----
    if not skip_validation:
        validation = validate_sql(resolved_sources, encoding)
        if not validation["valid"]:
            error_details = "\n".join(
                f"  {fr['file']}: {len(fr['errors'])} error(s)"
                for fr in validation["file_results"]
                if fr["errors"]
            )
            raise ToolError(
                f"Validation failed with {validation['error_file_count']} file(s) "
                f"having errors:\n{error_details}\n\n"
                f"Fix the SQL files and try again, or use skip_validation=true to force conversion."
            )

    # ---- Build internal config dict (same shape as YAML) ----
    internal_config = {
        "output_dir": output_dir,
        "base_package": base_package,
        "encoding": encoding,
        "sources": resolved_sources,
    }
    if config:
        # Merge additional config fields
        for key in ("logger", "database", "java_packages", "java_package",
                     "type_aliases", "integration_test"):
            if key in config:
                internal_config[key] = config[key]

    # ---- Run conversion ----
    try:
        # Use the config-mode path: _parse_config-like then generate_project
        # Since main() is monolithic, we replicate its key phases:

        # Phase 0: Table DDL pre-scan
        flux_gauss._init_log(output_dir)
        flux_gauss._log(f"[INFO] MCP conversion started — {len(resolved_sources)} file(s)")

        # Scan for CREATE TABLE DDL
        for src in resolved_sources:
            try:
                table_schema = flux_gauss.parse_table_ddl(src)
                if table_schema:
                    for tbl, cols in table_schema.items():
                        tbl_lower = tbl.lower()
                        if tbl_lower not in flux_gauss.TYPE_OVERRIDES:
                            flux_gauss.TYPE_OVERRIDES[tbl_lower] = cols
            except Exception:
                pass  # Non-DDL files are expected to fail here

        # Phase 1: Parse
        flux_gauss._log(f"[INFO] Phase 1: Parsing SQL files")
        ast_results = flux_gauss.parse_sql_files(resolved_sources)
        parse_errors_map = {}

        # Phase 2: Extract + Analyze
        all_packages = {}
        all_skipped = []
        base_pkg = base_package

        for src_path in resolved_sources:
            ast = ast_results.get(src_path, {})
            if not ast:
                continue
            try:
                procs, pkg_vars, custom_types = flux_gauss.extract_procedures(ast, src_path)
                comments = flux_gauss.extract_comments(ast, src_path)
            except Exception as e:
                parse_errors_map[src_path] = {"errors": [{"message": str(e)}]}
                continue

            pkg_name = flux_gauss._infer_package_name(src_path, procs)
            java_pkg = base_pkg
            # Apply java_packages mapping if present
            if "java_packages" in internal_config:
                for jp in internal_config["java_packages"]:
                    if src_path in jp.get("sources", []):
                        java_pkg = jp["package"]
                        break

            pkg_info = flux_gauss.PackageInfo(
                package_name=pkg_name,
                source_file=src_path,
                java_package=java_pkg,
                procedures=procs,
                package_vars=pkg_vars,
                custom_types=custom_types,
                comments=comments,
            )
            all_packages[pkg_name] = pkg_info

        # Phase 3: Analyze all procedures
        for pkg in all_packages.values():
            for proc in pkg.procedures:
                try:
                    flux_gauss.analyze_procedure(proc, all_packages)
                except Exception as e:
                    flux_gauss._log(f"    ❌ Error analyzing {proc.name}: {e}", to_stdout=False)

        # Phase 4: Generate project
        pkg_list = list(all_packages.values())
        changed = {p.package_name for p in pkg_list} if full else None
        flux_gauss.generate_project(
            output_dir, pkg_list,
            changed_packages=changed,
            config=internal_config,
        )

        # Phase 5: Build report
        report = flux_gauss.build_conversion_report(
            output_dir, pkg_list, all_skipped,
            parse_errors_map, config_path="MCP mode",
        )
        report_paths = flux_gauss.write_conversion_report(report, output_dir)

        # Collect stats
        total_procs = sum(len(p.procedures) for p in pkg_list)
        total_dml = sum(
            len(proc.dml_statements) for p in pkg_list for proc in p.procedures
        )
        stub_count = len(flux_gauss.STUB_PROCEDURES)

        # Build mappings
        mappings = []
        for pkg in pkg_list:
            class_name = flux_gauss.package_to_classname(pkg.package_name)
            for proc in pkg.procedures:
                mappings.append({
                    "sql_procedure": proc.name,
                    "sql_package": pkg.package_name,
                    "java_service": f"{class_name}Service",
                    "java_method": flux_gauss.java_method_name(proc.name),
                    "is_stub": proc.is_stub if hasattr(proc, 'is_stub') else False,
                })

        flux_gauss._close_log(output_dir)

        return {
            "success": True,
            "output_dir": str(Path(output_dir).resolve()),
            "generated_files": [],  # would need to collect from generate_project
            "report": {
                "total_packages": len(pkg_list),
                "total_procedures": total_procs,
                "total_dml": total_dml,
                "total_cross_calls": len(flux_gauss.UNRESOLVED_CALLS),
                "stub_count": stub_count,
                "mappings": mappings,
                "errors": parse_errors_map,
                "unresolved_calls": flux_gauss.UNRESOLVED_CALLS,
            },
            "report_paths": report_paths,
            "log_path": str(Path(output_dir) / ".fluxgauss" / "logs" / "conversion-latest.log"),
            "summary": {
                "packages": len(pkg_list),
                "procedures": total_procs,
                "dml_statements": total_dml,
                "stubs": stub_count,
                "unresolved_calls": len(flux_gauss.UNRESOLVED_CALLS),
                "validation_passed": True,
            },
        }

    except ToolError:
        raise
    except Exception as e:
        flux_gauss._log(f"❌ Conversion failed: {e}", to_stdout=False)
        flux_gauss._log(traceback.format_exc(), to_stdout=False)
        raise ToolError(f"Conversion failed: {e}") from e
```

**Step 5: Write integration test for convert_sql MCP tool**

```python
# File: tests/test_mcp_convert.py
import pytest
import tempfile
import os
from pathlib import Path

# ... test with valid config, missing files, validation failure, skip_validation
```

**Step 6: Add `mcp` dependency documentation**

No `requirements.txt` exists. Document in README:
```markdown
### MCP Server (Python)

```bash
pip install mcp
python3 converter/fluxgauss_mcp.py
```
```

**Step 7: Test with MCP Inspector**

```bash
pip install mcp[cli]
mcp dev converter/fluxgauss_mcp.py
# Opens MCP Inspector UI at http://localhost:5173
# Test: validate_sql with demo-project/sql/pkg_order.sql
# Test: convert_sql with same file
```

**Step 8: Commit**

```bash
git add converter/fluxgauss_mcp.py tests/test_mcp_validate.py tests/test_mcp_convert.py
git commit -m "feat(python): add MCP server with validate_sql and convert_sql tools"
```

---

## Part B: Rust MCP Server

### Task B1: Create Rust MCP Crate Skeleton

**Files:**
- Create: `crates/fluxgauss-mcp/Cargo.toml`
- Create: `crates/fluxgauss-mcp/src/main.rs`
- Modify: `Cargo.toml` (workspace members)

**Step 1: Add workspace member**

```toml
# In /Cargo.toml, change:
members = ["crates/fluxgauss"]
# to:
members = ["crates/fluxgauss", "crates/fluxgauss-mcp"]
```

**Step 2: Create crate Cargo.toml**

```toml
# crates/fluxgauss-mcp/Cargo.toml
[package]
name = "fluxgauss-mcp"
version = "0.1.0"
edition = "2021"
description = "FluxGauss MCP Server — Rust Engine"

[[bin]]
name = "fluxgauss-mcp"
path = "src/main.rs"

[dependencies]
fluxgauss = { path = "../fluxgauss" }
rmcp = { version = "1.7", features = ["server", "macros", "transport-io"] }
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
anyhow = "1"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
```

**Step 3: Write minimal MCP server with tool stubs**

```rust
// crates/fluxgauss-mcp/src/main.rs
use rmcp::{
    handler::server::ServerHandler,
    model::{ProtocolVersion, ServerCapabilities, ServerInfo},
    tool, tool_handler, tool_router, Error as McpError,
    transport::stdio,
};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

// ── Tool Input/Output Types ──

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ValidateSqlRequest {
    files: Vec<String>,
    #[serde(default = "default_encoding")]
    encoding: String,
}

fn default_encoding() -> String { "utf-8".into() }

#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileValidateResultJson {
    file: String,
    path: String,
    errors: Vec<ValidateErrorJson>,
    warnings: Vec<ValidateErrorJson>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ValidateErrorJson {
    line: usize,
    column: usize,
    message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ValidateSqlResponse {
    valid: bool,
    error_file_count: usize,
    warning_file_count: usize,
    file_results: Vec<FileValidateResultJson>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ConvertSqlRequest {
    #[serde(default)]
    config: Option<serde_json::Value>,
    #[serde(default)]
    files: Option<Vec<String>>,
    #[serde(default)]
    output_dir: Option<String>,
    #[serde(default)]
    base_package: Option<String>,
    #[serde(default)]
    full: bool,
    #[serde(default)]
    debug: bool,
    #[serde(default)]
    skip_validation: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ProcedureMappingJson {
    sql_procedure: String,
    sql_package: String,
    java_service: String,
    java_method: String,
    is_stub: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ConvertReportJson {
    total_packages: usize,
    total_procedures: usize,
    total_dml: usize,
    total_cross_calls: usize,
    stub_count: usize,
    mappings: Vec<ProcedureMappingJson>,
    errors: Vec<String>,
    unresolved_calls: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ConvertSummaryJson {
    packages: usize,
    procedures: usize,
    dml_statements: usize,
    stubs: usize,
    unresolved_calls: usize,
    validation_passed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ConvertSqlResponse {
    success: bool,
    output_dir: String,
    generated_files: Vec<String>,
    report: ConvertReportJson,
    report_paths: Vec<String>,
    log_path: String,
    summary: ConvertSummaryJson,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

// ── MCP Server ──

#[derive(Clone)]
pub struct FluxGaussMcpServer {
    tool_router: rmcp::handler::server::tool::ToolRouter<Self>,
}

#[tool_router]
impl FluxGaussMcpServer {
    pub fn new() -> Self {
        Self { tool_router: Self::tool_router() }
    }

    #[tool(description = "Validate SQL stored procedure files for syntax errors, package consistency, and undefined variables. Call this BEFORE convert_sql.")]
    async fn validate_sql(
        &self,
        params: rmcp::handler::server::wrapper::Parameters<ValidateSqlRequest>,
    ) -> Result<rmcp::handler::server::wrapper::Json<ValidateSqlResponse>, McpError> {
        // Implementation in Step 4
        Ok(rmcp::handler::server::wrapper::Json(ValidateSqlResponse {
            valid: true,
            error_file_count: 0,
            warning_file_count: 0,
            file_results: vec![],
        }))
    }

    #[tool(description = "Convert SQL stored procedures (PL/pgSQL) into a Spring Boot + MyBatis Java project. Always run validate_sql first.")]
    async fn convert_sql(
        &self,
        params: rmcp::handler::server::wrapper::Parameters<ConvertSqlRequest>,
    ) -> Result<rmcp::handler::server::wrapper::Json<ConvertSqlResponse>, McpError> {
        // Implementation in Step 5
        Ok(rmcp::handler::server::wrapper::Json(ConvertSqlResponse {
            success: false,
            output_dir: String::new(),
            generated_files: vec![],
            report: ConvertReportJson {
                total_packages: 0, total_procedures: 0,
                total_dml: 0, total_cross_calls: 0, stub_count: 0,
                mappings: vec![], errors: vec![], unresolved_calls: vec![],
            },
            report_paths: vec![],
            log_path: String::new(),
            summary: ConvertSummaryJson {
                packages: 0, procedures: 0, dml_statements: 0,
                stubs: 0, unresolved_calls: 0, validation_passed: false,
            },
            error: Some("Not yet implemented".into()),
        }))
    }
}

#[tool_handler]
impl ServerHandler for FluxGaussMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            protocol_version: ProtocolVersion::V_2025_06_18,
            name: "fluxgauss".into(),
            version: env!("CARGO_PKG_VERSION").into(),
            description: Some("PL/pgSQL → Spring Boot/MyBatis Java converter (Rust engine)".into()),
            capabilities: ServerCapabilities::builder().enable_tools().build(),
            ..Default::default()
        }
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter("info")
        .init();

    tracing::info!("FluxGauss MCP server (Rust) starting on stdio");
    let server = FluxGaussMcpServer::new();
    let transport = stdio::stdio();
    let server_instance = server.serve(transport).await?;
    let quit_reason = server_instance.waiting().await?;
    tracing::info!("Server shutdown: {:?}", quit_reason);
    Ok(())
}
```

### Task B2: Implement `validate_sql` Tool (Rust)

**Files:**
- Modify: `crates/fluxgauss-mcp/src/main.rs`

**Step 1: Implement** (replace stub in `validate_sql` method)

```rust
#[tool(description = "...")]
async fn validate_sql(
    &self,
    params: rmcp::handler::server::wrapper::Parameters<ValidateSqlRequest>,
) -> Result<rmcp::handler::server::wrapper::Json<ValidateSqlResponse>, McpError> {
    let req = params.0;

    // Resolve file paths
    let mut paths = Vec::new();
    for f in &req.files {
        let p = std::path::PathBuf::from(f);
        if !p.exists() {
            return Err(McpError::from(anyhow::anyhow!("File not found: {}", f)));
        }
        // Canonicalize if possible
        paths.push(p.canonicalize().unwrap_or(p));
    }

    // Call the existing phase0_validate function
    let result = fluxgauss::pipeline::phase0_validate(&paths);

    // Transform to MCP result format
    let file_results: Vec<FileValidateResultJson> = result.file_results.iter().map(|fr| {
        FileValidateResultJson {
            file: fr.basename.clone(),
            path: String::new(), // paths are already known by caller
            errors: fr.errors.iter().map(|e| {
                ValidateErrorJson {
                    line: 0,  // ParserError may not carry line info for all variants
                    column: 0,
                    message: format_parser_error(e),
                }
            }).collect(),
            warnings: fr.warnings.iter().map(|w| {
                ValidateErrorJson {
                    line: 0, column: 0,
                    message: format_parser_error(w),
                }
            }).collect(),
        }
    }).collect();

    Ok(rmcp::handler::server::wrapper::Json(ValidateSqlResponse {
        valid: !result.has_errors(),
        error_file_count: result.error_file_count,
        warning_file_count: result.warning_file_count,
        file_results,
    }))
}

fn format_parser_error(err: &ogsql_parser::ParserError) -> String {
    match err {
        ogsql_parser::ParserError::UnexpectedToken { location, expected, got } => {
            format!("line {}, col {}: expected {}, got {}",
                    location.line, location.column, expected, got)
        }
        ogsql_parser::ParserError::UnexpectedEof { expected, location } => {
            format!("line {}, col {}: unexpected EOF, expected {}",
                    location.line, location.column, expected)
        }
        ogsql_parser::ParserError::Warning { message, .. } => message.clone(),
        other => format!("{:?}", other),
    }
}
```

### Task B3: Implement `convert_sql` Tool (Rust)

**Files:**
- Modify: `crates/fluxgauss-mcp/src/main.rs`

**Step 1: Implement** (replace stub in `convert_sql` method)

Key flow:
1. Resolve inputs (config or direct files)
2. If `skip_validation` is false, call `phase0_validate` — error out if `has_errors()`
3. Build `AppConfig` from input JSON
4. Call `pipeline::run_pipeline()`
5. Build and save `ConversionReport`
6. Return structured JSON

```rust
#[tool(description = "...")]
async fn convert_sql(
    &self,
    params: rmcp::handler::server::wrapper::Parameters<ConvertSqlRequest>,
) -> Result<rmcp::handler::server::wrapper::Json<ConvertSqlResponse>, McpError> {
    let req = params.0;

    // ── Resolve inputs ──
    let (sources, output_dir, base_package, encoding, maybe_config) =
        resolve_convert_inputs(&req)?;

    // ── Validate (unless skipped) ──
    if !req.skip_validation {
        let validation = fluxgauss::pipeline::phase0_validate(&sources);
        if validation.has_errors() {
            let error_details: Vec<String> = validation.file_results.iter()
                .filter(|fr| !fr.errors.is_empty())
                .map(|fr| format!("  {}: {} error(s)", fr.basename, fr.errors.len()))
                .collect();
            return Err(McpError::from(anyhow::anyhow!(
                "Validation failed with {} file(s) having errors:\n{}\n\n\
                 Fix the SQL files and try again, or use skip_validation=true.",
                validation.error_file_count,
                error_details.join("\n"),
            )));
        }
    }

    // ── Build AppConfig ──
    let config = maybe_config.unwrap_or_else(|| {
        // Build from direct parameters
        fluxgauss::config::AppConfig {
            output_dir: Some(output_dir.clone()),
            base_package: Some(base_package.clone()),
            encoding: Some(encoding.clone()),
            sources: Some(sources.iter().map(|p| p.to_string_lossy().into_owned()).collect()),
            ..Default::default()
        }
    });

    // ── Run pipeline ──
    let mut incremental = fluxgauss::incremental::IncrementalState::new(
        std::path::Path::new(&output_dir), req.full
    );
    if let Err(e) = incremental.initialize() {
        return Err(McpError::from(anyhow::anyhow!("Initialization failed: {}", e)));
    }

    let result = fluxgauss::pipeline::run_pipeline(
        &sources, &config, &mut incremental, req.debug
    );

    // ── Build report ──
    let report = fluxgauss::report::build_report(
        &result.packages, result.skipped.clone(),
        result.unresolved_calls.clone(), result.stub_count,
        "MCP mode", &output_dir, sources.len(),
    );
    let report_paths = report.save_auto(std::path::Path::new(&output_dir));

    // ── Build mappings ──
    let mappings: Vec<ProcedureMappingJson> = result.packages.iter()
        .flat_map(|pkg| {
            let class_name = fluxgauss::naming::package_to_classname(&pkg.package_name);
            pkg.procedures.iter().map(move |proc| ProcedureMappingJson {
                sql_procedure: proc.name.clone(),
                sql_package: pkg.package_name.clone(),
                java_service: format!("{}Service", class_name),
                java_method: fluxgauss::naming::java_method_name(&proc.name),
                is_stub: proc.is_stub(),
            })
        })
        .collect();

    let total_procedures: usize = result.packages.iter().map(|p| p.procedures.len()).sum();

    Ok(rmcp::handler::server::wrapper::Json(ConvertSqlResponse {
        success: true,
        output_dir: std::path::Path::new(&output_dir).canonicalize()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or(output_dir.clone()),
        generated_files: result.generated_files,
        report: ConvertReportJson {
            total_packages: result.packages.len(),
            total_procedures,
            total_dml: result.total_dml,
            total_cross_calls: result.total_cross_calls,
            stub_count: result.stub_count,
            mappings,
            errors: result.errors.iter().map(|e| format!("{:?}", e)).collect(),
            unresolved_calls: result.unresolved_calls,
        },
        report_paths,
        log_path: format!("{}/.fluxgauss/logs/conversion-latest.log", output_dir),
        summary: ConvertSummaryJson {
            packages: result.packages.len(),
            procedures: total_procedures,
            dml_statements: result.total_dml,
            stubs: result.stub_count,
            unresolved_calls: result.unresolved_calls.len(),
            validation_passed: !req.skip_validation,
        },
        error: None,
    }))
}

fn resolve_convert_inputs(
    req: &ConvertSqlRequest,
) -> Result<(Vec<PathBuf>, String, String, String, Option<fluxgauss::config::AppConfig>), McpError> {
    if let Some(config_val) = &req.config {
        // JSON config → AppConfig via serde
        let config: fluxgauss::config::AppConfig = serde_json::from_value(config_val.clone())
            .map_err(|e| McpError::from(anyhow::anyhow!("Invalid config: {}", e)))?;
        let sources: Vec<PathBuf> = config.sources.as_ref()
            .map(|s| s.iter().map(PathBuf::from).collect())
            .unwrap_or_default();
        let output_dir = config.output_dir_or_default();
        let base_package = config.base_package_or_default();
        let encoding = config.encoding_or_default();
        Ok((sources, output_dir, base_package, encoding, Some(config)))
    } else {
        let files = req.files.as_ref()
            .ok_or_else(|| McpError::from(anyhow::anyhow!("Missing 'files' or 'config'")))?
            .iter().map(PathBuf::from).collect();
        let output_dir = req.output_dir.clone().unwrap_or_else(|| "./dest".into());
        let base_package = req.base_package.clone().unwrap_or_else(|| "com.example.demo".into());
        let encoding = "utf-8".into();
        Ok((files, output_dir, base_package, encoding, None))
    }
}
```

### Task B4: Add Serialize Derives to Rust Types

**Files:**
- Modify: `crates/fluxgauss/src/types.rs` — add `#[derive(Serialize)]` to key types
- Modify: `crates/fluxgauss/Cargo.toml` — add `serde` dependency if not present

Some internal types (e.g., `ProcedureInfo`, `PackageInfo`) may not derive `Serialize`. Add derives as needed for the MCP response construction.

```toml
# crates/fluxgauss/Cargo.toml — ensure serde is available
[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"  # may already exist
```

### Task B5: Test Rust MCP Server

**Step 1: Build**

```bash
cargo build --release -p fluxgauss-mcp
```

**Step 2: Test with MCP Inspector**

```bash
# Install MCP Inspector (Node.js)
npx @modelcontextprotocol/inspector ./target/release/fluxgauss-mcp

# Test: validate_sql with demo-project/sql/pkg_order.sql
# Test: convert_sql with same file
```

**Step 3: Test with a real MCP client**

Configure Claude Desktop:
```json
{
  "mcpServers": {
    "fluxgauss-rust": {
      "command": "target/release/fluxgauss-mcp",
      "env": {}
    }
  }
}
```

### Task B6: Commit

```bash
git add Cargo.toml crates/fluxgauss-mcp/ crates/fluxgauss/src/types.rs
git commit -m "feat(rust): add MCP server binary with validate_sql and convert_sql tools"
```

---

## Part C: Integration & Documentation

### Task C1: Update README with MCP Section

**Files:**
- Modify: `README.md`

Add after the command-line options section:

```markdown
## MCP Server（Model Context Protocol）

FluxGauss 提供 Python 和 Rust 双引擎的 MCP 服务器，支持 AI 客户端（Claude Desktop、Cursor 等）直接调用存储过程转换功能。

### MCP 工具

| 工具 | 功能 |
|------|------|
| `validate_sql` | 验证 SQL 语法（建议在 `convert_sql` 之前调用） |
| `convert_sql` | 完整转换：验证 → 解析 → 分析 → 生成 Java 项目 |

### Python 引擎

```bash
pip install mcp
python3 converter/fluxgauss_mcp.py
```

### Rust 引擎

```bash
cargo build --release -p fluxgauss-mcp
./target/release/fluxgauss-mcp
```

### MCP 客户端配置

```json
{
  "mcpServers": {
    "fluxgauss": {
      "command": "python3",
      "args": ["converter/fluxgauss_mcp.py"],
      "env": {
        "OGSQL_BIN": "/usr/local/bin/ogsql"
      }
    }
  }
}
```

### 使用流程

1. **验证 SQL**：先调用 `validate_sql` 检查语法错误
2. **修复错误**：根据返回的错误信息修改 SQL 文件
3. **执行转换**：调用 `convert_sql` 生成 Java 项目

**⚠️ 重要**：如果 `validate_sql` 返回错误，`convert_sql` 将自动中止。
如需强制转换，使用 `skip_validation: true`。
```

### Task C2: Add MCP Configuration to PyInstaller Build (Optional)

**Files:**
- Modify: `.github/workflows/release.yml` (if pyinstaller packaging includes MCP)

If binary distribution should include MCP support, ensure `mcp` is bundled.

### Task C3: Final Verification

**Step 1: Python full conversion test**

```bash
# Start MCP server in dev mode
mcp dev converter/fluxgauss_mcp.py

# Via Inspector UI: call validate_sql with demo-project/sql/pkg_order.sql
# Expected: valid=true, 0 errors

# Via Inspector UI: call convert_sql with config from demo-project/fluxgauss_py.yaml
# Expected: success=true, generated files > 0
```

**Step 2: Rust full conversion test**

```bash
cargo build -p fluxgauss-mcp
npx @modelcontextprotocol/inspector ./target/debug/fluxgauss-mcp

# Via Inspector UI: same tests as Python
```

**Step 3: Error path test (validation failure)**

```bash
# Create a SQL file with a syntax error
echo "SELECTX * FROM nonexistent;" > /tmp/bad.sql

# Call validate_sql with /tmp/bad.sql → Expected: valid=false, errors > 0

# Call convert_sql with /tmp/bad.sql and skip_validation=false
# Expected: ToolError "Validation failed with 1 file(s) having errors"
```

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add MCP server usage guide for Python and Rust engines"
```

---

## Task Summary

| # | Task | Engine | Files | Estimated Time |
|---|------|--------|-------|---------------|
| A1 | Create Python MCP skeleton | Python | `converter/fluxgauss_mcp.py` | 30 min |
| A2 | Implement `validate_sql` tool | Python | `converter/fluxgauss_mcp.py` | 30 min |
| A3 | Test `validate_sql` | Python | `tests/test_mcp_validate.py` | 20 min |
| A4 | Implement `convert_sql` tool | Python | `converter/fluxgauss_mcp.py` | 1 hr |
| A5 | Test `convert_sql` | Python | `tests/test_mcp_convert.py` | 30 min |
| A6 | Document Python MCP | Python | `README.md` | 10 min |
| A7 | Verify with MCP Inspector | Python | — | 20 min |
| B1 | Create Rust MCP crate | Rust | `crates/fluxgauss-mcp/` | 20 min |
| B2 | Implement `validate_sql` | Rust | `crates/fluxgauss-mcp/src/main.rs` | 30 min |
| B3 | Implement `convert_sql` | Rust | `crates/fluxgauss-mcp/src/main.rs` | 1 hr |
| B4 | Add Serialize derives | Rust | `crates/fluxgauss/src/types.rs` | 20 min |
| B5 | Test Rust MCP | Rust | — | 20 min |
| C1 | Update README | Both | `README.md` | 15 min |
| C2 | Final verification | Both | — | 20 min |

**Total estimated: ~6 hours**

## Key Design Decisions

1. **`valid` field in validate result**: Boolean flag so LLM can easily check without counting errors.
2. **`skip_validation` flag in `convert_sql`**: Allows override when user explicitly wants to force conversion despite errors (matches existing `--skip-validate` CLI behavior).
3. **Config as JSON object**: LLMs can construct config inline without writing YAML files — lower friction.
4. **Both engines return identical JSON schemas**: LLM doesn't need to know which engine is running.
5. **`fluxgauss_mcp.py` imports `flux_gauss.py` directly**: No subprocess — direct function calls for better error propagation and performance.
6. **Rust crate separate from main binary**: Avoids bloating the CLI binary with MCP dependencies (tokio, rmcp).

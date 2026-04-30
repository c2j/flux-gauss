#!/usr/bin/env python3
"""
FluxGauss - OpenGauss/PostgreSQL stored procedure to Spring Boot + MyBatis converter.

Reads ogsql-parser JSON AST output and generates:
  - Java Service classes
  - MyBatis Mapper interfaces
  - MyBatis XML mapper files
  - Spring Boot project skeleton
"""

import json
import os
import re
import sys
import argparse
import subprocess
import tempfile
import traceback
import textwrap
import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

# ── Configuration ──────────────────────────────────────────────

def _resolve_ogsql_bin() -> str:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_dir = os.path.dirname(_script_dir)
    for candidate in [
        os.path.join(os.getcwd(), "ogsql"),
        os.environ.get("OGSQL_BIN", ""),
        shutil.which("ogsql") or "",
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "aarch64-apple-darwin", "release", "ogsql"),
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "release", "ogsql"),
    ]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "ogsql"


OGSQL_BIN = _resolve_ogsql_bin()
BASE_PACKAGE = "com.example.demo"
BASE_DIR = "src/main/java/" + BASE_PACKAGE.replace(".", "/")
RESOURCES_DIR = "src/main/resources"


def _pkg_java_package(pkg) -> str:
    return pkg.java_package or BASE_PACKAGE


def _pkg_base_dir(pkg) -> str:
    return "src/main/java/" + _pkg_java_package(pkg).replace(".", "/")

# ── Logger Configuration ──────────────────────────────────────────

LOGGER_PRESETS = {
    "slf4j": {
        "imports": [
            "import org.slf4j.Logger;",
            "import org.slf4j.LoggerFactory;",
        ],
        "declaration": "private static final Logger log = LoggerFactory.getLogger({class_name}.class);",
        "pom": [],
    },
    "log4j2": {
        "imports": [
            "import org.apache.logging.log4j.LogManager;",
            "import org.apache.logging.log4j.Logger;",
        ],
        "declaration": "private static final Logger log = LogManager.getLogger({class_name}.class);",
        "pom": [
            '<dependency>\n    <groupId>org.apache.logging.log4j</groupId>\n    <artifactId>log4j-core</artifactId>\n    <version>2.23.1</version>\n</dependency>',
            '<dependency>\n    <groupId>org.apache.logging.log4j</groupId>\n    <artifactId>log4j-slf4j2-impl</artifactId>\n    <version>2.23.1</version>\n</dependency>',
        ],
    },
    "commons-logging": {
        "imports": [
            "import org.apache.commons.logging.Log;",
            "import org.apache.commons.logging.LogFactory;",
        ],
        "declaration": "private static final Log log = LogFactory.getLog({class_name}.class);",
        "pom": [
            '<dependency>\n    <groupId>commons-logging</groupId>\n    <artifactId>commons-logging</artifactId>\n    <version>1.3.1</version>\n</dependency>',
        ],
    },
    "jul": {
        "imports": [
            "import java.util.logging.Logger;",
        ],
        "declaration": "private static final Logger log = Logger.getLogger({class_name}.class.getName());",
        "pom": [],
    },
}

# Current logger configuration (resolved from YAML config or defaults to slf4j)
_LOGGER_CONFIG = None  # resolved from YAML, defaults to slf4j


def _resolve_logger_config(config: dict) -> dict:
    """Resolve logger configuration from YAML config.

    Supported formats in fluxgauss.yaml:

    1. Preset (string):
        logger: log4j2

    2. Custom (dict):
        logger:
          imports:
            - "import com.mycompany.Logger;"
            - "import com.mycompany.LoggerFactory;"
          declaration: "private static final Logger log = LoggerFactory.create({class_name}.class);"
          pom:
            - '<dependency>...</dependency>'

    The 'declaration' field supports {class_name} placeholder.
    """
    raw = (config or {}).get("logger", "slf4j")

    if isinstance(raw, str):
        preset = LOGGER_PRESETS.get(raw)
        if preset is None:
            raise ValueError(
                f"Unknown logger preset '{raw}'. "
                f"Available presets: {', '.join(sorted(LOGGER_PRESETS.keys()))}. "
                f"Or use a dict for custom logger configuration."
            )
        return {
            "imports": list(preset["imports"]),
            "declaration": preset["declaration"],
            "pom": list(preset["pom"]),
        }

    if isinstance(raw, dict):
        imports = raw.get("imports")
        declaration = raw.get("declaration")
        if not imports or not declaration:
            raise ValueError(
                "Custom logger config requires 'imports' (list) and 'declaration' (string) fields. "
                "Example:\n"
                "  logger:\n"
                "    imports:\n"
                "      - \"import org.apache.logging.log4j.LogManager;\"\n"
                "      - \"import org.apache.logging.log4j.Logger;\"\n"
                "    declaration: \"private static final Logger log = LogManager.getLogger({class_name}.class);\""
            )
        return {
            "imports": list(imports),
            "declaration": declaration,
            "pom": list(raw.get("pom", [])),
        }

    raise ValueError(
        f"Invalid logger config: expected string or dict, got {type(raw).__name__}"
    )


def _get_logger_config() -> dict:
    global _LOGGER_CONFIG
    if _LOGGER_CONFIG is None:
        _LOGGER_CONFIG = _resolve_logger_config({})
    return _LOGGER_CONFIG

# ── Table DDL Parser ──────────────────────────────────────────

def parse_table_ddl(sql_file: str) -> dict:
    """Parse a SQL file containing CREATE TABLE statements and return a schema map.

    Returns: {table_name_lower: {column_name_lower: sql_type}}
    Example: {"db_log": {"id": "varchar2(20)", "proc_name": "varchar2(20)", ...}}
    """
    with open(sql_file, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    schema = {}
    table_pattern = re.compile(
        r'create\s+table\s+(?:if\s+not\s+exists\s+)?(?:\w+\.)?(\w+)\s*\((.*?)\)\s*;',
        re.IGNORECASE | re.DOTALL
    )

    for table_match in table_pattern.finditer(content):
        table_name = table_match.group(1).lower()
        columns_text = table_match.group(2)

        columns = {}
        parts = []
        depth = 0
        current = []
        for ch in columns_text:
            if ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())

        for part in parts:
            part = part.strip()
            if not part:
                continue

            tokens = part.split(None, 1)
            if len(tokens) < 2:
                part = re.sub(
                    r'^([a-zA-Z_][a-zA-Z0-9_]*)(varchar2|varchar|number|integer|int|char\b|date\b|timestamp|numeric|decimal|blob|clob|text\b|boolean|bigint|float|double|real|bytea|uuid|jsonb|json)',
                    r'\1 \2', part, flags=re.IGNORECASE
                )
                tokens = part.split(None, 1)
            if len(tokens) >= 2:
                col_name = tokens[0].strip().lower()
                col_type = tokens[1].strip()
                col_type = re.split(r'\s+(NOT\s+NULL|NULL|DEFAULT|PRIMARY|UNIQUE|CHECK|REFERENCES)', col_type, flags=re.IGNORECASE)[0].strip()
                columns[col_name] = col_type

        if columns:
            schema[table_name] = columns

    return schema


# ── Type Mapping ───────────────────────────────────────────────

SQL_TO_JAVA = {
    "bigint": "Long",
    "biginteger": "Long",
    "integer": "Integer",
    "int": "Integer",
    "int4": "Integer",
    "int8": "Long",
    "smallint": "Integer",
    "serial": "Integer",
    "bigserial": "Long",
    "number": "Long",
    "numeric": "java.math.BigDecimal",
    "decimal": "java.math.BigDecimal",
    "real": "Float",
    "float4": "Float",
    "float8": "Double",
    "double precision": "Double",
    "double": "Double",
    "varchar": "String",
    "varchar2": "String",
    "character varying": "String",
    "char": "String",
    "text": "String",
    "string": "String",
    "boolean": "Boolean",
    "bool": "Boolean",
    "timestamp": "java.sql.Timestamp",
    "timestamp without time zone": "java.sql.Timestamp",
    "timestamp with time zone": "java.sql.Timestamp",
    "date": "java.sql.Date",
    "time": "java.sql.Time",
    "bytea": "byte[]",
    "blob": "byte[]",
    "clob": "String",
    "json": "String",
    "jsonb": "String",
    "uuid": "String",
    "record": "Map<String, Object>",
    "exception": "String",
}


# User-configurable type overrides for %TYPE anchored declarations
# Format: (table_name_lower, column_name_lower) -> sql_type
TYPE_OVERRIDES = {
    # Example: ("db_log", "proc_name"): "varchar",
    # ("db_log", "log_level"): "varchar",
    # ("db_log", "step_no"): "integer",
}

UNRESOLVED_CALLS = []
STUB_PROCEDURES = []
UNSUPPORTED_FUNCTIONS = []
TODO_SUMMARY = []  # Collects (category, proc_id, source_file, detail) for diagnostic
_LOG_FH = None


def _init_log(output_dir: str):
    global _LOG_FH
    log_dir = _cache_base(output_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"conversion-{ts}.log"
    _LOG_FH = open(log_path, 'w', encoding='utf-8')
    _LOG_FH.write(f"FluxGauss Conversion Log\n")
    _LOG_FH.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    _LOG_FH.write(f"Output: {output_dir}\n\n")
    _LOG_FH.flush()
    return str(log_path)


def _log(msg: str, to_stdout: bool = True):
    global _LOG_FH
    if _LOG_FH:
        ts = datetime.now().strftime("%H:%M:%S")
        clean = re.sub(r'\x1b\[[0-9;]*m', '', msg)
        _LOG_FH.write(f"[{ts}] {clean}\n")
        _LOG_FH.flush()
    if to_stdout:
        print(msg)


def _close_log(output_dir: str):
    global _LOG_FH
    if _LOG_FH:
        _LOG_FH.write(f"\nFinished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        _LOG_FH.flush()
        _LOG_FH.close()
        log_dir = _cache_base(output_dir) / "logs"
        latest = log_dir / "conversion-latest.log"
        logs = sorted(p for p in log_dir.glob("conversion-*.log") if p != latest)
        if logs:
            shutil.copy2(str(logs[-1]), str(latest))
        _LOG_FH = None


_PROGRESS_TERMINAL_WIDTH = 72


def _progress_bar(phase: str, current: int, total: int, status: str = ""):
    if not sys.stdout.isatty():
        return
    pct = current / total if total > 0 else 0
    filled = int(_PROGRESS_TERMINAL_WIDTH * pct)
    bar = "█" * filled + "░" * (_PROGRESS_TERMINAL_WIDTH - filled)
    line = f"\r  {phase} [{bar}] {current}/{total} {pct:5.1%}"
    if status:
        status = status[:40] + "…" if len(status) > 41 else status
        line += f"  {status}"
    sys.stdout.write(line)
    sys.stdout.flush()


def _progress_done(phase: str, total: int):
    if not sys.stdout.isatty():
        return
    bar = "█" * _PROGRESS_TERMINAL_WIDTH
    sys.stdout.write(f"\r  {phase} [{bar}] {total}/{total} 100.0%  ✓\n")
    sys.stdout.flush()


def _record_unsupported(func_name, proc, is_special=False):
    tag = "SpecialFunction" if is_special else "FunctionCall"
    proc_id = f"{proc.package}.{proc.proc_name}" if proc else "unknown"
    src = proc.source_file if proc else ""
    entry = f"{proc_id} | {tag} | {func_name.lower()} | {src}"
    if entry not in UNSUPPORTED_FUNCTIONS:
        UNSUPPORTED_FUNCTIONS.append(entry)


def _record_todo(category: str, proc, detail: str = ""):
    proc_id = f"{proc.package}.{proc.proc_name}" if proc else "unknown"
    src = proc.source_file if proc else ""
    TODO_SUMMARY.append((category, proc_id, src, detail))


def _infer_type_from_column_name(column_name: str) -> str:
    """Guess SQL type from column name patterns."""
    col = column_name.lower()
    if any(s in col for s in ("name", "txt", "text", "info", "desc", "msg", "remark", "comment")):
        return "varchar"
    if any(s in col for s in ("id", "no", "num", "seq")):
        if "num" in col and "varchar" not in col:
            return "integer"
        return "bigint"
    if any(s in col for s in ("amount", "balance", "price", "qty", "quantity", "total", "salary")):
        return "numeric"
    if any(s in col for s in ("date", "time", "stamp")):
        return "timestamp"
    if any(s in col for s in ("flag", "status", "level", "type", "code")):
        return "varchar"
    return "varchar"  # default to String


def sql_type_to_java(sql_type) -> str:
    if not sql_type:
        return "Object"

    # Handle dict types (PercentType, RefCursor, etc.)
    if isinstance(sql_type, dict):
        if "TypeName" in sql_type:
            return sql_type_to_java(sql_type["TypeName"])
        elif "PercentType" in sql_type:
            pt = sql_type["PercentType"]
            table = (pt.get("table") or "").lower()
            column = (pt.get("column") or "").lower()
            # Check user overrides
            override = TYPE_OVERRIDES.get((table, column))
            if override:
                return sql_type_to_java(override)
            # Heuristic inference
            return sql_type_to_java(_infer_type_from_column_name(column))
        elif "PercentRowType" in sql_type:
            return "Map<String, Object>"
        elif "Record" in sql_type:
            return "Map<String, Object>"
        elif "RefCursor" in sql_type or "Cursor" in sql_type:
            return "Object"
        else:
            return "Object"

    if isinstance(sql_type, str):
        pct_match = re.match(r'^(\w+)\.(\w+)%type$', sql_type, re.IGNORECASE)
        if pct_match:
            table = pct_match.group(1).lower()
            column = pct_match.group(2).lower()
            override = TYPE_OVERRIDES.get((table, column))
            if override:
                return sql_type_to_java(override)
            return sql_type_to_java(_infer_type_from_column_name(column))

    normalized = sql_type.lower().strip()
    normalized = re.sub(r"\(.*\)", "", normalized).strip()
    result = SQL_TO_JAVA.get(normalized)
    if result:
        return result
    # Unknown type name — likely a user-defined composite type (CREATE TYPE ... AS (...))
    # Map to Map<String, Object> so field access via .get() compiles
    return "Map<String, Object>"


def is_simple_java_type(java_type: str) -> bool:
    """Check if the type is a simple type (no import needed)."""
    return java_type in (
        "String", "Long", "Integer", "Boolean", "Double", "Float",
        "Object", "byte[]", "Map<String, Object>", "void",
    )


# ── Naming Helpers ─────────────────────────────────────────────

def _java_safe_identifier(s: str) -> str:
    """Prepend '_' if identifier starts with a digit (invalid Java name)."""
    if s and s[0].isdigit():
        s = "_" + s
    return s


def snake_to_camel(s: str) -> str:
    """Convert snake_case to camelCase."""
    parts = s.lower().split("_")
    result = parts[0] + "".join(p.capitalize() for p in parts[1:])
    return _java_safe_identifier(result)


def snake_to_pascal(s: str) -> str:
    """Convert snake_case to PascalCase."""
    result = "".join(p.capitalize() for p in s.lower().split("_"))
    return _java_safe_identifier(result)


def package_to_classname(pkg_name: str) -> str:
    if pkg_name.startswith("pkg_"):
        name = pkg_name[4:]
    elif pkg_name.startswith("PKG_"):
        name = pkg_name[4:]
    elif pkg_name.startswith("pack_"):
        name = pkg_name[5:]
    else:
        name = pkg_name
    return snake_to_pascal(name.lower())


def java_method_name(proc_name: str) -> str:
    """get_product_info -> getProductInfo"""
    return snake_to_camel(proc_name)


def mapper_method_id(proc_name: str) -> str:
    """get_product_info -> getProductInfo (same as java method)"""
    return snake_to_camel(proc_name)


# ── AST Model ──────────────────────────────────────────────────

@dataclass
class Parameter:
    name: str
    java_type: str
    sql_type: str
    mode: Optional[str] = None  # IN, OUT, INOUT

    @property
    def java_name(self) -> str:
        return snake_to_camel(self.name)

    @property
    def is_out(self) -> bool:
        return self.mode and self.mode.upper() in ("OUT", "INOUT")

    @property
    def is_refcursor(self) -> bool:
        return self.sql_type and self.sql_type.lower() in ("refcursor", "ref cursor", "refcur", "cursor")


@dataclass
class CommentInfo:
    """A single SQL comment with source position."""
    text: str          # Original comment text, preserving -- or /* */ delimiters
    line: int          # Start line (1-based)
    end_line: int      # End line
    column: int        # Column
    comment_type: str  # "line" or "block"


@dataclass
class DmlStatement:
    sql_type: str
    method_id: str
    sql_text: str
    result_type: Optional[str] = None
    parameter_types: dict = field(default_factory=dict)
    optional_filters: list = field(default_factory=list)
    returns_list: bool = False


@dataclass
class ServiceCall:
    """Represents a cross-service method call."""
    service_name: str  # e.g., "inventoryService"
    method_name: str   # e.g., "checkStock"
    args: list         # e.g., ["productId", "qty"]
    package_name: str = ""


@dataclass
class ProcedureInfo:
    """Extracted info for a single stored procedure/function."""
    name: str                    # full name: pkg_order.create_order
    package: str                 # pkg_order
    proc_name: str               # create_order
    is_function: bool            # True for FUNCTION, False for PROCEDURE
    return_type: Optional[str]   # SQL return type (for functions)
    parameters: list             # List[Parameter]
    body: dict                   # Raw PL/pgSQL block AST
    sql_text: str                # Original SQL text

    # Generated artifacts (filled during processing)
    dml_statements: list = field(default_factory=list)
    service_calls: list = field(default_factory=list)
    java_logic_lines: list = field(default_factory=list)
    imports: set = field(default_factory=set)
    local_vars: dict = field(default_factory=dict)
    table_refs: set = field(default_factory=set)
    var_assignments: dict = field(default_factory=dict)
    is_autonomous: bool = False  # PRAGMA AUTONOMOUS_TRANSACTION

    # NEW: Cursor tracking
    open_cursors: dict = field(default_factory=dict)   # cursor_name -> {"result_var": str, "index_var": str}
    refcursor_out_params: set = field(default_factory=set)  # param names that are REFCURSOR OUT
    cursor_decls: dict = field(default_factory=dict)   # cursor_name -> parsed_query (from DECLARE section)
    source_file: str = ""          # Original SQL file name for display (e.g., PKG_ORDER.sql)
    _source_path: str = ""         # Full path for file access (set by pipeline)
    source_start_line: int = 0     # Procedure start line in original file
    source_end_line: int = 0       # Procedure end line in original file
    leading_comments: list = field(default_factory=list)   # List[CommentInfo] — comments before procedure declaration
    inline_comments: list = field(default_factory=list)    # List[CommentInfo] — comments inside procedure body


@dataclass
class PackageInfo:
    """All procedures in a single package (SQL file)."""
    package_name: str  # e.g., pkg_order
    procedures: list = field(default_factory=list)  # List[ProcedureInfo]
    table_refs: set = field(default_factory=set)    # Tables referenced
    package_vars: dict = field(default_factory=dict)  # name -> {"java_type": ..., "default": ...}
    source_file: str = ""  # Original SQL file name
    comments: list = field(default_factory=list)  # List[CommentInfo] — package-level comments not in any procedure
    java_package: str = ""  # Custom Java package override (empty = use BASE_PACKAGE)


# ── Conversion Report ──────────────────────────────────────────

@dataclass
class SkippedItem:
    sql_file: str
    statement_type: str
    category: str
    name: str
    detail: str
    line_start: int = 0
    line_end: int = 0


@dataclass
class ProcedureMapping:
    sql_file: str
    procedure_name: str
    procedure_type: str
    java_service: str
    java_method: str
    mapper_methods: list
    generated_files: list
    is_stub: bool = False
    has_parse_error: bool = False
    notes: str = ""


@dataclass
class ConversionReport:
    generated_at: str
    config_path: str
    output_dir: str
    sql_files: list
    procedure_mappings: list
    skipped_items: list
    parse_errors: list
    unresolved_calls: list
    total_packages: int = 0
    total_procedures: int = 0
    total_dml: int = 0
    total_cross_calls: int = 0


# ── AST Parser ─────────────────────────────────────────────────

def _split_sql_statements(sql_text: str) -> list:
    """Split SQL text into individual statements. Returns list of (sql_text, start_line) tuples."""
    statements = []
    current = []
    chunk_start_line = 1
    current_line = 0
    in_dollar_quote = False
    dollar_tag = ""
    for line in sql_text.split('\n'):
        current_line += 1
        if not in_dollar_quote:
            for tag_match in re.finditer(r'\$([A-Za-z_0-9]*)\$', line):
                if not in_dollar_quote:
                    in_dollar_quote = True
                    dollar_tag = tag_match.group(0)
                    break
        else:
            if dollar_tag in line:
                in_dollar_quote = False
                dollar_tag = ""
        current.append(line)
        if not in_dollar_quote and re.search(r'\$\$\s*LANGUAGE\s+PLPGSQL\s*;?\s*$', line, re.IGNORECASE):
            combined = '\n'.join(current).strip()
            if combined:
                first_content = chunk_start_line
                for cl in current:
                    if cl.strip():
                        break
                    first_content += 1
                statements.append((combined, first_content))
            current = []
            chunk_start_line = current_line + 1
    if current:
        combined = '\n'.join(current).strip()
        if combined:
            first_content = chunk_start_line
            for cl in current:
                if cl.strip():
                    break
                first_content += 1
            statements.append((combined, first_content))
    return statements


def parse_sql_file(sql_path: str) -> dict:
    """Run ogsql-parser on a SQL file and return JSON AST."""
    with open(sql_path, 'r', encoding='utf-8', errors='replace') as f:
        sql_text = f.read()

    stmts = _split_sql_statements(sql_text)

    if len(stmts) <= 1:
        result = subprocess.run(
            [OGSQL_BIN, "--comments", "-f", sql_path, "parse", "-j"],
            capture_output=True, text=True
        )
        if result.returncode != 0 or not result.stdout.strip().startswith("{"):
            _log(f"  [WARN] ogsql-parser returned {result.returncode}: {result.stderr}")
            result = subprocess.run(
                [OGSQL_BIN, "-f", sql_path, "parse", "-j"],
                capture_output=True, text=True
            )
        ast = json.loads(result.stdout)
        ast["comments"] = _extract_comments_from_text(sql_text)
        return ast

    combined_ast = {"statements": [], "errors": [], "comments": []}
    for i, (stmt_sql, start_line) in enumerate(stmts):
        line_offset = start_line - 1
        tmp_path = os.path.join(tempfile.gettempdir(), f"fluxgauss_{os.getpid()}_{i}.sql")
        try:
            with open(tmp_path, 'w') as tf:
                tf.write(stmt_sql)
            result = subprocess.run(
                [OGSQL_BIN, "--comments", "-f", tmp_path, "parse", "-j"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip().startswith("{"):
                result = subprocess.run(
                    [OGSQL_BIN, "-f", tmp_path, "parse", "-j"],
                    capture_output=True, text=True, timeout=10,
                )
            if result.stdout.strip():
                try:
                    stmt_ast = json.loads(result.stdout)
                    _offset_lines_in_ast(stmt_ast, line_offset)
                    combined_ast["statements"].extend(stmt_ast.get("statements", []))
                    combined_ast["errors"].extend(stmt_ast.get("errors", []))
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            combined_ast["errors"].append({"parse_error": str(e)})
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    combined_ast["comments"] = _extract_comments_from_text(sql_text)
    return combined_ast


def _offset_lines_in_ast(ast: dict, offset: int):
    """Shift all line numbers in an AST fragment by a fixed offset (for multi-statement merge)."""
    if offset == 0:
        return
    for stmt in ast.get("statements", []):
        for _key, val in stmt.items():
            if isinstance(val, dict):
                _offset_dict_lines(val, offset)
        if "start_line" in stmt:
            stmt["start_line"] += offset
        if "end_line" in stmt:
            stmt["end_line"] += offset
    for c in ast.get("comments", []):
        if "line" in c:
            c["line"] += offset
        if "end_line" in c:
            c["end_line"] += offset


def _offset_dict_lines(d: dict, offset: int):
    """Recursively shift 'line' fields in a nested dict."""
    if not isinstance(d, dict):
        return
    for key, val in d.items():
        if key in ("line", "start_line", "end_line") and isinstance(val, int):
            d[key] = val + offset
        elif key == "span" and isinstance(val, dict):
            _offset_dict_lines(val, offset)
        elif isinstance(val, dict):
            _offset_dict_lines(val, offset)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    _offset_dict_lines(item, offset)


def extract_parameters(params_list: list) -> list:
    """Extract parameter info from AST parameter list."""
    result = []
    for p in params_list:
        name = p.get("name", "")
        sql_type = p.get("data_type", "varchar")
        mode = p.get("mode")

        # Workaround: ogsql parser misparses "OUT result INT" as
        # name="out", data_type="result int", mode=null.
        # Detect and correct: name starts with "out" and data_type contains a real type name.
        if isinstance(sql_type, str) and name.lower() in ("out", "in", "inout"):
            parts = sql_type.strip().split()
            if len(parts) >= 2:
                # First part is the real param name, rest is the type
                potential_name = parts[0]
                potential_type = " ".join(parts[1:])
                known_modes = {"out", "in", "inout", "in out"}
                if name.lower() in known_modes and potential_name.lower() not in known_modes:
                    name = potential_name
                    sql_type = potential_type
                    mode = p.get("name", "").lower()  # original name was the mode

        # Handle data_type that could be a dict (e.g., PercentType, RefCursor)
        if isinstance(sql_type, dict):
            if "RefCursor" in sql_type or "Cursor" in sql_type:
                sql_type = "refcursor"
            elif "PercentType" in sql_type:
                pass  # resolved by sql_type_to_java() via TYPE_OVERRIDES
            elif "TypeName" in sql_type:
                sql_type = sql_type["TypeName"]
            else:
                sql_type = "varchar"
        java_type = sql_type_to_java(sql_type)
        result.append(Parameter(
            name=name,
            java_type=java_type,
            sql_type=sql_type,
            mode=mode,
        ))
    return result


def extract_procedures(ast: dict, source_file: str = "") -> tuple:
    """Extract all procedures/functions and package-level variables from parsed AST."""
    procedures = []
    package_vars = {}
    for stmt_wrapper in ast.get("statements", []):
        for stmt_type, stmt_data in stmt_wrapper.items():
            if stmt_type in ("CreateFunction", "CreateProcedure"):
                name_parts = stmt_data.get("name", [])
                package = name_parts[0] if len(name_parts) > 1 else ""
                proc_name = name_parts[-1] if name_parts else "unknown"
                full_name = ".".join(name_parts)

                is_function = stmt_type == "CreateFunction"
                return_type = stmt_data.get("return_type") if is_function else None

                params = extract_parameters(stmt_data.get("parameters", []))
                # Detect REFCURSOR OUT params
                refcursor_outs = set()
                for p in params:
                    if p.is_out and p.is_refcursor:
                        refcursor_outs.add(p.java_name)
                block = stmt_data.get("block", {})
                sql_text = stmt_wrapper.get("sql_text", "")

                proc = ProcedureInfo(
                    name=full_name,
                    package=package,
                    proc_name=proc_name,
                    is_function=is_function,
                    return_type=return_type,
                    parameters=params,
                    body=block,
                    sql_text=sql_text,
                    refcursor_out_params=refcursor_outs,
                    source_file=source_file,
                    source_start_line=stmt_data.get("start_line") or stmt_wrapper.get("start_line", 0),
                    source_end_line=stmt_data.get("end_line") or stmt_wrapper.get("end_line", 0),
                )
                procedures.append(proc)

            elif stmt_type == "CreatePackageBody":
                package_name_parts = stmt_data.get("name", [])
                package_name = package_name_parts[-1] if package_name_parts else "unknown"

                for item in stmt_data.get("items", []):
                    for item_type, item_data in item.items():
                        if item_type == "Variable":
                            var_name = item_data.get("name", "")
                            var_type_raw = item_data.get("data_type", {})
                            if isinstance(var_type_raw, dict):
                                var_type = sql_type_to_java(var_type_raw)
                            else:
                                var_type = sql_type_to_java(str(var_type_raw))
                            default_expr = item_data.get("default")
                            default_val = _expr_to_java(default_expr, None) if default_expr else None
                            package_vars[var_name] = {"java_type": var_type, "default": default_val}

                for item in stmt_data.get("items", []):
                    for item_type, item_data in item.items():
                        if item_type not in ("Procedure", "Function"):
                            continue
                        proc_name = item_data.get("name", [])
                        proc_name = proc_name[-1] if proc_name else "unknown"
                        full_name = f"{package_name}.{proc_name}"
                        is_function = item_type == "Function"
                        return_type = item_data.get("return_type") if is_function else None
                        params = extract_parameters(item_data.get("parameters", []))
                        refcursor_outs = set()
                        for p in params:
                            if p.is_out and p.is_refcursor:
                                refcursor_outs.add(p.java_name)
                        block = item_data.get("block", {})
                        sql_text = item_data.get("sql_text", "")

                        proc = ProcedureInfo(
                            name=full_name,
                            package=package_name,
                            proc_name=proc_name,
                            is_function=is_function,
                            return_type=return_type,
                            parameters=params,
                            body=block,
                            sql_text=sql_text,
                            refcursor_out_params=refcursor_outs,
                            source_file=source_file,
                            source_start_line=item_data.get("start_line", 0),
                            source_end_line=item_data.get("end_line", 0),
                        )
                        procedures.append(proc)
    return procedures, package_vars


def _extract_comments_from_text(sql_text: str) -> list:
    """Extract comments directly from SQL source text with accurate line numbers.

    ogsql reports wrong line numbers for comments inside ``$$...$$`` bodies
    (they are offset by the body length).  This function bypasses the problem
    by scanning the raw SQL text with a regex, producing perfectly accurate
    1-based line numbers for both ``--`` and ``/* */`` comments.
    """
    comments = []
    for m in re.finditer(r'(--[^\n]*|/\*[\s\S]*?\*/)', sql_text):
        start_pos = m.start()
        end_pos = m.end()
        start_line = sql_text[:start_pos].count('\n') + 1
        end_line = sql_text[:end_pos].count('\n') + 1
        raw = m.group(0)
        comment_type = "block" if raw.startswith('/*') else "line"
        # Strip leading whitespace (indentation) so text starts with -- or /*
        text = raw.lstrip()
        comments.append({
            "text": text,
            "line": start_line,
            "end_line": end_line,
            "type": comment_type,
        })
    return comments


def extract_comments(ast: dict) -> list:
    """Extract CommentInfo list from AST JSON comments array."""
    comments = []
    for c in ast.get("comments", []):
        comments.append(CommentInfo(
            text=c.get("text", ""),
            line=c.get("line", 0),
            end_line=c.get("end_line", 0),
            column=c.get("column", 0),
            comment_type=c.get("type", "line"),
        ))
    return comments


def _map_comments_to_procedures(comments: list, procedures: list, source_file: str = ""):
    """Assign comments to procedures based on line number proximity.

    Rules:
    - Comments between prev_proc end and current proc start → leading_comments
    - Comments between proc start_line and end_line → inline_comments
    - Comments not inside any procedure → returned as package-level comments
    """
    if not comments or not procedures:
        return comments  # all become package-level

    # Sort procedures by start line
    sorted_procs = sorted(procedures, key=lambda p: p.source_start_line)

    package_level = []

    for comment in comments:
        # Check if comment is inside any procedure body
        target_proc = None
        for proc in sorted_procs:
            if proc.source_start_line <= comment.line <= proc.source_end_line:
                target_proc = proc
                break

        if target_proc:
            target_proc.inline_comments.append(comment)
            continue

        # Check if comment is a leading comment (before a procedure)
        assigned = False
        for idx, proc in enumerate(sorted_procs):
            prev_end = 0
            if idx > 0:
                prev_end = sorted_procs[idx - 1].source_end_line

            if prev_end < comment.line < proc.source_start_line:
                proc.leading_comments.append(comment)
                assigned = True
                break

        if not assigned:
            package_level.append(comment)

    return package_level


_PROCEDURE_TYPES = {"CreateFunction", "CreateProcedure", "CreatePackageBody"}
_DML_TYPES = {"Select", "Insert", "InsertAll", "InsertFirst", "Update", "Delete", "Merge"}
_AST_METADATA_KEYS = {"sql_text", "start_line", "start_col", "end_line", "end_col",
                       "dynamic_sql_analysis", "transaction_analysis", "query_fingerprints"}

_DDL_PREFIXES = ("Create", "Alter", "Drop")
_DDL_DISPLAY_MAP = {
    "CreateTable": "CREATE TABLE", "CreateTableAs": "CREATE TABLE AS",
    "CreateIndex": "CREATE INDEX", "CreateGlobalIndex": "CREATE GLOBAL INDEX",
    "CreateSequence": "CREATE SEQUENCE", "CreateType": "CREATE TYPE",
    "CreateView": "CREATE VIEW", "CreateMaterializedView": "CREATE MATERIALIZED VIEW",
    "CreateTrigger": "CREATE TRIGGER", "CreateSchema": "CREATE SCHEMA",
    "CreateDatabase": "CREATE DATABASE", "CreateTablespace": "CREATE TABLESPACE",
    "CreateDomain": "CREATE DOMAIN", "CreateExtension": "CREATE EXTENSION",
    "CreateRole": "CREATE ROLE", "CreateUser": "CREATE USER", "CreateGroup": "CREATE GROUP",
    "CreateSynonym": "CREATE SYNONYM", "CreatePackage": "CREATE PACKAGE (spec)",
    "CreateCast": "CREATE CAST", "CreateConversion": "CREATE CONVERSION",
    "AlterTable": "ALTER TABLE", "AlterIndex": "ALTER INDEX",
    "AlterSequence": "ALTER SEQUENCE", "AlterCompositeType": "ALTER TYPE",
    "AlterView": "ALTER VIEW", "AlterDomain": "ALTER DOMAIN",
    "AlterFunction": "ALTER FUNCTION", "AlterProcedure": "ALTER PROCEDURE",
    "AlterTrigger": "ALTER TRIGGER", "AlterSchema": "ALTER SCHEMA",
    "AlterDatabase": "ALTER DATABASE",
    "Drop": "DROP", "Truncate": "TRUNCATE TABLE",
    "Grant": "GRANT", "Revoke": "REVOKE",
    "GrantRole": "GRANT ROLE", "RevokeRole": "REVOKE ROLE",
    "Comment": "COMMENT ON",
}


def _is_ddl_type(stmt_type: str) -> bool:
    return (stmt_type.startswith(_DDL_PREFIXES)
            or stmt_type in ("Truncate", "Grant", "Revoke", "GrantRole", "RevokeRole", "Comment"))


def extract_non_procedure_statements(ast: dict, source_file: str = "") -> list:
    skipped = []
    for stmt_wrapper in ast.get("statements", []):
        for stmt_type, stmt_data in stmt_wrapper.items():
            if stmt_type in _PROCEDURE_TYPES or stmt_type in _AST_METADATA_KEYS:
                continue

            line_start = stmt_wrapper.get("start_line", 0)
            line_end = stmt_wrapper.get("end_line", 0)

            if _is_ddl_type(stmt_type):
                name = _extract_ddl_name(stmt_data, stmt_type)
                category = _DDL_DISPLAY_MAP.get(stmt_type, stmt_type)
                detail = _build_ddl_detail(stmt_data, stmt_type)
                skipped.append(SkippedItem(
                    sql_file=source_file,
                    statement_type="DDL",
                    category=category,
                    name=name,
                    detail=detail,
                    line_start=line_start,
                    line_end=line_end,
                ))
            elif stmt_type in _DML_TYPES:
                name = f"{stmt_type} ({_extract_dml_target(stmt_data, stmt_type)})"
                skipped.append(SkippedItem(
                    sql_file=source_file,
                    statement_type="DML",
                    category=stmt_type,
                    name=name,
                    detail="独立 DML 语句，未组织到存储过程中",
                    line_start=line_start,
                    line_end=line_end,
                ))
            else:
                display = _DDL_DISPLAY_MAP.get(stmt_type, stmt_type)
                sql_text = stmt_wrapper.get("sql_text", "")
                detail = f"{display} — 不涉及存储过程转换"
                if sql_text:
                    detail += f"\nSQL: {sql_text[:200]}"
                skipped.append(SkippedItem(
                    sql_file=source_file,
                    statement_type="OTHER",
                    category=display,
                    name=display,
                    detail=detail,
                    line_start=line_start,
                    line_end=line_end,
                ))
    return skipped


def _extract_ddl_name(stmt_data: dict, stmt_type: str) -> str:
    if not isinstance(stmt_data, dict):
        return stmt_type
    for key in ("name", "table_name", "type_name"):
        val = stmt_data.get(key)
        if val:
            if isinstance(val, list):
                return val[-1] if val else stmt_type
            return str(val)
    names = stmt_data.get("names")
    if isinstance(names, list) and names:
        first = names[0]
        if isinstance(first, list) and first:
            return first[-1]
        return str(first)
    return stmt_type


def _build_ddl_detail(stmt_data: dict, stmt_type: str) -> str:
    if stmt_type == "CreateTable" and isinstance(stmt_data, dict):
        cols = stmt_data.get("columns", [])
        if isinstance(cols, list):
            col_names = []
            for c in cols:
                if isinstance(c, dict):
                    for k, v in c.items():
                        if k == "Column":
                            n = v.get("name", "")
                            if n:
                                col_names.append(n)
            if col_names:
                return f"表定义 ({len(col_names)} 列): {', '.join(col_names[:10])}"
        return "表定义 — 仅作类型参考，不转换"
    if stmt_type == "CreateType":
        return "类型定义 — 用于存储过程中 TYPE 声明，仅作参考"
    return f"{_DDL_DISPLAY_MAP.get(stmt_type, stmt_type)} — 不涉及存储过程，仅作参考"


def _extract_dml_target(stmt_data: dict, stmt_type: str) -> str:
    if not isinstance(stmt_data, dict):
        return "unknown"
    if stmt_type == "Insert":
        table = stmt_data.get("table", [])
        return table[-1] if isinstance(table, list) and table else "unknown"
    if stmt_type == "Update":
        tables = stmt_data.get("tables", [])
        for t in (tables if isinstance(tables, list) else []):
            if isinstance(t, dict):
                for k, v in t.items():
                    if k == "Table":
                        name = v.get("name", [])
                        return name[-1] if isinstance(name, list) and name else "unknown"
        return "unknown"
    if stmt_type == "Delete":
        return _extract_dml_target_simple(stmt_data)
    if stmt_type == "Select":
        from_clause = stmt_data.get("from", [])
        for item in (from_clause if isinstance(from_clause, list) else []):
            if isinstance(item, dict):
                for k, v in item.items():
                    if k == "Table":
                        name = v.get("name", [])
                        return name[-1] if isinstance(name, list) and name else "unknown"
    return "unknown"


def _extract_dml_target_simple(stmt_data: dict) -> str:
    for key in ("table", "tables"):
        val = stmt_data.get(key)
        if val:
            if isinstance(val, list):
                if val and isinstance(val[0], str):
                    return val[-1]
                for item in val:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            if k == "Table":
                                name = v.get("name", [])
                                return name[-1] if isinstance(name, list) and name else "unknown"
    return "unknown"


# ── DML / Logic Analyzer ──────────────────────────────────────

def analyze_procedure(proc: ProcedureInfo, all_packages: dict):
    """Analyze a procedure's body to extract DML, service calls, and Java logic."""
    block = proc.body
    if not block:
        return

    # Process declarations
    for decl in block.get("declarations", []):
        for decl_type, decl_data in decl.items():
            if decl_type == "Variable":
                var_name = decl_data.get("name", "")
                raw_type = decl_data.get("data_type", "varchar")
                # Handle dict types (PercentType, Record, etc.)
                java_type = sql_type_to_java(raw_type)
                proc.local_vars[var_name] = java_type
            elif decl_type == "Record":
                var_name = decl_data.get("name", "")
                if var_name:
                    proc.local_vars[var_name] = "Map<String, Object>"
            elif decl_type == "Pragma":
                pragma_name = decl_data.get("name", "")
                if pragma_name == "AUTONOMOUS_TRANSACTION":
                    proc.is_autonomous = True
                    proc.imports.add("import org.springframework.transaction.annotation.Propagation;")
            elif decl_type == "Cursor":
                cursor_name = decl_data.get("name", "")
                parsed_q = decl_data.get("parsed_query")
                if parsed_q:
                    proc.cursor_decls[cursor_name] = parsed_q
                    proc.cursor_decls[cursor_name.lower()] = parsed_q

    # Process body statements
    body_stmts = block.get("body", [])
    dml_counter = {}
    stmt_checkpoints = []  # [(java_line_start_idx, java_line_end_idx), ...]
    for i, stmt in enumerate(_iter_statements(body_stmts)):
        pre_idx = len(proc.java_logic_lines)
        try:
            _process_statement(stmt, proc, all_packages, dml_counter)
        except Exception as e:
            stmt_preview = str(stmt)[:120] if stmt else "<empty>"
            proc.java_logic_lines.append(f"// ERROR: 处理语句失败 - {str(e).replace('*/', '').replace(chr(10), ' ')}")
            _log(f"      ⚠ Statement error in {proc.name}: {e}\n        stmt: {stmt_preview}")
            _log(traceback.format_exc(), to_stdout=False)
        post_idx = len(proc.java_logic_lines)
        if post_idx > pre_idx:
            stmt_checkpoints.append((pre_idx, post_idx))

    # Inject inline comments into method body at proportional positions
    if proc.inline_comments and stmt_checkpoints:
        _inject_inline_comments(proc, stmt_checkpoints)


def _inject_inline_comments(proc: ProcedureInfo, checkpoints: list):
    """Insert inline comments into java_logic_lines at the correct positions.

    Strategy: read the original SQL file, find actual body statement boundaries
    by scanning for semicolons between BEGIN and END, then map each comment to
    the first statement whose SQL line is > comment line.
    Falls back to proportional mapping when the source file is unavailable.
    """
    stmt_lines = _find_body_stmt_lines(proc)
    sorted_comments = sorted(proc.inline_comments, key=lambda c: c.line)

    groups = []
    batch = [sorted_comments[0]]
    for c in sorted_comments[1:]:
        if c.line - batch[-1].end_line <= 2:
            batch.append(c)
        else:
            groups.append(batch)
            batch = [c]
    groups.append(batch)

    n_cp = len(checkpoints)
    insertions = []
    for group in groups:
        last_comment_line = max(c.end_line for c in group)
        cp_idx = _map_comment_to_checkpoint(
            last_comment_line, stmt_lines, checkpoints, proc
        )
        java_lines = [_format_comment_for_java(c) for c in group]
        java_lines = [l for l in java_lines if l]
        if java_lines:
            insertions.append((checkpoints[cp_idx][0], java_lines))

    for insert_at, lines in sorted(insertions, key=lambda x: x[0], reverse=True):
        for i, line in enumerate(lines):
            proc.java_logic_lines.insert(insert_at + i, line)


def _find_body_stmt_lines(proc: ProcedureInfo) -> list:
    """Scan the original SQL file to find the starting line of each top-level body statement.

    Returns a list of 1-based line numbers (one per body statement between
    BEGIN and END), or an empty list on failure.  Block depth tracking ensures
    semicolons inside IF/FOR/WHILE/LOOP sub-blocks are not counted.
    """
    path = proc._source_path or proc.source_file
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return []

    start = (proc.source_start_line or 1) - 1
    end = proc.source_end_line or len(lines)

    body_start = None
    body_end = None
    depth = 0
    for i in range(start, min(end, len(lines))):
        stripped = lines[i].strip().upper()
        if stripped == 'BEGIN':
            if body_start is None:
                body_start = i + 1
            depth += 1
        elif stripped.startswith('END') and stripped.rstrip(';').strip() == 'END':
            depth -= 1
            if depth == 0:
                body_end = i + 1
                break

    if body_start is None or body_end is None:
        return []

    stmt_lines = []
    blk_depth = 0
    for i in range(body_start, body_end):
        stripped = lines[i].strip()
        up = stripped.upper()

        if re.match(r'END\s+IF\b', up) or re.match(r'END\s+LOOP\b', up):
            blk_depth = max(0, blk_depth - 1)
            continue
        if up in ('END;', 'END'):
            if blk_depth > 0:
                blk_depth -= 1
            continue

        is_blk = False
        if re.match(r'IF\s+', up) and 'THEN' in up:
            is_blk = True
        elif (re.match(r'FOR\s+', up) or re.match(r'WHILE\s+', up)) and 'LOOP' in up:
            is_blk = True
        elif up == 'LOOP':
            is_blk = True

        if is_blk:
            if blk_depth == 0:
                stmt_lines.append(i + 1)
            blk_depth += 1
            continue

        if blk_depth > 0:
            continue

        in_str = False
        in_bc = False
        col = 0
        found = False
        while col < len(lines[i]):
            ch = lines[i][col]
            if in_bc:
                if ch == '*' and col + 1 < len(lines[i]) and lines[i][col + 1] == '/':
                    in_bc = False
                    col += 1
            elif in_str:
                if ch == "'":
                    in_str = False
            elif ch == "'":
                in_str = True
            elif ch == '-' and col + 1 < len(lines[i]) and lines[i][col + 1] == '-':
                break
            elif ch == '/' and col + 1 < len(lines[i]) and lines[i][col + 1] == '*':
                in_bc = True
                col += 1
            elif ch == ';':
                found = True
            col += 1
        if found:
            stmt_lines.append(i + 1)

    return stmt_lines


def _map_comment_to_checkpoint(comment_line: int, stmt_lines: list,
                               checkpoints: list, proc: ProcedureInfo) -> int:
    """Map a comment's SQL line to the index of the checkpoint it should precede."""
    n_cp = len(checkpoints)
    if n_cp == 0:
        return 0

    if stmt_lines:
        for i, sl in enumerate(stmt_lines):
            if sl > comment_line:
                return min(i, n_cp - 1)
        return n_cp - 1

    span = proc.source_end_line - proc.source_start_line
    if span <= 0:
        return 0
    rel = (comment_line - proc.source_start_line) / span
    total = len(proc.java_logic_lines)
    for i, (start, _) in enumerate(checkpoints):
        if (start / total if total else 0) >= rel:
            return i
    return n_cp - 1


def _iter_statements(stmts):
    """Flatten nested statement structures."""
    for s in stmts:
        if isinstance(s, dict):
            yield s


def _process_statement(stmt: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    for stmt_type, stmt_data in stmt.items():
        if stmt_type == "SqlStatement":
            _process_sql_statement(stmt_data, proc, dml_counter)
        elif stmt_type == "If":
            _process_if(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Return":
            _process_return(stmt_data, proc)
        elif stmt_type == "Assignment":
            _process_assignment(stmt_data, proc, all_packages)
        elif stmt_type == "Raise":
            _process_raise(stmt_data, proc)
        elif stmt_type == "Perform":
            _process_perform(stmt_data, proc, all_packages)
        elif stmt_type == "For":
            _process_for(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "While":
            _process_while(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Loop":
            _process_loop(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Open":
            _process_cursor_open(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Fetch":
            _process_cursor_fetch(stmt_data, proc)
        elif stmt_type == "Close":
            _process_cursor_close(stmt_data, proc)
        elif stmt_type == "Exit":
            _process_exit(stmt_data, proc)
        elif stmt_type == "Null":
            proc.java_logic_lines.append("// no-op")
        elif stmt_type == "Execute":
            _process_execute(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Block":
            for s in _iter_statements(stmt_data.get("body", [])):
                _process_statement(s, proc, all_packages, dml_counter)
        elif stmt_type == "Commit":
            if proc.is_autonomous:
                proc.java_logic_lines.append("// COMMIT — auto-committed by @Transactional(propagation = REQUIRES_NEW)")
            else:
                proc.java_logic_lines.append("// COMMIT — auto-committed by Spring @Transactional boundary")
        elif stmt_type == "Rollback":
            if proc.is_autonomous:
                proc.java_logic_lines.append("// ROLLBACK — auto-rolled-back by @Transactional(propagation = REQUIRES_NEW) on exception")
            else:
                proc.java_logic_lines.append("TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();")
                proc.imports.add("import org.springframework.transaction.interceptor.TransactionAspectSupport;")
        elif stmt_type == "ProcedureCall":
            _process_procedure_call(stmt_data, proc, all_packages)
        elif stmt_type in ("sql_text",):
            sql = stmt_data
            if isinstance(sql, str) and "call " in sql.lower():
                _process_call_text(sql, proc, all_packages)
        elif stmt_type == "Continue":
            cond = stmt_data.get("condition")
            if cond:
                java_cond = _expr_to_java(cond, proc)
                proc.java_logic_lines.append(f"if ({java_cond}) {{")
                proc.java_logic_lines.append("    continue;")
                proc.java_logic_lines.append("}")
            else:
                proc.java_logic_lines.append("continue;")
        elif stmt_type == "Goto":
            label = stmt_data.get("label", "unknown")
            proc.java_logic_lines.append(f"// GOTO {label} — Java has no goto, manual refactor required")
            _record_todo("GOTO", proc, f"label={label}")
        elif stmt_type == "Case":
            _process_case_stmt(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Savepoint":
            sp_name = stmt_data.get("name", "sp")
            sp_java = snake_to_camel(sp_name)
            proc.java_logic_lines.append(f"Savepoint {sp_java} = connection.setSavepoint(\"{sp_name}\");")
            proc.imports.add("import java.sql.Savepoint;")
        elif stmt_type == "ReturnQuery":
            _process_return_query(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "ForAll":
            proc.java_logic_lines.append(f"// TODO: FORALL — bulk operation requires manual implementation")
            _record_todo("FORALL", proc, "bulk DML")
        else:
            proc.java_logic_lines.append(f"// TODO: unhandled PL/pgSQL statement type: {stmt_type}")
            _record_todo("UNHANDLED_STMT", proc, str(stmt_type))


def _dml_method_name(dml_type: str, proc_name: str, counter: dict) -> str:
    n = counter.get(dml_type, 0)
    counter[dml_type] = n + 1
    return f"{dml_type}{snake_to_pascal(proc_name)}" + (f"_{n}" if n > 0 else "")


def _strip_into_clause(sql: str) -> str:
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+\w+(\s*,\s*\w+)*\s+(?=from\b)', ' ', sql, flags=re.IGNORECASE)
    return stripped


def _rewrite_select_for_into(sql: str, into_targets: list) -> str:
    if not into_targets:
        return _strip_into_clause(sql)
    into_fields = _extract_all_into_targets(into_targets)
    if not into_fields:
        return _strip_into_clause(sql)
    field_names = [fn for fn, _ in into_fields]
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+\w+(\s*,\s*\w+)*\s+(?=from\b)', ' ', sql, flags=re.IGNORECASE)
    m = re.match(r'(select\s+)(.*?)(\s+from\b)', stripped, re.IGNORECASE | re.DOTALL)
    if not m:
        return stripped
    col_clause = m.group(2).strip()
    cols = [c.strip() for c in re.split(r',\s*', col_clause)]
    if len(cols) != len(field_names):
        return stripped
    new_cols = []
    for col, alias in zip(cols, field_names):
        col_lower = col.lower().strip()
        alias_lower = alias.lower().strip()
        if col_lower == alias_lower:
            new_cols.append(col)
        else:
            new_cols.append(f"{col} AS {alias}")
    return f"{m.group(1)}{', '.join(new_cols)}{m.group(3)}" + stripped[m.end():]


def _process_sql_statement(stmt_data: dict, proc: ProcedureInfo, dml_counter: dict):
    """Process a parsed SQL DML statement."""
    for sql_type, sql_details in stmt_data.items():
        if sql_type == "sql_text":
            continue

        sql_text = stmt_data.get("sql_text", "")

        if sql_type == "Select":
            into_targets = sql_details.get("into_targets")
            from_tables = _extract_table_names(sql_details.get("from", []))

            for t in from_tables:
                proc.table_refs.add(t)

            mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)

            if into_targets:
                first_var = _extract_into_variable(into_targets)
                if first_var:
                    var_java = snake_to_camel(first_var)
                    result_type = proc.local_vars.get(first_var, "Object")
                    var_names = _extract_all_into_variables(into_targets)

                    if len(var_names) > 1:
                        result_type = "Map<String, Object>"
                        proc.java_logic_lines.append(
                            f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                        )
                        into_targets_full = _extract_all_into_targets(into_targets)
                        for field_name, full_parts in into_targets_full:
                            if len(full_parts) >= 2:
                                map_var = snake_to_camel(full_parts[0])
                                vn_java = snake_to_camel(field_name)
                                var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                                cast = f"({var_type}) " if var_type != "Object" else ""
                                _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', f'{cast}_row.get("{field_name}")')
                            else:
                                var_type = proc.local_vars.get(field_name, "Object")
                                vn_java = snake_to_camel(field_name)
                                _emit_assignment(proc, vn_java, f'({var_type}) _row.get("{field_name}")')
                    else:
                        into_targets_full = _extract_all_into_targets(into_targets)
                        if into_targets_full and len(into_targets_full[0][1]) >= 2:
                            full_parts = into_targets_full[0][1]
                            map_var = snake_to_camel(full_parts[0])
                            result_type = "Map<String, Object>"
                            col_name = _extract_select_column(sql_text, 0)
                            proc.java_logic_lines.append(
                                f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                            )
                            _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                        else:
                            _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters)})')

                    proc.dml_statements.append(DmlStatement(
                        sql_type="select",
                        method_id=mapper_method,
                        sql_text=_rewrite_select_for_into(sql_text, into_targets),
                        result_type=result_type,
                    ))
            else:
                proc.dml_statements.append(DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                ))
                proc.java_logic_lines.append(
                    f'List<Map<String, Object>> _result = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                )

        elif sql_type == "Insert":
            from_tables = _extract_table_names_from_insert(sql_details)
            for t in from_tables:
                proc.table_refs.add(t)

            mapper_method = _dml_method_name("insert", proc.proc_name, dml_counter)
            proc.dml_statements.append(DmlStatement(
                sql_type="insert",
                method_id=mapper_method,
                sql_text=sql_text,
            ))
            proc.java_logic_lines.append(
                f'mapper.{mapper_method}({_build_param_args(proc.parameters)});'
            )

        elif sql_type == "Update":
            from_tables = _extract_table_names_from_update(sql_details)
            for t in from_tables:
                proc.table_refs.add(t)

            mapper_method = _dml_method_name("update", proc.proc_name, dml_counter)
            proc.dml_statements.append(DmlStatement(
                sql_type="update",
                method_id=mapper_method,
                sql_text=sql_text,
            ))
            proc.java_logic_lines.append(
                f'mapper.{mapper_method}({_build_param_args(proc.parameters)});'
            )

        elif sql_type == "Delete":
            table_name = _extract_table_name_from_dml(sql_details)
            proc.table_refs.add(table_name)

            mapper_method = _dml_method_name("delete", proc.proc_name, dml_counter)
            proc.dml_statements.append(DmlStatement(
                sql_type="delete",
                method_id=mapper_method,
                sql_text=sql_text,
            ))
            proc.java_logic_lines.append(
                f'mapper.{mapper_method}({_build_param_args(proc.parameters)});'
            )


def _process_if(if_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    condition = _expr_to_java(if_data.get("condition", {}), proc)
    proc.java_logic_lines.append(f"if ({condition}) {{")

    for s in _iter_statements(if_data.get("then_stmts", [])):
        _process_statement(s, proc, all_packages, dml_counter)
    _indent_last_lines(proc, 1)

    for elsif in if_data.get("elsifs", []):
        elsif_cond = _expr_to_java(elsif.get("condition", {}), proc)
        proc.java_logic_lines.append(f"}} else if ({elsif_cond}) {{")
        for s in _iter_statements(elsif.get("stmts", [])):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

    if if_data.get("else_stmts"):
        proc.java_logic_lines.append("} else {")
        for s in _iter_statements(if_data["else_stmts"]):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

    proc.java_logic_lines.append("}")


def _process_return(return_data: dict, proc: ProcedureInfo):
    """Convert RETURN to Java return."""
    expr = return_data.get("expression")
    if expr:
        java_expr = _expr_to_java(expr, proc)
        proc.java_logic_lines.append(f"return {java_expr};")
    else:
        proc.java_logic_lines.append("return;")


def _is_string_expr(expr: str) -> bool:
    s = expr.strip()
    if s.startswith('"') or s.startswith("String.valueOf(") or s.startswith("String.format("):
        return True
    if s.endswith(".toString()") or s.endswith(".getMessage()"):
        return True
    if s.startswith("String."):
        return True
    if ' + ' in s and s.startswith('"'):
        return True
    return False


def _emit_assignment(proc: ProcedureInfo, target: str, expr: str):
    out_param_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
    out_string_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "String"}
    # Handle composite type field write: target is __MAP_PUT__var__key
    if target.startswith("__MAP_PUT__"):
        _, rest = target.split("__MAP_PUT__", 1)
        var_java, field_key = rest.split("__", 1)
        if target in out_param_names or f"({var_java})" in expr:
            proc.java_logic_lines.append(f"{var_java}.put(\"{field_key}\", {expr});")
        else:
            proc.java_logic_lines.append(f"{var_java}.put(\"{field_key}\", {expr});")
    elif target in out_param_names:
        if target in out_string_names and not _is_string_expr(expr):
            expr = f"String.valueOf({expr})"
        proc.java_logic_lines.append(f"{target}.set({expr});")
    else:
        proc.java_logic_lines.append(f"{target} = {expr};")


def _process_assignment(assign_data: dict, proc: ProcedureInfo, all_packages: dict):
    target = _expr_to_java(assign_data.get("target", {}), proc, as_read=False)
    expression = assign_data.get("expression", {})
    java_expr = _expr_to_java(expression, proc, all_packages=all_packages)

    if isinstance(expression, dict):
        for k, v in expression.items():
            if k == "FunctionCall":
                func_name_parts = v.get("name", [])
                if len(func_name_parts) >= 2:
                    if len(func_name_parts) >= 3:
                        pkg = func_name_parts[-2]
                        func = func_name_parts[-1]
                    else:
                        pkg = func_name_parts[0]
                        func = func_name_parts[1]
                    matched_pkg = _find_registered_pkg(pkg, all_packages)
                    if matched_pkg:
                        svc_name = f"{package_to_classname(matched_pkg).lower()}Service"
                        method = java_method_name(func)
                        raw_args = v.get("args", [])
                        target_proc = _find_target_proc(matched_pkg, func, all_packages)
                        args_java = []
                        for i, a in enumerate(raw_args):
                            a_java = _expr_to_java(a, proc)
                            if target_proc and i < len(target_proc.parameters):
                                ptype = target_proc.parameters[i].java_type
                                if "BigDecimal" in ptype and _is_numeric_literal(a):
                                    a_java = f"java.math.BigDecimal.valueOf({a_java})"
                                elif ".get(" in a_java and ptype not in ("Object", "Map<String, Object>"):
                                    if ptype == "long":
                                        a_java = f"((Number) {a_java}).longValue()"
                                    elif ptype == "Long":
                                        a_java = f"((Number) {a_java}).longValue()"
                                    elif ptype == "int":
                                        a_java = f"((Number) {a_java}).intValue()"
                                    elif ptype == "Integer":
                                        a_java = f"((Number) {a_java}).intValue()"
                                    elif "BigDecimal" in ptype:
                                        a_java = f"((java.math.BigDecimal) {a_java})"
                                    elif ptype == "String":
                                        a_java = f"(String) {a_java}"
                                    else:
                                        a_java = f"({ptype}) {a_java}"
                            args_java.append(a_java)
                        args = ", ".join(args_java)
                        if matched_pkg.lower() == proc.package.lower():
                            java_expr = f"this.{method}({args})"
                        else:
                            proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
                            java_expr = f"{svc_name}.{method}({args})"
            elif k == "Literal":
                if isinstance(v, dict) and "String" in v:
                    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
                    if var_name:
                        proc.var_assignments[var_name] = v["String"]

    target_type = _infer_target_type(target, proc)
    expr_type = _infer_expr_type(expression, proc)

    if "BigDecimal" in target_type:
        if _is_integer_literal(expression, 0):
            java_expr = "java.math.BigDecimal.ZERO"
        elif _is_numeric_literal(expression):
            java_expr = f"java.math.BigDecimal.valueOf({java_expr})"
        elif expr_type in ("Integer", "int", "Long", "long", "Double", "double", "Float", "float"):
            java_expr = f"java.math.BigDecimal.valueOf({java_expr})"
    elif target_type == "String" and expr_type not in ("String", "Object", None):
        if _is_numeric_literal(expression):
            java_expr = f"String.valueOf({java_expr})"
        elif expr_type in ("Integer", "int", "Long", "long", "Double", "double"):
            java_expr = f"String.valueOf({java_expr})"

    # Cast Map.get() results when assigning to typed variables
    if ".get(" in java_expr and target_type not in ("Object", "Map<String, Object>", ""):
        if target_type == "String":
            java_expr = f"(String) {java_expr}"
        elif "BigDecimal" in target_type:
            java_expr = f"({target_type}) {java_expr}"
        elif target_type in ("Long", "Integer"):
            java_expr = f"({target_type}) {java_expr}"

    target_out = None
    for p in proc.parameters:
        if p.java_name == target and p.is_out:
            target_out = p
            break
    if target_out and target_out.java_type == "String" and _is_numeric_literal(expression):
        java_expr = f"String.valueOf({java_expr})"

    _emit_assignment(proc, target, java_expr)


def _process_perform(perform_data: dict, proc: ProcedureInfo, all_packages: dict):
    """Convert PERFORM to cross-service call."""
    parsed = perform_data.get("parsed_expr")
    query = perform_data.get("query", "")

    if parsed:
        for k, v in parsed.items():
            if k == "FunctionCall":
                func_name_parts = v.get("name", [])
                if len(func_name_parts) >= 2:
                    if len(func_name_parts) >= 3:
                        pkg = func_name_parts[-2]
                        func = func_name_parts[-1]
                    else:
                        pkg = func_name_parts[0]
                        func = func_name_parts[1]
                    matched_pkg = _find_registered_pkg(pkg, all_packages)
                    if matched_pkg:
                        svc_name = f"{package_to_classname(matched_pkg).lower()}Service"
                        method = java_method_name(func)
                        args = ", ".join(_expr_to_java(a, proc) for a in v.get("args", []))
                        if matched_pkg.lower() == proc.package.lower():
                            proc.java_logic_lines.append(f"this.{method}({args});")
                        else:
                            proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
                            proc.java_logic_lines.append(f"{svc_name}.{method}({args});")
                    else:
                        UNRESOLVED_CALLS.append(f"{proc.package}.{proc.proc_name} -> PERFORM {query}")
                        proc.java_logic_lines.append(f"// PERFORM {query}")
                else:
                    proc.java_logic_lines.append(f"// PERFORM {query}")
    else:
        proc.java_logic_lines.append(f"// PERFORM {query}")


def _process_for(for_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    variable = for_data.get("variable", "i")
    var_java = snake_to_camel(variable)
    kind = for_data.get("kind", {})
    body_stmts = for_data.get("body", [])

    if variable in proc.local_vars:
        del proc.local_vars[variable]

    if "Range" in kind:
        range_data = kind["Range"]
        low = _expr_to_java(range_data.get("low", {"Literal": {"Integer": 0}}), proc)
        high = _expr_to_java(range_data.get("high", {"Literal": {"Integer": 0}}), proc)
        reverse = range_data.get("reverse", False)

        if reverse:
            proc.java_logic_lines.append(f"for (int {var_java} = {high}; {var_java} >= {low}; {var_java}--) {{")
        else:
            proc.java_logic_lines.append(f"for (int {var_java} = {low}; {var_java} <= {high}; {var_java}++) {{")

        for s in _iter_statements(body_stmts):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)
        proc.java_logic_lines.append("}")
    elif "Query" in kind:
        query_data = kind["Query"]
        parsed_query = query_data.get("parsed_query")
        if parsed_query:
            sql_text = _reconstruct_sql_from_ast(parsed_query)
            if sql_text:
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                proc.dml_statements.append(DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                ))
                proc.java_logic_lines.append(
                    f"List<Map<String, Object>> {var_java}List = mapper.{mapper_method}({_build_param_args(proc.parameters)});"
                )
                proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {var_java}List) {{")

                for s in _iter_statements(body_stmts):
                    _process_statement(s, proc, all_packages, dml_counter)
                _indent_last_lines(proc, 1)
                proc.java_logic_lines.append("}")
                return
        proc.java_logic_lines.append(f"// TODO: FOR IN SELECT loop — query reconstruction failed")
        _record_todo("FOR_QUERY_FAILED", proc, "parsed_query or sql reconstruction failed")
    elif "Cursor" in kind:
        cursor_info = kind["Cursor"]
        cursor_expr = cursor_info.get("cursor_name", {})
        cursor_name = _extract_name_from_expr(cursor_expr)

        cursor_meta = (proc.open_cursors.get(cursor_name)
                       or proc.open_cursors.get(cursor_name.lower()))

        if cursor_meta:
            result_var = cursor_meta["result_var"]
            index_var = cursor_meta["index_var"]
            proc.java_logic_lines.append(f"for (int {index_var} = 0; {index_var} < {result_var}.size(); {index_var}++) {{")
            proc.java_logic_lines.append(f"    found = {index_var} < {result_var}.size();")
            proc.java_logic_lines.append(f"    Map<String, Object> {var_java} = {result_var}.get({index_var});")
            for s in _iter_statements(body_stmts):
                _process_statement(s, proc, all_packages, dml_counter)
            _indent_last_lines(proc, 1)
            proc.java_logic_lines.append("}")
        else:
            cursor_decl = (proc.cursor_decls.get(cursor_name)
                           or proc.cursor_decls.get(cursor_name.lower()))
            if cursor_decl:
                sql_text = _reconstruct_sql_from_ast(cursor_decl)
                if sql_text:
                    sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                    mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                    proc.dml_statements.append(DmlStatement(
                        sql_type="select",
                        method_id=mapper_method,
                        sql_text=sql_text,
                        result_type="Map<String, Object>",
                        returns_list=True,
                    ))
                    proc.java_logic_lines.append(
                        f"List<Map<String, Object>> {var_java}List = mapper.{mapper_method}({_build_param_args(proc.parameters)});"
                    )
                    proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {var_java}List) {{")
                    for s in _iter_statements(body_stmts):
                        _process_statement(s, proc, all_packages, dml_counter)
                    _indent_last_lines(proc, 1)
                    proc.java_logic_lines.append("}")
                else:
                    proc.java_logic_lines.append(f"// TODO: FOR IN cursor '{cursor_name}' — query reconstruction failed")
                    _record_todo("FOR_CURSOR_QUERY_FAILED", proc, cursor_name)
            else:
                proc.java_logic_lines.append(f"// TODO: FOR IN cursor '{cursor_name}' — cursor not tracked")
                _record_todo("FOR_CURSOR_UNTRACKED", proc, cursor_name)
    else:
        proc.java_logic_lines.append(f"// TODO: FOR loop with unsupported kind: {list(kind.keys())}")
        _record_todo("FOR_UNSUPPORTED_KIND", proc, str(list(kind.keys())))


def _process_while(while_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    condition = _expr_to_java(while_data.get("condition", {}), proc)
    body_stmts = while_data.get("body", [])

    proc.java_logic_lines.append(f"while ({condition}) {{")
    for s in _iter_statements(body_stmts):
        _process_statement(s, proc, all_packages, dml_counter)
    _indent_last_lines(proc, 1)
    proc.java_logic_lines.append("}")


def _process_loop(loop_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    body_stmts = loop_data.get("body", [])

    proc.java_logic_lines.append("while (true) {")
    for s in _iter_statements(body_stmts):
        _process_statement(s, proc, all_packages, dml_counter)
    _indent_last_lines(proc, 1)
    proc.java_logic_lines.append("}")


def _process_cursor_open(open_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    cursor_info = open_data.get("cursor", {})
    cursor_name = _extract_name_from_expr(cursor_info)
    kind = open_data.get("kind", {})

    # Check if cursor is an OUT REFCURSOR parameter
    cursor_java = snake_to_camel(cursor_name)
    is_out_refcursor = cursor_java in proc.refcursor_out_params

    if "ForQuery" in kind:
        fq = kind["ForQuery"]
        parsed_query = fq.get("parsed_query")

        if parsed_query:
            sql_text = _reconstruct_sql_from_ast(parsed_query)
            if sql_text:
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                proc.dml_statements.append(DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                ))

                # Register cursor in tracking
                result_var = f"{snake_to_camel(cursor_name)}Result"
                index_var = f"{snake_to_camel(cursor_name)}Idx"
                proc.open_cursors[cursor_name] = {
                    "result_var": result_var,
                    "index_var": index_var,
                }
                proc.open_cursors[cursor_name.lower()] = proc.open_cursors[cursor_name]

                if is_out_refcursor:
                    # For REFCURSOR OUT params, store result for return
                    proc.java_logic_lines.append(
                        f"List<Map<String, Object>> {result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters)});"
                    )
                else:
                    proc.java_logic_lines.append(
                        f"List<Map<String, Object>> {result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters)});"
                    )
                    proc.java_logic_lines.append(f"int {index_var} = 0;")
                return

    if "Simple" in kind:
        # OPEN cursor_name(args) — cursor was declared with a query in DECLARE section
        parsed_query = proc.cursor_decls.get(cursor_name) or proc.cursor_decls.get(cursor_name.lower())
        if parsed_query:
            sql_text = _reconstruct_sql_from_ast(parsed_query)
            if sql_text:
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                proc.dml_statements.append(DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                ))

                result_var = f"{snake_to_camel(cursor_name)}Result"
                index_var = f"{snake_to_camel(cursor_name)}Idx"
                proc.open_cursors[cursor_name] = {
                    "result_var": result_var,
                    "index_var": index_var,
                }
                proc.open_cursors[cursor_name.lower()] = proc.open_cursors[cursor_name]

                proc.java_logic_lines.append(
                    f"List<Map<String, Object>> {result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters)});"
                )
                proc.java_logic_lines.append(f"int {index_var} = 0;")
                return

    proc.java_logic_lines.append(f"// cursor {cursor_name} opened — managed by mapper query")


def _reconstruct_sql_from_ast(parsed_query: dict) -> str:
    tmp_path = os.path.join(tempfile.gettempdir(), f"fluxgauss_{os.getpid()}_query.json")
    try:
        with open(tmp_path, "w") as f:
            json.dump({"statements": [parsed_query]}, f)
        result = subprocess.run(
            [OGSQL_BIN, "json2sql", "-f", tmp_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().rstrip(";")
    except Exception:
        pass
    return ""


def _process_cursor_fetch(fetch_data: dict, proc: ProcedureInfo):
    cursor_info = fetch_data.get("cursor", {})
    cursor_name = _extract_name_from_expr(cursor_info)
    into_info = fetch_data.get("into")

    # Look up cursor in tracking
    cursor_meta = proc.open_cursors.get(cursor_name) or proc.open_cursors.get(cursor_name.lower())

    if cursor_meta:
        result_var = cursor_meta["result_var"]
        index_var = cursor_meta["index_var"]

        proc.java_logic_lines.append(f"found = {index_var} < {result_var}.size();")

        if into_info:
            if isinstance(into_info, list):
                var_names = [_extract_name_from_expr(item) for item in into_info]
            else:
                var_names = [_extract_name_from_expr(into_info)]

            proc.java_logic_lines.append(f"if (found) {{")
            proc.java_logic_lines.append(f"    Map<String, Object> _row = {result_var}.get({index_var});")
            proc.java_logic_lines.append(f"    {index_var}++;")
            for vn in var_names:
                var_type = proc.local_vars.get(vn, "Object")
                vn_java = snake_to_camel(vn)
                proc.java_logic_lines.append(f'    {vn_java} = ({var_type}) _row.get("{vn}");')
            proc.java_logic_lines.append("}")
    else:
        # Fallback: original behavior
        if into_info:
            if isinstance(into_info, list):
                var_names = [_extract_name_from_expr(item) for item in into_info]
            else:
                var_names = [_extract_name_from_expr(into_info)]
            vars_str = ", ".join(var_names)
            proc.java_logic_lines.append(f"found = {cursor_name}Result != null && !{cursor_name}Result.isEmpty();")
            if len(var_names) > 1:
                for idx, vn in enumerate(var_names):
                    var_type = proc.local_vars.get(vn, "Object")
                    vn_java = snake_to_camel(vn)
                    proc.java_logic_lines.append(f'{vn_java} = ({var_type}) {cursor_name}Result.get("{vn}");')
            elif var_names:
                vn = var_names[0]
                var_type = proc.local_vars.get(vn, "Object")
                vn_java = snake_to_camel(vn)
                proc.java_logic_lines.append(f'{vn_java} = ({var_type}) {cursor_name}Result.get("{vn}");')
        else:
            proc.java_logic_lines.append(f"found = false;")


def _process_cursor_close(close_data: dict, proc: ProcedureInfo):
    cursor_info = close_data.get("cursor", {})
    cursor_name = _extract_name_from_expr(cursor_info)
    proc.java_logic_lines.append(f"// cursor {cursor_name} closed")


def _find_registered_pkg(pkg: str, all_packages: dict):
    pkg_lower = pkg.lower()
    for registered_pkg in all_packages:
        if registered_pkg.lower() == pkg_lower:
            return registered_pkg
    return None


def _find_target_proc(pkg_name: str, proc_name: str, all_packages: dict):
    """Find a ProcedureInfo by package and procedure name for parameter type lookup."""
    if not all_packages:
        return None
    pkg = all_packages.get(pkg_name)
    if not pkg:
        return None
    for p in pkg.procedures:
        if p.proc_name.lower() == proc_name.lower():
            return p
    return None


def _process_procedure_call(call_data: dict, proc: ProcedureInfo, all_packages: dict):
    func_name_parts = call_data.get("name", [])
    args = call_data.get("arguments", [])

    if len(func_name_parts) >= 3:
        pkg = func_name_parts[-2]
        func = func_name_parts[-1]
    elif len(func_name_parts) == 2:
        pkg = func_name_parts[0]
        func = func_name_parts[1]
    else:
        proc.java_logic_lines.append(f"// CALL {'.'.join(func_name_parts)}(...)")
        return

    matched_pkg = _find_registered_pkg(pkg, all_packages)

    if matched_pkg:
        svc_name = f"{package_to_classname(matched_pkg).lower()}Service"
        method = java_method_name(func)
        args_java = ", ".join(_expr_to_java(a, proc, as_read=False) for a in args)
        is_self_call = (matched_pkg.lower() == proc.package.lower())
        if not is_self_call:
            proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
        call_target = f"this.{method}" if is_self_call else f"{svc_name}.{method}"
        proc.java_logic_lines.append(f"{call_target}({args_java});")
    else:
        full_name = ".".join(func_name_parts)
        UNRESOLVED_CALLS.append(f"{proc.package}.{proc.proc_name} -> {full_name}")
        proc.java_logic_lines.append(f"// CALL {full_name}(...)")


def _wrap_try_catch(body_lines: list, handlers: list, proc: ProcedureInfo, all_packages: dict = None) -> list:
    out_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
    out_string_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "String"}
    result = ["try {"]
    result.extend(f"    {line}" for line in body_lines)

    for handler in handlers:
        conditions = handler.get("conditions", [])
        stmts = handler.get("statements", [])
        condition_name = conditions[0] if conditions else "EXCEPTION"
        result.append(f"}} catch (Exception e) {{ // {condition_name} — src: {proc.source_file}:{proc.source_start_line}")
        for s in _iter_statements(stmts):
            for sk, sv in s.items():
                if sk == "Assignment":
                    target = _expr_to_java(sv.get("target", {}), proc, as_read=False)
                    expr_raw = sv.get("expression", {})
                    expr = _expr_to_java(expr_raw, proc)
                    expr = re.sub(r'\bsqlerrm\b', 'e.getMessage()', expr, flags=re.IGNORECASE)
                    if target in out_java_names:
                        if target in out_string_names and not _is_string_expr(expr):
                            expr = f"String.valueOf({expr})"
                        result.append(f"    {target}.set({expr});")
                    else:
                        result.append(f"    {target} = {expr};")
                elif sk == "Raise":
                    result.append(f"    throw new BusinessException(e.getMessage());")
                elif sk == "Return":
                    result.append(f"    return;")
                elif sk == "ProcedureCall":
                    if all_packages:
                        func_name_parts = sv.get("name", [])
                        call_args = sv.get("arguments", [])
                        if len(func_name_parts) >= 2:
                            pkg = func_name_parts[-2] if len(func_name_parts) >= 3 else func_name_parts[0]
                            func = func_name_parts[-1] if len(func_name_parts) >= 3 else func_name_parts[1]
                            matched = _find_registered_pkg(pkg, all_packages)
                            if matched:
                                svc_name = f"{package_to_classname(matched).lower()}Service"
                                method = java_method_name(func)
                                args_java = ", ".join(_expr_to_java(a, proc, as_read=True) for a in call_args)
                                is_self_call = (matched.lower() == proc.package.lower())
                                if not is_self_call:
                                    proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched))
                                call_target = f"this.{method}" if is_self_call else f"{svc_name}.{method}"
                                result.append(f"    {call_target}({args_java});")
                            else:
                                full_name = ".".join(func_name_parts)
                                result.append(f"    // CALL {full_name}(...)")
                        else:
                            result.append(f"    // CALL {'.'.join(func_name_parts)}(...)")
                    else:
                        result.append(f"    // log error")
                elif sk == "Perform":
                    result.append(f"    // log error")
                else:
                    result.append(f"    // {sk}")

    result.append("}")
    return result


def _process_exit(exit_data: dict, proc: ProcedureInfo):
    condition = exit_data.get("condition")
    if condition:
        java_cond = _expr_to_java(condition, proc)
        proc.java_logic_lines.append(f"if ({java_cond}) {{ break; }}")
    else:
        proc.java_logic_lines.append("break;")


def _process_case_stmt(case_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    operand = _expr_to_java(case_data.get("expression", {}), proc)
    whens = case_data.get("whens", [])
    else_stmts = case_data.get("else_stmts", [])

    first = True
    for when in whens:
        cond = _expr_to_java(when.get("condition", {}), proc)
        keyword = "if" if first else "} else if"
        proc.java_logic_lines.append(f"{keyword} ({operand}.equals({cond})) {{")
        first = False
        for s in _iter_statements(when.get("stmts", [])):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

    if else_stmts:
        proc.java_logic_lines.append("} else {")
        for s in _iter_statements(else_stmts):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

    if whens or else_stmts:
        proc.java_logic_lines.append("}")


def _process_return_query(rq_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    if not proc.is_function:
        proc.java_logic_lines.append("// TODO: RETURN QUERY in non-function context")
        _record_todo("RETURN_QUERY_NON_FUNC", proc, "")
        return

    is_dynamic = rq_data.get("is_dynamic", False)
    if not is_dynamic:
        query = rq_data.get("query", "")
        if query:
            sql_text = _convert_placeholders_to_mybatis(query)
            sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
            sql_type = _detect_sql_type(sql_text)
            mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)
            ret_type = sql_type_to_java(proc.return_type) if proc.return_type else "Object"

            proc.dml_statements.append(DmlStatement(
                sql_type=sql_type,
                method_id=mapper_method,
                sql_text=sql_text,
                result_type=f"List<{ret_type}>",
                returns_list=True,
            ))
            proc.java_logic_lines.append(f"return mapper.{mapper_method}({_build_param_args(proc.parameters)});")
        else:
            proc.java_logic_lines.append("// TODO: RETURN QUERY — empty query")
            _record_todo("RETURN_QUERY_EMPTY", proc, "")
    else:
        dynamic_expr = rq_data.get("dynamic_expr", {})
        var_name = _extract_var_name_from_expr(dynamic_expr)
        proc.java_logic_lines.append(f"// TODO: RETURN QUERY EXECUTE — dynamic SQL: {var_name}")
        _record_todo("RETURN_QUERY_DYNAMIC", proc, f"var={var_name}")


def _extract_var_name_from_expr(expr: dict) -> str:
    if not isinstance(expr, dict):
        return ""
    for key, val in expr.items():
        if key == "PlVariable":
            parts = val if isinstance(val, list) else [val]
            return parts[-1] if parts else ""
    return ""


def _process_execute(execute_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    # NEW: Prefer parsed_query when available (parser already parsed the SQL)
    parsed_query = execute_data.get("parsed_query")
    if parsed_query:
        sql_text = _reconstruct_sql_from_ast(parsed_query)
        if sql_text:
            sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
            # Also convert USING args as MyBatis parameters
            using_args = execute_data.get("using_args", [])
            for arg in using_args:
                if isinstance(arg, dict):
                    argument = arg.get("argument", {})
                    arg_name = _extract_var_name_from_expr(argument)
                    if arg_name:
                        sql_text = re.sub(
                            rf'\b{re.escape(arg_name)}\b',
                            f'#{{{snake_to_camel(arg_name)}}}',
                            sql_text, flags=re.IGNORECASE
                        )

            sql_type = _detect_sql_type(sql_text)
            mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)
            into_targets = execute_data.get("into_targets", [])

            if into_targets:
                first_var = _extract_into_variable(into_targets)
                if first_var:
                    var_java = snake_to_camel(first_var)
                    result_type = proc.local_vars.get(first_var, "Object")
                    var_names = _extract_all_into_variables(into_targets)
                    if len(var_names) > 1:
                        result_type = "Map<String, Object>"
                        proc.java_logic_lines.append(
                            f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                        )
                        into_targets_full = _extract_all_into_targets(into_targets)
                        for field_name, full_parts in into_targets_full:
                            if len(full_parts) >= 2:
                                map_var = snake_to_camel(full_parts[0])
                                vn_java = snake_to_camel(field_name)
                                var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                                cast = f"({var_type}) " if var_type != "Object" else ""
                                _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', f'{cast}_row.get("{field_name}")')
                            else:
                                var_type = proc.local_vars.get(field_name, "Object")
                                vn_java = snake_to_camel(field_name)
                                _emit_assignment(proc, vn_java, f'({var_type}) _row.get("{field_name}")')
                    else:
                        into_targets_full = _extract_all_into_targets(into_targets)
                        if into_targets_full and len(into_targets_full[0][1]) >= 2:
                            full_parts = into_targets_full[0][1]
                            map_var = snake_to_camel(full_parts[0])
                            result_type = "Map<String, Object>"
                            proc.java_logic_lines.append(
                                f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                            )
                            _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                        else:
                            _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters)})')
                    proc.dml_statements.append(DmlStatement(
                        sql_type=sql_type,
                        method_id=mapper_method,
                        sql_text=sql_text,
                        result_type=result_type,
                    ))
            else:
                proc.dml_statements.append(DmlStatement(
                    sql_type=sql_type,
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>" if sql_type == "select" else None,
                ))
                proc.java_logic_lines.append(
                    f'mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                )
            return

    # FALLBACK: existing string tracing logic (keep as-is)
    string_expr = execute_data.get("string_expr", {})
    var_name = _extract_var_name_from_expr(string_expr)
    sql_text = proc.var_assignments.get(var_name, "")
    using_args = execute_data.get("using_args", [])
    into_targets = execute_data.get("into_targets", [])

    if not sql_text:
        proc.java_logic_lines.append(f"// TODO: EXECUTE {var_name} — could not resolve SQL string")
        _record_todo("EXECUTE_UNRESOLVED", proc, f"var={var_name}")
        return

    sql_text = _convert_placeholders_to_mybatis(sql_text)
    sql_type = _detect_sql_type(sql_text)
    mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)

    if into_targets:
        first_var = _extract_into_variable(into_targets)
        if first_var:
            var_java = snake_to_camel(first_var)
            result_type = proc.local_vars.get(first_var, "Object")
            var_names = _extract_all_into_variables(into_targets)
            if len(var_names) > 1:
                result_type = "Map<String, Object>"
                proc.java_logic_lines.append(
                    f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                )
                into_targets_full = _extract_all_into_targets(into_targets)
                for field_name, full_parts in into_targets_full:
                    if len(full_parts) >= 2:
                        map_var = snake_to_camel(full_parts[0])
                        var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                        cast = f"({var_type}) " if var_type != "Object" else ""
                        _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', f'{cast}_row.get("{field_name}")')
                    else:
                        var_type = proc.local_vars.get(field_name, "Object")
                        vn_java = snake_to_camel(field_name)
                        _emit_assignment(proc, vn_java, f'({var_type}) _row.get("{field_name}")')
            else:
                into_targets_full = _extract_all_into_targets(into_targets)
                if into_targets_full and len(into_targets_full[0][1]) >= 2:
                    full_parts = into_targets_full[0][1]
                    map_var = snake_to_camel(full_parts[0])
                    result_type = "Map<String, Object>"
                    proc.java_logic_lines.append(
                        f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters)});'
                    )
                    _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                else:
                    _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters)})')
            proc.dml_statements.append(DmlStatement(
                sql_type=sql_type,
                method_id=mapper_method,
                sql_text=sql_text,
                result_type=result_type,
            ))
    else:
        proc.dml_statements.append(DmlStatement(
            sql_type=sql_type,
            method_id=mapper_method,
            sql_text=sql_text,
            result_type="Map<String, Object>" if sql_type == "select" else None,
        ))
        proc.java_logic_lines.append(
            f'mapper.{mapper_method}({_build_param_args(proc.parameters)});'
        )


def _convert_placeholders_to_mybatis(sql: str) -> str:
    sql = re.sub(r':(\w+)', lambda m: f'#{{{snake_to_camel(m.group(1))}}}', sql)
    sql = re.sub(r'\$(\d+)', lambda m: f'#{{param{m.group(1)}}}', sql)
    return sql


def _detect_sql_type(sql: str) -> str:
    first_word = sql.strip().split()[0].lower() if sql.strip() else ""
    if first_word == "select":
        return "select"
    elif first_word == "insert":
        return "insert"
    elif first_word == "update":
        return "update"
    elif first_word == "delete":
        return "delete"
    return "select"


def _extract_name_from_expr(expr: dict) -> str:
    if not isinstance(expr, dict):
        return str(expr)
    for key, val in expr.items():
        if key in ("PlVariable", "ColumnRef"):
            if isinstance(val, list):
                return val[-1] if val else "?"
            return str(val)
        elif key == "FunctionCall":
            parts = val.get("name", [])
            return parts[-1] if parts else "?"
    return "?"


def _process_raise(raise_data: dict, proc: ProcedureInfo):
    level = raise_data.get("level", "")
    message = (raise_data.get("message") or "''").strip("'\"")
    params = raise_data.get("params", [])

    placeholder_idx = 0
    while "%" in message and placeholder_idx < len(params):
        message = message.replace("%", f"__PH{placeholder_idx}__", 1)
        placeholder_idx += 1

    for i in range(placeholder_idx):
        message = message.replace(f"__PH{i}__", "%s")

    if level == "Exception":
        if params:
            args_java = ", ".join(_expr_to_java(p, proc) for p in params)
            proc.java_logic_lines.append(
                f'throw new BusinessException(String.format("{message}", {args_java}));'
            )
        else:
            proc.java_logic_lines.append(f'throw new BusinessException("{message}");')
    elif level in ("Notice", "Info", "Log", "Debug"):
        log_level = {"notice": "info", "info": "info", "log": "info", "debug": "debug"}.get(level.lower(), "info")
        log_msg = message.replace("%s", "{}")
        if params:
            args_java = ", ".join(_expr_to_java(p, proc) for p in params)
            proc.java_logic_lines.append(f'log.{log_level}("{log_msg}", {args_java});')
        else:
            proc.java_logic_lines.append(f'log.{log_level}("{log_msg}");')
    else:
        proc.java_logic_lines.append(f'// RAISE {level} {message}')


def _process_call_text(sql: str, proc: ProcedureInfo, all_packages: dict):
    """Process CALL statement from raw sql_text."""
    # Normalize spaces: ogsql-parser outputs "pkg_inventory . reserve_stock"
    normalized = re.sub(r'\s*\.\s*', '.', sql.strip())
    # Extract "call pkg_xxx.proc_name(args)"
    match = re.match(r"call\s+([\w.]+)\s*\(([^)]*)\)", normalized, re.IGNORECASE)
    if match:
        full_name = match.group(1)
        args_str = match.group(2).strip()
        # Normalize args: remove extra spaces around dots
        args_str = re.sub(r'\s*\.\s*', '.', args_str)
        parts = full_name.split(".")
        if len(parts) >= 2:
            pkg = parts[0]
            func = parts[1]
            matched_pkg = _find_registered_pkg(pkg, all_packages)
            if matched_pkg:
                svc_name = f"{package_to_classname(matched_pkg).lower()}Service"
                method = java_method_name(func)
                java_args = _convert_sql_args_to_java(args_str, proc)
                is_self_call = (matched_pkg.lower() == proc.package.lower())
                if not is_self_call:
                    proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
                call_target = f"this.{method}" if is_self_call else f"{svc_name}.{method}"
                proc.java_logic_lines.append(f"{call_target}({java_args});")
            else:
                UNRESOLVED_CALLS.append(f"{proc.package}.{proc.proc_name} -> {full_name}({args_str})")
                proc.java_logic_lines.append(f"// CALL {full_name}({args_str})")


def _convert_sql_args_to_java(args_str: str, proc: ProcedureInfo) -> str:
    """Convert SQL-style CALL arguments to Java expressions."""
    if not args_str.strip():
        return ""
    # Split by comma, but respect parentheses nesting
    args = []
    depth = 0
    current = []
    for ch in args_str:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append(''.join(current).strip())

    java_args = []
    for arg in args:
        java_args.append(_sql_arg_to_java(arg, proc))
    return ", ".join(java_args)


def _sql_arg_to_java(arg: str, proc: ProcedureInfo) -> str:
    """Convert a single SQL argument to Java expression."""
    arg = arg.strip()
    # String literal
    if arg.startswith("'") and arg.endswith("'"):
        return f'"{arg[1:-1]}"'
    # Numeric literal
    if re.match(r'^-?\d+$', arg):
        return arg
    if re.match(r'^-?\d+\.\d+$', arg):
        return f"{arg}d"
    # Check if it's a known parameter (p_xxx)
    for p in proc.parameters:
        if arg.lower() == p.name.lower():
            return p.java_name
    # Check if it's a known local variable (v_xxx)
    if arg.lower() in proc.local_vars:
        return snake_to_camel(arg.lower())
    # Default: camelCase it
    return snake_to_camel(arg)


SQL_FUNCTION_MAP = {
    "coalesce": "Objects.requireNonNullElse",
    "nullif": "(_a == _b ? null : _a)",
    "greatest": "Math.max",
    "least": "Math.min",
    "abs": "Math.abs",
    "ceil": "Math.ceil",
    "floor": "Math.floor",
    "round": "Math.round",
    "upper": "String.valueOf({args}).toUpperCase()",
    "lower": "String.valueOf({args}).toLowerCase()",
    "trim": "__EXPR__String.valueOf({args0}).trim()",
    "length": "__EXPR__String.valueOf({args0}).length()",
    "to_char": "__HANDLER__",
    "to_number": "Long.valueOf",
    "to_clob": "{args0}",
    "to_date": "java.sql.Date.valueOf",
    "to_timestamp": "java.sql.Timestamp.valueOf",
    "current_timestamp": "__EXPR__new java.sql.Timestamp(System.currentTimeMillis())",
    "current_date": "__EXPR__new java.sql.Date(System.currentTimeMillis())",
    "now": "__EXPR__new java.sql.Timestamp(System.currentTimeMillis())",
    "concat": "String.format",
    "substr": "String.valueOf({args0}).substring({args1})",
    "substrb": "String.valueOf({args0}).substring({args1})",
    "substring": "String.valueOf({args0}).substring({args1})",
    "replace": "String.valueOf({args0}).replace({args1}, {args2})",
    "lpad": "__EXPR__String.format(\"%" + "%1$\" + ({args1}) + \"s\", {args0}).replace(\" \", {args2})",
    "rpad": "__EXPR__String.format(\"%" + "%1$-\" + ({args1}) + \"s\", {args0}).replace(\" \", {args2})",
    "nvl": "__EXPR__({args0} != null ? {args0} : {args1})",
    "nvl2": "__EXPR__({args0} != null ? {args1} : {args2})",
    "decode": "__HANDLER__",
    "trunc": "__EXPR__Math.floor((double)({args0}))",
    "mod": "__EXPR__(({args0}) % ({args1}))",
    "power": "Math.pow",
    "sign": "__EXPR__Integer.signum((int)({args0}))",
    "instr": "__EXPR__String.valueOf({args0}).indexOf({args1}) + 1",
    "rtrim": "__EXPR__String.valueOf({args0}).replaceAll(\"\\\\s+$\", \"\")",
    "ltrim": "__EXPR__String.valueOf({args0}).replaceAll(\"^\\\\s+\", \"\")",
    "chr": "__EXPR__String.valueOf((char)({args0}))",
    "ascii": "__EXPR__(int) String.valueOf({args0}).charAt(0)",
    "add_months": "__EXPR__java.time.LocalDate.now().plusMonths({args1})",
    "months_between": "__EXPR__java.time.Period.between(((java.time.LocalDate){args0}), ((java.time.LocalDate){args1})).toTotalMonths()",
    "last_day": "__EXPR__java.time.LocalDate.now().withDayOfMonth(java.time.LocalDate.now().lengthOfMonth())",
    "next_day": "__EXPR__java.time.LocalDate.now().plusWeeks(1)",
    "trunc_date": "__EXPR__java.time.LocalDate.from(java.time.LocalDateTime.ofInstant(java.sql.Timestamp.valueOf({args0}).toInstant(), java.time.ZoneId.systemDefault()))",
    "row_number": "rowNumber",
    "rownum": "rowNumber",
    "count": "__EXPR__0L",
    "sum": "__EXPR__0L",
    "max": "Math.max",
    "min": "Math.min",
    "avg": "__EXPR__0.0d",
    "cast": "({type}) {expr}",
    "regexp_replace": "__EXPR__String.valueOf({args0}).replaceAll({args1}, {args2})",
    "regexp_like": "__EXPR__String.valueOf({args0}).matches({args1})",
    "date_trunc": "__HANDLER__",
    "translate": "__HANDLER__",
    "left": "__EXPR__String.valueOf({args0}).substring(0, Math.min(Integer.parseInt(String.valueOf({args1})), String.valueOf({args0}).length()))",
    "right": "__EXPR__String.valueOf({args0}).substring(Math.max(0, String.valueOf({args0}).length() - Integer.parseInt(String.valueOf({args1}))))",
    "reverse": "__EXPR__new StringBuilder(String.valueOf({args0})).reverse().toString()",
    "repeat": "__EXPR__String.valueOf({args0}).repeat(Integer.parseInt(String.valueOf({args1})))",
    "split_part": "__EXPR__String.valueOf({args0}).split(java.util.regex.Pattern.quote(String.valueOf({args1})))[Math.min(Integer.parseInt(String.valueOf({args2})) - 1, String.valueOf({args0}).split(java.util.regex.Pattern.quote(String.valueOf({args1}))).length - 1)]",
    "initcap": "__EXPR__java.util.Arrays.stream(String.valueOf({args0}).split(\" \")).map(w -> w.isEmpty() ? w : Character.toUpperCase(w.charAt(0)) + w.substring(1).toLowerCase()).collect(java.util.stream.Collectors.joining(\" \"))",
    "sqrt": "__EXPR__Math.sqrt({args0})",
    "log": "__EXPR__Math.log({args0})",
    "exp": "__EXPR__Math.exp({args0})",
}

SQL_EXPR_FUNCTIONS = {k for k, v in SQL_FUNCTION_MAP.items() if v.startswith("__EXPR__")}


# SpecialFunction handler: converts ogsql SpecialFunction AST nodes to Java expressions.
# ogsql uses SpecialFunction for SQL built-ins that employ special keyword syntax
# (FROM/FOR/IN/PLACING/LEADING/TRAILING/BOTH etc.) rather than standard func(args) calls.
#
# Each handler receives (val, proc, _expr_to_java) and returns a Java expression string.
# val is the dict value under the "SpecialFunction" key, containing "name" and "args".

def _sf_substr(val, proc, _expr_to_java_fn):
    """SUBSTR/SUBSTRING: SQL 1-based → Java 0-based substring."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    s = args_java[0] if len(args_java) > 0 else '""'
    if len(args_java) >= 3:
        start = args_java[1]
        length = args_java[2]
        return f"String.valueOf({s}).substring(Math.max(0, ({start}) - 1), Math.min(String.valueOf({s}).length(), Math.max(0, ({start}) - 1) + ({length})))"
    elif len(args_java) == 2:
        start = args_java[1]
        return f"String.valueOf({s}).substring(Math.max(0, ({start}) - 1))"
    return f"String.valueOf({s})"


def _sf_overlay(val, proc, _expr_to_java_fn):
    """OVERLAY: SQL OVERLAY(str PLACING repl FROM start FOR len) → Java string splice."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    s = args_java[0] if len(args_java) > 0 else '""'
    repl = args_java[1] if len(args_java) > 1 else '""'
    start = args_java[2] if len(args_java) > 2 else "1"
    length = args_java[3] if len(args_java) > 3 else None
    s_var = f"String.valueOf({s})"
    if length is not None:
        return f"({s_var}).substring(0, Math.max(0, ({start}) - 1)) + String.valueOf({repl}) + ({s_var}).substring(Math.max(0, ({start}) - 1 + ({length})))"
    return f"({s_var}).substring(0, Math.max(0, ({start}) - 1)) + String.valueOf({repl}) + ({s_var}).substring(Math.max(0, ({start}) - 1 + String.valueOf({repl}).length()))"


def _sf_position(val, proc, _expr_to_java_fn):
    """POSITION: SQL POSITION(substr IN str) → Java indexOf + 1 (SQL is 1-based)."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    substr = args_java[0] if len(args_java) > 0 else '""'
    s = args_java[1] if len(args_java) > 1 else '""'
    return f"(String.valueOf({s}).indexOf(String.valueOf({substr})) + 1)"


def _sf_extract(val, proc, _expr_to_java_fn):
    """EXTRACT: SQL EXTRACT(field FROM expr) → Java temporal field access."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    field_expr = args_java[0] if len(args_java) > 0 else '"YEAR"'
    src_expr = args_java[1] if len(args_java) > 1 else "new java.sql.Timestamp(System.currentTimeMillis())"
    field_name = field_expr.strip('"').strip("'").upper()
    field_map = {
        "YEAR": "toLocalDate().getYear()",
        "MONTH": "toLocalDate().getMonthValue()",
        "DAY": "toLocalDate().getDayOfMonth()",
        "HOUR": "toLocalDateTime().getHour()",
        "MINUTE": "toLocalDateTime().getMinute()",
        "SECOND": "toLocalDateTime().getSecond()",
    }
    accessor = field_map.get(field_name, f"/* EXTRACT {field_name} */ -1")
    return f"java.sql.Timestamp.valueOf(String.valueOf({src_expr})).{accessor}"


def _sf_trim(val, proc, _expr_to_java_fn):
    """TRIM: SQL TRIM(LEADING/TRAILING/BOTH chars FROM str) → Java regex/string ops."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    if len(args_java) < 3:
        s = args_java[0] if args_java else '""'
        return f"String.valueOf({s}).trim()"
    direction = args_java[0].strip('"').strip("'").upper()
    chars = args_java[1]
    s = args_java[2]
    if direction in ("BOTH", ""):
        if chars == '" "' or chars == "' '":
            return f"String.valueOf({s}).trim()"
        return f"String.valueOf({s}).replaceAll(\"^\" + java.util.regex.Pattern.quote(String.valueOf({chars})) + \"+|\" + java.util.regex.Pattern.quote(String.valueOf({chars})) + \"+$\", \"\")"
    elif direction == "LEADING":
        if chars == '" "' or chars == "' '":
            return f"String.valueOf({s}).replaceAll(\"^\\\\s+\", \"\")"
        return f"String.valueOf({s}).replaceAll(\"^\" + java.util.regex.Pattern.quote(String.valueOf({chars})) + \"+\", \"\")"
    elif direction == "TRAILING":
        if chars == '" "' or chars == "' '":
            return f"String.valueOf({s}).replaceAll(\"\\\\s+$\", \"\")"
        return f"String.valueOf({s}).replaceAll(java.util.regex.Pattern.quote(String.valueOf({chars})) + \"+$\", \"\")"
    return f"String.valueOf({s}).trim()"


def _sf_convert(val, proc, _expr_to_java_fn):
    """CONVERT: SQL CONVERT(expr USING encoding) → Java String(byte[]) encoding."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    expr_str = args_java[0] if len(args_java) > 0 else '""'
    encoding = args_java[1] if len(args_java) > 1 else '"UTF-8"'
    return f"new String(String.valueOf({expr_str}).getBytes(), {encoding})"


def _sf_current_timestamp(val, proc, _expr_to_java_fn):
    """CURRENT_TIMESTAMP / CURRENT_TIME: optional precision → Java Timestamp."""
    return "new java.sql.Timestamp(System.currentTimeMillis())"


def _sf_group_concat(val, proc, _expr_to_java_fn):
    """GROUP_CONCAT: special aggregation syntax → TODO placeholder."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    return f"/* TODO: GROUP_CONCAT requires manual translation */ String.join(\", \", java.util.Collections.emptyList())"


SPECIAL_FUNCTION_MAP = {
    "substr": _sf_substr,
    "substring": _sf_substr,
    "overlay": _sf_overlay,
    "position": _sf_position,
    "extract": _sf_extract,
    "trim": _sf_trim,
    "convert": _sf_convert,
    "current_timestamp": _sf_current_timestamp,
    "current_time": _sf_current_timestamp,
    "group_concat": _sf_group_concat,
}


def _indent_last_lines(proc: ProcedureInfo, level: int):
    """Add indentation to recently added lines."""
    indent = "    " * level
    # Indent lines added since the last control structure
    lines = proc.java_logic_lines
    # Find the last opening brace
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].endswith("{"):
            # Indent everything after this
            for j in range(i + 1, len(lines)):
                if not lines[j].startswith("}") and not lines[j].strip().startswith("//"):
                    lines[j] = indent + lines[j]
            break


_DATE_TRUNC_UNIT_MAP = {
    "microsecond": "ChronoUnit.MICROS", "milliseconds": "ChronoUnit.MILLIS",
    "second": "ChronoUnit.SECONDS", "minute": "ChronoUnit.MINUTES",
    "hour": "ChronoUnit.HOURS", "day": "ChronoUnit.DAYS",
    "week": "ChronoUnit.WEEKS", "month": "ChronoUnit.MONTHS",
    "quarter": "ChronoUnit.MONTHS", "year": "ChronoUnit.YEARS",
    "decade": "ChronoUnit.DECADES", "century": "ChronoUnit.CENTURIES",
}

_TO_CHAR_DATE_MAP = {
    "yyyy": "yyyy", "yy": "yy", "mm": "MM", "mon": "MMM", "month": "MMMM",
    "dd": "dd", "dy": "EEE", "day": "EEEE",
    "hh24": "HH", "hh12": "hh", "hh": "HH", "mi": "mm", "ss": "ss",
    "ff3": "SSS", "ms": "SSS",
}


def _handle_function(func_name, args_java, proc):
    if func_name == "decode":
        if len(args_java) < 3:
            return "null"
        expr = args_java[0]
        parts = []
        i = 1
        while i + 1 < len(args_java):
            parts.append((args_java[i], args_java[i + 1]))
            i += 2
        default = args_java[-1] if i < len(args_java) else "null"
        result = default
        for val, ret in reversed(parts):
            result = f"({expr}.equals({val}) ? {ret} : {result})"
        return result

    elif func_name == "to_char":
        if len(args_java) == 1:
            return f"String.valueOf({args_java[0]})"
        fmt_raw = args_java[1].strip('"').strip("'").lower() if len(args_java) > 1 else ""
        java_fmt = fmt_raw
        for sql_pat, java_pat in sorted(_TO_CHAR_DATE_MAP.items(), key=lambda x: -len(x[0])):
            java_fmt = java_fmt.replace(sql_pat, java_pat)
        has_date_token = any(t in fmt_raw for t in ("yyyy", "yy", "mm", "mon", "dd", "hh", "mi", "ss"))
        if has_date_token:
            return f"new java.text.SimpleDateFormat(\"{java_fmt}\").format(new java.util.Date(java.sql.Timestamp.valueOf(String.valueOf({args_java[0]})).getTime()))"
        num_fmt = args_java[1].strip('"').strip("'")
        num_fmt_java = num_fmt.replace("FM", "").replace(",", "").replace("9", "#").replace("0", "0")
        return f"new java.text.DecimalFormat(\"{num_fmt_java}\").format({args_java[0]})"

    elif func_name == "date_trunc":
        if len(args_java) < 2:
            return args_java[0] if args_java else "null"
        field_raw = args_java[0].strip('"').strip("'").lower()
        unit = _DATE_TRUNC_UNIT_MAP.get(field_raw, "ChronoUnit.DAYS")
        if "MONTHS" in unit and field_raw == "quarter":
            return f"java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant(java.sql.Timestamp.valueOf(String.valueOf({args_java[1]})).toInstant(), java.time.ZoneId.systemDefault()).truncatedTo(java.time.temporal.ChronoUnit.DAYS).withMonth(((java.time.LocalDateTime.ofInstant(java.sql.Timestamp.valueOf(String.valueOf({args_java[1]})).toInstant(), java.time.ZoneId.systemDefault()).getMonthValue() - 1) / 3) * 3 + 1).withDayOfMonth(1))"
        return f"java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant(java.sql.Timestamp.valueOf(String.valueOf({args_java[1]})).toInstant(), java.time.ZoneId.systemDefault()).truncatedTo(java.time.temporal.{unit}))"

    elif func_name == "translate":
        if len(args_java) < 3:
            return args_java[0] if args_java else '""'
        s = args_java[0]
        from_chars = args_java[1]
        to_chars = args_java[2]
        return f"String.valueOf({s}).chars().mapToObj(c -> {{ int idx = String.valueOf({from_chars}).indexOf(c); return idx >= 0 && idx < String.valueOf({to_chars}).length() ? String.valueOf({to_chars}).charAt(idx) : (char) c; }}).collect(java.util.stream.Collectors.joining())"

    return f"/* TODO: {func_name} */ null"


_NUMERIC_FUNC_RETURN_INT = {"length", "instr", "ascii", "sign", "mod", "round", "trunc"}
_NUMERIC_FUNC_RETURN_DOUBLE = {"abs", "ceil", "floor", "power", "sqrt", "log", "exp"}
_NUMERIC_FUNC_RETURN_LONG = {"to_number"}
_STRING_FUNC_RETURN = {
    "upper", "lower", "trim", "replace", "concat", "lpad", "rpad", "rtrim", "ltrim",
    "chr", "substr", "substring", "nvl", "nvl2", "coalesce", "to_char", "to_clob",
    "reverse", "repeat", "initcap", "regexp_replace", "regexp_like", "left", "right",
    "split_part", "translate", "overlay",
}


def _java_type_from_field_name(field_name: str) -> str:
    """Infer Java type from a composite type field name (heuristic)."""
    col = field_name.lower()
    if any(s in col for s in ("name", "txt", "text", "info", "desc", "msg", "remark", "comment", "label", "status", "code", "type", "flag")):
        return "String"
    if any(s in col for s in ("id", "no", "seq")):
        return "Long"
    if any(s in col for s in ("amount", "balance", "price", "qty", "quantity", "total", "salary")):
        return "java.math.BigDecimal"
    if any(s in col for s in ("count", "cnt", "num")):
        return "Integer"
    if any(s in col for s in ("date", "time", "stamp")):
        return "java.sql.Timestamp"
    return "Object"


def _infer_expr_type(expr, proc: ProcedureInfo) -> str:
    """Infer the Java type of an AST expression."""
    if expr is None:
        return "Object"
    if isinstance(expr, (int, float)):
        return "Double" if isinstance(expr, float) else "Integer"
    if isinstance(expr, bool):
        return "Boolean"
    if isinstance(expr, str):
        return "Object"
    if not isinstance(expr, dict):
        return "Object"

    for key, val in expr.items():
        if key in ("PlVariable", "ColumnRef"):
            parts = val if isinstance(val, list) else [val]
            name = parts[-1] if parts else ""
            # Multi-part: composite/ROWTYPE/RECORD field access
            if len(parts) >= 2:
                var_name_raw = parts[0]
                field_name = parts[-1]
                if var_name_raw in proc.local_vars:
                    var_type = proc.local_vars[var_name_raw]
                    if var_type == "Map<String, Object>":
                        return _java_type_from_field_name(field_name)
                # RECORD loop variable or undeclared Map — infer from field name
                return _java_type_from_field_name(field_name)
            if name in proc.local_vars:
                return proc.local_vars[name]
            for p in proc.parameters:
                if p.name.lower() == name.lower():
                    return p.java_type
            return "Object"
        elif key == "Literal":
            if isinstance(val, dict):
                if "String" in val:
                    return "String"
                if "Integer" in val:
                    return "Integer"
                if "Float" in val:
                    return "Double"
                if "Boolean" in val:
                    return "Boolean"
                if "Null" in val:
                    return "Object"
            return "Object"
        elif key == "FunctionCall":
            name_parts = val.get("name", [])
            func_name = name_parts[-1].lower() if name_parts else ""
            if func_name in _STRING_FUNC_RETURN:
                return "String"
            if func_name in _NUMERIC_FUNC_RETURN_INT:
                return "Integer"
            if func_name in _NUMERIC_FUNC_RETURN_DOUBLE:
                return "Double"
            if func_name in _NUMERIC_FUNC_RETURN_LONG:
                return "Long"
            return "Object"
        elif key == "SpecialFunction":
            func_name = val.get("name", "").lower()
            if func_name in ("substr", "substring", "overlay", "trim",
                             "convert", "replace", "to_char"):
                return "String"
            if func_name in ("position", "extract"):
                return "Integer"
            if func_name in ("current_timestamp", "current_time"):
                return "java.sql.Timestamp"
            if func_name == "group_concat":
                return "String"
            return "Object"
        elif key == "UnaryOp":
            return _infer_expr_type(val.get("expr"), proc)
        elif key == "BinaryOp":
            # For arithmetic ops, infer from operands
            left_type = _infer_expr_type(val.get("left"), proc)
            right_type = _infer_expr_type(val.get("right"), proc)
            op = val.get("op", "")
            if op in ("+", "-", "*", "/"):
                if "BigDecimal" in left_type or "BigDecimal" in right_type:
                    return "java.math.BigDecimal"
                if "Double" in left_type or "Double" in right_type:
                    return "Double"
                if "Long" in left_type or "Long" in right_type:
                    return "Long"
                return "Integer"
            return "Object"
    return "Object"




def _is_numeric_literal(expr) -> bool:
    if not isinstance(expr, dict):
        return False
    for key, val in expr.items():
        if key == "Literal":
            if isinstance(val, dict):
                return "Integer" in val or "Float" in val
    return False


def _is_numeric_literal_expr(java_str: str) -> bool:
    """Check if a Java expression string looks like a numeric literal."""
    s = java_str.strip()
    if not s:
        return False
    try:
        float(s.rstrip('dDfFlL'))
        return True
    except ValueError:
        return False


def _is_integer_literal(expr, value=None) -> bool:
    if not isinstance(expr, dict):
        return False
    for key, val in expr.items():
        if key == "Literal":
            if isinstance(val, dict) and "Integer" in val:
                if value is None:
                    return True
                return val["Integer"] == value
    return False


def _get_out_param(expr, proc: ProcedureInfo):
    if proc is None or not isinstance(expr, dict):
        return None
    for key, val in expr.items():
        if key in ("PlVariable", "ColumnRef"):
            parts = val if isinstance(val, list) else [val]
            name = parts[-1] if parts else ""
            for p in proc.parameters:
                if p.name.lower() == name.lower() and p.is_out:
                    return p
    return None


def _infer_target_type(target_name: str, proc: ProcedureInfo) -> str:
    for var_name, var_type in proc.local_vars.items():
        if snake_to_camel(var_name) == target_name:
            return var_type
    for p in proc.parameters:
        if p.java_name == target_name:
            return p.java_type
    return "Object"




def _expr_to_java(expr, proc: ProcedureInfo = None, as_read: bool = True, all_packages: dict = None) -> str:
    """Convert an AST expression to Java code."""
    if expr is None:
        return "null"
    if isinstance(expr, str):
        upper = expr.upper()
        if upper == "SYSDATE":
            return "new java.sql.Date(System.currentTimeMillis())"
        if upper == "LOCALTIMESTAMP":
            return "java.sql.Timestamp.valueOf(java.time.LocalDateTime.now())"
        if upper in ("CURRENT_TIMESTAMP", "NOW"):
            return "new java.sql.Timestamp(System.currentTimeMillis())"
        if upper == "CURRENT_DATE":
            return "new java.sql.Date(System.currentTimeMillis())"
        return expr
    if isinstance(expr, (int, float)):
        return str(expr)
    if isinstance(expr, bool):
        return "true" if expr else "false"

    if not isinstance(expr, dict):
        return str(expr)

    for key, val in expr.items():
        if key == "ColumnRef":
            parts = val if isinstance(val, list) else [val]
            name = parts[-1] if parts else ""
            upper = name.upper()
            if upper == "FOUND":
                return "found"
            if upper == "SQLERRM":
                return "e.getMessage()"
            if upper in ("CURRENT_TIMESTAMP", "NOW"):
                return "new java.sql.Timestamp(System.currentTimeMillis())"
            if upper in ("CURRENT_DATE", "SYSDATE"):
                return "new java.sql.Date(System.currentTimeMillis())"
            if upper == "LOCALTIMESTAMP":
                return "java.sql.Timestamp.valueOf(java.time.LocalDateTime.now())"
            if upper == "LOG_LEVEL_FILTER":
                return '"3"'
            # NEW: Detect sequence.NEXTVAL / sequence.CURRVAL
            if len(parts) >= 2:
                second_last = parts[-2].upper()
                if second_last in ("NEXTVAL", "CURRVAL"):
                    seq_name = "_".join(parts[:-1])
                    if second_last == "NEXTVAL":
                        return f'/* NEXTVAL: use sequence query for {seq_name} */ null'
                    else:
                        return f'/* CURRVAL: use sequence query for {seq_name} */ null'
            # Multi-part ColumnRef: composite/ROWTYPE/RECORD field access
            # e.g. ["v_emp", "status"] → vEmp.get("status")
            if len(parts) >= 2 and proc is not None:
                var_name_raw = parts[0]
                field_name = parts[-1]
                var_java = snake_to_camel(var_name_raw)
                var_type = proc.local_vars.get(var_name_raw, "")
                if var_type in ("Map<String, Object>", "Object") or var_name_raw not in proc.local_vars:
                    field_key = field_name.lower()
                    if not as_read:
                        return f'__MAP_PUT__{var_java}__{field_key}'
                    return f'{var_java}.get("{field_key}")'
            java_name = snake_to_camel(name)
            if as_read and proc is not None:
                for p in proc.parameters:
                    if p.is_out and p.java_name == java_name:
                        java_name = f"{java_name}.get()"
                        break
            return java_name
        elif key == "PlVariable":
            parts = val if isinstance(val, list) else [val]
            name = parts[-1] if parts else ""
            upper = name.upper()
            if upper == "SQLERRM":
                return "e.getMessage()"
            if upper in ("CURRENT_TIMESTAMP", "NOW"):
                return "new java.sql.Timestamp(System.currentTimeMillis())"
            if upper in ("CURRENT_DATE", "SYSDATE"):
                return "new java.sql.Date(System.currentTimeMillis())"
            if upper == "LOCALTIMESTAMP":
                return "java.sql.Timestamp.valueOf(java.time.LocalDateTime.now())"
            java_name = snake_to_camel(name)
            if as_read and proc is not None:
                for p in proc.parameters:
                    if p.is_out and p.java_name == java_name:
                        java_name = f"{java_name}.get()"
                        break
            return java_name
        elif key == "Literal":
            return _literal_to_java(val)
        elif key == "BinaryOp":
            left = _expr_to_java(val.get("left", {}), proc)
            right = _expr_to_java(val.get("right", {}), proc)
            op = val.get("op", "")

            if proc is not None:
                left_type = _infer_expr_type(val.get("left"), proc)
                right_type = _infer_expr_type(val.get("right"), proc)

                left_out = _get_out_param(val.get("left"), proc)
                right_out = _get_out_param(val.get("right"), proc)

                if left_out and left_out.java_type == "String" and op in (">", "<", ">=", "<=", "=", "<>"):
                    left = f"Long.valueOf({left})"
                    left_type = "Long"
                if right_out and right_out.java_type == "String" and op in (">", "<", ">=", "<=", "=", "<>"):
                    right = f"Long.valueOf({right})"
                    right_type = "Long"

                # Cast .get() field access results for typed comparisons
                if ".get(" in left and "BigDecimal" in left_type:
                    left = f"((java.math.BigDecimal) {left})"
                elif ".get(" in left and left_type == "Integer":
                    left = f"((Integer) {left})"
                if ".get(" in right and "BigDecimal" in right_type:
                    right = f"((java.math.BigDecimal) {right})"
                elif ".get(" in right and right_type == "Integer":
                    right = f"((Integer) {right})"

                is_bd = "BigDecimal" in left_type or "BigDecimal" in right_type
                is_str = left_type == "String" or right_type == "String"

                if is_bd and op in (">", "<", ">=", "<=", "=", "<>"):
                    cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                    if _is_numeric_literal(val.get("right")):
                        right = f"java.math.BigDecimal.valueOf({right})"
                    return f"{left}.compareTo({right}) {cmp_map[op]} 0"

                if is_str and not is_bd and op in (">", "<", ">=", "<="):
                    cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">="}
                    return f"{left}.compareTo({right}) {cmp_map[op]} 0"

                if ("Long" in left_type or "Long" in right_type) and op in (">", "<", ">=", "<=", "=", "<>"):
                    cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                    if _is_numeric_literal(val.get("right")):
                        right = f"Long.valueOf({right})"
                    elif _is_numeric_literal(val.get("left")):
                        left = f"Long.valueOf({left})"
                    return f"{left}.compareTo({right}) {cmp_map[op]} 0"

                if is_bd and op in ("+", "-", "*", "/"):
                    arith_map = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
                    method = arith_map[op]
                    if _is_numeric_literal(val.get("right")):
                        right = f"java.math.BigDecimal.valueOf({right})"
                    elif "BigDecimal" not in right_type:
                        right = f"java.math.BigDecimal.valueOf({right})"
                    return f"{left}.{method}({right})"

            java_op = _java_op(op)
            if op == "=" and _is_string_comparison(val):
                return f"{left}.equals({right})"
            elif op == "<>" and _is_string_comparison(val):
                return f"!{left}.equals({right})"
            return f"{left} {java_op} {right}"
        elif key == "UnaryOp":
            operand = _expr_to_java(val.get("expr", {}), proc)
            op = val.get("op", "")
            java_op = _java_op(op)
            if java_op == "!":
                return f"!{operand}"
            return f"{java_op}{operand}"
        elif key == "InList":
            expr_java = _expr_to_java(val.get("expr", {}), proc)
            items = val.get("list", [])
            items_java = [_expr_to_java(item, proc) for item in items]
            items_str = ", ".join(items_java)
            negated = val.get("negated", False)
            if negated:
                return f"!Arrays.asList({items_str}).contains({expr_java})"
            return f"Arrays.asList({items_str}).contains({expr_java})"
        elif key == "FunctionCall":
            name_parts = val.get("name", [])
            func_name = name_parts[-1] if name_parts else "unknown"
            func_name_lower = func_name.lower()
            args_java = [_expr_to_java(a, proc) for a in val.get("args", [])]
            args_str = ", ".join(args_java)

            if func_name_lower in SQL_FUNCTION_MAP:
                mapped = SQL_FUNCTION_MAP[func_name_lower]
                if func_name_lower == "coalesce" and len(args_java) >= 2:
                    first_type = _infer_expr_type(val.get("args", [{}])[0], proc) if val.get("args") else "Object"
                    if "BigDecimal" in first_type:
                        args_java = [(a if a != "0" else "java.math.BigDecimal.ZERO") for a in args_java]
                        args_str = ", ".join(args_java)
                tpl_args = {
                    "args": args_str,
                    "args0": args_java[0] if len(args_java) > 0 else "",
                    "args1": args_java[1] if len(args_java) > 1 else "",
                    "args2": args_java[2] if len(args_java) > 2 else "",
                }
                if mapped == "__HANDLER__":
                    return _handle_function(func_name_lower, args_java, proc)
                elif mapped.startswith("__EXPR__"):
                    result = mapped[8:]
                    for k, v in tpl_args.items():
                        result = result.replace("{" + k + "}", v)
                    return result
                elif mapped == "__SKIP__":
                    return args_java[0] if args_java else "null"
                elif any("{" + k + "}" in mapped for k in tpl_args):
                    for k, v in tpl_args.items():
                        mapped = mapped.replace("{" + k + "}", v)
                    return mapped
                elif "(" in mapped:
                    return f"{mapped}({args_str})"
                else:
                    return f"{mapped}({args_str})"
            else:
                # Determine if this is a self-call (same package) for param-type-aware wrapping
                self_call_pkg = None
                self_call_func = func_name
                if proc and len(name_parts) == 1:
                    self_call_pkg = proc.package
                elif proc and len(name_parts) >= 2:
                    candidate_pkg = name_parts[-2]
                    matched = _find_registered_pkg(candidate_pkg, all_packages)
                    if matched and matched.lower() == proc.package.lower():
                        self_call_pkg = proc.package
                        self_call_func = name_parts[-1]
                    else:
                        # Cross-package call - also check target proc params for .get() args
                        if matched:
                            target_proc = _find_target_proc(matched, name_parts[-1], all_packages)
                            if target_proc:
                                method = java_method_name(name_parts[-1])
                                wrapped_args = []
                                for i, a_java in enumerate(args_java):
                                    if i < len(target_proc.parameters):
                                        target_type = target_proc.parameters[i].java_type
                                        if ".get(" in a_java and target_type not in ("Object", "Map<String, Object>"):
                                            if target_type == "long":
                                                wrapped_args.append(f"((Number) {a_java}).longValue()")
                                            elif target_type == "Long":
                                                wrapped_args.append(f"((Number) {a_java}).longValue()")
                                            elif target_type == "int":
                                                wrapped_args.append(f"((Number) {a_java}).intValue()")
                                            elif target_type == "Integer":
                                                wrapped_args.append(f"((Number) {a_java}).intValue()")
                                            elif "BigDecimal" in target_type:
                                                wrapped_args.append(f"((java.math.BigDecimal) {a_java})")
                                            elif target_type == "String":
                                                wrapped_args.append(f"(String) {a_java}")
                                            else:
                                                wrapped_args.append(f"({target_type}) {a_java}")
                                        else:
                                            wrapped_args.append(a_java)
                                    else:
                                        wrapped_args.append(a_java)
                                svc_name = f"{package_to_classname(matched).lower()}Service"
                                proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched))
                                return f"{svc_name}.{method}({', '.join(wrapped_args)})"
                if self_call_pkg:
                    target_proc = _find_target_proc(self_call_pkg, self_call_func, all_packages)
                    if target_proc:
                        method = java_method_name(self_call_func)
                        wrapped_args = []
                        for i, a_java in enumerate(args_java):
                            if i < len(target_proc.parameters):
                                target_type = target_proc.parameters[i].java_type
                                if "BigDecimal" in target_type and _is_numeric_literal_expr(a_java):
                                    wrapped_args.append(f"java.math.BigDecimal.valueOf({a_java})")
                                elif ".get(" in a_java and target_type not in ("Object", "Map<String, Object>"):
                                    # RECORD field access returns Object; cast to target param type
                                    if target_type == "long":
                                        wrapped_args.append(f"((Number) {a_java}).longValue()")
                                    elif target_type == "Long":
                                        wrapped_args.append(f"((Number) {a_java}).longValue()")
                                    elif target_type == "int":
                                        wrapped_args.append(f"((Number) {a_java}).intValue()")
                                    elif target_type == "Integer":
                                        wrapped_args.append(f"((Number) {a_java}).intValue()")
                                    elif "BigDecimal" in target_type:
                                        wrapped_args.append(f"((java.math.BigDecimal) {a_java})")
                                    elif target_type == "String":
                                        wrapped_args.append(f"(String) {a_java}")
                                    else:
                                        wrapped_args.append(f"({target_type}) {a_java}")
                                else:
                                    wrapped_args.append(a_java)
                            else:
                                wrapped_args.append(a_java)
                        return f"this.{method}({', '.join(wrapped_args)})"
                _record_unsupported(func_name, proc)
                return f"/* TODO: implement {func_name}({args_str}) */ ({snake_to_camel(func_name)}(/* {args_str} */))"
        elif key == "SpecialFunction":
            func_name = val.get("name", "").lower()
            handler = SPECIAL_FUNCTION_MAP.get(func_name)
            if handler:
                return handler(val, proc, _expr_to_java)
            _record_unsupported(func_name, proc, is_special=True)
            args_java = [_expr_to_java(a, proc) for a in val.get("args", [])]
            return f"/* UNSUPPORTED: {func_name} — special syntax, no Java mapping */"
        elif key == "IsNull":
            inner = _expr_to_java(val.get("expr", {}), proc)
            negated = val.get("negated", False)
            if negated:
                return f"{inner} != null"
            return f"{inner} == null"
        elif key == "Parenthesized":
            return f"({_expr_to_java(val, proc)})"
        elif key == "Expr":
            if isinstance(val, list) and len(val) >= 1:
                return _expr_to_java(val[0], proc)
            return _expr_to_java(val, proc)

    return str(expr)


def _is_string_comparison(binary_op: dict) -> bool:
    right = binary_op.get("right", {})
    if isinstance(right, dict):
        for k, v in right.items():
            if k == "Literal" and isinstance(v, dict) and "String" in v:
                return True
    return False


def _literal_to_java(lit) -> str:
    if isinstance(lit, dict):
        for key, val in lit.items():
            if key == "String":
                return f'"{val}"'
            elif key == "Integer":
                return str(val)
            elif key == "Float":
                return f"{val}d"
            elif key == "Boolean":
                return "true" if val else "false"
            elif key == "Null":
                return "null"
    return str(lit)


def _java_op(sql_op: str) -> str:
    """Convert SQL operator to Java operator."""
    ops = {
        "=": "==", "<>": "!=", "!=": "!=",
        ">": ">", "<": "<", ">=": ">=", "<=": "<=",
        "AND": "&&", "OR": "||", "NOT": "!",
        "+": "+", "-": "-", "*": "*", "/": "/",
        "||": "+", "LIKE": "LIKE",
        "IS NULL": "== null", "IS NOT NULL": "!= null",
    }
    return ops.get(sql_op, sql_op)


# ── SQL Reconstruction Helpers ─────────────────────────────────

def _extract_table_names(from_clause: list) -> list:
    tables = []
    for item in from_clause:
        for key, val in item.items():
            if key == "Table":
                name = val.get("name", [])
                tables.append(name[-1] if name else "unknown")
            elif key in ("Join", "LeftJoin", "RightJoin", "CrossJoin"):
                # Recurse for join tables
                pass
    return tables


def _extract_table_names_from_insert(insert_data: dict) -> list:
    table = insert_data.get("table", [])
    return [table[-1]] if table else ["unknown"]


def _extract_table_names_from_update(update_data: dict) -> list:
    tables = update_data.get("tables", [])
    result = []
    for t in tables:
        for k, v in t.items():
            if k == "Table":
                name = v.get("name", [])
                result.append(name[-1] if name else "unknown")
    return result


def _extract_table_name_from_dml(dml_data: dict) -> str:
    for key in ("table", "tables"):
        val = dml_data.get(key)
        if val:
            if isinstance(val, list):
                if val and isinstance(val[0], str):
                    return val[-1]
            elif isinstance(val, list):
                for item in val:
                    for k, v in item.items():
                        if k == "Table":
                            name = v.get("name", [])
                            return name[-1] if name else "unknown"
    return "unknown"


def _extract_into_variable(into_targets: list) -> Optional[str]:
    if not into_targets:
        return None
    first = into_targets[0]
    for k, v in first.items():
        if k == "Expr" and isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    for ik, iv in item.items():
                        if ik in ("PlVariable", "ColumnRef"):
                            if isinstance(iv, list):
                                return iv[-1]
                            return iv
    return None


def _extract_all_into_variables(into_targets: list) -> list:
    result = []
    for target in into_targets:
        for k, v in target.items():
            if k == "Expr" and isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for ik, iv in item.items():
                            if ik in ("PlVariable", "ColumnRef"):
                                name = iv[-1] if isinstance(iv, list) else iv
                                result.append(name)
    return result


def _extract_all_into_targets(into_targets: list) -> list:
    result = []
    for target in into_targets:
        for k, v in target.items():
            if k == "Expr" and isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for ik, iv in item.items():
                            if ik in ("PlVariable", "ColumnRef"):
                                if isinstance(iv, list) and len(iv) >= 2:
                                    result.append((iv[-1], list(iv)))
                                elif isinstance(iv, str):
                                    result.append((iv, [iv]))
    return result


def _build_param_args(params: list) -> str:
    return ", ".join(p.java_name for p in params if not p.is_out)


# ── Code Generation ────────────────────────────────────────────

def generate_project(output_dir: str, packages: list, changed_packages: set = None,
                     config: dict = None, progress_cb=None):
    if changed_packages is not None and not changed_packages:
        return
    base_path = Path(output_dir)
    base_path.mkdir(parents=True, exist_ok=True)

    if not (base_path / "pom.xml").exists():
        _write_pom_xml(base_path)
        _write_application_yml(base_path, config)
        _write_main_application(base_path)
        _write_business_exception(base_path)

    svc_method_param_counts: dict = {}
    all_packages = {p.package_name: p for p in packages}
    for p in packages:
        svc_var = f"{package_to_classname(p.package_name)[0].lower()}{package_to_classname(p.package_name)[1:]}Service"
        for proc in p.procedures:
            mname = java_method_name(proc.proc_name)
            in_count = sum(1 for param in proc.parameters if not param.is_out)
            out_count = sum(1 for param in proc.parameters if param.is_out)
            svc_method_param_counts[(svc_var, mname)] = (in_count + out_count, proc.is_function)

    active_pkgs = [pkg for pkg in packages
                   if changed_packages is None or pkg.package_name in changed_packages]
    n_gen = len(active_pkgs)
    for idx, pkg in enumerate(active_pkgs, 1):
        if progress_cb:
            progress_cb("pkg", idx, n_gen, pkg.package_name)
        service_injections = _collect_service_injections(pkg)

        _write_mapper_interface(base_path, pkg)
        _write_mapper_xml(base_path, pkg)
        _write_service_class(base_path, pkg, service_injections, all_packages)
        service_injections = _collect_service_injections(pkg)
        _write_service_test(base_path, pkg, service_injections, svc_method_param_counts, all_packages)


def _collect_service_injections(pkg: PackageInfo) -> dict:
    services = {}
    for proc in pkg.procedures:
        for call in proc.service_calls:
            if call.service_name not in services:
                services[call.service_name] = call.package_name
    return services


def _write_pom_xml(base_path: Path):
    core_deps = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <project xmlns="http://maven.apache.org/POM/4.0.0"
                 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                 https://maven.apache.org/xsd/maven-4.0.0.xsd">
            <modelVersion>4.0.0</modelVersion>

            <parent>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-starter-parent</artifactId>
                <version>3.2.5</version>
                <relativePath/>
            </parent>

            <groupId>com.example</groupId>
            <artifactId>demo</artifactId>
            <version>0.0.1-SNAPSHOT</version>
            <name>demo</name>

            <properties>
                <java.version>17</java.version>
            </properties>

            <dependencies>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-web</artifactId>
                </dependency>
                <dependency>
                    <groupId>org.mybatis.spring.boot</groupId>
                    <artifactId>mybatis-spring-boot-starter</artifactId>
                    <version>3.0.3</version>
                </dependency>
                <dependency>
                    <groupId>org.postgresql</groupId>
                    <artifactId>postgresql</artifactId>
                    <scope>runtime</scope>
                </dependency>
                <dependency>
                    <groupId>org.projectlombok</groupId>
                    <artifactId>lombok</artifactId>
                    <optional>true</optional>
                </dependency>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-test</artifactId>
                    <scope>test</scope>
                </dependency>""")

    build_section = textwrap.dedent("""\
            </dependencies>

            <build>
                <plugins>
                    <plugin>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot-maven-plugin</artifactId>
                        <configuration>
                            <excludes>
                                <exclude>
                                    <groupId>org.projectlombok</groupId>
                                    <artifactId>lombok</artifactId>
                                </exclude>
                            </excludes>
                        </configuration>
                    </plugin>
                    <plugin>
                        <groupId>org.apache.maven.plugins</groupId>
                        <artifactId>maven-surefire-plugin</artifactId>
                        <configuration>
                            <argLine>-Dnet.bytebuddy.experimental=true</argLine>
                        </configuration>
                    </plugin>
                </plugins>
            </build>
        </project>
    """)

    logger_cfg = _get_logger_config()
    logger_deps = ""
    for dep_xml in logger_cfg.get("pom", []):
        logger_deps += f"\n                {dep_xml}"

    content = core_deps + logger_deps + build_section
    (base_path / "pom.xml").write_text(content)


def _write_application_yml(base_path: Path, config: dict = None):
    db = (config or {}).get("database", {})
    url = db.get("url", "jdbc:postgresql://localhost:5432/demo")
    username = db.get("username", "postgres")
    password = db.get("password", "postgres")
    driver = db.get("driver", "org.postgresql.Driver")
    content = textwrap.dedent(f"""\
        spring:
          datasource:
            url: {url}
            username: {username}
            password: {password}
            driver-class-name: {driver}

        mybatis:
          mapper-locations: classpath:mapper/*.xml
          configuration:
            map-underscore-to-camel-case: true
    """)
    res_dir = base_path / RESOURCES_DIR
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "application.yml").write_text(content)


def _write_main_application(base_path: Path):
    java_dir = base_path / BASE_DIR
    java_dir.mkdir(parents=True, exist_ok=True)

    content = textwrap.dedent(f"""\
        package {BASE_PACKAGE};

        import org.mybatis.spring.annotation.MapperScan;
        import org.springframework.boot.SpringApplication;
        import org.springframework.boot.autoconfigure.SpringBootApplication;

        @SpringBootApplication
        @MapperScan("{BASE_PACKAGE}.mapper")
        public class DemoApplication {{
            public static void main(String[] args) {{
                SpringApplication.run(DemoApplication.class, args);
            }}
        }}
    """)
    (java_dir / "DemoApplication.java").write_text(content)


def _write_business_exception(base_path: Path):
    java_dir = base_path / BASE_DIR
    (java_dir / "exception").mkdir(parents=True, exist_ok=True)

    content = textwrap.dedent(f"""\
        package {BASE_PACKAGE}.exception;

        public class BusinessException extends RuntimeException {{
            public BusinessException(String message) {{
                super(message);
            }}

            public BusinessException(String message, Throwable cause) {{
                super(message, cause);
            }}
        }}
    """)
    (java_dir / "exception" / "BusinessException.java").write_text(content)


def _write_mapper_interface(base_path: Path, pkg: PackageInfo):
    """Generate MyBatis Mapper interface."""
    java_dir = base_path / _pkg_base_dir(pkg) / "mapper"
    java_dir.mkdir(parents=True, exist_ok=True)
    class_name = f"{package_to_classname(pkg.package_name)}Mapper"

    methods = []
    imports = set()
    for proc in pkg.procedures:
        for dml in proc.dml_statements:
            method_sig = _build_mapper_method(proc, dml, imports)
            methods.append(method_sig)

    if not methods:
        methods = [f"// No direct DML operations for {pkg.package_name}"]

    imports_str = "\n".join(sorted(imports)) + "\n" if imports else ""
    methods_str = "\n".join(methods)
    indented_methods = _indent(methods_str, 1)

    content = textwrap.dedent(f"""\
        package {_pkg_java_package(pkg)}.mapper;

        {imports_str}
        import org.apache.ibatis.annotations.*;

        @Mapper
        public interface {class_name} {{

        {indented_methods}
        }}
    """)
    (java_dir / f"{class_name}.java").write_text(content)


def _build_mapper_method(proc: ProcedureInfo, dml: DmlStatement, imports: set) -> str:
    """Build a single mapper method signature."""
    method_name = dml.method_id

    # Build parameter list
    params = []
    for p in proc.parameters:
        if p.is_out:
            continue
        params.append(f"@Param(\"{p.java_name}\") {p.java_type} {p.java_name}")

    params_str = ", ".join(params) if params else ""

    # Determine return type
    if dml.sql_type == "select":
        if dml.returns_list:
            ret = "List<Map<String, Object>>"
            imports.add("import java.util.List;")
            imports.add("import java.util.Map;")
        elif dml.result_type and dml.result_type == "Integer":
            ret = "Integer"
        elif dml.result_type and dml.result_type != "Map<String, Object>":
            ret = dml.result_type
            if not is_simple_java_type(ret):
                imports.add(f"import {ret};")
        else:
            ret = "Map<String, Object>"
            imports.add("import java.util.Map;")
    elif dml.sql_type == "insert":
        ret = "int"
    elif dml.sql_type == "update":
        ret = "int"
    elif dml.sql_type == "delete":
        ret = "int"
    else:
        ret = "void"

    source_info = f"// {proc.source_file}:{proc.source_start_line} — {proc.name}" if proc.source_file else ""
    comment_lines = ""
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            comment_lines += f"    {formatted}\n"
    prefix = f"    {source_info}\n{comment_lines}    " if source_info else f"{comment_lines}    "
    return f"{prefix}{ret} {method_name}({params_str});"


def _write_mapper_xml(base_path: Path, pkg: PackageInfo):
    """Generate MyBatis XML mapper file."""
    mapper_dir = base_path / RESOURCES_DIR / "mapper"
    mapper_dir.mkdir(parents=True, exist_ok=True)

    namespace = f"{_pkg_java_package(pkg)}.mapper.{package_to_classname(pkg.package_name)}Mapper"

    statements = []
    for proc in pkg.procedures:
        for dml in proc.dml_statements:
            stmt_xml = _build_mapper_statement(proc, dml)
            statements.append(stmt_xml)

    stmts_xml = "\n\n".join(statements) if statements else f"<!-- No statements for {pkg.package_name} -->"

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"')
    lines.append('        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">')
    lines.append(f'<mapper namespace="{namespace}">')
    lines.append("")
    for i, stmt in enumerate(statements):
        if i > 0:
            lines.append("")
        for stmt_line in stmt.split("\n"):
            lines.append(f"    {stmt_line}")
    lines.append("")
    lines.append("</mapper>")
    content = "\n".join(lines) + "\n"
    (mapper_dir / f"{package_to_classname(pkg.package_name)}Mapper.xml").write_text(content)


def _clean_sql(sql: str) -> str:
    sql = re.sub(r'\s*\.\s*', '.', sql)
    sql = re.sub(r'\s*\(\s*', '(', sql)
    sql = re.sub(r'\s*\)', ')', sql)
    sql = re.sub(r' {2,}', ' ', sql)
    return sql.strip()


def _format_sql(sql: str) -> str:
    try:
        result = subprocess.run(
            [OGSQL_BIN, "format"],
            input=sql, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return sql


def _build_mapper_statement(proc: ProcedureInfo, dml: DmlStatement) -> str:
    sql = _clean_sql(dml.sql_text.strip())
    if sql.endswith(";"):
        sql = sql[:-1]

    param_placeholders = []
    def _protect(m):
        param_placeholders.append(m.group(0))
        return f"__PH{len(param_placeholders) - 1}__"

    sql = re.sub(r'#\{[^}]+\}', _protect, sql)
    sql = _format_sql(sql)

    for i, ph in enumerate(param_placeholders):
        sql = sql.replace(f"__PH{i}__", ph)

    sql = _convert_params_to_mybatis(sql, proc.parameters, proc.local_vars)

    result_type_attr = ""
    if dml.sql_type == "select":
        if dml.returns_list:
            result_type_attr = ' resultType="java.util.LinkedHashMap"'
        elif dml.result_type and dml.result_type == "Integer":
            result_type_attr = ' resultType="int"'
        elif dml.result_type and dml.result_type != "Map<String, Object>":
            if is_simple_java_type(dml.result_type):
                result_type_attr = f' resultType="{dml.result_type.lower()}"'
            else:
                result_type_attr = f' resultType="{dml.result_type}"'
        else:
            result_type_attr = ' resultType="java.util.LinkedHashMap"'

    tag = dml.sql_type
    params_attrs = ""
    if proc.parameters:
        param_types = set(p.java_type for p in proc.parameters if not p.is_out)
        if len(param_types) == 1:
            params_attrs = f' parameterType="{list(param_types)[0].lower()}"'

    filter_line = ""
    if dml.optional_filters:
        filter_map = ", ".join(f"{f['param']} -> {f['column']}" for f in dml.optional_filters)
        filter_line = f'<!-- Optional filters: {filter_map}. Consider using <if test="..."> in MyBatis -->'

    formatted_sql = "\n".join(f"    {line}" for line in sql.split("\n"))

    xml_parts = []
    source_info = f"Source: {proc.source_file}:{proc.source_start_line}-{proc.source_end_line} — {proc.name}.{dml.method_id}" if proc.source_file else f"Source: {proc.name}.{dml.method_id}"
    xml_parts.append(f"<!-- {source_info} -->")
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            xml_parts.append(f"<!-- {formatted.lstrip('/ ').strip()} -->")
    if filter_line:
        xml_parts.append(filter_line)
    xml_parts.append(f'<{tag} id="{dml.method_id}"{params_attrs}{result_type_attr}>')
    xml_parts.append(formatted_sql)
    xml_parts.append(f'</{tag}>')
    return "\n".join(xml_parts)


def _convert_params_to_mybatis(sql: str, params: list, local_vars: dict = None) -> str:
    """Convert SQL parameter references to MyBatis #{{paramName}} syntax."""
    for p in params:
        sql = re.sub(
            rf'\b{re.escape(p.name)}\b',
            f'#{{{p.java_name}}}',
            sql,
            flags=re.IGNORECASE
        )
    if local_vars:
        for var_name in local_vars:
            java_name = snake_to_camel(var_name)
            sql = re.sub(
                rf'\b{re.escape(var_name)}\b',
                f'#{{{java_name}}}',
                sql,
                flags=re.IGNORECASE
            )
    return sql


def _write_service_class(base_path: Path, pkg: PackageInfo, service_injections: dict, all_packages: dict = None):
    """Generate Spring Service class."""
    java_dir = base_path / _pkg_base_dir(pkg) / "service"
    java_dir.mkdir(parents=True, exist_ok=True)
    class_name = f"{package_to_classname(pkg.package_name)}Service"
    mapper_name = f"{package_to_classname(pkg.package_name)[0].lower()}{package_to_classname(pkg.package_name)[1:]}Mapper"

    # Imports
    all_imports = set()
    all_imports.add(f"import {_pkg_java_package(pkg)}.mapper.{package_to_classname(pkg.package_name)}Mapper;")
    all_imports.add(f"import {BASE_PACKAGE}.exception.BusinessException;")
    logger_cfg = _get_logger_config()
    for imp in logger_cfg["imports"]:
        all_imports.add(imp)
    all_imports.add("import org.springframework.stereotype.Service;")
    all_imports.add("import org.springframework.transaction.annotation.Transactional;")

    for proc in pkg.procedures:
        all_imports.update(proc.imports)
        for p in proc.parameters:
            if not is_simple_java_type(p.java_type):
                all_imports.add(f"import {p.java_type};")

    # Service injection for cross-package calls
    _all_pkgs = all_packages or {}
    for svc_var, pkg_name in service_injections.items():
        if pkg_name:
            svc_class = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var.replace("Service", "")
            svc_class = f"{package_to_classname(svc_class_part)}Service"
            pkg_name = svc_class_part
        target_jp = _pkg_java_package(_all_pkgs[pkg_name]) if pkg_name in _all_pkgs else BASE_PACKAGE
        all_imports.add(f"import {target_jp}.service.{svc_class};")

    imports_str = "\n".join(sorted(all_imports))

    all_body_text = ""
    for proc in pkg.procedures:
        all_body_text += " ".join(proc.java_logic_lines) + " "
        for vn, vt in proc.local_vars.items():
            all_body_text += vt + " "
        if proc.body and proc.body.get("exception_block"):
            for handler in (proc.body.get("exception_block") or {}).get("handlers", []):
                for s in _iter_statements(handler.get("statements", [])):
                    for sk, sv in s.items():
                        all_body_text += str(sv) + " "

    if "List<" in all_body_text or "List<Map" in all_body_text:
        all_imports.add("import java.util.List;")
    if "Map<String" in all_body_text:
        all_imports.add("import java.util.Map;")
    if any("Map<String" in vt for proc in pkg.procedures for vn, vt in proc.local_vars.items()):
        all_imports.add("import java.util.Map;")
        all_imports.add("import java.util.HashMap;")
    if "AtomicReference<" in all_body_text or any(p.is_out for proc in pkg.procedures for p in proc.parameters):
        all_imports.add("import java.util.concurrent.atomic.AtomicReference;")
    if "Arrays.asList" in all_body_text:
        all_imports.add("import java.util.Arrays;")
    if "Objects.requireNonNullElse" in all_body_text:
        all_imports.add("import java.util.Objects;")

    imports_str = "\n".join(sorted(all_imports))

    methods = []
    for proc in pkg.procedures:
        method = _build_service_method(proc, mapper_name, all_packages)
        methods.append(method)

    service_injections = _collect_service_injections(pkg)

    for svc_var, pkg_name in service_injections.items():
        if pkg_name:
            svc_class = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var.replace("Service", "")
            svc_class = f"{package_to_classname(svc_class_part)}Service"
            pkg_name = svc_class_part
        target_jp = _pkg_java_package(_all_pkgs[pkg_name]) if pkg_name in _all_pkgs else BASE_PACKAGE
        all_imports.add(f"import {target_jp}.service.{svc_class};")

    lines = []
    lines.append(f"package {_pkg_java_package(pkg)}.service;")
    lines.append("")
    for imp in sorted(all_imports):
        lines.append(imp)
    lines.append("")
    lines.append("@Service")
    if pkg.source_file:
        lines.append(f"// Source: {pkg.source_file}")
    for c in pkg.comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            lines.append(formatted)
    lines.append(f"public class {class_name} {{")
    logger_cfg = _get_logger_config()
    lines.append(f"    {logger_cfg['declaration'].format(class_name=class_name)}")
    lines.append("")

    constructor_params = [f"{package_to_classname(pkg.package_name)}Mapper {mapper_name}"]
    constructor_assigns = [f"        this.{mapper_name} = {mapper_name};"]
    for svc_var, pkg_name in service_injections.items():
        if pkg_name:
            svc_class = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var.replace("Service", "")
            svc_class = f"{package_to_classname(svc_class_part)}Service"
        constructor_params.append(f"{svc_class} {svc_var}")
        constructor_assigns.append(f"        this.{svc_var} = {svc_var};")

    lines.append(f"    private final {package_to_classname(pkg.package_name)}Mapper {mapper_name};")
    for svc_var, pkg_name in service_injections.items():
        if pkg_name:
            svc_class = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var.replace("Service", "")
            svc_class = f"{package_to_classname(svc_class_part)}Service"
        lines.append(f"    private final {svc_class} {svc_var};")

    lines.append("")
    params_str = ", ".join(constructor_params)
    lines.append(f"    public {class_name}({params_str}) {{")
    for assign in constructor_assigns:
        lines.append(assign)
    lines.append("    }")

    # Emit package-level variables as static final fields
    if pkg.package_vars:
        lines.append("")
        for var_name, var_info in pkg.package_vars.items():
            java_name = snake_to_camel(var_name)
            java_type = var_info["java_type"]
            default = var_info.get("default")
            if default is not None:
                lines.append(f"    private static final {java_type} {java_name} = {default};")
            else:
                default_val = _default_for_type(java_type)
                lines.append(f"    private static final {java_type} {java_name} = {default_val};")

    for i, method in enumerate(methods):
        if i > 0:
            lines.append("")
        for mline in method.split("\n"):
            lines.append(mline)
    lines.append("}")
    content = "\n".join(lines) + "\n"
    (java_dir / f"{class_name}.java").write_text(content)


def _default_for_type(java_type: str) -> str:
    t = java_type.lower() if java_type else ""
    if "long" in t:
        return "0L"
    if "integer" in t or t == "int":
        return "0"
    if "bigdecimal" in t or "big_decimal" in t:
        return "java.math.BigDecimal.ZERO"
    if "double" in t:
        return "0.0d"
    if "float" in t:
        return "0.0f"
    if "boolean" in t:
        return "false"
    if t.startswith("map<"):
        return "new HashMap<>()"
    return "null"


def _format_comment_for_java(comment) -> str:
    """Format a SQL CommentInfo as a Java comment line."""
    text = comment.text
    if text.startswith('--'):
        text = text[2:].strip()
    elif text.startswith('/*') and text.endswith('*/'):
        text = text[2:-2].strip()
        text = ' '.join(line.strip() for line in text.split('\n') if line.strip())
    return f"// {text}" if text else ""


def _build_service_method(proc: ProcedureInfo, mapper_name: str, all_packages: dict = None) -> str:
    params = []
    out_params = []
    for p in proc.parameters:
        if p.is_out:
            if p.is_refcursor:
                # Skip REFCURSOR OUT params — they become the return value
                continue
            holder_type = f"AtomicReference<{p.java_type}>"
            params.append(f"{holder_type} {p.java_name}")
            out_params.append(p)
            proc.imports.add("import java.util.concurrent.atomic.AtomicReference;")
        else:
            param_type = _to_primitive_if_boxed(p.java_type)
            params.append(f"{param_type} {p.java_name}")

    params_str = ", ".join(params) if params else ""

    if proc.is_function:
        ret_type = sql_type_to_java(proc.return_type) if proc.return_type else "Object"
        if not is_simple_java_type(ret_type):
            proc.imports.add(f"import {ret_type};")
    else:
        # Check for REFCURSOR OUT — it becomes the return type
        refcursor_outs = [p for p in proc.parameters if p.is_out and p.is_refcursor]
        if refcursor_outs:
            ret_type = "List<Map<String, Object>>"
        else:
            ret_type = "void"

    method_name = java_method_name(proc.proc_name)

    body_lines = []

    out_java_names = {p.java_name for p in out_params}
    top_level_declares = set()
    top_level_insert_idx = 0
    for var_name, var_type in proc.local_vars.items():
        var_java = snake_to_camel(var_name)
        if var_java not in out_java_names:
            default_val = _default_for_type(var_type)
            body_lines.append(f"{var_type} {var_java} = {default_val};")
            top_level_declares.add(var_java)
            top_level_insert_idx = len(body_lines)

    for p in out_params:
        body_lines.append(f"{p.java_name}.set(null);")

    logic_text = " ".join(proc.java_logic_lines)

    needs_found = "found" in logic_text and "!found" in logic_text
    if needs_found:
        body_lines.append("boolean found = false;")

    exception_block = proc.body.get("exception_block") if proc.body else None

    for line in proc.java_logic_lines:
        body_lines.append(line)

    # Handle REFCURSOR OUT: return the cursor result
    refcursor_outs = [p for p in proc.parameters if p.is_out and p.is_refcursor]
    result_vars_to_hoist = set()
    for rc_out in refcursor_outs:
        rc_java = rc_out.java_name
        for cursor_name, meta in proc.open_cursors.items():
            if snake_to_camel(cursor_name) == rc_java:
                result_var = meta["result_var"]
                if result_var not in top_level_declares:
                    result_vars_to_hoist.add(result_var)
                body_lines.append(f"return {result_var};")
                break

    if result_vars_to_hoist:
        cleaned = []
        for line in body_lines:
            s = line.strip()
            modified = False
            for hv in result_vars_to_hoist:
                m = re.match(rf'^(List<Map<String, Object>>)\s+{re.escape(hv)}\s*=\s*(.*)', s)
                if m:
                    indent = line[:len(line) - len(line.lstrip())]
                    cleaned.append(f"{indent}{hv} = {m.group(2)}")
                    modified = True
                    break
            if not modified:
                cleaned.append(line)
        body_lines = cleaned
        insert_idx = 0
        for i, line in enumerate(body_lines):
            if not line.startswith("return") and not line.startswith("if") and not line.startswith("}"):
                insert_idx = i + 1
            else:
                break
        for hv in sorted(result_vars_to_hoist):
            body_lines.insert(insert_idx, f"List<Map<String, Object>> {hv} = null;")
            insert_idx += 1

    if not body_lines:
        body_lines.append("// Auto-generated from stored procedure")

    # Hoist local variable declarations before try-catch so they're visible in catch blocks
    hoisted_decls = []
    remaining_lines = []
    for line in body_lines:
        s = line.strip()
        if re.match(r'^(String|Long|Integer|BigDecimal|java\.math\.BigDecimal|AtomicReference|List<Map<String, Object>>|boolean|int|long|double|float)\s+\w+\s*=', s):
            hoisted_decls.append(line)
        else:
            remaining_lines.append(line)
    body_lines = remaining_lines

    if exception_block:
        handlers = exception_block.get("handlers", [])
        body_lines = _wrap_try_catch(body_lines, handlers, proc, all_packages)

    body_lines = hoisted_decls + body_lines

    body_lines = [line.replace("mapper.", f"{mapper_name}.") for line in body_lines]

    # Strip REFCURSOR OUT args from mapper calls (removed from method params)
    refcursor_out_java_names = {p.java_name for p in proc.parameters if p.is_out and p.is_refcursor}
    if refcursor_out_java_names:
        cleaned_lines = []
        for line in body_lines:
            for rc_name in refcursor_out_java_names:
                line = re.sub(rf',\s*{re.escape(rc_name)}\s*\)', ')', line)
                line = re.sub(rf'{re.escape(rc_name)}\s*,\s*', '', line)
            cleaned_lines.append(line)
        body_lines = cleaned_lines

    if ret_type != "void":
        body_lines = [line.replace("return;", "return null;") if line.strip() == "return;" else line for line in body_lines]

    has_complex_issues = _has_compilation_issues(body_lines, out_params, proc)
    if has_complex_issues:
        STUB_PROCEDURES.append(proc.name)
        body_lines = _generate_stub_body(proc, out_params)

    depth = 0
    indented = []
    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            indented.append('')
            continue

        closes = stripped.startswith('}')
        last_open = stripped.rfind('{')
        last_close = stripped.rfind('}')
        opens = last_open != -1 and last_open > last_close
        single_line = last_open != -1 and last_close != -1 and last_open < last_close

        if closes and not single_line:
            depth -= 1

        indented.append('        ' + '    ' * depth + stripped)

        if opens and not single_line:
            depth += 1

    body_str = '\n'.join(indented)

    has_dml = any(d.sql_type in ("insert", "update", "delete") for d in proc.dml_statements)

    method_lines = []
    source_info = f"{proc.source_file}:{proc.source_start_line}-{proc.source_end_line}" if proc.source_file else ""
    method_lines.append(f"    // Source: {proc.name} ({'FUNCTION' if proc.is_function else 'PROCEDURE'}) — {source_info}")
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            method_lines.append(f"    {formatted}")
    if has_complex_issues:
        method_lines.append("    // TODO: Complex PL/pgSQL pattern requires manual review")
        _record_todo("COMPLEX_REVIEW", proc, "generated code failed compilation checks → stub")
    if proc.is_autonomous:
        method_lines.append("    @Transactional(propagation = Propagation.REQUIRES_NEW)")
    elif has_dml:
        method_lines.append("    @Transactional")
    method_lines.append(f"    public {ret_type} {method_name}({params_str}) {{")
    method_lines.append(body_str)
    method_lines.append("    }")
    return "\n".join(method_lines)


def _has_compilation_issues(body_lines: list, out_params: list, proc: ProcedureInfo = None) -> bool:
    all_text = " ".join(body_lines)
    issues = 0
    if re.search(r'\bv_cursorResult\b', all_text) and "vCursorResult" not in all_text and "v_cursorResult =" not in all_text:
        issues += 1
    if re.search(r'AtomicReference.*<=', all_text):
        issues += 1

    out_java_names = {p.java_name for p in out_params}
    for line in body_lines:
        for name in out_java_names:
            # OUT param used in direct comparison (missing .get())
            if re.search(rf'\b{re.escape(name)}\s*(==|!=|<=|>=|<|>)', line):
                issues += 1
            # OUT param used without .get() or .set() accessor (e.g. Long.parseLong(totalNum))
            if re.search(rf'\b{re.escape(name)}\b', line):
                if not re.search(rf'\b{re.escape(name)}\s*\.\s*(get|set)\s*\(', line):
                    if 'AtomicReference<' not in line:
                        if not re.search(rf'\(\s*[^)]*\b{re.escape(name)}\b', line):
                            issues += 1

    # String OUT params: detect .set() with wrong argument types
    for p in out_params:
        if p.java_type == "String":
            # .set() with bare integer literal (catches catch-block outCode.set(1))
            if re.search(rf'\b{re.escape(p.java_name)}\.set\(\d+\)', all_text):
                issues += 1
            # .set() with mapper call returning Object (not String)
            if re.search(rf'\b{re.escape(p.java_name)}\.set\(\s*\w+Mapper\.', all_text):
                issues += 1

    if proc is not None:
        declared_result_vars = {meta["result_var"] for meta in proc.open_cursors.values()}
        local_var_java_names = {snake_to_camel(v) for v in proc.local_vars.keys()}
        param_java_names = {p.java_name for p in proc.parameters}
        known_names = declared_result_vars | local_var_java_names | param_java_names
        for line in body_lines:
            matches = re.findall(r'\b(\w+Result)\b', line)
            for m in matches:
                if m not in declared_result_vars and m not in known_names:
                    issues += 1

    # UNSUPPORTED function calls generate non-compilable placeholder code
    if re.search(r'/\*\s*(UNSUPPORTED|TODO: implement)\b', all_text):
        issues += 1

    # GOTO conversions leave unreachable code after while(true) { continue; } with no break
    if re.search(r'while\s*\(\s*true\s*\)', all_text) and 'continue;' in all_text and 'break;' not in all_text:
        issues += 1

    return issues > 0


def _generate_stub_body(proc: ProcedureInfo, out_params: list) -> list:
    _record_todo("AUTO_STUB", proc, "complex PL/pgSQL pattern → stub body")
    lines = ["// TODO: Auto-generated stub — complex PL/pgSQL pattern requires manual implementation"]
    for p in out_params:
        lines.append(f"{p.java_name}.set(null);")
    if proc.is_function:
        ret_type = sql_type_to_java(proc.return_type) if proc.return_type else "Object"
        lines.append(f"return null;")
    elif any(p.is_out and p.is_refcursor for p in proc.parameters):
        lines.append("return null;")
    return lines


def _indent(text: str, level: int) -> str:
    prefix = "    " * level
    return "\n".join(prefix + line for line in text.split("\n"))


def _to_primitive_if_boxed(java_type: str) -> str:
    _BOXED_TO_PRIMITIVE = {
        "Long": "long", "Integer": "int", "Boolean": "boolean",
        "Double": "double", "Float": "float",
    }
    return _BOXED_TO_PRIMITIVE.get(java_type, java_type)


def _write_service_test(base_path: Path, pkg: PackageInfo, service_injections: dict,
                         svc_method_param_counts: dict, all_packages: dict = None):
    jp = _pkg_java_package(pkg)
    test_dir = base_path / "src/test/java" / jp.replace(".", "/") / "service"
    test_dir.mkdir(parents=True, exist_ok=True)
    class_name = f"{package_to_classname(pkg.package_name)}Service"
    mapper_name = f"{package_to_classname(pkg.package_name)[0].lower()}{package_to_classname(pkg.package_name)[1:]}Mapper"
    test_class_name = f"{class_name}Test"

    imports = set()
    imports.add("import org.junit.jupiter.api.Test;")
    imports.add("import org.junit.jupiter.api.extension.ExtendWith;")
    imports.add("import org.mockito.InjectMocks;")
    imports.add("import org.mockito.Mock;")
    imports.add("import org.mockito.junit.jupiter.MockitoExtension;")
    imports.add("import org.mockito.junit.jupiter.MockitoSettings;")
    imports.add("import org.mockito.quality.Strictness;")
    imports.add(f"import {jp}.service.{class_name};")
    imports.add(f"import {jp}.mapper.{package_to_classname(pkg.package_name)}Mapper;")
    imports.add(f"import {BASE_PACKAGE}.exception.BusinessException;")
    imports.add("import static org.mockito.Mockito.*;")
    imports.add("import static org.junit.jupiter.api.Assertions.*;")

    _all_pkgs = all_packages or {}
    for svc_var, pkg_name in service_injections.items():
        if pkg_name:
            svc_class = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var.replace("Service", "")
            svc_class = f"{package_to_classname(svc_class_part)}Service"
            pkg_name = svc_class_part
        target_jp = _pkg_java_package(_all_pkgs[pkg_name]) if pkg_name in _all_pkgs else BASE_PACKAGE
        imports.add(f"import {target_jp}.service.{svc_class};")

    needs_atomic_ref = any(p.is_out for proc in pkg.procedures for p in proc.parameters)
    if needs_atomic_ref:
        imports.add("import java.util.concurrent.atomic.AtomicReference;")

    test_methods = []
    seen_method_names: dict = {}
    for proc in pkg.procedures:
        tests = _build_test_methods(proc, mapper_name, service_injections, svc_method_param_counts, pkg)
        for i, test_code in enumerate(tests):
            lines_of_code = test_code.strip().split("\n")
            method_line = next((l for l in lines_of_code if "void test_" in l), None)
            if method_line:
                base_name = method_line.strip().split("void ")[1].split("(")[0]
                count = seen_method_names.get(base_name, 0)
                seen_method_names[base_name] = count + 1
                if count > 0:
                    test_code = test_code.replace(f"void {base_name}()", f"void {base_name}_{count}()")
            test_methods.append(test_code)

    if not test_methods:
        test_methods.append(
            "    @Test\n"
            "    void testServiceExists() {\n"
            f"        assertNotNull(service);\n"
            "    }"
        )

    lines = []
    lines.append(f"package {jp}.service;")
    lines.append("")
    for imp in sorted(imports):
        lines.append(imp)
    lines.append("")
    lines.append("@ExtendWith(MockitoExtension.class)")
    lines.append("@MockitoSettings(strictness = Strictness.LENIENT)")
    if pkg.source_file:
        lines.append(f"// Source: {pkg.source_file}")
    for c in pkg.comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            lines.append(formatted)
    lines.append(f"class {test_class_name} {{")
    lines.append("")
    lines.append(f"    @Mock")
    lines.append(f"    private {package_to_classname(pkg.package_name)}Mapper {mapper_name};")

    for svc_var, pkg_name in service_injections.items():
        if pkg_name:
            svc_class = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var.replace("Service", "")
            svc_class = f"{package_to_classname(svc_class_part)}Service"
        lines.append("")
        lines.append(f"    @Mock")
        lines.append(f"    private {svc_class} {svc_var};")

    lines.append("")
    lines.append(f"    @InjectMocks")
    lines.append(f"    private {class_name} service;")

    for i, tm in enumerate(test_methods):
        lines.append("")
        lines.append(tm)

    lines.append("}")
    lines.append("")
    content = "\n".join(lines)
    (test_dir / f"{test_class_name}.java").write_text(content)


def _build_test_methods(proc: ProcedureInfo, mapper_name: str, service_injections: dict,
                         svc_method_param_counts: dict, pkg: PackageInfo) -> list:
    method_name = java_method_name(proc.proc_name)
    results = []

    in_params = [p for p in proc.parameters if not p.is_out]
    out_params = [p for p in proc.parameters if p.is_out]

    param_values = []
    param_args = []
    for p in in_params:
        val = _default_test_value(p.java_type, p.java_name)
        param_values.append(f"{p.java_type} {p.java_name} = {val};")
        param_args.append(p.java_name)

    out_decls = []
    out_args = []
    for p in out_params:
        if p.is_refcursor:
            continue
        holder = f"AtomicReference<{p.java_type}>"
        out_decls.append(f"{holder} {p.java_name} = new AtomicReference<>(null);")
        out_args.append(p.java_name)

    all_args = param_args + out_args
    args_str = ", ".join(all_args)

    has_raise = any("throw new BusinessException" in line for line in proc.java_logic_lines)
    has_dml = any(d.sql_type in ("insert", "update", "delete") for d in proc.dml_statements)
    has_service_calls = len(proc.service_calls) > 0

    if has_raise:
        results.append(_build_error_test(proc, mapper_name, param_values, out_decls, args_str, service_injections, svc_method_param_counts, pkg))
        results.append(_build_success_test(proc, mapper_name, param_values, out_decls, args_str, service_injections, svc_method_param_counts, pkg))
    else:
        results.append(_build_success_test(proc, mapper_name, param_values, out_decls, args_str, service_injections, svc_method_param_counts, pkg))

    return results


def _default_test_value(java_type: str, param_name: str) -> str:
    lower = java_type.lower()
    name_lower = param_name.lower()
    if "long" in lower:
        if "id" in name_lower:
            return "1L"
        return "100L"
    if "integer" in lower or "int" in lower:
        if "qty" in name_lower or "limit" in name_lower or "threshold" in name_lower:
            return "5"
        if "count" in name_lower:
            return "10"
        return "1"
    if java_type == "java.math.BigDecimal":
        return "new java.math.BigDecimal(\"99.99\")"
    if "big_decimal" in lower:
        return "new java.math.BigDecimal(\"99.99\")"
    if "double" in lower:
        return "1.0d"
    if "float" in lower:
        return "1.0f"
    if "boolean" in lower:
        return "true"
    if "timestamp" in lower:
        return "java.sql.Timestamp.valueOf(\"2024-01-01 00:00:00\")"
    if "date" in lower:
        return "java.sql.Date.valueOf(\"2024-01-01\")"
    return f"\"test_{param_name}\""


def _build_success_test(proc: ProcedureInfo, mapper_name: str,
                         param_values: list, out_decls: list,
                         args_str: str, service_injections: dict,
                         svc_method_param_counts: dict, pkg: PackageInfo) -> str:
    method_name = java_method_name(proc.proc_name)
    lines = []
    has_while = any("while (" in line or "while(" in line for line in proc.java_logic_lines)
    if has_while:
        lines.append("    @org.junit.jupiter.api.Disabled(\"auto-generated mock cannot terminate while loop\")")
    lines.append("    @Test")
    lines.append(f"    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)")
    lines.append(f"    void test_{method_name}_success() {{")

    for pv in param_values:
        lines.append(f"        {pv}")
    for od in out_decls:
        lines.append(f"        {od}")

    _mock_all_mapper_methods(mapper_name, pkg, lines)

    is_function = proc.is_function
    if is_function:
        lines.append(f"        var result = service.{method_name}({args_str});")
        lines.append(f"        assertNotNull(result);")
    else:
        lines.append(f"        service.{method_name}({args_str});")

    lines.append("    }")
    return "\n".join(lines)


def _collect_all_dmls(pkg: PackageInfo) -> dict:
    all_dmls = {}
    for p in pkg.procedures:
        in_param_count = sum(1 for param in p.parameters if not param.is_out)
        for dml in p.dml_statements:
            if dml.method_id not in all_dmls:
                all_dmls[dml.method_id] = (dml.method_id, dml.sql_type, dml.result_type, dml.returns_list, in_param_count, dml.sql_text)
    return all_dmls


def _extract_mock_fields_from_sql(sql_text: str) -> dict:
    if not sql_text:
        return {}
    stripped = re.sub(r'--.*$', '', sql_text, flags=re.MULTILINE)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)
    m = re.match(r'select\s+(.*?)\s+from\b', stripped, re.IGNORECASE | re.DOTALL)
    if not m:
        return {}
    col_clause = m.group(1).strip()
    if col_clause == '*':
        return {}
    columns = []
    for part in re.split(r',\s*', col_clause):
        part = part.strip()
        if not part:
            continue
        alias_match = re.match(r'.+\s+[Aa][Ss]\s+(\w+)\s*$', part)
        if alias_match:
            columns.append(alias_match.group(1).lower())
            continue
        if '.' in part:
            continue
        if '(' in part:
            continue
        if part.strip() == '*':
            continue
        columns.append(part.lower().strip())
    fields = {}
    for col in columns:
        fields[col] = _mock_value_for_column(col)
    return fields if fields else {}


def _mock_value_for_column(col_name: str) -> str:
    n = col_name.lower()
    if "id" in n:
        return "1L"
    if any(k in n for k in ("salary", "amount", "price", "total", "balance", "cost")):
        return "java.math.BigDecimal.TEN"
    if any(k in n for k in ("name", "dept", "title", "label", "status", "desc")):
        return "\"test\""
    if any(k in n for k in ("count", "qty", "quantity", "num", "head_count")):
        return "5"
    if any(k in n for k in ("date", "time", "created", "updated")):
        return "\"2025-01-01\""
    return "1"


def _extract_select_column(sql_text: str, index: int) -> str:
    if not sql_text:
        return "col"
    stripped = re.sub(r'--.*$', '', sql_text, flags=re.MULTILINE)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', stripped, flags=re.IGNORECASE | re.DOTALL)
    m = re.match(r'select\s+(.*?)\s+from\b', stripped, re.IGNORECASE | re.DOTALL)
    if not m:
        return "col"
    cols = [c.strip() for c in re.split(r',\s*', m.group(1))]
    if index >= len(cols):
        return "col"
    col = cols[index].strip()
    alias_match = re.match(r'.+\s+[Aa][Ss]\s+(\w+)\s*$', col)
    if alias_match:
        return alias_match.group(1)
    return col


def _mock_select_return(dml_sql_type: str, dml_result_type, dml_returns_list: bool, mapper_name: str, dml_method_id: str, method_any: str, dml_sql_text: str = "") -> str:
    if dml_sql_type != "select":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(1);"
    if dml_returns_list:
        mock_fields = _extract_mock_fields_from_sql(dml_sql_text)
        if mock_fields:
            puts = " ".join(f'm.put("{k}", {v});' for k, v in mock_fields.items())
            return f"        {{ var m = new java.util.HashMap<String,Object>(); {puts} when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.util.List.of(m)); }}"
        return f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.util.List.of(m)); }}"
    if dml_result_type == "Integer":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(999);"
    if dml_result_type == "Long":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(999L);"
    if dml_result_type == "String":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(\"test\");"
    if dml_result_type == "Boolean":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(true);"
    if dml_result_type and dml_result_type not in ("Map<String, Object>", "java.util.Map"):
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(null);"
    mock_fields = _extract_mock_fields_from_sql(dml_sql_text)
    if mock_fields:
        puts = " ".join(f'm.put("{k}", {v});' for k, v in mock_fields.items())
        return f"        {{ var m = new java.util.HashMap<String,Object>(); {puts} when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m); }}"
    return f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); m.put(\"product_id\", 1L); m.put(\"v_product_id\", 1L); m.put(\"v_qty\", 10); m.put(\"total\", 100); m.put(\"v_total\", 100); m.put(\"stock_qty\", 999); m.put(\"name\", \"test\"); m.put(\"status\", \"ACTIVE\"); m.put(\"v_status\", \"PENDING\"); m.put(\"v_amount\", java.math.BigDecimal.TEN); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m); }}"


def _mock_all_mapper_methods(mapper_name: str, pkg: PackageInfo, lines: list, error_mode: bool = False):
    all_dmls = _collect_all_dmls(pkg)
    for dml_key, dml_info in all_dmls.items():
        dml_method_id, dml_sql_type, dml_result_type, dml_returns_list, dml_param_count, dml_sql_text = dml_info
        method_any = ", ".join(["any()"] * dml_param_count) if dml_param_count > 0 else ""
        if error_mode and dml_sql_type == "select":
            if dml_returns_list:
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.util.List.of());")
            elif dml_result_type == "Integer":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(0);")
            elif dml_result_type == "Long":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(0L);")
            elif dml_result_type == "String":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(\"\");")
            else:
                lines.append(f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); m.put(\"product_id\", 1L); m.put(\"v_product_id\", 1L); m.put(\"v_qty\", 10); m.put(\"total\", 0); m.put(\"v_total\", 0); m.put(\"stock_qty\", 0); m.put(\"name\", \"\"); m.put(\"status\", \"REJECTED\"); m.put(\"v_status\", \"REJECTED\"); m.put(\"v_amount\", java.math.BigDecimal.ZERO); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m); }}")
        else:
            lines.append(_mock_select_return(dml_sql_type, dml_result_type, dml_returns_list, mapper_name, dml_method_id, method_any, dml_sql_text))


def _build_any_matchers(proc: ProcedureInfo) -> str:
    count = sum(1 for p in proc.parameters if not p.is_out)
    if count == 0:
        return ""
    return ", ".join(["any()"] * count)


def _build_error_test(proc: ProcedureInfo, mapper_name: str,
                       param_values: list, out_decls: list,
                       args_str: str, service_injections: dict,
                       svc_method_param_counts: dict, pkg: PackageInfo) -> str:
    method_name = java_method_name(proc.proc_name)
    has_while = any("while (" in line or "while(" in line for line in proc.java_logic_lines)
    lines = []
    if has_while:
        lines.append("    @org.junit.jupiter.api.Disabled(\"auto-generated mock cannot terminate while loop\")")
    lines.append("    @Test")
    lines.append(f"    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)")
    lines.append(f"    void test_{method_name}_throwsBusinessException() {{")
    lines.append("        org.junit.jupiter.api.Assumptions.assumeTrue(false, \"auto-generated error test requires domain-specific test data\");")

    for pv in param_values:
        lines.append(f"        {pv}")
    for od in out_decls:
        lines.append(f"        {od}")

    _mock_all_mapper_methods(mapper_name, pkg, lines, error_mode=True)

    lines.append(f"        assertThrows(BusinessException.class, () -> {{")
    lines.append(f"            service.{method_name}({args_str});")
    lines.append(f"        }});")
    lines.append("    }")
    return "\n".join(lines)


def _build_mock_args(proc: ProcedureInfo) -> str:
    args = []
    for p in proc.parameters:
        if p.is_out:
            continue
        args.append(p.java_name)
    return ", ".join(args) if args else "any()"


# ── Cache & Config ─────────────────────────────────────────────

CACHE_DIR_NAME = ".fluxgauss"


def _compute_file_hash(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _parse_config(config_path: str) -> dict:
    """Parse YAML config file. Falls back to simple line parser if pyyaml unavailable."""
    if yaml:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    config = {}
    current_list = None
    with open(config_path, 'r') as f:
        for line in f:
            line = line.rstrip()
            if not line or line.lstrip().startswith('#'):
                continue
            list_match = re.match(r'^\s+-\s+(.+)$', line)
            if list_match:
                if isinstance(current_list, list):
                    current_list.append(list_match.group(1).strip().strip('"').strip("'"))
                continue
            kv_match = re.match(r'^(\w[\w_]*)\s*:\s*(.*)$', line)
            if kv_match:
                key, value = kv_match.group(1), kv_match.group(2).strip()
                if value:
                    config[key] = value.strip('"').strip("'")
                else:
                    config[key] = []
                    current_list = config[key]
    return config


def _cache_base(output_dir: str) -> Path:
    return Path(output_dir) / CACHE_DIR_NAME


def _load_manifest(output_dir: str) -> dict:
    path = _cache_base(output_dir) / "manifest.json"
    if path.exists():
        with open(path, 'r') as f:
            return json.load(f)
    return {"files": {}}


def _save_manifest(output_dir: str, manifest: dict):
    base = _cache_base(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "manifest.json", 'w') as f:
        json.dump(manifest, f, indent=2)


def _cached_ast_path(output_dir: str, sql_file: str) -> Path:
    safe = re.sub(r'[^\w]', '_', sql_file)
    return _cache_base(output_dir) / "ast" / f"{safe}.json"


def _load_cached_ast(output_dir: str, sql_file: str):
    path = _cached_ast_path(output_dir, sql_file)
    if path.exists():
        with open(path, 'r') as f:
            return json.load(f)
    return None


def _save_cached_ast(output_dir: str, sql_file: str, ast: dict):
    ast_dir = _cache_base(output_dir) / "ast"
    ast_dir.mkdir(parents=True, exist_ok=True)
    with open(_cached_ast_path(output_dir, sql_file), 'w') as f:
        json.dump(ast, f)


def _find_dependent_packages(packages: list, changed_pkg_names: set) -> set:
    """BFS from changed packages through reverse dependency graph."""
    pkg_map = {p.package_name: p for p in packages}
    reverse_deps = defaultdict(set)
    for pkg in packages:
        for proc in pkg.procedures:
            for call in proc.service_calls:
                if call.package_name:
                    dep_name = _find_registered_pkg(call.package_name, pkg_map)
                    if dep_name and dep_name != pkg.package_name:
                        reverse_deps[dep_name].add(pkg.package_name)

    affected = set(changed_pkg_names)
    queue = list(changed_pkg_names)
    while queue:
        current = queue.pop(0)
        for dep in reverse_deps.get(current, set()):
            if dep not in affected:
                affected.add(dep)
                queue.append(dep)
    return affected


def _clean_stale_packages(output_dir: str, old_manifest: dict, current_packages: list):
    """Delete generated files for packages no longer in config."""
    current_pkg_names = {p.package_name for p in current_packages}
    # Build pkg_name → java_package map from old manifest for path resolution
    old_pkg_jp = {}
    for info in old_manifest.get("files", {}).values():
        pkg = info.get("package")
        if pkg:
            old_pkg_jp[pkg] = info.get("java_package", "")
    old_pkgs = set(old_pkg_jp.keys())
    removed = old_pkgs - current_pkg_names
    if not removed:
        return
    base_path = Path(output_dir)
    for pkg_name in removed:
        class_name = package_to_classname(pkg_name)
        old_jp = old_pkg_jp.get(pkg_name, "") or BASE_PACKAGE
        old_dir = "src/main/java/" + old_jp.replace(".", "/")
        stale_files = [
            base_path / old_dir / "service" / f"{class_name}Service.java",
            base_path / old_dir / "mapper" / f"{class_name}Mapper.java",
            base_path / RESOURCES_DIR / "mapper" / f"{class_name}Mapper.xml",
            base_path / "src/test/java" / old_jp.replace(".", "/") / "service" / f"{class_name}ServiceTest.java",
        ]
        for f in stale_files:
            if f.exists():
                f.unlink()
                _log(f"    Removed: {f.relative_to(base_path)}")


# ── Conversion Report Generation ─────────────────────────────

def build_conversion_report(
    output_dir: str, packages: list, all_skipped: list, parse_errors_map: dict,
    config_path: str = ""
) -> ConversionReport:
    report = ConversionReport(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        config_path=config_path or "CLI mode",
        output_dir=os.path.abspath(output_dir),
        sql_files=[],
        procedure_mappings=[],
        skipped_items=all_skipped,
        parse_errors=[],
        unresolved_calls=list(UNRESOLVED_CALLS),
        total_packages=len(packages),
        total_procedures=sum(len(pkg.procedures) for pkg in packages),
        total_dml=sum(len(proc.dml_statements) for pkg in packages for proc in pkg.procedures),
        total_cross_calls=sum(len(proc.service_calls) for pkg in packages for proc in pkg.procedures),
    )

    base_path = Path(output_dir)
    for pkg in packages:
        class_name = package_to_classname(pkg.package_name)
        jp_path = _pkg_java_package(pkg).replace('.', '/')
        generated = [
            f"src/main/java/{jp_path}/service/{class_name}Service.java",
            f"src/main/java/{jp_path}/mapper/{class_name}Mapper.java",
            f"src/main/resources/mapper/{class_name}Mapper.xml",
            f"src/test/java/{jp_path}/service/{class_name}ServiceTest.java",
        ]
        generated = [g for g in generated if (base_path / g).exists()]

        for proc in pkg.procedures:
            mapper_methods = [dml.method_id for dml in proc.dml_statements]
            is_stub = proc.name in STUB_PROCEDURES
            has_error = any(
                (err.get("location") or "").startswith(proc.name)
                for errs in parse_errors_map.values()
                for err in (errs if isinstance(errs, list) else [errs])
            )
            notes = ""
            if is_stub:
                notes = "复杂 PL/pgSQL 模式，生成 Stub，需人工审查"
            if any("TODO" in line for line in proc.java_logic_lines):
                todo_count = sum(1 for line in proc.java_logic_lines if "TODO" in line)
                if notes:
                    notes += f"；含 {todo_count} 个 TODO 待处理"
                else:
                    notes = f"含 {todo_count} 个 TODO 待处理"

            report.procedure_mappings.append(ProcedureMapping(
                sql_file=pkg.source_file,
                procedure_name=proc.name,
                procedure_type="FUNCTION" if proc.is_function else "PROCEDURE",
                java_service=f"{class_name}Service",
                java_method=java_method_name(proc.proc_name),
                mapper_methods=mapper_methods,
                generated_files=generated,
                is_stub=is_stub,
                has_parse_error=has_error,
                notes=notes,
            ))

    for sql_file, errors in parse_errors_map.items():
        if isinstance(errors, list):
            for err in errors:
                report.parse_errors.append((sql_file, err))
        else:
            report.parse_errors.append((sql_file, errors))

    return report


def _render_report_markdown(report: ConversionReport) -> str:
    lines = []
    lines.append("# FluxGauss 转换报告")
    lines.append("")
    lines.append(f"**生成时间**: {report.generated_at}  ")
    lines.append(f"**配置文件**: {report.config_path}  ")
    lines.append(f"**输出目录**: `{report.output_dir}`")
    lines.append("")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append(f"| 指标 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 输入 SQL 文件 | {len(set(m.sql_file for m in report.procedure_mappings)) + len(set(s.sql_file for s in report.skipped_items))} |")
    lines.append(f"| 转换的包 | {report.total_packages} |")
    lines.append(f"| 存储过程/函数 | {report.total_procedures} |")
    lines.append(f"| 提取的 DML (MyBatis mapper) | {report.total_dml} |")
    lines.append(f"| 跨包调用 | {report.total_cross_calls} |")
    converted_count = sum(1 for m in report.procedure_mappings if not m.is_stub)
    stub_count = sum(1 for m in report.procedure_mappings if m.is_stub)
    lines.append(f"| 成功转换 | {converted_count} |")
    if stub_count:
        lines.append(f"| ⚠️ Stub（需人工审查） | {stub_count} |")
    lines.append(f"| ⏭ 跳过（不涉及存储过程） | {len(report.skipped_items)} |")
    lines.append(f"| ❌ 解析错误 | {len(report.parse_errors)} |")
    if report.unresolved_calls:
        lines.append(f"| ⚠️ 未解析的跨包调用 | {len(report.unresolved_calls)} |")
    if TODO_SUMMARY:
        lines.append(f"| 🔍 TODO 待处理 | {len(TODO_SUMMARY)} |")
    lines.append("")

    if report.procedure_mappings:
        lines.append("---")
        lines.append("")
        lines.append("## SQL → Java 映射")
        lines.append("")

        by_file = defaultdict(list)
        for m in report.procedure_mappings:
            by_file[m.sql_file].append(m)

        for sql_file, mappings in sorted(by_file.items()):
            svc_name = mappings[0].java_service
            lines.append(f"### `{sql_file}` → `{svc_name}`")
            lines.append("")
            lines.append("| SQL 存储过程/函数 | 类型 | Java 方法 | Mapper 方法 | 状态 | 备注 |")
            lines.append("|-----------------|------|-----------|-------------|------|------|")
            for m in mappings:
                status = "⚠️ Stub" if m.is_stub else "✅"
                mapper_str = ", ".join(m.mapper_methods) if m.mapper_methods else "—"
                if not m.notes:
                    m.notes = ""
                lines.append(
                    f"| `{m.procedure_name}` | {m.procedure_type} | "
                    f"`{m.java_service}.{m.java_method}()` | {mapper_str} | "
                    f"{status} | {m.notes} |"
                )
            lines.append("")

            gen_files = mappings[0].generated_files
            if gen_files:
                lines.append("**生成的文件**:")
                lines.append("")
                for gf in gen_files:
                    lines.append(f"- `{gf}`")
                lines.append("")

    if report.skipped_items:
        lines.append("---")
        lines.append("")
        lines.append("## ⏭ 跳过项 — 不涉及存储过程，仅作参考")
        lines.append("")
        lines.append("以下 SQL 语句不涉及存储过程/函数的转换，仅列出供参考。")
        lines.append("")

        by_file = defaultdict(list)
        for s in report.skipped_items:
            by_file[s.sql_file].append(s)

        for sql_file, items in sorted(by_file.items()):
            lines.append(f"### `{sql_file}`")
            lines.append("")
            for item in items:
                icon = {"DDL": "📋", "DML": "📝", "OTHER": "❓"}.get(item.statement_type, "❓")
                loc = ""
                if item.line_start:
                    loc = f" (行 {item.line_start}"
                    if item.line_end and item.line_end != item.line_start:
                        loc += f"-{item.line_end}"
                    loc += ")"
                lines.append(f"- {icon} **{item.category}**: `{item.name}`{loc} — {item.detail}")
            lines.append("")

    errors_and_warnings = []
    if report.parse_errors:
        errors_and_warnings.append(("❌ 解析错误", "parse_errors"))
    if report.unresolved_calls:
        errors_and_warnings.append(("⚠️ 未解析的跨包调用", "unresolved"))
    if any(m.is_stub for m in report.procedure_mappings):
        errors_and_warnings.append(("⚠️ Stub 过程（需人工审查）", "stubs"))

    if errors_and_warnings:
        lines.append("---")
        lines.append("")
        lines.append("## 错误与警告")
        lines.append("")

        if report.parse_errors:
            lines.append("### ❌ 解析错误")
            lines.append("")
            by_file = defaultdict(list)
            for sql_file, err in report.parse_errors:
                by_file[sql_file].append(err)
            for sql_file, errs in sorted(by_file.items()):
                lines.append(f"**`{sql_file}`**:")
                for err in errs:
                    if isinstance(err, dict):
                        msg = err.get("message", err.get("parse_error", str(err)))
                        loc = err.get("location", "")
                        if loc:
                            lines.append(f"- 行 {loc}: {msg}")
                        else:
                            lines.append(f"- {msg}")
                    else:
                        lines.append(f"- {err}")
                lines.append("")

        if report.unresolved_calls:
            lines.append("### ⚠️ 未解析的跨包调用")
            lines.append("")
            lines.append("以下存储过程调用了未包含在输入中的包，请在配置中添加对应的 SQL 文件。")
            lines.append("")
            for uc in report.unresolved_calls:
                lines.append(f"- `{uc}`")
            lines.append("")

        stubs = [m for m in report.procedure_mappings if m.is_stub]
        if stubs:
            lines.append("### ⚠️ Stub 过程（需人工审查）")
            lines.append("")
            lines.append("以下过程因包含复杂的 PL/pgSQL 模式，自动转换为 Stub，需要人工实现：")
            lines.append("")
            for m in stubs:
                lines.append(f"- `{m.procedure_name}` → `{m.java_service}.{m.java_method}()`")
            lines.append("")

    if UNSUPPORTED_FUNCTIONS:
        lines.append("---")
        lines.append("")
        lines.append("## ⚠️ 未映射的函数调用")
        lines.append("")
        lines.append("以下函数在转换过程中未找到对应的 Java 映射，生成的代码中包含 `/* UNSUPPORTED */` 注释，需要手动实现：")
        lines.append("")
        lines.append("| 存储过程 | 节点类型 | 函数名 | 源文件 |")
        lines.append("|----------|----------|--------|--------|")
        for entry in UNSUPPORTED_FUNCTIONS:
            parts = entry.split(" | ")
            proc_id = parts[0] if len(parts) > 0 else ""
            tag = parts[1] if len(parts) > 1 else ""
            fn = parts[2] if len(parts) > 2 else ""
            src = parts[3] if len(parts) > 3 else ""
            lines.append(f"| `{proc_id}` | {tag} | `{fn}` | {src} |")
        lines.append("")

    if TODO_SUMMARY:
        lines.append("---")
        lines.append("")
        lines.append("## 🔍 TODO 诊断摘要")
        lines.append("")

        from collections import Counter as _Counter
        cat_counts = _Counter(cat for cat, _, _, _ in TODO_SUMMARY)
        cat_detail_counts = _Counter((cat, detail) for cat, _, _, detail in TODO_SUMMARY)

        CATEGORY_META = {
            "UNHANDLED_STMT": ("未处理的语句类型", "PL/pgSQL 语句类型在转换器中无对应处理器"),
            "FOR_UNSUPPORTED_KIND": ("FOR 循环未知类型", "FOR 循环的 kind 不在 Range/Query 之中"),
            "FOR_QUERY_FAILED": ("FOR 查询重建失败", "FOR IN SELECT 的 SQL 无法从 AST 还原"),
            "EXECUTE_UNRESOLVED": ("EXECUTE 动态 SQL 未解析", "动态 SQL 变量无法追踪到完整 SQL 字符串"),
            "AUTO_STUB": ("自动 Stub", "生成代码未通过编译检查，方法体被替换为 Stub"),
            "COMPLEX_REVIEW": ("复杂模式需审查", "同 AUTO_STUB，为方法级注释标记"),
        }

        lines.append("### 按类别统计")
        lines.append("")
        lines.append("| 类别 | 说明 | 数量 |")
        lines.append("|------|------|------|")
        for cat, count in cat_counts.most_common():
            meta = CATEGORY_META.get(cat, (cat, ""))
            lines.append(f"| `{cat}` | {meta[1] if isinstance(meta, tuple) else meta} | {count} |")
        lines.append("")

        unhandled_details = {d: c for (cat, d), c in cat_detail_counts.items() if cat == "UNHANDLED_STMT"}
        if unhandled_details:
            lines.append("### UNHANDLED_STMT 详细分布（缺失的语句类型）")
            lines.append("")
            lines.append("| 语句类型 | 出现次数 |")
            lines.append("|----------|----------|")
            for detail, count in sorted(unhandled_details.items(), key=lambda x: -x[1]):
                lines.append(f"| `{detail}` | {count} |")
            lines.append("")

        for_kind_details = {d: c for (cat, d), c in cat_detail_counts.items() if cat in ("FOR_UNSUPPORTED_KIND", "FOR_QUERY_FAILED")}
        if for_kind_details:
            lines.append("### FOR 循环问题详细分布")
            lines.append("")
            lines.append("| Kind 类型 | 出现次数 |")
            lines.append("|-----------|----------|")
            for detail, count in sorted(for_kind_details.items(), key=lambda x: -x[1]):
                lines.append(f"| `{detail}` | {count} |")
            lines.append("")

        execute_details = {d: c for (cat, d), c in cat_detail_counts.items() if cat == "EXECUTE_UNRESOLVED"}
        if execute_details:
            lines.append("### EXECUTE 动态 SQL 问题分布")
            lines.append("")
            lines.append("| 变量 | 出现次数 |")
            lines.append("|------|----------|")
            for detail, count in sorted(execute_details.items(), key=lambda x: -x[1]):
                lines.append(f"| `{detail}` | {count} |")
            lines.append("")

        lines.append("### 按存储过程分布")
        lines.append("")
        proc_counts = _Counter(proc_id for _, proc_id, _, _ in TODO_SUMMARY)
        lines.append("| 存储过程 | TODO 数量 | 源文件 |")
        lines.append("|----------|-----------|--------|")
        proc_src = {}
        for _, proc_id, src, _ in TODO_SUMMARY:
            proc_src[proc_id] = src
        for proc_id, count in proc_counts.most_common(50):
            lines.append(f"| `{proc_id}` | {count} | `{proc_src.get(proc_id, '')}` |")
        if len(proc_counts) > 50:
            lines.append(f"| ... | ... | （共 {len(proc_counts)} 个过程，仅显示前 50） |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*报告由 FluxGauss v{_VERSION} 自动生成*")
    lines.append("")

    return "\n".join(lines)


def write_conversion_report(report: ConversionReport, output_dir: str,
                            report_file: str = None) -> list:
    content = _render_report_markdown(report)

    written = []

    # Always write timestamped copy to .fluxgauss/reports/
    ts = report.generated_at.replace(" ", "_").replace(":", "").replace("-", "")
    report_dir = _cache_base(output_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts_path = report_dir / f"conversion-report-{ts}.md"
    ts_path.write_text(content, encoding="utf-8")
    written.append(str(ts_path))

    # Also create/update latest symlink
    latest_path = report_dir / "conversion-report-latest.md"
    latest_path.write_text(content, encoding="utf-8")

    # If --report specified, write to that path too
    if report_file:
        target = Path(report_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(str(target))

    return written


# ── Main ───────────────────────────────────────────────────────

FLUXGAUSS_LOGO = """\

    ███████╗██╗     ██╗   ██╗██╗  ██╗     ██████╗  █████╗ ██╗   ██╗███████╗███████╗
    ██╔════╝██║     ██║   ██║╚██╗██╔╝    ██╔════╝ ██╔══██╗██║   ██║██╔════╝██╔════╝
    █████╗  ██║     ██║   ██║ ╚███╔╝     ██║  ███╗███████║██║   ██║███████╗███████╗
    ██╔══╝  ██║     ██║   ██║ ██╔██╗     ██║   ██║██╔══██║██║   ██║╚════██║╚════██║
    ██║     ███████╗╚██████╔╝██╔╝ ██╗    ╚██████╔╝██║  ██║╚██████╔╝███████║███████║
    ╚═╝     ╚══════╝ ╚═════╝ ╚═╝  ╚═╝     ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝"""

FLUXGAUSS_HELP = f"""\
{FLUXGAUSS_LOGO}

 OpenGauss/PostgreSQL 存储过程 → Spring Boot + MyBatis 自动转换器

 用法:
   fluxgauss -c <config.yaml>              通过配置文件转换（推荐，支持增量）
   fluxgauss -o <dir> -s <sql> [<sql> ...] 通过命令行参数转换

 示例:
   fluxgauss -c fluxgauss.yaml             使用配置文件
   fluxgauss -o ./dest -s pkg_order.sql pkg_product.sql
   fluxgauss -c fluxgauss.yaml --full      强制全量重新生成
   fluxgauss -c fluxgauss.yaml --report ./report.md

  配置文件格式 (YAML):
    output_dir: ./dest                     输出目录
    base_package: com.example.demo         Java 包名（可选）
    logger: slf4j                          日志框架（可选，默认 slf4j）
                                           可选: slf4j, log4j2, commons-logging, jul
                                           或自定义:
                                             logger:
                                               imports:
                                                 - "import com.myco.Logger;"
                                               declaration: "private static final Logger log = ...;"
                                               pom:
                                                 - "<dependency>...</dependency>"
    database:                              数据库连接（可选）
      url: jdbc:postgresql://localhost/db
      username: postgres
      password: postgres
      driver: org.postgresql.Driver
    sources:                               SQL 源文件列表
      - sql/pkg_order.sql
      - sql/pkg_product.sql
    java_packages:                        自定义 Java 包名映射（可选）
      - package: com.example.order
        sources:
          - sql/pkg_order.sql
      - package: com.example.inventory
        sources:
          - sql/pkg_inventory.sql
    # 或单个映射:
    # java_package:
    #   package: com.example.order
    #   sources:
    #     - sql/pkg_order.sql
"""


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="fluxgauss",
        usage="%(prog)s -c <config.yaml> | -o <dir> -s <sql> [...]",
        add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=FLUXGAUSS_HELP,
    )
    parser.add_argument("-h", "--help", action="store_true", default=False)
    parser.add_argument("-c", "--config", metavar="FILE", help="YAML 配置文件路径")
    parser.add_argument("-o", "--output", metavar="DIR", help="输出目录")
    parser.add_argument("-s", "--sources", nargs="+", metavar="SQL", help="SQL 源文件列表")
    parser.add_argument("--full", action="store_true", default=False, help="强制全量重新生成（忽略缓存）")
    parser.add_argument("--report", metavar="FILE", help="指定转换报告输出路径")
    parser.add_argument("-v", "--version", action="store_true", default=False, help="显示版本信息")
    return parser


_VERSION = "1.0.0"


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.help:
        print(FLUXGAUSS_HELP)
        sys.exit(0)

    if args.version:
        print(f"fluxgauss v{_VERSION}")
        sys.exit(0)

    # ── Resolve config ──
    output_dir = None
    sql_files = []
    config_path = None
    config = {}

    if args.config:
        config_path = args.config
        if not os.path.isfile(config_path):
            print(f"Error: config file not found: {config_path}")
            sys.exit(1)
        config = _parse_config(config_path)
        output_dir = config.get('output_dir', './dest')
        sql_files = config.get('sources', [])
        if config.get('base_package'):
            global BASE_PACKAGE, BASE_DIR, _LOGGER_CONFIG
            BASE_PACKAGE = config['base_package']
            BASE_DIR = "src/main/java/" + BASE_PACKAGE.replace(".", "/")
        if 'logger' in config:
            _LOGGER_CONFIG = _resolve_logger_config(config)
    elif args.output and args.sources:
        output_dir = args.output
        sql_files = args.sources
    else:
        print(f"Error: 请指定配置文件 (-c) 或输出目录 + 源文件 (-o + -s)")
        print(f"  用法: fluxgauss -c fluxgauss.yaml")
        print(f"  用法: fluxgauss -o ./dest -s pkg_order.sql pkg_product.sql")
        print(f"  帮助: fluxgauss -h")
        sys.exit(1)

    sql_file_to_java_package = {}
    if args.config and ('java_packages' in config or 'java_package' in config):
        if not yaml:
            print("  ⚠ java_package 配置需要 pyyaml 支持，请安装: pip install pyyaml", file=sys.stderr)
            print("  ⚠ java_package 配置已忽略，将使用默认 base_package", file=sys.stderr)
        else:
            _jp_entries = config.get('java_packages', [])
            if not _jp_entries and 'java_package' in config:
                jp_single = config['java_package']
                _jp_entries = [jp_single] if isinstance(jp_single, dict) else jp_single
            for jp_entry in _jp_entries:
                if not isinstance(jp_entry, dict):
                    continue
                jp_name = jp_entry.get('package', '')
                for src in jp_entry.get('sources', []):
                    sql_file_to_java_package[src] = jp_name
                    if src not in sql_files:
                        sql_files.append(src)

    missing_files = [f for f in sql_files if not os.path.exists(f)]
    if missing_files:
        for f in missing_files:
            _log(f"  ⚠ Source file not found, skipping: {f}")
            parse_errors_map[f] = [{"parse_error": f"file not found: {f}"}]
        sql_files = [f for f in sql_files if os.path.exists(f)]
        if not sql_files:
            _log(f"  ❌ No valid source files. Exiting.")
            sys.exit(1)

    # ── Incremental: hash comparison ──
    manifest = _load_manifest(output_dir) if not args.full else {"files": {}}
    old_files = manifest.get("files", {})
    changed_files = set()
    for f in sql_files:
        current_hash = _compute_file_hash(f)
        old_entry = old_files.get(f, {})
        if current_hash != old_entry.get("hash", ""):
            changed_files.add(f)

    is_incremental = bool(old_files) and len(changed_files) < len(sql_files)
    full_regen = args.full or not old_files or len(changed_files) == len(sql_files)

    print(FLUXGAUSS_LOGO)
    log_path = _init_log(output_dir)
    _log(f"  Output:    {output_dir}")
    _log(f"  Config:    {config_path or 'CLI mode'}")
    _log(f"  Input:     {len(sql_files)} SQL file(s)")
    if is_incremental:
        _log(f"  Incremental: {len(changed_files)} changed, {len(sql_files) - len(changed_files)} cached")
    print()

    # ── Phase 0: Pre-scan for table DDL ──
    for sql_file in sql_files:
        if sql_file not in changed_files and not full_regen:
            continue
        with open(sql_file, 'r', encoding='utf-8', errors='replace') as _f:
            _content = _f.read()
        if re.search(r'create\s+table', _content, re.IGNORECASE):
            schema = parse_table_ddl(sql_file)
            for tbl, cols in schema.items():
                for col, col_type in cols.items():
                    TYPE_OVERRIDES[(tbl, col)] = col_type

    # ── Phase 1: Parse SQL files (use cache for unchanged) ──
    packages = []
    all_package_names = {}
    sql_file_to_pkg = {}
    all_skipped = []
    parse_errors_map = {}
    n_sql = len(sql_files)

    for idx, sql_file in enumerate(sql_files, 1):
        basename = os.path.basename(sql_file)
        try:
            if sql_file not in changed_files and not full_regen:
                cached_ast = _load_cached_ast(output_dir, sql_file)
                if cached_ast:
                    ast = cached_ast
                    _progress_bar("Parse", idx, n_sql, f"Cached {basename}")
                    _log(f"  Cached: {basename}", to_stdout=False)
                else:
                    changed_files.add(sql_file)
                    full_regen = len(changed_files) == len(sql_files)

            if sql_file in changed_files or full_regen:
                _progress_bar("Parse", idx, n_sql, f"Parsing {basename}")
                _log(f"  Parsing: {basename}", to_stdout=False)
                ast = parse_sql_file(sql_file)
                _save_cached_ast(output_dir, sql_file, ast)

            errors = ast.get("errors", [])
            if errors:
                _log(f"    ⚠ {len(errors)} parse error(s)", to_stdout=False)
                parse_errors_map[basename] = errors

            skipped = extract_non_procedure_statements(ast, source_file=basename)
            if skipped:
                all_skipped.extend(skipped)

            procedures, pkg_vars = extract_procedures(ast, source_file=basename)
            for p in procedures:
                p._source_path = sql_file
            comments = extract_comments(ast)
            pkg_level_comments = _map_comments_to_procedures(comments, procedures, source_file=basename)
            if not procedures:
                _log(f"    (no procedures found)", to_stdout=False)
                continue

            pkg_name = procedures[0].package if procedures[0].package else Path(sql_file).stem
            pkg = PackageInfo(package_name=pkg_name, procedures=procedures, package_vars=pkg_vars, source_file=basename, java_package=sql_file_to_java_package.get(sql_file, ""), comments=pkg_level_comments)
            packages.append(pkg)
            all_package_names[pkg_name] = pkg
            sql_file_to_pkg[sql_file] = pkg_name

            for proc in procedures:
                _log(f"    ✅ {'FUNCTION' if proc.is_function else 'PROCEDURE'}: {proc.name} ({len(proc.parameters)} params)", to_stdout=False)
            if pkg.java_package:
                _log(f"    📦 Java package: {pkg.java_package}", to_stdout=False)
        except Exception as e:
            _log(f"  ❌ Error processing {basename}: {e}", to_stdout=False)
            _log(traceback.format_exc(), to_stdout=False)
            parse_errors_map[basename] = [{"parse_error": str(e)}]
            continue

    _progress_done("Parse", n_sql)

    # ── Phase 2: Analyze all procedures ──
    all_procs = [(pkg, proc) for pkg in packages for proc in pkg.procedures]
    n_analyze = len(all_procs)
    _log(f"\n  Analyzing cross-package dependencies...", to_stdout=False)
    for idx, (pkg, proc) in enumerate(all_procs, 1):
        _progress_bar("Analyze", idx, n_analyze, proc.name)
        try:
            analyze_procedure(proc, all_package_names)
        except Exception as e:
            _log(f"    ❌ Error analyzing {proc.name}: {e}", to_stdout=False)
            _log(traceback.format_exc(), to_stdout=False)
            proc.java_logic_lines.append(f"// ERROR: 转换失败 - {e}")
            STUB_PROCEDURES.append(proc.name)
    _progress_done("Analyze", n_analyze)

    # ── Determine affected packages (changed + transitive dependents) ──
    if full_regen or not is_incremental:
        changed_pkg_names = None
    else:
        directly_changed = {sql_file_to_pkg[f] for f in changed_files if f in sql_file_to_pkg}
        changed_pkg_names = _find_dependent_packages(packages, directly_changed)
        _log(f"\n  Incremental: regenerating {len(changed_pkg_names)}/{len(packages)} packages")

    # ── Phase 3: Generate ──
    _log(f"\n  Generating Spring Boot project...", to_stdout=False)
    try:
        generate_project(output_dir, packages, changed_packages=changed_pkg_names, config=config,
                         progress_cb=lambda phase, i, n, s: (
                             _progress_bar("Generate", i, n, s) if phase == "pkg" else None
                         ))
        _progress_done("Generate", len([p for p in packages
                                        if changed_pkg_names is None or p.package_name in changed_pkg_names]))
    except Exception as e:
        _log(f"  ❌ Error generating project: {e}", to_stdout=False)
        _log(traceback.format_exc(), to_stdout=False)

    _clean_stale_packages(output_dir, manifest, packages)

    # ── Save manifest ──
    new_manifest = {"files": {}}
    for f in sql_files:
        new_manifest["files"][f] = {
            "hash": _compute_file_hash(f),
            "package": sql_file_to_pkg.get(f, ""),
            "java_package": sql_file_to_java_package.get(f, ""),
        }
    _save_manifest(output_dir, new_manifest)

    STUB_PROCEDURES.clear()

    report = build_conversion_report(
        output_dir, packages, all_skipped, parse_errors_map,
        config_path=config_path or ""
    )
    report_paths = write_conversion_report(report, output_dir,
                                            report_file=args.report)
    UNSUPPORTED_FUNCTIONS.clear()
    TODO_SUMMARY.clear()

    # ── Summary ──
    total_procs = sum(len(pkg.procedures) for pkg in packages)
    total_dml = sum(len(proc.dml_statements) for pkg in packages for proc in pkg.procedures)
    total_calls = sum(len(proc.service_calls) for pkg in packages for proc in pkg.procedures)

    _log(f"\n  Done!")
    _log(f"    Packages:    {len(packages)}")
    _log(f"    Procedures:  {total_procs}")
    _log(f"    DML stmts:   {total_dml} (extracted as iBatis mapper methods)")
    _log(f"    Cross-calls: {total_calls}")
    _log(f"    Test files:  {len(packages)} (generated unit tests)")
    _log(f"    Skipped:     {len(all_skipped)} (non-procedure SQL)")
    if TODO_SUMMARY:
        _log(f"    TODOs:       {len(TODO_SUMMARY)} (详见转换报告)")
    _log(f"")
    _log(f"    详细处理日志: {_cache_base(output_dir) / 'logs' / 'conversion-latest.log'}")

    if report_paths:
        _log(f"\n  📄 转换报告:")
        for p in report_paths:
            _log(f"    - {p}")
        _log(f"    - {_cache_base(output_dir) / 'logs' / 'conversion-latest.log'}")

    if UNRESOLVED_CALLS:
        _log(f"\n  ⚠ Unresolved cross-package calls ({len(UNRESOLVED_CALLS)}):")
        for uc in UNRESOLVED_CALLS:
            _log(f"    - {uc}")
        _log(f"  Hint: Add the missing package SQL file to the converter input.")

    _log(f"\n  Output: {os.path.abspath(output_dir)}")

    _close_log(output_dir)


if __name__ == "__main__":
    main()

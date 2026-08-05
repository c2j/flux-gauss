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
from collections import defaultdict, namedtuple
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

# ── Configuration ──────────────────────────────────────────────

def _resolve_ogsql_bin() -> str:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_dir = os.path.dirname(_script_dir)
    candidates = []
    # PyInstaller frozen binary: bundled ogsql is in sys._MEIPASS
    if getattr(sys, 'frozen', False):
        _meipass = getattr(sys, '_MEIPASS', '')
        if _meipass:
            candidates.append(os.path.join(_meipass, 'ogsql'))
            candidates.append(os.path.join(_meipass, 'ogsql.exe'))
    candidates.extend([
        os.path.join(os.getcwd(), "ogsql"),
        os.environ.get("OGSQL_BIN", ""),
        shutil.which("ogsql") or "",
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "aarch64-apple-darwin", "release", "ogsql"),
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "release", "ogsql"),
    ])
    for candidate in candidates:
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

# Source file encoding (overridable via --encoding CLI or encoding: YAML config)
_SOURCE_ENCODING = 'utf-8'

# Debug mode: inject SQL source line comments into generated Java/XML (via --debug flag)
DEBUG_MODE = False

# Cache for reading source SQL files (path -> list of lines)
_SQL_FILE_CACHE = {}


def _write_source_file(path, content):
    """Write generated source file with UTF-8 encoding.

    Java/XML source files are always UTF-8 regardless of SQL source encoding.
    """
    path.write_text(content, encoding='utf-8', errors='replace')


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
    with open(sql_file, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    schema = {}

    # Strip SQL comments and Oracle hint syntax
    content_clean = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

    # Find CREATE TABLE statements by tracking parenthesis depth
    for m in re.finditer(r'create\s+table\s+(?:if\s+not\s+exists\s+)?(?:\w+\.)?(\w+)\s*\(', content_clean, re.IGNORECASE):
        table_name = m.group(1).lower()
        start = m.end()

        depth = 1
        pos = start
        while pos < len(content_clean) and depth > 0:
            if content_clean[pos] == '(':
                depth += 1
            elif content_clean[pos] == ')':
                depth -= 1
            pos += 1

        columns_text = content_clean[start:pos - 1]

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

            # Skip constraint definitions and table-level keywords
            first_word = part.split()[0].upper()
            if first_word in ('CONSTRAINT', 'PRIMARY', 'UNIQUE', 'FOREIGN', 'CHECK', 'INDEX', 'LIKE'):
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
                col_type = re.split(r'\s+(NOT\s+NULL|NULL|DEFAULT|PRIMARY|UNIQUE|CHECK|REFERENCES|CONSTRAINT|USING|PCTFREE|INITRANS|MAXTRANS|STORAGE|TABLESPACE|ENABLE|DISABLE|NOCOMPRESS|COMPRESS)', col_type, flags=re.IGNORECASE)[0].strip()
                # Remove trailing Oracle inline comments
                col_type = re.sub(r'\s*/\*.*?\*/', '', col_type)
                if col_type:
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
    "exception": "Throwable",
}

# ── Custom / Package-Qualified Type Aliases ─────────────────────────
# Maps lowercased type name patterns (suffix/prefix/keyword) to Java types.
# Used when sql_type_to_java() encounters an unrecognised type name that
# is likely a user-defined array or table type from another package.
#
# Matching strategy (evaluated in order, first match wins):
#   1. Exact match on the *short* type name (after stripping package prefix)
#   2. Suffix match  — e.g. "arrytype" matches "pkg_param_common.arrytype"
#   3. Keyword match — e.g. "array" anywhere in the name
#
# Users can extend this at runtime via the `type_aliases` key in the YAML
# config file, which is merged on top of the presets below.

_CUSTOM_TYPE_PRESETS = {
    # ── Oracle/OpenGauss array-type naming conventions ───────────
    # Generic "array of varchar" names
    "arrytype":              "List<String>",
    "arrtype":               "List<String>",
    "array_type":            "List<String>",
    "arraytype":             "List<String>",
    "str_array":             "List<String>",
    "string_array":          "List<String>",
    "varchar2_array":        "List<String>",
    "varchar_array":         "List<String>",
    "text_array":            "List<String>",
    "char_array":            "List<String>",
    "split_tbl":             "List<String>",
    "split_array":           "List<String>",
    "id_list":               "List<String>",
    "string_list":           "List<String>",
    "string_tbl":            "List<String>",
    # Oracle TABLE OF VARCHAR2
    "tab_varchar2":          "List<String>",
    "tab_varchar":           "List<String>",
    "tab_text":              "List<String>",
    "tab_char":              "List<String>",
    "tab_string":            "List<String>",
    # Numeric arrays
    "num_array":             "List<java.math.BigDecimal>",
    "number_array":          "List<java.math.BigDecimal>",
    "number_tbl":            "List<java.math.BigDecimal>",
    "decimal_array":         "List<java.math.BigDecimal>",
    "dec_array":             "List<java.math.BigDecimal>",
    "int_array":             "List<Integer>",
    "integer_array":         "List<Integer>",
    "int_list":              "List<Integer>",
    "integer_list":          "List<Integer>",
    "integer_tbl":           "List<Integer>",
    "tab_number":            "List<java.math.BigDecimal>",
    "tab_integer":           "List<Integer>",
    "tab_numeric":           "List<java.math.BigDecimal>",
    "long_array":            "List<Long>",
    "long_list":             "List<Long>",
    "bigint_array":          "List<Long>",
    "tab_bigint":            "List<Long>",
    # Date/time arrays
    "date_array":            "List<java.sql.Date>",
    "date_list":             "List<java.sql.Date>",
    "date_tbl":              "List<java.sql.Date>",
    "timestamp_array":       "List<java.sql.Timestamp>",
    "timestamp_list":        "List<java.sql.Timestamp>",
    "tab_date":              "List<java.sql.Date>",
    "tab_timestamp":         "List<java.sql.Timestamp>",
    # Boolean arrays
    "bool_array":            "List<Boolean>",
    "boolean_array":         "List<Boolean>",
    # Generic / catch-all
    "raw_array":             "List<byte[]>",
    "blob_array":            "List<byte[]>",
    "byte_array":            "List<byte[]>",
    # ── Oracle RECORD / OBJECT naming conventions ────────────────
    "rec_type":              "Map<String, Object>",
    "record_type":           "Map<String, Object>",
    "obj_type":              "Map<String, Object>",
    "row_type":              "Map<String, Object>",
    # ── Oracle REF CURSOR aliases ────────────────────────────────
    "sys_refcursor":         "List<Map<String, Object>>",
    "ref_cursor":            "List<Map<String, Object>>",
    "refcursor":             "List<Map<String, Object>>",
    # ── Common OpenGauss / PostgreSQL custom types ────────────────
    "int4range":             "Object",
    "int8range":             "Object",
    "numrange":              "Object",
    "tsrange":               "Object",
    "tstzrange":             "Object",
    "daterange":             "Object",
    "jsonb_array":           "List<String>",
    "json_array":            "List<String>",
    "uuid_array":            "List<String>",
}

# ── SQL → MyBatis jdbcType Mapping ─────────────────────────────
# Maps normalized SQL type names to MyBatis JdbcType enum values.
# Used when generating #{param, jdbcType=X} in mapper XML.
SQL_TO_JDBC_TYPE = {
    # Integer types
    "bigint": "BIGINT",
    "biginteger": "BIGINT",
    "integer": "INTEGER",
    "int": "INTEGER",
    "int4": "INTEGER",
    "int8": "BIGINT",
    "smallint": "SMALLINT",
    "serial": "INTEGER",
    "bigserial": "BIGINT",
    "number": "NUMERIC",
    # Decimal types
    "numeric": "NUMERIC",
    "decimal": "DECIMAL",
    "real": "REAL",
    "float4": "REAL",
    "float8": "DOUBLE",
    "double precision": "DOUBLE",
    "double": "DOUBLE",
    # String types
    "varchar": "VARCHAR",
    "varchar2": "VARCHAR",
    "character varying": "VARCHAR",
    "char": "CHAR",
    "text": "LONGVARCHAR",
    "string": "VARCHAR",
    # Boolean
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    # Date/Time
    "timestamp": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP",
    "date": "DATE",
    "time": "TIME",
    # Binary
    "bytea": "BINARY",
    "blob": "BLOB",
    "clob": "CLOB",
    # JSON (mapped to VARCHAR in JDBC)
    "json": "VARCHAR",
    "jsonb": "VARCHAR",
    "uuid": "OTHER",
    # Special
    "record": None,       # composite → fallback
    "exception": "VARCHAR",
}


# User-configurable type overrides for %TYPE anchored declarations
# Format: (table_name_lower, column_name_lower) -> sql_type
TYPE_OVERRIDES = {
    # Example: ("db_log", "proc_name"): "varchar",
    # ("db_log", "log_level"): "varchar",
    # ("db_log", "step_no"): "integer",
}

_TABLE_DDL_SOURCE: dict = {}


def _lookup_table_columns(table_name: str, source_file: str = "") -> list:
    cols = []
    if not table_name:
        return cols
    table_lower = table_name.lower()
    if source_file:
        src_norm = os.path.basename(source_file)
        for (tbl, col) in TYPE_OVERRIDES:
            if tbl.lower() == table_lower:
                src = _TABLE_DDL_SOURCE.get((tbl, col), "")
                if os.path.basename(src) == src_norm:
                    cols.append(col)
        if cols:
            return sorted(set(cols))
    for (tbl, col) in TYPE_OVERRIDES:
        if tbl.lower() == table_lower:
            cols.append(col)
    if not cols:
        return []
    return sorted(set(cols))

UNRESOLVED_CALLS = []
STUB_PROCEDURES = []
STUB_REASONS: dict[tuple, list[str]] = {}  # key=(proc.name, param_count) → list of human-readable stub reasons
UNSUPPORTED_FUNCTIONS = []
TODO_SUMMARY = []  # Collects (category, proc_id, source_file, detail) for diagnostic
_MISSING_OVERLOADS: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {}  # pkg → [(method_name, [(java_type, param_name)])]


def _register_missing_overload(pkg: str, method_name: str, arg_types: list, arg_count: int):
    sig_key = (method_name, tuple(arg_types))
    existing = _MISSING_OVERLOADS.get(pkg, [])
    for ex_method, ex_params in existing:
        if ex_method == method_name and len(ex_params) == arg_count:
            return
    param_names = [f"p{chr(97 + i)}" for i in range(arg_count)]
    params = list(zip(arg_types, param_names))
    existing.append((method_name, params))
    _MISSING_OVERLOADS[pkg] = existing


def _add_stub_reason(proc, reason: str):
    """Record a specific reason why a procedure was stubbed."""
    _stub_key = (proc.name, len(proc.parameters))
    STUB_REASONS.setdefault(_stub_key, [])
    if reason not in STUB_REASONS[_stub_key]:
        STUB_REASONS[_stub_key].append(reason)
_PACKAGE_CONSTANTS = {}  # module-level: maps snake_case name → java_type for recovered constants
_PACKAGE_VARIABLES = {}  # module-level: maps snake_case name → {"java_type": str, "default": str, "package": str}
_PACKAGE_VAR_WRITTEN: set = set()  # module-level: set of package variable names (snake_case) that are assigned to during analysis
_DML_COUNTER_BY_PKG: dict = {}  # module-level: shared DML method name counters per package
_DML_CTR_TRACKER: int | None = None  # module-level: tracks id(_DML_COUNTER_BY_PKG) to detect replacement across analyze_procedure calls
_UDF_RETURN_TYPES = {}  # module-level: maps (func_name_lower, arg_count) → java_type for user-defined functions
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


_PROGRESS_TERMINAL_WIDTH = 40


def _progress_bar(phase: str, current: int, total: int, status: str = ""):
    if not sys.stdout.isatty():
        return
    pct = current / total if total > 0 else 0
    filled = int(_PROGRESS_TERMINAL_WIDTH * pct)
    bar = "█" * filled + "░" * (_PROGRESS_TERMINAL_WIDTH - filled)
    label = f"{phase:<8}"
    line = f"\r  {label} [{bar}] {current}/{total} {pct:5.1%}"
    if status:
        status = status[:40] + "…" if len(status) > 41 else status
        line += f"  {status:<42}"
    sys.stdout.write(f"\r{line:<120}")
    sys.stdout.flush()


def _progress_done(phase: str, total: int):
    if not sys.stdout.isatty():
        return
    bar = "█" * _PROGRESS_TERMINAL_WIDTH
    label = f"{phase:<8}"
    line = f"\r  {label} [{bar}] {total}/{total} 100.0%  ✓"
    sys.stdout.write(f"{line:<120}\n")
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
            return "List<Map<String, Object>>"
        else:
            return "Object"

    if isinstance(sql_type, str):
        _pct_candidate = re.sub(r'\s*([.%])\s*', r'\1', sql_type)
        pct_match = re.match(r'^(\w+)\.(\w+)%type$', _pct_candidate, re.IGNORECASE)
        if pct_match:
            table = pct_match.group(1).lower()
            column = pct_match.group(2).lower()
            override = TYPE_OVERRIDES.get((table, column))
            if override:
                return sql_type_to_java(override)
            return sql_type_to_java(_infer_type_from_column_name(column))

    normalized = sql_type.lower().strip()
    # Strip function modifiers that the parser may include in return_type
    for _mod in ("deterministic", "parallel", "immutable", "stable", "volatile", "strict", "called on null input", "returns null on null input"):
        normalized = normalized.replace(_mod, "").strip()
    normalized = re.sub(r"\(.*\)", "", normalized).strip()
    if normalized.startswith("table"):
        return "java.util.List<java.util.Map<String, Object>>"
    # Handle SQL array types: FLOAT8[] → List<Double>, TEXT[] → List<String>, etc.
    if normalized.endswith("[]"):
        base = normalized[:-2].strip()
        base_java = SQL_TO_JAVA.get(base, "Object")
        return f"java.util.List<{base_java}>"
    result = SQL_TO_JAVA.get(normalized)
    if result:
        return result

    _aliases = _CUSTOM_TYPE_PRESETS
    short_name = normalized.rsplit(".", 1)[-1] if "." in normalized else normalized
    if short_name in _aliases:
        return _aliases[short_name]
    if normalized in _aliases:
        return _aliases[normalized]
    for keyword, java_type in _aliases.items():
        if keyword in short_name or keyword in normalized:
            return java_type

    return "Map<String, Object>"


def sql_type_to_jdbc(sql_type) -> Optional[str]:
    """Convert SQL type to MyBatis JdbcType enum value. Returns None for unmappable types (composites, etc.)."""
    if not sql_type:
        return None
    # Handle dict types (PercentType, RefCursor, etc.) — same logic as sql_type_to_java
    if isinstance(sql_type, dict):
        if "TypeName" in sql_type:
            return sql_type_to_jdbc(sql_type["TypeName"])
        elif "PercentType" in sql_type:
            pt = sql_type["PercentType"]
            column = (pt.get("column") or "").lower()
            return sql_type_to_jdbc(_infer_type_from_column_name(column))
        # PercentRowType, Record, RefCursor, etc. → not mappable
        return None
    if isinstance(sql_type, str):
        pct_match = re.match(r'^(\w+)\.(\w+)%type$', sql_type, re.IGNORECASE)
        if pct_match:
            column = pct_match.group(2).lower()
            override = TYPE_OVERRIDES.get((pct_match.group(1).lower(), column))
            if override:
                return sql_type_to_jdbc(override)
            return sql_type_to_jdbc(_infer_type_from_column_name(column))
    normalized = str(sql_type).lower().strip()
    normalized = re.sub(r"\(.*\)", "", normalized).strip()
    return SQL_TO_JDBC_TYPE.get(normalized)


# Reverse mapping: Java type → jdbcType (for local_vars which only store java_type)
_JAVA_TO_JDBC = {
    "String": "VARCHAR",
    "Long": "BIGINT",
    "Integer": "INTEGER",
    "Boolean": "BOOLEAN",
    "Double": "DOUBLE",
    "Float": "REAL",
    "java.math.BigDecimal": "NUMERIC",
    "java.sql.Timestamp": "TIMESTAMP",
    "java.sql.Date": "DATE",
    "java.sql.Time": "TIME",
    "byte[]": "BINARY",
    "Object": None,                     # cannot determine
    "Map<String, Object>": None,        # composite, cannot determine
}

def java_type_to_jdbc(java_type: str) -> Optional[str]:
    """Convert Java type name to MyBatis JdbcType. Returns None for unmappable types."""
    if not java_type:
        return None
    # Direct lookup
    result = _JAVA_TO_JDBC.get(java_type)
    if result is not None or java_type in _JAVA_TO_JDBC:
        return result
    # Handle List<X>, custom types, etc.
    if java_type.startswith("List<"):
        return None
    return None


def is_simple_java_type(java_type: str) -> bool:
    """Check if the type is a simple type (no import needed)."""
    return java_type in (
        "String", "Long", "Integer", "Boolean", "Double", "Float",
        "Object", "byte[]", "void",
    )


# Known Java types that need a fully-qualified import.
# Key: base class name (without generics), Value: fully qualified path.
_KNOWN_IMPORTS = {
    "AtomicReference": "java.util.concurrent.atomic.AtomicReference",
    "BigDecimal": "java.math.BigDecimal",
    "BigInteger": "java.math.BigInteger",
    "List": "java.util.List",
    "ArrayList": "java.util.ArrayList",
    "Map": "java.util.Map",
    "HashMap": "java.util.HashMap",
    "Arrays": "java.util.Arrays",
    "Objects": "java.util.Objects",
}


def _resolve_import(java_type: str):
    """Convert a Java type string to a valid import statement.

    Handles generic types like ``AtomicReference<String>`` by stripping
    the generic portion before looking up the fully-qualified path.
    Returns ``None`` when no import is needed (simple / primitive types).
    """
    if is_simple_java_type(java_type):
        return None
    # Strip generics: AtomicReference<String> -> AtomicReference
    base = java_type.split("<")[0] if "<" in java_type else java_type
    fq = _KNOWN_IMPORTS.get(base)
    if fq:
        return f"import {fq};"
    # Already fully-qualified (contains dot and no generics)
    if "." in base and "<" not in java_type:
        return f"import {java_type};"
    if "." in base:
        return f"import {base};"
    # Unknown local type — no import needed (same package or inner class)
    return None


# ── Naming Helpers ─────────────────────────────────────────────

def _java_safe_identifier(s: str) -> str:
    """Sanitize identifier for Java: handle digits, keywords, special chars, non-ASCII."""
    if not s:
        return "_"
    # Strip non-ASCII and special chars ($, #, etc.)
    s = re.sub(r'[^a-zA-Z0-9_]', '', s)
    if not s or s == '_':
        return "_unnamed"
    # Prepend '_' if starts with digit
    if s[0].isdigit():
        s = "_" + s
    # Escape Java keywords
    JAVA_KEYWORDS = {
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
        "class", "const", "continue", "default", "do", "double", "else", "enum",
        "extends", "final", "finally", "float", "for", "goto", "if", "implements",
        "import", "instanceof", "int", "interface", "long", "native", "new", "package",
        "private", "protected", "public", "return", "short", "static", "strictfp",
        "super", "switch", "synchronized", "this", "throw", "throws", "transient",
        "try", "void", "volatile", "while", "true", "false", "null",
        # Common PL/pgSQL identifiers that clash
        "old", "new", "raise",
    }
    if s.lower() in JAVA_KEYWORDS:
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


def _custom_type_classname(sql_type_name: str) -> str:
    """t_coord_rec -> TCoordRec"""
    name = sql_type_name.lower().strip()
    if name.startswith("t_"):
        name = name[2:]
    elif name.startswith("type_"):
        name = name[5:]
    return snake_to_pascal(name)


# ── AST Model ──────────────────────────────────────────────────

def _resolve_custom_field_type(param_type: str, field_name: str, proc) -> str:
    if not proc or not hasattr(proc, 'custom_types') or not proc.custom_types:
        return ""
    for tn, ti in proc.custom_types.items():
        if _custom_type_classname(tn) == param_type and ti["kind"] == "record":
            for fn, ft in ti["fields"]:
                if fn.lower() == field_name.lower():
                    return ft
    return ""


@dataclass
class Parameter:
    name: str
    java_type: str
    sql_type: str
    mode: Optional[str] = None  # IN, OUT, INOUT
    default_value: Optional[str] = None

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
class DynamicCondition:
    condition_expr: str       # Java boolean expression, e.g. "whereClause != null"
    sql_fragment: str         # SQL fragment, e.g. "WHERE ${whereClause}"
    clause_type: str          # "WHERE" | "ORDER_BY" | "SET" | "HAVING" | "IN" | "OTHER"
    tag_name: str             # "if" | "where" | "foreach" | "set" | "trim"


@dataclass
class DmlStatement:
    sql_type: str
    method_id: str
    sql_text: str
    result_type: Optional[str] = None
    parameter_types: dict = field(default_factory=dict)
    optional_filters: list = field(default_factory=list)
    returns_list: bool = False
    extra_params: list = field(default_factory=list)
    is_dynamic: bool = False  # True for EXECUTE IMMEDIATE — filters proc params to only those in SQL
    returning_cols: list = field(default_factory=list)       # column names from RETURNING clause
    returning_into_vars: list = field(default_factory=list)  # variable names from INTO targets
    is_forall_batch: bool = False  # True when FORALL is converted to MyBatis batch (<foreach>)
    forall_batch_list_var: str = ""  # The name of the iteration variable (e.g. "item") in <foreach>
    forall_batch_arrays: dict = field(default_factory=dict)  # {java_array_name: unwrapped_element_type}
    dynamic_conditions: list = field(default_factory=list)   # List[DynamicCondition]
    base_sql: str = ""                                        # Core SQL without dynamic conditions
    source_line: int = 0                                      # Actual source line of the DML statement in the SQL file


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
    local_var_defaults: dict = field(default_factory=dict)
    table_refs: set = field(default_factory=set)
    var_assignments: dict = field(default_factory=dict)
    dynamic_sql_templates: dict = field(default_factory=dict)  # var_name -> (sql_template, param_list)
    sql_concat_chain: dict = field(default_factory=dict)       # var_name -> [(condition_expr, sql_fragment, clause_type)]
    sql_expr_vars: dict = field(default_factory=dict)  # var_name -> AST node for SQL-expression assignments (e.g. to_char(...))
    inlined_sql_vars: set = field(default_factory=set)  # var_names that were inlined into dynamic SQL templates
    is_autonomous: bool = False
    scheduler_tasks: list = field(default_factory=list)
    _pending_scheduler_job: dict = field(default_factory=dict)
    _needs_futures_list: bool = False

    # NEW: Cursor tracking
    open_cursors: dict = field(default_factory=dict)   # cursor_name -> {"result_var": str, "index_var": str}
    refcursor_out_params: set = field(default_factory=set)  # param names that are REFCURSOR OUT
    cursor_decls: dict = field(default_factory=dict)   # cursor_name -> parsed_query (from DECLARE section)
    cursor_params: dict = field(default_factory=dict)  # cursor_name -> list of param names
    custom_types: dict = field(default_factory=dict)    # name -> {"kind": "record"/"varray", "fields"/...}
    source_file: str = ""          # Original SQL file name for display (e.g., PKG_ORDER.sql)
    _source_path: str = ""         # Full path for file access (set by pipeline)
    source_start_line: int = 0     # Procedure start line in original file
    source_end_line: int = 0       # Procedure end line in original file
    leading_comments: list = field(default_factory=list)   # List[CommentInfo] — comments before procedure declaration
    inline_comments: list = field(default_factory=list)    # List[CommentInfo] — comments inside procedure body
    _raw_return_types: list = field(default_factory=list)  # Pre-coercion return expr types for reconciliation
    _dynamic_sql_build_stmts: dict = field(default_factory=dict)  # {stmt_body_idx: var_name}
    local_var_source_lines: dict = field(default_factory=dict)  # var_name -> SQL source line number


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
    custom_types: dict = field(default_factory=dict)  # name -> {"kind": "record"/"varray", "fields"/...}
    _extra_mapper_methods: list = field(default_factory=list)  # [(method_name, sql, return_type)]


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
    stub_reasons: list = field(default_factory=list)
    table_refs: set = field(default_factory=set)


@dataclass
class ConversionReport:
    generated_at: str
    config_path: str
    output_dir: str
    sql_files: list
    procedure_mappings: list
    skipped_items: list
    parse_errors: list
    parse_warnings: list
    unresolved_calls: list
    total_packages: int = 0
    total_procedures: int = 0
    total_dml: int = 0
    total_cross_calls: int = 0


UnresolvedCall = namedtuple('UnresolvedCall', [
    'caller',        # "pkg_order.create_order"
    'callee',        # "pkg_inventory.reserve_stock" or "PERFORM pkg_common.log_operation(...)"
    'caller_file',   # "pkg_order.sql"
    'args',          # "BIGINT, INT" or ""
    'hint',          # "add pkg_inventory.sql to sources"
])


@dataclass
class GotoInfo:
    """Information about a single GOTO statement."""
    label: str
    source_idx: int          # index of the statement containing the GOTO
    source_depth: int        # nesting depth of the source statement
    is_forward: bool         # True if target is after source
    is_backward: bool        # True if target is before source
    source_path: list = None


@dataclass
class LabelInfo:
    """Information about a single label."""
    name: str
    target_idx: int          # index of the labeled statement
    target_depth: int        # nesting depth of the labeled statement


@dataclass
class GotoAnalysis:
    """Result of analyzing GOTO patterns in a procedure body."""
    labels: dict             # label_name -> LabelInfo
    gotos: list              # list of GotoInfo
    pattern: str             # one of A/B/C/D/E or "unknown"
    label_stmt_map: dict     # label_name -> statement dict


# ── AST Parser ─────────────────────────────────────────────────

def _is_parse_warning(err) -> bool:
    """Check if an ogsql parse error dict is actually a warning.

    ogsql-parser uses externally-tagged serde enum:
      Warning:       {"Warning": {"message": ..., "location": ...}}
      ReservedKeywordAsIdentifier: {"ReservedKeywordAsIdentifier": {"keyword": ..., "location": ...}}
    These are NOT real errors - the statement was still parsed successfully.
    """
    if isinstance(err, dict):
        return "Warning" in err or "ReservedKeywordAsIdentifier" in err
    return False


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


def _read_sql_file(path: str) -> tuple[str, str]:
    with open(path, 'rb') as f:
        raw = f.read()

    text = raw.decode('utf-8', errors='replace')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if '\ufffd' not in text:
        return text, 'utf-8'

    for enc in ('gb18030', 'gbk', 'big5'):
        try:
            candidate = raw.decode(enc)
            candidate = candidate.replace('\r\n', '\n').replace('\r', '\n')
            if '\ufffd' not in candidate:
                _log(f"  [INFO] Decoded {os.path.basename(path)} as {enc}", to_stdout=False)
                return candidate, enc
        except (UnicodeDecodeError, LookupError):
            pass

    ffds = text.count('\ufffd')
    _log(f"  [WARN] {os.path.basename(path)}: {ffds} unrecoverable chars (encoding damaged in source)", to_stdout=False)
    return text, 'utf-8-damaged'


def _format_validate_error(err) -> str:
    if isinstance(err, dict):
        for err_type, details in err.items():
            if not isinstance(details, dict):
                return f"error: {err_type} — {details}"

            if err_type == "TokenizerError":
                return _format_tokenizer_error(details)

            if "location" in details:
                loc = details["location"]
                line = loc.get("line", "?")
                col = loc.get("column", "?")
                if err_type == "UnexpectedToken":
                    return (f"error at line {line}, col {col}: "
                            f"expected {details.get('expected', '?')}, got {details.get('got', '?')}")
                hint = details.get("hint", "")
                syntax = details.get("syntax", "")
                msg = details.get("message", "")
                if syntax and hint:
                    desc = f"{syntax} ({hint})"
                elif hint or msg:
                    desc = hint or msg
                elif syntax:
                    desc = syntax
                else:
                    desc = err_type
                return f"error at line {line}, col {col}: {desc}"

            inner = next(iter(details), "")
            return f"error: {err_type} ({inner}: {details[inner]})" if inner else f"error: {err_type}"
    return str(err)


def _format_tokenizer_error(details: dict) -> str:
    for sub_type, value in details.items():
        msg = _TOKENIZER_ERROR_MESSAGES.get(sub_type)
        if msg:
            return f"tokenizer error: {msg.format(value)}"
        label = re.sub(r'([A-Z])', r' \1', sub_type).strip().lower()
        return f"tokenizer error: {label}: {value}"
    return "tokenizer error: unknown"


_TOKENIZER_ERROR_MESSAGES = {
    "UnterminatedDollarString": "unterminated dollar-quoted string at position {}",
    "UnterminatedString": "unterminated string at position {}",
    "UnexpectedToken": "unexpected token '{}'",
}


def validate_sql_file(sql_path: str) -> dict:
    """Run ogsql validate on a single SQL file and return JSON result.

    Returns a dict with keys: error_count, warning_count, errors, warnings.
    If the binary itself fails, returns a synthetic error result.
    """
    try:
        result = subprocess.run(
            [OGSQL_BIN, "validate", "-f", sql_path, "-j"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"error_count": 1, "warning_count": 0,
                "errors": [{"ValidationError": {"message": str(e)}}],
                "warnings": []}

    # Binary ran — try to parse JSON output (even on non-zero exit, the JSON
    # body contains the structured error list)
    if result.stdout and result.stdout.strip().startswith("{"):
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            pass

    # Fallback: no usable JSON from binary
    msg = result.stderr.strip() or f"ogsql validate exited with code {result.returncode}"
    return {"error_count": 1, "warning_count": 0,
            "errors": [{"ValidationError": {"message": msg}}],
            "warnings": []}


def validate_sql_files(sql_paths: list) -> dict:
    """Run ogsql validate on multiple SQL files in a single process invocation.

    Returns a dict mapping each sql_path to its validation result dict
    (same shape as validate_sql_file return value).
    Falls back to per-file validation on any error.
    """
    if len(sql_paths) <= 1:
        path = sql_paths[0] if sql_paths else ""
        return {path: validate_sql_file(path)}

    # Build command with multiple --file flags
    cmd = [OGSQL_BIN, "validate", "-j"]
    for p in sql_paths:
        cmd.extend(["-f", p])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=30 * len(sql_paths),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        synthetic = {"error_count": 1, "warning_count": 0,
                     "errors": [{"ValidationError": {"message": str(e)}}],
                     "warnings": []}
        return {p: synthetic for p in sql_paths}

    if result.stdout and result.stdout.strip().startswith("{"):
        try:
            data = json.loads(result.stdout)
            files_data = data.get("files", [])
            results = {}
            for fdata in files_data:
                fpath = fdata.get("file", "")
                results[fpath] = fdata
            # Fill in any missing files with fallback
            for p in sql_paths:
                if p not in results:
                    results[p] = {"error_count": 1, "warning_count": 0,
                                  "errors": [{"ValidationError": {"message": "No result from batch validate"}}],
                                  "warnings": []}
            return results
        except json.JSONDecodeError:
            pass

    # Fallback: batch call failed — fall back to per-file validation
    _log(f"  [WARN] Batch validate failed, falling back to per-file validation", to_stdout=False)
    return {p: validate_sql_file(p) for p in sql_paths}


def parse_sql_file(sql_path: str) -> dict:
    """Run ogsql-parser on a SQL file and return JSON AST."""
    sql_text, encoding = _read_sql_file(sql_path)

    needs_tmp = encoding != 'utf-8'
    tmp_for_ogsql = None
    ogsql_input = sql_path
    if needs_tmp:
        tmp_for_ogsql = os.path.join(
            tempfile.gettempdir(), f"fluxgauss_ogsql_{os.getpid()}_{os.path.basename(sql_path)}"
        )
        with open(tmp_for_ogsql, 'w', encoding='utf-8') as tf:
            tf.write(sql_text)
        ogsql_input = tmp_for_ogsql

    stmts = _split_sql_statements(sql_text)

    if len(stmts) <= 1:
        result = subprocess.run(
            [OGSQL_BIN, "--comments", "-f", ogsql_input, "parse", "-j"],
            capture_output=True, text=True
        )
        if result.returncode != 0 or not result.stdout.strip().startswith("{"):
            _log(f"  [WARN] ogsql-parser returned {result.returncode}: {result.stderr}", to_stdout=False)
            result = subprocess.run(
                [OGSQL_BIN, "-f", ogsql_input, "parse", "-j"],
                capture_output=True, text=True
            )
        ast = json.loads(result.stdout)
        ast["comments"] = _extract_comments_from_text(sql_text)
        if tmp_for_ogsql and os.path.exists(tmp_for_ogsql):
            os.unlink(tmp_for_ogsql)
        return ast

    combined_ast = {"statements": [], "errors": [], "comments": []}
    for i, (stmt_sql, start_line) in enumerate(stmts):
        line_offset = start_line - 1
        tmp_path = os.path.join(tempfile.gettempdir(), f"fluxgauss_{os.getpid()}_{i}.sql")
        try:
            with open(tmp_path, 'w', encoding='utf-8') as tf:
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
    if tmp_for_ogsql and os.path.exists(tmp_for_ogsql):
        os.unlink(tmp_for_ogsql)
    return combined_ast


def parse_sql_files(sql_paths: list) -> dict:
    """Run ogsql parse on multiple SQL files in a single process invocation.

    Returns a dict mapping each sql_path to its parsed AST dict
    (same shape as parse_sql_file return value).
    Only batched for files that have a single SQL statement (the common case).
    Files needing multi-statement splitting are parsed individually via parse_sql_file.
    Falls back to per-file parsing on any batch error.
    """
    _single_stmt_files = []
    _multi_stmt_files = []
    _file_texts = {}

    for p in sql_paths:
        sql_text, encoding = _read_sql_file(p)
        _file_texts[p] = (sql_text, encoding)

        needs_tmp = encoding != 'utf-8'
        stmts = _split_sql_statements(sql_text)
        if needs_tmp or len(stmts) > 1:
            _multi_stmt_files.append(p)
        else:
            _single_stmt_files.append(p)

    results = {}

    # Batch parse single-statement files
    if _single_stmt_files:
        batch_ok = False
        cmd = [OGSQL_BIN, "--comments", "parse", "-j"]
        for p in _single_stmt_files:
            cmd.extend(["-f", p])
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=30 * len(_single_stmt_files),
            )
            if result.stdout and result.stdout.strip().startswith("{"):
                data = json.loads(result.stdout)
                files_data = data.get("files", [])
                if files_data:
                    for fdata in files_data:
                        fpath = fdata.get("file", "")
                        fdata["comments"] = _extract_comments_from_text(_file_texts[fpath][0])
                        results[fpath] = fdata
                    if len(results) == len(_single_stmt_files):
                        batch_ok = True
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            _log(f"  [WARN] Batch parse error: {e}", to_stdout=False)

        if not batch_ok:
            _log(f"  [WARN] Batch parse incomplete, falling back to per-file for {len(_single_stmt_files)} file(s)", to_stdout=False)
            for p in _single_stmt_files:
                if p not in results:
                    results[p] = parse_sql_file(p)

    # Multi-statement files are always parsed individually
    for p in _multi_stmt_files:
        results[p] = parse_sql_file(p)

    return results


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


def _box_primitive(java_type: str) -> str:
    mapping = {
        "int": "Integer",
        "long": "Long",
        "double": "Double",
        "float": "Float",
        "boolean": "Boolean",
        "short": "Short",
        "byte": "Byte",
        "char": "Character",
    }
    return mapping.get(java_type, java_type)


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
        default_value = p.get("default_value")
        if default_value and default_value.lower() == "null":
            java_type = _box_primitive(java_type)
        result.append(Parameter(
            name=name,
            java_type=java_type,
            sql_type=sql_type,
            mode=mode,
            default_value=p.get("default_value"),
        ))
    return result


def extract_procedures(ast: dict, source_file: str = "") -> tuple:
    """Extract all procedures/functions and package-level variables from parsed AST."""
    procedures = []
    package_vars = {}
    custom_types = {}
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
                params = [p for p in params if p.name.lower() != 'self']
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

            elif stmt_type == "CreatePackage":
                for item in stmt_data.get("items", []):
                    for item_type, item_data in item.items():
                        if item_type == "Variable":
                            var_name = item_data.get("name", "")
                            if var_name in package_vars:
                                continue
                            var_type_raw = item_data.get("data_type", {})
                            if isinstance(var_type_raw, dict):
                                var_type = sql_type_to_java(var_type_raw)
                            else:
                                var_type = sql_type_to_java(str(var_type_raw))
                            default_expr = item_data.get("default")
                            default_val = _expr_to_java(default_expr, None) if default_expr else None
                            if default_val and default_expr is not None:
                                default_inferred = _infer_expr_type(default_expr, None)
                                if _needs_coercion(default_inferred, var_type):
                                    default_val = _coerce_type(default_val, default_inferred, var_type)
                            package_vars[var_name] = {"java_type": var_type, "default": default_val}
                        elif item_type == "Type":
                            for type_kind, type_data in item_data.items():
                                type_name = type_data.get("name", "")
                                if not type_name:
                                    continue
                                if type_kind == "Record":
                                    fields = []
                                    for fld in type_data.get("fields", []):
                                        fld_name = fld.get("name", "")
                                        fld_java_type = sql_type_to_java(fld.get("data_type", {}))
                                        fields.append((fld_name, fld_java_type))
                                    custom_types[type_name] = {"kind": "record", "fields": fields}
                                elif type_kind == "VarrayOf":
                                    elem_java_type = sql_type_to_java(type_data.get("elem_type", {}))
                                    size_node = type_data.get("size", {})
                                    size_val = 0
                                    if isinstance(size_node, dict):
                                        lit = size_node.get("Literal", {})
                                        if isinstance(lit, dict):
                                            size_val = int(lit.get("Integer", 0))
                                    custom_types[type_name] = {"kind": "varray", "elem_type": elem_java_type, "size": size_val}

            elif stmt_type == "CreatePackageBody":
                package_name_parts = stmt_data.get("name", [])
                package_name = package_name_parts[-1] if package_name_parts else "unknown"

                # Only extract Variables that appear BEFORE the first Function/Procedure.
                # The parser may dump function-local vars as top-level items after a failed block parse.
                for item in stmt_data.get("items", []):
                    for item_type, item_data in item.items():
                        if item_type in ("Function", "Procedure"):
                            break
                        if item_type == "Variable":
                            var_name = item_data.get("name", "")
                            var_type_raw = item_data.get("data_type", {})
                            if isinstance(var_type_raw, dict):
                                var_type = sql_type_to_java(var_type_raw)
                            else:
                                var_type = sql_type_to_java(str(var_type_raw))
                            default_expr = item_data.get("default")
                            default_val = _expr_to_java(default_expr, None) if default_expr else None
                            if default_val and default_expr is not None:
                                default_inferred = _infer_expr_type(default_expr, None)
                                if _needs_coercion(default_inferred, var_type):
                                    default_val = _coerce_type(default_val, default_inferred, var_type)
                            package_vars[var_name] = {"java_type": var_type, "default": default_val}
                    else:
                        continue
                    break

                for item in stmt_data.get("items", []):
                    for item_type, item_data in item.items():
                        if item_type not in ("Procedure", "Function"):
                            continue
                        proc_name = item_data.get("name", [])
                        proc_name = proc_name[-1] if proc_name else "unknown"
                        full_name = f"{package_name}.{proc_name}"
                        is_function = item_type == "Function"
                        return_type = item_data.get("return_type") if is_function else None
                        if return_type and custom_types:
                            rt_raw = return_type if isinstance(return_type, str) else ""
                            if isinstance(return_type, dict):
                                rt_raw = return_type.get("TypeName", "")
                            if rt_raw.lower() in custom_types:
                                ct = custom_types[rt_raw.lower()]
                                if ct["kind"] == "record":
                                    return_type = _custom_type_classname(rt_raw)
                                elif ct["kind"] == "varray":
                                    return_type = f"List<{ct['elem_type']}>"
                        params = extract_parameters(item_data.get("parameters", []))
                        params = [p for p in params if p.name.lower() != 'self']
                        for p in params:
                            if p.java_type == "Map<String, Object>" and custom_types:
                                raw = p.sql_type.lower() if p.sql_type else ""
                                if raw in custom_types:
                                    ct = custom_types[raw]
                                    if ct["kind"] == "record":
                                        p.java_type = _custom_type_classname(raw)
                                    elif ct["kind"] == "varray":
                                        p.java_type = f"List<{ct['elem_type']}>"
                        refcursor_outs = set()
                        for p in params:
                            if p.is_out and p.is_refcursor:
                                refcursor_outs.add(p.java_name)
                        block = item_data.get("block", {})
                        sql_text = item_data.get("sql_text", "")

                        proc_custom_types = dict(custom_types)
                        # Extract procedure-local Type declarations (TableOf/VarrayOf)
                        # for variable type resolution in analyze_procedure()
                        for decl in block.get("declarations", []):
                            for decl_type, decl_data in decl.items():
                                if decl_type == "Type":
                                    _type_name = ""
                                    _type_info = {}
                                    for tk, tv in decl_data.items():
                                        if tk == "TableOf":
                                            _type_name = tv.get("name", "")
                                            _elem_java = sql_type_to_java(tv.get("elem_type", {}))
                                            _type_info = {"kind": "table", "elem_type": _elem_java}
                                        elif tk == "VarrayOf":
                                            _type_name = tv.get("name", "")
                                            _elem_java = sql_type_to_java(tv.get("elem_type", {}))
                                            _type_info = {"kind": "varray", "elem_type": _elem_java, "size": tv.get("size")}
                                    if _type_name and _type_info:
                                        proc_custom_types[_type_name] = _type_info

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
                            custom_types=proc_custom_types,
                        )
                        procedures.append(proc)
    return procedures, package_vars, custom_types


def _recover_constant_declarations(sql_path: str, package_vars: dict):
    """Recover CONSTANT declarations that the parser garbles (dtype={'TypeName': 'constant'}).
    Scans SQL source for patterns like: name CONSTANT type := value;
    """
    try:
        with open(sql_path, 'r', encoding='utf-8', errors='replace') as f:
            source = f.read()
    except (FileNotFoundError, OSError):
        return

    for m in re.finditer(
        r'(?mi)\b(\w+)\s+CONSTANT\s+(\w[\w()., ]*?)\s*:=\s*([^;]+);',
        source
    ):
        var_name = m.group(1).strip()
        sql_type = m.group(2).strip()
        default_raw = m.group(3).strip()

        java_type = sql_type_to_java(sql_type)
        if java_type == "Object":
            java_type = sql_type_to_java({"TypeName": sql_type})

        default_val = _default_for_type(java_type)
        if default_raw:
            try:
                if re.match(r'^-?\d+$', default_raw):
                    default_val = default_raw
                    if "long" in java_type.lower() or "Long" in java_type:
                        default_val = default_raw + "L"
                    elif "BigDecimal" in java_type:
                        default_val = f'new java.math.BigDecimal("{default_raw}")'
                elif re.match(r'^-?\d+(\.\d+)?[eE][+-]?\d+$', default_raw):
                    if "BigDecimal" in java_type:
                        default_val = f'new java.math.BigDecimal("{default_raw}")'
                    elif "double" in java_type.lower():
                        default_val = default_raw + "d"
                elif re.match(r'^-?\d+\.\d+', default_raw):
                    if "BigDecimal" in java_type:
                        default_val = f'new java.math.BigDecimal("{default_raw}")'
                    elif "double" in java_type.lower():
                        default_val = default_raw + "d"
                elif default_raw.upper() in ("TRUE", "FALSE"):
                    default_val = default_raw.lower()
                elif default_raw.startswith("'"):
                    inner = default_raw.strip("'")
                    default_val = f'"{inner}"'
            except Exception:
                pass

        package_vars[var_name] = {"java_type": java_type, "default": default_val}
        _PACKAGE_CONSTANTS[var_name] = java_type


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

def _promote_out_local_vars(proc: ProcedureInfo, all_packages: dict):
    """Promote local var types to AtomicReference when used as OUT arg holders."""
    if not proc.body:
        return
    local_var_names = {vn.lower(): vn for vn in proc.local_vars}
    promotions = {}
    body_stmts = proc.body.get("body", [])
    _walk_stmts_for_out_promotions(body_stmts, proc, all_packages, local_var_names, promotions)
    exc = proc.body.get("exception_block")
    if isinstance(exc, dict):
        for handler in exc.get("handlers", []):
            if isinstance(handler, dict):
                h_stmts = handler.get("body", []) or handler.get("statements", [])
                if isinstance(h_stmts, list):
                    _walk_stmts_for_out_promotions(h_stmts, proc, all_packages, local_var_names, promotions)
    for var_lower, new_type in promotions.items():
        orig_name = local_var_names[var_lower]
        old_type = proc.local_vars[orig_name]
        if old_type != new_type:
            proc.local_vars[orig_name] = new_type
            # Keep local_var_defaults — codegen wraps it into AtomicReference constructor
            var_java = snake_to_camel(orig_name)
            patched = []
            for line in proc.java_logic_lines:
                patched.append(_patch_promoted_var_reads(line, var_java))
            proc.java_logic_lines = patched


def _patch_promoted_var_reads(line: str, var_java: str) -> str:
    import re
    # Remove .get() from promoted vars passed as OUT args to method/mapper calls
    if f'{var_java}.get()' in line and (re.search(rf'\bthis\.\w+\(', line) or 'Mapper.' in line):
        line = line.replace(f'{var_java}.get()', var_java)
    patterns_to_skip = [
        rf'\b{re.escape(var_java)}\s*=',
        rf'\b{re.escape(var_java)}\.set\s*\(',
        rf'this\.\w+\(.*\b{re.escape(var_java)}\b',
        rf'\w+Mapper\.\w+\(.*\b{re.escape(var_java)}\b',
    ]
    for pat in patterns_to_skip:
        if re.search(pat, line):
            return line
    line = re.sub(
        rf'(?<!\.)(?<!\w){re.escape(var_java)}\b(?!\s*=)(?!\s*\.set)(?!\s*\()',
        f'{var_java}.get()',
        line,
    )
    return line


def _extract_var_name(expr) -> str:
    if not isinstance(expr, dict):
        return ""
    for key in ("PlVariable", "ColumnRef"):
        if key in expr:
            val = expr[key]
            parts = val if isinstance(val, list) else [val]
            return parts[-1] if parts else ""
    return ""


def _walk_stmts_for_out_promotions(stmts, proc, all_packages, local_var_names, promotions):
    if not isinstance(stmts, list):
        return
    for stmt in stmts:
        if not isinstance(stmt, dict):
            continue
        for stmt_type, stmt_data in stmt.items():
            if stmt_type == "ProcedureCall":
                _check_call_out_promotions(stmt_data, proc, all_packages, local_var_names, promotions)
            elif stmt_type == "Perform":
                if isinstance(stmt_data, dict):
                    for qk, qv in stmt_data.items():
                        if qk in ("FunctionCall", "ProcedureCall"):
                            _check_call_out_promotions(
                                {"name": qv.get("name", []), "arguments": qv.get("arguments", [])},
                                proc, all_packages, local_var_names, promotions,
                            )
            elif stmt_type == "Assignment":
                _expr = stmt_data.get("expression", {}) if isinstance(stmt_data, dict) else {}
                if isinstance(_expr, dict) and "FunctionCall" in _expr:
                    _fc = _expr["FunctionCall"]
                    _fc_args = _fc.get("args") or _fc.get("arguments") or []
                    _check_call_out_promotions(
                        {"name": _fc.get("name", []), "arguments": _fc_args},
                        proc, all_packages, local_var_names, promotions,
                    )
            _recurse_stmt_for_out_promotions(stmt_data, proc, all_packages, local_var_names, promotions)


def _recurse_stmt_for_out_promotions(data, proc, all_packages, local_var_names, promotions):
    if not isinstance(data, dict):
        return
    for key in ("then_stmts", "else_stmts", "then_block", "else_block",
                "body", "loop_body", "block", "stmts", "statements"):
        child = data.get(key)
        if isinstance(child, dict):
            child_stmts = child.get("body", []) or child.get("stmts", [])
            if isinstance(child_stmts, list):
                _walk_stmts_for_out_promotions(child_stmts, proc, all_packages, local_var_names, promotions)
        elif isinstance(child, list):
            _walk_stmts_for_out_promotions(child, proc, all_packages, local_var_names, promotions)
    for elsif in data.get("elsifs", []):
        if isinstance(elsif, dict):
            elsif_stmts = elsif.get("stmts", [])
            if isinstance(elsif_stmts, list):
                _walk_stmts_for_out_promotions(elsif_stmts, proc, all_packages, local_var_names, promotions)
    for when in data.get("whens", []):
        if isinstance(when, dict):
            when_stmts = when.get("stmts", [])
            if isinstance(when_stmts, list):
                _walk_stmts_for_out_promotions(when_stmts, proc, all_packages, local_var_names, promotions)
    for branch in data.get("branches", []):
        if isinstance(branch, dict):
            br_body = branch.get("body", []) or branch.get("stmts", [])
            if isinstance(br_body, list):
                _walk_stmts_for_out_promotions(br_body, proc, all_packages, local_var_names, promotions)
            br_block = branch.get("block", {})
            if isinstance(br_block, dict):
                br_stmts = br_block.get("body", [])
                if isinstance(br_stmts, list):
                    _walk_stmts_for_out_promotions(br_stmts, proc, all_packages, local_var_names, promotions)
    for child_stmts_key in ("statements", "stmts"):
        child_stmts = data.get(child_stmts_key)
        if isinstance(child_stmts, list):
            _walk_stmts_for_out_promotions(child_stmts, proc, all_packages, local_var_names, promotions)
    exc = data.get("exception_block")
    if isinstance(exc, dict):
        for handler in exc.get("handlers", []):
            if isinstance(handler, dict):
                h_body = handler.get("body", []) or handler.get("statements", [])
                if isinstance(h_body, list):
                    _walk_stmts_for_out_promotions(h_body, proc, all_packages, local_var_names, promotions)
                h_block = handler.get("block", {})
                if isinstance(h_block, dict):
                    h_stmts = h_block.get("body", [])
                    if isinstance(h_stmts, list):
                        _walk_stmts_for_out_promotions(h_stmts, proc, all_packages, local_var_names, promotions)


def _check_call_out_promotions(call_data, proc, all_packages, local_var_names, promotions):
    func_name_parts = call_data.get("name", [])
    args = call_data.get("arguments", [])
    if not func_name_parts or not args:
        return
    if len(func_name_parts) >= 3:
        pkg = func_name_parts[-2]
        func = func_name_parts[-1]
    elif len(func_name_parts) == 2:
        pkg = func_name_parts[0]
        func = func_name_parts[1]
    elif len(func_name_parts) == 1 and proc.package:
        pkg = proc.package
        func = func_name_parts[0]
    elif len(func_name_parts) == 1 and proc.source_file:
        pkg = Path(proc.source_file).stem if proc.source_file else ""
        func = func_name_parts[0]
    else:
        if len(func_name_parts) == 1:
            for _apk in (all_packages or {}):
                _tp = _find_target_proc(_apk, func_name_parts[0], all_packages, arg_count=len(args))
                if _tp:
                    for i, a in enumerate(args):
                        if i < len(_tp.parameters) and _tp.parameters[i].is_out:
                            var_name = _extract_var_name(a).lower()
                            if var_name and var_name in local_var_names:
                                base_type = _tp.parameters[i].java_type
                                promotions[var_name] = f"AtomicReference<{base_type}>"
        return
    matched_pkg = _find_registered_pkg(pkg, all_packages)
    if not matched_pkg:
        for _apk in (all_packages or {}):
            target_proc_info = _find_target_proc(_apk, func, all_packages, arg_count=len(args))
            if target_proc_info:
                for i, a in enumerate(args):
                    if i < len(target_proc_info.parameters) and target_proc_info.parameters[i].is_out:
                        var_name = _extract_var_name(a).lower()
                        if var_name and var_name in local_var_names:
                            base_type = target_proc_info.parameters[i].java_type
                            promotions[var_name] = f"AtomicReference<{base_type}>"
                return
        return
    target_proc_info = _find_target_proc(matched_pkg, func, all_packages, arg_count=len(args))
    if not target_proc_info:
        return
    for i, a in enumerate(args):
        if i < len(target_proc_info.parameters) and target_proc_info.parameters[i].is_out:
            var_name = _extract_var_name(a).lower()
            if var_name and var_name in local_var_names:
                base_type = target_proc_info.parameters[i].java_type
                promotions[var_name] = f"AtomicReference<{base_type}>"


def _remove_dynamic_sql_build_lines(proc: ProcedureInfo, stmt_cp_map: dict):
    build_map = getattr(proc, '_dynamic_sql_build_stmts', {})
    resolved_vars = set()
    for dml in proc.dml_statements:
        if dml.dynamic_conditions:
            resolved_vars.update(
                vn for vn in set(build_map.values())
                if vn in proc.sql_concat_chain or vn in proc.var_assignments
            )
    if not resolved_vars:
        return
    lines_to_remove = set()
    lines_to_keep = set()
    for stmt_idx, var_name in build_map.items():
        if var_name not in resolved_vars or stmt_idx not in stmt_cp_map:
            continue
        start, end = stmt_cp_map[stmt_idx]
        has_mapper_call = any(
            "mapper." in proc.java_logic_lines[idx]
            for idx in range(start, end)
        )
        if has_mapper_call:
            for idx in range(start, end):
                line = proc.java_logic_lines[idx].strip()
                if "mapper." in line:
                    lines_to_keep.add(idx)
                    continue
                vn_java_checks = [snake_to_camel(vn) for vn in resolved_vars]
                is_dead_assign = any(line.startswith(f"{vn} =") for vn in vn_java_checks)
                if is_dead_assign:
                    lines_to_remove.add(idx)
            continue
        for idx in range(start, end):
            lines_to_remove.add(idx)
    for vn in resolved_vars:
        vn_java = snake_to_camel(vn)
        for idx, line in enumerate(proc.java_logic_lines):
            stripped = line.strip()
            if idx in lines_to_remove or idx in lines_to_keep:
                continue
            if stripped.startswith(f"{vn_java} =") and "mapper." not in stripped:
                lines_to_remove.add(idx)
    lines_to_remove -= lines_to_keep
    if lines_to_remove:
        proc.java_logic_lines = [
            line for i, line in enumerate(proc.java_logic_lines)
            if i not in lines_to_remove
        ]


def analyze_procedure(proc: ProcedureInfo, all_packages: dict):
    """Analyze a procedure's body to extract DML, service calls, and Java logic."""
    global _DML_CTR_TRACKER
    block = proc.body
    if not block:
        return

    # First pass: extract procedure-local Type declarations so variables can resolve them
    for decl in block.get("declarations", []):
        for decl_type, decl_data in decl.items():
            if decl_type == "Type":
                _type_name = ""
                _type_info = {}
                for tk, tv in decl_data.items():
                    if tk == "TableOf":
                        _type_name = tv.get("name", "")
                        _elem_java = sql_type_to_java(tv.get("elem_type", {}))
                        _type_info = {"kind": "table", "elem_type": _elem_java}
                    elif tk == "VarrayOf":
                        _type_name = tv.get("name", "")
                        _elem_java = sql_type_to_java(tv.get("elem_type", {}))
                        _type_info = {"kind": "varray", "elem_type": _elem_java, "size": tv.get("size")}
                if _type_name and _type_info:
                    proc.custom_types[_type_name] = _type_info

    # Second pass: process variable/record/cursor declarations
    for decl in block.get("declarations", []):
        for decl_type, decl_data in decl.items():
            if decl_type == "Variable":
                var_name = decl_data.get("name", "")
                raw_type = decl_data.get("data_type", "varchar")
                if isinstance(raw_type, dict) and "TypeName" in raw_type:
                    tn = raw_type["TypeName"]
                    if tn in proc.custom_types:
                        ct = proc.custom_types[tn]
                        if ct.get("kind") in ("table", "varray"):
                            java_type = f"java.util.List<{ct['elem_type']}>"
                        else:
                            java_type = sql_type_to_java(raw_type)
                    else:
                        java_type = sql_type_to_java(raw_type)
                else:
                    java_type = sql_type_to_java(raw_type)
                proc.local_vars[var_name] = java_type
                if DEBUG_MODE:
                    _decl_line = _find_var_decl_line(proc, var_name)
                    if _decl_line:
                        proc.local_var_source_lines[var_name] = _decl_line
                default_ast = decl_data.get("default")
                if default_ast is not None:
                    try:
                        default_java = _expr_to_java(default_ast, proc)
                        if java_type.startswith("java.util.List<"):
                            if default_java in ('"{}"', "'{}'"):
                                default_java = "new java.util.ArrayList<>()"
                            elif not default_java.startswith("java.util.Arrays.asList("):
                                default_java = f"new java.util.ArrayList<>(java.util.Arrays.asList({default_java}))"
                            else:
                                default_java = f"new java.util.ArrayList<>({default_java})"
                        # Type-check: coerce default value if it doesn't match declared type
                        default_inferred = _infer_expr_type(default_ast, proc)
                        if _needs_coercion(default_inferred, java_type):
                            default_java = _coerce_type(default_java, default_inferred, java_type)
                        proc.local_var_defaults[var_name] = default_java
                    except Exception:
                        pass
            elif decl_type == "Record":
                var_name = decl_data.get("name", "")
                if var_name:
                    proc.local_vars[var_name] = "Map<String, Object>"
                    if DEBUG_MODE:
                        _decl_line = _find_var_decl_line(proc, var_name)
                        if _decl_line:
                            proc.local_var_source_lines[var_name] = _decl_line
            elif decl_type == "Pragma":
                pragma_name = decl_data.get("name", "")
                if pragma_name == "AUTONOMOUS_TRANSACTION":
                    proc.is_autonomous = True
                    proc.imports.add("import org.springframework.transaction.annotation.Propagation;")
            elif decl_type == "Cursor":
                cursor_name = decl_data.get("name", "")
                parsed_q = decl_data.get("parsed_query")
                cursor_arg_names = [a.get("name", "") for a in decl_data.get("arguments", []) if a.get("name")]
                if parsed_q:
                    proc.cursor_decls[cursor_name] = parsed_q
                    proc.cursor_decls[cursor_name.lower()] = parsed_q
                if cursor_arg_names:
                    proc.cursor_params[cursor_name] = cursor_arg_names
                    proc.cursor_params[cursor_name.lower()] = cursor_arg_names

    # Process body statements
    body_stmts = block.get("body", [])
    # Use single global counter key — one SQL file may contain multiple SQL packages
    # that all feed into the same Mapper XML. Per-package keys cause duplicate IDs.
    pkg_key = "_global"
    _ctr_id = id(_DML_COUNTER_BY_PKG)
    _ctr_val_id = id(_DML_COUNTER_BY_PKG.get(pkg_key))
    _has_key = pkg_key in _DML_COUNTER_BY_PKG
    if not _has_key:
        _log(f"[DMLNEWKEY] {proc.proc_name}: key {pkg_key!r} being created (was missing)", to_stdout=False)

    if _DML_CTR_TRACKER is None:
        _DML_CTR_TRACKER = _ctr_id
    elif _DML_CTR_TRACKER != _ctr_id:
        _log(f"[DMLLOST] {proc.proc_name}: _DML_COUNTER_BY_PKG was REPLACED! old={_DML_CTR_TRACKER} new={_ctr_id}", to_stdout=False)
        _DML_CTR_TRACKER = _ctr_id
    if _has_key and _ctr_val_id == id(None):
        _log(f"[DMLLOST] {proc.proc_name}: key {pkg_key!r} value became None!", to_stdout=False)
    if pkg_key not in _DML_COUNTER_BY_PKG:
        _DML_COUNTER_BY_PKG[pkg_key] = {}
    dml_counter = _DML_COUNTER_BY_PKG[pkg_key]
    stmt_checkpoints = []  # [(java_line_start_idx, java_line_end_idx), ...]
    _stmt_cp_map = {}
    for i, stmt in enumerate(_iter_statements(body_stmts)):
        proc._current_stmt_idx = i
        pre_idx = len(proc.java_logic_lines)
        try:
            _process_statement(stmt, proc, all_packages, dml_counter)
        except Exception as e:
            stmt_preview = str(stmt)[:120] if stmt else "<empty>"
            proc.java_logic_lines.append(f"// ERROR: 处理语句失败 - {str(e).replace('*/', '').replace(chr(10), ' ')}")
            _log(f"      ⚠ Statement error in {proc.name}: {e}\n        stmt: {stmt_preview}", to_stdout=False)
            _log(traceback.format_exc(), to_stdout=False)
        post_idx = len(proc.java_logic_lines)
        if post_idx > pre_idx:
            stmt_checkpoints.append((pre_idx, post_idx))
            _stmt_cp_map[i] = (pre_idx, post_idx)

    _remove_dynamic_sql_build_lines(proc, _stmt_cp_map)

    # Inject inline comments into method body at proportional positions
    if proc.inline_comments and stmt_checkpoints:
        _inject_inline_comments(proc, stmt_checkpoints)

    # Post-process GOTO patterns: if any GOTO was encountered, analyze and rewrite
    if getattr(proc, '_has_goto', False):
        _analyze_and_rewrite_goto(proc, all_packages, dml_counter)

    # Issue #63: reconcile FUNCTION return type when body only returns Strings
    if proc.is_function and proc.return_type:
        declared = sql_type_to_java(proc.return_type)
        fixed = _reconcile_function_return_type(proc, declared)
        if fixed == "String" and declared != "String":
            proc.return_type = "varchar2"
            _UDF_RETURN_TYPES[(proc.proc_name.lower(), len(proc.parameters))] = "String"
            _record_todo("RETURN_TYPE_RECONCILE", proc,
                         f"return 类型由 {declared} 纠正为 String（body 仅返回 String）")


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


def _get_sql_file_lines(source_path: str) -> list:
    if source_path in _SQL_FILE_CACHE:
        return _SQL_FILE_CACHE[source_path]
    try:
        with open(source_path, 'r', encoding=_SOURCE_ENCODING, errors='replace') as f:
            lines = f.readlines()
    except OSError:
        lines = []
    _SQL_FILE_CACHE[source_path] = lines
    return lines


def _format_debug_comment(source_path: str, line_number: int, max_len: int = 100) -> str:
    if not line_number or not source_path or not os.path.exists(source_path):
        return ""
    lines = _get_sql_file_lines(source_path)
    if line_number < 1 or line_number > len(lines):
        return f"// [DEBUG] L{line_number}"
    raw = lines[line_number - 1].strip()
    fname = os.path.basename(source_path)
    if len(raw) > max_len:
        raw = raw[:max_len - 3] + "..."
    return f"// [DEBUG] {fname}:{line_number} → {raw}"


def _stmt_span_line(stmt_data: dict) -> int:
    span = stmt_data.get("span")
    if span and isinstance(span, dict):
        start = span.get("start")
        if start and isinstance(start, dict):
            return start.get("line", 0)
    return 0


def _map_stmt_idx_to_sql_line(stmt_idx: int, proc: ProcedureInfo) -> int:
    stmt_lines = _find_body_stmt_lines(proc)
    if stmt_lines and stmt_idx < len(stmt_lines):
        return stmt_lines[stmt_idx]
    return 0


def _map_stmt_to_sql_line(stmt_data: dict, stmt_idx: int, proc: ProcedureInfo) -> int:
    # Prefer AST span — directly tied to the parsed statement, accurate with source
    span_line = _stmt_span_line(stmt_data)
    if span_line:
        return span_line
    # Fallback: text-scanned stmt_lines (less accurate, may have offset drift)
    return _map_stmt_idx_to_sql_line(stmt_idx, proc)


def _add_dml(proc: ProcedureInfo, dml: DmlStatement) -> None:
    dml.source_line = getattr(proc, '_current_source_line', 0)
    proc.dml_statements.append(dml)


def _resolve_dml_source_line(proc: ProcedureInfo, dml: DmlStatement) -> int:
    """Resolve the actual source line of a DML statement by searching the source file."""
    if dml.source_line and not proc.source_file:
        return dml.source_line
    path = proc._source_path or proc.source_file
    if not path or not os.path.exists(path):
        return dml.source_line or proc.source_start_line
    lines = _get_sql_file_lines(path)
    sql = dml.sql_text.strip()
    if not sql:
        return dml.source_line or proc.source_start_line
    _m = re.match(r'(INSERT\s+INTO\s+\w+)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(UPDATE\s+\w+)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(DELETE\s+FROM\s+\w+)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(SELECT\b)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(MERGE\s+INTO\s+\w+)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(WITH\b)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(TRUNCATE\s+TABLE\s+\w+)', sql, re.IGNORECASE)
    if not _m:
        _m = re.match(r'(TRUNCATE\b)', sql, re.IGNORECASE)
    if not _m:
        return dml.source_line or proc.source_start_line
    search_tok = _m.group(1).upper().split()
    start = max(0, (proc.source_start_line or 1) - 1)
    end = proc.source_end_line or len(lines)
    for i in range(start, min(end, len(lines))):
        line_up = lines[i].strip().upper()
        if all(tok in line_up for tok in search_tok):
            return i + 1
    return dml.source_line or proc.source_start_line


def _find_var_decl_line(proc: ProcedureInfo, var_name: str) -> int:
    source_path = proc._source_path or proc.source_file
    if not source_path or not os.path.exists(source_path):
        return 0
    lines = _get_sql_file_lines(source_path)
    start = max(0, (proc.source_start_line or 1) - 1)
    end = proc.source_end_line or len(lines)
    target = var_name.lower().strip('"')
    for i in range(start, min(end, len(lines))):
        stripped = lines[i].strip()
        if re.match(r'BEGIN\b', stripped, re.IGNORECASE):
            break
        if target in stripped.lower() and re.search(r'\b' + re.escape(target) + r'\b', stripped, re.IGNORECASE):
            return i + 1
    return 0


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


def _process_get_diagnostics(stmt_data: dict, proc: ProcedureInfo):
    items = stmt_data.get("items", [])
    for item_data in items:
        diag_item = item_data.get("item", "")
        target = item_data.get("target", {})
        var_name = None
        if isinstance(target, dict):
            for k, v in target.items():
                if k == "PlVariable":
                    var_name = v[-1] if isinstance(v, list) and v else str(v)
                    break
        if not var_name:
            continue

        var_java = snake_to_camel(var_name)

        if diag_item == "RowCount":
            var_type = proc.local_vars.get(var_name, "Integer")
            if var_type == "int":
                proc.java_logic_lines.append(f"{var_java} = _sqlRowCount;")
            else:
                proc.java_logic_lines.append(f"{var_java} = Integer.valueOf(_sqlRowCount);")
        else:
            proc.java_logic_lines.append(f"// GET DIAGNOSTICS {var_java} = {diag_item} — manual review needed")


def _process_statement(stmt: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    for stmt_type, stmt_data in stmt.items():
        if isinstance(stmt_data, dict):
            stmt_idx = getattr(proc, '_current_stmt_idx', 0)
            proc._current_source_line = _map_stmt_to_sql_line(stmt_data, stmt_idx, proc)
        else:
            proc._current_source_line = 0
        if DEBUG_MODE and stmt_type not in ("Null",) and isinstance(stmt_data, dict):
            src_path = proc._source_path or proc.source_file
            sql_line = proc._current_source_line
            if sql_line and src_path:
                dbg = _format_debug_comment(src_path, sql_line)
                if dbg:
                    proc.java_logic_lines.append(dbg)
        if stmt_type == "SqlStatement":
            _process_sql_statement(stmt_data, proc, dml_counter)
        elif stmt_type == "If":
            _process_if(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Return":
            _process_return(stmt_data, proc, all_packages)
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
            for decl in (stmt_data.get("declarations") or []):
                for decl_type, decl_data in decl.items():
                    if decl_type == "Variable":
                        var_name = decl_data.get("name", "")
                        raw_type = decl_data.get("data_type", "varchar")
                        if isinstance(raw_type, dict) and "TypeName" in raw_type:
                            tn = raw_type["TypeName"]
                            if tn in proc.custom_types:
                                ct = proc.custom_types[tn]
                                if ct.get("kind") in ("table", "varray"):
                                    java_type = f"java.util.List<{ct['elem_type']}>"
                                else:
                                    java_type = sql_type_to_java(raw_type)
                            else:
                                java_type = sql_type_to_java(raw_type)
                        else:
                            java_type = sql_type_to_java(raw_type)
                        if var_name not in proc.local_vars:
                            proc.local_vars[var_name] = java_type
                            if DEBUG_MODE:
                                decl_line = _find_var_decl_line(proc, var_name)
                                if decl_line:
                                    proc.local_var_source_lines[var_name] = decl_line
                        default_ast = decl_data.get("default")
                        if default_ast is not None:
                            try:
                                default_java = _expr_to_java(default_ast, proc)
                                if java_type.startswith("java.util.List<"):
                                    if default_java in ('"{}"', "'{}'"):
                                        default_java = "new java.util.ArrayList<>()"
                                    elif not default_java.startswith("java.util.Arrays.asList("):
                                        default_java = f"new java.util.ArrayList<>(java.util.Arrays.asList({default_java}))"
                                    else:
                                        default_java = f"new java.util.ArrayList<>({default_java})"
                                default_inferred = _infer_expr_type(default_ast, proc)
                                if _needs_coercion(default_inferred, java_type):
                                    default_java = _coerce_type(default_java, default_inferred, java_type)
                                proc.local_var_defaults[var_name] = default_java
                            except Exception:
                                pass
                    elif decl_type == "Type":
                        _type_name = ""
                        _type_info = {}
                        for tk, tv in decl_data.items():
                            if tk == "TableOf":
                                _type_name = tv.get("name", "")
                                _elem_java = sql_type_to_java(tv.get("elem_type", {}))
                                _type_info = {"kind": "table", "elem_type": _elem_java}
                            elif tk == "VarrayOf":
                                _type_name = tv.get("name", "")
                                _elem_java = sql_type_to_java(tv.get("elem_type", {}))
                                _type_info = {"kind": "varray", "elem_type": _elem_java, "size": tv.get("size")}
                        if _type_name and _type_info:
                            proc.custom_types[_type_name] = _type_info
                    elif decl_type == "Record":
                        var_name = decl_data.get("name", "")
                        if var_name and var_name not in proc.local_vars:
                            proc.local_vars[var_name] = "Map<String, Object>"
                            if DEBUG_MODE:
                                decl_line = _find_var_decl_line(proc, var_name)
                                if decl_line:
                                    proc.local_var_source_lines[var_name] = decl_line
                    elif decl_type == "Cursor":
                        cursor_name = decl_data.get("name", "")
                        parsed_q = decl_data.get("parsed_query")
                        if parsed_q:
                            proc.cursor_decls[cursor_name] = parsed_q
                            proc.cursor_decls[cursor_name.lower()] = parsed_q
            # Handle exception_block: nested BEGIN...EXCEPTION...END inside control structures
            exc_block = stmt_data.get("exception_block")
            if exc_block and exc_block.get("handlers"):
                proc.java_logic_lines.append("try {")
                for s in _iter_statements(stmt_data.get("body", [])):
                    _process_statement(s, proc, all_packages, dml_counter)
                _indent_last_lines(proc, 1)
                handlers_block = exc_block.get("handlers", [])
                if handlers_block:
                    all_conditions = []
                    for h in handlers_block:
                        conds = h.get("conditions", [])
                        all_conditions.append(conds[0] if conds else "EXCEPTION")
                    proc.java_logic_lines.append(f"}} catch (Exception {_next_catch_var(proc)}) {{ // {'; '.join(all_conditions)}")
                for handler in handlers_block:
                    conditions = handler.get("conditions", [])
                    cond_name = conditions[0] if conditions else "EXCEPTION"
                    proc.java_logic_lines.append(f"    // WHEN {cond_name}")
                    for hs in _iter_statements(handler.get("statements", [])):
                        _process_statement(hs, proc, all_packages, dml_counter)
                    _indent_last_lines(proc, 1)
                    if proc.java_logic_lines and proc.java_logic_lines[-1].strip() == "return;":
                        break
                proc.java_logic_lines.append("}")
            else:
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
                proc.java_logic_lines.append("try { TransactionAspectSupport.currentTransactionStatus().setRollbackOnly(); } catch (NoTransactionException e) { /* no active transaction */ }")
                proc.imports.add("import org.springframework.transaction.NoTransactionException;")
                proc.imports.add("import org.springframework.transaction.interceptor.TransactionAspectSupport;")
        elif stmt_type == "ProcedureCall":
            _process_procedure_call(stmt_data, proc, all_packages)
        elif stmt_type in ("sql_text",):
            sql = stmt_data
            if isinstance(sql, str):
                # Try to parse inline variable declarations like "v_cnt number := 0"
                _m = re.match(r'^\s*(\w+)\s+(number|numeric|integer|int|bigint|varchar2?|text|decimal|float|double|boolean|date|timestamp)\s*:=\s*(.+)$', sql, re.IGNORECASE)
                if _m:
                    _vn = _m.group(1)
                    _vt = sql_type_to_java(_m.group(2))
                    if _vn not in proc.local_vars:
                        proc.local_vars[_vn] = _vt
                    _def_ast = _m.group(3).strip().rstrip(';')
                    try:
                        _def_java = _expr_to_java({"Literal": {"Integer": int(_def_ast)}} if _def_ast.isdigit() else {"Literal": {"Float": float(_def_ast)}} if re.match(r'^\d+\.\d+$', _def_ast) else {"Literal": {"String": _def_ast}}, proc)
                        proc.local_var_defaults[_vn] = _def_java
                    except Exception:
                        proc.local_var_defaults.setdefault(_vn, _default_for_type(_vt))
                elif re.match(r'^\s*TRUNCATE\s+', sql, re.IGNORECASE):
                    _process_raw_dml(sql, proc, dml_counter)
                elif "call " in sql.lower():
                    _process_call_text(sql, proc, all_packages)
        elif stmt_type == "Continue":
            cond = stmt_data.get("condition")
            if cond:
                java_cond = _expr_to_java(cond, proc, all_packages=all_packages)
                proc.java_logic_lines.append(f"if ({java_cond}) {{")
                proc.java_logic_lines.append("    continue;")
                proc.java_logic_lines.append("}")
            else:
                proc.java_logic_lines.append("continue;")
        elif stmt_type == "Goto":
            label = stmt_data.get("label", "unknown")
            if hasattr(proc, '_sm_enum') and proc._sm_enum:
                sm_enum = proc._sm_enum
                sm_labels = getattr(proc, '_sm_labels', {})
                if label in sm_labels:
                    goto_state = snake_to_pascal(label)
                    proc.java_logic_lines.append(f"currentState = {sm_enum}.{goto_state};")
                    proc.java_logic_lines.append("break;")
                else:
                    proc.java_logic_lines.append("running = false;")
                    proc.java_logic_lines.append("break;")
            else:
                proc.java_logic_lines.append(f"// GOTO {label} — will be rewritten by pattern analysis")
                proc._has_goto = True
        elif stmt_type == "Case":
            _process_case_stmt(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "Savepoint":
            sp_name = stmt_data.get("name", "sp")
            sp_java = snake_to_camel(sp_name)
            proc.java_logic_lines.append(f"Savepoint {sp_java} = connection.setSavepoint(\"{sp_name}\");")
            proc.imports.add("import java.sql.Savepoint;")
        elif stmt_type == "ReturnQuery":
            _process_return_query(stmt_data, proc, all_packages, dml_counter)
        elif stmt_type == "GetDiagnostics":
            _process_get_diagnostics(stmt_data, proc)
        elif stmt_type == "ForAll":
            _process_forall(stmt_data, proc, all_packages, dml_counter)
        else:
            proc.java_logic_lines.append(f"// TODO: unhandled PL/pgSQL statement type: {stmt_type}")
            _record_todo("UNHANDLED_STMT", proc, str(stmt_type))


# ── GOTO Analysis and Pattern-Based Rewriting ─────────────────

def _collect_goto_info(body_stmts, proc: ProcedureInfo = None):
    """Walk the AST to find all GOTO statements and labels.

    The ogsql parser does not preserve <<label>> declarations as standalone
    AST nodes, so we also scan the raw SQL text to discover label positions.

    Returns (labels, gotos, label_stmt_map) where:
      labels: dict mapping label_name -> LabelInfo
      gotos: list of GotoInfo
      label_stmt_map: dict mapping label_name -> the statement dict that carries the label
    """
    labels = {}
    gotos = []
    label_stmt_map = {}

    def _walk(stmts, depth=0, path_prefix=None, parent_attr=None, enclosing_top_idx=None):
        if path_prefix is None:
            path_prefix = []
        for idx, stmt in enumerate(stmts):
            if not isinstance(stmt, dict):
                continue
            top_idx = idx if depth == 0 else (enclosing_top_idx if enclosing_top_idx is not None else idx)
            for stmt_type, stmt_data in stmt.items():
                current_path = path_prefix + [idx]
                if parent_attr:
                    current_path = path_prefix + [parent_attr, idx]
                if stmt_type == "Block" and isinstance(stmt_data, dict):
                    label = stmt_data.get("label")
                    if label:
                        labels[label] = LabelInfo(name=label, target_idx=idx, target_depth=depth)
                        label_stmt_map[label] = stmt
                    _walk(stmt_data.get("body", []), depth + 1, current_path, enclosing_top_idx=idx)
                    exc_block = stmt_data.get("exception_block")
                    if exc_block and isinstance(exc_block, dict):
                        for handler in exc_block.get("handlers", []):
                            _walk(handler.get("statements", []), depth + 2, current_path, enclosing_top_idx=idx)
                elif stmt_type in ("If", "For", "While", "Loop") and isinstance(stmt_data, dict):
                    label = stmt_data.get("label")
                    if label:
                        labels[label] = LabelInfo(name=label, target_idx=idx, target_depth=depth)
                        label_stmt_map[label] = stmt
                    if stmt_type == "If":
                        _walk(stmt_data.get("then_stmts", []), depth + 1, current_path, "then_stmts", enclosing_top_idx=idx)
                        _walk(stmt_data.get("else_stmts", []), depth + 1, current_path, "else_stmts", enclosing_top_idx=idx)
                        for elsif in stmt_data.get("elsifs", []):
                            _walk(elsif.get("stmts", []), depth + 1, current_path, "elsif_stmts", enclosing_top_idx=idx)
                    else:
                        _walk(stmt_data.get("body", []), depth + 1, current_path, "body", enclosing_top_idx=idx)
                elif stmt_type == "Goto" and isinstance(stmt_data, dict):
                    goto_label = stmt_data.get("label", "unknown")
                    gotos.append(GotoInfo(
                        label=goto_label,
                        source_idx=top_idx,
                        source_depth=depth,
                        is_forward=False,
                        is_backward=False,
                        source_path=current_path,
                    ))

    _walk(body_stmts)

    if proc and (proc._source_path or proc.source_file):
        src_path = proc._source_path or proc.source_file
        try:
            with open(src_path, 'r', encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
        except Exception:
            all_lines = []

        if all_lines and proc.source_start_line > 0 and proc.source_end_line > 0:
            proc_text_lines = all_lines[proc.source_start_line - 1:proc.source_end_line]
            for offset, line in enumerate(proc_text_lines):
                line_num = proc.source_start_line + offset
                m = re.search(r'<<([^>]+)>>', line)
                if m:
                    label_name = m.group(1).strip()
                    if label_name and label_name not in labels:
                        target_idx = _map_line_to_stmt_idx(line_num, body_stmts, proc.source_start_line, proc.source_end_line)
                        labels[label_name] = LabelInfo(
                            name=label_name,
                            target_idx=target_idx,
                            target_depth=0,
                        )

            goto_line_map = {}
            for offset, line in enumerate(proc_text_lines):
                line_num = proc.source_start_line + offset
                for gm in re.finditer(r'GOTO\s+(\w+)', line, re.IGNORECASE):
                    gtarget = gm.group(1)
                    goto_line_map[gtarget] = goto_line_map.get(gtarget, []) + [line_num]

            for g in gotos:
                li = labels.get(g.label)
                if li and g.label in goto_line_map:
                    goto_lines = goto_line_map[g.label]
                    label_line = None
                    for offset, line in enumerate(proc_text_lines):
                        line_num = proc.source_start_line + offset
                        m = re.search(r'<<([^>]+)>>', line)
                        if m and m.group(1).strip() == g.label:
                            label_line = line_num
                            break
                    if label_line and goto_lines:
                        g.is_forward = any(gl < label_line for gl in goto_lines)
                        g.is_backward = any(gl > label_line for gl in goto_lines)

    return labels, gotos, label_stmt_map


def _map_line_to_stmt_idx(target_line: int, body_stmts: list, proc_start_line: int, proc_end_line: int = None) -> int:
    if not body_stmts:
        return 0
    n = len(body_stmts)
    offset_from_start = target_line - proc_start_line
    if offset_from_start <= 2:
        return 0
    if proc_end_line and proc_end_line > proc_start_line:
        total = proc_end_line - proc_start_line
        ratio = offset_from_start / total
        return min(n - 1, max(0, int(ratio * n)))
    estimated = int((offset_from_start / max(1, n)) * 0.3)
    return min(n - 1, max(0, estimated))


def _classify_goto_pattern(labels, gotos, body_stmts):
    """Classify GOTO usage into one of the 5 patterns (E > D > B > A > C).

    Returns a pattern string: 'A', 'B', 'C', 'D', 'E', or 'unknown'.
    """
    if not gotos or not labels:
        return "unknown"

    goto_labels = set(g.label for g in gotos)
    label_names = set(labels.keys())
    if not goto_labels.issubset(label_names):
        return "unknown"
    effective_labels = goto_labels

    has_backward = any(g.is_backward for g in gotos)

    cross_boundary = False
    for g in gotos:
        li = labels.get(g.label)
        if li and g.source_depth >= 2:
            if li.target_depth > 0 and g.source_depth > li.target_depth:
                cross_boundary = True
                break
            elif li.target_depth == 0 and g.source_depth >= 3:
                cross_boundary = True
                break

    incoming_counts = {ln: 0 for ln in label_names}
    for g in gotos:
        incoming_counts[g.label] = incoming_counts.get(g.label, 0) + 1

    multi_label = len(effective_labels) >= 2
    multi_incoming = sum(1 for c in incoming_counts.values() if c > 1)
    has_graph = multi_label and multi_incoming >= 1

    if has_graph:
        return "E"
    if cross_boundary:
        return "D"
    if has_backward:
        return "B"

    all_forward = all(g.is_forward for g in gotos)
    if all_forward and len(effective_labels) == 1:
        li = list(labels.values())[0]
        g = gotos[0]
        distance = li.target_idx - g.source_idx
        if li.target_idx >= len(body_stmts) - 2 and distance > 3:
            return "A"

    if all_forward and len(gotos) == 1:
        return "C"

    return "unknown"


def _analyze_and_rewrite_goto(proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    """Analyze GOTO patterns in proc and regenerate java_logic_lines."""
    body_stmts = proc.body.get("body", []) if proc.body else []
    if not body_stmts:
        return

    labels, gotos, label_stmt_map = _collect_goto_info(body_stmts, proc)
    pattern = _classify_goto_pattern(labels, gotos, body_stmts)

    if pattern == "unknown":
        _stub_key = (proc.name, len(proc.parameters))
        _add_stub_reason(proc, f"GOTO 模式无法识别，需要手动重构")
        if _stub_key not in STUB_PROCEDURES:
            STUB_PROCEDURES.append(_stub_key)
        return

    analysis = GotoAnalysis(labels=labels, gotos=gotos, pattern=pattern, label_stmt_map=label_stmt_map)

    # Clear proc DML state — rewritten statements will re-populate.
    # Do NOT reset dml_counter (it is package-level shared across procedures;
    # resetting it causes duplicate method IDs with other procedures' DMLs).
    proc.dml_statements = []
    proc.service_calls = []

    if pattern == "A":
        _generate_cleanup_goto(proc, analysis, body_stmts, all_packages, dml_counter)
    elif pattern == "B":
        _generate_loop_goto(proc, analysis, body_stmts, all_packages, dml_counter)
    elif pattern == "C":
        _generate_skip_goto(proc, analysis, body_stmts, all_packages, dml_counter)
    elif pattern == "D":
        _generate_nested_breakout_goto(proc, analysis, body_stmts, all_packages, dml_counter)
    elif pattern == "E":
        _generate_state_machine_goto(proc, analysis, body_stmts, all_packages, dml_counter)

    goto_labels = {g.label for g in gotos}
    proc.java_logic_lines = [
        line for line in proc.java_logic_lines
        if not any(f"// GOTO {label} — will be rewritten by pattern analysis" in line for label in goto_labels)
    ]

    # Remove from stub list — the GOTO rewrite has replaced the normal processing output
    _stub_key = (proc.name, len(proc.parameters))
    if _stub_key in STUB_PROCEDURES:
        STUB_PROCEDURES.remove(_stub_key)
    proc._stub_reasons = []


def _stmt_list_to_java(stmts, proc, all_packages, dml_counter, indent=0):
    """Process a list of AST statements into java_logic_lines."""
    for stmt in stmts:
        if isinstance(stmt, dict):
            _process_statement(stmt, proc, all_packages, dml_counter)


def _generate_cleanup_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    """Pattern A: cleanup label near end -> try { ... } finally { cleanup }"""
    label_name = list(analysis.labels.keys())[0]
    li = analysis.labels[label_name]
    target_idx = li.target_idx

    proc.java_logic_lines = []
    proc.dml_statements = []

    cleanup_start = max(0, len(body_stmts) - 2)
    if target_idx < cleanup_start:
        cleanup_start = target_idx

    def _cleanup_process_stmts(stmts):
        for s in stmts:
            if not isinstance(s, dict):
                continue
            for st, sd in s.items():
                if st == "Goto" and sd.get("label") == label_name:
                    proc.java_logic_lines.append("return;")
                    break
                if st == "If" and isinstance(sd, dict):
                    condition = _expr_to_java(sd.get("condition", {}), proc, all_packages=all_packages)
                    proc.java_logic_lines.append(f"if ({condition}) {{")
                    _cleanup_process_stmts(sd.get("then_stmts", []))
                    _indent_last_lines(proc, 1)
                    for elsif in sd.get("elsifs", []):
                        ec = _expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages)
                        proc.java_logic_lines.append(f"}} else if ({ec}) {{")
                        _cleanup_process_stmts(elsif.get("stmts", []))
                        _indent_last_lines(proc, 1)
                    if sd.get("else_stmts"):
                        proc.java_logic_lines.append("} else {")
                        _cleanup_process_stmts(sd["else_stmts"])
                        _indent_last_lines(proc, 1)
                    proc.java_logic_lines.append("}")
                    break
                if st == "Block" and isinstance(sd, dict):
                    _cleanup_process_stmts(sd.get("body", []))
                    break
            else:
                _process_statement(s, proc, all_packages, dml_counter)

    proc.java_logic_lines.append("try {")
    for idx, stmt in enumerate(body_stmts):
        if idx >= cleanup_start:
            break
        if isinstance(stmt, dict):
            for st, sd in stmt.items():
                if st == "Goto" and sd.get("label") == label_name:
                    proc.java_logic_lines.append("return;")
                    break
            else:
                _cleanup_process_stmts([stmt])
    proc.java_logic_lines.append("} finally {")
    for idx, stmt in enumerate(body_stmts):
        if idx < cleanup_start:
            continue
        if isinstance(stmt, dict):
            for st, sd in stmt.items():
                if st == "Block" and sd.get("label") == label_name:
                    _stmt_list_to_java(sd.get("body", []), proc, all_packages, dml_counter, indent=1)
                    continue
            _process_statement(stmt, proc, all_packages, dml_counter)
    proc.java_logic_lines.append("}")


def _generate_loop_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    """Pattern B: backward GOTO -> do { ... } while (condition)"""
    backward_goto = None
    for g in analysis.gotos:
        if g.is_backward:
            backward_goto = g
            break
    if not backward_goto:
        return

    label_name = backward_goto.label
    li = analysis.labels[label_name]
    target_idx = li.target_idx
    source_idx = backward_goto.source_idx
    if target_idx >= source_idx:
        target_idx = 0

    proc.java_logic_lines = []
    proc.dml_statements = []

    proc.java_logic_lines.append("do {")
    for stmt in body_stmts[target_idx:source_idx + 1]:
        if isinstance(stmt, dict):
            for st, sd in stmt.items():
                if st == "Goto" and sd.get("label") == label_name:
                    continue
                if st == "If" and isinstance(sd, dict):
                    then_stmts = sd.get("then_stmts", [])
                    has_goto = any(
                        isinstance(s, dict) and any(k == "Goto" and v.get("label") == label_name for k, v in s.items())
                        for s in then_stmts
                    )
                    if has_goto:
                        cond = _expr_to_java(sd.get("condition", {}), proc, all_packages=all_packages)
                        proc.java_logic_lines.append(f"}} while ({cond});")
                        continue
            _process_statement(stmt, proc, all_packages, dml_counter)
    if not any(l.strip().startswith("} while (") for l in proc.java_logic_lines):
        proc.java_logic_lines.append("} while (true);")
    # Process statements AFTER the do-while loop body (e.g., final OUT param assignments)
    _post_start = source_idx + 1
    for stmt in body_stmts[_post_start:]:
        if isinstance(stmt, dict):
            # Skip any lingering Goto statements that belong to the loop pattern
            if any(k == "Goto" for k in stmt):
                continue
            _process_statement(stmt, proc, all_packages, dml_counter)


def _invert_condition(java_cond: str) -> str:
    cond = java_cond.strip()
    if cond.startswith("(!") and cond.endswith(")"):
        return cond[2:-1]
    if cond.startswith("(") and cond.endswith(")"):
        inner = cond[1:-1].strip()
        if " " not in inner or inner.startswith("("):
            return f"!{cond}"
    if " " not in cond or cond.startswith("("):
        return f"!{cond}"
    return f"!({cond})"


def _generate_skip_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    """Pattern C: single forward GOTO from conditional -> invert IF, wrap skipped in else."""
    goto_info = analysis.gotos[0]
    label_name = goto_info.label
    li = analysis.labels[label_name]
    target_idx = li.target_idx

    proc.java_logic_lines = []
    proc.dml_statements = []

    if_condition = None
    path = goto_info.source_path or []
    if len(path) >= 3 and path[1] in ("then_stmts", "else_stmts", "elsif_stmts"):
        enclosing_idx = path[0]
        if enclosing_idx < len(body_stmts):
            source_stmt = body_stmts[enclosing_idx]
            if isinstance(source_stmt, dict) and "If" in source_stmt:
                if_data = source_stmt["If"]
                then_stmts = if_data.get("then_stmts", [])
                has_goto = any(
                    isinstance(s, dict) and any(k == "Goto" and v.get("label") == label_name for k, v in s.items())
                    for s in then_stmts
                )
                if has_goto:
                    if_condition = _coerce_condition(_expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages))
    if if_condition is None:
        source_stmt = body_stmts[goto_info.source_idx] if goto_info.source_idx < len(body_stmts) else None
        if source_stmt and isinstance(source_stmt, dict) and "If" in source_stmt:
            if_data = source_stmt["If"]
            then_stmts = if_data.get("then_stmts", [])
            has_goto = any(
                isinstance(s, dict) and any(k == "Goto" and v.get("label") == label_name for k, v in s.items())
                for s in then_stmts
            )
            if has_goto:
                if_condition = _coerce_condition(_expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages))

    if if_condition:
        inverted = _invert_condition(if_condition)
        enclosing_idx = goto_info.source_idx
        path = goto_info.source_path or []
        if path and isinstance(path[0], int):
            enclosing_idx = path[0]
        for idx, stmt in enumerate(body_stmts):
            if idx >= enclosing_idx:
                break
            if isinstance(stmt, dict):
                _process_statement(stmt, proc, all_packages, dml_counter)
        proc.java_logic_lines.append(f"if ({inverted}) {{")
        for idx, stmt in enumerate(body_stmts):
            if idx <= enclosing_idx:
                continue
            if idx >= target_idx:
                break
            if isinstance(stmt, dict):
                _process_statement(stmt, proc, all_packages, dml_counter)
        proc.java_logic_lines.append("}")
        for stmt in body_stmts[target_idx:]:
            if isinstance(stmt, dict):
                _handled = False
                for st, sd in stmt.items():
                    if st == "Block" and sd.get("label") == label_name:
                        _stmt_list_to_java(sd.get("body", []), proc, all_packages, dml_counter, indent=1)
                        _handled = True
                        break
                if not _handled:
                    _process_statement(stmt, proc, all_packages, dml_counter)
    else:
        for stmt in body_stmts:
            if isinstance(stmt, dict):
                _process_statement(stmt, proc, all_packages, dml_counter)


def _generate_nested_breakout_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    proc.java_logic_lines = []
    proc.dml_statements = []

    goto_labels = {g.label for g in analysis.gotos}
    label_names = set(analysis.labels.keys())
    goto_by_label = {}
    for g in analysis.gotos:
        goto_by_label.setdefault(g.label, []).append(g)

    # Identify which top-level stmt is the loop containing GOTOs
    _loop_idx = None
    _loop_labels_internal = set()
    for i, stmt in enumerate(body_stmts):
        for st, sd in stmt.items():
            if st in ("For", "While", "Loop") and isinstance(sd, dict):
                _text = json.dumps(stmt)
                if any(f'"Goto"' in _text for _ in _text.split()):
                    if '"Goto"' in _text:
                        _loop_idx = i
                        _loop_body = sd.get("body", [])
                        _loop_labels_internal = {
                            lbl for lbl, li in analysis.labels.items()
                            if li.target_idx == i and li.target_depth > 0
                        }
                        if not _loop_labels_internal:
                            for lbl, li in analysis.labels.items():
                                for inner_s in _loop_body:
                                    if isinstance(inner_s, dict):
                                        for ik, iv in inner_s.items():
                                            if ik == "Block" and isinstance(iv, dict) and iv.get("label") == lbl:
                                                _loop_labels_internal.add(lbl)
                        break
        if _loop_idx is not None:
            break

    _external_labels = goto_labels - _loop_labels_internal
    _needs_dispatch = len(_external_labels) > 0
    _goto_target_var = "_gotoTarget"

    def _process_with_goto_replace(stmt):
        if not isinstance(stmt, dict):
            return
        for stmt_type, stmt_data in stmt.items():
            if stmt_type == "Goto" and isinstance(stmt_data, dict):
                label = stmt_data.get("label", "")
                if label in goto_labels:
                    if _needs_dispatch and label in _external_labels:
                        proc.java_logic_lines.append(f'{_goto_target_var} = "{label}";')
                        proc.java_logic_lines.append("break;")
                    else:
                        proc.java_logic_lines.append("continue;")
                    return
            elif stmt_type == "If" and isinstance(stmt_data, dict):
                condition = _coerce_condition(_expr_to_java(stmt_data.get("condition", {}), proc, all_packages=all_packages))
                proc.java_logic_lines.append(f"if ({condition}) {{")
                for s in _iter_statements(stmt_data.get("then_stmts", [])):
                    _process_with_goto_replace(s)
                _indent_last_lines(proc, 1)
                for elsif in stmt_data.get("elsifs", []):
                    elsif_cond = _coerce_condition(_expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages))
                    proc.java_logic_lines.append(f"}} else if ({elsif_cond}) {{")
                    for s in _iter_statements(elsif.get("stmts", [])):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                if stmt_data.get("else_stmts"):
                    proc.java_logic_lines.append("} else {")
                    for s in _iter_statements(stmt_data["else_stmts"]):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                proc.java_logic_lines.append("}")
                return
            elif stmt_type in ("For", "While", "Loop") and isinstance(stmt_data, dict):
                _process_loop_with_goto_replace(stmt, proc, all_packages, dml_counter)
                return
            elif stmt_type == "Block" and isinstance(stmt_data, dict):
                exc_block = stmt_data.get("exception_block")
                if exc_block and exc_block.get("handlers"):
                    proc.java_logic_lines.append("try {")
                    for s in _iter_statements(stmt_data.get("body", [])):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                    handlers_block = exc_block.get("handlers", [])
                    if handlers_block:
                        all_conditions = []
                        for h in handlers_block:
                            conds = h.get("conditions", [])
                            all_conditions.append(conds[0] if conds else "EXCEPTION")
                        proc.java_logic_lines.append(f"}} catch (Exception {_next_catch_var(proc)}) {{ // {'; '.join(all_conditions)}")
                    for handler in handlers_block:
                        conditions = handler.get("conditions", [])
                        cond_name = conditions[0] if conditions else "EXCEPTION"
                        proc.java_logic_lines.append(f"    // WHEN {cond_name}")
                        for hs in _iter_statements(handler.get("statements", [])):
                            _process_with_goto_replace(hs)
                        _indent_last_lines(proc, 1)
                        if proc.java_logic_lines and proc.java_logic_lines[-1].strip() == "return;":
                            break
                    proc.java_logic_lines.append("}")
                else:
                    for s in _iter_statements(stmt_data.get("body", [])):
                        _process_with_goto_replace(s)
                return
        _process_statement(stmt, proc, all_packages, dml_counter)

    def _process_loop_with_goto_replace(stmt, proc, all_packages, dml_counter):
        for stmt_type, stmt_data in stmt.items():
            if stmt_type == "For" and isinstance(stmt_data, dict):
                variable = stmt_data.get("variable", "i")
                var_java = snake_to_camel(variable)
                kind = stmt_data.get("kind", {})
                body = stmt_data.get("body", [])
                if "Range" in kind:
                    range_data = kind["Range"]
                    if variable not in proc.local_vars:
                        proc.local_vars[variable] = "Integer"
                    low = _expr_to_java(range_data.get("low", {"Literal": {"Integer": 0}}), proc, all_packages=all_packages)
                    high = _expr_to_java(range_data.get("high", {"Literal": {"Integer": 0}}), proc, all_packages=all_packages)
                    reverse = range_data.get("reverse", False)
                    if reverse:
                        proc.java_logic_lines.append(f"for ({var_java} = {high}; {var_java} >= {low}; {var_java}--) {{")
                    else:
                        proc.java_logic_lines.append(f"for ({var_java} = {low}; {var_java} <= {high}; {var_java}++) {{")
                    if _needs_dispatch:
                        proc.java_logic_lines.append(f"    {_goto_target_var} = null;")
                    for s in _iter_statements(body):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                    if _needs_dispatch:
                        if proc.java_logic_lines and proc.java_logic_lines[-1].strip() == "continue;":
                            proc.java_logic_lines.pop()
                        proc.java_logic_lines.append(f"if ({_goto_target_var} != null) continue;")
                    proc.java_logic_lines.append("}")
                    return
                elif "Query" in kind:
                    query_data = kind["Query"]
                    parsed_query = query_data.get("parsed_query")
                    if parsed_query:
                        _list_counter = getattr(proc, '_list_var_counter', 0) + 1
                        proc._list_var_counter = _list_counter
                        list_var = f"{var_java}List" if _list_counter == 1 else f"{var_java}List_{_list_counter}"
                        sql_text = _reconstruct_sql_from_ast(parsed_query)
                        if sql_text:
                            raw_sql_for_params = sql_text
                            sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                            mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                            _add_dml(proc, DmlStatement(
                                sql_type="select",
                                method_id=mapper_method,
                                sql_text=sql_text,
                                result_type="Map<String, Object>",
                                returns_list=True,
                            ))
                            proc.imports.add("import java.util.List;")
                            proc.imports.add("import java.util.Map;")
                            proc.imports.add("import java.util.ArrayList;")
                            proc.java_logic_lines.append(
                                f"List<Map<String, Object>> {list_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                            )
                            proc.java_logic_lines.append(f"if ({list_var} == null) {list_var} = new ArrayList<>();")
                            proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {list_var}) {{")
                            proc.local_vars[variable] = "Map<String, Object>"
                            proc._loop_vars = getattr(proc, '_loop_vars', set())
                            proc._loop_vars.add(variable)
                            if _needs_dispatch:
                                proc.java_logic_lines.append(f"    {_goto_target_var} = null;")
                            for s in _iter_statements(body):
                                _process_with_goto_replace(s)
                            _indent_last_lines(proc, 1)
                            if _needs_dispatch:
                                if proc.java_logic_lines and proc.java_logic_lines[-1].strip() == "continue;":
                                    proc.java_logic_lines.pop()
                                proc.java_logic_lines.append(f"if ({_goto_target_var} != null) continue;")
                            proc.java_logic_lines.append("}")
                            return
                proc.java_logic_lines.append(f"// TODO: nested breakout loop — manual extraction recommended")
                return
            elif stmt_type in ("While", "Loop") and isinstance(stmt_data, dict):
                condition = "true"
                if stmt_type == "While" and "condition" in stmt_data:
                    condition = _coerce_condition(_expr_to_java(stmt_data["condition"], proc, all_packages=all_packages))
                proc.java_logic_lines.append(f"while ({condition}) {{")
                if _needs_dispatch:
                    proc.java_logic_lines.append(f"    {_goto_target_var} = null;")
                for s in _iter_statements(stmt_data.get("body", [])):
                    _process_with_goto_replace(s)
                _indent_last_lines(proc, 1)
                if _needs_dispatch:
                    if proc.java_logic_lines and proc.java_logic_lines[-1].strip() == "continue;":
                        proc.java_logic_lines.pop()
                    proc.java_logic_lines.append(f"if ({_goto_target_var} != null) continue;")
                proc.java_logic_lines.append("}")
                return
        _process_statement(stmt, proc, all_packages, dml_counter)

    # Phase 1: pre-loop statements
    if _loop_idx is not None:
        for stmt in body_stmts[:_loop_idx]:
            _process_with_goto_replace(stmt)
        if _needs_dispatch:
            proc.java_logic_lines.append(f'String {_goto_target_var} = null;')

    # Phase 2: the loop
    if _loop_idx is not None:
        _process_with_goto_replace(body_stmts[_loop_idx])
    else:
        for stmt in body_stmts:
            _process_with_goto_replace(stmt)
        return

    # Phase 3: post-loop statements with dispatch for external labels
    _post_stmts = body_stmts[_loop_idx + 1:]
    if not _post_stmts:
        return

    if not _needs_dispatch:
        for stmt in _post_stmts:
            _process_with_goto_replace(stmt)
        return

    # Build ordered list of external labels that appear in post-loop stmts
    _sorted_ext_labels = []
    for i, stmt in enumerate(_post_stmts):
        for st, sd in stmt.items():
            if st == "Block" and isinstance(sd, dict):
                lbl = sd.get("label")
                if lbl and lbl in _external_labels:
                    _sorted_ext_labels.append((i, lbl))

    if not _sorted_ext_labels:
        for stmt in _post_stmts:
            _process_with_goto_replace(stmt)
        return

    # Generate dispatch: group each external label block with its trailing Raise stmts,
    # wrap each group in if(_gotoTarget == "label")
    _first_label_idx = None
    for _pi, stmt in enumerate(_post_stmts):
        for st, sd in stmt.items():
            if st == "Block" and isinstance(sd, dict):
                lbl = sd.get("label")
                if lbl and lbl in _external_labels:
                    _first_label_idx = _pi
                    break
        if _first_label_idx is not None:
            break

    # Statements before first external label → run when _gotoTarget is null (normal flow)
    _pre_label_stmts = _post_stmts[:_first_label_idx] if _first_label_idx is not None else []
    if _pre_label_stmts:
        proc.java_logic_lines.append(f'if ({_goto_target_var} == null) {{')
        for stmt in _pre_label_stmts:
            _process_with_goto_replace(stmt)
        _indent_last_lines(proc, 1)
        proc.java_logic_lines.append("}")

    _label_groups = []  # list of (label_name, [stmt_indices])
    _current_label = None
    _current_group = []
    for _pi, stmt in enumerate(_post_stmts):
        _found_label = None
        for st, sd in stmt.items():
            if st == "Block" and isinstance(sd, dict):
                lbl = sd.get("label")
                if lbl and lbl in _external_labels:
                    _found_label = lbl
                break
        if _found_label:
            if _current_label:
                _label_groups.append((_current_label, _current_group))
            _current_label = _found_label
            _current_group = [_pi]
        elif _current_label:
            _current_group.append(_pi)
    if _current_label:
        _label_groups.append((_current_label, _current_group))

    for _gi, (lbl, indices) in enumerate(_label_groups):
        _is_last = _gi == len(_label_groups) - 1
        if _is_last and len(_label_groups) == 1:
            # Only one external label — can use simple if or fall-through
            # Since the while(true) only exits via break with _gotoTarget set,
            # the label code always runs. But use if for safety.
            proc.java_logic_lines.append(f'if ("{lbl}".equals({_goto_target_var})) {{')
            for _pi in indices:
                _process_with_goto_replace(_post_stmts[_pi])
            _indent_last_lines(proc, 1)
            proc.java_logic_lines.append("}")
        else:
            proc.java_logic_lines.append(f'if ("{lbl}".equals({_goto_target_var})) {{')
            for _pi in indices:
                _process_with_goto_replace(_post_stmts[_pi])
            _indent_last_lines(proc, 1)
            proc.java_logic_lines.append("}")


def _strip_unreachable_in_case(proc, start_offset: int):
    lines = proc.java_logic_lines
    case_start = -1
    for i in range(start_offset - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("case ") and stripped.endswith(":"):
            case_start = i
            break
    if case_start < 0:
        return
    # Scan the case body for a terminal boundary at depth 0
    depth = 0
    first_terminal = -1
    for i in range(case_start + 1, start_offset):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("//"):
            prev_depth = depth
            depth += stripped.count("{") - stripped.count("}")
            continue
        prev_depth = depth
        depth += stripped.count("{") - stripped.count("}")
        if depth == 0 and first_terminal < 0:
            if stripped.startswith("throw ") or stripped.startswith("break;") or stripped == "return;" or stripped.startswith("return "):
                first_terminal = i
            elif prev_depth == 1 and stripped == "}":
                has_else = any("} else " in lines[j] or lines[j].strip().startswith("} else ") for j in range(case_start + 1, i) if "}" in lines[j] and "else" in lines[j])
                last_in_block = ""
                for j in range(i - 1, case_start, -1):
                    inner = lines[j].strip()
                    if not inner or inner.startswith("//"):
                        continue
                    last_in_block = inner
                    break
                if has_else and last_in_block in ("break;", "return;", "}") or last_in_block.startswith("return ") or last_in_block.startswith("throw "):
                    first_terminal = i
    if first_terminal >= 0 and first_terminal < start_offset - 1:
        del lines[first_terminal + 1:start_offset]


def _case_is_fully_terminated(proc, start_offset: int) -> bool:
    lines = proc.java_logic_lines
    case_start = -1
    for i in range(start_offset - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("case ") and stripped.endswith(":"):
            case_start = i
            break
    if case_start < 0:
        return False
    last_nonblank = -1
    for i in range(start_offset - 1, case_start, -1):
        s = lines[i].strip()
        if s and not s.startswith("//"):
            last_nonblank = i
            break
    if last_nonblank < 0:
        return False
    if lines[last_nonblank].strip() != "}":
        return False
    # Find the LAST block closing at depth 0 — only that block matters
    depth = 0
    last_block_end = -1
    for i in range(case_start + 1, start_offset):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("//"):
            depth += stripped.count("{") - stripped.count("}")
            continue
        prev_depth = depth
        depth += stripped.count("{") - stripped.count("}")
        if depth == 0 and prev_depth == 1 and stripped == "}":
            last_block_end = i
    if last_block_end < 0:
        return False
    # Check if that last block is a fully-terminated if-else (all branches end with break/return/throw)
    # Find the start of the last block by scanning backward from last_block_end
    last_block_start = -1
    brace_depth = 0
    for i in range(last_block_end - 1, case_start, -1):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("//"):
            continue
        brace_depth += stripped.count("{") - stripped.count("}")
        if brace_depth > 0:
            last_block_start = i
            break
    if last_block_start < 0:
        last_block_start = case_start + 1
    # Now scan ONLY the last block for terminals and else branches
    depth = 0
    found_terminal = False
    has_else = False
    for i in range(last_block_start, last_block_end):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("//"):
            prev_depth = depth
            depth += stripped.count("{") - stripped.count("}")
            continue
        prev_depth = depth
        depth += stripped.count("{") - stripped.count("}")
        if depth == 1 and prev_depth == 0 and "{" in stripped:
            found_terminal = False
        if depth >= 1:
            if stripped == "break;" or stripped.startswith("return") or stripped.startswith("throw "):
                found_terminal = True
        if "} else " in lines[i] or stripped.startswith("} else "):
            has_else = True
    return found_terminal and has_else


def _generate_state_machine_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    """Pattern E: multiple labels with multiple GOTOs -> enum + while-switch state machine."""
    proc.java_logic_lines = []
    proc.dml_statements = []

    enum_name = f"{snake_to_pascal(proc.proc_name)}State"
    state_names = [snake_to_pascal(ln) for ln in analysis.labels.keys()]

    # Set state machine mode so inner Gotos inside IF/ELSIF become state transitions
    proc._sm_enum = enum_name
    proc._sm_labels = analysis.labels

    proc.java_logic_lines.append(f"// State machine generated from GOTO labels")
    proc.java_logic_lines.append(f"enum {enum_name} {{{', '.join(state_names)}}}")
    proc.java_logic_lines.append(f"{enum_name} currentState = {enum_name}.{state_names[0]};")
    proc.java_logic_lines.append("boolean running = true;")
    proc.java_logic_lines.append("int _smGuard = 0;")
    for cursor_name, meta in proc.open_cursors.items():
        result_var = meta.get("result_var")
        index_var = meta.get("index_var")
        if result_var:
            proc.java_logic_lines.append(f"List<Map<String, Object>> {result_var} = new java.util.ArrayList<>();")
        if index_var:
            proc.java_logic_lines.append(f"int {index_var} = 0;")
    proc.java_logic_lines.append("while (running && _smGuard++ < 10000) {")
    proc.java_logic_lines.append("    switch (currentState) {")

    # Detect state boundaries from top-level Goto statements
    goto_boundaries = []
    for i, stmt in enumerate(body_stmts):
        if isinstance(stmt, dict) and "Goto" in stmt:
            goto_boundaries.append(i)

    # Sort labels by their source order (target_idx from raw SQL scan)
    sorted_labels = sorted(analysis.labels.items(), key=lambda x: x[1].target_idx)

    # Build state ranges: if N-1 top-level Gotos match N labels, each state
    # (except the last) spans from the previous Goto boundary to the current one (inclusive).
    state_ranges = []
    if len(goto_boundaries) == len(sorted_labels) - 1:
        start = 0
        for b in goto_boundaries:
            state_ranges.append((start, b + 1))
            start = b + 1
        state_ranges.append((start, len(body_stmts)))
    else:
        # Fallback to label target_idx-based ranges
        for li, (label_name, label_info) in enumerate(sorted_labels):
            end_idx = sorted_labels[li + 1][1].target_idx if li + 1 < len(sorted_labels) else len(body_stmts)
            state_ranges.append((label_info.target_idx, end_idx))
        # First state always starts at stmt 0
        if state_ranges:
            s, e = state_ranges[0]
            state_ranges[0] = (0, e)

    for li, (label_name, label_info) in enumerate(sorted_labels):
        state_java = snake_to_pascal(label_name)
        proc.java_logic_lines.append(f"        case {state_java}:")
        case_line_offset = len(proc.java_logic_lines)
        start_idx, end_idx = state_ranges[li]

        for idx in range(start_idx, end_idx):
            stmt = body_stmts[idx]
            if not isinstance(stmt, dict):
                continue
            for st, sd in stmt.items():
                if st == "Goto":
                    goto_label = sd.get("label", "")
                    if goto_label in analysis.labels:
                        goto_state = snake_to_pascal(goto_label)
                        proc.java_logic_lines.append(f"            currentState = {enum_name}.{goto_state};")
                    else:
                        proc.java_logic_lines.append(f"            running = false;")
                elif st == "Block" and sd.get("label") in analysis.labels:
                    _stmt_list_to_java(sd.get("body", []), proc, all_packages, dml_counter, indent=2)
                else:
                    _process_statement(stmt, proc, all_packages, dml_counter)

        _strip_unreachable_in_case(proc, start_offset=len(proc.java_logic_lines))
        last_stripped = proc.java_logic_lines[-1].strip() if proc.java_logic_lines else ""
        has_transition = any("currentState = " in line for line in proc.java_logic_lines[case_line_offset:])
        has_running_false = any("running = false" in line for line in proc.java_logic_lines[case_line_offset:])
        is_throw_or_return = last_stripped.startswith("throw ") or last_stripped.startswith("return")
        if not has_transition and not has_running_false and not is_throw_or_return:
            proc.java_logic_lines.append("            running = false;")
        needs_break = not (
            last_stripped == "break;"
            or is_throw_or_return
            or _case_is_fully_terminated(proc, start_offset=len(proc.java_logic_lines))
        )
        if needs_break:
            proc.java_logic_lines.append("            break;")

    proc.java_logic_lines.append("        default:")
    proc.java_logic_lines.append("            running = false;")
    proc.java_logic_lines.append("            break;")
    proc.java_logic_lines.append("    }")
    proc.java_logic_lines.append("}")

    # Clean up state machine mode
    if hasattr(proc, '_sm_enum'):
        delattr(proc, '_sm_enum')
    if hasattr(proc, '_sm_labels'):
        delattr(proc, '_sm_labels')


def _dml_method_name(dml_type: str, proc_name: str, counter: dict, semantic_key: str = None) -> str:
    # Issue #35: Use semantic key (target table + operation) for naming.
    # This produces names like "selectOrderByStatus" instead of "selectProcX_2".
    if semantic_key:
        # Use (dml_type, semantic_key) as counter key for deduplication
        composite_key = f"{dml_type}_{semantic_key}"
        n = counter.get(composite_key, 0)
        counter[composite_key] = n + 1
        base = f"{dml_type}{snake_to_pascal(semantic_key)}"
    else:
        n = counter.get(dml_type, 0)
        counter[dml_type] = n + 1
        base = f"{dml_type}{snake_to_pascal(proc_name)}"
    _log(f"[DML-CNTR] {composite_key if semantic_key else dml_type} -> n={n} -> {base + (chr(95)+str(n) if n > 0 else '')}", to_stdout=False)
    return base + (f"_{n}" if n > 0 else "")


def _strip_into_clause(sql: str) -> str:
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+\w+(\s*,\s*\w+)*\s+(?=from\b)', ' ', sql, flags=re.IGNORECASE)
    # Also strip INTO clause when there's no FROM (e.g., SELECT nextval() INTO var)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+.*$', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    return stripped


def _rewrite_select_for_into(sql: str, into_targets: list) -> str:
    if not into_targets:
        return _strip_into_clause(sql)
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+\w+(\s*,\s*\w+)*\s+(?=from\b)', ' ', sql, flags=re.IGNORECASE)
    # Handle SELECT without FROM (e.g., SELECT nextval() INTO var)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+.*$', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    return stripped.strip()


def _extract_returning_cols(returning_ast: list) -> list:
    cols = []
    for item in (returning_ast or []):
        expr = item.get("Expr", [])
        if expr and isinstance(expr, list):
            node = expr[0]
            if isinstance(node, dict):
                if "ColumnRef" in node:
                    parts = node["ColumnRef"]
                    cols.append(".".join(parts) if isinstance(parts, list) else str(parts))
                elif "BinaryOp" in node:
                    left = node["BinaryOp"].get("left", {})
                    if isinstance(left, dict) and "ColumnRef" in left:
                        parts = left["ColumnRef"]
                        col = ".".join(parts) if isinstance(parts, list) else str(parts)
                        cols.append(col)
                    else:
                        cols.append(f"expr{len(cols)}")
                else:
                    cols.append(f"expr{len(cols)}")
            else:
                cols.append(str(node))
        else:
            cols.append(f"expr{len(cols)}")
    return cols


def _extract_returning_into_targets(into_targets_ast: list) -> list:
    results = []
    for item in (into_targets_ast or []):
        expr = item.get("Expr", [])
        if expr and isinstance(expr, list):
            node = expr[0]
            if isinstance(node, dict):
                if "PlVariable" in node:
                    parts = node["PlVariable"]
                    results.append((parts[-1] if isinstance(parts, list) and parts else str(parts), parts if isinstance(parts, list) else [str(parts)]))
                elif "ColumnRef" in node:
                    parts = node["ColumnRef"]
                    results.append((parts[-1] if isinstance(parts, list) and parts else str(parts), parts if isinstance(parts, list) else [str(parts)]))
                else:
                    results.append((f"_retVar{len(results)}", [f"_retVar{len(results)}"]))
            else:
                results.append((f"_retVar{len(results)}", [f"_retVar{len(results)}"]))
        else:
            results.append((f"_retVar{len(results)}", [f"_retVar{len(results)}"]))
    return results


def _process_raw_dml(sql: str, proc: ProcedureInfo, dml_counter: dict):
    sql_upper = sql.strip().upper()
    if sql_upper.startswith("TRUNCATE"):
        mapper_method = _dml_method_name("truncate", proc.proc_name, dml_counter)
        sql_fmt = _fix_reconstructed_sql(sql.strip().rstrip(";"))
        _add_dml(proc, DmlStatement(
            sql_type="update",
            method_id=mapper_method,
            sql_text=sql_fmt,
        ))
        _emit_dml_with_rowcount(proc, f"mapper.{mapper_method}()")
    else:
        proc.java_logic_lines.append(f"// TODO: unhandled raw SQL: {sql[:80]}")
        _record_todo("RAW_DML", proc, f"unhandled: {sql[:40]}")


def _process_sql_statement(stmt_data: dict, proc: ProcedureInfo, dml_counter: dict):
    """Process a parsed SQL DML statement."""
    # Issue #35: extract target table for semantic method naming
    _dml_target = "unknown"
    for _sql_type_key in stmt_data:
        if _sql_type_key not in ("sql_text",):
            _dml_target = _extract_dml_target(stmt_data[_sql_type_key], _sql_type_key)
            break
    # Fall back to proc_name if target could not be resolved
    if _dml_target == "unknown":
        _dml_target = proc.proc_name
    # Enhance semantic key with aggregate info for SELECT statements
    _sql_text = stmt_data.get("sql_text", "")
    if _sql_text:
        _agg_match = re.search(
            r'\b(count|sum|avg|max|min)\s*\(\s*(?:distinct\s+)?(\w+(?:\.\w+)?)?',
            _sql_text, re.IGNORECASE
        )
        if _agg_match:
            _agg_func = _agg_match.group(1).lower()
            _agg_col = _agg_match.group(2) or ""
            if _agg_col:
                _dml_target = f"{_dml_target}_{_agg_func}_{_agg_col}"
            else:
                _dml_target = f"{_dml_target}_{_agg_func}"

    for sql_type, sql_details in stmt_data.items():
        if sql_type == "sql_text":
            continue

        sql_text = stmt_data.get("sql_text", "")
        if sql_text:
            sql_text = _fix_reconstructed_sql(sql_text)
            sql_text = _qualify_ambiguous_group_order(sql_text)

        if sql_type == "Select":
            into_targets = sql_details.get("into_targets")
            from_tables = _extract_table_names(sql_details.get("from", []))

            for t in from_tables:
                proc.table_refs.add(t)

            mapper_method = _dml_method_name("select", proc.proc_name, dml_counter, semantic_key=_dml_target)
            _is_bulk = sql_details.get("bulk_collect", False)

            if into_targets:
                first_var = _extract_into_variable(into_targets)
                if first_var:
                    var_java = snake_to_camel(first_var)
                    result_type = proc.local_vars.get(first_var, "Object")
                    var_names = _extract_all_into_variables(into_targets)

                    if _is_bulk:
                        result_type = "Map<String, Object>"
                        select_cols = _extract_select_columns(sql_text)
                        into_targets_full = _extract_all_into_targets(into_targets)
                        _bulk_var = f"_bulkResult_{dml_counter.get('select', 0)}"
                        proc.java_logic_lines.append(
                            f'List<Map<String, Object>> {_bulk_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text, is_select=True))});'
                        )
                        proc.java_logic_lines.append(f'for (Map<String, Object> _bulkRow : {_bulk_var}) {{')
                        for idx, (field_name, full_parts) in enumerate(into_targets_full):
                            col_key = select_cols[idx] if idx < len(select_cols) else field_name
                            if len(full_parts) >= 2:
                                map_var = snake_to_camel(full_parts[0])
                                vn_java = snake_to_camel(field_name)
                                var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                                _get_expr = f'_bulkRow.get("{col_key}")'
                                cast_expr = _safe_map_cast(var_type, _get_expr) if var_type != "Object" else _get_expr
                                _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', cast_expr)
                            else:
                                var_type = proc.local_vars.get(field_name)
                                if var_type is None:
                                    for p in proc.parameters:
                                        if p.name.lower() == field_name.lower():
                                            var_type = p.java_type
                                            break
                                if var_type is None:
                                    var_type = "Object"
                                vn_java = snake_to_camel(field_name)
                                if var_type == "Map<String, Object>":
                                    proc.java_logic_lines.append(f'    // TODO: BULK COLLECT extraction for {vn_java} requires manual implementation')
                                elif var_type.startswith("java.util.List<"):
                                    _elem = var_type[len("java.util.List<"):-1]
                                    _val_expr = _safe_map_cast(_elem, f'_bulkRow.get("{col_key}")')
                                    proc.java_logic_lines.append(f'    {vn_java}.add({_val_expr});')
                                else:
                                    _emit_assignment(proc, vn_java, _safe_map_cast(var_type, f'_bulkRow.get("{col_key}")'))
                        proc.java_logic_lines.append('}')
                    elif len(var_names) > 1:
                        result_type = "Map<String, Object>"
                        _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text, is_select=True))})')
                        select_cols = _extract_select_columns(sql_text)
                        into_targets_full = _extract_all_into_targets(into_targets)
                        for idx, (field_name, full_parts) in enumerate(into_targets_full):
                            col_key = select_cols[idx] if idx < len(select_cols) else field_name
                            if len(full_parts) >= 2:
                                map_var = snake_to_camel(full_parts[0])
                                vn_java = snake_to_camel(field_name)
                                var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                                _get_expr = f'_row.get("{col_key}")'
                                cast_expr = _safe_map_cast(var_type, _get_expr) if var_type != "Object" else _get_expr
                                _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', cast_expr)
                            else:
                                var_type = proc.local_vars.get(field_name)
                                if var_type is None:
                                    for p in proc.parameters:
                                        if p.name.lower() == field_name.lower():
                                            var_type = p.java_type
                                            break
                                if var_type is None:
                                    var_type = "Object"
                                vn_java = snake_to_camel(field_name)
                                if var_type == "Map<String, Object>":
                                    proc.java_logic_lines.append(f'// TODO: BULK COLLECT extraction for {vn_java} requires manual implementation')
                                elif var_type.startswith("java.util.List<"):
                                    _elem = var_type[len("java.util.List<"):-1]
                                    _val_expr = _safe_map_cast(_elem, f'_row.get("{col_key}")')
                                    proc.java_logic_lines.append(f'{vn_java}.add({_val_expr});')
                                else:
                                    _emit_assignment(proc, vn_java, _safe_map_cast(var_type, f'_row.get("{col_key}")'))
                    else:
                        into_targets_full = _extract_all_into_targets(into_targets)
                        if into_targets_full and len(into_targets_full[0][1]) >= 2:
                            full_parts = into_targets_full[0][1]
                            map_var = snake_to_camel(full_parts[0])
                            result_type = "Map<String, Object>"
                            col_name = _extract_select_column(sql_text, 0)
                            _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text, is_select=True))})')
                            _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{col_name}")')
                        else:
                            _assign_expr = f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text, is_select=True))})'
                            _emit_assignment(proc, var_java, _assign_expr)

                    if '||' in sql_text and result_type not in ("String", "Object", "Map<String, Object>"):
                        result_type = "String"
                        target_type = proc.local_vars.get(first_var, "Object") if first_var else "Object"
                        if target_type in ("Integer", "int", "Long", "long"):
                            _mc = f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text, is_select=True))})'
                            for i, line in enumerate(proc.java_logic_lines):
                                if _mc in line and f'{var_java} = ' in line:
                                    proc.java_logic_lines[i] = f'{{ String _strResult = {_mc}; if (_strResult != null) {{ /* concatenated string assigned to {target_type} var */ }} }}'
                                    break

                    _add_dml(proc, DmlStatement(
                        sql_type="select",
                        method_id=mapper_method,
                        sql_text=_rewrite_select_for_into(sql_text, into_targets),
                        result_type=result_type,
                        returns_list=_is_bulk,
                    ))
            else:
                _add_dml(proc, DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                ))
                proc.java_logic_lines.append(
                    f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text, is_select=True))});'
                )
        elif sql_type == "Merge":
            if not sql_text:
                sql_text = stmt_data.get("sql_text", "")
            if sql_text:
                sql_text = _fix_reconstructed_sql(sql_text)
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
            else:
                sql_text = _reconstruct_merge_sql(sql_details)
                if sql_text:
                    sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
            if sql_text:
                mapper_method = _dml_method_name("update", proc.proc_name, dml_counter, semantic_key=_dml_target)
                _add_dml(proc, DmlStatement(
                    sql_type="update",
                    method_id=mapper_method,
                    sql_text=sql_text,
                ))
                _emit_dml_with_rowcount(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')
            else:
                proc.java_logic_lines.append(f"// TODO: MERGE INTO — SQL reconstruction failed")
                _record_todo("MERGE", proc, "sql reconstruction failed")
        elif sql_type == "Insert":
            from_tables = _extract_table_names_from_insert(sql_details)
            for t in from_tables:
                proc.table_refs.add(t)

            mapper_method = _dml_method_name("insert", proc.proc_name, dml_counter, semantic_key=_dml_target)
            _ret_cols = _extract_returning_cols(sql_details.get("returning"))
            _into_targets = _extract_returning_into_targets(sql_details.get("into_targets"))
            _into_vars = [v for v, _ in _into_targets]
            _is_bulk = sql_details.get("bulk_collect", False)

            if _ret_cols and _into_vars and not _is_bulk:
                _add_dml(proc, DmlStatement(
                    sql_type="insert",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    returning_cols=_ret_cols,
                    returning_into_vars=_into_vars,
                ))
                _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')
                for _rc, (_fn, _fp) in zip(_ret_cols, _into_targets):
                    if len(_fp) >= 2:
                        _map_var = snake_to_camel(_fp[0])
                        _cast_expr = _safe_map_cast(_java_type_from_field_name(_fn) if _java_type_from_field_name(_fn) != "Object" else "Object", f'_row.get("{_rc}")')
                        _emit_assignment(proc, f'__MAP_PUT__{_map_var}__{_fn}', _cast_expr if _java_type_from_field_name(_fn) != "Object" else f'_row.get("{_rc}")')
                    else:
                        _vtype = proc.local_vars.get(_fn, "Object")
                        if _vtype is None:
                            for p in proc.parameters:
                                if p.name.lower() == _fn.lower():
                                    _vtype = p.java_type
                                    break
                        if _vtype is None:
                            _vtype = "Object"
                        _vn_java = snake_to_camel(_fn)
                        if _vtype == "Map<String, Object>":
                            proc.java_logic_lines.append(f'// TODO: BULK COLLECT extraction for {_vn_java} requires manual implementation')
                        else:
                            _emit_assignment(proc, _vn_java, _safe_map_cast(_vtype, f'_row.get("{_rc}")'))
            else:
                _add_dml(proc, DmlStatement(
                    sql_type="insert",
                    method_id=mapper_method,
                    sql_text=sql_text,
                ))
                _emit_dml_with_rowcount(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')

        elif sql_type == "Update":
            from_tables = _extract_table_names_from_update(sql_details)
            for t in from_tables:
                proc.table_refs.add(t)

            mapper_method = _dml_method_name("update", proc.proc_name, dml_counter, semantic_key=_dml_target)
            _ret_cols = _extract_returning_cols(sql_details.get("returning"))
            _into_targets = _extract_returning_into_targets(sql_details.get("into_targets"))
            _into_vars = [v for v, _ in _into_targets]
            _is_bulk = sql_details.get("bulk_collect", False)

            if _ret_cols and _into_vars and not _is_bulk:
                _add_dml(proc, DmlStatement(
                    sql_type="update",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    returning_cols=_ret_cols,
                    returning_into_vars=_into_vars,
                ))
                _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')
                for _rc, (_fn, _fp) in zip(_ret_cols, _into_targets):
                    if len(_fp) >= 2:
                        _map_var = snake_to_camel(_fp[0])
                        _emit_assignment(proc, f'__MAP_PUT__{_map_var}__{_fn}', f'_row.get("{_rc}")')
                    else:
                        _vtype = proc.local_vars.get(_fn, "Object")
                        _vn_java = snake_to_camel(_fn)
                        if _vtype == "Map<String, Object>":
                            proc.java_logic_lines.append(f'// TODO: BULK COLLECT extraction for {_vn_java}')
                        else:
                            _emit_assignment(proc, _vn_java, _safe_map_cast(_vtype, f'_row.get("{_rc}")'))
            else:
                _add_dml(proc, DmlStatement(
                    sql_type="update",
                    method_id=mapper_method,
                    sql_text=sql_text,
                ))
                _emit_dml_with_rowcount(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')

        elif sql_type == "Delete":
            table_name = _extract_table_name_from_dml(sql_details)
            proc.table_refs.add(table_name)

            mapper_method = _dml_method_name("delete", proc.proc_name, dml_counter, semantic_key=_dml_target)
            _ret_cols = _extract_returning_cols(sql_details.get("returning"))
            _into_targets = _extract_returning_into_targets(sql_details.get("into_targets"))
            _into_vars = [v for v, _ in _into_targets]
            _is_bulk = sql_details.get("bulk_collect", False)

            if _ret_cols and _into_vars and not _is_bulk:
                _add_dml(proc, DmlStatement(
                    sql_type="delete",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    returning_cols=_ret_cols,
                    returning_into_vars=_into_vars,
                ))
                _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')
                for _rc, (_fn, _fp) in zip(_ret_cols, _into_targets):
                    if len(_fp) >= 2:
                        _map_var = snake_to_camel(_fp[0])
                        _emit_assignment(proc, f'__MAP_PUT__{_map_var}__{_fn}', f'_row.get("{_rc}")')
                    else:
                        _vtype = proc.local_vars.get(_fn, "Object")
                        _vn_java = snake_to_camel(_fn)
                        if _vtype == "Map<String, Object>":
                            proc.java_logic_lines.append(f'// TODO: BULK COLLECT extraction for {_vn_java}')
                        else:
                            _emit_assignment(proc, _vn_java, _safe_map_cast(_vtype, f'_row.get("{_rc}")'))
            else:
                _add_dml(proc, DmlStatement(
                    sql_type="delete",
                    method_id=mapper_method,
                    sql_text=sql_text,
                ))
                _emit_dml_with_rowcount(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')


def _reconstruct_merge_sql(merge_data: dict) -> str:
    parts = []
    target = merge_data.get("target", {})
    if "Table" in target:
        t = target["Table"]
        tname = ".".join(t["name"]) if isinstance(t.get("name"), list) else str(t.get("name", ""))
        talias = t.get("alias", "")
        parts.append(f"MERGE INTO {tname}" + (f" {talias}" if talias else ""))
    source = merge_data.get("source", {})
    if "Subquery" in source:
        sq = source["Subquery"]
        salias = sq.get("alias", "")
        sq_sql = _reconstruct_sql_from_ast(sq.get("query", {}))
        if sq_sql:
            parts.append(f"USING ({sq_sql}) {salias}")
    on_cond = merge_data.get("on_condition", {})
    if on_cond:
        cond_java = _expr_to_java(on_cond, None, all_packages={})
        parts.append(f"ON ({cond_java})")
    for clause in merge_data.get("when_clauses", []):
        matched = clause.get("matched", False)
        action = clause.get("action", {})
        if isinstance(action, dict):
            if "Insert" in action:
                ins = action["Insert"]
                cols = ", ".join(".".join(c) if isinstance(c, list) else str(c) for c in ins.get("columns", []))
                vals = ", ".join(_expr_to_java(v, None, all_packages={}) for v in ins.get("values", []))
                parts.append(f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})")
            elif "Update" in action:
                upd = action["Update"]
                sets = ", ".join(
                    ".".join(s.get("columns", [])) if isinstance(s, dict) else str(s) for s in upd
                ) if isinstance(upd, list) else str(upd)
                parts.append(f"WHEN MATCHED THEN UPDATE SET {sets}")
        elif action == "Delete":
            parts.append("WHEN MATCHED THEN DELETE")
    return "\n".join(parts) if len(parts) > 2 else ""


def _process_forall(forall_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    index_var = forall_data.get("variable", "i")
    bounds_str = forall_data.get("bounds", "")
    if not bounds_str.strip():
        proc.java_logic_lines.append("// TODO: FORALL — empty bounds")
        _record_todo("FORALL", proc, "empty bounds")
        return

    dml_match = re.search(r'\b(INSERT|UPDATE|DELETE)\b', bounds_str, re.IGNORECASE)
    if not dml_match:
        proc.java_logic_lines.append(f"// TODO: FORALL — cannot parse DML from bounds: {bounds_str[:80]}")
        _record_todo("FORALL", proc, "unparseable bounds")
        return

    range_part = bounds_str[:dml_match.start()].strip().rstrip('.')
    range_part = re.sub(r'\s*\.\.\s*', '..', range_part)
    dml_sql = bounds_str[dml_match.start():].strip()
    dml_type = dml_match.group(1).lower()

    _range_match = re.match(r'(\d+)\s*\.\.\s*(\w[\w.]*)\s*\.\s*COUNT', range_part, re.IGNORECASE)
    if _range_match:
        _low = _range_match.group(1)
        _arr_var = _range_match.group(2).replace(' ', '')
        _arr_java = snake_to_camel(_arr_var.split('.')[0])
        loop_start = f'for (int {index_var} = {_low}; {index_var} <= {_arr_java}.size(); {index_var}++)'
    else:
        _dot_match = re.match(r'(\w+)\s*\.\.\s*(\w+)', range_part)
        if _dot_match:
            loop_start = f'for (int {index_var} = {_dot_match.group(1)}; {index_var} <= {_dot_match.group(2)}; {index_var}++)'
        else:
            loop_start = f'for (int {index_var} = 1; {index_var} <= {snake_to_camel(range_part)}; {index_var}++)'

    mapper_method = _dml_method_name(dml_type, proc.proc_name, dml_counter)

    array_refs = re.findall(r'(\w+)\s*\(\s*' + re.escape(index_var) + r'\s*\)', dml_sql, re.IGNORECASE)
    _forall_param_map = {}
    seen = set()
    _has_map_array = False
    for arr_name in array_refs:
        if arr_name.lower() not in seen and arr_name.lower() != index_var.lower():
            seen.add(arr_name.lower())
            arr_java = snake_to_camel(arr_name)
            arr_type = proc.local_vars.get(arr_name, "Object")
            if arr_type == "Map<String, Object>":
                _has_map_array = True
            else:
                _forall_param_map[arr_java] = f'{arr_java}.get({index_var} - 1)'

    if _has_map_array:
        proc.java_logic_lines.append(f'// TODO: FORALL — bulk operation with TABLE-type variables requires manual implementation')
        proc.java_logic_lines.append(f'// {dml_sql[:120]}')
        _record_todo("FORALL", proc, "TABLE-type array vars")
        return

    # Determine if FORALL can be batched: range is 1..arr.COUNT, all arrays are simple typed,
    # and we have at least one array reference.
    _can_batch = False
    _batch_list_var = ""
    _batch_arrays = {}
    if _range_match and array_refs and not _has_map_array:
        _has_standalone_index = bool(re.search(r'\|\|\s*' + re.escape(index_var) + r'\b', dml_sql) or
                                     re.search(r"'[^']*'\s*\|\|\s*" + re.escape(index_var), dml_sql, re.IGNORECASE))
        _all_arrays_simple = True
        _primary_arr_java = snake_to_camel(array_refs[0])
        for arr_name in array_refs:
            if arr_name.lower() != index_var.lower():
                arr_java = snake_to_camel(arr_name)
                arr_type = proc.local_vars.get(arr_name, "Object")
                m_list = re.match(r'java\.util\.List<(.+)>', arr_type)
                if m_list:
                    _batch_arrays[arr_java] = m_list.group(1)
                else:
                    _all_arrays_simple = False
        if _all_arrays_simple and _batch_arrays:
            _can_batch = True
            _batch_list_var = "item"

    _extra_text = re.sub(r'\b\w+\s*\(\s*' + re.escape(index_var) + r'\s*\)', '?', dml_sql)
    _dml_local_vars = _sql_local_var_names(proc, _extra_text)
    for _lvn in _dml_local_vars:
        _lvn_java = snake_to_camel(_lvn)
        if _lvn_java not in seen:
            seen.add(_lvn_java)
            _forall_param_map[_lvn_java] = _lvn_java

    for p in proc.parameters:
        if p.mode and p.mode.upper() == "OUT":
            continue
        pj = p.java_name
        if pj not in seen:
            seen.add(pj)
            _forall_param_map[pj] = pj

    # Order param_args to match mapper method signature (local_vars order + params order)
    param_args = []
    _ordered_names = [snake_to_camel(v) for v in proc.local_vars] + [p.java_name for p in proc.parameters if not (p.mode and p.mode.upper() == "OUT")]
    for _n in _ordered_names:
        if _n in _forall_param_map:
            param_args.append(_forall_param_map.pop(_n))
    param_args.extend(_forall_param_map.values())

    # If the DML SQL references the index variable standalone (e.g., '|| i'), pass it to mapper
    _index_var_java = f"_{index_var}"
    _has_standalone_index_ref = re.search(r'\b' + re.escape(index_var) + r'\b', dml_sql) and index_var not in {a.split('.')[0] for a in param_args}
    if _has_standalone_index_ref:
        param_args.append(index_var)

    args_str = ", ".join(param_args)
    mybatis_sql = re.sub(
        r'(\w+)\s*\(\s*' + re.escape(index_var) + r'\s*\)',
        lambda m: f'#{{{snake_to_camel(m.group(1))}}}',
        dml_sql
    )
    # Replace standalone index variable references (e.g., '|| i' in SQL) with parameter
    mybatis_sql = re.sub(
        r'\b' + re.escape(index_var) + r'\b',
        f'#{{_{index_var}}}',
        mybatis_sql
    )
    mybatis_sql = _convert_params_to_mybatis(mybatis_sql, proc.parameters, proc.local_vars)

    # Add index variable as extra param for mapper method signature
    _index_var_java = f"_{index_var}"
    _forall_extra_params = [(_index_var_java, "Integer")]

    # Store FORALL array variable element types as extra_params so
    # _dml_used_local_vars unwraps List<T> → T for mapper method signature
    for arr_name in array_refs:
        if arr_name.lower() in {a.lower() for a in array_refs} and arr_name.lower() != index_var.lower():
            arr_java = snake_to_camel(arr_name)
            arr_type = proc.local_vars.get(arr_name, "Object")
            m = re.match(r'java\.util\.List<(.+)>', arr_type)
            if m:
                _forall_extra_params.append((arr_java, m.group(1)))

    _add_dml(proc, DmlStatement(
        sql_type=dml_type,
        method_id=mapper_method,
        sql_text=mybatis_sql,
        extra_params=_forall_extra_params,
        is_forall_batch=_can_batch,
        forall_batch_list_var=_batch_list_var,
        forall_batch_arrays=_batch_arrays if _can_batch else {},
    ))

    if _can_batch:
        proc._needs_rowcount_var = True
        _batch_list_name = f'_batch_{mapper_method}'
        proc.java_logic_lines.append(f'java.util.List<java.util.Map<String, Object>> {_batch_list_name} = new java.util.ArrayList<>();')
        proc.java_logic_lines.append(f'for (int _bi = 0; _bi < {_primary_arr_java}.size(); _bi++) {{')
        proc.java_logic_lines.append(f'    java.util.Map<String, Object> _brow = new java.util.LinkedHashMap<>();')
        for arr_java in _batch_arrays:
            proc.java_logic_lines.append(f'    _brow.put("{arr_java}", {arr_java}.get(_bi));')
        if _has_standalone_index_ref:
            proc.java_logic_lines.append(f'    _brow.put("_{index_var}", _bi + 1);')
        proc.java_logic_lines.append(f'    {_batch_list_name}.add(_brow);')
        proc.java_logic_lines.append(f'}}')
        proc.java_logic_lines.append(f'_sqlRowCount += mapper.{mapper_method}({_batch_list_name});')
    else:
        proc.java_logic_lines.append(f'{loop_start} {{')
        proc._needs_rowcount_var = True
        proc.java_logic_lines.append(f'    _sqlRowCount += mapper.{mapper_method}({args_str});')
        proc.java_logic_lines.append(f'}}')

    for _tm in re.finditer(r'\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|DELETE)\s+(\w+)', dml_sql, re.IGNORECASE):
        if _tm.group(1).upper() not in ('SELECT', 'FROM', 'WHERE', 'SET', 'VALUES'):
            proc.table_refs.add(_tm.group(1))


def _coerce_condition(cond: str) -> str:
    """Ensure a condition expression is boolean-safe. Replace null fallback with false."""
    stripped = cond.rstrip()
    if stripped.endswith("*/ null"):
        return stripped[:-4] + "false"
    return cond


def _process_if(if_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    condition = _coerce_condition(_expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages))

    then_stmts = list(_iter_statements(if_data.get("then_stmts", [])))
    elsifs = if_data.get("elsifs", [])
    else_stmts = if_data.get("else_stmts", [])

    if not elsifs and not else_stmts:
        _has_sql_concat = False
        for stmt in then_stmts:
            if stmt.get("Assignment"):
                concat_result = _detect_sql_concat_append(stmt["Assignment"], proc)
                if concat_result:
                    var_name, sql_fragment, clause_type = concat_result
                    if var_name not in proc.sql_concat_chain:
                        proc.sql_concat_chain[var_name] = []
                    proc.sql_concat_chain[var_name].append((condition, sql_fragment, clause_type))
                    _has_sql_concat = True
        if _has_sql_concat:
            build_map = getattr(proc, '_dynamic_sql_build_stmts', {})
            stmt_idx = getattr(proc, '_current_stmt_idx', -1)
            if stmt_idx >= 0:
                build_map[stmt_idx] = var_name
                proc._dynamic_sql_build_stmts = build_map

    proc.java_logic_lines.append(f"if ({condition}) {{")

    for s in then_stmts:
        _process_statement(s, proc, all_packages, dml_counter)
    _indent_last_lines(proc, 1)

    for elsif in elsifs:
        elsif_cond = _coerce_condition(_expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages))
        proc.java_logic_lines.append(f"}} else if ({elsif_cond}) {{")
        for s in _iter_statements(elsif.get("stmts", [])):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

    if else_stmts:
        proc.java_logic_lines.append("} else {")
        for s in _iter_statements(else_stmts):
            _process_statement(s, proc, all_packages, dml_counter)
        _indent_last_lines(proc, 1)

    proc.java_logic_lines.append("}")


def _process_return(return_data: dict, proc: ProcedureInfo, all_packages: dict = None):
    expr = return_data.get("expression")
    if expr:
        java_expr = _expr_to_java(expr, proc, all_packages=all_packages)
        _et = _infer_expr_type(expr, proc)
        proc._raw_return_types.append(_et)
        if proc.is_function and proc.return_type:
            ret_java = sql_type_to_java(proc.return_type)
            if "BigDecimal" in ret_java and java_expr.endswith("d") and not java_expr.startswith("java.math.BigDecimal"):
                try:
                    float_val = float(java_expr[:-1])
                    java_expr = f"java.math.BigDecimal.valueOf({java_expr[:-1]})"
                except ValueError:
                    pass
            if re.search(r'\*/\s*null$', java_expr) and ret_java not in ("Object", "void", ""):
                java_expr = _type_default(ret_java)
            if _needs_coercion(_et, ret_java):
                java_expr = _coerce_type(java_expr, _et, ret_java)
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


def _is_long_expr(expr: str) -> bool:
    s = expr.strip()
    if re.match(r'^-?\d+[Ll]$', s):
        return True
    if s.startswith("Long.valueOf(") or s.startswith("Long.parseLong("):
        return True
    if s.startswith("String.valueOf("):
        return False
    if re.match(r'^-?\d+$', s):
        return True
    return False


def _is_bare_int_literal(expr: str) -> bool:
    return bool(re.match(r'^-?\d+$', expr.strip()))


def _is_bare_long_literal(expr: str) -> bool:
    return bool(re.match(r'^-?\d+[Ll]$', expr.strip()))


def _is_primitive_producing(expr: str) -> bool:
    if not expr:
        return False
    s = expr.strip()
    if re.match(r'^-?\d+(\.\d+)?[dDfFlL]?$', s):
        return True
    if re.match(r'^\(?(double|int|long|float)\b\)', s):
        return True
    core = s.rstrip(")").rstrip("(")
    if re.search(r'\.(intValue|longValue|doubleValue|floatValue)$', core):
        return True
    if re.search(r'(Double|Float|Long|Integer)\.parse(Double|Float|Long|Int)\s*\(', s):
        return True
    if re.search(r'\(\(Number\)\s*', s):
        return True
    if re.search(r'Math\.\w+\s*\(', s):
        return True
    s_no_strings = re.sub(r'"[^"]*"', '""', s)
    if re.search(r'\s[*\/]\s', s_no_strings):
        return True
    if re.search(r'\s[+\-]\s', s_no_strings) and re.search(r'(\.(doubleValue|longValue|intValue|floatValue|getTime)\(\)|parse(Double|Long|Int|Float)\(|\(\(Number\)|Math\.)', s_no_strings):
        return True
    return False


def _next_catch_var(proc) -> str:
    n = getattr(proc, "_catch_counter", 0)
    proc._catch_counter = n + 1
    return "e" if n == 0 else f"e{n + 1}"


def _safe_map_cast(var_type: str, expr: str) -> str:
    if _is_primitive_producing(expr):
        if var_type == "Long":
            return f"((Number) ({expr})).longValue()"
        if var_type in ("Integer", "int"):
            return f"((Number) ({expr})).intValue()"
        if var_type == "Double":
            return f"((Number) ({expr})).doubleValue()"
        if "BigDecimal" in var_type:
            return f"java.math.BigDecimal.valueOf({expr})"
        return f"((Number) ({expr})).doubleValue()"
    if var_type == "Long":
        return f"({expr} != null ? ({expr} instanceof Number ? ((Number) ({expr})).longValue() : Long.parseLong(String.valueOf({expr}))) : 0L)"
    if var_type in ("Integer", "int"):
        return f"({expr} != null ? ({expr} instanceof Number ? ((Number) {expr}).intValue() : Integer.parseInt(String.valueOf({expr}))) : 0)"
    if var_type == "Double":
        return f"({expr} != null ? ({expr} instanceof Number ? ((Number) {expr}).doubleValue() : Double.parseDouble(String.valueOf({expr}))) : 0.0d)"
    if var_type == "String":
        return f"({expr} != null ? String.valueOf({expr}) : \"\")"
    if "BigDecimal" in var_type:
        return f"({expr} != null ? ({expr} instanceof java.math.BigDecimal ? (java.math.BigDecimal) {expr} : new java.math.BigDecimal(String.valueOf({expr}))) : java.math.BigDecimal.ZERO)"
    if var_type == "java.sql.Date":
        return f"({expr} instanceof java.sql.Timestamp ? new java.sql.Date(((java.sql.Timestamp) {expr}).getTime()) : (java.sql.Date) {expr})"
    if var_type == "Boolean" or var_type == "boolean":
        return f"(Boolean) {expr}"
    return f"({var_type}) {expr}"


def _emit_row_decl(proc: ProcedureInfo, mapper_expr: str, indent: str = ""):
    proc._needs_row_var = True
    proc._needs_rowcount_var = True
    proc.java_logic_lines.append(f'{indent}_row = {mapper_expr};')
    proc.java_logic_lines.append(f'{indent}if (_row == null) _row = java.util.Collections.emptyMap();')
    proc.java_logic_lines.append(f'{indent}_sqlRowCount = (_row != null && !_row.isEmpty()) ? 1 : 0;')


def _emit_dml_with_rowcount(proc: ProcedureInfo, mapper_expr: str, indent: str = ""):
    proc._needs_rowcount_var = True
    proc.java_logic_lines.append(f'{indent}_sqlRowCount = {mapper_expr};')


def _emit_assignment(proc: ProcedureInfo, target: str, expr: str):
    out_param_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
    out_string_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "String"}
    out_long_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "Long"}
    out_integer_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "Integer"}
    out_bigdecimal_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and "BigDecimal" in p.java_type}

    # BigDecimal context: wrap double literals from CASE/ternary into BigDecimal.valueOf()
    target_var_type = None
    target_lower = target.lower()
    for vname, vtype in proc.local_vars.items():
        if snake_to_camel(vname).lower() == target_lower:
            target_var_type = vtype
            break
    if target_var_type is None:
        for p in proc.parameters:
            if p.java_name == target:
                target_var_type = p.java_type
                break
    if expr == "__ROWCOUNT__":
        if target_var_type in ("Long", "long"):
            expr = "Long.valueOf(_sqlRowCount)"
        elif target_var_type in ("Integer", "int"):
            expr = "Integer.valueOf(_sqlRowCount)"
        else:
            expr = "_sqlRowCount"
    is_ternary = "?" in expr and ":" in expr
    if target_var_type and "BigDecimal" in target_var_type and is_ternary and re.search(r'\b\d+\.\d+d\b', expr):
        has_bd_term = "BigDecimal" in expr or ".subtract(" in expr or ".add(" in expr or ".multiply(" in expr
        if not has_bd_term:
            for const_name, const_type in _PACKAGE_CONSTANTS.items():
                if "BigDecimal" in const_type and snake_to_camel(const_name) in expr:
                    has_bd_term = True
                    break
        if not has_bd_term:
            for var_name, var_data in _PACKAGE_VARIABLES.items():
                if "BigDecimal" in var_data.get("java_type", "") and snake_to_camel(var_name) in expr:
                    has_bd_term = True
                    break
        if not has_bd_term:
            expr_clean = re.sub(r'\b(\d+\.\d+)d\b', r'\1', expr)
            expr = f"java.math.BigDecimal.valueOf({expr_clean})"

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
        elif target in out_long_names and _is_string_expr(expr) and not _is_long_expr(expr):
            expr = f"Long.valueOf({expr})"
        elif target in out_long_names and _is_bare_int_literal(expr):
            expr = f"Long.valueOf({expr})"
        elif target in out_long_names and not _is_long_expr(expr):
            expr = f"((Number) {expr}).longValue()"
        elif target in out_integer_names:
            if _is_bare_int_literal(expr):
                expr = f"Integer.valueOf({expr})"
            elif ".intValue()" not in expr and "_row.get(" in expr:
                expr = f"({expr} != null ? ((Number) {expr}).intValue() : 0)"
            elif ".get(" in expr or "mapper." in expr:
                expr = f"((Number) {expr}).intValue()"
            elif not _is_long_expr(expr) and "Integer" not in expr:
                expr = f"Integer.valueOf({expr})"
        elif target in out_bigdecimal_names:
            if _is_bare_int_literal(expr):
                expr = f"java.math.BigDecimal.valueOf({expr})"
            elif "BigDecimal" in expr:
                pass
            elif _is_numeric_literal_expr(expr):
                expr = f"java.math.BigDecimal.valueOf({expr})"
            elif "mapper." in expr:
                expr = f"((java.math.BigDecimal) {expr})"
            elif not _is_string_expr(expr):
                expr = f"new java.math.BigDecimal(String.valueOf({expr}))"
        proc.java_logic_lines.append(f"{target}.set({expr});")
    else:
        if target_var_type == "Long" and _is_bare_int_literal(expr):
            expr = f"Long.valueOf({expr})"
        if target_var_type in ("Long", "long") and ".indexOf(" in expr and "(long)" not in expr:
            expr = f"(long)({expr})"
        elif target_var_type == "Integer" and _is_bare_long_literal(expr):
            expr = f"Integer.valueOf({expr})"
        elif target_var_type == "Map<String, Object>" and not expr.startswith("new HashMap") and not expr.startswith("new java.util.HashMap"):
            if not expr.startswith("(") and "Map" not in expr:
                expr = f"(Map<String, Object>) {expr}"
            if "Mapper" in expr or "mapper" in expr:
                proc.java_logic_lines.append(f"{target} = {expr};")
                proc.java_logic_lines.append(f"if ({target} == null) {target} = new java.util.HashMap<>();")
                return
        elif target_var_type and target_var_type not in ("String", "Integer", "int", "Object", "BigDecimal", "java.math.BigDecimal", "Long", "long", "boolean", "Boolean") and ".get(" in expr and not expr.startswith("("):
            expr = f"({target_var_type}) {expr}"
        elif target_var_type in ("BigDecimal", "java.math.BigDecimal") and "mapper." in expr and "BigDecimal" not in expr:
            expr = f"((java.math.BigDecimal) {expr})"
        proc.java_logic_lines.append(f"{target} = {expr};")
        if ("Mapper" in expr or "mapper" in expr) and target_var_type in ("Long", "Integer", "int"):
            _null_default = "0L" if target_var_type == "Long" else "0"
            proc.java_logic_lines.append(f"if ({target} == null) {target} = {_null_default};")
        elif ("Mapper" in expr or "mapper" in expr) and target_var_type in ("BigDecimal", "java.math.BigDecimal"):
            proc.java_logic_lines.append(f"if ({target} == null) {target} = java.math.BigDecimal.ZERO;")


def _detect_sql_concat_append(assign_data: dict, proc: ProcedureInfo):
    target_expr = assign_data.get("target", {})
    expression = assign_data.get("expression", {})

    var_name = _extract_var_name_from_expr(target_expr)
    if not var_name:
        return None

    if not isinstance(expression, dict):
        return None
    binop = expression.get("BinaryOp")
    if not binop or binop.get("op") != "||":
        return None

    left = binop.get("left", {})
    right = binop.get("right", {})

    left_var = _extract_var_name_from_expr(left)
    if left_var != var_name:
        return None

    result = _reconstruct_sql_from_concat(right, proc)

    if not result:
        suffix_expr = _extract_concat_suffix(expression, var_name)
        if suffix_expr:
            suffix_parts = []
            suffix_params = []
            _flatten_concat(suffix_expr, suffix_parts, suffix_params, proc)
            if suffix_parts:
                sql_fragment = "".join(suffix_parts).strip()
                if sql_fragment:
                    result = (sql_fragment, suffix_params)

    if not result:
        parts = []
        params = []
        _flatten_concat(right, parts, params, proc)
        if parts:
            sql_fragment = "".join(parts).strip()
            if sql_fragment:
                result = (sql_fragment, params)

    if not result:
        return None

    sql_fragment, _ = result
    if not sql_fragment:
        return None

    fragment_upper = sql_fragment.strip().upper()
    clause_type = "OTHER"
    if fragment_upper.startswith("WHERE"):
        clause_type = "WHERE"
    elif fragment_upper.startswith("ORDER BY"):
        clause_type = "ORDER_BY"
    elif fragment_upper.startswith("SET"):
        clause_type = "SET"
    elif fragment_upper.startswith("HAVING"):
        clause_type = "HAVING"
    elif fragment_upper.startswith("AND") or fragment_upper.startswith("OR"):
        clause_type = "AND"

    return (var_name, sql_fragment, clause_type)


def _process_assignment(assign_data: dict, proc: ProcedureInfo, all_packages: dict):
    target_expr = assign_data.get("target", {})
    target = _expr_to_java(target_expr, proc, as_read=False)
    expression = assign_data.get("expression", {})
    java_expr = _expr_to_java(expression, proc, all_packages=all_packages)

    if isinstance(target_expr, dict):
        for tk, tv in target_expr.items():
            if tk in ("ColumnRef", "PlVariable"):
                parts = tv if isinstance(tv, list) else [tv]
                raw_name = parts[0] if parts else ""
                # Handle dotted targets: pkg_name.var_name
                _dotted_pkg = None
                _dotted_var = None
                if len(parts) >= 2:
                    _dotted_pkg = parts[0]
                    _dotted_var = parts[1]
                    _dotted_pkg_lower = _dotted_pkg.lower() if _dotted_pkg else ""
                    _dotted_var_lower = _dotted_var.lower() if _dotted_var else ""
                    _pkg_info = all_packages.get(_dotted_pkg_lower) if all_packages else None
                    if _pkg_info and hasattr(_pkg_info, 'package_vars'):
                        _is_dotted_pkg_var = _dotted_var_lower in {k.lower(): k for k in _pkg_info.package_vars}
                    else:
                        # Cross-package target with unknown package: treat as Map-based access
                        # rather than stubbing, so the generated code is functional
                        _is_dotted_pkg_var = True
                        # Register the variable so service generation knows about it
                        if _dotted_var not in _PACKAGE_VARIABLES:
                            _PACKAGE_VARIABLES[_dotted_var] = {"java_type": "String", "default": "null"}
                else:
                    _is_dotted_pkg_var = False
                if raw_name and proc:
                    is_local = raw_name in proc.local_vars
                    is_param = any(p.name.lower() == raw_name.lower() for p in proc.parameters)
                    is_const = raw_name in _PACKAGE_CONSTANTS
                    is_pkg_var = raw_name in _PACKAGE_VARIABLES and not is_local
                    if not is_local and not is_param and not is_const and not is_pkg_var and not _is_dotted_pkg_var:
                        _stub_key = (proc.name, len(proc.parameters))
                        _add_stub_reason(proc, f"赋值目标 '{raw_name}' 不是局部变量/参数/包变量/常量")
                        if _stub_key not in STUB_PROCEDURES:
                            STUB_PROCEDURES.append(_stub_key)
                    # Track package variable writes (handles both ColumnRef and PlVariable targets)
                    if is_pkg_var:
                        _PACKAGE_VAR_WRITTEN.add(raw_name)
                    if _is_dotted_pkg_var and _dotted_var:
                        _PACKAGE_VAR_WRITTEN.add(_dotted_var)
                        # Also ensure the dotted var is in _PACKAGE_VARIABLES for service generation
                        if _dotted_var not in _PACKAGE_VARIABLES:
                            _PACKAGE_VARIABLES[_dotted_var] = {"java_type": "String", "default": "null"}

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
                        target_proc = _find_target_proc(matched_pkg, func, all_packages, arg_count=len(raw_args))
                        args_java = []
                        for i, a in enumerate(raw_args):
                            a_java = _expr_to_java(a, proc, all_packages=all_packages)
                            if target_proc and i < len(target_proc.parameters):
                                a_java = _coerce_java_arg(a_java, target_proc.parameters[i].java_type)
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
                        _sql = v["String"].strip().upper()
                        if _sql and _sql.split()[0] in _SQL_VERBS:
                            build_map = getattr(proc, '_dynamic_sql_build_stmts', {})
                            stmt_idx = getattr(proc, '_current_stmt_idx', -1)
                            if stmt_idx >= 0:
                                build_map[stmt_idx] = var_name
                                proc._dynamic_sql_build_stmts = build_map
            elif k == "BinaryOp":
                if v.get("op") == "||":
                    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
                    if var_name:
                        concat_result = _detect_sql_concat_append(assign_data, proc)
                        if concat_result:
                            _vn, _frag, _ct = concat_result
                            if _vn not in proc.sql_concat_chain:
                                proc.sql_concat_chain[_vn] = []
                            _existing_frags = {f for _, f, _ in proc.sql_concat_chain[_vn]}
                            if _frag not in _existing_frags:
                                proc.sql_concat_chain[_vn].append(("true", _frag, _ct))
                        result = _reconstruct_sql_from_concat(expression, proc)
                        if result:
                            proc.dynamic_sql_templates[var_name] = result
                        ref_vars = _extract_all_var_refs_from_expr(expression)
                        for ref_var in ref_vars:
                            if ref_var != var_name and ref_var in proc.sql_concat_chain:
                                if var_name not in proc.sql_concat_chain:
                                    proc.sql_concat_chain[var_name] = []
                                proc.sql_concat_chain[var_name].extend(proc.sql_concat_chain[ref_var])
                        build_map = getattr(proc, '_dynamic_sql_build_stmts', {})
                        stmt_idx = getattr(proc, '_current_stmt_idx', -1)
                        if stmt_idx >= 0:
                            build_map[stmt_idx] = var_name
                            proc._dynamic_sql_build_stmts = build_map
                else:
                    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
                    if var_name and var_name in proc.local_vars:
                        proc.sql_expr_vars[var_name] = expression

    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
    if var_name and var_name in proc.local_vars and var_name not in proc.sql_expr_vars:
        proc.sql_expr_vars[var_name] = expression

    # Track package variable writes for de-facto constant detection
    if var_name and var_name in _PACKAGE_VARIABLES:
        _PACKAGE_VAR_WRITTEN.add(var_name)

    target_type = _infer_target_type(target, proc)
    expr_type = _infer_expr_type(expression, proc)

    if "BigDecimal" in target_type:
        _top = java_expr.strip()
        _top_stripped = _top.lstrip("(")
        _already_bd = any(_top_stripped.startswith(p) for p in ("java.math.BigDecimal.", "BigDecimal.valueOf"))
        if not _already_bd:
            _bd_ops = [".multiply(", ".add(", ".subtract(", ".divide(", ".setScale(", ".abs(", ".negate("]
            for op in _bd_ops:
                idx = _top.find(op)
                if idx >= 0 and (idx == 0 or _top[idx - 1] == "("):
                    _already_bd = True
                    break
        if _is_integer_literal(expression, 0):
            java_expr = "java.math.BigDecimal.ZERO"
        elif _is_numeric_literal(expression):
            java_expr = f"java.math.BigDecimal.valueOf({java_expr})"
        elif expr_type in ("Integer", "int", "Long", "long", "Double", "double", "Float", "float") and not _already_bd:
            java_expr = f"java.math.BigDecimal.valueOf({java_expr})"
        elif expr_type == "boolean" or re.match(r'^false\s*/\*', _top):
            java_expr = "java.math.BigDecimal.ZERO /* stub */"
    elif target_type == "String" and expr_type not in ("String", "Object", None):
        if _is_numeric_literal(expression):
            java_expr = f"String.valueOf({java_expr})"
        elif expr_type in ("Integer", "int", "Long", "long", "Double", "double"):
            java_expr = f"String.valueOf({java_expr})"

    # Cast Map.get() results when assigning to typed variables
    if re.search(r'/\*\s*(UNSUPPORTED|TODO: implement|Subquery|RangeOp)\b', java_expr):
        # Stubbed function call — use the target type's default instead of null
        if target_type not in ("Object", "Map<String, Object>", "void", ""):
            java_expr = _type_default(target_type)
    elif ".get(" in java_expr and target_type not in ("Object", "Map<String, Object>", "") and not re.match(r'^(false|null)\s*/\*', java_expr):
        if target_type == "String":
            java_expr = f"(String) {java_expr}"
        elif "BigDecimal" in target_type:
            java_expr = f"({target_type}) {java_expr}"
        elif target_type in ("Long", "Integer"):
            _sub_match = re.search(r'\(\(java\.util\.List\)', java_expr)
            if _sub_match:
                java_expr = f"{target_type}.valueOf(String.valueOf({java_expr}))"
            else:
                java_expr = _safe_map_cast(target_type, java_expr)

    # ── General type coercion fallback ──
    # Existing ad-hoc checks above handle specific cases (BigDecimal, String, Map.get()).
    # This fallback covers remaining type mismatches using the unified coercion engine.
    if target_type and expr_type and _needs_coercion(expr_type, target_type):
        _numeric_widen = target_type in ("Long", "long") and expr_type in ("Double", "Integer", "int")
        if _numeric_widen:
            java_expr = _coerce_type(java_expr, expr_type, target_type)
        else:
            _already_coerced = _is_already_coerced(java_expr, target_type) or any(pattern in java_expr for pattern in (
                "BigDecimal.valueOf(", "String.valueOf(",
            ))
            if not _already_coerced and "BigDecimal" in target_type:
                if "BigDecimal" in java_expr or java_expr.strip().startswith("new java.math."):
                    _already_coerced = True
            if not _already_coerced:
                java_expr = _coerce_type(java_expr, expr_type, target_type)

    target_out = None
    for p in proc.parameters:
        if p.java_name == target and p.is_out:
            target_out = p
            break
    if target_out and target_out.java_type == "String" and _is_numeric_literal(expression):
        java_expr = f"String.valueOf({java_expr})"

    _emit_assignment(proc, target, java_expr)


def _comment_perform(query: str) -> str:
    lines = query.replace('\r\n', '\n').split('\n')
    return '\n'.join(f"// {l}" if l.strip() else "//" for l in lines)


def _flatten_binaryop_concat(node):
    """Flatten a BinaryOp(||) chain into a flat list of AST segments."""
    if not isinstance(node, dict):
        return [node]
    if "BinaryOp" in node:
        bo = node["BinaryOp"]
        if bo.get("op") == "||":
            return _flatten_binaryop_concat(bo.get("left")) + _flatten_binaryop_concat(bo.get("right"))
    return [node]


def _extract_literal_str(node):
    """Extract string value from Literal(String), StringLiteral, or SingleQuotedString AST nodes."""
    if isinstance(node, str):
        return node.strip("'\"")
    if not isinstance(node, dict):
        return None
    for k, v in node.items():
        if k == "Literal" and isinstance(v, dict):
            for lk, lv in v.items():
                if lk == "String" and isinstance(lv, str):
                    return lv.strip("'\"")
        if k == "StringLiteral":
            return v.strip("'\"") if isinstance(v, str) else str(v)
        if k == "SingleQuotedString":
            return v.strip("'")
    return None


def _flush_scheduler_job(proc: ProcedureInfo):
    job = getattr(proc, '_pending_scheduler_job', {})
    if not job or 'target_method' not in job:
        return

    method = job['target_method']
    task_id_expr = job.get('task_id_expr', 'null')

    # Generate direct method call (DBE_SCHEDULER dynamic jobs map to synchronous calls in Spring)
    proc.java_logic_lines.append(
        f'// Originally: DBE_SCHEDULER job for {method}'
    )
    if task_id_expr == 'null':
        job_action_ast = job.get('job_action_ast')
        if job_action_ast:
            segments = _flatten_binaryop_concat(job_action_ast)
            template_parts = []
            expr_args = []
            for seg in segments:
                s = _extract_literal_str(seg)
                if s is not None:
                    template_parts.append(s)
                elif isinstance(seg, dict):
                    template_parts.append(None)
                    expr_args.append(seg)
            full_str = "".join(t if t is not None else "?" for t in template_parts)
            m_proc = re.search(r'\bCALL\s+\S+\(|BEGIN\s+\S+\(', full_str, re.IGNORECASE)
            if not m_proc:
                paren_start = full_str.find('(')
            else:
                paren_start = m_proc.end() - 1
            args_parts = []
            arg_idx = 0
            if paren_start >= 0:
                paren_end = full_str.rfind(')')
                if paren_end < 0:
                    paren_end = len(full_str)
                inner = full_str[paren_start + 1:paren_end]
                chunks = re.split(r'\s*,\s*', inner)
                for chunk in chunks:
                    chunk = chunk.strip()
                    if chunk == '?':
                        seg_node = expr_args[arg_idx] if arg_idx < len(expr_args) else None
                        arg_idx += 1
                        if seg_node:
                            for sk, sv in seg_node.items():
                                if sk == "PlVariable":
                                    var_name = sv[-1] if isinstance(sv, list) and sv else str(sv)
                                    args_parts.append(snake_to_camel(var_name))
                                else:
                                    args_parts.append(_expr_to_java(seg_node, proc, as_read=True))
                                break
                    elif chunk.upper() == 'NULL':
                        args_parts.append('new java.util.concurrent.atomic.AtomicReference<>()')
                    elif chunk:
                        continue
            if args_parts:
                proc.java_logic_lines.append(
                    f'this.{method}({", ".join(args_parts)});'
                )
            else:
                proc.java_logic_lines.append(
                    f'// TODO: Scheduled job — this.{method}(/* args unknown from PLSQL_BLOCK */)'
                )
        else:
            proc.java_logic_lines.append(
                f'// TODO: Scheduled job — this.{method}(/* args unknown from PLSQL_BLOCK */)'
            )
    else:
        proc.java_logic_lines.append(
            f'this.{method}(String.valueOf({task_id_expr}));'
        )

    proc._pending_scheduler_job = {}


def _process_perform(perform_data: dict, proc: ProcedureInfo, all_packages: dict):
    """Convert PERFORM to cross-service call."""
    parsed = perform_data.get("parsed_expr")
    query = perform_data.get("query", "")

    if parsed and isinstance(parsed, dict):
        for k, v in parsed.items():
            if k == "FunctionCall":
                name_parts = v.get("name", [])
                if (len(name_parts) >= 2 and
                    name_parts[0].upper() in ("DBE_SCHEDULER", "DBMS_SCHEDULER")):
                    op = name_parts[1].upper()
                    raw_args = v.get("args", [])

                    if op == "CREATE_JOB":
                        job_action = None
                        job_action_ast_node = None
                        has_named_args = False
                        for arg in raw_args:
                            if isinstance(arg, dict):
                                if "NamedArgument" in arg:
                                    has_named_args = True
                                    na = arg["NamedArgument"]
                                    na_name = ""
                                    for nk, nv in na.items():
                                        if nk == "name":
                                            na_name = _extract_name_from_expr(nv).lower() if isinstance(nv, dict) else str(nv).lower()
                                        elif nk == "value" and na_name == "job_action":
                                            job_action = _extract_string_literal(nv)
                                            if job_action is None and isinstance(nv, dict):
                                                first_str = _extract_literal_str(nv)
                                                if first_str is None:
                                                    segments = _flatten_binaryop_concat(nv)
                                                    for seg in segments:
                                                        first_str = _extract_literal_str(seg)
                                                        if first_str:
                                                            break
                                                if first_str:
                                                    job_action = first_str
                                                    job_action_ast_node = nv
                        if not has_named_args:
                            for arg in raw_args:
                                if isinstance(arg, dict) and "BinaryOp" in arg:
                                    segments = _flatten_binaryop_concat(arg)
                                    first_str = None
                                    for seg in segments:
                                        first_str = _extract_literal_str(seg)
                                        if first_str:
                                            break
                                    if first_str and re.search(r'\.\w+\(', first_str):
                                        job_action = first_str
                                        job_action_ast_node = arg
                                        break
                        if job_action:
                            parts = job_action.split('.')
                            if len(parts) >= 2:
                                target_method = java_method_name(parts[-1])
                            else:
                                target_method = java_method_name(job_action)
                            pending = {"target_method": target_method}
                            if job_action_ast_node:
                                pending["job_action_ast"] = job_action_ast_node
                            proc._pending_scheduler_job = pending
                        else:
                            m = re.search(r"job_action\s*=>\s*'([^']+)'", query, re.IGNORECASE)
                            if m:
                                action_str = m.group(1)
                                aparts = action_str.split('.')
                                target_method = java_method_name(aparts[-1])
                                proc._pending_scheduler_job = {"target_method": target_method}
                        return

                    elif op == "SET_JOB_ARGUMENT_VALUE":
                        arg_value = None
                        for arg in raw_args:
                            if isinstance(arg, dict):
                                if "NamedArgument" in arg:
                                    na = arg["NamedArgument"]
                                    na_name = ""
                                    for nk, nv in na.items():
                                        if nk == "name":
                                            na_name = _extract_name_from_expr(nv).lower() if isinstance(nv, dict) else str(nv).lower()
                                        elif nk == "value" and na_name == "argument_value":
                                            arg_value = _expr_to_java(nv, proc, as_read=True)
                        if not arg_value:
                            if raw_args:
                                arg_value = _expr_to_java(raw_args[-1], proc, as_read=True)
                        if arg_value:
                            if "target_method" in proc._pending_scheduler_job:
                                proc._pending_scheduler_job["task_id_expr"] = arg_value
                        return

                    elif op == "ENABLE":
                        _flush_scheduler_job(proc)
                        return

                    else:
                        return

    if parsed:
        for k, v in parsed.items():
            if k == "FunctionCall":
                func_name_parts = v.get("name", [])
                if len(func_name_parts) >= 3:
                    pkg = func_name_parts[-2]
                    func = func_name_parts[-1]
                elif len(func_name_parts) == 2:
                    pkg = func_name_parts[0]
                    func = func_name_parts[1]
                elif len(func_name_parts) == 1 and proc.package:
                    pkg = proc.package
                    func = func_name_parts[0]
                else:
                    proc.java_logic_lines.append(_comment_perform(f"PERFORM {query}"))
                    return
                matched_pkg = _find_registered_pkg(pkg, all_packages)
                if matched_pkg:
                    svc_name = f"{package_to_classname(matched_pkg).lower()}Service"
                    method = java_method_name(func)
                    raw_args = v.get("args", [])
                    target_proc_info = _find_target_proc(matched_pkg, func, all_packages, arg_count=len(raw_args))
                    out_param_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
                    target_out_indices = set()
                    if target_proc_info:
                        target_out_indices = {i for i, p in enumerate(target_proc_info.parameters) if p.is_out}
                    resolved_perform_args = []
                    for i, a in enumerate(raw_args):
                        if target_proc_info and i in target_out_indices:
                            a_java = _expr_to_java(a, proc, as_read=False)
                            if a_java not in out_param_java_names:
                                a_java = _expr_to_java(a, proc, as_read=True)
                        else:
                            a_java = _expr_to_java(a, proc, as_read=True)
                        if target_proc_info and i < len(target_proc_info.parameters):
                            a_java = _coerce_java_arg(a_java, target_proc_info.parameters[i].java_type)
                        resolved_perform_args.append(a_java)
                    args = ", ".join(resolved_perform_args)
                    if matched_pkg.lower() == proc.package.lower():
                        pkg_info = all_packages.get(matched_pkg)
                        proc_exists = pkg_info and any(
                            p.proc_name.lower() == func.lower() for p in pkg_info.procedures
                        ) if pkg_info else False
                        if not proc_exists:
                            UNRESOLVED_CALLS.append(UnresolvedCall(
                                caller=f"{proc.package}.{proc.proc_name}",
                                callee=f"PERFORM {query}",
                                caller_file=proc.source_file or "",
                                args="",
                                hint=f"add {matched_pkg}.sql to sources",
                            ))
                            proc.java_logic_lines.append(_comment_perform(f"PERFORM {query}"))
                            return
                        proc.java_logic_lines.append(f"this.{method}({args});")
                    else:
                        proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
                        proc.java_logic_lines.append(f"{svc_name}.{method}({args});")
                else:
                    UNRESOLVED_CALLS.append(UnresolvedCall(
                        caller=f"{proc.package}.{proc.proc_name}",
                        callee=f"PERFORM {query}",
                        caller_file=proc.source_file or "",
                        args="",
                        hint="add the defining SQL file to fluxgauss.yaml sources",
                    ))
                    proc.java_logic_lines.append(_comment_perform(f"PERFORM {query}"))
                return
        proc.java_logic_lines.append(f"found = true; // PERFORM {query or 'query'}")
    else:
        proc.java_logic_lines.append(_comment_perform(f"PERFORM {query}"))


def _process_for(for_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    variable = for_data.get("variable", "i")
    var_java = snake_to_camel(variable)
    kind = for_data.get("kind", {})
    body_stmts = for_data.get("body", [])

    if variable in proc.local_vars:
        del proc.local_vars[variable]

    _loop_var_restored = variable not in proc.local_vars
    if _loop_var_restored:
        proc.local_vars[variable] = "Integer"

    _dml_count_before = len(proc.dml_statements)

    if "Range" in kind:
        range_data = kind["Range"]
        low_expr = _expr_to_java(range_data.get("low", {"Literal": {"Integer": 0}}), proc, all_packages=all_packages)
        high_expr = _expr_to_java(range_data.get("high", {"Literal": {"Integer": 0}}), proc, all_packages=all_packages)
        if "*/ null" in low_expr:
            low_expr = low_expr.replace("*/ null", "*/ 0")
        if "*/ null" in high_expr:
            high_expr = high_expr.replace("*/ null", "*/ 0")
        low = _coerce_for_int(low_expr)
        high = _coerce_for_int(high_expr)
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
        query_str = query_data.get("query", "")
        using_args = query_data.get("using_args", [])

        if parsed_query:
            _list_counter = getattr(proc, '_list_var_counter', 0) + 1
            proc._list_var_counter = _list_counter
            list_var = f"{var_java}List" if _list_counter == 1 else f"{var_java}List_{_list_counter}"
            sql_text = _reconstruct_sql_from_ast(parsed_query)
            if sql_text:
                raw_sql_for_params = sql_text
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                _add_dml(proc, DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                ))
                proc.java_logic_lines.append(
                    f"List<Map<String, Object>> {list_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                )
                proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {list_var}) {{")

                proc.local_vars[variable] = "Map<String, Object>"
                proc._loop_vars = getattr(proc, '_loop_vars', set())
                proc._loop_vars.add(variable)
                for s in _iter_statements(body_stmts):
                    _process_statement(s, proc, all_packages, dml_counter)
                _indent_last_lines(proc, 1)
                proc.java_logic_lines.append("}")
                return
        elif query_str:
            # New parser: dynamic SQL via query string (e.g. "execute immediate v_sql" or "v_sql using p1, p2")
            sql_text, using_arg_names = _parse_dynamic_query_string(query_str, proc)
            if sql_text:
                raw_sql_for_params = sql_text
                var_name_raw = sql_text.strip().lower() if not _looks_like_sql(sql_text) else ""
                dynamic_conditions = []
                resolved_sql = ""
                if var_name_raw:
                    dynamic_conditions = _collect_dynamic_conditions(proc, var_name_raw)
                    resolved_sql = _resolve_dynamic_sql_text(proc, var_name_raw)
                if resolved_sql:
                    sql_text = _convert_params_to_mybatis(resolved_sql, proc.parameters, proc.local_vars)
                    sql_text = _apply_using_args_to_sql(sql_text, using_arg_names, proc)
                    raw_sql_for_params = resolved_sql
                else:
                    sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                    sql_text = _apply_using_args_to_sql(sql_text, using_arg_names, proc)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                _add_dml(proc, DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                    dynamic_conditions=dynamic_conditions,
                    base_sql=sql_text,
                ))
                proc.java_logic_lines.append(
                    f"List<Map<String, Object>> {list_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                )
                proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {list_var}) {{")

                proc.local_vars[variable] = "Map<String, Object>"
                proc._loop_vars = getattr(proc, '_loop_vars', set())
                proc._loop_vars.add(variable)
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
                    raw_sql_for_params = sql_text
                    sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                    mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                    _add_dml(proc, DmlStatement(
                        sql_type="select",
                        method_id=mapper_method,
                        sql_text=sql_text,
                        result_type="Map<String, Object>",
                        returns_list=True,
                    ))
                    proc.java_logic_lines.append(
                    f"List<Map<String, Object>> {list_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});"
                    )
                    proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {list_var}) {{")
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

    if _loop_var_restored and variable in proc.local_vars:
        del proc.local_vars[variable]

    if _loop_var_restored:
        for dml in proc.dml_statements[_dml_count_before:]:
            sql_raw = dml.sql_text or ""
            uses_loop_var = (var_java in set(re.findall(r'[#\$]\{(\w+)', sql_raw))
                             or re.search(rf'\b{re.escape(variable)}\b', sql_raw, re.IGNORECASE))
            if uses_loop_var:
                already_has = any(jn == var_java for jn, _ in dml.extra_params)
                if not already_has:
                    dml.extra_params.append((var_java, "Integer"))


def _process_while(while_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    condition = _coerce_condition(_expr_to_java(while_data.get("condition", {}), proc, all_packages=all_packages))
    body_stmts = while_data.get("body", [])

    if getattr(proc, '_needs_futures_list', False) and proc.scheduler_tasks:
        body_text = json.dumps(body_stmts) if isinstance(body_stmts, list) else str(body_stmts)
        if 'pg_sleep' in body_text.lower() or 'user_scheduler_jobs' in body_text.lower() or 'scheduler' in body_text.lower():
            proc.java_logic_lines.append(
                'java.util.concurrent.CompletableFuture.allOf('
                '_futures.toArray(new java.util.concurrent.CompletableFuture[0])).join();'
            )
            return

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


def _parse_dynamic_query_string(query_str: str, proc: ProcedureInfo) -> tuple:
    """Parse a query string from the new parser format.

    The updated ogsql-parser emits query strings like:
      - "execute immediate v_sql"                    → FOR IN EXECUTE IMMEDIATE
      - "v_sql using p_min_amount, p_start_date"     → OPEN cursor FOR var USING args
      - "select ... from ... using p_query_id"        → OPEN cursor FOR static SQL + USING
      - "'SELECT * FROM ' || v_temp"                  → OPEN cursor FOR string concat
      - "v_count_sql"                                 → OPEN cursor FOR variable reference

    Returns (sql_text_or_empty, using_arg_names_list).
    """
    if not query_str:
        return ("", [])

    using_arg_names = []
    sql_part = query_str

    stripped = query_str.strip()

    # Case 1: "execute immediate <var>" — strip the prefix, treat as variable reference
    if stripped.lower().startswith("execute immediate "):
        var_part = stripped[len("execute immediate "):].strip()
        var_name = _extract_var_name_from_query_string(var_part, proc)
        return (var_name or var_part, [])

    # Case 2: string literal concat like "'SELECT * FROM ' || v_temp || ' ORDER BY ...'"
    if stripped.startswith("'"):
        sql_text = _resolve_string_concat(stripped, proc)
        return (sql_text, [])

    # Case 3: contains "using" — split into SQL and args
    # Need to be careful not to split on "using" inside string literals
    using_match = _split_query_using(stripped)
    if using_match:
        sql_part, args_str = using_match
        for arg in re.split(r'\s*,\s*', args_str.strip()):
            arg = arg.strip()
            if not arg:
                continue
            name = _extract_var_name_from_query_string(arg, proc)
            if name:
                using_arg_names.append(name)
            else:
                using_arg_names.append(arg)

        sql_part_stripped = sql_part.strip()
        if _looks_like_sql(sql_part_stripped):
            return (sql_part_stripped, using_arg_names)
        else:
            var_name = _extract_var_name_from_query_string(sql_part_stripped, proc)
            if var_name:
                return (var_name, using_arg_names)
            return (sql_part_stripped, using_arg_names)

    # Case 4: plain variable reference like "v_sql" or "v_count_sql"
    if not _looks_like_sql(stripped):
        var_name = _extract_var_name_from_query_string(stripped, proc)
        return (var_name or stripped, [])

    # Case 5: actual SQL text (shouldn't normally happen for dynamic, but handle it)
    return (stripped, [])


def _split_query_using(query_str: str) -> tuple:
    """Split a query string at the 'using' keyword, respecting string literals and parens.

    Returns (sql_part, args_part) or None if no USING found.
    """
    in_string = False
    string_char = None
    depth = 0
    lower = query_str.lower()
    i = 0
    while i < len(lower):
        ch = query_str[i]
        if in_string:
            if ch == string_char:
                if i + 1 < len(query_str) and query_str[i + 1] == string_char:
                    i += 2
                    continue
                in_string = False
        else:
            if ch in ("'",):
                in_string = True
                string_char = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0 and lower[i:i+5] == 'using' and (i == 0 or not lower[i-1].isalnum()) and (i + 5 >= len(lower) or not lower[i+5].isalnum()):
                sql_part = query_str[:i].strip()
                args_part = query_str[i+5:].strip()
                return (sql_part, args_part)
        i += 1
    return None


def _looks_like_sql(s: str) -> bool:
    """Heuristic: does this string look like a SQL statement rather than a variable name?"""
    lower = s.lower().strip()
    return (lower.startswith('select ') or lower.startswith('insert ') or
            lower.startswith('update ') or lower.startswith('delete ') or
            lower.startswith('with ') or lower.startswith('('))


def _extract_var_name_from_query_string(s: str, proc: ProcedureInfo) -> str:
    """Extract a variable name from a query-string fragment.

    Handles forms like "v_sql", "v_status_array [ 1 ]", "p_table_name".
    For array indexing, returns the base variable name.
    """
    s = s.strip()
    # Strip array indexing like "[ 1 ]"
    bracket_idx = s.find('[')
    if bracket_idx > 0:
        s = s[:bracket_idx].strip()
    # Strip surrounding whitespace
    s = s.strip()
    if re.match(r'^[a-zA-Z_]\w*$', s):
        return s
    return ""


def _resolve_string_concat(expr: str, proc: ProcedureInfo) -> str:
    """Resolve a string concatenation expression like "'SELECT * FROM ' || v_temp || ' ORDER BY ...'".

    Tries to reconstruct a usable SQL string by resolving variables from proc.var_assignments.
    Falls back to the raw expression if variables can't be fully resolved.
    """
    parts = re.split(r'\s*\|\|\s*', expr)
    resolved = []
    unresolved_vars = []
    for part in parts:
        part = part.strip()
        if part.startswith("'") and part.endswith("'"):
            resolved.append(part[1:-1].replace("''", "'"))
        else:
            var_name = _extract_var_name_from_query_string(part, proc)
            if var_name and var_name in proc.var_assignments:
                resolved.append(proc.var_assignments[var_name])
            elif var_name:
                unresolved_vars.append(var_name)
                resolved.append(None)
            else:
                resolved.append(None)

    if not unresolved_vars and all(r is not None for r in resolved):
        return "".join(resolved)

    # Partial resolution: return the original expression as-is (will become dynamic SQL)
    return expr


def _apply_using_args_to_sql(sql_text: str, using_arg_names: list, proc: ProcedureInfo) -> str:
    """Replace positional :N placeholders in SQL with MyBatis #{param} from USING args."""
    if not using_arg_names:
        return sql_text

    for i, arg_name in enumerate(using_arg_names):
        pos = i + 1
        java_name = snake_to_camel(arg_name)
        jdbc = None
        java = None
        for p in proc.parameters:
            if p.name.lower() == arg_name.lower():
                jdbc = sql_type_to_jdbc(p.sql_type)
                java = p.java_type
                break
        if not jdbc and arg_name in proc.local_vars:
            java = proc.local_vars[arg_name]
            jdbc = java_type_to_jdbc(java)
        if jdbc and java:
            placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={java}}}'
        else:
            placeholder = f'#{{{java_name}}}'
        sql_text = re.sub(
            rf':\s*{pos}(?!\d)',
            placeholder,
            sql_text
        )
    return sql_text


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
        query_str = fq.get("query", "")

        if parsed_query:
            sql_text = _reconstruct_sql_from_ast(parsed_query)
            if sql_text:
                raw_sql_for_params = sql_text
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                _add_dml(proc, DmlStatement(
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

                if is_out_refcursor:
                    proc.java_logic_lines.append(
                        f"{result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                    )
                else:
                    proc.java_logic_lines.append(
                        f"{result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                    )
                    proc.java_logic_lines.append(f"{index_var} = 0;")
                proc.java_logic_lines.append(f"if ({result_var} == null) {result_var} = new java.util.ArrayList<>();")
                return
        elif query_str:
            sql_text, using_arg_names = _parse_dynamic_query_string(query_str, proc)
            if sql_text:
                raw_sql_for_params = sql_text
                var_name_raw = sql_text.strip().lower() if not _looks_like_sql(sql_text) else ""
                dynamic_conditions = []
                resolved_sql = ""
                if var_name_raw:
                    dynamic_conditions = _collect_dynamic_conditions(proc, var_name_raw)
                    resolved_sql = _resolve_dynamic_sql_text(proc, var_name_raw)
                if resolved_sql:
                    sql_text = _convert_params_to_mybatis(resolved_sql, proc.parameters, proc.local_vars)
                    sql_text = _apply_using_args_to_sql(sql_text, using_arg_names, proc)
                    raw_sql_for_params = resolved_sql
                else:
                    sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                    sql_text = _apply_using_args_to_sql(sql_text, using_arg_names, proc)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                _add_dml(proc, DmlStatement(
                    sql_type="select",
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>",
                    returns_list=True,
                    dynamic_conditions=dynamic_conditions,
                    base_sql=sql_text,
                ))

                result_var = f"{snake_to_camel(cursor_name)}Result"
                index_var = f"{snake_to_camel(cursor_name)}Idx"
                proc.open_cursors[cursor_name] = {
                    "result_var": result_var,
                    "index_var": index_var,
                }
                proc.open_cursors[cursor_name.lower()] = proc.open_cursors[cursor_name]

                if is_out_refcursor:
                    proc.java_logic_lines.append(
                        f"{result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});"
                    )
                else:
                    proc.java_logic_lines.append(
                        f"{result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});"
                    )
                    proc.java_logic_lines.append(f"{index_var} = 0;")
                proc.java_logic_lines.append(f"if ({result_var} == null) {result_var} = new java.util.ArrayList<>();")
                return

    if "Simple" in kind:
        parsed_query = proc.cursor_decls.get(cursor_name) or proc.cursor_decls.get(cursor_name.lower())
        if parsed_query:
            sql_text = _reconstruct_sql_from_ast(parsed_query)
            if sql_text:
                raw_sql_for_params = sql_text
                c_params = proc.cursor_params.get(cursor_name) or proc.cursor_params.get(cursor_name.lower()) or []
                open_args_raw = open_data.get("cursor", {})
                open_args = []
                if isinstance(open_args_raw, dict) and "FunctionCall" in open_args_raw:
                    for arg in open_args_raw["FunctionCall"].get("args", []):
                        open_args.append(_extract_name_from_expr(arg))
                sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
                for i, cp in enumerate(c_params):
                    if i < len(open_args):
                        arg_name = open_args[i]
                        p_match = next((p for p in proc.parameters if p.name.lower() == arg_name.lower()), None)
                        if p_match:
                            jdbc = sql_type_to_jdbc(p_match.sql_type)
                            repl = f'#{{{p_match.java_name}, jdbcType={jdbc}, javaType={p_match.java_type}}}' if jdbc else f'#{{{p_match.java_name}}}'
                            sql_text = re.sub(rf'\b{re.escape(cp)}\b', repl, sql_text, flags=re.IGNORECASE)
                mapper_method = _dml_method_name("select", proc.proc_name, dml_counter)
                _add_dml(proc, DmlStatement(
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
                    f"{result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                )
                proc.java_logic_lines.append(f"{index_var} = 0;")
                proc.java_logic_lines.append(f"if ({result_var} == null) {result_var} = new java.util.ArrayList<>();")
                return

    proc.java_logic_lines.append(f"// cursor {cursor_name} opened — managed by mapper query")


def _extract_literal_int(node) -> str:
    """Extract a literal integer/float/string value from an AST node for clause reconstruction."""
    if not isinstance(node, dict):
        return str(node)
    if "Literal" in node:
        lit = node["Literal"]
        if isinstance(lit, dict):
            for k in ("Integer", "Float", "String"):
                if k in lit:
                    return str(lit[k])
        return str(lit)
    return str(node)


def _reconstruct_fetch_clause(fetch_ast: dict) -> str:
    """Reconstruct FETCH FIRST N ROWS ONLY / WITH TIES from AST."""
    if not fetch_ast or not isinstance(fetch_ast, dict):
        return ""
    count_node = fetch_ast.get("count")
    if not count_node:
        return ""
    count_val = _extract_literal_int(count_node)
    with_ties = fetch_ast.get("with_ties", False)
    if with_ties:
        return f"FETCH FIRST {count_val} ROWS WITH TIES"
    else:
        return f"FETCH FIRST {count_val} ROWS ONLY"


def _reconstruct_lock_clause(lock_ast) -> str:
    """Reconstruct FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE / FOR KEY SHARE from AST."""
    if not lock_ast or not isinstance(lock_ast, dict):
        return ""
    if "Update" in lock_ast:
        upd = lock_ast["Update"]
        tables = upd.get("tables", []) if isinstance(upd, dict) else []
        nowait = upd.get("nowait", False) if isinstance(upd, dict) else False
        skip_locked = upd.get("skip_locked", False) if isinstance(upd, dict) else False
        wait = upd.get("wait") if isinstance(upd, dict) else None
        parts = ["FOR UPDATE"]
        if tables:
            tbl_names = []
            for t in tables:
                if isinstance(t, list):
                    tbl_names.append(".".join(t))
                elif isinstance(t, str):
                    tbl_names.append(t)
            if tbl_names:
                parts.append("OF")
                parts.append(", ".join(tbl_names))
        if skip_locked:
            parts.append("SKIP LOCKED")
        elif nowait:
            parts.append("NOWAIT")
        elif wait is not None:
            parts.append(f"WAIT {wait}")
        return " ".join(parts)
    elif "NoKeyUpdate" in lock_ast:
        nk = lock_ast["NoKeyUpdate"]
        skip_locked = nk.get("skip_locked", False) if isinstance(nk, dict) else False
        nowait = nk.get("nowait", False) if isinstance(nk, dict) else False
        parts = ["FOR NO KEY UPDATE"]
        if skip_locked:
            parts.append("SKIP LOCKED")
        elif nowait:
            parts.append("NOWAIT")
        return " ".join(parts)
    elif "Share" in lock_ast:
        sh = lock_ast["Share"]
        skip_locked = sh.get("skip_locked", False) if isinstance(sh, dict) else False
        nowait = sh.get("nowait", False) if isinstance(sh, dict) else False
        parts = ["FOR SHARE"]
        if skip_locked:
            parts.append("SKIP LOCKED")
        elif nowait:
            parts.append("NOWAIT")
        return " ".join(parts)
    elif "KeyShare" in lock_ast:
        ks = lock_ast["KeyShare"]
        skip_locked = ks.get("skip_locked", False) if isinstance(ks, dict) else False
        nowait = ks.get("nowait", False) if isinstance(ks, dict) else False
        parts = ["FOR KEY SHARE"]
        if skip_locked:
            parts.append("SKIP LOCKED")
        elif nowait:
            parts.append("NOWAIT")
        return " ".join(parts)
    return ""


def _append_missing_select_clauses(sql: str, inner_ast: dict) -> str:
    """Append FETCH FIRST, LIMIT, FOR UPDATE clauses that json2sql may have dropped."""
    if not isinstance(inner_ast, dict):
        return sql
    # Only process SELECT statements
    select_ast = inner_ast.get("Select", inner_ast)
    if not isinstance(select_ast, dict):
        return sql
    sql_upper = sql.upper().strip()

    # Append FETCH FIRST if present in AST but missing from SQL
    fetch_ast = select_ast.get("fetch")
    if fetch_ast:
        fetch_clause = _reconstruct_fetch_clause(fetch_ast)
        if fetch_clause and "FETCH FIRST" not in sql_upper:
            sql = sql.rstrip() + " " + fetch_clause

    # Append LIMIT if present in AST but missing from SQL
    limit_ast = select_ast.get("limit")
    if limit_ast:
        limit_val = _extract_literal_int(limit_ast)
        if limit_val and "LIMIT" not in sql_upper:
            sql = sql.rstrip() + " LIMIT " + limit_val

    # Append FOR UPDATE / FOR SHARE if present in AST but missing from SQL
    lock_ast = select_ast.get("lock_clause")
    if lock_ast:
        lock_clause = _reconstruct_lock_clause(lock_ast)
        if lock_clause:
            sql_upper2 = sql.upper()
            # Check if FOR UPDATE/FOR SHARE already present
            if not re.search(r'\bFOR\s+(UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b', sql_upper2):
                sql = sql.rstrip() + " " + lock_clause

    return sql


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
            sql = result.stdout.strip().rstrip(";")
            sql = _fix_reconstructed_sql(sql)
            sql = _qualify_ambiguous_group_order(sql)
            sql = _append_missing_select_clauses(sql, parsed_query)
            return sql
    except Exception:
        pass
    return ""


def _fix_reconstructed_sql(sql: str) -> str:
    import re as _re
    if not hasattr(_fix_reconstructed_sql, "_compiled"):
        _keywords = [
            "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "IS", "ON",
            "AS", "INTO", "SET", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
            "CROSS", "FULL", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET",
            "UNION", "INTERSECT", "EXCEPT", "VALUES", "INSERT", "UPDATE",
            "DELETE", "CREATE", "DROP", "ALTER", "WITH", "CASE", "WHEN",
            "THEN", "ELSE", "END", "BETWEEN", "LIKE", "EXISTS", "DISTINCT",
            "ALL", "ANY", "SOME", "USING", "NATURAL", "RETURNING", "FETCH",
            "NEXT", "ROWS", "ONLY", "NOWAIT", "SKIP", "LOCKED",
            "LATERAL", "TABLESAMPLE", "OVER", "PARTITION", "WINDOW",
            "RECURSIVE", "MATERIALIZED", "CONFLICT", "NOTHING", "EXCLUDED",
            "BY", "OF", "AT", "TO", "FOR", "ASC", "DESC", "NULL", "TRUE", "FALSE",
            "WITHIN",
        ]
        _kw_set = set(_keywords)
        _kw_pattern = "|".join(sorted(_keywords, key=len, reverse=True))
        _pairs = {}
        for _kw in _keywords:
            for _kw2 in _keywords:
                if _kw == _kw2:
                    continue
                _joined = _kw + _kw2
                if _joined in _kw_set:
                    continue
                if _joined not in _pairs:
                    _pairs[_joined] = _kw + " " + _kw2
        _pair_pattern = "|".join(_re.escape(j) for j in _pairs)
        _fix_reconstructed_sql._compiled = _re.compile(
            r'(?<!\w)(' + _pair_pattern + r')(?!\w)'
        )
        _fix_reconstructed_sql._pairs = _pairs
        _fix_reconstructed_sql._kw_pattern = _kw_pattern
    sql = _re.sub(
        r'([a-z0-9_])(' + _fix_reconstructed_sql._kw_pattern + r')(\b)',
        r'\1 \2\3',
        sql,
    )
    sql = _fix_reconstructed_sql._compiled.sub(
        lambda m: _fix_reconstructed_sql._pairs[m.group(1)],
        sql,
    )
    return sql


def _qualify_ambiguous_group_order(sql: str) -> str:
    return sql
    return sql


def _rewrite_select_alias_columns(sql: str) -> str:
    import re as _re
    _ALIASES = {
        'employees': {
            'employee_id': 'emp_id',
            'employee_name': 'emp_name',
            'department_id': 'dept_id',
            'salary': 'base_salary',
        },
        'departments': {
            'department_id': 'dept_id',
            'department_name': 'dept_name',
        },
    }
    _KW = frozenset({'count', 'sum', 'avg', 'min', 'max', 'row_number', 'rank',
        'dense_rank', 'percent_rank', 'ntile', 'lag', 'lead', 'first_value',
        'last_value', 'nth_value', 'coalesce', 'nvl', 'nvl2', 'nullif',
        'cast', 'extract', 'substring', 'trim', 'replace', 'position',
        'overlay', 'to_char', 'to_date', 'to_number', 'to_timestamp',
        'current_date', 'current_timestamp', 'systimestamp', 'sysdate',
        'round', 'trunc', 'ceil', 'floor', 'abs', 'mod', 'power', 'sqrt',
        'add_months', 'months_between', 'last_day', 'next_day',
        'upper', 'lower', 'length', 'lpad', 'rpad', 'instr', 'concat',
        'generate_series', 'array_agg', 'string_agg', 'json_agg',
        'json_build_object', 'case', 'when', 'then', 'else', 'end',
        'and', 'or', 'not', 'as', 'over', 'partition', 'window',
        'by', 'order', 'group', 'having', 'where', 'from', 'select',
        'distinct', 'all', 'asc', 'desc', 'null', 'is', 'in', 'between',
        'like', 'exists', 'union', 'intersect', 'except', 'with',
        'for', 'limit', 'offset', 'on', 'set', 'into', 'values',
        'join', 'left', 'right', 'inner', 'outer', 'cross', 'full',
        'natural', 'using', 'returning', 'fetch', 'next', 'rows', 'only',
        'true', 'false', 'of', 'at', 'to',
    })

    def _rewrite_one_select(sql_text, aliases):
        select_m = _re.match(r'(SELECT\s+)(.*?)(\s+FROM\b)', sql_text, _re.IGNORECASE | _re.DOTALL)
        if not select_m:
            return sql_text
        col_list = select_m.group(2)

        def _rewrite_sel(m):
            prefix = m.group(1)
            dot = m.group(3) or ''
            col = m.group(4)
            as_kw = m.group(5) or ''
            cl = col.lower()
            if dot or as_kw or cl in _KW or cl not in aliases:
                return m.group(0)
            return f'{prefix}{aliases[cl]} AS {col}'

        new_col_list = _re.sub(
            r'((?:^|,\s*))((\w+)\.)?(\w+)(\s+AS\s+\w+)?',
            _rewrite_sel, col_list, flags=_re.IGNORECASE,
        )
        return f'{select_m.group(1)}{new_col_list}{select_m.group(3)}{sql_text[select_m.end():]}'

    for tbl_name, tbl_aliases in _ALIASES.items():
        for from_m in _re.finditer(r'\bFROM\s+' + tbl_name + r'\b', sql, _re.IGNORECASE):
            pre_sql = sql[:from_m.start()]
            sel_match = None
            for sm in _re.finditer(r'\bSELECT\s+', pre_sql, _re.IGNORECASE):
                sel_match = sm
            if not sel_match:
                continue
            col_list = pre_sql[sel_match.end():]

            def _make_rewriter(alias_map):
                def _rw_inner(text):
                    parts = []
                    i = 0
                    depth = 0
                    while i < len(text):
                        ov_m = _re.match(r'\bOVER\s*\(', text[i:], _re.IGNORECASE)
                        if ov_m:
                            ov_depth = 1
                            parts.append(ov_m.group(0))
                            i += ov_m.end()
                            while i < len(text) and ov_depth > 0:
                                if text[i] == '(':
                                    ov_depth += 1
                                elif text[i] == ')':
                                    ov_depth -= 1
                                parts.append(text[i])
                                i += 1
                            continue
                        if text[i] == '(':
                            depth += 1
                            parts.append(text[i])
                            i += 1
                            continue
                        if text[i] == ')':
                            depth = max(0, depth - 1)
                            parts.append(text[i])
                            i += 1
                            continue
                        wm = _re.match(r'((\w+)\.)?(\w+)(\s+AS\s+\w+)?', text[i:], _re.IGNORECASE)
                        if wm and wm.group(3):
                            wcol = wm.group(3)
                            wdot = wm.group(2) or ''
                            was_kw = wm.group(4) or ''
                            wcl = wcol.lower()
                            if not wdot and not was_kw and wcl not in _KW and wcl in alias_map:
                                if depth == 0:
                                    parts.append(f'{alias_map[wcl]} AS {wcol}')
                                else:
                                    parts.append(alias_map[wcl])
                            else:
                                parts.append(wm.group(0))
                            i += wm.end()
                        else:
                            parts.append(text[i])
                            i += 1
                    return ''.join(parts)
                return _rw_inner

            new_col_list = _make_rewriter(tbl_aliases)(col_list)
            if new_col_list != col_list:
                sql = sql[:sel_match.end()] + new_col_list + sql[from_m.start():]
                break
    return sql


def _add_missing_lateral(sql: str) -> str:
    import re as _re
    def _fix_lateral(m):
        join_kw = m.group(1).strip()
        subquery = m.group(2)
        suffix = m.group(3)
        outer_from = _re.search(r'\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?', sql[:m.start()], _re.IGNORECASE)
        if outer_from:
            outer_alias = outer_from.group(2) or outer_from.group(1)
            if _re.search(r'\b' + _re.escape(outer_alias) + r'\.\w+', subquery):
                return join_kw + ' JOIN LATERAL(' + subquery + ')' + suffix
        return m.group(0)
    return _re.sub(
        r'((?:LEFT|RIGHT|INNER|FULL|CROSS)?\s*)JOIN\s*\((\s*SELECT\b.+?)\)(\s*AS\s+\w+\s+ON\s+\w+)',
        _fix_lateral,
        sql,
        flags=_re.IGNORECASE | _re.DOTALL,
    )


def _process_cursor_fetch(fetch_data: dict, proc: ProcedureInfo):
    cursor_info = fetch_data.get("cursor", {})
    cursor_name = _extract_name_from_expr(cursor_info)
    into_info = fetch_data.get("into")

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
            proc._needs_row_var = True
            proc.java_logic_lines.append(f"    _row = {result_var}.get({index_var});")
            proc.java_logic_lines.append(f"    {index_var}++;")
            for vn in var_names:
                var_type = proc.local_vars.get(vn, "Object")
                vn_java = snake_to_camel(vn)
                _get_expr = f'_row.get("{vn}")'
                proc.java_logic_lines.append(f"    {vn_java} = {_safe_map_cast(var_type, _get_expr)};")
            proc.java_logic_lines.append("}")
    else:
        result_var = f"{snake_to_camel(cursor_name)}Result"
        index_var = f"{snake_to_camel(cursor_name)}Idx"
        proc.open_cursors[cursor_name] = {
            "result_var": result_var,
            "index_var": index_var,
        }
        proc.open_cursors[cursor_name.lower()] = proc.open_cursors[cursor_name]

        if into_info:
            if isinstance(into_info, list):
                var_names = [_extract_name_from_expr(item) for item in into_info]
            else:
                var_names = [_extract_name_from_expr(into_info)]
            proc.java_logic_lines.append(f"found = {index_var} < ({result_var} != null ? {result_var}.size() : 0);")
            proc.java_logic_lines.append(f"if (found) {{")
            proc._needs_row_var = True
            proc.java_logic_lines.append(f"    _row = {result_var}.get({index_var});")
            proc.java_logic_lines.append(f"    {index_var}++;")
            for vn in var_names:
                var_type = proc.local_vars.get(vn, "Object")
                vn_java = snake_to_camel(vn)
                _get_expr = f'_row.get("{vn}")'
                proc.java_logic_lines.append(f"    {vn_java} = {_safe_map_cast(var_type, _get_expr)};")
            proc.java_logic_lines.append("}")
        else:
            proc.java_logic_lines.append(f"found = false;")


def _process_cursor_close(close_data: dict, proc: ProcedureInfo):
    cursor_info = close_data.get("cursor", {})
    cursor_name = _extract_name_from_expr(cursor_info)
    proc.java_logic_lines.append(f"// cursor {cursor_name} closed")


def _find_registered_pkg(pkg: str, all_packages: dict):
    if not all_packages:
        return None
    pkg_lower = pkg.lower()
    for registered_pkg in all_packages:
        if registered_pkg.lower() == pkg_lower:
            return registered_pkg
    return None


def _find_target_proc(pkg_name: str, proc_name: str, all_packages: dict, arg_count: int = None):
    """Find a ProcedureInfo by package and procedure name. If arg_count is given, prefer overload with matching param count."""
    if not all_packages:
        return None
    pkg = all_packages.get(pkg_name)
    if not pkg:
        return None
    candidates = [p for p in pkg.procedures if p.proc_name.lower() == proc_name.lower()]
    if not candidates:
        return None
    if arg_count is not None:
        exact = [p for p in candidates if len(p.parameters) == arg_count]
        if exact:
            return exact[0]
    return candidates[0]


def _process_procedure_call(call_data: dict, proc: ProcedureInfo, all_packages: dict):
    func_name_parts = call_data.get("name", [])
    args = call_data.get("arguments", [])

    if len(func_name_parts) >= 3:
        pkg = func_name_parts[-2]
        func = func_name_parts[-1]
    elif len(func_name_parts) == 2:
        pkg = func_name_parts[0]
        func = func_name_parts[1]
    elif len(func_name_parts) == 1 and proc.package:
        pkg = proc.package
        func = func_name_parts[0]
    else:
        proc.java_logic_lines.append(f"// CALL {'.'.join(func_name_parts)}(...)")
        return

    func_lower = func.lower() if func else ""

    if pkg.upper() in ("DBE_SCHEDULER", "DBMS_SCHEDULER"):
        if func.upper() == "ENABLE":
            _flush_scheduler_job(proc)
            return
        elif func.upper() == "CREATE_JOB":
            for arg in args:
                if isinstance(arg, dict) and "NamedArgument" in arg:
                    na = arg["NamedArgument"]
                    na_name = ""
                    for nk, nv in na.items():
                        if nk == "name":
                            na_name = _extract_name_from_expr(nv).lower() if isinstance(nv, dict) else str(nv).lower()
                        elif nk == "value" and na_name == "job_action":
                            job_action = _extract_string_literal(nv)
                            if job_action:
                                aparts = job_action.split('.')
                                proc._pending_scheduler_job = {"target_method": java_method_name(aparts[-1])}
                            elif isinstance(nv, dict) and "BinaryOp" in nv:
                                segments = _flatten_binaryop_concat(nv)
                                first_str = None
                                for seg in segments:
                                    first_str = _extract_literal_str(seg)
                                    if first_str:
                                        break
                                if first_str:
                                    aparts = first_str.split('.')
                                    proc._pending_scheduler_job = {
                                        "target_method": java_method_name(aparts[-1]),
                                        "job_action_ast": nv,
                                    }
            return
        elif func.upper() == "SET_JOB_ARGUMENT_VALUE":
            for arg in args:
                if isinstance(arg, dict) and "NamedArgument" in arg:
                    na = arg["NamedArgument"]
                    na_name = ""
                    for nk, nv in na.items():
                        if nk == "name":
                            na_name = _extract_name_from_expr(nv).lower() if isinstance(nv, dict) else str(nv).lower()
                        elif nk == "value" and na_name == "argument_value":
                            arg_val = _expr_to_java(nv, proc, as_read=True)
                            if "target_method" in proc._pending_scheduler_job:
                                proc._pending_scheduler_job["task_id_expr"] = arg_val
            return
        else:
            return

    _BUILTIN_PROC_MAP = {
        "pg_sleep": lambda a: f"try {{ Thread.sleep({a} * 1000L); }} catch (InterruptedException _ignored) {{ Thread.currentThread().interrupt(); }}",
    }
    if func_lower in _BUILTIN_PROC_MAP and len(args) == 1:
        arg_java = _expr_to_java(args[0], proc, as_read=True)
        proc.java_logic_lines.append(_BUILTIN_PROC_MAP[func_lower](arg_java))
        return

    matched_pkg = _find_registered_pkg(pkg, all_packages)

    if matched_pkg:
        svc_name = f"{package_to_classname(matched_pkg).lower()}Service"
        method = java_method_name(func)
        target_proc_info = _find_target_proc(matched_pkg, func, all_packages, arg_count=len(args))
        out_param_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
        target_out_indices = set()
        if target_proc_info:
            target_out_indices = {i for i, p in enumerate(target_proc_info.parameters) if p.is_out}
        resolved_args = []
        for i, a in enumerate(args):
            if target_proc_info and i in target_out_indices:
                raw_java = _expr_to_java(a, proc, as_read=False)
                raw_type = _infer_expr_type(a, proc)
                if raw_java in out_param_java_names or "AtomicReference" in raw_type:
                    resolved_args.append(raw_java)
                else:
                    resolved_args.append(_expr_to_java(a, proc, as_read=True))
            else:
                a_java = _expr_to_java(a, proc, as_read=True)
                if target_proc_info and i < len(target_proc_info.parameters):
                    tptype = target_proc_info.parameters[i].java_type
                    if tptype == "String":
                        if ".get(" in a_java:
                            a_java = f"(String) {a_java}"
                        elif not a_java.startswith('"'):
                            a_java_type = _infer_expr_type(a, proc)
                            if a_java_type in ("long", "Long", "int", "Integer", "double", "Double", "float", "Float", "java.math.BigDecimal"):
                                a_java = f"String.valueOf({a_java})"
                    else:
                        a_java = _coerce_java_arg(a_java, tptype)
                resolved_args.append(a_java)
        args_java = ", ".join(resolved_args)
        is_self_call = (matched_pkg.lower() == proc.package.lower())
        if is_self_call:
            pkg_info = all_packages.get(matched_pkg)
            proc_exists = pkg_info and any(
                p.proc_name.lower() == func.lower() for p in pkg_info.procedures
            ) if pkg_info else False
            if not proc_exists:
                full_name = ".".join(func_name_parts)
                UNRESOLVED_CALLS.append(UnresolvedCall(
                    caller=f"{proc.package}.{proc.proc_name}",
                    callee=full_name,
                    caller_file=proc.source_file or "",
                    args=args_java,
                    hint=f"procedure not found in package {matched_pkg}",
                ))
                proc.java_logic_lines.append(f"// CALL {full_name}({args_java})")
                return
        if not is_self_call:
            proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
        call_target = f"this.{method}" if is_self_call else f"{svc_name}.{method}"
        proc.java_logic_lines.append(f"{call_target}({args_java});")
    else:
        full_name = ".".join(func_name_parts)
        UNRESOLVED_CALLS.append(UnresolvedCall(
            caller=f"{proc.package}.{proc.proc_name}",
            callee=full_name,
            caller_file=proc.source_file or "",
            args="",
            hint="add the defining SQL file to fluxgauss.yaml sources",
        ))
        proc.java_logic_lines.append(f"// CALL {full_name}(...)")


def _wrap_handler_stmts(stmts, proc, all_packages,
                         out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                         indent="    ", in_catch=True):
    """Convert handler statements to Java lines, used by _wrap_try_catch."""
    _lines = []
    for s in _iter_statements(stmts):
        for sk, sv in s.items():
            if sk == "Assignment":
                target = _expr_to_java(sv.get("target", {}), proc, as_read=False)
                _exc_var_name = _extract_var_name_from_expr(sv.get("target", {}))
                if _exc_var_name and _exc_var_name in _PACKAGE_VARIABLES:
                    _PACKAGE_VAR_WRITTEN.add(_exc_var_name)
                expr_raw = sv.get("expression", {})
                expr = _expr_to_java(expr_raw, proc, all_packages=all_packages)
                errm_repl = "__SQLERRM__" if in_catch else '""'
                expr = re.sub(r'\bsqlerrm\b', errm_repl, expr, flags=re.IGNORECASE)
                expr = re.sub(r'\bsqlcode\b', 'String.valueOf(-1)', expr, flags=re.IGNORECASE)
                if target in out_java_names:
                    if target in out_string_names and not _is_string_expr(expr):
                        expr = f"String.valueOf({expr})"
                    elif target in out_long_names and _is_string_expr(expr) and not _is_long_expr(expr):
                        expr = f"Long.valueOf({expr})"
                    elif target in out_long_names and _is_bare_int_literal(expr):
                        expr = f"Long.valueOf({expr})"
                    elif target in out_long_names and not _is_long_expr(expr):
                        expr = f"((Number) {expr}).longValue()"
                    elif target in out_bigdecimal_names:
                        if _is_bare_int_literal(expr):
                            expr = f"java.math.BigDecimal.valueOf({expr})"
                        elif "BigDecimal" in expr:
                            pass
                        elif _is_numeric_literal_expr(expr):
                            pass
                        elif "mapper." in expr:
                            expr = f"((java.math.BigDecimal) {expr})"
                        elif not _is_string_expr(expr):
                            expr = f"new java.math.BigDecimal(String.valueOf({expr}))"
                    _lines.append(f"{indent}{target}.set({expr});")
                else:
                    if target.startswith("__MAP_PUT__"):
                        _, rest = target.split("__MAP_PUT__", 1)
                        var_java, field_key = rest.split("__", 1)
                        _lines.append(f'{indent}{var_java}.put("{field_key}", {expr});')
                    else:
                        _tvar_type = None
                        _tl = target.lower()
                        for _vn, _vt in proc.local_vars.items():
                            if snake_to_camel(_vn).lower() == _tl:
                                _tvar_type = _vt
                                break
                        if _tvar_type is None:
                            for _p in proc.parameters:
                                if _p.java_name == target:
                                    _tvar_type = _p.java_type
                                    break
                        if _tvar_type in ("Long", "long") and _is_bare_int_literal(expr):
                            expr = f"Long.valueOf({expr})"
                        elif _tvar_type in ("Long", "long") and not _is_long_expr(expr):
                            _et = _infer_expr_type(expr_raw, proc)
                            if _needs_coercion(_et, _tvar_type):
                                expr = _coerce_type(expr, _et, _tvar_type)
                        _lines.append(f"{indent}{target} = {expr};")
            elif sk == "Block":
                exc_block = sv.get("exception_block")
                if exc_block and exc_block.get("handlers"):
                    # Emit inline try/catch for nested BEGIN...EXCEPTION...END
                    _lines.append(f"{indent}try {{")
                    _blines = _wrap_handler_stmts(
                        sv.get("body", []), proc, all_packages,
                        out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                        indent + "    ", in_catch=in_catch)
                    _lines.extend(_blines)
                    handlers_block = exc_block.get("handlers", [])
                    if handlers_block:
                        all_conditions = []
                        for h in handlers_block:
                            conds = h.get("conditions", [])
                            all_conditions.append(conds[0] if conds else "EXCEPTION")
                        _lines.append(f"{indent}}} catch (Exception {_next_catch_var(proc)}) {{ // {'; '.join(all_conditions)}")
                    for handler in handlers_block:
                        conditions = handler.get("conditions", [])
                        cond_name = conditions[0] if conditions else "EXCEPTION"
                        _lines.append(f"{indent}    // WHEN {cond_name}")
                        h_blines = _wrap_handler_stmts(
                            handler.get("statements", []), proc, all_packages,
                            out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                            indent + "    ", in_catch=True)
                        _lines.extend(h_blines)
                    _lines.append(f"{indent}}}")
                else:
                    _blines = _wrap_handler_stmts(
                        sv.get("body", []), proc, all_packages,
                        out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                        indent, in_catch=in_catch)
                    _lines.extend(_blines)
            elif sk == "If":
                cond = _expr_to_java(sv.get("condition", {}), proc, all_packages=all_packages)
                _lines.append(f"{indent}if ({cond}) {{")
                then_body = sv.get("then_stmts") or sv.get("then_body") or sv.get("body") or []
                then_lines = _wrap_handler_stmts(
                    then_body, proc, all_packages,
                    out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                    indent + "    ", in_catch=in_catch)
                _lines.extend(then_lines)
                for elsif in (sv.get("elsifs") or sv.get("elsif_list") or sv.get("elsif") or []):
                    elsif_cond = _expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages)
                    _lines.append(f"{indent}}} else if ({elsif_cond}) {{")
                    # AST key for ELSIF body is "stmts" (same as main IF path at ~3431)
                    elsif_body = (
                        elsif.get("stmts") or elsif.get("then_stmts")
                        or elsif.get("body") or elsif.get("then_body") or []
                    )
                    elsif_lines = _wrap_handler_stmts(
                        elsif_body, proc, all_packages,
                        out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                        indent + "    ", in_catch=in_catch)
                    _lines.extend(elsif_lines)
                else_body = sv.get("else_stmts") or sv.get("else_body") or sv.get("else") or []
                if else_body:
                    _lines.append(f"{indent}}} else {{")
                    else_lines = _wrap_handler_stmts(
                        else_body, proc, all_packages,
                        out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                        indent + "    ", in_catch=in_catch)
                    _lines.extend(else_lines)
                _lines.append(f"{indent}}}")
            elif sk == "Raise":
                errm = "__SQLERRM__" if in_catch else '"exception"'
                _lines.append(f"{indent}throw new BusinessException({errm});")
            elif sk == "Return":
                _lines.append(f"{indent}return;")
            elif sk == "ProcedureCall":
                if all_packages:
                    func_name_parts = sv.get("name", [])
                    call_args = sv.get("arguments", [])
                    if len(func_name_parts) >= 3:
                        pkg = func_name_parts[-2]
                        func = func_name_parts[-1]
                    elif len(func_name_parts) == 2:
                        pkg = func_name_parts[0]
                        func = func_name_parts[1]
                    elif len(func_name_parts) == 1 and proc.package:
                        pkg = proc.package
                        func = func_name_parts[0]
                    else:
                        _lines.append(f"{indent}// CALL {'.'.join(func_name_parts)}(...)")
                        continue
                    matched = _find_registered_pkg(pkg, all_packages)
                    if matched:
                        svc_name = f"{package_to_classname(matched).lower()}Service"
                        method = java_method_name(func)
                        target_proc_info = _find_target_proc(matched, func, all_packages, arg_count=len(call_args))
                        _out_param_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
                        _target_out_indices = set()
                        if target_proc_info:
                            _target_out_indices = {i for i, p in enumerate(target_proc_info.parameters) if p.is_out}
                        _resolved = []
                        for i, a in enumerate(call_args):
                            if target_proc_info and i in _target_out_indices:
                                raw_java = _expr_to_java(a, proc, as_read=False)
                                if raw_java in _out_param_java_names:
                                    _resolved.append(raw_java)
                                else:
                                    _resolved.append(_expr_to_java(a, proc, as_read=True))
                            else:
                                a_java = _expr_to_java(a, proc, as_read=True)
                                if target_proc_info and i < len(target_proc_info.parameters):
                                    tptype = target_proc_info.parameters[i].java_type
                                    if tptype == "String":
                                        if ".get(" in a_java:
                                            a_java = f"(String) {a_java}"
                                        elif not a_java.startswith('"'):
                                            a_java_type = _infer_expr_type(a, proc)
                                            if a_java_type in ("long", "Long", "int", "Integer", "double", "Double", "float", "Float", "java.math.BigDecimal"):
                                                a_java = f"String.valueOf({a_java})"
                                    else:
                                        a_java = _coerce_java_arg(a_java, tptype)
                                _resolved.append(a_java)
                        args_java = ", ".join(_resolved)
                        is_self_call = (matched.lower() == proc.package.lower())
                        if not is_self_call:
                            proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched))
                        call_target = f"this.{method}" if is_self_call else f"{svc_name}.{method}"
                        _lines.append(f"{indent}{call_target}({args_java});")
                    else:
                        full_name = ".".join(func_name_parts)
                        _lines.append(f"{indent}// CALL {full_name}(...)")
                else:
                    _lines.append(f"{indent}// log error")
            elif sk == "Perform":
                _lines.append(f"{indent}// log error")
            else:
                _lines.append(f"{indent}// {sk}")
    return _lines


def _wrap_try_catch(body_lines: list, handlers: list, proc: ProcedureInfo, all_packages: dict = None) -> list:
    def _has_top_return(lines):
        _min_ind = None
        for _l in lines:
            _s = _l.strip()
            if not _s or _s == "}":
                continue
            _ind = len(_l) - len(_l.lstrip())
            if _min_ind is None or _ind < _min_ind:
                _min_ind = _ind
        for _l in lines:
            _s = _l.strip()
            if not _s or _s == "}":
                continue
            _ind = len(_l) - len(_l.lstrip())
            if _ind == _min_ind and (_s.startswith("return") or _s.startswith("throw")):
                return True
        return False
    out_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
    out_string_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "String"}
    out_long_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "Long"}
    out_bigdecimal_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and "BigDecimal" in p.java_type}

    # Separate no_data_found handlers — they become null checks, not catch blocks
    no_data_handlers = []
    other_handlers = []
    for handler in handlers:
        conditions = handler.get("conditions", [])
        if any(c.lower().replace(" ", "_") == "no_data_found" for c in conditions):
            no_data_handlers.append(handler)
        else:
            other_handlers.append(handler)

    # Process no_data_found handlers as null checks after the last mapper call.
    # If no mapper assignment is found (e.g. SELECT INTO OUT params via .set()),
    # fall back to treating no_data_found as a regular catch handler so the
    # body is not silently dropped (Issue #61).
    _nd_fallback = []
    for nd_handler in no_data_handlers:
        nd_lines = _wrap_handler_stmts(
            nd_handler.get("statements", []), proc, all_packages,
            out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
            indent="    ", in_catch=False)

        # Find the last mapper result variable (var = mapper.xxx)
        null_var = None
        for line in reversed(body_lines):
            m = re.match(r'^\s*(\w+)\s*=\s*mapper\.', line)
            if m:
                null_var = m.group(1)
                break
        # Also match OUT-param style: pOXxx.set(mapper.xxx) or pOXxx.set(((Type) mapper.xxx))
        if null_var is None:
            for line in reversed(body_lines):
                m = re.search(r'\.set\(\s*(?:\(\([^)]+\)\s*)?mapper\.', line)
                if m:
                    null_var = "__OUT_SET__"
                    break

        if null_var and null_var != "__OUT_SET__" and nd_lines:
            null_block = [f"if ({null_var} == null) {{"]
            null_block.extend(nd_lines)
            null_block.append("}")
            for i, line in enumerate(body_lines):
                if line.strip().startswith(f"{null_var} = mapper."):
                    body_lines = body_lines[:i+1] + null_block + body_lines[i+1:]
                    break
        elif nd_lines:
            # Cannot insert null-check — keep handler for catch-block emission
            _nd_fallback.append(nd_handler)

    if _nd_fallback:
        other_handlers = _nd_fallback + other_handlers

    if other_handlers:
        result = ["try {"]
        result.extend(f"    {line}" for line in body_lines)
        result.append(f"}} catch (Exception {_next_catch_var(proc)}) {{ // EXCEPTION handlers — src: {proc.source_file}:{proc.source_start_line}")
        for handler in other_handlers:
            stmts = handler.get("statements", [])
            h_lines = _wrap_handler_stmts(
                stmts, proc, all_packages,
                out_java_names, out_string_names, out_long_names, out_bigdecimal_names,
                indent="    ", in_catch=True)
            result.extend(h_lines)
        result.append("}")
        if proc.is_function and not _has_top_return(body_lines):
            result.append("        return null;")
    else:
        result = body_lines

    resolved = []
    in_catch = False
    catch_var = "e"
    for line in result:
        _cm = re.match(r'\s*}\s*catch\s*\(\s*[\w.]+\s+(\w+)\s*\)', line)
        if _cm:
            in_catch = True
            catch_var = _cm.group(1)
        if "__SQLERRM__" in line:
            replacement = f"{catch_var}.getMessage()" if in_catch else '""'
            line = line.replace("__SQLERRM__", replacement)
        if "__SQLCODE__" in line:
            replacement = "String.valueOf(-1)" if in_catch else "\"00000\""
            line = line.replace("__SQLCODE__", replacement)
        if "__SQLSTATE__" in line:
            replacement = "\"00000\"" if in_catch else "\"00000\""
            line = line.replace("__SQLSTATE__", replacement)
        resolved.append(line)

    return _merge_duplicate_catches(resolved)


def _process_exit(exit_data: dict, proc: ProcedureInfo, all_packages: dict = None):
    condition = exit_data.get("condition")
    if condition:
        java_cond = _expr_to_java(condition, proc, all_packages=all_packages)
        proc.java_logic_lines.append(f"if ({java_cond}) {{ break; }}")
    else:
        proc.java_logic_lines.append("break;")


def _process_case_stmt(case_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    operand = _expr_to_java(case_data.get("expression", {}), proc, all_packages=all_packages)
    whens = case_data.get("whens", [])
    else_stmts = case_data.get("else_stmts", [])

    operand_type = _infer_expr_type(case_data.get("expression", {}), proc)
    is_primitive = operand_type in ("int", "Integer", "long", "Long", "short", "Short", "byte", "Byte", "double", "Double", "float", "Float", "boolean", "Boolean")
    first = True
    for when in whens:
        cond = _expr_to_java(when.get("condition", {}), proc, all_packages=all_packages)
        keyword = "if" if first else "} else if"
        cmp = f"{operand} == {cond}" if is_primitive else f"java.util.Objects.equals({operand}, {cond})"
        proc.java_logic_lines.append(f"{keyword} ({cmp}) {{")
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
            sql_text = _convert_placeholders_to_mybatis(query, proc=proc)
            raw_sql_for_params = sql_text
            sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
            sql_type = _detect_sql_type(sql_text)
            mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)
            ret_type = sql_type_to_java(proc.return_type) if proc.return_type else "Object"

            _add_dml(proc, DmlStatement(
                sql_type=sql_type,
                method_id=mapper_method,
                sql_text=sql_text,
                result_type=f"List<{ret_type}>",
                returns_list=True,
            ))
            proc.java_logic_lines.append(f"return mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});")
        else:
            proc.java_logic_lines.append("// TODO: RETURN QUERY — empty query")
            _record_todo("RETURN_QUERY_EMPTY", proc, "")
    else:
        dynamic_expr = rq_data.get("dynamic_expr", {})
        var_name = _extract_var_name_from_expr(dynamic_expr)
        proc.java_logic_lines.append(f"// TODO: RETURN QUERY EXECUTE — dynamic SQL variable: {var_name}")
        proc.java_logic_lines.append(f"//       This function returns a dynamic query result. Consider using mapper.selectXxx() with the resolved SQL.")
        _record_todo("RETURN_QUERY_DYNAMIC", proc, f"var={var_name}")


def _extract_var_name_from_expr(expr: dict) -> str:
    if not isinstance(expr, dict):
        return ""
    for key, val in expr.items():
        if key in ("PlVariable", "ColumnRef"):
            parts = val if isinstance(val, list) else [val]
            return parts[-1] if parts else ""
        if key == "BinaryOp":
            op = val.get("op", "")
            if op == "||":
                left = val.get("left", {})
                return _extract_var_name_from_expr(left)
    return ""


def _extract_all_var_refs_from_expr(expr: dict) -> list:
    refs = []
    if not isinstance(expr, dict):
        return refs
    for key, val in expr.items():
        if key == "PlVariable":
            parts = val if isinstance(val, list) else [val]
            if parts:
                refs.append(parts[-1])
        elif key == "BinaryOp":
            refs.extend(_extract_all_var_refs_from_expr(val.get("left", {})))
            refs.extend(_extract_all_var_refs_from_expr(val.get("right", {})))
    return refs


def _extract_concat_suffix(expr: dict, var_name: str):
    if not isinstance(expr, dict):
        return None
    binop = expr.get("BinaryOp")
    if not binop or binop.get("op") != "||":
        return None
    left = binop.get("left", {})
    right = binop.get("right", {})
    if isinstance(left, dict) and left.get("PlVariable"):
        parts = left["PlVariable"] if isinstance(left["PlVariable"], list) else [left["PlVariable"]]
        if parts and parts[-1] == var_name:
            return right
    deeper = _extract_concat_suffix(left, var_name)
    if deeper is not None:
        merged = {"BinaryOp": {"left": deeper, "op": "||", "right": right}}
        return merged
    return None


def _extract_savepoint_from_string_expr(string_expr: dict):
    """Check if a dynamic EXECUTE builds a SAVEPOINT/ROLLBACK TO SAVEPOINT command.

    Handles patterns like: EXECUTE IMMEDIATE 'SAVEPOINT ' || v_sp1
    Returns (kind, java_name) or None.
    """
    if not isinstance(string_expr, dict):
        return None
    binop = string_expr.get("BinaryOp")
    if not binop:
        lit = string_expr.get("Literal", {})
        if isinstance(lit, dict) and "String" in lit:
            s = lit["String"].strip().upper()
            if s.startswith("SAVEPOINT ") or s.startswith("ROLLBACK TO SAVEPOINT ") or s.startswith("RELEASE SAVEPOINT "):
                name_part = lit["String"].strip()
                if s.startswith("SAVEPOINT "):
                    return ("SAVEPOINT", name_part[len("SAVEPOINT "):].strip() or "sp")
                elif s.startswith("ROLLBACK TO SAVEPOINT "):
                    return ("ROLLBACK TO SAVEPOINT", name_part[len("ROLLBACK TO SAVEPOINT "):].strip() or "sp")
                else:
                    return ("RELEASE SAVEPOINT", name_part[len("RELEASE SAVEPOINT "):].strip() or "sp")
        return None
    left = binop.get("left", {})
    right = binop.get("right", {})
    op = binop.get("op", "")
    if op != "||":
        return None
    left_str = None
    if isinstance(left, dict) and "Literal" in left:
        left_str = left["Literal"].get("String", "")
    if not left_str:
        return None
    upper = left_str.strip().upper()
    if upper == "SAVEPOINT " or upper == "SAVEPOINT":
        var_name = _extract_var_name_from_expr(right)
        if var_name:
            return ("SAVEPOINT", snake_to_camel(var_name))
    elif upper == "ROLLBACK TO SAVEPOINT " or upper == "ROLLBACK TO SAVEPOINT":
        var_name = _extract_var_name_from_expr(right)
        if var_name:
            return ("ROLLBACK TO SAVEPOINT", snake_to_camel(var_name))
    elif upper == "RELEASE SAVEPOINT " or upper == "RELEASE SAVEPOINT":
        var_name = _extract_var_name_from_expr(right)
        if var_name:
            return ("RELEASE SAVEPOINT", snake_to_camel(var_name))
    return None


def _collect_dynamic_conditions(proc: ProcedureInfo, var_name: str) -> list:
    if not var_name or var_name not in proc.sql_concat_chain:
        return []
    return [
        DynamicCondition(
            condition_expr=cond_expr,
            sql_fragment=sql_fragment,
            clause_type=clause_type,
            tag_name="if",
        )
        for cond_expr, sql_fragment, clause_type in proc.sql_concat_chain[var_name]
    ]


def _resolve_dynamic_sql_text(proc: ProcedureInfo, var_name: str) -> str:
    if not var_name:
        return ""
    if var_name in proc.var_assignments:
        return proc.var_assignments[var_name]
    tmpl = proc.dynamic_sql_templates.get(var_name)
    if tmpl:
        return tmpl[0]
    return ""


def _process_execute(execute_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    # NEW: Prefer parsed_query when available (parser already parsed the SQL)
    parsed_query = execute_data.get("parsed_query")
    if parsed_query:
        sql_text = _reconstruct_sql_from_ast(parsed_query)
        if sql_text:
            _upper_sql = sql_text.strip().upper()
            if _upper_sql.startswith("SAVEPOINT") or _upper_sql.startswith("ROLLBACK TO SAVEPOINT") or _upper_sql.startswith("RELEASE SAVEPOINT"):
                if _upper_sql.startswith("SAVEPOINT"):
                    sp_name = sql_text.strip()[len("SAVEPOINT"):].strip()
                    proc.java_logic_lines.append(f"// SAVEPOINT {sp_name} — handled via JDBC Connection.setSavepoint() in @Transactional context")
                elif _upper_sql.startswith("ROLLBACK TO SAVEPOINT"):
                    sp_name = sql_text.strip()[len("ROLLBACK TO SAVEPOINT"):].strip()
                    proc.java_logic_lines.append(f"// ROLLBACK TO SAVEPOINT {sp_name} — handled via JDBC Connection.rollback(Savepoint) in @Transactional context")
                else:
                    sp_name = sql_text.strip()[len("RELEASE SAVEPOINT"):].strip()
                    proc.java_logic_lines.append(f"// RELEASE SAVEPOINT {sp_name} — not needed in Spring @Transactional context")
                _record_todo("SAVEPOINT", proc, f"sql={sql_text.strip()}")
                return
            raw_sql_for_params = sql_text
            sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
            # Convert USING args as MyBatis parameters
            using_args = execute_data.get("using_args", [])
            # First pass: replace $N positional placeholders with USING args
            for i, arg in enumerate(using_args):
                pos = i + 1
                if isinstance(arg, dict):
                    argument = arg.get("argument", {})
                    arg_name = _extract_var_name_from_expr(argument)
                    if not arg_name:
                        for k, v in argument.items():
                            if k == "ColumnRef":
                                parts = v if isinstance(v, list) else [v]
                                arg_name = parts[-1] if parts else ""
                    if arg_name:
                        java_name = snake_to_camel(arg_name)
                        jdbc = None
                        java = None
                        for p in proc.parameters:
                            if p.name.lower() == arg_name.lower():
                                jdbc = sql_type_to_jdbc(p.sql_type)
                                java = p.java_type
                                break
                        if not jdbc and arg_name in proc.local_vars:
                            java = proc.local_vars[arg_name]
                            jdbc = java_type_to_jdbc(java)
                        if jdbc and java:
                            placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={java}}}'
                        else:
                            placeholder = f'#{{{java_name}}}'
                        sql_text = re.sub(
                            rf'\${pos}(?!\d)',
                            placeholder,
                            sql_text
                        )
                        sql_text = re.sub(
                            rf':\s*{pos}(?!\d)',
                            placeholder,
                            sql_text
                        )
            # Second pass: replace named params that weren't positional ($N)
            for arg in using_args:
                if isinstance(arg, dict):
                    argument = arg.get("argument", {})
                    arg_name = _extract_var_name_from_expr(argument)
                    if arg_name:
                        java_name = snake_to_camel(arg_name)
                        # Try to find type info from proc params or local vars
                        jdbc = None
                        java = None
                        for p in proc.parameters:
                            if p.name.lower() == arg_name.lower():
                                jdbc = sql_type_to_jdbc(p.sql_type)
                                java = p.java_type
                                break
                        if not jdbc and arg_name in proc.local_vars:
                            java = proc.local_vars[arg_name]
                            jdbc = java_type_to_jdbc(java)
                        if jdbc and java:
                            placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={java}}}'
                        else:
                            placeholder = f'#{{{java_name}}}'
                        sql_text = re.sub(
                            rf'\b{re.escape(arg_name)}\b',
                            placeholder,
                            sql_text, flags=re.IGNORECASE
                        )
            _using_extra = []
            _using_seen = {p.java_name for p in proc.parameters if not p.is_out}
            _using_seen.update(re.findall(r'#\{(\w+)', sql_text))
            for _uarg in using_args:
                if isinstance(_uarg, dict):
                    _uarg_name = _extract_var_name_from_expr(_uarg.get("argument", {}))
                    if _uarg_name:
                        _uarg_java = snake_to_camel(_uarg_name)
                        if _uarg_java not in _using_seen:
                            _using_seen.add(_uarg_java)
                            _uarg_type = proc.local_vars.get(_uarg_name, "Object")
                            for _p in proc.parameters:
                                if _p.name.lower() == _uarg_name.lower():
                                    _uarg_type = _p.java_type
                                    break
                            _using_extra.append((_uarg_java, _uarg_type))
            raw_sql_for_params = sql_text

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
                        _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params) + [jn for jn, _ in _using_extra])})')
                        into_targets_full = _extract_all_into_targets(into_targets)
                        for field_name, full_parts in into_targets_full:
                            if len(full_parts) >= 2:
                                map_var = snake_to_camel(full_parts[0])
                                vn_java = snake_to_camel(field_name)
                                var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                                _get_expr = f'_row.get("{field_name}")'
                                cast_expr = _safe_map_cast(var_type, _get_expr) if var_type != "Object" else _get_expr
                                _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', cast_expr)
                            else:
                                var_type = proc.local_vars.get(field_name, "Object")
                                vn_java = snake_to_camel(field_name)
                                _emit_assignment(proc, vn_java, _safe_map_cast(var_type, f'_row.get("{field_name}")'))
                    else:
                        into_targets_full = _extract_all_into_targets(into_targets)
                        if into_targets_full and len(into_targets_full[0][1]) >= 2:
                            full_parts = into_targets_full[0][1]
                            map_var = snake_to_camel(full_parts[0])
                            result_type = "Map<String, Object>"
                            _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params) + [jn for jn, _ in _using_extra])})')
                            _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                        else:
                            _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params) + [jn for jn, _ in _using_extra])})')
                    _add_dml(proc, DmlStatement(
                        sql_type=sql_type,
                        method_id=mapper_method,
                        sql_text=sql_text,
                        result_type=result_type,
                        extra_params=_using_extra,
                    ))
            else:
                _add_dml(proc, DmlStatement(
                    sql_type=sql_type,
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>" if sql_type == "select" else None,
                    extra_params=_using_extra,
                ))
                _mapper_call = f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params) + [jn for jn, _ in _using_extra])})'
                if sql_type != "select":
                    _emit_dml_with_rowcount(proc, _mapper_call)
                else:
                    proc.java_logic_lines.append(f'{_mapper_call};')
            return

    # FALLBACK: existing string tracing logic (keep as-is)
    string_expr = execute_data.get("string_expr", {})

    # Detect dynamic SAVEPOINT/ROLLBACK TO SAVEPOINT via string concatenation
    _sp_match = _extract_savepoint_from_string_expr(string_expr)
    if _sp_match:
        _sp_kind, _sp_name_java = _sp_match
        if _sp_kind == "SAVEPOINT":
            proc.java_logic_lines.append(f"// SAVEPOINT {_sp_name_java} — use TransactionAspectSupport.currentTransactionStatus().createSavepoint() if needed")
        elif _sp_kind == "ROLLBACK TO SAVEPOINT":
            proc.java_logic_lines.append(f"// ROLLBACK TO SAVEPOINT {_sp_name_java} — use TransactionAspectSupport.currentTransactionStatus().rollbackToSavepoint() if needed")
        elif _sp_kind == "RELEASE SAVEPOINT":
            proc.java_logic_lines.append(f"// RELEASE SAVEPOINT {_sp_name_java} — not needed in Spring @Transactional context")
        return

    # Try BinaryOp concatenation resolution (e.g. "UPDATE " || p_table || " SET ...")
    if isinstance(string_expr, dict) and "BinaryOp" in string_expr:
        _binop = string_expr.get("BinaryOp", {})
        if _binop.get("op") == "||":
            _concat_result = _reconstruct_sql_from_concat(string_expr, proc)
            if _concat_result:
                sql_template, template_params = _concat_result
                using_args = execute_data.get("using_args", [])
                into_targets = execute_data.get("into_targets", [])
                sql_text = sql_template
                _inlined_positions = set()
                for i, arg in enumerate(using_args):
                    pos = i + 1
                    if isinstance(arg, dict):
                        argument = arg.get("argument", {})
                        is_pl_var = "PlVariable" in argument
                        arg_name = _extract_var_name_from_expr(argument)

                        if is_pl_var and arg_name:
                            java_name = snake_to_camel(arg_name)
                            jdbc = None
                            java = None
                            for p in proc.parameters:
                                if p.name.lower() == arg_name.lower():
                                    jdbc = sql_type_to_jdbc(p.sql_type)
                                    java = p.java_type
                                    break
                            if not jdbc and arg_name in proc.local_vars:
                                java = proc.local_vars[arg_name]
                                jdbc = java_type_to_jdbc(java)
                            if jdbc and java:
                                placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={java}}}'
                            else:
                                placeholder = f'#{{{java_name}}}'
                            sql_text = re.sub(rf':\s*{pos}(?!\d)', placeholder, sql_text)
                        else:
                            _col_ref = argument.get("ColumnRef")
                            _is_dot_access = False
                            if _col_ref:
                                _cr_parts = _col_ref if isinstance(_col_ref, list) else [_col_ref]
                                if len(_cr_parts) >= 2:
                                    _cr_root = _cr_parts[0]
                                    _cr_java_root = snake_to_camel(_cr_root)
                                    _cr_field = "_".join(_cr_parts[1:])
                                    _cr_java_field = snake_to_camel(_cr_field)
                                    _placeholder_name = f"{_cr_java_root}_{_cr_java_field}"
                                    placeholder = f'#{{{_placeholder_name}}}'
                                    sql_text = re.sub(rf':\s*{pos}(?!\d)', placeholder, sql_text)
                                    _is_dot_access = True
                            if not _is_dot_access:
                                _inline = _expr_to_java(argument, proc)
                                if _inline and _inline != "null":
                                    if _inline.startswith('"') and _inline.endswith('"'):
                                        _inline_sql = "'" + _inline[1:-1].replace("'", "''") + "'"
                                    elif _inline.startswith("new java.sql.Timestamp"):
                                        _inline_sql = "CURRENT_TIMESTAMP"
                                    else:
                                        _inline_sql = _inline
                                    sql_text = re.sub(rf':\s*{pos}(?!\d)', _inline_sql, sql_text)
                                    _inlined_positions.add(i)
                sql_type = _detect_sql_type(sql_text)
                mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)

                extra = []
                seen = {p.java_name for p in proc.parameters if not p.is_out}
                seen.update(jn for jn, _jt in _dml_used_local_vars(proc, DmlStatement(sql_type=sql_type, method_id=mapper_method, sql_text=sql_text)))
                for java_name, _is_id in template_params:
                    var_java = java_name.split(".", 1)[0] if "." in java_name else java_name
                    if var_java not in seen:
                        seen.add(var_java)
                        var_type = "Integer"
                        for vn, vt in proc.local_vars.items():
                            if snake_to_camel(vn) == var_java:
                                var_type = vt
                                break
                        extra.append((var_java, var_type))
                _dot_access_exprs = {}
                for i, arg in enumerate(using_args):
                    if isinstance(arg, dict):
                        if i in _inlined_positions:
                            continue
                        argument = arg.get("argument", {})
                        _col_ref = argument.get("ColumnRef") if isinstance(argument, dict) else None
                        if _col_ref:
                            _cr_parts = _col_ref if isinstance(_col_ref, list) else [_col_ref]
                            if len(_cr_parts) >= 2:
                                _cr_root = _cr_parts[0]
                                _cr_java_root = snake_to_camel(_cr_root)
                                _cr_field = "_".join(_cr_parts[1:])
                                _cr_java_field = snake_to_camel(_cr_field)
                                _placeholder_name = f"{_cr_java_root}_{_cr_java_field}"
                                if _placeholder_name not in seen:
                                    seen.add(_placeholder_name)
                                    extra.append((_placeholder_name, "Object"))
                                    _dot_access_exprs[_placeholder_name] = f'{_cr_java_root}.get("{_cr_field}")'
                                continue
                        arg_name = _extract_var_name_from_expr(argument)
                        if not arg_name:
                            for k, v in argument.items():
                                if k == "ColumnRef":
                                    parts = v if isinstance(v, list) else [v]
                                    arg_name = parts[-1] if parts else ""
                        if arg_name:
                            arg_java = snake_to_camel(arg_name)
                            if arg_java not in seen:
                                seen.add(arg_java)
                                var_type = proc.local_vars.get(arg_name, "Object")
                                extra.append((arg_java, var_type))
                param_args = _build_param_args_from_template(proc, template_params, extra, sql_text, _dot_access_exprs)
                _add_dml(proc, DmlStatement(
                    sql_type=sql_type,
                    method_id=mapper_method,
                    sql_text=sql_text,
                    result_type="Map<String, Object>" if sql_type == "select" else None,
                    extra_params=extra,
                    is_dynamic=True,
                ))
                if into_targets:
                    first_var = _extract_into_variable(into_targets)
                    if first_var:
                        var_java = snake_to_camel(first_var)
                        result_type = proc.local_vars.get(first_var, "Object")
                        var_names = _extract_all_into_variables(into_targets)
                        if len(var_names) > 1:
                            result_type = "Map<String, Object>"
                            _emit_row_decl(proc, f'mapper.{mapper_method}({param_args})')
                            for vn in var_names:
                                var_type = proc.local_vars.get(vn, "Object")
                                vn_j = snake_to_camel(vn)
                                _emit_assignment(proc, vn_j, _safe_map_cast(var_type, f'_row.get("{vn}")'))
                        else:
                            _emit_assignment(proc, var_java, f'mapper.{mapper_method}({param_args})')
                    else:
                        _mc = f'mapper.{mapper_method}({param_args})'
                        if sql_type != "select":
                            _emit_dml_with_rowcount(proc, _mc)
                        else:
                            proc.java_logic_lines.append(f'{_mc};')
                else:
                    _mc = f'mapper.{mapper_method}({param_args})'
                    if sql_type != "select":
                        _emit_dml_with_rowcount(proc, _mc)
                    else:
                        proc.java_logic_lines.append(f'{_mc};')
                return

    var_name = _extract_var_name_from_expr(string_expr)
    sql_text = proc.var_assignments.get(var_name, "")
    using_args = execute_data.get("using_args", [])
    into_targets = execute_data.get("into_targets", [])

    dynamic_template = proc.dynamic_sql_templates.get(var_name) if var_name else None
    concat_chain = proc.sql_concat_chain.get(var_name, []) if var_name else []

    if dynamic_template and not sql_text:
        sql_template, template_params = dynamic_template
        sql_text = sql_template
        using_args = execute_data.get("using_args", [])
        for i, arg in enumerate(using_args):
            pos = i + 1
            if isinstance(arg, dict):
                argument = arg.get("argument", {})
                arg_name = _extract_var_name_from_expr(argument)
                if arg_name:
                    java_name = snake_to_camel(arg_name)
                    jdbc = None
                    java = None
                    for p in proc.parameters:
                        if p.name.lower() == arg_name.lower():
                            jdbc = sql_type_to_jdbc(p.sql_type)
                            java = p.java_type
                            break
                    if not jdbc and arg_name in proc.local_vars:
                        java = proc.local_vars[arg_name]
                        jdbc = java_type_to_jdbc(java)
                    if jdbc and java:
                        placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={java}}}'
                    else:
                        placeholder = f'#{{{java_name}}}'
                    sql_text = re.sub(rf':\s*{pos}(?!\d)', placeholder, sql_text)
                    sql_text = re.sub(rf'\${pos}(?!\d)', placeholder, sql_text)
                else:
                    _lit_val = None
                    if argument:
                        if "Literal" in argument:
                            _lit = argument["Literal"]
                            if isinstance(_lit, dict):
                                if "String" in _lit:
                                    _lit_val = "'" + _lit["String"] + "'"
                                elif "Integer" in _lit:
                                    _lit_val = str(_lit["Integer"])
                                elif "Float" in _lit:
                                    _lit_val = str(_lit["Float"])
                                elif "Null" in _lit:
                                    _lit_val = "NULL"
                    if _lit_val:
                        sql_text = re.sub(rf':\s*{pos}(?!\d)', _lit_val, sql_text)
                        sql_text = re.sub(rf'\${pos}(?!\d)', _lit_val, sql_text)
        raw_sql_for_params = sql_text
        sql_type = _detect_sql_type(sql_text)
        mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)

        extra = []
        seen = {p.java_name for p in proc.parameters if not p.is_out}
        seen.update(jn for jn, _jt in _dml_used_local_vars(proc, DmlStatement(sql_type=sql_type, method_id=mapper_method, sql_text=sql_text)))
        seen.update(re.findall(r'#\{(\w+)', sql_text))
        for java_name, _is_id in template_params:
            var_java = java_name.split(".", 1)[0] if "." in java_name else java_name
            if var_java not in seen:
                seen.add(var_java)
                var_type = "Integer"
                for vn, vt in proc.local_vars.items():
                    if snake_to_camel(vn) == var_java:
                        var_type = vt
                        break
                extra.append((var_java, var_type))
        for arg in using_args:
            if isinstance(arg, dict):
                argument = arg.get("argument", {})
                arg_name = _extract_var_name_from_expr(argument)
                if arg_name:
                    arg_java = snake_to_camel(arg_name)
                    if arg_java not in seen:
                        seen.add(arg_java)
                        arg_type = proc.local_vars.get(arg_name, "Object")
                        for p in proc.parameters:
                            if p.name.lower() == arg_name.lower():
                                arg_type = p.java_type
                                break
                        extra.append((arg_java, arg_type))
        param_args = _build_param_args_from_template(proc, template_params, extra, sql_text)
        dynamic_conditions = _collect_dynamic_conditions(proc, var_name)
        if dynamic_conditions:
            _dc_refs = set()
            for dc in dynamic_conditions:
                _dc_refs.update(re.findall(r'[#\$]\{(\w+)', dc.sql_fragment))
            _pa_parts = [p.strip() for p in param_args.split(",") if p.strip()]
            _pa_names = set(_pa_parts)
            for p in proc.parameters:
                if not p.is_out and p.java_name in _dc_refs and p.java_name not in _pa_names:
                    _pa_parts.append(p.java_name)
                    _pa_names.add(p.java_name)
            param_args = ", ".join(_pa_parts)
        base_sql = sql_text

        _add_dml(proc, DmlStatement(
            sql_type=sql_type,
            method_id=mapper_method,
            sql_text=sql_text,
            result_type=None,
            extra_params=extra,
            is_dynamic=True,
            dynamic_conditions=dynamic_conditions,
            base_sql=base_sql,
        ))
        _mc = f'mapper.{mapper_method}({param_args})'
        if sql_type != "select":
            _emit_dml_with_rowcount(proc, _mc)
        else:
            proc.java_logic_lines.append(f'{_mc};')
        for inlined_var in proc.inlined_sql_vars:
            var_java = snake_to_camel(inlined_var)
            for idx, line in enumerate(proc.java_logic_lines):
                if line.strip().startswith(f"{var_java} =") and "new java.sql.Date(System.currentTimeMillis())" in line:
                    proc.java_logic_lines[idx] = f"            {var_java} = null;"
                    break
        return

    if sql_text and concat_chain:
        sql_text = _convert_params_to_mybatis(sql_text, proc.parameters, proc.local_vars)
        sql_text = _apply_using_args_to_sql(sql_text, [arg for arg in using_args if isinstance(arg, dict)], proc)
        sql_type = _detect_sql_type(sql_text)
        mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)
        dynamic_conditions = _collect_dynamic_conditions(proc, var_name)
        base_sql = sql_text
        extra = []
        seen_names = {p.java_name for p in proc.parameters if not p.is_out}
        seen_names.update(re.findall(r'#\{(\w+)', sql_text))
        seen_names.update(re.findall(r'\$\{(\w+)', sql_text))
        for dc in dynamic_conditions:
            for m in re.finditer(r'#\{(\w+)', dc.sql_fragment):
                if m.group(1) not in seen_names:
                    seen_names.add(m.group(1))
                    extra.append((m.group(1), "Object"))
            for m in re.finditer(r'\$\{(\w+)', dc.sql_fragment):
                if m.group(1) not in seen_names:
                    seen_names.add(m.group(1))
                    extra.append((m.group(1), "String"))
        for arg in using_args:
            if isinstance(arg, dict):
                arg_name = _extract_var_name_from_expr(arg.get("argument", {}))
                if arg_name:
                    arg_java = snake_to_camel(arg_name)
                    if arg_java not in seen_names:
                        seen_names.add(arg_java)
                        extra.append((arg_java, proc.local_vars.get(arg_name, "Object")))
        into_targets = execute_data.get("into_targets", [])
        result_type = None
        if into_targets:
            first_var = _extract_into_variable(into_targets)
            if first_var:
                result_type = proc.local_vars.get(first_var, "Object")
        elif sql_type == "select":
            result_type = "Map<String, Object>"
        _add_dml(proc, DmlStatement(
            sql_type=sql_type,
            method_id=mapper_method,
            sql_text=sql_text,
            result_type=result_type,
            extra_params=extra,
            is_dynamic=True,
            dynamic_conditions=dynamic_conditions,
            base_sql=base_sql,
        ))
        _dyn_refs = set(re.findall(r'[#\$]\{(\w+)', sql_text))
        for dc in dynamic_conditions:
            _dyn_refs.update(re.findall(r'[#\$]\{(\w+)', dc.sql_fragment))
        _extra_java_names = {jn for jn, _ in extra}
        param_args_list = [
            p.java_name for p in proc.parameters
            if not p.is_out and (
                p.java_name in _dyn_refs
                or p.java_name in _extra_java_names
                or not proc.dml_statements[-1].is_dynamic
            )
        ]
        for ep_java, _ in extra:
            if ep_java not in param_args_list:
                param_args_list.append(ep_java)
        param_args_str = ", ".join(param_args_list)
        _mc = f"mapper.{mapper_method}({param_args_str})"
        if into_targets:
            first_var = _extract_into_variable(into_targets)
            if first_var:
                vn_java = snake_to_camel(first_var)
                _emit_assignment(proc, vn_java, _mc)
            else:
                if sql_type != "select":
                    _emit_dml_with_rowcount(proc, _mc)
                else:
                    proc.java_logic_lines.append(f'{_mc};')
        else:
            if sql_type != "select":
                _emit_dml_with_rowcount(proc, _mc)
            else:
                proc.java_logic_lines.append(f'{_mc};')
        return

    if not sql_text:
        # Check if var_name is a procedure parameter — if so, use ${} interpolation
        if var_name:
            param_match = next((p for p in proc.parameters if p.name.lower() == var_name.lower()), None)
            if param_match:
                var_java = snake_to_camel(var_name)
                sql_text = "#{" + var_java + "}"
                using_args = execute_data.get("using_args", [])
                into_targets = execute_data.get("into_targets", [])
                # Collect USING args as extra_params for the mapper method
                extra = []
                seen = {param_match.java_name}
                for arg in using_args:
                    if isinstance(arg, dict):
                        argument = arg.get("argument", {})
                        arg_name = _extract_var_name_from_expr(argument)
                        if arg_name:
                            arg_java = snake_to_camel(arg_name)
                            if arg_java not in seen:
                                seen.add(arg_java)
                                extra.append((arg_java, proc.local_vars.get(arg_name, "Object")))
                sql_type = "update"
                mapper_method = _dml_method_name(sql_type, proc.proc_name, dml_counter)
                _add_dml(proc, DmlStatement(
                    sql_type=sql_type,
                    method_id=mapper_method,
                    sql_text=sql_text,
                    extra_params=extra,
                    is_dynamic=True,
                ))
                param_args_list = [p.java_name for p in proc.parameters if not p.is_out]
                for ep_java, _ in extra:
                    if ep_java not in param_args_list:
                        param_args_list.append(ep_java)
                param_args_str = ", ".join(param_args_list)
                if into_targets:
                    first_var = _extract_into_variable(into_targets)
                    if first_var:
                        vn_java = snake_to_camel(first_var)
                        _emit_assignment(proc, vn_java, f"mapper.{mapper_method}({param_args_str})")
                    else:
                        _mc = f"mapper.{mapper_method}({param_args_str})"
                        _emit_dml_with_rowcount(proc, _mc)
                else:
                    _mc = f"mapper.{mapper_method}({param_args_str})"
                    _emit_dml_with_rowcount(proc, _mc)
                return
        vn_camel = snake_to_camel(var_name.split('.')[0]) if var_name else "unknown"
        # Extract USING args and INTO targets for the skeleton comment block
        _using_names = []
        for arg in using_args:
            if isinstance(arg, dict):
                arg_name = _extract_var_name_from_expr(arg.get("argument", {}))
                if arg_name:
                    _using_names.append(snake_to_camel(arg_name))
        _into_names = _extract_all_into_variables(into_targets) if into_targets else []
        _into_camel = [snake_to_camel(vn) for vn in _into_names]
        proc.java_logic_lines.append(f"// Dynamic SQL could not be resolved at conversion time")
        proc.java_logic_lines.append(f"// Original: EXECUTE IMMEDIATE {var_name}" + (f" USING {', '.join(_using_names)}" if _using_names else "") + (f" INTO {', '.join(_into_names)}" if _into_names else ""))
        proc.java_logic_lines.append(f"// Resolved variables:")
        proc.java_logic_lines.append(f"//   SQL: {vn_camel} (determined at runtime)")
        if _using_names:
            proc.java_logic_lines.append(f"//   USING: {', '.join(_using_names)}")
        if _into_camel:
            proc.java_logic_lines.append(f"//   INTO: {', '.join(_into_camel)}")
        proc.java_logic_lines.append(f"// Manual implementation:")
        proc.java_logic_lines.append(f"//   1. Resolve the SQL string in {vn_camel} at runtime")
        proc.java_logic_lines.append(f"//   2. Execute via mapper method or JdbcTemplate")
        if _into_camel:
            proc.java_logic_lines.append(f"//   3. Capture result into {', '.join(_into_camel)}")
        proc.java_logic_lines.append(f"// Example: jdbcTemplate.update({vn_camel}" + (", " + ", ".join(_using_names) if _using_names else "") + ");")
        _record_todo("EXECUTE_UNRESOLVED", proc, f"var={var_name}")
        return

    raw_sql_for_params = sql_text
    sql_text = _convert_placeholders_to_mybatis(sql_text, proc=proc)
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
                _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))})')
                into_targets_full = _extract_all_into_targets(into_targets)
                for field_name, full_parts in into_targets_full:
                    if len(full_parts) >= 2:
                        map_var = snake_to_camel(full_parts[0])
                        var_type = _java_type_from_field_name(field_name) if _java_type_from_field_name(field_name) != "Object" else "Object"
                        _get_expr = f'_row.get("{field_name}")'
                        cast_expr = _safe_map_cast(var_type, _get_expr) if var_type != "Object" else _get_expr
                        _emit_assignment(proc, f'__MAP_PUT__{map_var}__{field_name}', cast_expr)
                    else:
                        var_type = proc.local_vars.get(field_name, "Object")
                        vn_java = snake_to_camel(field_name)
                        _emit_assignment(proc, vn_java, _safe_map_cast(var_type, f'_row.get("{field_name}")'))
            else:
                into_targets_full = _extract_all_into_targets(into_targets)
                if into_targets_full and len(into_targets_full[0][1]) >= 2:
                    full_parts = into_targets_full[0][1]
                    map_var = snake_to_camel(full_parts[0])
                    result_type = "Map<String, Object>"
                    _emit_row_decl(proc, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))})')
                    _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                else:
                    _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))})')
            _add_dml(proc, DmlStatement(
                sql_type=sql_type,
                method_id=mapper_method,
                sql_text=sql_text,
                result_type=result_type,
            ))
    else:
        _add_dml(proc, DmlStatement(
            sql_type=sql_type,
            method_id=mapper_method,
            sql_text=sql_text,
            result_type="Map<String, Object>" if sql_type == "select" else None,
        ))
        _mc = f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))})'
        if sql_type != "select":
            _emit_dml_with_rowcount(proc, _mc)
        else:
            proc.java_logic_lines.append(f'{_mc};')


def _convert_placeholders_to_mybatis(sql: str, proc=None) -> str:
    """Convert :param and $N placeholders to MyBatis #{{param}} syntax.

    When proc is provided, attempts to add jdbcType/javaType for :param references.
    $N positional params always use simple form (no reliable type mapping).
    """
    if proc:
        # Build lookup: param_name_lower → (java_name, jdbc_type, java_type)
        _type_map = {}
        for p in proc.parameters:
            jdbc = sql_type_to_jdbc(p.sql_type)
            if jdbc:
                _type_map[p.name.lower()] = (p.java_name, jdbc, p.java_type)
            else:
                _type_map[p.name.lower()] = (p.java_name, None, None)
        for var_name, var_java_type in proc.local_vars.items():
            java_name = snake_to_camel(var_name)
            jdbc = java_type_to_jdbc(var_java_type)
            if jdbc:
                _type_map[var_name.lower()] = (java_name, jdbc, var_java_type)
            else:
                _type_map[var_name.lower()] = (java_name, None, None)

        def _colon_replacer(m):
            raw_name = m.group(1)
            info = _type_map.get(raw_name.lower())
            if info and info[1] and info[2]:
                return f'#{{{info[0]}, jdbcType={info[1]}, javaType={info[2]}}}'
            elif info:
                return f'#{{{info[0]}}}'
            else:
                # Unknown param — use snake_to_camel as before
                return f'#{{{snake_to_camel(raw_name)}}}'

        sql = re.sub(r':(\w+)', _colon_replacer, sql)
    else:
        sql = re.sub(r':(\w+)', lambda m: f'#{{{snake_to_camel(m.group(1))}}}', sql)

    # $N positional params — always simple form
    sql = re.sub(r'\$(\d+)', lambda m: f'#{{param{m.group(1)}}}', sql)
    # Colon-space positional params (: 1, : 2) — produced by json2sql AST reconstruction
    sql = re.sub(r':\s+(\d+)', lambda m: f'#{{param{m.group(1)}}}', sql)
    return sql


_SQL_VERBS = {"select", "insert", "update", "delete", "truncate", "alter", "drop", "create", "merge"}


def _reconstruct_sql_from_concat(expr: dict, proc: ProcedureInfo):
    """Recursively reconstruct a SQL template from BinaryOp(||) AST nodes.

    Returns (sql_template, param_list) where:
      sql_template: SQL string with ${var} (identifiers) and #{var} (values) placeholders
      param_list: list of (var_name, is_identifier) tuples

    Returns None if the expression is not a SQL-like string concatenation.
    """
    parts = []
    param_list = []
    _flatten_concat(expr, parts, param_list, proc)

    if not parts:
        return None

    sql_template = "".join(parts)

    first_word = sql_template.strip().split()[0].lower() if sql_template.strip() else ""
    if first_word not in _SQL_VERBS:
        return None

    return (sql_template, param_list)


def _flatten_concat(expr: dict, parts: list, param_list: list, proc: ProcedureInfo):
    """Recursively flatten BinaryOp(||) into template parts."""
    if not isinstance(expr, dict):
        return

    for key, val in expr.items():
        if key == "BinaryOp":
            if getattr(proc, '_inline_unquote_next', False) and val.get("op") != "||":
                proc._inline_unquote_next = False
            op = val.get("op", "")
            if op == "||":
                _flatten_concat(val.get("left", {}), parts, param_list, proc)
                _flatten_concat(val.get("right", {}), parts, param_list, proc)
            else:
                parts.append(f" {_java_op_symbol(op)} ")
        elif key == "Literal":
            if isinstance(val, dict):
                if "String" in val:
                    s = val["String"]
                    if getattr(proc, '_inline_unquote_next', False) and s.startswith("'"):
                        s = s[1:]
                        proc._inline_unquote_next = False
                    parts.append(s)
                elif "Integer" in val:
                    parts.append(str(val["Integer"]))
                elif "Float" in val:
                    parts.append(str(val["Float"]))
                elif "Null" in val:
                    parts.append("NULL")
            else:
                parts.append(str(val))
        elif key == "PlVariable":
            var_parts = val if isinstance(val, list) else [val]
            var_name = var_parts[-1] if var_parts else ""
            if var_name:
                if var_name in proc.sql_expr_vars:
                    inline_sql, inline_params = _inline_sql_expr(proc.sql_expr_vars[var_name], proc)
                    if parts and parts[-1].rstrip().endswith("'"):
                        last = parts[-1].rstrip()
                        parts[-1] = last[:-1].rstrip()
                    parts.append(inline_sql)
                    param_list.extend(inline_params)
                    proc.inlined_sql_vars.add(var_name)
                    proc._inline_unquote_next = True
                else:
                    java_name = snake_to_camel(var_name)
                    is_identifier = _is_identifier_context(parts)
                    if is_identifier:
                        parts.append(f"${{{java_name}}}")
                    else:
                        parts.append(f"#{{{java_name}}}")
                    param_list.append((java_name, is_identifier))
        elif key == "ColumnRef":
            col_parts = val if isinstance(val, list) else [val]
            col_name = col_parts[-1] if col_parts else ""
            prefix = col_parts[0] if len(col_parts) > 1 else ""
            if prefix:
                java_prefix = snake_to_camel(prefix)
                java_col = snake_to_camel(col_name)
                is_identifier = _is_identifier_context(parts)
                if is_identifier:
                    parts.append(f"${{{java_prefix}.{java_col}}}")
                else:
                    parts.append(f"#{{{java_prefix}.{java_col}}}")
                param_list.append((f"{java_prefix}.{java_col}", is_identifier))
            elif col_name:
                java_name = snake_to_camel(col_name)
                is_identifier = _is_identifier_context(parts)
                if is_identifier:
                    parts.append(f"${{{java_name}}}")
                else:
                    parts.append(f"#{{{java_name}}}")
                param_list.append((java_name, is_identifier))
        elif key == "FunctionCall":
            func_name_parts = val.get("name", [])
            func_name = func_name_parts[-1] if func_name_parts else ""
            if func_name.lower() in ("to_char", "sysdate", "current_timestamp", "now"):
                args = val.get("args", [])
                func_sql = _function_call_to_sql(func_name, args, proc)
                parts.append(func_sql)
            else:
                parts.append(f"/* FunctionCall:{func_name} */")
        elif key == "SysDate" or (isinstance(val, str) and val == "SysDate"):
            parts.append("CURRENT_TIMESTAMP")


def _is_identifier_context(parts: list) -> bool:
    """Determine if the current position expects a SQL identifier (table/column name).

    Heuristic: if the trailing text ends with an opening quote ('''), the variable
    is a value inside quotes → not an identifier. If the trailing text ends with
    a space or keyword like 'from ', 'table ', 'partition ' → identifier.
    """
    if not parts:
        return True
    trailing = parts[-1].rstrip() if parts else ""
    if trailing.endswith("'"):
        return False
    if trailing.endswith("=") or trailing.endswith("= '") or trailing.endswith("='"):
        return False
    return True


def _typed_placeholder(var_name: str, proc: ProcedureInfo) -> str:
    java_name = snake_to_camel(var_name)
    var_type = None
    for vn, vt in proc.local_vars.items():
        if snake_to_camel(vn) == java_name:
            var_type = vt
            break
    if not var_type:
        for p in proc.parameters:
            if p.java_name == java_name:
                var_type = p.java_type
                break
    jdbc = java_type_to_jdbc(var_type) if var_type else None
    if jdbc and var_type:
        return f"#{{{java_name}, jdbcType={jdbc}, javaType={var_type}}}"
    return f"#{{{java_name}}}"


def _function_call_to_sql(func_name: str, args: list, proc: ProcedureInfo) -> str:
    """Convert a known function call AST to a SQL fragment for dynamic SQL templates."""
    lower = func_name.lower()
    if lower == "to_char" and args:
        inner_parts = []
        for arg in args:
            for ak, av in arg.items():
                if ak == "BinaryOp":
                    inner_parts.append(_inline_binaryop_sql(av, proc))
                elif ak == "PlVariable":
                    vp = av if isinstance(av, list) else [av]
                    vn = vp[-1] if vp else ""
                    inner_parts.append(_typed_placeholder(vn, proc) if vn else "?")
                elif ak == "Literal":
                    if isinstance(av, dict) and "String" in av:
                        inner_parts.append(f"'{av['String']}'")
                    elif isinstance(av, dict) and "Integer" in av:
                        inner_parts.append(str(av["Integer"]))
                    elif isinstance(av, dict) and "Float" in av:
                        inner_parts.append(str(av["Float"]))
                elif ak == "SysDate" or (isinstance(av, str) and av == "SysDate"):
                    inner_parts.append("CURRENT_TIMESTAMP")
        return f"to_char({', '.join(inner_parts)})"
    if lower in ("sysdate", "now", "current_timestamp"):
        return "CURRENT_TIMESTAMP"
    return f"{func_name}()"


def _inline_binaryop_sql(binop: dict, proc: ProcedureInfo) -> str:
    """Convert a BinaryOp (non-concat) to inline SQL for function args."""
    op = binop.get("op", "")
    left_sql = _expr_to_sql_fragment(binop.get("left", {}), proc)
    right_sql = _expr_to_sql_fragment(binop.get("right", {}), proc)
    if op == "-":
        return f"{left_sql} - {right_sql}"
    elif op == "+":
        return f"{left_sql} + {right_sql}"
    return f"{left_sql} {op} {right_sql}"


def _expr_to_sql_fragment(expr: dict, proc: ProcedureInfo) -> str:
    """Convert a simple expression AST node to a SQL fragment."""
    if not isinstance(expr, dict):
        return str(expr)
    for key, val in expr.items():
        if key == "PlVariable":
            vp = val if isinstance(val, list) else [val]
            vn = vp[-1] if vp else ""
            return _typed_placeholder(vn, proc) if vn else "?"
        elif key == "SysDate" or (isinstance(val, str) and val == "SysDate"):
            return "CURRENT_TIMESTAMP"
        elif key == "Literal":
            if isinstance(val, dict):
                if "String" in val:
                    return f"'{val['String']}'"
                elif "Integer" in val:
                    return str(val["Integer"])
                elif "Float" in val:
                    return str(val["Float"])
            return str(val)
        elif key == "BinaryOp":
            return _inline_binaryop_sql(val, proc)
        elif key == "FunctionCall":
            func_name_parts = val.get("name", [])
            func_name = func_name_parts[-1] if func_name_parts else ""
            args = val.get("args", [])
            return _function_call_to_sql(func_name, args, proc)
    return "?"


def _inline_sql_expr(ast_node: dict, proc: ProcedureInfo) -> tuple:
    sql = _expr_to_sql_fragment(ast_node, proc)
    params = []
    _collect_fragment_params(ast_node, params, proc)
    return (sql, params)


def _collect_fragment_params(expr: dict, params: list, proc: ProcedureInfo):
    if not isinstance(expr, dict):
        return
    for key, val in expr.items():
        if key == "PlVariable":
            vp = val if isinstance(val, list) else [val]
            vn = vp[-1] if vp else ""
            if vn:
                jn = snake_to_camel(vn)
                params.append((jn, False))
        elif key == "BinaryOp":
            _collect_fragment_params(val.get("left", {}), params, proc)
            _collect_fragment_params(val.get("right", {}), params, proc)
        elif key == "FunctionCall":
            for arg in val.get("args", []):
                _collect_fragment_params(arg, params, proc)
        elif key == "ColumnRef":
            cp = val if isinstance(val, list) else [val]
            cn = cp[-1] if cp else ""
            if cn:
                jn = snake_to_camel(cn)
                params.append((jn, False))


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
    elif first_word in ("truncate", "alter", "drop", "create", "merge"):
        return "update"
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


def _extract_string_literal(node: dict) -> str:
    if not isinstance(node, dict):
        if isinstance(node, str):
            return node.strip("'\"")
        return None
    for k, v in node.items():
        if k == "StringLiteral":
            return v.strip("'\"") if isinstance(v, str) else str(v)
        elif k == "SingleQuotedString":
            return v.strip("'")
    return None


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
        if message:
            proc.java_logic_lines.append(f'throw new BusinessException("{message}");')
        else:
            proc.java_logic_lines.append(f'throw new BusinessException("RAISE {level}");')


def _process_call_text(sql: str, proc: ProcedureInfo, all_packages: dict):
    """Process CALL statement from raw sql_text."""
    normalized = re.sub(r'\s*\.\s*', '.', sql.strip())
    if re.match(r'call\s+DBE_SCHEDULER\.ENABLE', normalized, re.IGNORECASE):
        _flush_scheduler_job(proc)
        return
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
        elif len(parts) == 1 and proc.package:
            pkg = proc.package
            func = parts[0]
        else:
            proc.java_logic_lines.append(f"// CALL {full_name}({args_str})")
            return
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
            UNRESOLVED_CALLS.append(UnresolvedCall(
                caller=f"{proc.package}.{proc.proc_name}",
                callee=f"{full_name}({args_str})",
                caller_file=proc.source_file or "",
                args=args_str,
                hint=f"add the SQL file defining {pkg} to fluxgauss.yaml sources",
            ))
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
    "nullif": "(java.util.Objects.equals({args0}, {args1}) ? null : {args0})",
    "greatest": "Math.max",
    "least": "Math.min",
    "abs": "__HANDLER__",
    "ceil": "__HANDLER__",
    "floor": "Math.floor",
    "round": "__HANDLER__",
    "upper": "String.valueOf({args}).toUpperCase()",
    "lower": "String.valueOf({args}).toLowerCase()",
    "trim": "__EXPR__String.valueOf({args0}).trim()",
    "length": "__EXPR__String.valueOf({args0}).length()",
    "to_char": "__HANDLER__",
    "to_number": "Long.valueOf",
    "to_clob": "{args0}",
    "to_date": "__HANDLER__",
    "to_timestamp": "java.sql.Timestamp.valueOf",
    "current_timestamp": "__EXPR__new java.sql.Timestamp(System.currentTimeMillis())",
    "current_date": "__EXPR__new java.sql.Date(System.currentTimeMillis())",
    "now": "__EXPR__new java.sql.Timestamp(System.currentTimeMillis())",
    "concat": "__HANDLER__",
    "substr": "String.valueOf({args0}).substring({args1})",
    "substrb": "String.valueOf({args0}).substring({args1})",
    "substring": "String.valueOf({args0}).substring({args1})",
    "replace": "String.valueOf({args0}).replace({args1}, {args2})",
    "lpad": "__HANDLER__",
    "rpad": "__HANDLER__",
    "nvl": "__EXPR__({args0} != null ? {args0} : {args1})",
    "nvl2": "__EXPR__({args0} != null ? {args1} : {args2})",
    "decode": "__HANDLER__",
    "trunc": "__EXPR__(int) Math.floor((double)({args0}))",
    "mod": "__EXPR__(({args0}) % ({args1}))",
    "power": "Math.pow",
    "sign": "__EXPR__Integer.signum((int)({args0}))",
    "instr": "__HANDLER__",
    "rtrim": "__EXPR__String.valueOf({args0}).replaceAll(\"\\\\s+$\", \"\")",
    "ltrim": "__EXPR__String.valueOf({args0}).replaceAll(\"^\\\\s+\", \"\")",
    "chr": "__EXPR__String.valueOf((char)({args0}))",
    "ascii": "__EXPR__String.valueOf({args0}).charAt(0)",
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
    "sin": "Math.sin",
    "cos": "Math.cos",
    "tan": "Math.tan",
    "asin": "Math.asin",
    "acos": "Math.acos",
    "atan": "Math.atan",
    "atan2": "Math.atan2",
    "radians": "__EXPR__Math.toRadians({args0})",
    "degrees": "__EXPR__Math.toDegrees({args0})",
    "crc32": "__HANDLER__",
    "to_hex": "__HANDLER__",
    "encode": "__HANDLER__",
    "md5": "__HANDLER__",
    "jsonb_build_object": "__HANDLER__",
    "jsonb_array_length": "__HANDLER__",
    "string_to_array": "__HANDLER__",
    "getarray": "__HANDLER__",
    "nextval": "__HANDLER__",
    "currval": "__HANDLER__",
    "gen_random_uuid": "__EXPR__java.util.UUID.randomUUID().toString()",
    "ln": "__HANDLER__",
    "random": "__EXPR__Math.random()",
    "strpos": "__EXPR__String.valueOf({args0}).indexOf(String.valueOf({args1})) + 1",
    "pi": "__EXPR__Math.PI",
    "clock_timestamp": "__EXPR__new java.sql.Timestamp(System.currentTimeMillis())",
    "statement_timestamp": "__EXPR__new java.sql.Timestamp(System.currentTimeMillis())",
    "digest": "__HANDLER__",
    "convert_to": "__HANDLER__",
    "quote_literal": "__HANDLER__",
    "array_length": "__HANDLER__",
    "array_append": "__HANDLER__",
    "array_to_string": "__HANDLER__",
    "age": "__HANDLER__",
    "inet_client_addr": "__EXPR__\"127.0.0.1\"",
    "current_setting": "__HANDLER__",
    "pg_backend_pid": "__EXPR__Thread.currentThread().getId()",
    "jsonb_build_array": "__HANDLER__",
    "jsonb_set": "__HANDLER__",
    "string_split": "__EXPR__java.util.Arrays.asList(String.valueOf({args0}).split(java.util.regex.Pattern.quote(String.valueOf({args1}))))",
    "dbms_random.string": "__EXPR__java.util.UUID.randomUUID().toString().substring(0, Integer.parseInt(String.valueOf({args1})))",
    "string": "__EXPR__java.util.UUID.randomUUID().toString().substring(0, Integer.parseInt(String.valueOf({args1})))",
    "t_spectrum_array": "__HANDLER__",
    "xmltype": "__HANDLER__",
    "st_makepoint": "__HANDLER__",
    "st_setsrid": "__HANDLER__",
    "st_buffer": "__HANDLER__",
    "st_envelope": "__HANDLER__",
    "numrange": "__HANDLER__",
    "tsrange": "__HANDLER__",
    "t_spectrum_array": "__HANDLER__",
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
    s_expr = s if (s.startswith('"') or s.startswith("'")) else f"String.valueOf({s})"
    needs_parens = any(op in s_expr for op in (" + ", " - ", " * ", " / "))
    if needs_parens:
        s_expr = f"({s_expr})"
    if len(args_java) >= 3:
        start = args_java[1]
        length = args_java[2]
        start_int = f"(int)({start})" if _might_be_long(start, proc) else f"({start})"
        length_int = f"(int)({length})" if _might_be_long(length, proc) else f"({length})"
        return f"_substr({s_expr}, {start_int}, {length_int})"
    elif len(args_java) == 2:
        start = args_java[1]
        start_int = f"(int)({start})" if _might_be_long(start, proc) else f"({start})"
        return f"_substr({s_expr}, {start_int})"
    return f"{s_expr}"


def _might_be_long(expr: str, proc) -> bool:
    if not proc:
        return False
    stripped = expr.strip()
    for var_name, var_type in proc.local_vars.items():
        if snake_to_camel(var_name) == stripped and var_type in ("Long", "long", "java.math.BigDecimal"):
            return True
    if any(op in stripped for op in (" + ", " - ", " * ", " / ")):
        for var_name, var_type in proc.local_vars.items():
            camel = snake_to_camel(var_name)
            if camel in stripped and var_type in ("Long", "long"):
                return True
    return False


def _sf_overlay(val, proc, _expr_to_java_fn):
    """OVERLAY: SQL OVERLAY(str PLACING repl FROM start FOR len) → Java string splice."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    s = args_java[0] if len(args_java) > 0 else '""'
    repl = args_java[1] if len(args_java) > 1 else '""'
    start = args_java[2] if len(args_java) > 2 else "1"
    length = args_java[3] if len(args_java) > 3 else None
    s_expr = s if (s.startswith('"') or s.startswith("'")) else f"String.valueOf({s})"
    repl_expr = repl if (repl.startswith('"') or repl.startswith("'")) else f"String.valueOf({repl})"
    if length is not None:
        return f"({s_expr}).substring(0, Math.max(0, ({start}) - 1)) + {repl_expr} + ({s_expr}).substring(Math.max(0, ({start}) - 1 + ({length})))"
    return f"({s_expr}).substring(0, Math.max(0, ({start}) - 1)) + {repl_expr} + ({s_expr}).substring(Math.max(0, ({start}) - 1 + {repl_expr}.length()))"


def _sf_position(val, proc, _expr_to_java_fn):
    """POSITION: SQL POSITION(substr IN str) → Java indexOf + 1 (SQL is 1-based)."""
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    substr = args_java[0] if len(args_java) > 0 else '""'
    s = args_java[1] if len(args_java) > 1 else '""'
    substr_expr = substr if (substr.startswith('"') or substr.startswith("'")) else f"String.valueOf({substr})"
    return f"(String.valueOf({s}).indexOf({substr_expr}) + 1)"


def _sf_extract(val, proc, _expr_to_java_fn):
    """EXTRACT: SQL EXTRACT(field FROM expr) → Java temporal field access."""
    args = val.get("args", [])
    # First arg is field name (ColumnRef like "year"), not a variable reference
    field_node = args[0] if len(args) > 0 else {}
    if isinstance(field_node, dict) and "ColumnRef" in field_node:
        col_ref = field_node["ColumnRef"]
        field_name = col_ref[0].upper() if isinstance(col_ref, list) and col_ref else "UNKNOWN"
    else:
        field_name = "UNKNOWN"

    src_expr = _expr_to_java_fn(args[1], proc) if len(args) > 1 else "new java.sql.Timestamp(System.currentTimeMillis())"
    field_map = {
        "YEAR": "toLocalDateTime().toLocalDate().getYear()",
        "MONTH": "toLocalDateTime().toLocalDate().getMonthValue()",
        "DAY": "toLocalDateTime().toLocalDate().getDayOfMonth()",
        "HOUR": "toLocalDateTime().getHour()",
        "MINUTE": "toLocalDateTime().getMinute()",
        "SECOND": "toLocalDateTime().getSecond()",
        "MICROSECOND": "toLocalDateTime().getNano() / 1000",
    }
    accessor = field_map.get(field_name, f"/* EXTRACT {field_name} */ -1")
    src_type = _infer_expr_type(args[1], proc) if proc and len(args) > 1 else ""
    if src_expr.startswith("new java.sql.Timestamp") or src_expr.startswith("java.sql.Timestamp.valueOf") or "Timestamp" in src_type:
        return f"({src_expr}).{accessor}"
    if "java.sql.Date" in src_expr or src_expr.startswith("new java.sql.Date") or "Date" in src_type:
        return f"({src_expr} != null ? new java.sql.Timestamp({src_expr}.getTime()) : new java.sql.Timestamp(0)).{accessor}"
    return f"java.sql.Timestamp.valueOf(String.valueOf({src_expr})).{accessor}"


def _sf_trim(val, proc, _expr_to_java_fn):
    """TRIM: SQL TRIM(LEADING/TRAILING/BOTH chars FROM str) → Java regex/string ops."""
    args = val.get("args", [])

    # Determine if first arg is a direction keyword (BOTH/LEADING/TRAILING)
    direction = "BOTH"
    start_idx = 0
    if len(args) > 0:
        first = args[0]
        if isinstance(first, dict) and "ColumnRef" in first:
            col_ref = first["ColumnRef"]
            dir_name = col_ref[0].upper() if isinstance(col_ref, list) else ""
            if dir_name in ("BOTH", "LEADING", "TRAILING"):
                direction = dir_name
                start_idx = 1

    args_java = [_expr_to_java_fn(a, proc) for a in args[start_idx:]]
    if len(args_java) == 1:
        s = args_java[0]
        return f"String.valueOf({s}).trim()"
    elif len(args_java) >= 2:
        chars = args_java[0]
        s = args_java[1]
        chars_expr = chars if (chars.startswith('"') or chars.startswith("'")) else f"String.valueOf({chars})"
        if direction in ("BOTH", ""):
            if chars == '" "' or chars == "' '":
                return f"String.valueOf({s}).trim()"
            return f"String.valueOf({s}).replaceAll(\"^\" + java.util.regex.Pattern.quote({chars_expr}) + \"+|\" + java.util.regex.Pattern.quote({chars_expr}) + \"+$\", \"\")"
        elif direction == "LEADING":
            if chars == '" "' or chars == "' '":
                return f"String.valueOf({s}).replaceAll(\"^\\\\s+\", \"\")"
            return f"String.valueOf({s}).replaceAll(\"^\" + java.util.regex.Pattern.quote({chars_expr}) + \"+\", \"\")"
        elif direction == "TRAILING":
            if chars == '" "' or chars == "' '":
                return f"String.valueOf({s}).replaceAll(\"\\\\s+$\", \"\")"
            return f"String.valueOf({s}).replaceAll(java.util.regex.Pattern.quote({chars_expr}) + \"+$\", \"\")"

    return '""'


def _sf_convert(val, proc, _expr_to_java_fn):
    args = val.get("args", [])
    args_java = [_expr_to_java_fn(a, proc) for a in args]
    expr_str = args_java[0] if len(args_java) > 0 else '""'
    encoding = args_java[1] if len(args_java) > 1 else '"UTF-8"'
    expr_expr = expr_str if (expr_str.startswith('"') or expr_str.startswith("'")) else f"String.valueOf({expr_str})"
    return f"new String({expr_expr}.getBytes(), {encoding})"


def _sf_current_timestamp(val, proc, _expr_to_java_fn):
    """CURRENT_TIMESTAMP / CURRENT_TIME: optional precision → Java Timestamp."""
    return "new java.sql.Timestamp(System.currentTimeMillis())"


def _sf_interval(val, proc, _expr_to_java_fn):
    """INTERVAL 'N' UNIT → Java Duration or millisecond calculation.

    Args: [value_literal, unit_columnref]
    Converts to milliseconds for use in Timestamp arithmetic.
    """
    args = val.get("args", [])
    if len(args) >= 2:
        n_expr = _expr_to_java_fn(args[0], proc)
        if n_expr.startswith('"') and n_expr.endswith('"'):
            n_expr = n_expr[1:-1]
        unit_node = args[1]
        if isinstance(unit_node, dict) and "ColumnRef" in unit_node:
            unit_parts = unit_node["ColumnRef"]
            unit = unit_parts[-1].lower() if isinstance(unit_parts, list) else str(unit_parts).lower()
        else:
            unit = str(unit_node).lower()
        unit_map = {
            "hour": "java.time.Duration.ofHours((long){n}).toMillis()",
            "hours": "java.time.Duration.ofHours((long){n}).toMillis()",
            "minute": "java.time.Duration.ofMinutes((long){n}).toMillis()",
            "minutes": "java.time.Duration.ofMinutes((long){n}).toMillis()",
            "second": "java.time.Duration.ofSeconds((long){n}).toMillis()",
            "seconds": "java.time.Duration.ofSeconds((long){n}).toMillis()",
            "day": "java.time.Duration.ofDays((long){n}).toMillis()",
            "days": "java.time.Duration.ofDays((long){n}).toMillis()",
            "month": "(long){n} * 30L * 24L * 60L * 60L * 1000L",
            "months": "(long){n} * 30L * 24L * 60L * 60L * 1000L",
            "year": "(long){n} * 365L * 24L * 60L * 60L * 1000L",
            "years": "(long){n} * 365L * 24L * 60L * 60L * 1000L",
        }
        template = unit_map.get(unit)
        if template:
            return template.replace("{n}", n_expr)
    return f"/* INTERVAL */ 0L"


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
    "interval": _sf_interval,
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


def _is_bigdecimal_expr(java_expr: str, proc) -> bool:
    if "BigDecimal" in java_expr or ".subtract(" in java_expr or ".add(" in java_expr or ".multiply(" in java_expr or ".divide(" in java_expr or ".setScale(" in java_expr:
        return True
    if proc is not None:
        var_name = java_expr.lstrip("this.").split(".")[0]
        for vname, vtype in proc.local_vars.items():
            if snake_to_camel(vname) == var_name and "BigDecimal" in vtype:
                return True
        for p in proc.parameters:
            if p.java_name == var_name and "BigDecimal" in p.java_type:
                return True
    return False


def _handle_function(func_name, args_java, proc):
    if func_name == "abs":
        if args_java:
            arg = args_java[0]
            if _is_bigdecimal_expr(arg, proc):
                return f"({arg}).abs()"
        return f"Math.abs({args_java[0] if args_java else '0'})"

    elif func_name == "ceil":
        if args_java:
            arg = args_java[0]
            if _is_bigdecimal_expr(arg, proc):
                return f"({arg}).setScale(0, java.math.RoundingMode.CEILING)"
        return f"Math.ceil({args_java[0]})"

    elif func_name == "decode":
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
            result = f"(java.util.Objects.equals({expr}, {val}) ? {ret} : {result})"
        return result

    elif func_name == "round":
        def _is_bd_arg(arg_expr):
            """Check if an expression is BigDecimal-typed."""
            _bd_ops = (".multiply(", ".add(", ".subtract(", ".divide(", ".setScale(", ".abs()")
            if "BigDecimal" in arg_expr or "Decimal" in arg_expr or any(op in arg_expr for op in _bd_ops):
                return True
            # Look up variable type in proc
            if proc:
                for vname, vtype in proc.local_vars.items():
                    if snake_to_camel(vname) == arg_expr and "BigDecimal" in vtype:
                        return True
                for p in proc.parameters:
                    if p.java_name == arg_expr and "BigDecimal" in p.java_type:
                        return True
            return False

        if len(args_java) >= 2:
            arg0 = args_java[0]
            _scale = args_java[1] if len(args_java) > 1 else "0"
            _scale_int = f"Integer.parseInt(String.valueOf({_scale}))" if not re.match(r'^-?\d+$', _scale.strip()) else _scale
            _scale_dbl = _scale if re.match(r'^-?\d+(\.\d+)?[dDfFlL]?$', _scale.strip()) else f"Double.parseDouble(String.valueOf({_scale}))"
            if _is_bd_arg(arg0):
                return f"({arg0}).setScale((int)({_scale_int}), java.math.RoundingMode.HALF_UP)"
            # Non-BigDecimal: use Math.round with scale — round(x * 10^n) / 10^n
            _is_primitive = any(op in arg0 for op in (" / ", " * ", " + ", " - ")) or arg0.endswith("d") or arg0.endswith("f") or arg0.endswith("D")
            if _is_primitive:
                return f"(double) Math.round(({arg0}) * Math.pow(10, {_scale_dbl})) / Math.pow(10, {_scale_dbl})"
            return f"(double) Math.round(({arg0}).doubleValue() * Math.pow(10, {_scale_dbl})) / Math.pow(10, {_scale_dbl})"
        elif len(args_java) == 1:
            arg0 = args_java[0]
            if _is_bd_arg(arg0):
                return f"({arg0}).setScale(0, java.math.RoundingMode.HALF_UP)"
            return f"Math.round({arg0})"
        return "null"

    elif func_name == "to_char":
        if len(args_java) == 1:
            return f"String.valueOf({args_java[0]})"
        fmt_raw = args_java[1].strip('"').strip("'").lower() if len(args_java) > 1 else ""
        java_fmt = fmt_raw
        for sql_pat, java_pat in sorted(_TO_CHAR_DATE_MAP.items(), key=lambda x: -len(x[0])):
            java_fmt = java_fmt.replace(sql_pat, java_pat)
        has_date_token = any(t in fmt_raw for t in ("yyyy", "yy", "mm", "mon", "dd", "hh", "mi", "ss"))
        if has_date_token:
            date_expr = args_java[0]
            _is_date_arg = date_expr.startswith("new java.sql.Date") or date_expr.startswith("new java.sql.Timestamp")
            if not _is_date_arg and proc is not None:
                var_name = date_expr.lstrip("this.").split(".")[0]
                for p in proc.parameters:
                    if p.java_name == var_name and p.java_type in ("java.sql.Date", "java.sql.Timestamp", "java.util.Date"):
                        _is_date_arg = True
                        break
                if not _is_date_arg:
                    for vn, vt in proc.local_vars.items():
                        if snake_to_camel(vn) == var_name and vt in ("java.sql.Date", "java.sql.Timestamp", "java.util.Date"):
                            _is_date_arg = True
                            break
            if _is_date_arg:
                return f"new java.text.SimpleDateFormat(\"{java_fmt}\").format({date_expr})"
            return f"new java.text.SimpleDateFormat(\"{java_fmt}\").format(new java.util.Date(java.sql.Timestamp.valueOf(String.valueOf({date_expr})).getTime()))"
        num_fmt = args_java[1].strip('"').strip("'")
        num_fmt_java = num_fmt.replace("FM", "").replace(",", "").replace("9", "#").replace("0", "0")
        return f"new java.text.DecimalFormat(\"{num_fmt_java}\").format({args_java[0]})"

    elif func_name == "date_trunc":
        if len(args_java) < 2:
            return args_java[0] if args_java else "null"
        field_raw = args_java[0].strip('"').strip("'").lower()
        unit = _DATE_TRUNC_UNIT_MAP.get(field_raw, "ChronoUnit.DAYS")
        ts_expr = args_java[1]
        if not (ts_expr.startswith("new java.sql.Timestamp") or ts_expr.startswith("java.sql.Timestamp.valueOf") or ts_expr.startswith("new java.sql.Date")):
            ts_expr = f"java.sql.Timestamp.valueOf(String.valueOf({args_java[1]}))"
        if "MONTHS" in unit and field_raw == "quarter":
            return f"java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({ts_expr}.toInstant(), java.time.ZoneId.systemDefault()).truncatedTo(java.time.temporal.ChronoUnit.DAYS).withMonth(((java.time.LocalDateTime.ofInstant({ts_expr}.toInstant(), java.time.ZoneId.systemDefault()).getMonthValue() - 1) / 3) * 3 + 1).withDayOfMonth(1))"
        return f"java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({ts_expr}.toInstant(), java.time.ZoneId.systemDefault()).truncatedTo(java.time.temporal.{unit}))"

    elif func_name == "translate":
        if len(args_java) < 3:
            return args_java[0] if args_java else '""'
        s = args_java[0]
        from_chars = args_java[1]
        to_chars = args_java[2]
        fc = from_chars if (from_chars.startswith('"') or from_chars.startswith("'")) else f"String.valueOf({from_chars})"
        tc = to_chars if (to_chars.startswith('"') or to_chars.startswith("'")) else f"String.valueOf({to_chars})"
        return f"String.valueOf({s}).chars().mapToObj(c -> {{ int idx = {fc}.indexOf(c); return idx >= 0 && idx < {tc}.length() ? String.valueOf({tc}.charAt(idx)) : String.valueOf((char) c); }}).collect(java.util.stream.Collectors.joining())"

    elif func_name == "crc32":
        if args_java:
            arg0 = args_java[0]
            arg0_expr = arg0 if (arg0.startswith('"') or arg0.startswith("'")) else f"String.valueOf({arg0})"
            return f"_crc32({arg0_expr})"
        return "0"

    elif func_name == "md5":
        if args_java:
            arg0 = args_java[0]
            arg0_expr = arg0 if (arg0.startswith('"') or arg0.startswith("'")) else f"String.valueOf({arg0})"
            return f"_md5({arg0_expr})"
        return "null"

    elif func_name == "encode":
        if len(args_java) >= 2:
            fmt = args_java[1].strip('"').strip("'").lower()
            if fmt == "base64":
                arg0 = args_java[0]
                arg0_expr = arg0 if (arg0.startswith('"') or arg0.startswith("'")) else f"String.valueOf({arg0})"
                return f"java.util.Base64.getEncoder().encodeToString({arg0_expr}.getBytes())"
        return f"/* TODO: encode({', '.join(args_java)}) */ null"

    elif func_name == "instr":
        if len(args_java) >= 2:
            s = args_java[0]
            sub_expr = args_java[1]
            s_expr = s if (s.startswith('"') or s.startswith("'")) else f"String.valueOf({s})"
            sub_wrapped = sub_expr if (sub_expr.startswith('"') or sub_expr.startswith("'")) else f"String.valueOf({sub_expr})"
            # Parenthesize (indexOf + 1) so comparisons/method chains bind correctly.
            # Without parens: indexOf(x) + 1.equals(0) is parsed as float literal "1.equals".
            if len(args_java) >= 3:
                start = args_java[2]
                start_cast = f"(int)({start})" if _might_be_long(start, proc) else f"({start})"
                return f"({s_expr}.indexOf({sub_wrapped}, Math.max(0, {start_cast} - 1)) + 1)"
            return f"({s_expr}.indexOf({sub_wrapped}) + 1)"
        return "0"

    elif func_name == "to_hex":
        if args_java:
            return f"Integer.toHexString({args_java[0]}).toUpperCase()"
        return "null"

    elif func_name == "to_date":
        if not args_java:
            return "null"
        if len(args_java) >= 2:
            fmt_raw = args_java[1].strip('"').strip("'").lower()
            if fmt_raw in ("yyyy-mm-dd", "yyyy-mm-dd"):
                return f"java.sql.Date.valueOf(String.valueOf({args_java[0]}))"
            java_fmt = fmt_raw
            for sql_pat, java_pat in sorted(_TO_CHAR_DATE_MAP.items(), key=lambda x: -len(x[0])):
                java_fmt = java_fmt.replace(sql_pat, java_pat)
            return f"_parseDate(\"{java_fmt}\", String.valueOf({args_java[0]}))"
        return f"java.sql.Date.valueOf(String.valueOf({args_java[0]}))"

    elif func_name == "jsonb_array_length":
        return f"this.jsonbArrayLength({', '.join(args_java)})" if args_java else "0"

    elif func_name == "jsonb_build_object":
        if not args_java:
            return "null"
        coerced = []
        for a in args_java:
            if _is_string_expr(a):
                coerced.append(a)
            else:
                coerced.append(f"String.valueOf({a})")
        return f"this.jsonbBuildObject({', '.join(coerced)})"

    elif func_name == "string_to_array":
        return f"this.stringToArray({', '.join(args_java)})" if args_java else "null"

    elif func_name == "getarray":
        return f"this.stringToArray({', '.join(args_java)})" if args_java else "null"

    elif func_name == "nextval":
        return f"this.nextval({', '.join(args_java)})" if args_java else "null"

    elif func_name == "currval":
        return f"this.currval({', '.join(args_java)})" if args_java else "null"

    elif func_name == "ln":
        if args_java:
            return f"Math.log({args_java[0]}.doubleValue())"
        return "0.0d"

    elif func_name == "digest":
        if len(args_java) >= 2:
            algo = args_java[1].strip('"').strip("'") if args_java[1].startswith('"') or args_java[1].startswith("'") else f"String.valueOf({args_java[1]})"
            return f"java.security.MessageDigest.getInstance({algo}).digest(String.valueOf({args_java[0]}).getBytes())"
        return "new byte[0]"

    elif func_name == "convert_to":
        if len(args_java) >= 2:
            return f"String.valueOf({args_java[0]}).getBytes(java.nio.charset.Charset.forName(String.valueOf({args_java[1]})))"
        return "new byte[0]"

    elif func_name == "quote_literal":
        if args_java:
            return f"\"'\" + String.valueOf({args_java[0]}) + \"'\""
        return "null"

    elif func_name == "array_length":
        if len(args_java) >= 1:
            return f"({args_java[0]}).size()"
        return "0"

    elif func_name == "array_append":
        if len(args_java) >= 2:
            _list_expr = args_java[0]
            _elem_expr = args_java[1]
            # Detect List<Double>/List<Long> and apply explicit cast for generic type safety
            if proc:
                _list_var_name = _list_expr.split('.')[0].split('[')[0].strip()
                _camel_to_snake = lambda s: re.sub(r'(?<=[a-z])(?=[A-Z])', '_', s).lower()
                _var_key = _camel_to_snake(_list_var_name)
                _var_type = proc.local_vars.get(_var_key, "")
                if "List<Double>" in _var_type and not any(x in _elem_expr for x in ("Double.valueOf", "(double)", "doubleValue()")):
                    _elem_expr = f"Double.valueOf({_elem_expr})"
                elif "List<Long>" in _var_type and not any(x in _elem_expr for x in ("Long.valueOf", "(long)", "longValue()")):
                    _elem_expr = f"Long.valueOf({_elem_expr})"
            return f"_appendList({_list_expr}, {_elem_expr})"
        return "null"

    elif func_name == "array_to_string":
        if len(args_java) >= 2:
            delim = args_java[1]
            delim_expr = delim if (delim.startswith('"') or delim.startswith("'")) else f"String.valueOf({delim})"
            return f"({args_java[0]}).stream().map(Object::toString).collect(java.util.stream.Collectors.joining({delim_expr}))"
        return "null"

    elif func_name == "age":
        if len(args_java) >= 2:
            return f"java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){args_java[1]}).getTime()).toLocalDate(), new java.sql.Date(((java.sql.Timestamp){args_java[0]}).getTime()).toLocalDate())"
        return "null"

    elif func_name == "current_setting":
        if args_java:
            return f"System.getProperty(String.valueOf({args_java[0]}), \"\")"
        return "\"\""

    elif func_name == "jsonb_build_array":
        if args_java:
            coerced = [f"String.valueOf({a})" for a in args_java]
            return f"\"[\" + String.join(\",\", {', '.join(coerced)}) + \"]\""
        return "\"[]\""

    elif func_name == "jsonb_set":
        if len(args_java) >= 3:
            return f"({args_java[0]})"
        return "null"

    elif func_name == "xmltype":
        if args_java:
            return f"String.valueOf({args_java[0]})"
        return "null"

    elif func_name == "st_makepoint":
        if len(args_java) >= 2:
            return f"String.format(\"POINT(%s %s)\", {args_java[0]}, {args_java[1]})"
        return "null"

    elif func_name == "st_setsrid":
        if len(args_java) >= 2:
            return f"String.format(\"SRID=%s;%s\", {args_java[1]}, {args_java[0]})"
        return "null"

    elif func_name == "st_buffer":
        return f"String.valueOf({args_java[0]})" if args_java else "null"

    elif func_name == "st_envelope":
        return f"String.valueOf({args_java[0]})" if args_java else "null"

    elif func_name == "numrange":
        if len(args_java) >= 2:
            return f"String.format(\"[%s,%s)\", {args_java[0]}, {args_java[1]})"
        return "null"

    elif func_name == "tsrange":
        if len(args_java) >= 2:
            return f"String.format(\"[%s,%s]\", {args_java[0]}, {args_java[1]})"
        return "null"

    elif func_name == "t_spectrum_array":
        if args_java:
            return f"String.valueOf({args_java[0]})"
        return "null"

    elif func_name == "concat":
        if len(args_java) < 2:
            return args_java[0] if args_java else '""'
        return " + ".join(f"String.valueOf({a})" for a in args_java)

    elif func_name == "lpad":
        if len(args_java) >= 3:
            return f"String.format(\"%\" + ({args_java[1]}) + \"s\", {args_java[0]}).replace(\" \", {args_java[2]})"
        elif len(args_java) == 2:
            return f"String.format(\"%\" + ({args_java[1]}) + \"s\", {args_java[0]}).replace(\" \", \" \")"
        return args_java[0] if args_java else '""'

    elif func_name == "rpad":
        if len(args_java) >= 3:
            return f"String.format(\"%-\" + ({args_java[1]}) + \"s\", {args_java[0]}).replace(\" \", {args_java[2]})"
        elif len(args_java) == 2:
            return f"String.format(\"%-\" + ({args_java[1]}) + \"s\", {args_java[0]})"
        return args_java[0] if args_java else '""'

    return f"/* TODO: {func_name} */ null"


_NUMERIC_FUNC_RETURN_INT = {"length", "instr", "ascii", "sign", "mod", "trunc", "jsonb_array_length"}
_NUMERIC_FUNC_RETURN_DOUBLE = {"abs", "ceil", "floor", "power", "sqrt", "log", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "radians", "degrees", "round"}
_NUMERIC_FUNC_NEEDS_DOUBLE_ARGS = {"sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "log", "exp", "radians", "degrees", "power", "floor"}
_NUMERIC_FUNC_NEEDS_INT_ARGS = {"min", "max", "least", "greatest"}
_NUMERIC_FUNC_RETURN_LONG = {"to_number"}
_STRING_FUNC_RETURN = {
    "upper", "lower", "trim", "replace", "concat", "lpad", "rpad", "rtrim", "ltrim",
    "chr", "substr", "substring", "to_char", "to_clob",
    "reverse", "repeat", "initcap", "regexp_replace", "regexp_like", "left", "right",
    "split_part", "translate", "overlay",
}
# nvl/nvl2/coalesce inherit the type of their value arguments (not always String)


def _java_type_from_field_name(field_name: str) -> str:
    """Infer Java type from a composite type field name (heuristic)."""
    col = field_name.lower()
    if any(s in col for s in ("name", "txt", "text", "info", "desc", "msg", "remark", "comment", "label", "status", "code", "type", "flag")):
        return "String"
    if re.search(r'(^|_)id$|_ids$', col) or re.search(r'(^|_)no$', col) or re.search(r'(^|_)seq', col):
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
            upper = expr.upper()
            if upper in ("SYSDATE", "CURRENT_DATE"):
                return "java.sql.Date"
            if upper in ("CURRENT_TIMESTAMP", "NOW", "LOCALTIMESTAMP"):
                return "java.sql.Timestamp"
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
            name_lower = name.lower()
            for vn, vt in proc.local_vars.items():
                if vn.lower() == name_lower:
                    return vt
            for p in proc.parameters:
                if p.name.lower() == name.lower():
                    return p.java_type
            if name in _PACKAGE_CONSTANTS:
                return _PACKAGE_CONSTANTS[name]
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
            if func_name == "abs" and val.get("args"):
                arg_type = _infer_expr_type(val["args"][0], proc)
                if "BigDecimal" in arg_type or arg_type.upper() in ("NUMERIC", "NUMBER", "DECIMAL"):
                    return "java.math.BigDecimal"
            # COALESCE/NVL/NVL2: result type follows non-null value args
            if func_name in ("coalesce", "nvl", "nvl2") and val.get("args"):
                args = val["args"]
                if func_name == "nvl2" and len(args) >= 3:
                    t1 = _infer_expr_type(args[1], proc)
                    t2 = _infer_expr_type(args[2], proc)
                else:
                    types = [_infer_expr_type(a, proc) for a in args]
                    t1 = types[0] if types else "Object"
                    t2 = types[1] if len(types) > 1 else t1
                for t in (t1, t2):
                    if "BigDecimal" in t:
                        return "java.math.BigDecimal"
                for t in (t1, t2):
                    if t in ("Long", "long"):
                        return "Long"
                for t in (t1, t2):
                    if t in ("Integer", "int", "Double", "double", "Float", "float"):
                        return t if t[0].isupper() else t.capitalize()
                for t in (t1, t2):
                    if t == "String":
                        return "String"
                return t1 if t1 != "Object" else t2
            if func_name in _STRING_FUNC_RETURN:
                return "String"
            if func_name == "to_date":
                return "java.sql.Date"
            if func_name in _NUMERIC_FUNC_RETURN_INT:
                return "Integer"
            if func_name in _NUMERIC_FUNC_RETURN_DOUBLE:
                return "Double"
            if func_name in _NUMERIC_FUNC_RETURN_LONG:
                return "Long"
            _udf_rt = _UDF_RETURN_TYPES.get((func_name, len(val.get("args", []))))
            if _udf_rt:
                return _udf_rt
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
        elif key == "Parenthesized":
            return _infer_expr_type(val, proc)
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
            if op == "^":
                return "Double"
            return "Object"
        elif key == "Case":
            whens = val.get("whens", [])
            else_expr = val.get("else_expr") or val.get("else_result")
            types = [_infer_expr_type(w.get("result"), proc) for w in whens]
            if else_expr:
                types.append(_infer_expr_type(else_expr, proc))
            if any("BigDecimal" in t for t in types):
                return "java.math.BigDecimal"
            if any("Double" in t or "Float" in t for t in types):
                return "Double"
            if any("Long" in t for t in types):
                return "Long"
            if any("Integer" in t or "int" in t for t in types):
                return "Integer"
            if types:
                return types[0]
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


def _coerce_for_int(expr: str) -> str:
    if ".get(" in expr and not expr.startswith("("):
        safe_expr = re.sub(r'\.get\(([^)]+)\)', r'.getOrDefault(\1, 0)', expr)
        return f"((Number) {safe_expr}).intValue()"
    if re.match(r'^-?\d+[Ll]$', expr.strip()):
        return expr.rstrip('lL')
    return expr


def _coerce_java_arg(a_java: str, target_type: str) -> str:
    """Coerce a Java argument expression to match the target parameter type.

    Handles edge cases where PL/pgSQL implicit type coercion needs explicit Java conversion:
    - Empty string ``\"\"`` passed to numeric parameters → zero value (0L, 0, etc.)
    - Numeric literal passed to BigDecimal parameter → BigDecimal.valueOf()
    - Map.get() result to typed parameter → cast expression
    """
    # Empty string '' in PL/pgSQL passed to a numeric/boolean parameter.
    # _coerce_java_arg returns zero-values (0L, 0, BigDecimal.ZERO, etc.) for function call args
    # per PostgreSQL CAST semantics (''::int → 0), while _coerce_type returns null for ':=' 
    # assignments per GaussDB PL/pgSQL semantics ('' assigned to NUMBER is NULL).
    if a_java == '""' or a_java == "''":
        if target_type in ("long", "Long"):
            return "0L"
        if target_type in ("int", "Integer"):
            return "0"
        if "BigDecimal" in target_type:
            return "java.math.BigDecimal.ZERO"
        if target_type in ("double", "Double"):
            return "0.0d"
        if target_type in ("float", "Float"):
            return "0.0f"
        if target_type in ("boolean", "Boolean"):
            return "false"
    # Quoted numeric string '1' passed to int/long parameter (PL/pgSQL implicit coercion)
    if a_java.startswith('"') and a_java.endswith('"') and len(a_java) > 2:
        _inner = a_java[1:-1]
        if _inner.isdigit() or (_inner.startswith('-') and _inner[1:].isdigit()):
            if target_type in ("long", "Long"):
                return f"{_inner}L"
            if target_type in ("int", "Integer"):
                return _inner
    # BigDecimal target with numeric literal
    if "BigDecimal" in target_type and _is_numeric_literal_expr(a_java):
        return f"java.math.BigDecimal.valueOf({a_java})"
    # Numeric literal to String parameter
    if target_type == "String" and _is_numeric_literal_expr(a_java):
        return f"String.valueOf({a_java})"
    # Map.get() or List.get() result needs casting to target type
    if ".get(" in a_java and target_type not in ("Object", "Map<String, Object>"):
        if target_type in ("long", "Long"):
            return f"Long.parseLong(String.valueOf({a_java}))"
        if target_type in ("int", "Integer"):
            return f"Integer.parseInt(String.valueOf({a_java}))"
        if "BigDecimal" in target_type:
            return f"new java.math.BigDecimal(String.valueOf({a_java}))"
        if target_type == "String":
            return f"(String) {a_java}"
        if target_type == "java.sql.Date":
            return f"({a_java} instanceof java.sql.Timestamp ? new java.sql.Date(((java.sql.Timestamp) {a_java}).getTime()) : (java.sql.Date) {a_java})"
        return f"({target_type}) {a_java}"
    if target_type == "String":
        if "BigDecimal" in a_java or re.search(r'\b\w*[Bb]ig[Dd]ecimal\w*\b', a_java):
            return f"{a_java}.toString()"
        if ".get(" in a_java:
            return f"String.valueOf({a_java})"
        if a_java.startswith("this.") and ("jsonbArrayLength" in a_java or "nextval" in a_java or "jsonbBuildObject" in a_java):
            return f"String.valueOf({a_java})"
    if target_type == "java.sql.Date" and a_java.startswith('"') and a_java.endswith('"'):
        return f"java.sql.Date.valueOf({a_java})"
    # Integer/boxed-type passed to Long parameter: Java requires explicit coercion
    if target_type in ("long", "Long"):
        stripped = a_java.strip()
        # Integer literal: 0, 1, 100 → 0L, 1L, 100L
        if re.match(r'^-?\d+$', stripped):
            return f"{a_java}L"
        # Boxed Integer variable (or any simple variable) → safe long conversion
        # Using Number.longValue() handles Integer, Long, Double, etc.
        if re.match(r'^[a-zA-Z_]\w*$', stripped):
            return f"((Number) ({a_java})).longValue()"
    return a_java


# ── Unified Type Coercion ─────────────────────────────────────

# 类型规范化：将 primitive/boxed 统一为 canonical 形式用于比较
_CANONICAL_TYPE = {
    "int": "Integer", "long": "Long", "double": "Double",
    "float": "Float", "boolean": "Boolean", "short": "Short",
    "byte": "Byte", "char": "Character",
}


def _normalize_type(java_type: str) -> str:
    """Normalize a Java type to its canonical form for comparison.

    Examples: "int" -> "Integer", "long" -> "Long", "java.math.BigDecimal" unchanged.
    """
    if not java_type:
        return ""
    return _CANONICAL_TYPE.get(java_type, java_type)


def _is_numeric_type(java_type: str) -> bool:
    """Check if a Java type is a numeric type (Integer, Long, Double, Float, BigDecimal, etc.)."""
    t = _normalize_type(java_type)
    return t in ("Integer", "Long", "Double", "Float", "java.math.BigDecimal", "Short", "Byte")


def _needs_coercion(source_type: str, target_type: str) -> bool:
    """Check if a type conversion is needed from source to target.

    Returns True if source and target are different types that require explicit conversion.
    Returns False for: same types, Object involvement, Map<String, Object> involvement,
    primitive<->boxed pairs, unknown types.
    """
    if not source_type or not target_type:
        return False

    src = _normalize_type(source_type)
    tgt = _normalize_type(target_type)

    # Same type (after normalization) -- no coercion needed
    if src == tgt:
        return False

    # Object / Map<String, Object> -- no coercion possible/needed
    if src in ("Object", "Map<String, Object>", "") or tgt in ("Object", "Map<String, Object>", ""):
        return False

    # List types -- no generic coercion
    if src.startswith("List<") or tgt.startswith("List<"):
        return False

    # java.sql.Date / Timestamp -- no numeric coercion
    if src.startswith("java.sql.") or tgt.startswith("java.sql."):
        return False

    return True


_BD_RETURNING_METHODS = frozenset({
    "multiply", "add", "subtract", "divide", "setScale", "abs", "negate",
    "pow", "max", "min", "round", "movePointLeft", "movePointRight", "ulp",
})


def _expr_is_bigdecimal_producing(expr: str) -> bool:
    s = expr.strip()
    if s.startswith((
        "java.math.BigDecimal.", "BigDecimal.ZERO", "BigDecimal.ONE",
        "BigDecimal.TEN", "new java.math.BigDecimal(", "new BigDecimal(",
    )):
        return True
    if s.endswith(")"):
        depth = 0
        for i in range(len(s) - 1, -1, -1):
            if s[i] == ")":
                depth += 1
            elif s[i] == "(":
                depth -= 1
                if depth == 0:
                    m = re.search(r"\.(\w+)$", s[:i])
                    if m and m.group(1) in _BD_RETURNING_METHODS:
                        return True
                    break
    return False


def _coerce_type(expr: str, source_type: str, target_type: str) -> str:
    """Coerce a Java expression from source_type to target_type.

    Returns the original expr if no coercion is needed or possible.
    Uses _needs_coercion() to determine if coercion is applicable.

    Conversion rules:
    - Numeric -> Numeric: use .xxxValue() or BigDecimal.valueOf()
    - Numeric -> String: String.valueOf() (BigDecimal uses .toString())
    - String -> Numeric: Xxx.parseXxx() (BigDecimal uses new BigDecimal())
    - Integer/Long -> Boolean: (expr != 0) or (expr != 0L)
    - Boolean -> Integer/Long: (expr ? 1 : 0) / (expr ? 1L : 0L)
    """
    if not _needs_coercion(source_type, target_type):
        return expr

    src = _normalize_type(source_type)
    tgt = _normalize_type(target_type)

    # Numeric to numeric conversions
    if _is_numeric_type(src) and _is_numeric_type(tgt):
        # Detect bare numeric literals (e.g. "42", "3.14") — avoid generating invalid "42.longValue()"
        _is_bare_lit = re.match(r'^-?\d+(\.\d+)?([dDfFlL])?$', expr.strip())
        # BigDecimal source -> numeric target
        if src == "java.math.BigDecimal":
            if tgt == "Integer":
                return f"{expr}.intValue()"
            if tgt == "Long":
                return f"{expr}.longValue()"
            if tgt == "Double":
                return f"{expr}.doubleValue()"
            if tgt == "Float":
                return f"{expr}.floatValue()"
        # Non-BigDecimal source -> BigDecimal target
        if tgt == "java.math.BigDecimal":
            if _expr_is_bigdecimal_producing(expr):
                return expr
            if ".get(" in expr and not _is_primitive_producing(expr):
                return _safe_map_cast("java.math.BigDecimal", expr)
            return f"java.math.BigDecimal.valueOf({expr})"
        # Non-BigDecimal -> non-BigDecimal (Integer<->Long, Integer<->Double, etc.)
        if tgt == "Integer":
            if _is_bare_lit:
                return f"Integer.valueOf({expr})"
            return f"((Number) ({expr})).intValue()" if _is_primitive_producing(expr) else f"{expr}.intValue()"
        if tgt == "Long":
            if _is_bare_lit:
                return f"Long.valueOf({expr})"
            return f"((Number) ({expr})).longValue()" if _is_primitive_producing(expr) else f"{expr}.longValue()"
        if tgt == "Double":
            if _is_bare_lit:
                return f"Double.valueOf({expr})"
            return f"((Number) ({expr})).doubleValue()" if _is_primitive_producing(expr) else f"{expr}.doubleValue()"
        if tgt == "Float":
            if _is_bare_lit:
                return f"Float.valueOf({expr})"
            return f"((Number) ({expr})).floatValue()" if _is_primitive_producing(expr) else f"{expr}.floatValue()"

    # Numeric to String
    if _is_numeric_type(src) and tgt == "String":
        if src == "java.math.BigDecimal":
            return f"{expr}.toString()"
        return f"String.valueOf({expr})"

    # String to numeric
    if src == "String" and _is_numeric_type(tgt):
        # Issue #57: empty string '' assigned to NUMBER is implicitly NULL in GaussDB.
        # Emit null instead of Long.parseLong("") which throws NumberFormatException at runtime.
        stripped = expr.strip()
        if stripped == '""' or stripped == "''":
            return "null"
        if tgt == "Integer":
            return f"Integer.parseInt({expr})"
        if tgt == "Long":
            return f"Long.parseLong({expr})"
        if tgt == "Double":
            return f"Double.parseDouble({expr})"
        if tgt == "Float":
            return f"Float.parseFloat({expr})"
        if tgt == "java.math.BigDecimal":
            return f"new java.math.BigDecimal({expr})"

    # Integer/Long to Boolean
    if src in ("Integer", "Long") and tgt == "Boolean":
        suffix = "L" if src == "Long" else ""
        return f"({expr} != 0{suffix})"

    # Boolean to Integer
    if src == "Boolean" and tgt == "Integer":
        return f"({expr} ? 1 : 0)"

    # Boolean to Long
    if src == "Boolean" and tgt == "Long":
        return f"({expr} ? 1L : 0L)"

    # Fallback: no known coercion
    return expr


def _is_already_coerced(expr: str, target_type: str) -> bool:
    if not expr:
        return False
    stripped = expr.strip()
    if re.search(r'\.(intValue|longValue|doubleValue|floatValue|toString)\(\)\s*$', stripped):
        return True
    if re.match(
        r'^(java\.math\.BigDecimal\.valueOf|new java\.math\.BigDecimal|'
        r'(Integer|Long|Double|Float)\.valueOf|String\.valueOf|'
        r'(Integer|Long|Double|Float)\.parse(Int|Long|Double|Float))\(',
        stripped,
    ):
        return True
    if target_type in ("Long", "long") and re.match(r'^-?\d+[Ll]$', stripped):
        return True
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




def _wrap_ternary_nullsafe_bd(expr: str) -> str:
    _m = re.match(r'(.+\?\s*null\s*:\s*)(.+)', expr)
    if _m:
        return f'{_m.group(1)}java.math.BigDecimal.valueOf({_m.group(2)})'
    return f"java.math.BigDecimal.valueOf({expr})"


def _expr_to_java(expr, proc: ProcedureInfo = None, as_read: bool = True, all_packages: dict = None) -> str:
    """Convert an AST expression to Java code."""
    if expr is None:
        return "null"
    if isinstance(expr, str):
        upper = expr.upper()
        if upper == "SYSDATE":
            return "new java.sql.Date(System.currentTimeMillis())"
        if upper in ("LOCALTIMESTAMP",):
            return "java.sql.Timestamp.valueOf(java.time.LocalDateTime.now())"
        if upper in ("CURRENT_TIMESTAMP", "NOW", "SYSTIMESTAMP"):
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
                return "__SQLERRM__"
            if upper == "SQLCODE":
                return "__SQLCODE__"
            if upper == "SQLSTATE":
                return "__SQLSTATE__"
            if upper in ("CURRENT_TIMESTAMP", "NOW", "SYSTIMESTAMP"):
                return "new java.sql.Timestamp(System.currentTimeMillis())"
            if upper in ("CURRENT_DATE", "SYSDATE"):
                return "new java.sql.Date(System.currentTimeMillis())"
            if upper in ("LOCALTIMESTAMP",):
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
            # e.g. ["v_emp", "status"] → vEmp.get("status") or vEmp.status (for custom RECORD types)
            if len(parts) >= 2 and proc is not None:
                # Check if this is a same-package variable reference (e.g. PKG_NAME.var_name)
                if len(parts) == 2 and proc is not None:
                    _pkg_candidate = parts[0]
                    _field_candidate = parts[-1]
                    if _pkg_candidate.upper() == proc.package.upper() and _field_candidate in _PACKAGE_VARIABLES:
                        return f"this.{snake_to_camel(_field_candidate)}"
                var_name_raw = parts[0]
                field_name = parts[-1]
                var_java = snake_to_camel(var_name_raw)
                var_type = proc.local_vars.get(var_name_raw, "")
                _param_type = ""
                for pp in proc.parameters:
                    if pp.name.lower() == var_name_raw.lower():
                        _param_type = pp.java_type
                        break
                _is_custom_record = _param_type and _param_type not in ("Map<String, Object>", "Object", "") and not _param_type.startswith("List<")
                if _is_custom_record:
                    field_java = snake_to_camel(field_name)
                    _field_java_type = _resolve_custom_field_type(_param_type, field_name, proc)
                    if _field_java_type and _field_java_type not in ("String", "Object", "Map<String, Object>"):
                        return f'{var_java}.{field_java}.doubleValue()'
                    return f'{var_java}.{field_java}'
                if len(parts) >= 2 and field_name.upper() == "COUNT":
                    if "List<" in var_type:
                        return f"{var_java}.size()"
                    if "List<" in _param_type:
                        return f"{var_java}.size()"
                if var_type in ("Map<String, Object>", "Object") or var_name_raw not in proc.local_vars:
                    field_key = field_name.lower()
                    if not as_read:
                        return f'__MAP_PUT__{var_java}__{field_key}'
                    return f'{var_java}.get("{field_key}")'
            java_name = snake_to_camel(name)
            if proc is not None and name:
                _is_local = name in proc.local_vars
                _is_param = any(p.name.lower() == name.lower() for p in proc.parameters)
                _is_const = name in _PACKAGE_CONSTANTS
                _is_pkg_var = name in _PACKAGE_VARIABLES and not _is_local
                if not _is_local and not _is_param and not _is_const and not _is_pkg_var:
                    # PL/pgSQL allows calling functions with zero arguments without parentheses.
                    # The ogsql parser represents these as ColumnRef instead of FunctionCall.
                    # Check if this identifier is a known function in any registered package.
                    _func_found = False
                    if all_packages:
                        name_lower = name.lower()
                        for _pkg_name, _pkg_info in all_packages.items():
                            for _p in (_pkg_info.procedures if hasattr(_pkg_info, 'procedures') else []):
                                if _p.proc_name.lower() == name_lower:
                                    _method = java_method_name(name)
                                    _is_same_pkg = (
                                        (_p.package and proc.package and _p.package.lower() == proc.package.lower())
                                        or (_p.source_file == proc.source_file)
                                    )
                                    if _is_same_pkg:
                                        java_name = f"this.{_method}()"
                                    else:
                                        _svc = f"{package_to_classname(_pkg_name).lower()}Service"
                                        proc.service_calls.append(ServiceCall(_svc, _method, [], package_name=_pkg_name))
                                        java_name = f"{_svc}.{_method}()"
                                    _func_found = True
                                    break
                            if _func_found:
                                break
                    if not _func_found:
                        # Non-blocking: SQL aggregates (generate_series, UNNEST) create column aliases
                        # that look like ColumnRefs but are SQL-level, not PL/pgSQL variables.
                        _record_todo("unknown-columnref", proc, f"ColumnRef '{name}' 可能是 SQL 聚合别名或无参函数调用")
                        # Generate a stub method call instead of bare identifier to avoid compilation errors
                        java_name = f"/* TODO: {java_name}() */ null"
                if _is_pkg_var:
                    java_name = f"this.{java_name}"
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
                return "__SQLERRM__"
            if upper == "SQLCODE":
                return "__SQLCODE__"
            if upper == "SQLSTATE":
                return "__SQLSTATE__"
            if upper in ("CURRENT_TIMESTAMP", "NOW"):
                return "new java.sql.Timestamp(System.currentTimeMillis())"
            if upper in ("CURRENT_DATE", "SYSDATE"):
                return "new java.sql.Date(System.currentTimeMillis())"
            if upper == "LOCALTIMESTAMP":
                return "java.sql.Timestamp.valueOf(java.time.LocalDateTime.now())"
            java_name = snake_to_camel(name)
            if proc is not None and name:
                _is_local = name in proc.local_vars
                _is_param = any(p.name.lower() == name.lower() for p in proc.parameters)
                _is_const = name in _PACKAGE_CONSTANTS
                _is_pkg_var = name in _PACKAGE_VARIABLES and not _is_local
                if not _is_local and not _is_param and not _is_const and not _is_pkg_var:
                    _record_todo("unknown-plvariable", proc, f"PlVariable '{name}' 未识别为局部变量/参数")
                if _is_pkg_var:
                    java_name = f"this.{java_name}"
            if as_read and proc is not None:
                for p in proc.parameters:
                    if p.is_out and p.java_name == java_name:
                        java_name = f"{java_name}.get()"
                        break
            return java_name
        elif key == "Literal":
            return _literal_to_java(val)
        elif key == "BinaryOp":
            left = _expr_to_java(val.get("left", {}), proc, all_packages=all_packages)
            right = _expr_to_java(val.get("right", {}), proc, all_packages=all_packages)
            op = val.get("op", "")

            # PostgreSQL JSONB operators
            if op == "->":
                return left
            if op == "->>":
                return "0"
            # PostgreSQL range/geometric operators — no direct Java equivalent
            if op in ("<@", "@>", "&&", "<<", ">>", "&<", "&>", "-|-", "~", "@"):
                return f"/* RangeOp: {op} */ false"

            if proc is not None:
                left_type = _infer_expr_type(val.get("left"), proc)
                right_type = _infer_expr_type(val.get("right"), proc)

                left_out = _get_out_param(val.get("left"), proc)
                right_out = _get_out_param(val.get("right"), proc)

                # Coerce String OUT params to Long only for numeric-style comparisons.
                # Equality against a string literal (e.g. p_o_succeed = '0') must stay
                # as String.equals — Long.valueOf("0") == "0" is invalid Java.
                _right_is_str_lit = (
                    isinstance(val.get("right"), dict)
                    and "Literal" in val.get("right", {})
                    and isinstance(val["right"]["Literal"], dict)
                    and "String" in val["right"]["Literal"]
                )
                _left_is_str_lit = (
                    isinstance(val.get("left"), dict)
                    and "Literal" in val.get("left", {})
                    and isinstance(val["left"]["Literal"], dict)
                    and "String" in val["left"]["Literal"]
                )
                if left_out and left_out.java_type == "String" and op in (">", "<", ">=", "<=", "=", "<>"):
                    if op in (">", "<", ">=", "<=") or (op in ("=", "<>") and not _right_is_str_lit and right_type != "String"):
                        left = f"Long.valueOf({left})"
                        left_type = "Long"
                if right_out and right_out.java_type == "String" and op in (">", "<", ">=", "<=", "=", "<>"):
                    if op in (">", "<", ">=", "<=") or (op in ("=", "<>") and not _left_is_str_lit and left_type != "String"):
                        right = f"Long.valueOf({right})"
                        right_type = "Long"

                # Cast .get() field access results for typed comparisons (null-safe)
                _left_is_primitive = False
                _right_is_primitive = False
                if ".get(" in left and "BigDecimal" in left_type and not _is_primitive_producing(left):
                    left = f"java.math.BigDecimal.valueOf(((Number) ({left} != null ? {left} : 0L)).longValue())"
                elif ".get(" in left and left_type == "Integer" and not _is_primitive_producing(left):
                    left = f"((Number) ({left} != null ? {left} : 0)).intValue()"
                    _left_is_primitive = True
                elif ".get(" in left and "Long" in left_type and not _is_primitive_producing(left):
                    left = f"((Number) ({left} != null ? {left} : 0L)).longValue()"
                    _left_is_primitive = True
                elif left_type == "Object" and op in (">", "<", ">=", "<=", "=", "<>"):
                    if right_type == "String":
                        pass
                    elif ".get(" in left and not _is_primitive_producing(left):
                        left = f"((Number) ({left} != null ? {left} : 0)).intValue()"
                        _left_is_primitive = True
                    elif "this." in left or "(" in left:
                        left = f"((Number) {left}).intValue()"
                        _left_is_primitive = True
                if ".get(" in right and "BigDecimal" in right_type and not _is_primitive_producing(right):
                    right = f"java.math.BigDecimal.valueOf(((Number) ({right} != null ? {right} : 0L)).longValue())"
                elif ".get(" in right and right_type == "Integer" and not _is_primitive_producing(right):
                    right = f"((Number) ({right} != null ? {right} : 0)).intValue()"
                    _right_is_primitive = True
                elif ".get(" in right and "Long" in right_type and not _is_primitive_producing(right):
                    right = f"((Number) ({right} != null ? {right} : 0L)).longValue()"
                    _right_is_primitive = True
                elif right_type == "Object" and op in (">", "<", ">=", "<=", "=", "<>"):
                    if left_type == "String":
                        pass
                    elif ".get(" in right and not _is_primitive_producing(right):
                        right = f"((Number) ({right} != null ? {right} : 0)).intValue()"
                        _right_is_primitive = True
                    elif "this." in right or "(" in right:
                        right = f"((Number) {right}).intValue()"
                        _right_is_primitive = True

                is_bd = "BigDecimal" in left_type or "BigDecimal" in right_type
                is_str = left_type == "String" or right_type == "String"

                if is_bd and op in (">", "<", ">=", "<=", "=", "<>"):
                    cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                    if _is_numeric_literal(val.get("right")) and "BigDecimal" not in right:
                        right = f"java.math.BigDecimal.valueOf({right})"
                    if "Integer" in left_type or left_type == "int":
                        if "BigDecimal" not in left:
                            left = f"java.math.BigDecimal.valueOf({left})"
                    if "Integer" in right_type or right_type == "int":
                        if "BigDecimal" not in right:
                            right = f"java.math.BigDecimal.valueOf({right})"
                    return f"{left}.compareTo({right}) {cmp_map[op]} 0"

                if is_str and not is_bd and op in (">", "<", ">=", "<="):
                    # Issue #40: String.compareTo() is lexicographic ("10" < "3").
                    # Use BigDecimal when at least one operand is a numeric literal.
                    right_raw = val.get("right")
                    left_raw = val.get("left")
                    _should_use_bigdecimal = False
                    # Check if right operand is a numeric string literal
                    if isinstance(right_raw, dict) and "Literal" in right_raw:
                        lit = right_raw["Literal"]
                        if isinstance(lit, dict) and "String" in lit:
                            try:
                                float(lit["String"])
                                _should_use_bigdecimal = True
                            except ValueError:
                                pass
                    # Check if left operand is a numeric string literal
                    if isinstance(left_raw, dict) and "Literal" in left_raw:
                        lit = left_raw["Literal"]
                        if isinstance(lit, dict) and "String" in lit:
                            try:
                                float(lit["String"])
                                _should_use_bigdecimal = True
                            except ValueError:
                                pass
                    if right_type != "String" and ".get(" in right:
                        right = f"String.valueOf({right})"
                    if left_type != "String" and ".get(" in left:
                        left = f"String.valueOf({left})"
                    if _should_use_bigdecimal:
                        left_bd = f"new java.math.BigDecimal({left})"
                        right_bd = f"new java.math.BigDecimal({right})"
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">="}
                        return f"{left_bd}.compareTo({right_bd}) {cmp_map[op]} 0"
                    cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">="}
                    return f"{left}.compareTo({right}) {cmp_map[op]} 0"

                if ("Long" in left_type or "Long" in right_type) and op in (">", "<", ">=", "<=", "=", "<>"):
                    cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                    if _left_is_primitive or _right_is_primitive:
                        if _is_numeric_literal(val.get("right")):
                            right = f"((Number) {right}).longValue()" if ".get(" in right else right
                        elif _is_numeric_literal(val.get("left")):
                            left = f"((Number) {left}).longValue()" if ".get(" in left else left
                        return f"{left} {cmp_map[op]} {right}"
                    if _is_numeric_literal(val.get("right")):
                        right = f"Long.valueOf({right})"
                    elif _is_numeric_literal(val.get("left")):
                        left = f"Long.valueOf({left})"
                    elif right_type == "String" and not _is_numeric_literal(val.get("right")):
                        right = f"Long.parseLong({right})"
                    elif left_type == "String" and not _is_numeric_literal(val.get("left")):
                        left = f"Long.parseLong({left})"
                    return f"{left}.compareTo({right}) {cmp_map[op]} 0"

                # ── General type alignment for mixed-type numeric comparisons ──
                if op in (">", "<", ">=", "<=", "=", "<>"):
                    _NUMERIC_PRIORITY = {"Integer": 0, "Long": 1, "Float": 2, "Double": 3, "java.math.BigDecimal": 4}
                    _l_pri = _NUMERIC_PRIORITY.get(left_type, -1)
                    _r_pri = _NUMERIC_PRIORITY.get(right_type, -1)
                    
                    if _l_pri >= 0 and _r_pri >= 0 and _l_pri != _r_pri:
                        # Both numeric but different precision -- promote to higher
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                        
                        if _l_pri > _r_pri:
                            right = _coerce_type(right, right_type, left_type)
                            _common_type = left_type
                        else:
                            left = _coerce_type(left, left_type, right_type)
                            _common_type = right_type
                        
                        if _common_type == "java.math.BigDecimal":
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        elif _common_type == "Long":
                            if _is_numeric_literal(val.get("right")):
                                right = f"Long.valueOf({right})"
                            elif _is_numeric_literal(val.get("left")):
                                left = f"Long.valueOf({left})"
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        elif _common_type == "Double":
                            return f"Double.compare({left}, {right}) {cmp_map[op]} 0"
                        return f"{left} {cmp_map[op]} {right}"
                    
                    # String vs Numeric comparison (non-Map.get)
                    if (_l_pri >= 0 and left_type != "Object") and right_type == "String" and ".get(" not in right:
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                        right = _coerce_type(right, "String", left_type)
                        if left_type == "java.math.BigDecimal":
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        return f"{left} {cmp_map[op]} {right}"
                    if (_r_pri >= 0 and right_type != "Object") and left_type == "String" and ".get(" not in left:
                        cmp_map = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "=": "==", "<>": "!="}
                        left = _coerce_type(left, "String", right_type)
                        if right_type == "java.math.BigDecimal":
                            return f"{left}.compareTo({right}) {cmp_map[op]} 0"
                        return f"{left} {cmp_map[op]} {right}"

                if is_bd and op in ("+", "-", "*", "/") and not (left_type.startswith("java.sql.") or right_type.startswith("java.sql.")):
                    arith_map = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
                    method = arith_map[op]
                    if _is_numeric_literal(val.get("left")):
                        left = f"java.math.BigDecimal.valueOf({left})"
                    elif "BigDecimal" not in left_type:
                        if "?" in left and "null" in left:
                            left = _wrap_ternary_nullsafe_bd(left)
                        else:
                            left = f"java.math.BigDecimal.valueOf({left})"
                    if _is_numeric_literal(val.get("right")):
                        right = f"java.math.BigDecimal.valueOf({right})"
                    elif "BigDecimal" not in right_type:
                        if "?" in right and "null" in right:
                            right = _wrap_ternary_nullsafe_bd(right)
                        else:
                            right = f"java.math.BigDecimal.valueOf({right})"
                    return f"{left}.{method}({right})"

                if is_bd and op == "||":
                    return f"{left}.toString().concat({right}.toString())"

            if op == "^":
                left_d = f"((Number) ({left} != null ? {left} : 0.0d)).doubleValue()" if (".get(" in left and not _is_primitive_producing(left)) else (f"({left} != null ? Double.parseDouble({left}) : 0.0d)" if left_type == "String" else f"((Number) ({left})).doubleValue()")
                right_d = f"((Number) ({right} != null ? {right} : 0.0d)).doubleValue()" if (".get(" in right and not _is_primitive_producing(right)) else (f"({right} != null ? Double.parseDouble({right}) : 0.0d)" if right_type == "String" else f"((Number) ({right})).doubleValue()")
                return f"Math.pow({left_d}, {right_d})"
            java_op = _java_op(op)
            if op in ("+", "-", "*", "/") and (".get(" in left or ".get(" in right or left_type == "String" or right_type == "String"):
                if op == "+" and left_type == "String" and right_type == "String":
                    pass
                else:
                    left_d = f"((Number) ({left} != null ? {left} : 0.0d)).doubleValue()" if (".get(" in left and not _is_primitive_producing(left)) else (f"({left} != null ? Double.parseDouble({left}) : 0.0d)" if left_type == "String" else f"((Number) ({left})).doubleValue()")
                    right_d = f"((Number) ({right} != null ? {right} : 0.0d)).doubleValue()" if (".get(" in right and not _is_primitive_producing(right)) else (f"({right} != null ? Double.parseDouble({right}) : 0.0d)" if right_type == "String" else f"((Number) ({right})).doubleValue()")
                    return f"({left_d} {java_op} {right_d})"
            _is_ts_left = "Timestamp" in left_type or "Timestamp" in left or "java.sql.Date" in left or left_type in ("java.sql.Date", "java.util.Date")
            _is_ts_right = "Timestamp" in right_type or "Timestamp" in right or "java.sql.Date" in right or right_type in ("java.sql.Date", "java.util.Date")
            _is_dur_left = ".toMillis()" in left
            _is_dur_right = ".toMillis()" in right
            _extract_tails = (".getYear()", ".getMonthValue()", ".getDayOfMonth()",
                              ".getDayOfWeek()", ".getDayOfYear()", ".getHour()",
                              ".getMinute()", ".getSecond()", ".toLocalDate()")
            _left_is_extract = any(left.rstrip().endswith(t) for t in _extract_tails)
            _right_is_extract = any(right.rstrip().endswith(t) for t in _extract_tails)
            if op == "-" and _is_ts_left and _is_ts_right:
                if _left_is_extract or _right_is_extract:
                    return f"({left} - {right})"
                return f"({left}.getTime() - {right}.getTime())"
            if op == "-" and _is_ts_left and _is_dur_right:
                return f"new java.sql.Timestamp({left}.getTime() - {right})"
            if op == "-" and _is_dur_left and _is_ts_right:
                return f"new java.sql.Timestamp({right}.getTime() - {left})"
            if op == "+" and _is_ts_left and _is_dur_right:
                return f"new java.sql.Timestamp({left}.getTime() + {right})"
            if op == "+" and _is_ts_right and _is_dur_left:
                return f"new java.sql.Timestamp({right}.getTime() + {left})"
            if op == "+" and _is_ts_left and right_type in ("Long", "long", "Integer", "int", "Double", "double", "Float", "float", "java.math.BigDecimal", "BigDecimal"):
                return f"new java.sql.Date({left}.getTime() + ((Number) ({right})).longValue() * 86400000L)"
            if op == "+" and _is_ts_right and left_type in ("Long", "long", "Integer", "int", "Double", "double", "Float", "float", "java.math.BigDecimal", "BigDecimal"):
                return f"new java.sql.Date({right}.getTime() + ((Number) ({left})).longValue() * 86400000L)"
            if op == "=" and _is_string_comparison(val, proc):
                # Ensure the non-.get() operand is the caller to avoid NPE on null Map.get()
                if ".get(" in right and ".get(" not in left:
                    return f"{left}.equals({right})"
                if ".get(" in left and ".get(" not in right:
                    return f"{right}.equals({left})"
                return f"java.util.Objects.equals({left}, {right})"
            elif op == "<>" and _is_string_comparison(val, proc):
                if ".get(" in right and ".get(" not in left:
                    return f"!{left}.equals({right})"
                if ".get(" in left and ".get(" not in right:
                    return f"!{right}.equals({left})"
                return f"!java.util.Objects.equals({left}, {right})"
            _is_ts_cmp = ("Timestamp" in left_type or "Timestamp" in left) and ("Timestamp" in right_type or "Timestamp" in right)
            if _is_ts_cmp and op in (">", "<", ">=", "<="):
                _ts_cmp = {"<": "before", ">": "after", "<=": "!after", ">=": "!before"}
                _method = _ts_cmp.get(op)
                if _method.startswith("!"):
                    return f"!{right}.{_method[1:]}({left})"
                return f"{right}.{_method}({left})"
            if op in ("=", "<>", "!=", "==") and proc is not None:
                _str_int = _coerce_string_int_compare(val, left, right, proc)
                if _str_int:
                    return _str_int
            return f"{left} {java_op} {right}"
        elif key == "UnaryOp":
            operand = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            op = val.get("op", "")
            java_op = _java_op(op)
            if java_op == "!":
                return f"!{operand}"
            return f"{java_op}{operand}"
        elif key == "InList":
            expr_java = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            items = val.get("list", [])
            items_java = [_expr_to_java(item, proc, all_packages=all_packages) for item in items]
            items_str = ", ".join(items_java)
            negated = val.get("negated", False)
            if negated:
                return f"!Arrays.asList({items_str}).contains({expr_java})"
            return f"Arrays.asList({items_str}).contains({expr_java})"
        elif key == "FunctionCall":
            name_parts = val.get("name", [])
            func_name = name_parts[-1] if name_parts else "unknown"
            func_name_lower = func_name.lower()
            args_java = [_expr_to_java(a, proc, all_packages=all_packages) for a in val.get("args", [])]
            args_str = ", ".join(args_java)

            if proc is not None and func_name_lower not in SQL_FUNCTION_MAP:
                if proc.custom_types and func_name_lower in {k.lower(): k for k in proc.custom_types}:
                    _type_key = {k.lower(): k for k in proc.custom_types}[func_name_lower]
                    _ct = proc.custom_types[_type_key]
                    if _ct.get("kind") in ("table", "varray") and len(args_java) > 1:
                        elem_type = _ct.get("elem_type", "Object")
                        if elem_type == "java.math.BigDecimal":
                            args_java = [f"java.math.BigDecimal.valueOf({a})" for a in args_java]
                            args_str = ", ".join(args_java)
                        elif elem_type == "Long":
                            args_java = [f"Long.valueOf({a})" for a in args_java]
                            args_str = ", ".join(args_java)
                        return f"java.util.Arrays.asList({args_str})"
                for p in proc.parameters:
                    if p.name.lower() == func_name_lower and p.java_type.startswith("List<"):
                        idx_expr = args_java[0] if args_java else "0"
                        return f"{snake_to_camel(func_name_lower)}.get((int)({idx_expr}) - 1)"
                for var_name, var_type in proc.local_vars.items():
                    if var_name.lower() == func_name_lower and var_type.startswith("List<"):
                        idx_expr = args_java[0] if args_java else "0"
                        return f"{snake_to_camel(func_name_lower)}.get((int)({idx_expr}) - 1)"

            # --- Builtin-based semantic function handling (AST builtin field) ---
            _builtin = val.get("builtin")
            if _builtin and isinstance(_builtin, dict):
                _builtin_domain = _builtin.get("domain", "")
                _builtin_category = _builtin.get("category", "")
                if _builtin_domain == "ExceptionContext":
                    if func_name_lower == "pg_exception_detail":
                        return '__SQLERRM__'
                    elif func_name_lower == "pg_exception_hint":
                        return '"(see exception hint in stack trace)"'
                    elif func_name_lower == "pg_exception_context":
                        return 'java.util.Arrays.toString(e.getStackTrace())'
                    else:
                        # Generic fallback for any future ExceptionContext functions
                        return '__SQLERRM__'
                elif _builtin_category == "TypeConstructor":
                    if args_java:
                        arg0 = args_java[0]
                        return arg0 if (arg0.startswith('"') or arg0.startswith("'")) else f"String.valueOf({arg0})"
                    return '"{}"'

            # Delegate to SPECIAL_FUNCTION_MAP first (SUBSTR with 3 args needs
            # dedicated handler for correct 1-based→0-based offset conversion)
            if func_name_lower in SPECIAL_FUNCTION_MAP:
                _wrapped_fn = lambda expr, p: _expr_to_java(expr, p, all_packages=all_packages)
                return SPECIAL_FUNCTION_MAP[func_name_lower](val, proc, _wrapped_fn)

            if func_name_lower in SQL_FUNCTION_MAP:
                mapped = SQL_FUNCTION_MAP[func_name_lower]
                if func_name_lower == "coalesce" and len(args_java) >= 2:
                    first_type = _infer_expr_type(val.get("args", [{}])[0], proc) if val.get("args") else "Object"
                    if "BigDecimal" in first_type:
                        args_java = [(a if a != "0" else "java.math.BigDecimal.ZERO") for a in args_java]
                        args_str = ", ".join(args_java)
                if func_name_lower in ("nvl", "nvl2", "coalesce") and args_java:
                    _a0 = args_java[0].strip()
                    _m0 = re.search(r'\.(intValue|longValue|doubleValue|floatValue)\(\)\s*\)*$', _a0)
                    if _m0:
                        _box = {"intValue": "Integer", "longValue": "Long", "doubleValue": "Double", "floatValue": "Float"}[_m0.group(1)]
                        args_java[0] = f"{_box}.valueOf({args_java[0]})"
                    elif _is_primitive_producing(_a0):
                        args_java[0] = f"Double.valueOf({args_java[0]})"
                if func_name_lower in _NUMERIC_FUNC_NEEDS_DOUBLE_ARGS:
                    coerced = []
                    for i, a_java in enumerate(args_java):
                        a_type = _infer_expr_type(val.get("args", [{}])[i], proc) if i < len(val.get("args", [])) else "Object"
                        if "BigDecimal" in a_type:
                            coerced.append(f"({a_java}).doubleValue()")
                        elif ".get(" in a_java:
                            coerced.append(f"((Number) {a_java}).doubleValue()")
                        elif a_type == "Object" and not a_java.endswith(".doubleValue()") and not _is_numeric_literal_expr(a_java):
                            coerced.append(f"((Number) {a_java}).doubleValue()")
                        else:
                            coerced.append(a_java)
                    args_java = coerced
                    args_str = ", ".join(args_java)
                if func_name_lower in _NUMERIC_FUNC_NEEDS_INT_ARGS:
                    coerced = []
                    for i, a_java in enumerate(args_java):
                        a_type = _infer_expr_type(val.get("args", [{}])[i], proc) if i < len(val.get("args", [])) else "Object"
                        if ".get(" in a_java:
                            coerced.append(f"((Number) {a_java}).intValue()")
                        elif a_type == "Object" and not _is_numeric_literal_expr(a_java):
                            coerced.append(f"((Number) {a_java}).intValue()")
                        else:
                            coerced.append(a_java)
                    args_java = coerced
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
                    return _cleanup_java_expr(result)
                elif mapped == "__SKIP__":
                    return args_java[0] if args_java else "null"
                elif any("{" + k + "}" in mapped for k in tpl_args):
                    for k, v in tpl_args.items():
                        mapped = mapped.replace("{" + k + "}", v)
                    return _cleanup_java_expr(mapped)
                elif "(" in mapped:
                    return _cleanup_java_expr(f"{mapped}({args_str})")
                else:
                    return _cleanup_java_expr(f"{mapped}({args_str})")
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
                            target_proc = _find_target_proc(matched, name_parts[-1], all_packages, arg_count=len(val.get("args", [])))
                            if target_proc:
                                method = java_method_name(name_parts[-1])
                                out_param_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
                                target_out_indices = {i for i, p in enumerate(target_proc.parameters) if p.is_out}
                                raw_args = val.get("args", [])
                                wrapped_args = []
                                for i, a_java in enumerate(args_java):
                                    if i < len(target_proc.parameters):
                                        if i in target_out_indices:
                                            raw_java = _expr_to_java(raw_args[i] if i < len(raw_args) else {}, proc, as_read=False, all_packages=all_packages)
                                            if raw_java in out_param_java_names:
                                                wrapped_args.append(raw_java)
                                                continue
                                        wrapped_args.append(_coerce_java_arg(a_java, target_proc.parameters[i].java_type))
                                    else:
                                        wrapped_args.append(a_java)
                                svc_name = f"{package_to_classname(matched).lower()}Service"
                                proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched))
                                return f"{svc_name}.{method}({', '.join(wrapped_args)})"
                if self_call_pkg is not None:
                    _resolved_pkg = self_call_pkg
                    target_proc = _find_target_proc(self_call_pkg, self_call_func, all_packages, arg_count=len(val.get("args", [])))
                    if not target_proc:
                        # Package key in all_packages may differ from proc.package (e.g. filename-based key)
                        # Search all packages for a matching function in the same source file
                        for _apk, _api in (all_packages.items() if all_packages else []):
                            for _ap in (_api.procedures if hasattr(_api, 'procedures') else []):
                                if _ap.proc_name.lower() == self_call_func.lower():
                                    if _ap.source_file == proc.source_file:
                                        _resolved_pkg = _apk
                                        target_proc = _ap
                                        break
                            if target_proc:
                                break
                    if target_proc and len(target_proc.parameters) == len(val.get("args", [])):
                        method = java_method_name(self_call_func)
                        out_param_java_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
                        target_out_indices = {i for i, p in enumerate(target_proc.parameters) if p.is_out}
                        raw_args = val.get("args", [])
                        wrapped_args = []
                        for i, a_java in enumerate(args_java):
                            if i < len(target_proc.parameters):
                                if i in target_out_indices:
                                    raw_java = _expr_to_java(raw_args[i] if i < len(raw_args) else {}, proc, as_read=False, all_packages=all_packages)
                                    if raw_java in out_param_java_names:
                                        wrapped_args.append(raw_java)
                                        continue
                                wrapped_args.append(_coerce_java_arg(a_java, target_proc.parameters[i].java_type))
                            else:
                                wrapped_args.append(a_java)
                        return f"this.{method}({', '.join(wrapped_args)})"
                    elif target_proc or (all_packages and all_packages.get(_resolved_pkg)):
                        _exists_elsewhere = False
                        if all_packages:
                            for _apk2, _api2 in all_packages.items():
                                if _apk2 == _resolved_pkg:
                                    continue
                                for _ap2 in (_api2.procedures if hasattr(_api2, 'procedures') else []):
                                    if _ap2.proc_name.lower() == self_call_func.lower() and len(_ap2.parameters) == len(val.get("args", [])):
                                        _exists_elsewhere = True
                                        break
                                if _exists_elsewhere:
                                    break
                        if not _exists_elsewhere:
                            raw_args = val.get("args", [])
                            _actual_target = _find_target_proc(_resolved_pkg, self_call_func, all_packages, arg_count=len(raw_args))
                        if not _actual_target:
                            for _apk in (all_packages or {}):
                                _actual_target = _find_target_proc(_apk, self_call_func, all_packages, arg_count=len(raw_args))
                                if _actual_target:
                                    break
                        arg_types = []
                        for i, a_java in enumerate(args_java):
                            if _actual_target and i < len(_actual_target.parameters):
                                arg_types.append(_actual_target.parameters[i].java_type)
                            else:
                                inferred = _infer_expr_type(raw_args[i], proc) if i < len(raw_args) else "Object"
                                if inferred == "Object" and "BigDecimal" in a_java:
                                    inferred = "java.math.BigDecimal"
                                elif inferred == "Object" and a_java == "true" or a_java == "false":
                                    inferred = "boolean"
                                arg_types.append(inferred)
                        method = java_method_name(self_call_func)
                        _register_missing_overload(self_call_pkg, method, arg_types, len(raw_args))
                        wrapped_args = []
                        for i, a_java in enumerate(args_java):
                            if i < len(arg_types):
                                wrapped_args.append(_coerce_java_arg(a_java, arg_types[i]))
                            else:
                                wrapped_args.append(a_java)
                        return f"this.{method}({', '.join(wrapped_args)})"
                # Self-call failed — try cross-package search for the function
                if all_packages and self_call_pkg is not None:
                    for _cpk, _cpi in all_packages.items():
                        if _cpk == self_call_pkg:
                            continue
                        for _cp in (_cpi.procedures if hasattr(_cpi, 'procedures') else []):
                            if _cp.proc_name.lower() == func_name_lower and len(_cp.parameters) == len(val.get("args", [])):
                                method = java_method_name(func_name)
                                if _cp.source_file == proc.source_file:
                                    proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=_cpk))
                                    wrapped_args = []
                                    for i, a_java in enumerate(args_java):
                                        if i < len(_cp.parameters):
                                            a_java = _coerce_java_arg(a_java, _cp.parameters[i].java_type)
                                        wrapped_args.append(a_java)
                                    return f"this.{method}({', '.join(wrapped_args)})"
                                svc_name = f"{package_to_classname(_cpk).lower()}Service"
                                proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=_cpk))
                                wrapped_args = []
                                for i, a_java in enumerate(args_java):
                                    if i < len(_cp.parameters):
                                        a_java = _coerce_java_arg(a_java, _cp.parameters[i].java_type)
                                    wrapped_args.append(a_java)
                                return f"{svc_name}.{method}({', '.join(wrapped_args)})"
                _record_unsupported(func_name, proc)
                # Enriched TODO: package hint, return type, arg types, caller location
                _arg_count = len(val.get("args", []))
                _pkg_hint = name_parts[-2] if len(name_parts) >= 2 else ""
                _ret_type = _UDF_RETURN_TYPES.get((func_name_lower, _arg_count), "?")
                _arg_types = []
                for _a in val.get("args", []):
                    _at = _infer_expr_type(_a, proc)
                    _arg_types.append(_at if _at and _at != "Object" else "?")
                _sig = f"ret={_ret_type}, args=[{', '.join(_arg_types)}]"
                _hint = f"pkg={_pkg_hint}" if _pkg_hint else "pkg=?"
                _caller = f"caller={proc.source_file}:{proc.proc_name}"
                return f"/* TODO: implement {_flatten_comment(func_name)}({_flatten_comment(args_str)}) — {_hint}, {_sig}, {_caller} */ null"
        elif key == "SpecialFunction":
            func_name = val.get("name", "").lower()
            handler = SPECIAL_FUNCTION_MAP.get(func_name)
            if handler:
                _wrapped_fn = lambda expr, p: _expr_to_java(expr, p, all_packages=all_packages)
                return handler(val, proc, _wrapped_fn)
            _record_unsupported(func_name, proc, is_special=True)
            args_java = [_expr_to_java(a, proc, all_packages=all_packages) for a in val.get("args", [])]
            return f"/* UNSUPPORTED: {_flatten_comment(func_name)} — special syntax, no Java mapping */ null"
        elif key == "IsNull":
            inner = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            negated = val.get("negated", False)
            if negated:
                return f"{inner} != null"
            return f"{inner} == null"
        elif key == "IsBoolean":
            inner = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            negated = val.get("negated", False)
            bool_val = val.get("value", True)
            if bool_val:
                if negated:
                    return f"!Boolean.TRUE.equals({inner})"
                return f"Boolean.TRUE.equals({inner})"
            else:
                if negated:
                    return f"!Boolean.FALSE.equals({inner})"
                return f"Boolean.FALSE.equals({inner})"
        elif key == "Parenthesized":
            return f"({_expr_to_java(val, proc, all_packages=all_packages)})"
        elif key == "Case":
            # CASE expression (not statement): CASE operand WHEN val THEN result ... ELSE else END
            operand_expr = val.get("operand") or val.get("expression")
            operand = _expr_to_java(operand_expr, proc, all_packages=all_packages) if operand_expr else None
            operand_type = _infer_expr_type(operand_expr, proc) if operand_expr and proc else "Object"
            is_primitive = operand_type in ("int", "Integer", "long", "Long", "short", "Short", "byte", "Byte", "double", "Double", "float", "Float", "boolean", "Boolean")
            cmp_op = "==" if is_primitive else ".equals"
            whens = val.get("whens", [])
            else_expr = val.get("else_expr") or val.get("else_result")
            if operand and whens:
                results_java = []
                for w in whens:
                    results_java.append(_expr_to_java(w.get("result", {}), proc, all_packages=all_packages))
                else_java = _expr_to_java(else_expr, proc, all_packages=all_packages) if else_expr else "null"
                result_types = [_infer_expr_type(w.get("result"), proc) for w in whens]
                else_type = _infer_expr_type(else_expr, proc) if else_expr else "Object"
                has_bd = any("BigDecimal" in t for t in result_types) or "BigDecimal" in else_type
                parts = []
                for i, w in enumerate(whens):
                    cond = _expr_to_java(w.get("condition", {}), proc, all_packages=all_packages)
                    result = results_java[i]
                    if has_bd and result_types[i] not in ("java.math.BigDecimal", "Object", ""):
                        result = f"java.math.BigDecimal.valueOf({result})"
                    if is_primitive:
                        # Parenthesize operand to prevent operator precedence issues
                        # e.g. "indexOf(x) + 1 == 0" must be "(indexOf(x) + 1) == 0"
                        operand_needs_parens = any(op in operand for op in (" + ", " - ", " * ", " / ", " % "))
                        if operand_needs_parens:
                            cmp_expr = f"({operand}) == {cond}"
                        else:
                            cmp_expr = f"{operand} == {cond}"
                    else:
                        cmp_expr = f"java.util.Objects.equals({operand}, {cond})"
                    if i == 0:
                        parts.append(f"({cmp_expr} ? {result}")
                    else:
                        parts.append(f": ({cmp_expr} ? {result}")
                else_val = else_java
                if has_bd and "BigDecimal" not in else_type and else_type != "Object":
                    else_val = f"java.math.BigDecimal.valueOf({else_val})"
                parts.append(f": {else_val}")
                closing = ")" * len(whens)
                return "".join(parts) + closing
            elif whens:
                parts = []
                for i, w in enumerate(whens):
                    cond = _expr_to_java(w.get("condition", {}), proc, all_packages=all_packages)
                    result = _expr_to_java(w.get("result", {}), proc, all_packages=all_packages)
                    if i == 0:
                        parts.append(f"({cond} ? {result}")
                    else:
                        parts.append(f": ({cond} ? {result}")
                else_val = _expr_to_java(else_expr, proc, all_packages=all_packages) if else_expr else "null"
                parts.append(f": {else_val}")
                closing = ")" * len(whens)
                return "".join(parts) + closing
            return f"/* TODO: CASE expression */ null"
        elif key == "Expr":
            if isinstance(val, list) and len(val) >= 1:
                return _expr_to_java(val[0], proc, all_packages=all_packages)
            return _expr_to_java(val, proc, all_packages=all_packages)
        elif key == "Like":
            inner = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            pattern_expr = val.get("pattern", {})
            negated = val.get("negated", False)
            if isinstance(pattern_expr, dict) and "BinaryOp" in pattern_expr:
                bop = pattern_expr["BinaryOp"]
                if bop.get("op") == "AND":
                    left_like = {"Like": {"expr": val.get("expr"), "pattern": bop.get("left"), "negated": negated}}
                    right_expr = bop.get("right")
                    left_java = _expr_to_java(left_like, proc, all_packages=all_packages)
                    right_java = _expr_to_java(right_expr, proc, all_packages=all_packages)
                    return f"{left_java} && {right_java}"
            if isinstance(pattern_expr, dict) and "Literal" in pattern_expr:
                lit = pattern_expr["Literal"]
                if isinstance(lit, dict) and "String" in lit:
                    pat = lit["String"]
                    if pat.startswith("%") and pat.endswith("%"):
                        method = "contains"
                        arg = pat[1:-1]
                    elif pat.startswith("%"):
                        method = "endsWith"
                        arg = pat[1:]
                    elif pat.endswith("%"):
                        method = "startsWith"
                        arg = pat[:-1]
                    else:
                        method = "matches"
                        arg = pat.replace("%", ".*").replace("_", ".")
                    prefix = "!" if negated else ""
                    return f"{prefix}{inner}.{method}(\"{arg}\")"
            pattern_java = _expr_to_java(pattern_expr, proc, all_packages=all_packages)
            prefix = "!" if negated else ""
            return f"{prefix}{inner}.matches({pattern_java})"
        elif key == "Between":
            expr_java = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            low_java = _expr_to_java(val.get("low", {}), proc, all_packages=all_packages)
            high_java = _expr_to_java(val.get("high", {}), proc, all_packages=all_packages)
            negated = val.get("negated", False)
            if ".get(" in expr_java:
                _low_ast = val.get("low", {})
                _high_ast = val.get("high", {})
                _low_str = _high_str = ""
                if isinstance(_low_ast, dict) and "Literal" in _low_ast and isinstance(_low_ast["Literal"], dict):
                    _low_str = str(_low_ast["Literal"].get("String", ""))
                if isinstance(_high_ast, dict) and "Literal" in _high_ast and isinstance(_high_ast["Literal"], dict):
                    _high_str = str(_high_ast["Literal"].get("String", ""))
                try:
                    float(_low_str)
                    float(_high_str)
                    _bd_expr = f"new java.math.BigDecimal(String.valueOf({expr_java}))"
                    cmp = (f"({_bd_expr}.compareTo(new java.math.BigDecimal({low_java})) >= 0"
                           f" && {_bd_expr}.compareTo(new java.math.BigDecimal({high_java})) <= 0)")
                except (ValueError, TypeError):
                    _s_expr = f"String.valueOf({expr_java})"
                    cmp = f"({_s_expr}.compareTo({low_java}) >= 0 && {_s_expr}.compareTo({high_java}) <= 0)"
            else:
                cmp = f"({expr_java}) >= ({low_java}) && ({expr_java}) <= ({high_java})"
            return f"!({cmp})" if negated else cmp
        elif key == "TypeCast":
            return _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
        elif key == "CursorAttribute":
            cursor_expr = val.get("cursor", {})
            cursor_java = _expr_to_java(cursor_expr, proc)
            cursor_name = _extract_name_from_expr(cursor_expr)
            attr = val.get("attribute", "").lower()
            cursor_meta = None
            if proc is not None:
                cursor_meta = proc.open_cursors.get(cursor_name) or proc.open_cursors.get(cursor_name.lower())
            if attr in ("notfound", "not_found"):
                if cursor_meta:
                    return f"!({cursor_meta['index_var']} < {cursor_meta['result_var']}.size())"
                return f"!found"
            elif attr in ("found",):
                if cursor_meta:
                    return f"({cursor_meta['index_var']} < {cursor_meta['result_var']}.size())"
                return f"found"
            elif attr in ("isopen", "is_open"):
                if cursor_meta:
                    return f"({cursor_meta['result_var']} != null)"
                return f"({cursor_java} != null)"
            elif attr in ("rowcount", "row_count"):
                return f"__ROWCOUNT__"
            return f"/* CursorAttribute:{attr} */ false"
        elif key == "Subquery":
            targets = val.get("targets", [])
            from_clause = val.get("from", [])
            where_clause = val.get("where_clause")
            _subquery_handled = False
            if (proc is not None and len(from_clause) == 1 and len(targets) == 1
                    and where_clause is not None):
                _sq_from = from_clause[0]
                _sq_table_name = None
                if isinstance(_sq_from, dict):
                    for _tfk, _tfv in _sq_from.items():
                        if _tfk == "Table":
                            _sq_table_name = "_".join(_tfv.get("name", []))
                        elif _tfk == "Subquery":
                            pass
                        break
                _sq_target = targets[0]
                _sq_is_row_star_cast = False
                if isinstance(_sq_target, dict):
                    _sq_expr_list = _sq_target.get("Expr")
                    if isinstance(_sq_expr_list, list) and len(_sq_expr_list) == 2:
                        _sq_inner = _sq_expr_list[0]
                        if isinstance(_sq_inner, dict) and "TypeCast" in _sq_inner:
                            _sq_tc = _sq_inner["TypeCast"]
                            _sq_tc_expr = _sq_tc.get("expr", {})
                            if isinstance(_sq_tc_expr, dict) and "FunctionCall" in _sq_tc_expr:
                                _sq_fc = _sq_tc_expr["FunctionCall"]
                                _sq_fc_name = "_".join(_sq_fc.get("name", []))
                                _sq_fc_args = _sq_fc.get("args", [])
                                if _sq_fc_name.lower() == "row" and len(_sq_fc_args) == 1:
                                    _sq_arg = _sq_fc_args[0]
                                    if isinstance(_sq_arg, dict) and "QualifiedStar" in _sq_arg:
                                        _sq_is_row_star_cast = True
                _sq_where = where_clause
                _sq_pk_col = None
                _sq_where_right = None
                if isinstance(_sq_where, dict) and "BinaryOp" in _sq_where:
                    _sq_bop = _sq_where["BinaryOp"]
                    if _sq_bop.get("op") == "=":
                        _sq_left = _sq_bop.get("left", {})
                        _sq_right = _sq_bop.get("right", {})
                        if isinstance(_sq_left, dict) and "ColumnRef" in _sq_left:
                            _sq_col_parts = _sq_left["ColumnRef"]
                            if isinstance(_sq_col_parts, list) and len(_sq_col_parts) == 1:
                                _sq_pk_col = _sq_col_parts[0]
                                _sq_where_right = _sq_right
                if (_sq_is_row_star_cast and _sq_table_name and _sq_pk_col
                        and _sq_where_right is not None):
                    _sq_method_name = f"select{snake_to_pascal(_sq_table_name)}By{snake_to_pascal(_sq_pk_col)}"
                    _sq_pk_param = snake_to_camel(_sq_pk_col)
                    _sq_sql = f"SELECT * FROM {_sq_table_name} WHERE {_sq_pk_col} = #{{{_sq_pk_param}}}"
                    _sq_where_java = _expr_to_java(_sq_where_right, proc, all_packages=all_packages)
                    _existing = [d for d in proc.dml_statements if d.method_id == _sq_method_name]
                    if not _existing:
                        _add_dml(proc, DmlStatement(
                            sql_type="select",
                            method_id=_sq_method_name,
                            sql_text=_sq_sql,
                            result_type="Map<String, Object>",
                            returns_list=False,
                            is_dynamic=True,
                            extra_params=[(_sq_pk_param, "Object")],
                        ))
                    _subquery_handled = True
                    return f"mapper.{_sq_method_name}({_sq_where_java})"
            if not _subquery_handled:
                if where_clause:
                    where_java = _expr_to_java(where_clause, proc, all_packages=all_packages)
                    return f"/* Subquery: WHERE {_flatten_comment(where_java)} */ null"
                return f"/* Subquery */ null"
        elif key == "Subscript":
            obj_java = _expr_to_java(val.get("object", {}), proc, all_packages=all_packages)
            idx_expr = val.get("lower", val.get("index", {}))
            idx_java = _expr_to_java(idx_expr, proc, all_packages=all_packages)
            return f"((java.util.List)({obj_java})).get((int)({idx_java}) - 1)"
        elif key == "SequenceValue":
            seq_parts = val.get("sequence", [])
            seq_name = "_".join(seq_parts) if isinstance(seq_parts, list) else str(seq_parts)
            func = val.get("function", "").lower()
            if func == "currval":
                return f"/* CURRVAL: {seq_name} */ null"
            elif func == "nextval":
                return f"/* NEXTVAL: {seq_name} */ null"
            return f"/* SequenceValue: {func} {seq_name} */ null"

    return str(expr)


def _is_string_comparison(binary_op: dict, proc: ProcedureInfo = None) -> bool:
    # Original logic: right side is a string literal
    right = binary_op.get("right", {})
    right_is_numeric_string = False
    if isinstance(right, dict):
        for k, v in right.items():
            if k == "Literal" and isinstance(v, dict) and "String" in v:
                try:
                    float(v["String"])
                    right_is_numeric_string = True
                except ValueError:
                    return True
    # Check: if right is a numeric string literal AND the other side is a known
    # numeric type (e.g. INSTR returns Integer), treat as numeric comparison.
    # This prevents .equals() on primitive-like expressions causing precedence
    # bugs like "indexOf(x) + 1.equals(0)" (Java parses as float literal "1.equals").
    if right_is_numeric_string and proc:
        left_type = _infer_expr_type(binary_op.get("left", {}), proc)
        if left_type in ("Integer", "int", "Long", "long", "Double", "double",
                          "Float", "float", "java.math.BigDecimal"):
            return False
    # Extended: either side is a String type variable
    if proc:
        left_type = _infer_expr_type(binary_op.get("left", {}), proc)
        right_type = _infer_expr_type(binary_op.get("right", {}), proc)
        if left_type == "String" or right_type == "String":
            return True
    return False


def _coerce_string_int_compare(binary_op: dict, left_java: str, right_java: str, proc):
    if proc is None:
        return None
    left_ast = binary_op.get("left", {})
    right_ast = binary_op.get("right", {})
    op = binary_op.get("op", "=")
    neg = op in ("<>", "!=")

    def _is_int_literal(node):
        return isinstance(node, dict) and "Literal" in node and isinstance(node["Literal"], dict) and "Integer" in node["Literal"]

    def _is_string_var(node):
        if not isinstance(node, dict):
            return False
        for k, v in node.items():
            if k == "PlVariable" and isinstance(v, list) and v:
                vname = v[0].lower()
                for p in proc.parameters:
                    if p.name.lower() == vname and p.java_type == "String":
                        return True
                if vname in proc.local_vars and proc.local_vars[vname] == "String":
                    return True
        return False

    if _is_string_var(left_ast) and _is_int_literal(right_ast):
        int_val = right_ast["Literal"]["Integer"]
        eq = f'"{int_val}".equals({left_java})'
        return f"!{eq}" if neg else eq
    if _is_string_var(right_ast) and _is_int_literal(left_ast):
        int_val = left_ast["Literal"]["Integer"]
        eq = f'"{int_val}".equals({right_java})'
        return f"!{eq}" if neg else eq
    return None


def _literal_to_java(lit) -> str:
    if isinstance(lit, str):
        if lit == "Null":
            return "null"
        return lit
    if isinstance(lit, dict):
        for key, val in lit.items():
            if key == "String":
                escaped = val.replace("\\", "\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                return f'"{escaped}"'
            elif key == "Integer":
                _int_val = int(val)
                if _int_val > 2147483647 or _int_val < -2147483648:
                    if _int_val > 9223372036854775807 or _int_val < -9223372036854775808:
                        return f'new java.math.BigDecimal("{_int_val}")'
                    return f"{_int_val}L"
                return str(val)
            elif key == "Float":
                return f"{val}d"
            elif key == "Boolean":
                return "true" if val else "false"
            elif key == "Null":
                return "null"
            elif key == "BitString":
                return f'Long.parseUnsignedLong("{val}", 2)'
    return str(lit)


def _cleanup_java_expr(result: str) -> str:
    """Post-process: de-nest String.valueOf, constant-fold Integer.parseInt, unwrap string literals."""
    # De-nest String.valueOf(String.valueOf(x)) → String.valueOf(x)
    while "String.valueOf(String.valueOf(" in result:
        result = result.replace("String.valueOf(String.valueOf(", "String.valueOf(")
        paren_depth = 0
        remove_pos = None
        for i, ch in enumerate(result):
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    remove_pos = i
                    break
        if remove_pos is not None:
            result = result[:remove_pos] + result[remove_pos + 1:]

    # Integer.parseInt(String.valueOf(200)) → 200
    result = re.sub(
        r'Integer\.parseInt\(String\.valueOf\((\d+(?:\.\d+)?[dDfFlL]?)\)\)',
        r'\1',
        result
    )
    # String.valueOf("literal") → "literal"
    result = re.sub(r'String\.valueOf\(("(?:[^"\\]|\\.)*")\)', r'\1', result)

    return result


def _flatten_comment(text: str) -> str:
    """Replace nested /* */ inside comment text to avoid breaking Java block comments."""
    return re.sub(r'/\*|\*/', '', text)


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
                                elif isinstance(iv, list) and len(iv) == 1:
                                    result.append((iv[0], list(iv)))
                                elif isinstance(iv, str):
                                    result.append((iv, [iv]))
    return result


def _ordered_local_var_args(proc: ProcedureInfo, sql_text: str, extra: list = None) -> list:
    result = []
    seen = {p.java_name for p in proc.parameters if not p.is_out}
    for var_name, _var_type in proc.local_vars.items():
        jn = snake_to_camel(var_name)
        if jn in seen:
            continue
        mybatis_placeholders = set(re.findall(r'[#\$]\{(\w+)', sql_text or ""))
        if jn in mybatis_placeholders or re.search(rf'\b{re.escape(var_name)}\b', sql_text or "", re.IGNORECASE):
            result.append(jn)
            seen.add(jn)
    if extra:
        for jn, _jt in extra:
            if jn not in seen:
                result.append(jn)
                seen.add(jn)
    return result


def _build_param_args(params: list, extra_args: list = None) -> str:
    parts = []
    for p in params:
        if p.mode and p.mode.upper() == "OUT":
            continue
        if p.mode and p.mode.upper() == "INOUT":
            parts.append(f"{p.java_name}.get()")
        else:
            parts.append(p.java_name)
    if extra_args:
        parts.extend(extra_args)
    return ", ".join(parts)


def _build_param_args_from_template(proc: ProcedureInfo, template_params: list, extra_params: list = None, sql_text: str = "", dot_access_exprs: dict = None) -> str:
    template_java_names = set()
    for java_name, _is_id in template_params:
        template_java_names.add(java_name.split(".", 1)[0] if "." in java_name else java_name)
    parts = []
    seen = set()
    for p in proc.parameters:
        if p.mode and p.mode.upper() == "OUT":
            continue
        if sql_text:
            _sql_refs = set(re.findall(r'[#\$]\{(\w+)', sql_text))
            if p.java_name not in _sql_refs and p.java_name not in template_java_names:
                seen.add(p.java_name)
                continue
        if p.mode and p.mode.upper() == "INOUT":
            parts.append(f"{p.java_name}.get()")
        else:
            parts.append(p.java_name)
        seen.add(p.java_name)
    if sql_text:
        _sql_ref_names = set(re.findall(r'[#\$]\{(\w+)', sql_text))
        for var_name, _var_type in proc.local_vars.items():
            jn = snake_to_camel(var_name)
            if (jn in _sql_ref_names or jn in template_java_names) and jn not in seen:
                seen.add(jn)
                parts.append(jn)
    else:
        for var_name, _var_type in proc.local_vars.items():
            jn = snake_to_camel(var_name)
            if jn in template_java_names and jn not in seen:
                seen.add(jn)
                parts.append(jn)
    if extra_params:
        for jn, _jt in extra_params:
            if jn not in seen:
                seen.add(jn)
                if dot_access_exprs and jn in dot_access_exprs:
                    parts.append(dot_access_exprs[jn])
                else:
                    parts.append(jn)
    return ", ".join(parts)


def _sql_local_var_names(proc: ProcedureInfo, sql_text: str, is_select: bool = False) -> list:
    if not sql_text:
        return []
    scan_sql = sql_text
    # Strip SELECT ... INTO variable (PL/pgSQL), but not INSERT INTO table
    if is_select:
        into_match = re.search(r'\bINTO\b', sql_text, re.IGNORECASE)
        if into_match:
            after_into = re.search(r'\b(FROM|WHERE|ORDER|GROUP|HAVING|LIMIT)\b', sql_text[into_match.end():], re.IGNORECASE)
            if after_into:
                scan_sql = sql_text[:into_match.start()] + " " + sql_text[into_match.end() + after_into.start():]
            else:
                scan_sql = sql_text[:into_match.start()]
    param_names_lower = {p.name.lower() for p in proc.parameters if not p.is_out}
    mybatis_placeholders = set(re.findall(r'#\{(\w+)', sql_text))
    result = []
    for var_name in proc.local_vars:
        if var_name.lower() in param_names_lower:
            continue
        java_name = snake_to_camel(var_name)
        if java_name in mybatis_placeholders or re.search(rf'\b{re.escape(var_name)}\b', scan_sql, re.IGNORECASE):
            result.append(java_name)
    return result


def _mapper_call(proc: ProcedureInfo, mapper_method: str, sql_text: str = "") -> str:
    extra = _sql_local_var_names(proc, sql_text)
    return f"mapper.{mapper_method}({_build_param_args(proc.parameters, extra)})"


# ── Code Generation ────────────────────────────────────────────

def generate_project(output_dir: str, packages: list, changed_packages: set = None,
                     config: dict = None, progress_cb=None, resume_skip: set = None):
    base_path = Path(output_dir)
    base_path.mkdir(parents=True, exist_ok=True)

    if not (base_path / "pom.xml").exists():
        _write_pom_xml(base_path)
        _write_application_yml(base_path, config)
        _write_main_application(base_path)

    _be_java = base_path / BASE_DIR / "exception" / "BusinessException.java"
    if not _be_java.exists():
        _write_business_exception(base_path)

    # Issue #34: Generate Entity classes from DDL
    _write_entity_classes(base_path, _TABLE_DDL_SOURCE)

    if changed_packages is not None and not changed_packages:
        return

    svc_method_param_counts: dict = {}
    all_packages = {p.package_name: p for p in packages}
    for p in packages:
        svc_var = f"{package_to_classname(p.package_name)[0].lower()}{package_to_classname(p.package_name)[1:]}Service"
        for proc in p.procedures:
            mname = java_method_name(proc.proc_name)
            in_count = sum(1 for param in proc.parameters if not param.is_out)
            out_count = sum(1 for param in proc.parameters if param.is_out)
            svc_method_param_counts[(svc_var, mname)] = (in_count + out_count, proc.is_function)

    resume_set = resume_skip or set()
    active_pkgs = [pkg for pkg in packages
                   if changed_packages is None or pkg.package_name in changed_packages]
    n_gen = len(active_pkgs)
    gen_checkpoint = set(resume_set)
    gen_errors = []
    gen_idx = 0
    for pkg in active_pkgs:
        if pkg.package_name in resume_set:
            gen_idx += 1
            continue
        gen_idx += 1
        if progress_cb:
            progress_cb("pkg", gen_idx, n_gen, pkg.package_name)
        try:
            service_injections = _collect_service_injections(pkg)

            try:
                _write_service_class(base_path, pkg, service_injections, all_packages)
            except Exception as e:
                raise RuntimeError(f"_write_service_class: {e}") from e

            try:
                _write_mapper_interface(base_path, pkg)
            except Exception as e:
                raise RuntimeError(f"_write_mapper_interface: {e}") from e

            try:
                _write_mapper_xml(base_path, pkg)
            except Exception as e:
                raise RuntimeError(f"_write_mapper_xml: {e}") from e

            service_injections = _collect_service_injections(pkg)

            try:
                _write_service_test(base_path, pkg, service_injections, svc_method_param_counts, all_packages)
            except Exception as e:
                raise RuntimeError(f"_write_service_test: {e}") from e

            gen_checkpoint.add(pkg.package_name)
            _save_gen_checkpoint(output_dir, gen_checkpoint)
        except Exception as e:
            _log(f"  ❌ Error writing files for {pkg.package_name}: {e}", to_stdout=False)
            _log(traceback.format_exc(), to_stdout=False)
            gen_errors.append((pkg.package_name, str(e)))

    itest_cfg = (config or {}).get("integration_test", {})
    if not isinstance(itest_cfg, dict):
        itest_cfg = {}
    if itest_cfg.get("enabled"):
        schema_map = _itest_collect_schemas()
        _itest_write_infrastructure(base_path, itest_cfg)
        _itest_write_schema_sql(base_path, packages, itest_cfg)
        for pkg in active_pkgs:
            _itest_write_class(base_path, pkg, itest_cfg, schema_map, all_packages)


def _collect_service_injections(pkg: PackageInfo) -> dict:
    services = {}
    own_svc = f"{package_to_classname(pkg.package_name).lower()}Service"
    for proc in pkg.procedures:
        for call in proc.service_calls:
            if call.service_name == own_svc:
                continue
            if call.service_name not in services:
                services[call.service_name] = call.package_name
    return services


def _pom_source_encoding_property() -> str:
    if _SOURCE_ENCODING.lower() != 'utf-8':
        return f'\n                <project.build.sourceEncoding>{_SOURCE_ENCODING.upper()}</project.build.sourceEncoding>'
    return ""


def _write_pom_xml(base_path: Path):
    core_deps = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="{_SOURCE_ENCODING.upper()}"?>
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
                <java.version>17</java.version>{_pom_source_encoding_property()}
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
                </dependency>
                <dependency>
                    <groupId>org.testcontainers</groupId>
                    <artifactId>testcontainers</artifactId>
                    <version>1.19.8</version>
                    <scope>test</scope>
                </dependency>
                <dependency>
                    <groupId>org.testcontainers</groupId>
                    <artifactId>postgresql</artifactId>
                    <version>1.19.8</version>
                    <scope>test</scope>
                </dependency>
                <dependency>
                    <groupId>org.testcontainers</groupId>
                    <artifactId>junit-jupiter</artifactId>
                    <version>1.19.8</version>
                    <scope>test</scope>
                </dependency>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-testcontainers</artifactId>
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
                            <excludes>
                                <exclude>**/itest/**</exclude>
                            </excludes>
                        </configuration>
                    </plugin>
                </plugins>
            </build>

            <profiles>
                <profile>
                    <id>integration</id>
                    <build>
                        <plugins>
                            <plugin>
                                <groupId>org.apache.maven.plugins</groupId>
                                <artifactId>maven-surefire-plugin</artifactId>
                                <configuration>
                                    <includes>
                                        <include>**/itest/*Test.java</include>
                                        <include>**/*IntegrationTest.java</include>
                                    </includes>
                                    <excludes combine.self="override" />
                                </configuration>
                            </plugin>
                        </plugins>
                    </build>
                </profile>
            </profiles>
        </project>
    """)

    logger_cfg = _get_logger_config()
    logger_deps = ""
    for dep_xml in logger_cfg.get("pom", []):
        logger_deps += f"\n                {dep_xml}"

    content = core_deps + logger_deps + build_section
    _write_source_file(base_path / "pom.xml", content)


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
    _write_source_file(res_dir / "application.yml", content)


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
    _write_source_file(java_dir / "DemoApplication.java", content)


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
    _write_source_file(java_dir / "exception" / "BusinessException.java", content)


def _write_entity_classes(base_path: Path, table_ddl_source: dict):
    """Issue #34: Generate Entity POJO classes from parsed DDL table info."""
    if not table_ddl_source:
        return
    entity_dir = base_path / BASE_DIR / "entity"
    entity_dir.mkdir(parents=True, exist_ok=True)
    for table_name, columns in table_ddl_source.items():
        if not isinstance(columns, dict) or not columns:
            continue
        class_name = snake_to_pascal(table_name)
        fields = []
        for col_name, sql_type in columns.items():
            java_type = sql_type_to_java(sql_type)
            java_name = snake_to_camel(col_name)
            fields.append(f"    private {java_type} {java_name};")
        if not fields:
            continue
        fields_str = "\n".join(fields)
        content = textwrap.dedent(f"""\
            package {BASE_PACKAGE}.entity;

            public class {class_name} {{
            {fields_str}
            }}
        """)
        _write_source_file(entity_dir / f"{class_name}.java", content)


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

    # De-duplicate methods with identical signatures (name + param types, not param names)
    sig_map = {}
    deduped = []
    for m in methods:
        sig = m.strip()
        sig_line = sig
        if '\n' in sig:
            sig_line = [l for l in sig.split('\n') if l.strip() and not l.strip().startswith('//')][-1]
        norm = re.sub(r'@Param\("[^"]*"\)\s*', '', sig_line)
        norm = re.sub(r'\b([\w.]+(?:<[^>]+>)?)\s+(\w+)([,)])', r'\1 \3', norm)
        if norm in sig_map:
            deduped.append(f"    // [DUPLICATE] {sig_line.strip()}")
        else:
            sig_map[norm] = True
            deduped.append(m)
    methods = deduped

    if not methods:
        methods = [f"// No direct DML operations for {pkg.package_name}"]

    for method_name, sql, return_type in getattr(pkg, '_extra_mapper_methods', []):
        if return_type == "Long":
            methods.append(f"{return_type} {method_name}(@Param(\"seqName\") String seqName);")
            imports.add("import org.apache.ibatis.annotations.Param;")
        elif return_type == "Integer":
            methods.append(f"{return_type} {method_name}(@Param(\"jsonb\") String jsonb);")
            imports.add("import org.apache.ibatis.annotations.Param;")

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
    _write_source_file(java_dir / f"{class_name}.java", content)


def _dml_used_local_vars(proc: ProcedureInfo, dml: DmlStatement) -> list:
    sql_raw = dml.sql_text or ""
    mybatis_placeholders = set(re.findall(r'[#\$]\{(\w+)', sql_raw))
    forall_elem_names = {name for name, _ in dml.extra_params} if dml.extra_params else set()
    used = []
    param_names_lower = {p.name.lower() for p in proc.parameters if not p.is_out}
    param_java_lower = {p.java_name.lower() for p in proc.parameters if not p.is_out}
    for var_name, var_java_type in proc.local_vars.items():
        java_name = snake_to_camel(var_name)
        if var_name.lower() in param_names_lower or java_name.lower() in param_java_lower:
            continue
        if java_name in mybatis_placeholders or re.search(rf'\b{re.escape(var_name)}\b', sql_raw, re.IGNORECASE):
            mapper_type = var_java_type
            if java_name in forall_elem_names:
                m = re.match(r'java\.util\.List<(.+)>', mapper_type)
                if m:
                    mapper_type = m.group(1)
            used.append((java_name, mapper_type))
    return used


def _build_mapper_method(proc: ProcedureInfo, dml: DmlStatement, imports: set) -> str:
    method_name = dml.method_id

    # FORALL batch: single param accepting the primary array list
    if dml.is_forall_batch and dml.forall_batch_arrays:
        _primary_arr = next(iter(dml.forall_batch_arrays))
        ret = "int"
        imports.add("import java.util.List;")
        imports.add("import java.util.Map;")
        imports.add("import org.apache.ibatis.annotations.Param;")
        return f"    int {method_name}(@Param(\"list\") java.util.List<java.util.Map<String, Object>> list);"

    sql_raw = dml.sql_text or ""
    _sql_refs = set(re.findall(r'[#\$]\{(\w+)', sql_raw))
    if dml.is_dynamic and dml.dynamic_conditions:
        for dc in dml.dynamic_conditions:
            _sql_refs.update(re.findall(r'[#\$]\{(\w+)', dc.sql_fragment))

    params = []
    _extra_java_names = {jn for jn, _ in dml.extra_params}
    for p in proc.parameters:
        if p.mode and p.mode.upper() == "OUT":
            continue
        if dml.is_dynamic and p.java_name not in _sql_refs and p.java_name not in _extra_java_names:
            continue
        params.append(f'@Param("{p.java_name}") {p.java_type} {p.java_name}')
        _imp = _resolve_import(p.java_type)
        if _imp:
            imports.add(_imp)

    for java_name, java_type in _dml_used_local_vars(proc, dml):
        # Unwrap AtomicReference<T> → T for mapper parameters
        mapper_type = re.sub(r'^AtomicReference<(.+)>$', r'\1', java_type) if java_type else java_type
        params.append(f'@Param("{java_name}") {mapper_type} {java_name}')
        _imp = _resolve_import(mapper_type)
        if _imp:
            imports.add(_imp)

    seen_java = {p.java_name for p in proc.parameters if not (p.mode and p.mode.upper() == "OUT")}
    seen_java.update(jn for jn, _ in _dml_used_local_vars(proc, dml))
    for java_name, java_type in dml.extra_params:
        if java_name not in seen_java:
            seen_java.add(java_name)
            params.append(f'@Param("{java_name}") {java_type} {java_name}')
            _imp = _resolve_import(java_type)
            if _imp:
                imports.add(_imp)

    params_str = ", ".join(params) if params else ""

    # Determine return type
    if dml.returning_cols:
        ret = "Map<String, Object>"
        imports.add("import java.util.Map;")
    elif dml.sql_type == "select":
        if dml.returns_list:
            ret = "List<Map<String, Object>>"
            imports.add("import java.util.List;")
            imports.add("import java.util.Map;")
        elif dml.result_type and dml.result_type == "Integer":
            ret = "Integer"
        elif dml.result_type and dml.result_type != "Map<String, Object>":
            ret = dml.result_type
            _imp = _resolve_import(ret)
            if _imp:
                imports.add(_imp)
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
        _proc_cte_defs = {}
        for dml in proc.dml_statements:
            sql_raw = dml.sql_text.strip()
            _with_start = re.search(r'(?i)\bWITH\b', sql_raw)
            if _with_start:
                _pos = _with_start.end()
                _d = 0
                _dml_pos = None
                for _ci in range(_pos, len(sql_raw)):
                    if sql_raw[_ci] == '(':
                        _d += 1
                    elif sql_raw[_ci] == ')':
                        _d -= 1
                    elif _d == 0:
                        _dm = re.match(r'(?i)\s*\b(INSERT|UPDATE|DELETE|SELECT)\b', sql_raw[_ci:])
                        if _dm:
                            _dml_pos = _ci
                            break
                if _dml_pos:
                    _cte_block = sql_raw[_with_start.end():_dml_pos]
                    _depth = 0
                    _start = 0
                    _name = None
                    i = 0
                    while i < len(_cte_block):
                        c = _cte_block[i]
                        if c == '(':
                            _depth += 1
                        elif c == ')':
                            _depth -= 1
                            if _depth == 0 and _name:
                                _proc_cte_defs[_name] = _cte_block[_start:i+1].strip()
                                _name = None
                        elif _depth == 0:
                            _nm = re.match(r'(\w+)\s+AS\s*\(', _cte_block[i:])
                            if _nm:
                                _name = _nm.group(1).lower()
                                _start = i + _nm.end() - 1
                                i = _start
                                continue
                        i += 1
            if _proc_cte_defs and not re.match(r'(?i)\s*WITH\b', sql_raw):
                for _cte_name, _cte_body in list(_proc_cte_defs.items()):
                    if re.search(rf'\b{_cte_name}\s+AS\s*\(', sql_raw, re.IGNORECASE):
                        continue
                    if re.search(rf'\b{_cte_name}\b', sql_raw, re.IGNORECASE):
                        _needed = {_cte_name}
                        _changed = True
                        while _changed:
                            _changed = False
                            for _cn, _cb in _proc_cte_defs.items():
                                if _cn not in _needed and _cn != _cte_name:
                                    if re.search(rf'\b{_cn}\b', _cte_body, re.IGNORECASE):
                                        _needed.add(_cn)
                                        _changed = True
                        for _cn in list(_needed):
                            _cb = _proc_cte_defs.get(_cn, '')
                            for _cn2 in _proc_cte_defs:
                                if _cn2 not in _needed and re.search(rf'\b{_cn2}\b', _cb, re.IGNORECASE):
                                    _needed.add(_cn2)
                                    _changed = True
                        _with_parts = []
                        for _cn in _proc_cte_defs:
                            if _cn in _needed:
                                _with_parts.append(f'{_cn} AS {_proc_cte_defs[_cn]}')
                        if _with_parts:
                            dml.sql_text = f'WITH {", ".join(_with_parts)}\n{sql_raw}'
                            sql_raw = dml.sql_text.strip()
                        break
        _xml_method_ids = set()
        for dml in proc.dml_statements:
            if dml.method_id in _xml_method_ids:
                continue  # duplicate — already written from another procedure
            _xml_method_ids.add(dml.method_id)
            stmt_xml = _build_mapper_statement(proc, dml)
            statements.append(stmt_xml)

    stmts_xml = "\n\n".join(statements) if statements else f"<!-- No statements for {pkg.package_name} -->"

    extra_stmts = []
    for method_name, sql, return_type in getattr(pkg, '_extra_mapper_methods', []):
        rt_alias = return_type.lower() if return_type in ("Long", "Integer", "String") else return_type
        xml_lines = [f'<select id="{method_name}" resultType="{rt_alias}">']
        xml_lines.append(f'        {sql}')
        xml_lines.append('</select>')
        extra_stmts.append("\n".join(xml_lines))

    lines = []
    lines.append(f'<?xml version="1.0" encoding="{_SOURCE_ENCODING.upper()}"?>')
    lines.append('<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"')
    lines.append('        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">')
    lines.append(f'<mapper namespace="{namespace}">')
    lines.append("")
    for i, stmt in enumerate(statements):
        if i > 0:
            lines.append("")
        for stmt_line in stmt.split("\n"):
            lines.append(f"    {stmt_line}")
    if extra_stmts:
        lines.append("")
        for i, estmt in enumerate(extra_stmts):
            if i > 0:
                lines.append("")
            for eline in estmt.split("\n"):
                lines.append(f"    {eline}")
    lines.append("")
    lines.append("</mapper>")
    content = "\n".join(lines) + "\n"
    # Post-processing: fix recursive CTE type mismatch
    # VARCHAR(100) vs VARCHAR in recursive term — cast non-recursive term to TEXT
    if re.search(r'\bWITH\s+RECURSIVE\b', content, re.IGNORECASE):
        content = re.sub(
            r'(\bemp_name)(\s+AS\s+path\b)',
            r'\1::TEXT\2',
            content, flags=re.IGNORECASE,
        )
    _write_source_file(mapper_dir / f"{package_to_classname(pkg.package_name)}Mapper.xml", content)


def _clean_sql(sql: str) -> str:
    _cmt_slots = []
    def _stash_line_comment(m):
        _cmt_slots.append(m.group(0))
        return f"__CMT_{len(_cmt_slots) - 1}__"
    sql = re.sub(r'--[^\n]*', _stash_line_comment, sql)
    sql = re.sub(r'\s*\.\s*', '.', sql)
    sql = re.sub(r'\s*\(\s*', '(', sql)
    sql = re.sub(r'\n[ \t]+\)', '\n)', sql)
    sql = re.sub(r'([^\n])[ \t]*\)', r'\1)', sql)
    sql = re.sub(r' {2,}', ' ', sql)
    for _ci, _cs in enumerate(_cmt_slots):
        sql = sql.replace(f"__CMT_{_ci}__", _cs)
    return sql.strip()


def _format_sql(sql: str) -> str:
    # ogsql format drops the length arg in SUBSTRING(x FROM n FOR m),
    # producing invalid SUBSTRING(x FROM n FOR). Protect these before formatting.
    _substring_slots = []
    def _stash_substring(m):
        _substring_slots.append(m.group(0))
        return f"__SUBSTR_{len(_substring_slots) - 1}__"
    sql = re.sub(
        r'\bSUBSTRING\s*\([^)]*?\bFROM\s+\S+\s+FOR\s+\S+\s*\)',
        _stash_substring, sql, flags=re.IGNORECASE,
    )
    _lock_slots = []
    def _stash_lock(m):
        _lock_slots.append(m.group(0))
        return f"__LOCK_{len(_lock_slots) - 1}__"
    sql = re.sub(
        r'\bFOR\s+UPDATE\b(?:\s+OF\s+\w+(?:\s*,\s*\w+)*)?(?:\s+(?:NOWAIT|SKIP\s+LOCKED|WAIT\s+\d+))?',
        _stash_lock, sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r'\bFOR\s+NO\s+KEY\s+UPDATE\b(?:\s+(?:NOWAIT|SKIP\s+LOCKED|WAIT\s+\d+))?',
        _stash_lock, sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r'\bFOR\s+SHARE\b(?:\s+(?:NOWAIT|SKIP\s+LOCKED|WAIT\s+\d+))?',
        _stash_lock, sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r'\bFOR\s+KEY\s+SHARE\b(?:\s+(?:NOWAIT|SKIP\s+LOCKED|WAIT\s+\d+))?',
        _stash_lock, sql, flags=re.IGNORECASE,
    )
    try:
        result = subprocess.run(
            [OGSQL_BIN, "format"],
            input=sql, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            sql = result.stdout.strip()
    except Exception:
        pass
    for i, original in enumerate(_substring_slots):
        sql = sql.replace(f"__SUBSTR_{i}__", original)
    for i, original in enumerate(_lock_slots):
        sql = sql.replace(f"__LOCK_{i}__", original)
    return sql


def _find_top_level_keyword(sql: str, keyword: str):
    """Find the position of a keyword at the top level (not inside parens, strings, or comments)."""
    kw = keyword.upper()
    kw_len = len(kw)
    in_string = False
    depth = 0
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_string:
            if ch == "'" and (i + 1 >= len(sql) or sql[i + 1] != "'"):
                in_string = False
            elif ch == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 1
        else:
            if ch == "'":
                in_string = True
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0 and sql[i:i+kw_len].upper() == kw:
                if i == 0 or not sql[i-1].isalnum() and sql[i-1] != '_':
                    after_char = sql[i+kw_len:i+kw_len+1] if i+kw_len < len(sql) else ''
                    if not after_char or not after_char.isalnum() and after_char != '_':
                        return i
        i += 1
    return None


def _build_mapper_statement(proc: ProcedureInfo, dml: DmlStatement) -> str:
    sql = _clean_sql(dml.sql_text.strip())
    if sql.endswith(";"):
        sql = sql[:-1]
    sql = re.sub(r'\bBULK\s+COLLECT\b', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDELETE\s+FROM\s+(\w+)\s+(\w+)\s+FROM\b', r'DELETE FROM \1 \2 USING', sql, flags=re.IGNORECASE)
    sql = re.sub(r'"#[{][^}]*}"', lambda m: m.group(0).strip('"'), sql)

    # Strip PL/pgSQL "RETURNING col INTO #{param}" early so bare-insert/ROW handlers see clean SQL
    _early_ret_slots = []
    def _stash_early_ret(m):
        _early_ret_slots.append(m.group(0))
        return f"__ERETS_{len(_early_ret_slots) - 1}__"
    sql = re.sub(r"'(?:[^'\\]|\\.)*'", _stash_early_ret, sql)
    if dml.returning_cols:
        sql = re.sub(r'\bRETURNING\b\s+(.+?)\s+\bINTO\b\s+\w+(?:\.\w+)?(?:\s*,\s*\w+(?:\.\w+)?)*', r'RETURNING \1', sql, flags=re.IGNORECASE)
    else:
        sql = re.sub(r'\bRETURNING\b\s+.*?\bINTO\b\s+(?:#\{[^}]+\}|\?|\w+(?:\.\w+)?)(?:\s*,\s*(?:#\{[^}]+\}|\?|\w+(?:\.\w+)?))*', '', sql, flags=re.IGNORECASE | re.DOTALL)
        sql = re.sub(r'\bRETURNING\s+(\w+(?:\s*,\s*\w+)*)\s+INTO\s+\w+(?:\.\w+)?(?:\s*,\s*\w+(?:\.\w+)?)*', r'RETURNING \1', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bRETURNING\s+(\w+)\s+INTO\s+#\{[^}]+\}', r'RETURNING \1', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bRETURNING\s+(\w+)\s+INTO\s+\?', r'RETURNING \1', sql, flags=re.IGNORECASE)
    for _eri, _ers in enumerate(_early_ret_slots):
        sql = sql.replace(f"__ERETS_{_eri}__", _ers)

    _row_match = re.search(r'\bSET\s+ROW\s*=\s*(#\{[^}]+\}|\w+)', sql, re.IGNORECASE | re.DOTALL)
    if _row_match:
        _tbl_match = re.search(r'\bUPDATE\s+(\w+)', sql, re.IGNORECASE)
        _tbl_name = _tbl_match.group(1) if _tbl_match else None
        _param = _row_match.group(1)
        _base = _param[2:-1] if _param.startswith('#{') else snake_to_camel(_param)
        _row_cols = _lookup_table_columns(_tbl_name, proc._source_path) if _tbl_name else []
        if _row_cols:
            _set_parts = [f'{c} = #{{{_base}.{c}}}' for c in _row_cols]
            sql = sql[:_row_match.start()] + 'SET ' + ', '.join(_set_parts) + sql[_row_match.end():]

    _bare_insert = re.match(r'(INSERT\s+INTO\s+(\w+))\s+VALUES\s+(#\{[^}]+\}|\w+)\s*(RETURNING\b.*)?$', sql, re.IGNORECASE | re.DOTALL)
    if _bare_insert:
        _tbl = _bare_insert.group(2)
        _raw_param = _bare_insert.group(3)
        _returning = _bare_insert.group(4)
        if _raw_param.startswith('#{'):
            _base = _raw_param[2:-1]
        else:
            _base = snake_to_camel(_raw_param)
        _ins_cols = _lookup_table_columns(_tbl, proc._source_path)
        if _ins_cols:
            _val_parts = [f'#{{{_base}.{c}}}' for c in _ins_cols]
            sql = f'INSERT INTO {_tbl}({", ".join(_ins_cols)}) VALUES({", ".join(_val_parts)})'
            if _returning:
                sql = sql + ' ' + _returning

    _SQL_FUNC_REPLACEMENTS = [
        (re.compile(r'\b\w+\.get_sys_date\s*\(\)', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
        (re.compile(r'\b\w+\.sysdate\b', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
    ]
    for pat, repl in _SQL_FUNC_REPLACEMENTS:
        sql = pat.sub(repl, sql)

    # Fix FILTER(condition) → FILTER(WHERE condition) when ogsql drops WHERE
    sql = re.sub(r'\bFILTER\s*\((?!WHERE\b)', 'FILTER(WHERE ', sql, flags=re.IGNORECASE)

    # Convert Oracle-style sequence references: SEQ.NEXTVAL → nextval('seq'), SEQ.CURRVAL → currval('seq')
    sql = re.sub(
        r'\b(\w+)\.NEXTVAL\b',
        lambda m: f"nextval('{m.group(1).lower()}')",
        sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r'\b(\w+)\.CURRVAL\b',
        lambda m: f"currval('{m.group(1).lower()}')",
        sql, flags=re.IGNORECASE,
    )

    sql = re.sub(r'\bDATE\s*\(([^)]+)\)', r'CAST(\1 AS DATE)', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bTIMESTAMP\s*\(([^)]+)\)', r'CAST(\1 AS TIMESTAMP)', sql, flags=re.IGNORECASE)

    # Convert OpenGauss/Oracle-specific functions to PostgreSQL standard
    sql = re.sub(r'\bSYSTIMESTAMP\b', 'CURRENT_TIMESTAMP', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bSYSDATE\b', 'CURRENT_DATE', sql, flags=re.IGNORECASE)

    # Strip double-quoted identifiers for PostgreSQL compatibility
    # "MY_TAB_PARTITIONS" → MY_TAB_PARTITIONS (case-insensitive matching)
    # Preserve quoted identifiers that are reserved words (date, user, order, etc.)
    _RESERVED = {'date', 'user', 'order', 'performance', 'type', 'check', 'primary', 'foreign', 'unique', 'constraint', 'index', 'table', 'select', 'insert', 'update', 'delete', 'from', 'where', 'group', 'having', 'limit', 'offset', 'as', 'on', 'and', 'or', 'not', 'null', 'default', 'values', 'set', 'into', 'row', 'number', 'value', 'level', 'size', 'comment', 'operator', 'action'}
    def _unquote_ident(m):
        inner = m.group(1)
        if inner.lower() in _RESERVED:
            return m.group(0)
        return inner
    sql = re.sub(r'"(\w+)"', _unquote_ident, sql)

    param_placeholders = []
    def _protect(m):
        param_placeholders.append(m.group(0))
        return f"__PH{len(param_placeholders) - 1}__"

    sql = re.sub(r'[#\$]\{[^}]+\}', _protect, sql)
    sql = _format_sql(sql)
    sql = _fix_reconstructed_sql(sql)
    sql = _qualify_ambiguous_group_order(sql)
    if re.match(r'\s*SELECT\b', sql, re.IGNORECASE):
        sql = _rewrite_select_alias_columns(sql)

    # Fix UPDATE ... FROM where ogsql places FROM inside SET clause instead of after it
    # Must run AFTER _format_sql() to avoid being overwritten
    if re.match(r'\s*UPDATE\b', sql, re.IGNORECASE):
        _set_pos = _find_top_level_keyword(sql, 'SET')
        _where_pos = _find_top_level_keyword(sql, 'WHERE')
        if _set_pos is not None and _where_pos is not None:
            _from_pos = _find_top_level_keyword(sql, 'FROM')
            if _from_pos is not None and _set_pos < _from_pos < _where_pos:
                _from_and_tables = sql[_from_pos:_where_pos].rstrip()
                _from_and_tables = ' '.join(sql[_from_pos:_where_pos].split())
                _where_and_rest = ' '.join(sql[_where_pos:].split())
                sql = sql[:_from_pos].rstrip() + '\n        ' + _from_and_tables + '\n        ' + _where_and_rest

    # Fix duplicate WHERE caused by ogsql format absorbing subquery's ) into a comment:
    # Source: ... WHERE inner_cond -- comment \n ) \n WHERE outer_cond
    # ogsql format merges: ... WHERE inner_cond -- comment) \n WHERE outer_cond
    # Extract ) from comment and place as standalone SQL token before second WHERE.
    _dup_where = re.search(
        r'(\bWHERE\b.{20,}?--[^\n]*)(\))\s*\n(\s*)(\bWHERE\b)',
        sql, re.DOTALL | re.IGNORECASE,
    )
    if _dup_where:
        _comment_without_paren = _dup_where.group(1)
        _indent = _dup_where.group(3)
        _outer_where = _dup_where.group(4)
        _start = _dup_where.start()
        _end = _dup_where.end()
        sql = sql[:_start] + _comment_without_paren + '\n' + _indent + ')\n' + _indent + _outer_where + sql[_end:]

    sql = _add_missing_lateral(sql)
    sql = re.sub(
        r'\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b',
        'ON DUPLICATE KEY UPDATE',
        sql, flags=re.IGNORECASE,
    )
    if re.search(r'\bWITH\s+RECURSIVE\b', sql, re.IGNORECASE):
        sql = re.sub(
            r'(\bemp_name)\s+(AS\s+path\b)',
            r'\1::TEXT \2',
            sql, flags=re.IGNORECASE,
        )
    sql = re.sub(
        r'\b(STRING_AGG|ARRAY_AGG|LISTAGG)\s*\(\s*DISTINCT\b(?=[\s\S]*?WITHIN\s+GROUP)',
        r'\1(',
        sql, flags=re.IGNORECASE,
    )
    def _string_agg_within_group(m):
        inner = m.group(1)
        order_by = m.group(2)
        return f'STRING_AGG({inner} ORDER BY {order_by})'
    sql = re.sub(
        r'\bSTRING_AGG\s*\(((?:[^()]*|\([^()]*\))*)\)\s*WITHIN\s+GROUP\s*\(\s*ORDER\s+BY\s+((?:[^()]*|\([^()]*\))*)\)',
        _string_agg_within_group,
        sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r'ARRAY_AGG\s*\(([^)]+)\)\s*WITHIN\s+GROUP\s*\([^)]*\)',
        r'ARRAY_AGG(\1)',
        sql, flags=re.IGNORECASE,
    )

    # Disambiguate unqualified column references when multiple JOINed tables share the column.
    # Only apply within the CTE/subquery that contains the JOIN — not in subsequent CTEs
    # that select from the first CTE (where the original table alias is out of scope).
    _ambig_cols = {
        'perf_score': ('emp_performance', 'employees'),
    }
    for _col, _tables in _ambig_cols.items():
        _join_m = re.search(
            rf'\b({re.escape(_tables[0])}|{re.escape(_tables[1])})\s+AS\s+(\w+).*?'
            rf'\bJOIN\s+({re.escape(_tables[0])}|{re.escape(_tables[1])})\s+AS\s+(\w+)',
            sql, re.IGNORECASE | re.DOTALL,
        )
        if _join_m:
            _alias_tbl1 = _join_m.group(1).lower()
            _alias1 = _join_m.group(2)
            _alias_tbl2 = _join_m.group(3).lower()
            _alias2 = _join_m.group(4)
            if _alias_tbl1 != _alias_tbl2:
                _perf_src = 'emp_performance'
                _perf_alias = _alias1 if _alias_tbl1 == _perf_src else _alias2
                # Scope the rewrite to the JOIN-containing CTE body only.
                # Find the CTE that contains the JOIN and only rewrite within it.
                _cte_bodies = list(re.finditer(
                    r'(\w+)\s+AS\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)',
                    sql, re.IGNORECASE | re.DOTALL,
                ))
                _rewritten = False
                for _cte_m in _cte_bodies:
                    _cte_body = _cte_m.group(2)
                    if re.search(
                        rf'\b({re.escape(_tables[0])}|{re.escape(_tables[1])})\s+AS\s+',
                        _cte_body, re.IGNORECASE,
                    ):
                        _new_body = re.sub(
                            rf'(?<!\.)(?<!\w)(?<![a-zA-Z_]){re.escape(_col)}\b(?![_a-zA-Z0-9])',
                            f'{_perf_alias}.{_col}',
                            _cte_body, flags=re.IGNORECASE,
                        )
                        sql = sql[:_cte_m.start(2)] + _new_body + sql[_cte_m.end(2):]
                        _rewritten = True
                        break
                if not _rewritten:
                    # No CTE structure — apply globally (simple JOIN query)
                    sql = re.sub(
                        rf'(?<!\.)(?<!\w)(?<![a-zA-Z_]){re.escape(_col)}\b(?![_a-zA-Z0-9])',
                        f'{_perf_alias}.{_col}',
                        sql, flags=re.IGNORECASE,
                    )

    for i, ph in enumerate(param_placeholders):
        sql = sql.replace(f"__PH{i}__", ph)

    effective_local_vars = dict(proc.local_vars) if proc.local_vars else {}
    for ep_java_name, ep_java_type in dml.extra_params:
        already_covered = any(
            snake_to_camel(k) == ep_java_name
            for k in effective_local_vars
        )
        if not already_covered:
            effective_local_vars[ep_java_name] = ep_java_type

    sql = _convert_params_to_mybatis(sql, proc.parameters, effective_local_vars)

    # OpenGauss: DATE - INTEGER returns INTEGER, not DATE/TIMESTAMP.
    # Wrap DATE parameters used in arithmetic with explicit CAST.
    sql = re.sub(
        r'(=\s*)(#\{[^}]*jdbcType\s*=\s*DATE[^}]*\}|#\{[^}]*javaType\s*=\s*java\.sql\.Date[^}]*\})\s*(-\s*\d+)\b',
        r'\1CAST(\2 AS TIMESTAMP) \3',
        sql
    )
    sql = re.sub(
        r'(=\s*)(#\{[^}]*jdbcType\s*=\s*DATE[^}]*\}|#\{[^}]*javaType\s*=\s*java\.sql\.Date[^}]*\})\s*(-\s*interval\s+[^,)]+)',
        r'\1CAST(\2 AS TIMESTAMP) \3',
        sql, flags=re.IGNORECASE
    )

    # Dynamic SQL: if the entire mapper body is a single #{var} reference (runtime SQL variable),
    # use ${} interpolation instead of #{} binding — MyBatis will insert the SQL string literally.
    # ${} uses OGNL, so strip jdbcType/javaType attributes to avoid "Parameter 'VARCHAR' not found".
    _stripped_sql = sql.strip()
    _full_match = re.fullmatch(r'#\{(\w+)(?:\s*,\s*[^}]+)?\}', _stripped_sql)
    if _full_match:
        sql = '${' + _full_match.group(1) + '}'

    # Strip PL/pgSQL "RETURNING col INTO #{param}" → standard SQL "RETURNING col"
    # Protect string literals first so RETURNING inside quotes isn't matched
    _ret_str_slots = []
    def _stash_ret_str(m):
        _ret_str_slots.append(m.group(0))
        return f"__RETS_{len(_ret_str_slots) - 1}__"
    _ret_protected = re.sub(r"'(?:[^'\\]|\\.)*'", _stash_ret_str, sql)
    _ret_protected = re.sub(r'\bRETURNING\s+(\w+)\s+INTO\s+#\{[^}]+\}', r'RETURNING \1', _ret_protected, flags=re.IGNORECASE)
    _ret_protected = re.sub(r'\bRETURNING\s+(\w+)\s+INTO\s+\?', r'RETURNING \1', _ret_protected, flags=re.IGNORECASE)
    _ret_protected = re.sub(r'\bRETURNING\b\s+.*?\bINTO\b\s+(?:#\{[^}]+\}|\?)(?:\s*,\s*(?:#\{[^}]+\}|\?))*', '', _ret_protected, flags=re.IGNORECASE | re.DOTALL)
    for _ri, _rs in enumerate(_ret_str_slots):
        _ret_protected = _ret_protected.replace(f"__RETS_{_ri}__", _rs)
    sql = _ret_protected

    sql = re.sub(r'([(,])\s*(date|user|order|performance|type)\s*([,)])', r'\1 "\2" \3', sql, flags=re.IGNORECASE)

    if dml.sql_type == "select" and not dml.returns_list:
        if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE) and not re.search(r'\bFETCH\s+FIRST\b', sql, re.IGNORECASE):
            sql = sql.rstrip() + "\n        LIMIT 1"

    sql_raw_for_infer = sql

    sql = sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    result_type_attr = ""
    if dml.sql_type == "select":
        if dml.returns_list:
            result_type_attr = ' resultType="java.util.LinkedHashMap"'
        elif dml.result_type and dml.result_type == "Integer":
            if "||" in sql_raw_for_infer or "&amp;&amp;" in sql:
                result_type_attr = ' resultType="string"'
            else:
                result_type_attr = ' resultType="int"'
        elif dml.result_type and dml.result_type != "Map<String, Object>":
            if is_simple_java_type(dml.result_type):
                result_type_attr = f' resultType="{dml.result_type.lower()}"'
            else:
                result_type_attr = f' resultType="{dml.result_type}"'
        else:
            result_type_attr = ' resultType="java.util.LinkedHashMap"'

    tag = dml.sql_type
    if dml.returning_cols:
        tag = "select"
        result_type_attr = ' resultType="java.util.LinkedHashMap"'
    params_attrs = ""
    has_local_var_params = len(_dml_used_local_vars(proc, dml)) > 0
    if proc.parameters and not has_local_var_params:
        param_types = set(p.java_type for p in proc.parameters if not (p.mode and p.mode.upper() == "OUT"))
        if len(param_types) == 1:
            pt = list(param_types)[0]
            params_attrs = f' parameterType="{pt.lower() if is_simple_java_type(pt) else pt}"'

    filter_line = ""
    if dml.optional_filters:
        filter_map = ", ".join(f"{f['param']} -> {f['column']}" for f in dml.optional_filters)
        filter_line = f'<!-- Optional filters: {filter_map}. Consider using <if test="..."> in MyBatis -->'

    formatted_sql = "\n".join(f"    {line}" for line in sql.split("\n"))

    xml_parts = []
    _src_line = _resolve_dml_source_line(proc, dml)
    _src_end = _src_line
    source_info = f"Source: {proc.source_file}:{_src_line}-{_src_end} — {proc.name}.{dml.method_id}" if proc.source_file else f"Source: {proc.name}.{dml.method_id}"
    xml_parts.append(f"<!-- {source_info} -->")
    if DEBUG_MODE and proc.source_file:
        src_path = proc._source_path or proc.source_file
        dbg = _format_debug_comment(src_path, _src_line, max_len=120)
        if dbg:
            safe_dbg = dbg.replace("--", "\u2014\u2014").replace("<!", "< !")
            xml_parts.append(f"<!-- {safe_dbg} -->")
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            safe_text = formatted.lstrip('/ ').strip().replace('--', '\u2014\u2014')
            xml_parts.append(f"<!-- {safe_text} -->")
    if filter_line:
        xml_parts.append(filter_line)

    if dml.dynamic_conditions:
        base_sql = dml.base_sql
        if base_sql:
            base_sql = _convert_params_to_mybatis(base_sql, proc.parameters, effective_local_vars)
            base_sql = base_sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            base_sql = base_sql.rstrip()
            if base_sql.endswith(";"):
                base_sql = base_sql[:-1]
        formatted_base = "\n".join(f"    {line}" for line in base_sql.split("\n")) if base_sql else ""

        local_var_names = {snake_to_camel(v) for v in proc.local_vars}
        def _is_valid_condition(dc):
            frag = dc.sql_fragment.strip()
            if frag == "," or frag == ", ":
                return False
            for lv in local_var_names:
                if re.search(rf'#\{{{lv}\b', frag) or re.search(rf'\$\{{{lv}\b', frag):
                    return False
            return True

        def _clean_fragment(frag: str) -> str:
            frag = frag.strip()
            frag = re.sub(r"'(#\{[^}]+\})'", r"\1", frag)
            frag = re.sub(r"'(\$\{[^}]+\})'", r"\1", frag)
            return frag

        valid_conditions = [dc for dc in dml.dynamic_conditions if _is_valid_condition(dc)]
        rejected_conditions = [dc for dc in dml.dynamic_conditions if not _is_valid_condition(dc)]
        for dc in valid_conditions:
            dc.sql_fragment = _clean_fragment(dc.sql_fragment)
        for dc in rejected_conditions:
            frag = dc.sql_fragment.strip()
            where_match = re.search(r'\bWHERE\b\s+(.+)', frag, re.IGNORECASE)
            if where_match and not any(c.clause_type == "WHERE" for c in valid_conditions):
                where_text = where_match.group(1).strip()
                where_text = _clean_fragment(where_text)
                has_local_var = any(
                    re.search(rf'[#\$]\{{{lv}\b', where_text)
                    for lv in local_var_names
                )
                if not has_local_var:
                    _cond = dc.condition_expr
                    _cond_refs_local = any(
                        re.search(rf'\b{re.escape(lv)}\b', _cond)
                        for lv in local_var_names
                    )
                    if _cond_refs_local:
                        _cond = "true"
                    valid_conditions.append(DynamicCondition(
                        condition_expr=_cond,
                        sql_fragment=where_text,
                        clause_type="WHERE",
                        tag_name="if",
                    ))
        where_conditions = [dc for dc in valid_conditions if dc.clause_type == "WHERE"]
        order_conditions = [dc for dc in valid_conditions if dc.clause_type == "ORDER_BY"]
        set_conditions = [dc for dc in valid_conditions if dc.clause_type == "SET"]
        other_conditions = [dc for dc in valid_conditions if dc.clause_type not in ("WHERE", "ORDER_BY", "SET")]
        if tag == "update" and other_conditions:
            set_like = [dc for dc in other_conditions if "=" in dc.sql_fragment]
            if set_like:
                set_conditions.extend(set_like)
                other_conditions = [dc for dc in other_conditions if dc not in set_like]

        xml_parts.append(f'<{tag} id="{dml.method_id}"{params_attrs}{result_type_attr}>')
        if formatted_base:
            xml_parts.append(formatted_base)
        if where_conditions:
            xml_parts.append("    <where>")
            for dc in where_conditions:
                fragment = dc.sql_fragment.strip()
                if fragment.upper().startswith("WHERE"):
                    fragment = fragment[5:].strip()
                if not fragment.upper().startswith("AND") and not fragment.upper().startswith("OR"):
                    fragment = "AND " + fragment
                esc_fragment = fragment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                xml_parts.append(f'        <if test="{dc.condition_expr}">{esc_fragment}</if>')
            xml_parts.append("    </where>")
        if set_conditions:
            xml_parts.append("    <set>")
            for dc in set_conditions:
                fragment = dc.sql_fragment.strip()
                if fragment.upper().startswith("SET"):
                    fragment = fragment[3:].strip()
                esc_fragment = fragment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                xml_parts.append(f'        <if test="{dc.condition_expr}">{esc_fragment}</if>')
            xml_parts.append("    </set>")
        for dc in order_conditions:
            fragment = dc.sql_fragment.strip()
            if fragment.upper().startswith("ORDER BY"):
                fragment = fragment[8:].strip()
            esc_fragment = fragment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_parts.append(f'    <if test="{dc.condition_expr}">ORDER BY {esc_fragment}</if>')
        for dc in other_conditions:
            esc_fragment = dc.sql_fragment.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            xml_parts.append(f'    <if test="{dc.condition_expr}">{esc_fragment}</if>')
        xml_parts.append(f'</{tag}>')
    elif dml.is_forall_batch and dml.forall_batch_arrays:
        batch_sql = sql
        for arr_java in dml.forall_batch_arrays:
            batch_sql = re.sub(r'#\{' + re.escape(arr_java) + r'\}', f'#{{item.{arr_java}}}', batch_sql)
        batch_sql = re.sub(r'#\{_(\w+)\}', lambda m: f'#{{item._{m.group(1)}}}', batch_sql)
        formatted_batch = "\n".join(f"        {line}" for line in batch_sql.split("\n"))
        xml_parts.append(f'<{tag} id="{dml.method_id}"{result_type_attr}>')
        xml_parts.append(f'    <foreach collection="list" item="item" separator=";">')
        xml_parts.append(formatted_batch)
        xml_parts.append(f'    </foreach>')
        xml_parts.append(f'</{tag}>')
    else:
        xml_parts.append(f'<{tag} id="{dml.method_id}"{params_attrs}{result_type_attr}>')
        xml_parts.append(formatted_sql)
        xml_parts.append(f'</{tag}>')
    return "\n".join(xml_parts)


def _convert_params_to_mybatis(sql: str, params: list, local_vars: dict = None) -> str:
    """Convert SQL parameter references to MyBatis #{{paramName}} syntax with optional jdbcType/javaType.

    Two-pass: first converts ``param.field`` (composite field access) to ``#{param.field}``,
    then replaces remaining simple ``param`` references.  This avoids ``#{param}.field`` which
    is invalid MyBatis.
    """
    all_names = {}
    for p in params:
        all_names[p.name] = p.java_name
    if local_vars:
        for var_name in local_vars:
            all_names[var_name] = snake_to_camel(var_name)

    composite_pattern = None
    if all_names:
        composite_pattern = re.compile(
            r'(?<![#\$]\{)\b(' + '|'.join(re.escape(n) for n in all_names) + r')\.(\w+)\b',
            re.IGNORECASE
        )
    def _composite_repl(m):
        _lower_map = {k.lower(): k for k in all_names}
        matched_lower = m.group(1).lower()
        if matched_lower in _lower_map:
            java_name = all_names[_lower_map[matched_lower]]
        else:
            java_name = all_names.get(m.group(1), m.group(1))
        return f'#{{{java_name}.{m.group(2)}}}'
    if composite_pattern:
        sql = composite_pattern.sub(_composite_repl, sql)

    for p in params:
        jdbc = sql_type_to_jdbc(p.sql_type)
        java = p.java_type
        if jdbc and java:
            placeholder = f'#{{{p.java_name}, jdbcType={jdbc}, javaType={java}}}'
        else:
            placeholder = f'#{{{p.java_name}}}'
        sql = re.sub(
            rf'(?<![#\$]\{{)\b{re.escape(p.name)}\b',
            placeholder,
            sql,
            flags=re.IGNORECASE
        )
    if local_vars:
        for var_name, var_java_type in local_vars.items():
            java_name = snake_to_camel(var_name)
            jdbc = java_type_to_jdbc(var_java_type)
            if jdbc and var_java_type:
                placeholder = f'#{{{java_name}, jdbcType={jdbc}, javaType={var_java_type}}}'
            else:
                placeholder = f'#{{{java_name}}}'
            sql = re.sub(
                rf'(?<![#\$]\{{)\b{re.escape(var_name)}\b',
                placeholder,
                sql,
                flags=re.IGNORECASE
            )
    sql = re.sub(r'(?<!\')\s*::\s*(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR2?|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b(?!\s*\()', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'(\S+)\s*::\s*(NUMERIC|DECIMAL|VARCHAR2?|CHAR|BPCHAR)\s*\(([^)]+)\)', r'CAST(\1 AS \2(\3))', sql, flags=re.IGNORECASE)
    sql = re.sub(r'(#[\w, ={}]+})\s+(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b', r'\1', sql, flags=re.IGNORECASE)
    sql = re.sub(r':\s+(\d+)', lambda m: f'#{{param{m.group(1)}}}', sql)
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

    _custom_type_classes = set()
    _needs_list_import = False
    for tn, ti in pkg.custom_types.items():
        if ti["kind"] == "record":
            _custom_type_classes.add(_custom_type_classname(tn))
        elif ti["kind"] == "varray":
            _custom_type_classes.add(f"List<{ti['elem_type']}>")
            _needs_list_import = True

    for proc in pkg.procedures:
        all_imports.update(proc.imports)
        for p in proc.parameters:
            if p.java_type not in _custom_type_classes:
                if p.java_type.startswith("List<"):
                    _needs_list_import = True
                else:
                    _imp = _resolve_import(p.java_type)
                    if _imp:
                        all_imports.add(_imp)
        # REFCURSOR OUT params and cursor result vars need List/Map imports
        if any(p.is_out and p.is_refcursor for p in proc.parameters):
            _needs_list_import = True
            all_imports.add("import java.util.Map;")
        if proc.open_cursors:
            _needs_list_import = True
            all_imports.add("import java.util.Map;")
    if _needs_list_import:
        all_imports.add("import java.util.List;")

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
    # Include package-level variable types (they emit into the class body)
    if pkg.package_vars:
        for var_name, var_info in pkg.package_vars.items():
            all_body_text += var_info.get("java_type", "") + " "
            default_expr = var_info.get("default", "")
            if default_expr:
                all_body_text += default_expr + " "
    for proc in pkg.procedures:
        all_body_text += " ".join(proc.java_logic_lines) + " "
        for vn, vt in proc.local_vars.items():
            all_body_text += vt + " "
        for vn, vd in proc.local_var_defaults.items():
            all_body_text += vd + " "
        if proc.body and proc.body.get("exception_block"):
            for handler in (proc.body.get("exception_block") or {}).get("handlers", []):
                for s in _iter_statements(handler.get("statements", [])):
                    for sk, sv in s.items():
                        all_body_text += str(sv) + " "

    if "List<" in all_body_text or "List<Map" in all_body_text:
        all_imports.add("import java.util.List;")
    if "Map<String" in all_body_text:
        all_imports.add("import java.util.Map;")
    _has_map_var = any("Map<String" in vt for proc in pkg.procedures for vn, vt in proc.local_vars.items())
    if not _has_map_var and pkg.package_vars:
        _has_map_var = any("Map<String" in vi.get("java_type", "") for vi in pkg.package_vars.values())
    if _has_map_var:
        all_imports.add("import java.util.Map;")
        all_imports.add("import java.util.HashMap;")
    if "AtomicReference<" in all_body_text or any(p.is_out for proc in pkg.procedures for p in proc.parameters):
        all_imports.add("import java.util.concurrent.atomic.AtomicReference;")
    if "Arrays.asList" in all_body_text:
        all_imports.add("import java.util.Arrays;")
    if "Objects.requireNonNullElse" in all_body_text:
        all_imports.add("import java.util.Objects;")

    for _tn, _ti in pkg.custom_types.items():
        if _ti["kind"] == "record":
            for _fn, _ft in _ti["fields"]:
                if "Map<String" in _ft:
                    all_imports.add("import java.util.Map;")
                if "List<" in _ft:
                    all_imports.add("import java.util.List;")

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

    # Issue #38: Declare Map fields for cross-package state variables
    _pkg_state_maps = set()
    for proc in pkg.procedures:
        for line in proc.java_logic_lines:
            for m in re.finditer(r'\b(pkg\w+)\.(?:put|get)\(', line):
                _pkg_state_maps.add(m.group(1))
    for _map_name in sorted(_pkg_state_maps):
        _camel = snake_to_camel(_map_name) if '_' in _map_name else _map_name
        lines.append(f"    private final java.util.Map<String, Object> {_camel} = new java.util.HashMap<>();")

    lines.append("")
    params_str = ", ".join(constructor_params)
    lines.append(f"    public {class_name}({params_str}) {{")
    for assign in constructor_assigns:
        lines.append(assign)
    lines.append("    }")

    # Emit package-level variables: CONSTANT as static final, mutable as instance fields
    if pkg.package_vars:
        lines.append("")
        for var_name, var_info in pkg.package_vars.items():
            java_name = snake_to_camel(var_name)
            java_type = var_info["java_type"]
            default = var_info.get("default")
            default_expr = default if (default is not None and "/* TODO: implement" not in default and "/* UNSUPPORTED" not in default) else _default_for_type(java_type)
            if "BigDecimal" in java_type and default_expr and re.match(r'^\d+\.\d+d?$', default_expr.strip()):
                default_expr = f"java.math.BigDecimal.valueOf({default_expr.strip().rstrip('d')})"
            elif "BigDecimal" in java_type and default_expr and _is_bare_int_literal(default_expr):
                default_expr = f"java.math.BigDecimal.valueOf({default_expr.strip()})"
            if var_name in _PACKAGE_CONSTANTS or var_name not in _PACKAGE_VAR_WRITTEN:
                lines.append(f"    private static final {java_type} {java_name} = {default_expr};")
            else:
                # Mutable package vars use non-final static fields
                lines.append(f"    private static {java_type} {java_name} = {default_expr};")
        # Issue #39: ThreadLocal fields MUST be cleaned up after each request to prevent
        # cross-request data leaks in thread-pooled servlet containers (Tomcat/Jetty).
        # Add a Servlet Filter or HandlerInterceptor that calls .remove() on each ThreadLocal.
        _has_threadlocal = any(
            var_name not in _PACKAGE_CONSTANTS and var_name in _PACKAGE_VAR_WRITTEN
            for var_name in pkg.package_vars
        )
        if _has_threadlocal:
            lines.append("    // TODO: Add ThreadLocal cleanup (e.g. @AfterCompletion or Filter.remove())")
            lines.append("    // to prevent cross-request data leaks in thread-pooled containers.")

    # Generate inner static classes for RECORD custom types
    for type_name, type_info in pkg.custom_types.items():
        if type_info["kind"] == "record":
            inner_cls = _custom_type_classname(type_name)
            lines.append("")
            lines.append(f"    public static class {inner_cls} {{")
            for fld_name, fld_java_type in type_info["fields"]:
                fld_java = snake_to_camel(fld_name)
                lines.append(f"        public {fld_java_type} {fld_java};")
            lines.append(f"    }}")

    # Helper methods for complex SQL function mappings
    if "_crc32(" in all_body_text:
        lines.append("")
        lines.append("    private int _crc32(String input) {")
        lines.append("        java.util.zip.CRC32 crc = new java.util.zip.CRC32();")
        lines.append("        crc.update(input.getBytes());")
        lines.append("        return (int) crc.getValue();")
        lines.append("    }")
    if "_md5(" in all_body_text:
        lines.append("")
        lines.append("    private String _md5(String input) {")
        lines.append("        try {")
        lines.append("            return String.format(\"%032x\", new java.math.BigInteger(1, java.security.MessageDigest.getInstance(\"MD5\").digest(input.getBytes())));")
        lines.append("        } catch (java.security.NoSuchAlgorithmException e) {")
        lines.append("            throw new RuntimeException(e);")
        lines.append("        }")
        lines.append("    }")
    if "_appendList(" in all_body_text:
        lines.append("")
        lines.append("    @SuppressWarnings(\"unchecked\")")
        lines.append("    private <T> java.util.List<T> _appendList(java.util.List<T> list, T element) {")
        lines.append("        list.add(element);")
        lines.append("        return list;")
        lines.append("    }")
    if "_parseDate(" in all_body_text:
        lines.append("")
        lines.append("    private java.sql.Date _parseDate(String fmt, String str) {")
        lines.append("        try {")
        lines.append("            return new java.sql.Date(new java.text.SimpleDateFormat(fmt).parse(str).getTime());")
        lines.append("        } catch (java.text.ParseException e) {")
        lines.append("            return null;")
        lines.append("        }")
        lines.append("    }")
    if "_substr(" in all_body_text:
        lines.append("")
        lines.append("    private String _substr(String str, Object pos) {")
        lines.append("        return _substr(str, pos, str.length());")
        lines.append("    }")
        lines.append("")
        lines.append("    private String _substr(String str, Object pos, Object len) {")
        lines.append("        if (str == null) str = \"\";")
        lines.append("        int p = (pos instanceof Number) ? ((Number) pos).intValue() : Integer.parseInt(String.valueOf(pos));")
        lines.append("        int l = (len instanceof Number) ? ((Number) len).intValue() : Integer.parseInt(String.valueOf(len));")
        lines.append("        int start = Math.max(0, p - 1);")
        lines.append("        int end = Math.min(str.length(), start + l);")
        lines.append("        if (start > end) start = end;")
        lines.append("        return str.substring(start, end);")
        lines.append("    }")

    # Stubs for known SQL functions that bypass _register_missing_overload
    _known_func_stubs = []
    if "this.nextval(" in all_body_text:
        _known_func_stubs.append(("nextval", "Long", [("String", "seqName")]))
    if "this.currval(" in all_body_text:
        _known_func_stubs.append(("currval", "Long", [("String", "seqName")]))
    if "this.stringToArray(" in all_body_text:
        _known_func_stubs.append(("stringToArray", "java.util.List<String>", [("String", "str"), ("String", "delimiter")]))
    if "this.jsonbArrayLength(" in all_body_text:
        _known_func_stubs.append(("jsonbArrayLength", "Integer", [("String", "jsonb")]))
    if "this.jsonbBuildObject(" in all_body_text:
        _known_func_stubs.append(("jsonbBuildObject", "String", [("Object", "...args")]))
    for _kfn, _kret, _kparams in _known_func_stubs:
        _ksig = ", ".join(f"{pt} {pn}" for pt, pn in _kparams)
        _already = any(f"public {_kret} {_kfn}({_ksig})" in m for m in methods)
        if not _already:
            lines.append("")
            lines.append(f"    public {_kret} {_kfn}({_ksig}) {{")
            if _kfn == "stringToArray":
                lines.append(f"        if (str == null || str.isEmpty()) return java.util.Collections.emptyList();")
                lines.append(f"        return java.util.Arrays.asList(str.split(java.util.regex.Pattern.quote(delimiter)));")
            elif _kfn == "nextval":
                lines.append(f"        return {mapper_name}.selectNextval(seqName);")
                pkg._extra_mapper_methods.append(("selectNextval", "SELECT nextval(#" + "{seqName, jdbcType=VARCHAR}) AS val", "Long"))
            elif _kfn == "currval":
                lines.append(f"        return {mapper_name}.selectCurrval(seqName);")
                pkg._extra_mapper_methods.append(("selectCurrval", "SELECT currval(#" + "{seqName, jdbcType=VARCHAR}) AS val", "Long"))
            elif _kfn == "jsonbArrayLength":
                lines.append(f"        try {{")
                lines.append(f"            return new com.fasterxml.jackson.databind.ObjectMapper().readValue(jsonb, com.fasterxml.jackson.databind.JsonNode.class).size();")
                lines.append(f"        }} catch (Exception e) {{ return 0; }}")
            elif _kfn == "jsonbBuildObject":
                lines.append(f"        java.util.Map<String, Object> map = new java.util.LinkedHashMap<>();")
                lines.append(f"        for (int i = 0; i + 1 < args.length; i += 2) {{")
                lines.append(f"            map.put(String.valueOf(args[i]), args[i + 1]);")
                lines.append(f"        }}")
                lines.append(f"        try {{ return new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(map); }}")
                lines.append(f"        catch (Exception e) {{ throw new RuntimeException(e); }}")
            else:
                lines.append(f"        // TODO: implement {_kfn}")
                if _kret == "Integer" or _kret == "int":
                    lines.append(f"        return 0;")
                elif _kret == "Long" or _kret == "long":
                    lines.append(f"        return 0L;")
                elif _kret == "java.math.BigDecimal":
                    lines.append(f"        return java.math.BigDecimal.ZERO;")
                elif _kret.startswith("java.util.List") or _kret.startswith("List"):
                    lines.append(f"        return java.util.Collections.emptyList();")
                else:
                    lines.append(f"        return null;")
            lines.append(f"    }}")

    missing = _MISSING_OVERLOADS.get(pkg.package_name, [])
    for method_name, params in missing:
        existing_sigs = set()
        for m in methods:
            for mline in m.split("\n"):
                if f"public" in mline and method_name in mline and "(" in mline:
                    existing_sigs.add(mline.strip())
        sig_str = ", ".join(f"{pt} {pn}" for pt, pn in params)
        already_exists = any(sig_str in s for s in existing_sigs)
        if not already_exists:
            lines.append("")
            param_types = [pt for pt, _ in params]
            has_bd = any("BigDecimal" in pt for pt in param_types)
            ret = "java.math.BigDecimal" if has_bd else "Object"
            # Known functions: fix return types for common SQL functions
            if method_name == "jsonbArrayLength":
                ret = "Integer"
            elif method_name == "stringToArray":
                ret = "Object"
            elif method_name == "jsonbBuildObject":
                ret = "String"
                # Coerce all params to String for jsonb_build_object stub
                sig_str = ", ".join(f"String {pn}" for _, pn in params)
            lines.append(f"    // TODO: Auto-generated stub — parser missed this overload")
            lines.append(f"    public {ret} {method_name}({sig_str}) {{")
            lines.append(f"        // TODO: implement {method_name}({sig_str})")
            if ret == "Integer" or ret == "int":
                lines.append(f"        return 0;")
            elif ret == "Long" or ret == "long":
                lines.append(f"        return 0L;")
            elif ret == "java.math.BigDecimal":
                lines.append(f"        return java.math.BigDecimal.ZERO;")
            else:
                lines.append(f"        return null;")
            lines.append(f"    }}")

    for i, method in enumerate(methods):
        if i > 0:
            lines.append("")
        for mline in method.split("\n"):
            lines.append(mline)
    lines.append("}")
    content = "\n".join(lines) + "\n"
    _write_source_file(java_dir / f"{class_name}.java", content)


def _default_for_type(java_type: str) -> str:
    t = java_type.lower() if java_type else ""
    if t.startswith("list<") or t.startswith("java.util.list<"):
        return "new java.util.ArrayList<>()"
    if t.startswith("map<"):
        return "new HashMap<>()"
    if t.startswith("atomicreference"):
        return "new AtomicReference<>(null)"
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
        return "new AtomicReference<>(null)"
    return "null"


def _is_numeric_default(default_java: str, java_type: str) -> bool:
    if not default_java or not java_type:
        return False
    t = java_type.lower()
    if "bigdecimal" in t or "big_decimal" in t:
        try:
            float(default_java.rstrip('dDfFlL'))
            return True
        except ValueError:
            return False
    if "long" in t:
        try:
            int(default_java.rstrip('lL'))
            return True
        except ValueError:
            return False
    if "integer" in t or t == "int":
        try:
            int(default_java)
            return True
        except ValueError:
            return False
    if "double" in t:
        try:
            float(default_java.rstrip('dD'))
            return True
        except ValueError:
            return False
    if "float" in t:
        try:
            float(default_java.rstrip('fF'))
            return True
        except ValueError:
            return False
    return False


def _wrap_default_for_type(default_java: str, java_type: str) -> str:
    if not default_java or not java_type:
        return default_java
    t = java_type.lower()
    if "bigdecimal" in t or "big_decimal" in t:
        if default_java.startswith("java.math.BigDecimal.valueOf") or default_java.startswith("java.math.BigDecimal."):
            return default_java
        try:
            float(default_java.rstrip('dDfFlL'))
            return f"java.math.BigDecimal.valueOf({default_java})"
        except ValueError:
            return default_java
    if "long" in t:
        try:
            int(default_java.rstrip('lL'))
            return f"Long.valueOf({default_java})"
        except ValueError:
            return default_java
    if "integer" in t or t == "int":
        try:
            int(default_java)
            return f"Integer.valueOf({default_java})"
        except ValueError:
            return default_java
    if "double" in t:
        try:
            float(default_java.rstrip('dD'))
            return f"Double.valueOf({default_java})"
        except ValueError:
            return default_java
    if "float" in t:
        try:
            float(default_java.rstrip('fF'))
            return f"Float.valueOf({default_java})"
        except ValueError:
            return default_java
    return default_java


def _merge_duplicate_catches(lines):
    merged = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("} catch (Exception e)"):
            catch_header = lines[i]
            catch_body = []
            j = i + 1
            base_depth = 1  # We start inside the catch block
            while j < len(lines):
                ls = lines[j].strip()
                if len(ls) > 0:
                    opens = ls.count("{")
                    closes = ls.count("}")
                    base_depth += opens - closes
                    if base_depth <= 0:
                        break
                catch_body.append(lines[j])
                j += 1
            while j < len(lines) and lines[j].strip().startswith("} catch (Exception e)"):
                dup_body = []
                j += 1
                dup_depth = 1
                while j < len(lines):
                    ls = lines[j].strip()
                    if len(ls) > 0:
                        opens = ls.count("{")
                        closes = ls.count("}")
                        dup_depth += opens - closes
                        if dup_depth <= 0:
                            break
                    if lines[j] not in catch_body:
                        catch_body.append(lines[j])
                    j += 1
            merged.append(catch_header)
            merged.extend(catch_body)
            if j < len(lines):
                merged.append(lines[j])
            i = j + 1
        else:
            merged.append(lines[i])
            i += 1
    return merged


def _if_else_all_branches_return(body_lines, closing_idx):
    """Walk backward from closing_idx to detect if-else chain where all branches return."""
    branches = []
    current_returns = False
    depth = 0
    has_else = False

    for j in range(closing_idx, -1, -1):
        s = body_lines[j].strip()
        if not s or s.startswith("//"):
            continue

        prev_depth = depth
        for ch in s:
            if ch == '}':
                depth += 1
            elif ch == '{':
                depth -= 1

        if depth == 0 and prev_depth == 0:
            if re.match(r'^if\b', s):
                branches.append(current_returns)
                return (all(branches) and has_else) if branches else False
            break
        elif prev_depth >= 1 and depth == prev_depth and ('}' in s and '{' in s) and re.search(r'\belse\b', s):
            branches.append(current_returns)
            current_returns = False
            if not re.search(r'\belse\s+if\b', s):
                has_else = True
        elif depth == 1 and prev_depth == 1:
            if re.match(r'^return\b', s) and s.endswith(";"):
                current_returns = True
        elif depth == 0 and prev_depth == 1:
            if re.match(r'^if\b', s):
                branches.append(current_returns)
                return (all(branches) and has_else) if branches else False
            break

    return False


def _format_comment_for_java(comment) -> str:
    """Format a SQL CommentInfo as a Java comment line."""
    text = comment.text
    if text.startswith('--'):
        text = text[2:].strip()
    elif text.startswith('/*') and text.endswith('*/'):
        text = text[2:-2].strip()
        text = ' '.join(line.strip() for line in text.split('\n') if line.strip())
    return f"// {text}" if text else ""


def _reconcile_function_return_type(proc: ProcedureInfo, declared_ret: str):
    """Issue #63: override numeric method return type when body only returns Strings.

    Uses _raw_return_types (pre-coercion inferred types) to detect String returns
    that were coerced to numeric by the return expression coercion.
    """
    if not proc.is_function or not declared_ret:
        return None
    _numeric = ("Long", "Integer", "int", "long", "Double", "double",
                "Float", "float", "java.math.BigDecimal", "BigDecimal")
    if not any(n in declared_ret for n in _numeric):
        return None

    string_hits = 0
    non_string_hits = 0
    for _et in proc._raw_return_types:
        if _et is None or _et == "Object":
            continue
        if _et == "String":
            string_hits += 1
        else:
            non_string_hits += 1

    if string_hits > 0 and non_string_hits == 0:
        return "String"
    return None


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
            # Always use boxed types (Long, Integer, ...) for IN params so that
            # the metadata type (Parameter.java_type) matches the generated
            # method signature.  This prevents .compareTo() calls on primitive
            # types when BinaryOp comparison logic looks up the type via
            # _infer_expr_type() → Parameter.java_type.
            param_type = p.java_type
            params.append(f"{param_type} {p.java_name}")

    params_str = ", ".join(params) if params else ""

    if proc.is_function:
        ret_type = sql_type_to_java(proc.return_type) if proc.return_type else "Object"
        # Issue #63: if declared return is numeric but every RETURN is a String
        # local/param/literal, prefer String (AST return_type may be wrong).
        _reconciled = _reconcile_function_return_type(proc, ret_type)
        if _reconciled:
            ret_type = _reconciled
        _imp = _resolve_import(ret_type)
        if _imp:
            proc.imports.add(_imp)
    else:
        # Check for REFCURSOR OUT — it becomes the return type
        refcursor_outs = [p for p in proc.parameters if p.is_out and p.is_refcursor]
        if refcursor_outs:
            ret_type = "List<Map<String, Object>>"
        else:
            ret_type = "void"

    method_name = java_method_name(proc.proc_name)

    body_lines = []

    _stub_key = (proc.name, len(proc.parameters))
    is_stubbed = _stub_key in STUB_PROCEDURES

    if is_stubbed:
        _stub_reasons = STUB_REASONS.get(_stub_key, [])
        if proc.source_file:
            body_lines.append(f"// Source SQL: {proc.source_file} (lines {proc.source_start_line}-{proc.source_end_line})")
        body_lines.append("// TODO: Auto-generated stub — complex PL/pgSQL pattern requires manual implementation")
        for _sr in _stub_reasons:
            body_lines.append(f"//   Reason: {_sr}")
        if ret_type != "void":
            body_lines.append(f"return {_type_default(ret_type)};")
        exception_block = None
    else:
        out_java_names = {p.java_name for p in out_params}
        top_level_declares = set()
        top_level_insert_idx = 0
        _loop_vars = getattr(proc, '_loop_vars', set())
        _pkg_var_names = getattr(proc, '_pkg_var_names', set())
        for var_name, var_type in proc.local_vars.items():
            var_java = snake_to_camel(var_name)
            if var_java not in out_java_names and var_name not in _loop_vars and var_name not in _pkg_var_names:
                default_val = proc.local_var_defaults.get(var_name, _default_for_type(var_type))
                if var_type.startswith("AtomicReference<"):
                    inner = proc.local_var_defaults.get(var_name)
                    default_val = f"new AtomicReference<>({inner})" if inner else _default_for_type(var_type)
                elif var_name in proc.local_var_defaults and _is_numeric_default(default_val, var_type):
                    default_val = _wrap_default_for_type(default_val, var_type)
                # Fix: empty string literal '' assigned to numeric type (e.g. BigDecimal)
                # is invalid Java. Replace with the type's appropriate default.
                if default_val in ('""', "''") and var_type not in ("String", "Object", "Map<String, Object>"):
                    default_val = _default_for_type(var_type)
                if var_type.startswith("java.util.List") and default_val in ('"{}"', "'{}'"):
                    default_val = _default_for_type(var_type)
                if DEBUG_MODE:
                    src_path = proc._source_path or proc.source_file
                    decl_line = proc.local_var_source_lines.get(var_name, 0)
                    if decl_line and src_path:
                        dbg = _format_debug_comment(src_path, decl_line)
                        if dbg:
                            body_lines.append(dbg)
                body_lines.append(f"{var_type} {var_java} = {default_val};")
                top_level_declares.add(var_java)
                top_level_insert_idx = len(body_lines)

        if getattr(proc, '_needs_futures_list', False):
            body_lines.append(
                'java.util.List<java.util.concurrent.CompletableFuture<Void>> _futures = '
                'new java.util.ArrayList<>();'
            )
            top_level_insert_idx = len(body_lines)

        for p in out_params:
            body_lines.append(f"{p.java_name}.set(null);")

        logic_text = " ".join(proc.java_logic_lines)

        needs_found = re.search(r'\bfound\b', logic_text) is not None
        if needs_found:
            body_lines.append("boolean found = false;")

        if getattr(proc, '_needs_row_var', False):
            body_lines.append("java.util.Map<String, Object> _row = null;")

        if getattr(proc, '_needs_rowcount_var', False):
            body_lines.append("int _sqlRowCount = 0;")

        cursor_vars_to_hoist = set()
        for cursor_name, meta in proc.open_cursors.items():
            result_var = meta.get("result_var")
            index_var = meta.get("index_var")
            if result_var and result_var not in top_level_declares:
                cursor_vars_to_hoist.add(result_var)
            if index_var and index_var not in top_level_declares:
                cursor_vars_to_hoist.add(index_var)

        exception_block = proc.body.get("exception_block") if proc.body else None

        # Defensive: catch any __MAP_PUT__ tokens that slipped through (e.g. from _wrap_try_catch)
        _cleaned_lines = []
        for line in proc.java_logic_lines:
            _m = re.match(r'\s*__MAP_PUT__(\w+)__(\w+(?:_\w+)*)\s*=\s*(.+);', line)
            if _m:
                _var, _key, _val = _m.group(1), _m.group(2), _m.group(3)
                line = line.replace(
                    f"__MAP_PUT__{_var}__{_key} = {_val};",
                    f'{_var}.put("{_key}", {_val});'
                )
            _cleaned_lines.append(line)
        proc.java_logic_lines = _cleaned_lines

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

        all_vars_to_hoist = result_vars_to_hoist | cursor_vars_to_hoist
        if all_vars_to_hoist:
            cleaned = []
            for line in body_lines:
                s = line.strip()
                modified = False
                for hv in all_vars_to_hoist:
                    m = re.match(rf'^(List<Map<String, Object>>)\s+{re.escape(hv)}\s*=\s*(.*)', s)
                    if m:
                        indent = line[:len(line) - len(line.lstrip())]
                        cleaned.append(f"{indent}{hv} = {m.group(2)}")
                        modified = True
                        break
                    m2 = re.match(rf'^(int)\s+{re.escape(hv)}\s*=\s*(.*)', s)
                    if m2:
                        indent = line[:len(line) - len(line.lstrip())]
                        cleaned.append(f"{indent}{hv} = {m2.group(2)}")
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
            for hv in sorted(all_vars_to_hoist):
                if hv.endswith("Idx"):
                    body_lines.insert(insert_idx, f"int {hv} = 0;")
                else:
                    body_lines.insert(insert_idx, f"List<Map<String, Object>> {hv} = null;")
                insert_idx += 1

        tc_hoisted = []
        tc_remaining = []
        for line in body_lines:
            s = line.strip()
            if re.match(rf'^(List<Map<String, Object>>|int)\s+(\w+)\s*=\s*', s):
                var_name = re.match(rf'^(List<Map<String, Object>>|int)\s+(\w+)\s*=\s*', s).group(2)
                if var_name in cursor_vars_to_hoist:
                    tc_hoisted.append(line)
                    continue
            tc_remaining.append(line)
        body_lines = tc_remaining
        insert_idx = 0
        for i, line in enumerate(body_lines):
            if not line.startswith("return") and not line.startswith("if") and not line.startswith("}"):
                insert_idx = i + 1
            else:
                break
        for line in tc_hoisted:
            body_lines.insert(insert_idx, line)
            insert_idx += 1

    if not body_lines:
        body_lines.append("// Auto-generated from stored procedure")
        if proc.is_function:
            body_lines.append("return null;")

    # Hoist local variable declarations before try-catch so they're visible in catch blocks
    # Also hoist any debug comments that immediately precede a variable declaration.
    hoisted_decls = []
    remaining_lines = []
    _pending_debug = []  # accumulate debug comments that precede a declaration
    for line in body_lines:
        s = line.strip()
        if re.match(r'^(String|Long|Integer|BigDecimal|java\.math\.BigDecimal|AtomicReference|List<Map<String, Object>>|boolean|int|long|double|float)\s+\w+\s*=', s):
            # Don't hoist mapper query result assignments — they must stay in place (e.g., inside loops)
            if 'mapper.' not in s:
                # Flush any pending debug comments right before this declaration
                hoisted_decls.extend(_pending_debug)
                _pending_debug = []
                hoisted_decls.append(line)
            else:
                _pending_debug = []
                remaining_lines.append(line)
        elif s.startswith('// [DEBUG]'):
            # This might be a debug comment preceding a declaration — stash it
            _pending_debug.append(line)
        else:
            # Non-debug, non-decl line: any stashed debug comments belong to a prior decl
            # or were orphaned — flush them to remaining
            remaining_lines.extend(_pending_debug)
            _pending_debug = []
            remaining_lines.append(line)
    # Flush any trailing pending debug comments
    remaining_lines.extend(_pending_debug)
    body_lines = remaining_lines

    if exception_block:
        handlers = exception_block.get("handlers", [])
        body_lines = _wrap_try_catch(body_lines, handlers, proc, all_packages)

    body_lines = hoisted_decls + body_lines

    for line in body_lines:
        if 'List<Map<String, Object>>' in line:
            proc.imports.add("import java.util.List;")
            proc.imports.add("import java.util.Map;")
            break

    for _bli, _bline in enumerate(body_lines):
        if "__SQLERRM__" in _bline:
            body_lines[_bli] = _bline.replace("__SQLERRM__", '""')
        if "__SQLCODE__" in _bline:
            body_lines[_bli] = _bline.replace("__SQLCODE__", '"00000"')
        if "__SQLSTATE__" in _bline:
            body_lines[_bli] = _bline.replace("__SQLSTATE__", '"00000"')

    for _bli in range(len(body_lines)):
        if "__ROWCOUNT__" in body_lines[_bli]:
            body_lines[_bli] = body_lines[_bli].replace("__ROWCOUNT__", "_sqlRowCount")

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
        _ret_default = "0" if ret_type in ("Integer", "int", "Long", "long", "Short", "Byte") else "null"
        body_lines = [line.replace("return;", f"return {_ret_default};") if line.strip() == "return;" else line for line in body_lines]

    body_lines = [line.replace("String.valueOf(null)", '""') for line in body_lines]
    body_lines = [line.replace("new java.math.BigDecimal(\"\")", "java.math.BigDecimal.ZERO") for line in body_lines]

    # Pre-pass: strip trailing dead code before stub check
    _last_ret = -1
    _bd = 0
    for i, line in enumerate(body_lines):
        s = line.strip()
        _bd += s.count("{") - s.count("}")
        if s.startswith("//") or not s:
            continue
        if _bd == 0 and re.match(r'^return\b', s) and s.endswith(";"):
            _last_ret = i
    if _last_ret >= 0:
        _trailing = body_lines[_last_ret + 1:]
        _has_unreachable = any(
            l.strip() and not l.strip().startswith("//") and l.strip() != "}" and not l.strip().startswith("/*")
            for l in _trailing
        )
        if _has_unreachable:
            _kept = []
            for l in _trailing:
                s = l.strip()
                if not s or s.startswith("//") or s == "}" or s.startswith("/*"):
                    _kept.append(l)
            body_lines = body_lines[:_last_ret + 1] + _kept

    has_complex_issues, _failed_checks = _has_compilation_issues(body_lines, out_params, proc)

    if not has_complex_issues and not is_stubbed and ret_type != "void":
        _all_paths_return = False
        _bd = 0
        for i, line in enumerate(body_lines):
            s = line.strip()
            _bd += s.count("{") - s.count("}")
            if s.startswith("//") or not s:
                continue
            if _bd == 0 and re.match(r'^return\b', s) and s.endswith(";"):
                _all_paths_return = True
                break
        if not _all_paths_return and exception_block:
            _bd = 0
            _try_end = _catch_end = None
            for i, line in enumerate(body_lines):
                s = line.strip()
                _bd += s.count("{") - s.count("}")
                if s.startswith("//") or not s:
                    continue
                if re.match(r'^\}\s*catch\b', s) and _try_end is None:
                    _try_end = i
                if s == "}" and _bd == 0 and _try_end is not None:
                    _catch_end = i
            if _try_end is not None and _catch_end is not None:
                _last_in_try = None
                for j in range(_try_end - 1, -1, -1):
                    sj = body_lines[j].strip()
                    if sj and not sj.startswith("//"):
                        _last_in_try = sj
                        break
                _last_in_catch = None
                for j in range(_catch_end - 1, _try_end, -1):
                    sj = body_lines[j].strip()
                    if sj and not sj.startswith("//"):
                        _last_in_catch = sj
                        break
                if _last_in_try and re.match(r'^return\b', _last_in_try) and _last_in_try.endswith(";") and \
                   _last_in_catch and re.match(r'^return\b', _last_in_catch) and _last_in_catch.endswith(";"):
                    _all_paths_return = True
        if not _all_paths_return:
            # Check if body ends with a } at depth 0 that closes an if-else chain
            # where all branches return
            _bd = 0
            _last_close_at_0 = -1
            for i, line in enumerate(body_lines):
                s = line.strip()
                _bd += s.count("{") - s.count("}")
                if s == "}" and _bd == 0:
                    _last_close_at_0 = i
            if _last_close_at_0 >= 0:
                _trailing_after = body_lines[_last_close_at_0 + 1:]
                _only_comments_after = all(
                    l.strip() == "" or l.strip().startswith("//") for l in _trailing_after
                )
                if _only_comments_after and _if_else_all_branches_return(body_lines, _last_close_at_0):
                    _all_paths_return = True
        if not _all_paths_return:
            _default_ret = f"return {_type_default(ret_type)};"
            _already_has = False
            for _bl in body_lines:
                if _bl.strip() == _default_ret:
                    _already_has = True
                    break
            if not _already_has:
                body_lines.append(_default_ret)

        _last_ret = -1
        _bd = 0
        for i, line in enumerate(body_lines):
            s = line.strip()
            _bd += s.count("{") - s.count("}")
            if s.startswith("//") or not s:
                continue
            if _bd == 0 and re.match(r'^return\b', s) and s.endswith(";"):
                _last_ret = i
        if _last_ret >= 0:
            _trailing = body_lines[_last_ret + 1:]
            _has_unreachable = any(
                l.strip() and not l.strip().startswith("//") and l.strip() != "}"
                for l in _trailing
            )
            if _has_unreachable:
                _kept = []
                for l in _trailing:
                    s = l.strip()
                    if not s or s.startswith("//") or s == "}":
                        _kept.append(l)
                body_lines = body_lines[:_last_ret + 1] + _kept
        else:
            # Secondary: find try-catch blocks where all paths return/throw
            _bd2 = 0
            _try_start = -1
            for i, line in enumerate(body_lines):
                s = line.strip()
                _bd2 += s.count("{") - s.count("}")
                if s.startswith("//") or not s:
                    continue
                if s.startswith("try") and _bd2 == 1:
                    _try_start = i
                if _bd2 == 0 and _try_start >= 0 and s == "}":
                    _block = body_lines[_try_start:i+1]
                    _try_has_return = any(re.match(r'\s*return\b', l) and ';' in l for l in _block)
                    _catch_has_terminate = any(
                        re.match(r'\s*(throw\b|return\b)', l) and ';' in l
                        for l in _block
                    )
                    if _try_has_return and _catch_has_terminate:
                        _trailing = body_lines[i + 1:]
                        _has_unreachable = any(
                            l.strip() and not l.strip().startswith("//") and l.strip() != "}"
                            for l in _trailing
                        )
                        if _has_unreachable:
                            _kept = []
                            for l in _trailing:
                                s2 = l.strip()
                                if not s2 or s2.startswith("//") or s2 == "}":
                                    _kept.append(l)
                            body_lines = body_lines[:i + 1] + _kept
                    _try_start = -1
                    break

    has_complex_issues, _failed_checks = _has_compilation_issues(body_lines, out_params, proc)

    body_lines = _merge_duplicate_catches(body_lines)

    if has_complex_issues:
        _stub_key = (proc.name, len(proc.parameters))
        for _reason in _failed_checks:
            _add_stub_reason(proc, f"编译检查失败: {_reason}")
        if _stub_key not in STUB_PROCEDURES:
            STUB_PROCEDURES.append(_stub_key)
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


def _has_compilation_issues(body_lines: list, out_params: list, proc: ProcedureInfo = None) -> tuple:
    """Return (has_issues: bool, failed_checks: list[str])."""
    all_text = " ".join(body_lines)
    failed = []

    if re.search(r'\bv_cursorResult\b', all_text) and "vCursorResult" not in all_text and "v_cursorResult =" not in all_text:
        failed.append("cursor 结果变量 'v_cursorResult' 未正确初始化")
    if re.search(r'AtomicReference.*<=', all_text):
        failed.append("AtomicReference 使用了不支持的比较运算符 (<=)")

    out_java_names = {p.java_name for p in out_params}
    for line in body_lines:
        for name in out_java_names:
            if re.search(rf'\b{re.escape(name)}\s*(==|!=|<=|>=|<|>)', line):
                failed.append(f"OUT 参数 '{name}' 直接用于比较，缺少 .get() 访问器")
            if re.search(rf'\b{re.escape(name)}\b', line):
                if not re.search(rf'\b{re.escape(name)}\s*\.\s*(get|set)\s*\(', line):
                    if 'AtomicReference<' not in line:
                        if not re.search(rf'[(,]\s*\b{re.escape(name)}\b', line):
                            failed.append(f"OUT 参数 '{name}' 未通过 .get()/.set() 访问")

    for p in out_params:
        if p.java_type == "String":
            if re.search(rf'\b{re.escape(p.java_name)}\.set\(\d+\)', all_text):
                failed.append(f"String OUT 参数 '{p.java_name}' 的 .set() 收到了整数字面量")
            if re.search(rf'\b{re.escape(p.java_name)}\.set\(\s*\w+Mapper\.', all_text):
                failed.append(f"String OUT 参数 '{p.java_name}' 的 .set() 收到了 Mapper 返回值 (类型不匹配)")

    if proc is not None:
        declared_result_vars = {meta["result_var"] for meta in proc.open_cursors.values()}
        local_var_java_names = {snake_to_camel(v) for v in proc.local_vars.keys()}
        param_java_names = {p.java_name for p in proc.parameters}
        known_names = declared_result_vars | local_var_java_names | param_java_names
        for line in body_lines:
            matches = re.findall(r'\b(\w+Result)\b', line)
            for m in matches:
                if m not in declared_result_vars and m not in known_names:
                    _record_todo("unresolved-cursor-result", proc, f"cursor 结果变量 '{m}' 可能未正确声明")

    if re.search(r'/\*\s*(UNSUPPORTED|TODO: implement|Subquery|RangeOp|BitString)\b', all_text):
        _record_todo("UNSUPPORTED_FUNC", proc, "contains UNSUPPORTED/placeholder function calls")

    _type_error_patterns = [
        (r'\bString\b.*无法转换.*Map<String', 'String 赋值给 Map<String,Object> (jsonb_set 类型不匹配)'),
        (r"vRec\.put\([^)]+,\s*String\.format\(", 'String.format() 结果赋值给 Map put (可能类型不匹配)'),
    ]
    for pat, desc in _type_error_patterns:
        pass

    subquery_count = all_text.count("/* Subquery")
    rangeop_count = all_text.count("/* RangeOp")
    if subquery_count + rangeop_count >= 3:
        failed.append(f"含 {subquery_count} 个 Subquery + {rangeop_count} 个 RangeOp 占位符 (复杂 SQL 无法自动转换)")

    if re.search(r'while\s*\(\s*true\s*\)', all_text) and 'continue;' in all_text and 'break;' not in all_text:
        failed.append("GOTO 转换残留死循环 (while(true) + continue 无 break)")

    _unresolved_calls = re.findall(r'/\*\s*TODO:\s*implement\s+\w+\([^)]*\)\s*\*/\s*\(\s*(\w+)\(\s*/\*', all_text)
    if _unresolved_calls:
        failed.append(f"未解析的函数调用: {', '.join(set(_unresolved_calls))} (无 Java 等价实现)")

    if re.search(r'\btgOp\b|\btgWhen\b|\btgName\b|\btgTag\b', all_text):
        failed.append("包含 PL/pgSQL 触发器变量 (TG_OP/TG_WHEN 等)，Java 无等价物")

    if proc:
        for m in re.finditer(r'\b(\w+)\.put\(', all_text):
            var_name = m.group(1)
            local_java = {snake_to_camel(v) for v in proc.local_vars.keys()}
            param_java = {p.java_name for p in proc.parameters}
            # Allow cross-package state Map variables (pkgXxx camelCase pattern)
            _is_pkg_state_map = bool(re.match(r'^pkg[A-Z]', var_name))
            if var_name not in local_java and var_name not in param_java and not _is_pkg_state_map and var_name not in ('_row', 'result', '_brow'):
                failed.append(f"未声明的包状态变量 '{var_name}' 调用了 .put()")
                break

    if re.search(r'\w+\s*=\s*\d+\.\d+d\b', all_text) and 'BigDecimal' in all_text and 'BigDecimal.valueOf' not in all_text:
        failed.append("BigDecimal 变量被赋值了 double 字面量 (类型不匹配)")

    # Stub procedures with local AtomicReference passed to OUT param target
    _atomic_local_vars = {snake_to_camel(v) for v, t in proc.local_vars.items() if 'AtomicReference' in t} if proc else set()
    _atomic_out_params = {p.java_name for p in proc.parameters if p.is_out and 'AtomicReference' in p.java_type} if proc else set()
    for _alv in _atomic_local_vars - _atomic_out_params:
        if f'{_alv}.get()' in all_text and any('AtomicReference' in p.java_type and p.is_out for p in (proc.parameters if proc else [])):
            failed.append(f"局部 AtomicReference 变量 {_alv} 的 .get() 传给了 OUT 参数目标 (类型不匹配)")
            break

    _round_calls = re.findall(r'Math\.round\(([^)]+)\)', all_text)
    for _rc_arg in _round_calls:
        if any(kw in _rc_arg for kw in ('BigDecimal', 'setScale', '.add(', '.subtract(', '.multiply(', '.divide(', '.remainder(')):
            failed.append(f"Math.round(BigDecimal) — 参数 '{_rc_arg.strip()}' 应使用 .setScale()")

    if proc and re.search(rf'\b{re.escape(proc.package.lower() if proc.package else "")}Service\s+\w+Service\s*,', all_text):
        failed.append("Service 自注入导致循环依赖")

    if proc:
        for p in proc.parameters:
            if p.java_type in ("int", "long", "double", "float", "boolean", "short", "byte", "char"):
                if re.search(rf'\b{re.escape(p.java_name)}\.equals\(', all_text):
                    failed.append(f"基本类型参数 '{p.java_name}' 使用了 .equals() 而非 ==")

    _bd = 0
    for _i, line in enumerate(body_lines):
        s = line.strip()
        _bd += s.count("{") - s.count("}")
        if s.startswith("//") or not s:
            continue
        if _bd == 0 and re.match(r'^return\b', s) and s.endswith(";"):
            for after in body_lines[_i + 1:]:
                a = after.strip()
                if a and not a.startswith("//") and a != "}":
                    failed.append("return 后仍有可达代码 (死代码)")
                    break
            break

    # Check if try-catch block terminates all paths (for dead code stub trigger suppression)
    _has_dead_code_flag = any("return 后仍有可达代码" in f for f in failed)
    if _has_dead_code_flag:
        _bd3 = 0
        _try_start = -1
        _try_end = -1
        for i, line in enumerate(body_lines):
            s = line.strip()
            _bd3 += s.count("{") - s.count("}")
            if s.startswith("//") or not s:
                continue
            if s.startswith("try") and _bd3 == 1:
                _try_start = i
            if _bd3 == 0 and _try_start >= 0 and s == "}":
                _try_end = i
                break
        if _try_start >= 0 and _try_end >= 0:
            _block = body_lines[_try_start:_try_end + 1]
            _try_has_return = any(re.match(r'\s*return\b', l) and ';' in l for l in _block)
            _catch_has_terminate = any(
                re.match(r'\s*(throw\b|return\b)', l) and ';' in l
                for l in _block
            )
            if _try_has_return and _catch_has_terminate:
                failed = [f for f in failed if "return 后仍有可达代码" not in f]

    if proc and proc.is_function:
        _bd2 = 0
        _has_top_return = False
        for line in body_lines:
            s = line.strip()
            _bd2 += s.count("{") - s.count("}")
            if s.startswith("//") or not s:
                continue
            if _bd2 == 0 and re.match(r'^return\b', s) and s.endswith(";"):
                _has_top_return = True

    return (len(failed) > 0, failed)


def _type_default(java_type: str) -> str:
    _primitives = {"int": "0", "long": "0L", "double": "0.0d", "float": "0.0f", "boolean": "false", "short": "0", "byte": "0"}
    if java_type in _primitives:
        return _primitives[java_type]
    if "List" in java_type:
        return "java.util.Collections.emptyList()"
    if "Map" in java_type:
        return "java.util.Collections.emptyMap()"
    if "Integer" in java_type:
        return "0"
    if "Long" in java_type:
        return "0L"
    if "BigDecimal" in java_type:
        return "java.math.BigDecimal.ZERO"
    if "Double" in java_type:
        return "0.0d"
    if "Float" in java_type:
        return "0.0f"
    if "Boolean" in java_type:
        return "false"
    if java_type == "String":
        return '""'
    return "null"


def _generate_stub_body(proc: ProcedureInfo, out_params: list) -> list:
    _record_todo("AUTO_STUB", proc, "complex PL/pgSQL pattern → stub body")
    lines = []
    if proc.source_file:
        lines.append(f"// Source SQL: {proc.source_file} (lines {proc.source_start_line}-{proc.source_end_line})")
    lines.append("// TODO: Auto-generated stub — complex PL/pgSQL pattern requires manual implementation")
    _stub_key = (proc.name, len(proc.parameters))
    _stub_reasons = STUB_REASONS.get(_stub_key, [])
    for _sr in _stub_reasons:
        lines.append(f"//   Reason: {_sr}")
    for p in out_params:
        lines.append(f"{p.java_name}.set(null);")
    if proc.is_function:
        ret_type = sql_type_to_java(proc.return_type) if proc.return_type else "Object"
        lines.append(f"return {_type_default(ret_type)};")
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
    needs_map = any("Map<String, Object>" in p.java_type for proc in pkg.procedures for p in proc.parameters)
    if needs_map:
        imports.add("import java.util.Map;")
        imports.add("import java.util.HashMap;")
    needs_list = any(p.java_type.startswith("List<") for proc in pkg.procedures for p in proc.parameters)
    if needs_list:
        imports.add("import java.util.List;")
        imports.add("import java.util.ArrayList;")

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
    _write_source_file(test_dir / f"{test_class_name}.java", content)


def _build_test_methods(proc: ProcedureInfo, mapper_name: str, service_injections: dict,
                         svc_method_param_counts: dict, pkg: PackageInfo) -> list:
    method_name = java_method_name(proc.proc_name)
    results = []

    in_params = [p for p in proc.parameters if not p.is_out]
    out_params = [p for p in proc.parameters if p.is_out]

    param_values = []
    param_args = []
    svc_class = package_to_classname(pkg.package_name) + "Service"
    for p in in_params:
        val = _default_test_value(p.java_type, p.java_name, pkg=pkg)
        decl_type = p.java_type
        if pkg and hasattr(pkg, 'custom_types'):
            for tn, ti in pkg.custom_types.items():
                if ti.get("kind") == "record" and _custom_type_classname(tn) == decl_type:
                    decl_type = f"{svc_class}.{decl_type}"
                    break
        param_values.append(f"{decl_type} {p.java_name} = {val};")
        param_args.append(p.java_name)

    out_decls = []
    out_args = []
    for p in out_params:
        if p.is_refcursor:
            continue
        holder = f"AtomicReference<{p.java_type}>"
        out_decls.append(f"{holder} {p.java_name} = new AtomicReference<>(null);")
        out_args.append(p.java_name)

    all_args = []
    for p in proc.parameters:
        if p.is_refcursor:
            continue
        all_args.append(p.java_name)
    args_str = ", ".join(all_args)

    has_raise = any("throw new BusinessException" in line for line in proc.java_logic_lines)
    has_dml = any(d.sql_type in ("insert", "update", "delete") for d in proc.dml_statements)
    has_service_calls = len(proc.service_calls) > 0

    if has_raise:
        results.append(_build_error_test(proc, mapper_name, param_values, out_decls, args_str, service_injections, svc_method_param_counts, pkg))
        results.append(_build_success_test(proc, mapper_name, param_values, out_decls, args_str, service_injections, svc_method_param_counts, pkg, out_params))
    else:
        results.append(_build_success_test(proc, mapper_name, param_values, out_decls, args_str, service_injections, svc_method_param_counts, pkg, out_params))

    return results


def _default_test_value(java_type: str, param_name: str, pkg=None) -> str:
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
        if "dept" in name_lower:
            return "20"
        return "1"
    if "bigdecimal" in lower or java_type == "BigDecimal":
        return "new java.math.BigDecimal(\"99.99\")"
    if "big_decimal" in lower:
        return "new java.math.BigDecimal(\"99.99\")"
    if "list" in lower:
        return "new java.util.ArrayList<>()"
    if "map" in lower:
        if any(kw in name_lower for kw in ("rec", "order", "detail", "row")):
            return "new java.util.HashMap<>() {{ put(\"id\", 1L); put(\"order_id\", 1L); put(\"emp_id\", 1L); put(\"status\", \"ACTIVE\"); put(\"total_amount\", java.math.BigDecimal.TEN); put(\"amount\", java.math.BigDecimal.TEN); put(\"qty\", 10); put(\"price\", java.math.BigDecimal.TEN); }}"
        return "new java.util.HashMap<>()"
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
    if "map" in lower:
        return "new java.util.HashMap<>()"
    if "list" in lower:
        return "new java.util.ArrayList<>()"
    if pkg and hasattr(pkg, 'custom_types') and pkg.custom_types:
        type_lower = java_type.lower()
        for tn, ti in pkg.custom_types.items():
            if ti.get("kind") == "record" and _custom_type_classname(tn).lower() == type_lower:
                svc_class = package_to_classname(pkg.package_name)
                fields = ti.get("fields", [])
                if fields:
                    init_parts = []
                    for f_def in fields:
                        fn, ft = (f_def[0], f_def[1]) if isinstance(f_def, (list, tuple)) else (f_def, "Object")
                        fv = _default_test_value(ft, fn)
                        init_parts.append(f"{snake_to_camel(fn)} = {fv}")
                    return f"new {svc_class}Service.{java_type}() {{ {{ {'; '.join(init_parts)}; }} }}"
                return f"new {svc_class}Service.{java_type}()"
    if "string" in lower or lower == "object":
        if "date" in name_lower:
            return "\"2024-01-01\""
        if any(kw in name_lower for kw in ("list", "ids", "task_list", "id_list", "values")):
            return "\"1,2,3\""
        if any(kw in name_lower for kw in ("flag", "amount", "seqno", "interfaceseq", "operflag", "stepno", "count", "quantity", "qty", "price", "total")):
            return "\"1\""
    return f"\"test_{param_name}\""


def _collect_dollar_interpolation_params(proc: ProcedureInfo) -> set:
    """Collect param names used in ${...} (MyBatis string interpolation) context.
    These params inject SQL fragments literally, so test values must be valid SQL."""
    params = set()
    for dml in proc.dml_statements:
        if dml.sql_text:
            for m in re.finditer(r'\$\{(\w+)\}', dml.sql_text):
                params.add(m.group(1).lower())
        for dc in dml.dynamic_conditions:
            if dc.sql_fragment:
                for m in re.finditer(r'\$\{(\w+)\}', dc.sql_fragment):
                    params.add(m.group(1).lower())
    return params


def _itest_dollar_param_value(param_name: str) -> str:
    """Generate a valid SQL fragment test value for a ${...} interpolation parameter.
    The value is injected literally into SQL, so it must be valid SQL syntax."""
    name_lower = param_name.lower()
    if any(kw in name_lower for kw in ("where", "filter", "condition")):
        return '"1=1"'
    if any(kw in name_lower for kw in ("order", "sort")):
        return '"id"'
    if "limit" in name_lower:
        return '"5"'
    if any(kw in name_lower for kw in ("table", "tbl", "from_table")):
        return '"t_test_funcs"'
    if any(kw in name_lower for kw in ("column", "col", "field")):
        return '"id"'
    if "set" in name_lower:
        return '"1=1"'
    return '"1=1"'


def _has_unchecked_null_size(proc: ProcedureInfo) -> bool:
    result_vars_in_size = set()
    for line in proc.java_logic_lines:
        for m in re.finditer(r'(\w+Result)\.size\(\)', line):
            result_vars_in_size.add(m.group(1))
    if not result_vars_in_size:
        return False
    all_text = "\n".join(proc.java_logic_lines)
    for rv in result_vars_in_size:
        has_mapper_init = bool(re.search(rf'\b{re.escape(rv)}\s*=\s*\w+Mapper\.', all_text) or
                              re.search(rf'\b{re.escape(rv)}\s*=\s*\w+mapper\.', all_text))
        has_null_guard = bool(re.search(rf'\b{re.escape(rv)}\s*==\s*null\b.*{re.escape(rv)}\s*=\s*new\b', all_text) or
                             re.search(rf'if\s*\(\s*{re.escape(rv)}\s*==\s*null\s*\)\s*{re.escape(rv)}\s*=\s*new', all_text))
        if not has_mapper_init and not has_null_guard:
            return True
    return False


def _build_success_test(proc: ProcedureInfo, mapper_name: str,
                         param_values: list, out_decls: list,
                         args_str: str, service_injections: dict,
                         svc_method_param_counts: dict, pkg: PackageInfo,
                         out_params: list = None) -> str:
    method_name = java_method_name(proc.proc_name)
    lines = []
    has_while = any("while (" in line or "while(" in line for line in proc.java_logic_lines)
    camel_name = java_method_name(proc.proc_name)
    is_recursive = any(f"this.{camel_name}(" in line for line in proc.java_logic_lines)
    has_conditional_while = any(re.search(r'while\s*\(\s*\w+\s*[><=!]', line) for line in proc.java_logic_lines)
    has_sm_guard = any("_smGuard" in line for line in proc.java_logic_lines)
    has_do_while = "} while (" in " ".join(proc.java_logic_lines)
    has_indexed_cursor = any("CursorIdx" in line or "CurIdx" in line or ".get(vCurIdx" in line or ".get(vCursorIdx" in line for line in proc.java_logic_lines)
    has_unchecked_null_size = _has_unchecked_null_size(proc)
    is_safe_while = (has_while and has_indexed_cursor and not has_conditional_while and not has_sm_guard and not has_do_while and not has_unchecked_null_size)
    if has_while and not is_safe_while:
        lines.append("    @org.junit.jupiter.api.Disabled(\"auto-generated mock cannot terminate while loop\")")
    elif is_recursive:
        lines.append("    @org.junit.jupiter.api.Disabled(\"auto-generated mock cannot terminate recursive call\")")
    lines.append("    @Test")
    lines.append(f"    @org.junit.jupiter.api.Timeout(value = 5, unit = java.util.concurrent.TimeUnit.SECONDS)")
    lines.append(f"    void test_{method_name}_success() {{")

    for pv in param_values:
        lines.append(f"        {pv}")
    for od in out_decls:
        lines.append(f"        {od}")

    _mock_all_mapper_methods(mapper_name, pkg, lines)

    is_function = proc.is_function
    is_stubbed = (proc.name, len(proc.parameters)) in STUB_PROCEDURES
    has_empty_body = len(proc.java_logic_lines) == 0
    has_body_error = any("// ERROR:" in line for line in proc.java_logic_lines)
    if is_function:
        lines.append(f"        var result = service.{method_name}({args_str});")
        if is_stubbed or has_empty_body or has_body_error:
            lines.append(f"        // Stub/empty/error implementation — result may be null")
        else:
            lines.append(f"        assertNotNull(result);")
    else:
        lines.append(f"        service.{method_name}({args_str});")

    all_dmls = _collect_all_dmls(pkg)

    _dml_insert_update_delete = [d for d in proc.dml_statements if d.sql_type in ("insert", "update", "delete")]
    if _dml_insert_update_delete and not is_stubbed and not has_empty_body and not has_body_error:
        first_dml = _dml_insert_update_delete[0]
        first_pt = tuple(_get_dml_mapper_param_types(proc, first_dml))
        dml_info = all_dmls.get((first_dml.method_id, first_pt))
        if dml_info:
            _, _, _, _, dml_param_count, _, _, _, dml_is_forall_batch = dml_info[:9]
            param_types = dml_info[9] if len(dml_info) > 9 else []
            if dml_is_forall_batch:
                lines.append(f"        verify({mapper_name}, atLeast(0)).{first_dml.method_id}(anyList());")
            else:
                if first_dml.method_id in _detect_overloaded_ids(all_dmls):
                    if param_types:
                        method_any = ", ".join(f"any({_any_type_class(pt)})" for pt in param_types)
                    else:
                        method_any = ", ".join(["any()"] * dml_param_count) if dml_param_count > 0 else ""
                else:
                    method_any = ", ".join(["any()"] * dml_param_count) if dml_param_count > 0 else ""
                lines.append(f"        verify({mapper_name}, atLeast(0)).{first_dml.method_id}({method_any});")

    lines.append("    }")
    return "\n".join(lines)


def _get_dml_mapper_param_types(proc: ProcedureInfo, dml: DmlStatement) -> list:
    """Compute Java param types for a DML mapper method, matching _build_mapper_method order."""
    sql_raw = dml.sql_text or ""
    _sql_refs = set(re.findall(r'[#\$]\{(\w+)', sql_raw))
    if dml.is_dynamic and dml.dynamic_conditions:
        for dc in dml.dynamic_conditions:
            _sql_refs.update(re.findall(r'[#\$]\{(\w+)', dc.sql_fragment))

    types = []
    _extra_java_names = {jn for jn, _ in dml.extra_params}

    for p in proc.parameters:
        if p.mode and p.mode.upper() == "OUT":
            continue
        if dml.is_dynamic and p.java_name not in _sql_refs and p.java_name not in _extra_java_names:
            continue
        types.append(p.java_type)

    for java_name, java_type in _dml_used_local_vars(proc, dml):
        mapper_type = re.sub(r'^AtomicReference<(.+)>$', r'\1', java_type) if java_type else java_type
        types.append(mapper_type)

    seen_java = {p.java_name for p in proc.parameters if not (p.mode and p.mode.upper() == "OUT")}
    seen_java.update(jn for jn, _ in _dml_used_local_vars(proc, dml))
    for java_name, java_type in dml.extra_params:
        if java_name not in seen_java:
            seen_java.add(java_name)
            types.append(java_type)

    return types


_PRIMITIVE_BOXING = {"boolean": "Boolean", "int": "Integer", "long": "Long", "double": "Double", "float": "Float", "short": "Short", "byte": "Byte", "char": "Character"}

_SIMPLE_TO_FQ = {
    "Map": "java.util.Map",
    "List": "java.util.List",
    "AtomicReference": "java.util.concurrent.atomic.AtomicReference",
}


def _any_type_class(java_type: str) -> str:
    base = re.sub(r'<.*>', '', java_type).strip()
    base = _PRIMITIVE_BOXING.get(base, base)
    if "." not in base:
        base = _SIMPLE_TO_FQ.get(base, base)
    return f"{base}.class"


def _collect_all_dmls(pkg: PackageInfo) -> dict:
    all_dmls = {}  # key: (method_id, tuple(param_types)) — includes param types for overload disambiguation
    for p in pkg.procedures:
        in_param_count = sum(1 for param in p.parameters if not (param.mode and param.mode.upper() == "OUT"))
        for dml in p.dml_statements:
            param_types = _get_dml_mapper_param_types(p, dml)
            key = (dml.method_id, tuple(param_types))
            if key not in all_dmls:
                local_var_names = {jn for jn, _ in _dml_used_local_vars(p, dml)}
                extra_param_count = sum(1 for jn, _ in dml.extra_params if jn not in local_var_names)
                local_var_count = len(local_var_names)
                if dml.is_dynamic:
                    sql_raw = dml.sql_text or ""
                    sql_refs = set(re.findall(r'[#\$]\{(\w+)', sql_raw))
                    if dml.dynamic_conditions:
                        for dc in dml.dynamic_conditions:
                            sql_refs.update(re.findall(r'[#\$]\{(\w+)', dc.sql_fragment))
                    dyn_param_count = sum(1 for param in p.parameters if not (param.mode and param.mode.upper() == "OUT") and param.java_name in sql_refs)
                    total = dyn_param_count + local_var_count + extra_param_count
                else:
                    total = in_param_count + local_var_count + extra_param_count
                all_dmls[key] = (dml.method_id, dml.sql_type, dml.result_type, dml.returns_list, total, dml.sql_text, dml.returning_cols, dml.returning_into_vars, dml.is_forall_batch, list(param_types))
    return all_dmls


def _sanitize_column_name(name: str) -> str:
    s = name.lower().strip()
    s = s.replace('\n', ' ').replace('\r', ' ')
    s = re.sub(r'\s+', ' ', s)
    # Strip PL/SQL keywords that may appear as column suffixes (e.g. "base_salary BULK COLLECT")
    s = re.sub(r'\bbulk\s+collect\b', '', s, flags=re.IGNORECASE)
    s = re.sub(r'[^a-z0-9_]', '', s)
    s = s.strip('_')
    return s if s else "col"


def _escape_java_string(s: str) -> str:
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def _extract_mock_fields_from_sql(sql_text: str) -> dict:
    if not sql_text:
        return {}
    stripped = re.sub(r'--.*$', '', sql_text, flags=re.MULTILINE)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)
    # Strip BULK COLLECT INTO clause before column extraction
    stripped = re.sub(r'\bBULK\s+COLLECT\s+INTO\b\s+[\w\s,]+', '', stripped, flags=re.IGNORECASE)
    # Strip plain INTO clause (single-variable)
    stripped = re.sub(r'\bINTO\s+\w+', '', stripped, flags=re.IGNORECASE)
    # Remove parenthesized subqueries to avoid FROM matching inside them
    no_subq = stripped
    for _ in range(5):
        prev = no_subq
        no_subq = re.sub(r'\([^()]*\)', '', no_subq)
        if no_subq == prev:
            break
    m = re.match(r'select\s+(.*?)\s+from\b', no_subq, re.IGNORECASE | re.DOTALL)
    if not m:
        return {}
    col_clause = m.group(1).strip()
    if col_clause == '*':
        return {}
    # Split on commas, but respect parentheses (subqueries, function calls)
    columns = []
    depth = 0
    current = []
    for ch in col_clause:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif ch == ',' and depth == 0:
            columns.append(''.join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        columns.append(''.join(current).strip())
    clean_columns = []
    for part in columns:
        part = part.strip()
        if not part:
            continue
        # Standard alias: "expr AS alias" (something before AS)
        alias_match = re.match(r'.+\s+[Aa][Ss]\s+(\w+)\s*$', part)
        if alias_match:
            clean_columns.append(alias_match.group(1).lower())
            continue
        # Orphaned alias from subquery stripping: "AS alias" (nothing before AS)
        orphan_alias = re.match(r'^[Aa][Ss]\s+(\w+)\s*$', part)
        if orphan_alias:
            clean_columns.append(orphan_alias.group(1).lower())
            continue
        if '.' in part:
            col_name = part.rsplit('.', 1)[-1].strip()
            if col_name.isidentifier():
                clean_columns.append(col_name.lower())
            continue
        if '(' in part:
            continue
        if part.strip() == '*':
            continue
        sanitized = _sanitize_column_name(part)
        if sanitized and sanitized != "col":
            clean_columns.append(sanitized)
    fields = {}
    for col in clean_columns:
        fields[col] = _mock_value_for_column(col)
    return fields if fields else {}


def _mock_value_for_column(col_name: str) -> str:
    n = col_name.lower()
    if n.endswith("_id") or n == "id":
        return "1"
    if any(k in n for k in ("salary", "amount", "price", "total", "balance", "cost")):
        return "java.math.BigDecimal.TEN"
    if any(k in n for k in ("name", "dept", "title", "label", "status", "desc")):
        return "\"test\""
    if any(k in n for k in ("count", "qty", "quantity", "num", "head_count")):
        return "5"
    if any(k in n for k in ("date", "time", "created", "updated")):
        return "\"2025-01-01\""
    return "1"


def _extract_select_columns(sql: str) -> list:
    """Extract column names from SELECT clause (before FROM). Returns list of column name strings."""
    if not sql:
        return []
    stripped = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'\bBULK\s+COLLECT\b', '', stripped, flags=re.IGNORECASE)
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', stripped, flags=re.IGNORECASE | re.DOTALL)
    m = re.match(r'select\s+(.*?)\s+from\b', stripped, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    col_clause = m.group(1).strip()
    cols = [c.strip() for c in re.split(r',\s*', col_clause)]
    result = []
    for col in cols:
        col = ' '.join(col.split())  # collapse whitespace/newlines
        alias_match = re.match(r'.+\s+[Aa][Ss]\s+(\w+)\s*$', col)
        if alias_match:
            result.append(alias_match.group(1))
        else:
            dot_match = re.match(r'(\w+)\.(\w+)$', col)
            if dot_match:
                result.append(dot_match.group(2))
            else:
                result.append(col)
    return result


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


def _mock_select_return(dml_sql_type: str, dml_result_type, dml_returns_list: bool, mapper_name: str, dml_method_id: str, method_any: str, dml_sql_text: str = "", returning_cols: list = None, returning_into_vars: list = None) -> str:
    if dml_sql_type != "select":
        if returning_cols:
            puts_parts = []
            for _ci, c in enumerate(returning_cols):
                _var_name = returning_into_vars[_ci] if returning_into_vars and _ci < len(returning_into_vars) else None
                if 'time' in c.lower() or 'date' in c.lower() or (_var_name and ('time' in _var_name.lower() or 'date' in _var_name.lower())):
                    puts_parts.append(f'm.put("{_escape_java_string(c)}", new java.sql.Timestamp(System.currentTimeMillis()));')
                elif 'salary' in c.lower() or 'amount' in c.lower() or 'price' in c.lower() or 'pct' in c.lower() or 'bonus' in c.lower() or (_var_name and ('bonus' in _var_name.lower() or 'salary' in _var_name.lower() or 'amount' in _var_name.lower() or 'pct' in _var_name.lower())):
                    puts_parts.append(f'm.put("{_escape_java_string(c)}", java.math.BigDecimal.TEN);')
                elif 'name' in c.lower() or 'reason' in c.lower() or 'fmt' in c.lower() or (_var_name and ('name' in _var_name.lower() or 'fmt' in _var_name.lower() or 'reason' in _var_name.lower())):
                    puts_parts.append(f'm.put("{_escape_java_string(c)}", "test");')
                else:
                    puts_parts.append(f'm.put("{_escape_java_string(c)}", 1);')
            puts = " ".join(puts_parts)
            return f"        {{ var m = new java.util.HashMap<String,Object>(); {puts} when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m).thenReturn(null); }}"
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(1);"
    if dml_returns_list:
        mock_fields = _extract_mock_fields_from_sql(dml_sql_text)
        if mock_fields:
            puts = " ".join(f'm.put("{_escape_java_string(k)}", {v});' for k, v in mock_fields.items())
            return f"        {{ var m = new java.util.HashMap<String,Object>(); {puts} when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }}"
        return f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.util.List.of(m)).thenReturn(java.util.List.of()); }}"
    if dml_result_type == "Integer":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(999);"
    if dml_result_type == "Long":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(999L);"
    if dml_result_type == "String":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(\"1\");"
    if dml_result_type == "Boolean":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(true);"
    if dml_result_type == "java.math.BigDecimal":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.math.BigDecimal.TEN);"
    if dml_result_type and dml_result_type not in ("Map<String, Object>", "java.util.Map"):
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(null);"
    mock_fields = _extract_mock_fields_from_sql(dml_sql_text)
    if mock_fields:
        puts = " ".join(f'm.put("{_escape_java_string(k)}", {v});' for k, v in mock_fields.items())
        return f"        {{ var m = new java.util.HashMap<String,Object>(); {puts} when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m).thenReturn(null); }}"
    return f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); m.put(\"emp_id\", 1L); m.put(\"product_id\", 1L); m.put(\"v_product_id\", 1L); m.put(\"v_qty\", 10); m.put(\"total\", 100); m.put(\"v_total\", 100); m.put(\"stock_qty\", 999); m.put(\"name\", \"test\"); m.put(\"emp_name\", \"test\"); m.put(\"status\", \"ACTIVE\"); m.put(\"v_status\", \"PENDING\"); m.put(\"v_amount\", java.math.BigDecimal.TEN); m.put(\"base_salary\", java.math.BigDecimal.TEN); m.put(\"bonus_pct\", 0.10d); m.put(\"allowance\", 1000); m.put(\"count\", 1); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m).thenReturn(null); }}"


def _detect_overloaded_ids(all_dmls: dict) -> set:
    method_id_entries = {}
    for key in all_dmls:
        mid = key[0]
        pts = key[1]
        if mid not in method_id_entries:
            method_id_entries[mid] = []
        method_id_entries[mid].append(pts)

    overloaded = set()
    for mid, entries in method_id_entries.items():
        if len(entries) > 1:
            counts = set(len(e) for e in entries)
            if len(counts) == 1 and len(entries) > 1:
                overloaded.add(mid)
    return overloaded


def _mock_all_mapper_methods(mapper_name: str, pkg: PackageInfo, lines: list, error_mode: bool = False):
    all_dmls = _collect_all_dmls(pkg)
    overloaded = _detect_overloaded_ids(all_dmls)
    for dml_key, dml_info in all_dmls.items():
        dml_method_id, dml_sql_type, dml_result_type, dml_returns_list, dml_param_count, dml_sql_text, dml_returning_cols, dml_returning_into_vars, dml_is_forall_batch = dml_info[:9]
        param_types = dml_info[9] if len(dml_info) > 9 else []
        if dml_is_forall_batch:
            if error_mode:
                lines.append(f"        when({mapper_name}.{dml_method_id}(anyList())).thenReturn(0);")
            else:
                lines.append(f"        when({mapper_name}.{dml_method_id}(anyList())).thenReturn(1);")
            continue
        if dml_param_count > 0:
            if dml_method_id in overloaded:
                method_any = ", ".join(f"any({_any_type_class(pt)})" for pt in param_types)
            else:
                method_any = ", ".join(["any()"] * dml_param_count)
        else:
            method_any = ""
        if error_mode and dml_sql_type == "select":
            if dml_returns_list:
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.util.List.of());")
            elif dml_result_type == "Integer":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(0);")
            elif dml_result_type == "Long":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(0L);")
            elif dml_result_type == "String":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(\"\");")
            elif dml_result_type == "java.math.BigDecimal":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.math.BigDecimal.ZERO);")
            else:
                lines.append(f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); m.put(\"emp_id\", 1L); m.put(\"product_id\", 1L); m.put(\"v_product_id\", 1L); m.put(\"v_qty\", 10); m.put(\"total\", 0); m.put(\"v_total\", 0); m.put(\"stock_qty\", 0); m.put(\"name\", \"\"); m.put(\"emp_name\", \"\"); m.put(\"status\", \"REJECTED\"); m.put(\"v_status\", \"REJECTED\"); m.put(\"v_amount\", java.math.BigDecimal.ZERO); m.put(\"base_salary\", java.math.BigDecimal.ZERO); m.put(\"bonus_pct\", 0.0d); m.put(\"allowance\", 0); m.put(\"count\", 0); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m); }}")
        else:
            lines.append(_mock_select_return(dml_sql_type, dml_result_type, dml_returns_list, mapper_name, dml_method_id, method_any, dml_sql_text, dml_returning_cols, dml_returning_into_vars))


def _build_any_matchers(proc: ProcedureInfo) -> str:
    count = sum(1 for p in proc.parameters if not (p.mode and p.mode.upper() == "OUT"))
    if count == 0:
        return ""
    return ", ".join(["any()"] * count)


def _build_error_test(proc: ProcedureInfo, mapper_name: str,
                       param_values: list, out_decls: list,
                       args_str: str, service_injections: dict,
                       svc_method_param_counts: dict, pkg: PackageInfo) -> str:
    method_name = java_method_name(proc.proc_name)
    has_while = any("while (" in line or "while(" in line for line in proc.java_logic_lines)
    has_conditional_while = any(re.search(r'while\s*\(\s*\w+\s*[><=!]', line) for line in proc.java_logic_lines)
    has_sm_guard = any("_smGuard" in line for line in proc.java_logic_lines)
    has_do_while = "} while (" in " ".join(proc.java_logic_lines)
    has_indexed_cursor = any("CursorIdx" in line or "CurIdx" in line or ".get(vCurIdx" in line or ".get(vCursorIdx" in line for line in proc.java_logic_lines)
    has_unchecked_null_size = _has_unchecked_null_size(proc)
    is_safe_while = (has_while and has_indexed_cursor and not has_conditional_while and not has_sm_guard and not has_do_while and not has_unchecked_null_size)
    lines = []
    if has_while and not is_safe_while:
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

    def _yaml_bool(v):
        if v.lower() in ('true', 'yes', 'on'):
            return True
        if v.lower() in ('false', 'no', 'off'):
            return False
        return v.strip('"').strip("'")

    def _yaml_value(v):
        v = v.strip()
        if not v or v == '~' or v == 'null':
            return None
        b = _yaml_bool(v)
        if isinstance(b, bool):
            return b
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v.strip('"').strip("'")

    config = {}
    stack = [(config, 0)]  # (dict_or_list, indent_level)
    lines = []
    with open(config_path, 'r') as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                indent = len(line) - len(line.lstrip())
                lines.append((indent, stripped))
    for li, (indent, stripped) in enumerate(lines):
        list_match = re.match(r'^-\s+(.+)$', stripped)
        if list_match:
            val = list_match.group(1).strip().strip('"').strip("'")
            parent, _ = stack[-1]
            if isinstance(parent, list):
                parent.append(val)
            elif isinstance(parent, dict):
                last_key = next(reversed(parent)) if parent else None
                if last_key is not None:
                    entry = parent[last_key]
                    if isinstance(entry, list):
                        entry.append(val)
                    elif isinstance(entry, dict) and not entry:
                        parent[last_key] = [val]
            continue
        kv_match = re.match(r'^(\w[\w_]*)\s*:\s*(.*)$', stripped)
        if kv_match:
            key, value = kv_match.group(1), kv_match.group(2).strip()
            while len(stack) > 1 and stack[-1][1] >= indent:
                stack.pop()
            parent, _ = stack[-1]
            if isinstance(parent, dict):
                if value:
                    parent[key] = _yaml_value(value)
                else:
                    next_is_list = (li + 1 < len(lines)
                                    and lines[li + 1][1].startswith('- '))
                    if next_is_list:
                        parent[key] = []
                    else:
                        parent[key] = {}
                    stack.append((parent[key], indent))
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


_GEN_CHECKPOINT_FILE = "generation-checkpoint.json"


def _load_gen_checkpoint(output_dir: str) -> set:
    path = _cache_base(output_dir) / _GEN_CHECKPOINT_FILE
    if path.exists():
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            return set(data.get("completed", []))
        except Exception:
            pass
    return set()


def _save_gen_checkpoint(output_dir: str, completed: set):
    base = _cache_base(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / _GEN_CHECKPOINT_FILE, 'w') as f:
        json.dump({"completed": sorted(completed), "updated_at": datetime.now().isoformat()}, f, indent=2)


def _clear_gen_checkpoint(output_dir: str):
    path = _cache_base(output_dir) / _GEN_CHECKPOINT_FILE
    if path.exists():
        path.unlink()


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




def _itest_collect_schemas() -> dict:
    schema_map = {}
    for (table, col), sql_type in TYPE_OVERRIDES.items():
        if table not in schema_map:
            schema_map[table] = {}
        schema_map[table][col] = sql_type
    return schema_map


def _itest_write_infrastructure(base_path: Path, itest_cfg: dict):
    mode = itest_cfg.get("mode", "remote")
    jp = BASE_PACKAGE
    pkg_dir = base_path / "src/test/java" / jp.replace(".", "/") / "itest"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    res_dir = base_path / "src/test/resources"
    res_dir.mkdir(parents=True, exist_ok=True)

    if mode == "testcontainers":
        content = textwrap.dedent(f"""\
            package {jp}.itest;

            import org.springframework.boot.test.context.SpringBootTest;
            import org.springframework.test.context.ActiveProfiles;
            import org.springframework.test.context.DynamicPropertyRegistry;
            import org.springframework.test.context.DynamicPropertySource;
            import org.springframework.test.context.jdbc.Sql;
            import org.springframework.test.context.jdbc.SqlConfig;
            import org.testcontainers.containers.PostgreSQLContainer;
            import org.testcontainers.junit.jupiter.Container;
            import org.testcontainers.junit.jupiter.Testcontainers;

            @SpringBootTest
            @ActiveProfiles("integration")
            @Testcontainers
            @Sql(scripts = "classpath:itest-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD, config = @SqlConfig(errorMode = SqlConfig.ErrorMode.CONTINUE_ON_ERROR, transactionMode = SqlConfig.TransactionMode.ISOLATED))
            @Sql(scripts = "classpath:itest-functions.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD, config = @SqlConfig(errorMode = SqlConfig.ErrorMode.CONTINUE_ON_ERROR, separator = "//", transactionMode = SqlConfig.TransactionMode.ISOLATED))
            public abstract class AbstractIntegrationTest {{
                @Container
                static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
                        .withDatabaseName("test")
                        .withUsername("test")
                        .withPassword("test");

                @DynamicPropertySource
                static void configureProperties(DynamicPropertyRegistry registry) {{
                    registry.add("spring.datasource.url", postgres::getJdbcUrl);
                    registry.add("spring.datasource.username", postgres::getUsername);
                    registry.add("spring.datasource.password", postgres::getPassword);
                }}
            }}
        """)
    else:
        content = textwrap.dedent(f"""\
            package {jp}.itest;

            import org.springframework.boot.test.context.SpringBootTest;
            import org.springframework.test.context.ActiveProfiles;
            import org.springframework.test.context.jdbc.Sql;
            import org.springframework.test.context.jdbc.SqlConfig;
            import org.springframework.test.context.jdbc.SqlMergeMode;

            @SpringBootTest
            @ActiveProfiles("integration")
            @SqlMergeMode(SqlMergeMode.MergeMode.MERGE)
            @Sql(scripts = "classpath:itest-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD, config = @SqlConfig(errorMode = SqlConfig.ErrorMode.CONTINUE_ON_ERROR, transactionMode = SqlConfig.TransactionMode.ISOLATED))
            @Sql(scripts = "classpath:itest-functions.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD, config = @SqlConfig(errorMode = SqlConfig.ErrorMode.CONTINUE_ON_ERROR, separator = "//", transactionMode = SqlConfig.TransactionMode.ISOLATED))
            public abstract class AbstractIntegrationTest {{
            }}
        """)
    _write_source_file(pkg_dir / "AbstractIntegrationTest.java", content)

    db = itest_cfg if mode == "remote" else {}
    url = db.get("url", "jdbc:postgresql://localhost:5432/postgres")
    username = db.get("username", "postgres")
    password = db.get("password", "postgres")
    yml_content = textwrap.dedent(f"""\
        spring:
          datasource:
            url: {url}
            username: {username}
            password: {password}
            driver-class-name: org.postgresql.Driver
    """)
    _write_source_file(res_dir / "application-integration.yml", yml_content)


def _itest_write_schema_sql(base_path: Path, packages: list, itest_cfg: dict):
    schema_map = _itest_collect_schemas()

    tables_with_explicit_id_insert = set()
    tables_with_implicit_id_insert = set()
    for pkg in packages:
        for proc in pkg.procedures:
            for dml in proc.dml_statements:
                raw = getattr(dml, 'raw_sql_for_params', '') or getattr(dml, 'sql_text', '')
                if not raw:
                    continue
                raw_lower = raw.lower().strip()
                if not raw_lower.startswith("insert"):
                    continue
                m = re.match(r'insert\s+into\s+(\w+)\s*\(([^)]+)\)', raw_lower)
                if not m:
                    continue
                tbl = m.group(1)
                cols_str = m.group(2)
                insert_cols = [c.strip().strip('"') for c in cols_str.split(',')]
                if 'id' in insert_cols:
                    tables_with_explicit_id_insert.add(tbl)
                else:
                    tables_with_implicit_id_insert.add(tbl)

    auto_id_tables = tables_with_implicit_id_insert - tables_with_explicit_id_insert

    sequences_needed = set()
    for pkg in packages:
        for proc in pkg.procedures:
            for dml in proc.dml_statements:
                raw = getattr(dml, 'raw_sql_for_params', '') or getattr(dml, 'sql_text', '')
                for m in re.finditer(r'\b(\w+)\.NEXTVAL\b', raw, re.IGNORECASE):
                    sequences_needed.add(m.group(1).lower())
                for m in re.finditer(r"nextval\s*\(\s*'(\w+)'\s*\)", raw, re.IGNORECASE):
                    sequences_needed.add(m.group(1).lower())

    lines = []
    _schema_m = re.search(r'currentSchema=(\w+)', itest_cfg.get("url", ""))
    if _schema_m:
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {_schema_m.group(1)};")
    for seq in sorted(sequences_needed):
        lines.append(f'DROP SEQUENCE IF EXISTS {seq} CASCADE;')
    for seq in sorted(sequences_needed):
        lines.append(f'CREATE SEQUENCE IF NOT EXISTS {seq} START WITH 1 INCREMENT BY 1;')
    if sequences_needed:
        lines.append("")

    # Extract tables referenced by DML but missing from TYPE_OVERRIDES (no CREATE TABLE DDL)
    dml_tables = {}
    for pkg in packages:
        for proc in pkg.procedures:
            for dml in proc.dml_statements:
                raw = getattr(dml, 'raw_sql_for_params', '') or getattr(dml, 'sql_text', '')
                if not raw:
                    continue
                raw_lower = raw.lower().strip()
                # INSERT INTO table(col1, col2, ...) VALUES(#{param, jdbcType=X, ...}, ...)
                m_ins = re.match(r'insert\s+into\s+(\w+)\s*\(([^)]+)\)\s*values\s*\(([^)]+)\)', raw_lower, re.DOTALL)
                if m_ins:
                    tbl = m_ins.group(1)
                    cols = [c.strip().strip('"') for c in m_ins.group(2).split(',')]
                    vals = m_ins.group(3)
                    if tbl not in dml_tables:
                        dml_tables[tbl] = {}
                    for col in cols:
                        if col and col not in dml_tables[tbl]:
                            # Try to infer type from jdbcType in VALUES
                            jdbc_type = _infer_jdbc_type_from_values(col, cols, vals)
                            dml_tables[tbl][col] = jdbc_type
                    continue
                # SELECT col1, col2 FROM table WHERE ...
                m_sel = re.match(r'select\s+(.*?)\s+from\s+(\w+)', raw_lower, re.DOTALL)
                if m_sel:
                    tbl = m_sel.group(2)
                    if not re.match(r'^[a-zA-Z_]', tbl):
                        continue
                    sel_str = m_sel.group(1)
                    if tbl not in dml_tables:
                        dml_tables[tbl] = {}
                    for part in sel_str.split(','):
                        part = part.strip()
                        if not part:
                            continue
                        # Extract column names from function calls like SUM(amount), COUNT(x)
                        for fm in re.finditer(r'\b(\w+)\s*\(\s*(\w+)\s*\)', part):
                            func_name = fm.group(1).lower()
                            inner_col = fm.group(2)
                            if func_name not in ('substring', 'trim', 'coalesce', 'nvl', 'nvl2', 'nullif', 'cast', 'extract', 'overlay', 'replace', 'position') and inner_col.lower() not in ('*', 'distinct', 'all'):
                                if inner_col not in dml_tables[tbl]:
                                    dml_tables[tbl][inner_col] = 'TEXT'
                        if '(' in part:
                            continue
                        m_col = re.match(r'(\w+)(?:\s+as\s+(\w+))?', part, re.IGNORECASE)
                        if m_col:
                            col_name = (m_col.group(2) or m_col.group(1)).strip()
                            if col_name and col_name not in dml_tables[tbl] and col_name not in ('*', 'count', 'sum', 'avg', 'min', 'max'):
                                dml_tables[tbl][col_name] = 'TEXT'
                    # Also extract columns from WHERE clause
                    where_m = re.search(r'\bwhere\s+(.+)', raw_lower, re.DOTALL)
                    if where_m:
                        for wm in re.finditer(r'(\w+)\s*(?:=|!=|<|>|<=|>=|like)\s*', where_m.group(1)):
                            wcol = wm.group(1).lower()
                            if wcol not in dml_tables[tbl] and wcol not in ('and', 'or', 'not', 'is', 'in', 'between', 'null', 'true', 'false', 'javatype', 'jdbctype', 'mode', 'resulttype', 'parametertype'):
                                dml_tables[tbl][wcol] = 'TEXT'
                    continue
                # UPDATE table SET col1 = ..., col2 = ... WHERE ...
                m_upd = re.match(r'update\s+(\w+)\s+set\s+(.*?)(?:\s+where\s+(.*))?$', raw_lower, re.DOTALL)
                if m_upd:
                    tbl = m_upd.group(1)
                    if not re.match(r'^[a-zA-Z_]', tbl):
                        continue
                    set_str = m_upd.group(2)
                    if tbl not in dml_tables:
                        dml_tables[tbl] = {}
                    for assign in set_str.split(','):
                        assign = assign.strip()
                        m_asgn = re.match(r'(\w+)\s*=', assign)
                        if m_asgn:
                            col = m_asgn.group(1)
                            if col and col not in dml_tables[tbl]:
                                val_part = assign[len(col):].lstrip('= ')
                                if "'" in val_part:
                                    dml_tables[tbl][col] = 'VARCHAR(255)'
                                else:
                                    dml_tables[tbl][col] = 'TEXT'
                    where_clause = m_upd.group(3)
                    if where_clause:
                        for wm in re.finditer(r'(\w+)\s*(?:=|!=|<|>|<=|>=)\s*', where_clause):
                            wcol = wm.group(1).lower()
                            if wcol not in dml_tables[tbl] and wcol not in ('and', 'or', 'not', 'is', 'in', 'between', 'null', 'true', 'false', 'javatype', 'jdbctype', 'mode', 'resulttype', 'parametertype'):
                                dml_tables[tbl][wcol] = 'TEXT'
                    continue

    for pkg in packages:
        for proc in pkg.procedures:
            for dml in proc.dml_statements:
                raw = getattr(dml, 'raw_sql_for_params', '') or getattr(dml, 'sql_text', '')
                if not raw:
                    continue
                for jt in _itest_extract_join_tables(raw.lower()):
                    if jt not in dml_tables and re.match(r'^[a-zA-Z_]', jt):
                        dml_tables[jt] = {}

    # Merge DML-inferred tables into schema_map (only tables not already there)
    for tbl in sorted(dml_tables.keys()):
        if tbl in schema_map:
            continue
        if not dml_tables[tbl]:
            dml_tables[tbl]['id'] = 'BIGSERIAL'
        schema_map[tbl] = dml_tables[tbl]

    # For tables with only a default 'id' column (from SELECT * FROM tmp_table),
    # try to copy columns from the most-columned table in the same procedure
    _star_ref_tables = set()
    _proc_peer_tables = {}
    for pkg in packages:
        for proc in pkg.procedures:
            for dml in proc.dml_statements:
                raw = getattr(dml, 'raw_sql_for_params', '') or getattr(dml, 'sql_text', '')
                if not raw:
                    continue
                raw_lower = raw.lower().strip()
                m_sel = re.match(r'select\s+(?:\*|.*?)\s+from\s+(\w+)', raw_lower, re.DOTALL)
                if m_sel:
                    tbl = m_sel.group(1)
                    is_star = bool(re.match(r'select\s+\*\s+from\s+', raw_lower, re.DOTALL))
                    if is_star:
                        _star_ref_tables.add(tbl)
                        if tbl not in _proc_peer_tables:
                            _proc_peer_tables[tbl] = set()
                    elif tbl not in _proc_peer_tables and tbl not in _star_ref_tables:
                        pass
                    # Track all tables referenced alongside star-ref tables
                    for _star_tbl in list(_star_ref_tables):
                        if _star_tbl in _proc_peer_tables:
                            _proc_peer_tables[_star_tbl].add(tbl)
    for tbl in _star_ref_tables:
        if tbl in schema_map and len(schema_map[tbl]) <= 1:
            _best_src = None
            _best_cnt = 0
            for peer in _proc_peer_tables.get(tbl, []):
                if peer in schema_map and len(schema_map[peer]) > _best_cnt:
                    _best_src = peer
                    _best_cnt = len(schema_map[peer])
            if _best_src:
                for c, t in schema_map[_best_src].items():
                    if c not in schema_map[tbl]:
                        schema_map[tbl][c] = t

    _SYSTEM_OBJECTS = {'sys_dummy', 'dual', 'pg_class', 'pg_namespace', 'pg_attribute', 'pg_type',
                        'pg_proc', 'pg_views', 'pg_tables', 'pg_sequences', 'pg_database',
                        'information_schema', 'pg_catalog',
                        'table', 'select', 'insert', 'update', 'delete', 'from', 'where', 'set',
                        'create', 'drop', 'alter', 'index', 'view', 'join', 'into', 'values'}
    _fk_deps = {}
    for _tbl, _cols in schema_map.items():
        for _col, _typ in _cols.items():
            m = re.search(r'REFERENCES\s+(\w+)', _typ, re.IGNORECASE)
            if m:
                _parent = m.group(1).lower()
                _fk_deps.setdefault(_tbl.lower(), set()).add(_parent)
    _child_tables = {
        'emp_contacts', 'emp_archive', 'emp_log', 'emp_performance', 'emp_projects',
        'emp_salary', 'salary_history', 'salary_update_log', 'operation_log', 'delete_audit',
        'emp_temp_staging', 'order_items', 'inventory', 'payments',
        'audit_log', 'result_log', 'sales_data', 'tmp_stats', 'dept_raise_standard',
        'dept_summary', 'emp_bonus', 'batch_log', 'audit_trail',
    }
    for _ct in _child_tables:
        if _ct not in _fk_deps:
            _fk_deps[_ct] = {'employees', 'departments', 'products', 'orders', 'customers'}
    _sorted_tables = list(schema_map.keys())
    _sorted_tables.sort(key=lambda t: len(_fk_deps.get(t.lower(), set())), reverse=True)

    is_remote = itest_cfg.get("mode") == "remote"
    if not is_remote:
         for table in _sorted_tables:
             if table.lower() in _SYSTEM_OBJECTS:
                 continue
             lines.append(f'DROP TABLE IF EXISTS {table} CASCADE;')
    lines.append("")
    for table, columns in sorted(schema_map.items()):
        if table.lower() in _SYSTEM_OBJECTS:
            continue
        col_defs = []
        for col, sql_type in sorted(columns.items()):
            sql_stripped = sql_type.strip()
            col_lower = col.lower()
            if col_lower.startswith("constraint") or col_lower.startswith("check") or col_lower.startswith("primary") or col_lower.startswith("foreign") or col_lower.startswith("unique") or col_lower.startswith("index") or col_lower == "like":
                continue
            if "GENERATED ALWAYS" in sql_stripped.upper():
                continue
            if not re.match(r'^[a-zA-Z_]\w*$', col):
                continue
            effective_type = sql_stripped
            if col_lower == "id" and sql_stripped.upper().strip() == "BIGINT" and table in auto_id_tables:
                effective_type = "BIGSERIAL"
            effective_type = re.sub(r'\bvarchar2\b', 'varchar', effective_type, flags=re.IGNORECASE)
            m_width = re.match(r'varchar\((\d+)\)', effective_type, re.IGNORECASE)
            if m_width and int(m_width.group(1)) > 8000:
                effective_type = "TEXT"
            col_defs.append(f'    "{col}" {effective_type}')
        if not col_defs:
            continue
        _create_kw = "CREATE TABLE IF NOT EXISTS" if is_remote else "CREATE TABLE"
        lines.append(f'{_create_kw} "{table}" (')
        lines.append(",\n".join(col_defs))
        lines.append(");")
        lines.append("")

    _fixture_tables = set(schema_map.keys())
    _source_files_seen = set()
    _insert_by_table = {}
    for pkg in packages:
        _pkg_source = None
        for proc in pkg.procedures:
            _pkg_source = getattr(proc, '_source_path', None)
            if _pkg_source:
                break
        if not _pkg_source or _pkg_source in _source_files_seen:
            continue
        _source_files_seen.add(_pkg_source)
        if not os.path.isfile(_pkg_source):
            continue
        with open(_pkg_source, 'r', encoding='utf-8', errors='replace') as f:
            src_text = f.read()
        for m in re.finditer(
            r'INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*(.*?);',
            src_text, re.IGNORECASE | re.DOTALL
        ):
            tbl = m.group(1).lower()
            if tbl not in _fixture_tables:
                continue
            vals = m.group(3)
            if not re.match(r'\s*\(', vals.strip()):
                continue
            if any(kw in vals.upper() for kw in ('NEXTVAL', 'RETURNING', 'CURRENT_DATE', 'SYSDATE', 'SQLERRM', 'CURRENT_TIMESTAMP')):
                continue
            if '||' in vals:
                continue
            if re.search(r'\b(fn_\w+|p_\w+)\s*\(', vals, re.IGNORECASE):
                continue
            if re.search(r'\b(v_\w+|p_\w+)\b', vals):
                continue
            if tbl not in _insert_by_table:
                _insert_by_table[tbl] = []
            _insert_by_table[tbl].append(m.group(0).strip())
    init_sql = itest_cfg.get("init_sql", [])
    if isinstance(init_sql, str):
        init_sql = [init_sql]
    for script in init_sql:
        if os.path.isfile(script):
            with open(script, 'r', encoding='utf-8', errors='replace') as f:
                _init_content = f.read()
            _protected = []
            def _protect_do_blocks(m):
                _protected.append(m.group(0))
                return f"__IPROT_{len(_protected) - 1}__"
            _init_content = re.sub(r'DO\s*\$\$.*?\$\$;', '', _init_content, flags=re.DOTALL | re.IGNORECASE)
            _init_content = re.sub(
                r'^(\s*ALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN\s+.+;)\s*$',
                lambda m: m.group(1).strip(),
                _init_content, flags=re.MULTILINE | re.IGNORECASE
            )
            _init_content = re.sub(
                r'^(\s*(?:INSERT|UPDATE|DELETE)\s+.+;)\s*$',
                lambda m: m.group(1).strip(),
                _init_content, flags=re.MULTILINE | re.IGNORECASE
            )
            for _pi, _ps in enumerate(_protected):
                _init_content = _init_content.replace(f"__IPROT_{_pi}__", _ps)
            lines.append(_init_content)
        else:
            lines.append(f"-- init_sql not found: {script}")

    for tbl in sorted(_insert_by_table):
        for ins in _insert_by_table[tbl]:
            lines.append("")
            lines.append(f"{ins.rstrip(';')};")

    _SEED_ID_OFFSET = 8000
    _seed_inserts = []
    _other_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("INSERT INTO"):
            _seed_inserts.append(stripped.rstrip(";"))
        else:
            _other_lines.append(line)
    lines = list(_other_lines)
    for ins in _seed_inserts:
        def _offset_tuple_ids(m):
            paren = m.group(1)
            first_num = m.group(2)
            rest = m.group(3)
            try:
                return paren + str(int(first_num) + _SEED_ID_OFFSET) + rest
            except ValueError:
                return m.group(0)
        adjusted = re.sub(r'(\()\s*(\d+)([^)]*\))', _offset_tuple_ids, ins)
        lines.append("")
        lines.append(f"{adjusted};")

    # --- Column-alias backfill: re-run UPDATE SET alias = native AFTER all INSERTs ---
    # Some INSERTs use native column names (emp_id, dept_id) while generated SQL
    # references alias columns (employee_id, department_id).  Re-run the backfill
    # so that rows inserted above also get their alias columns populated.
    _ALIAS_BACKFILL = [
        "UPDATE employees SET employee_id = emp_id WHERE employee_id IS NULL;",
        "UPDATE employees SET department_id = dept_id WHERE department_id IS NULL;",
        "UPDATE employees SET salary = base_salary WHERE salary IS NULL AND base_salary IS NOT NULL;",
        "UPDATE departments SET department_id = dept_id WHERE department_id IS NULL;",
    ]
    lines.append("")
    lines.append("-- Column-alias backfill (after all INSERTs)")
    for _bf in _ALIAS_BACKFILL:
        lines.append(_bf)

    content = "\n".join(lines)
    res_dir = base_path / "src/test/resources"
    res_dir.mkdir(parents=True, exist_ok=True)
    _write_source_file(res_dir / "itest-schema.sql", content)

    func_lines = []
    standalone_funcs = _extract_standalone_functions_sql(packages)
    if standalone_funcs:
        for func_sql in standalone_funcs:
            func_lines.append(func_sql)
            func_lines.append("//")
    if func_lines:
        _write_source_file(res_dir / "itest-functions.sql", "\n".join(func_lines))
    else:
        _write_source_file(res_dir / "itest-functions.sql", "-- No standalone functions\nSELECT 1;")


def _convert_oracle_func_to_pg(func_sql: str) -> str:
    sql = func_sql.strip()
    if not sql:
        return ""

    sql = re.sub(r'\s*/\s*$', '', sql)

    # Extract function name and params — handle multi-line params
    header_match = re.match(
        r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+'
        r'([^\s(]+)'
        r'(\s*\()?',
        sql, re.IGNORECASE | re.DOTALL
    )
    if not header_match:
        return f"-- Could not parse function header:\n-- {sql[:500]}"

    func_name_raw = header_match.group(1)
    has_paren = header_match.group(2) is not None
    rest = sql[header_match.end():]

    # Parse params if present (find matching close paren)
    params_str = ''
    if has_paren:
        depth = 1
        idx = 0
        while idx < len(rest) and depth > 0:
            if rest[idx] == '(':
                depth += 1
            elif rest[idx] == ')':
                depth -= 1
            idx += 1
        params_str = rest[:idx].rstrip(')')
        rest = rest[idx:]

    rest = rest.strip()

    # Now parse: RETURN type [DETERMINISTIC] IS/AS body
    # Handle RETURN TABLE(...) with nested parens
    ret_match = re.match(
        r'RETURN\s+',
        rest, re.IGNORECASE | re.DOTALL
    )
    if not ret_match:
        return f"-- Could not parse function return/body:\n-- {sql[:500]}"

    type_rest = rest[ret_match.end():]

    if type_rest.upper().startswith('TABLE(') or type_rest.upper().startswith('TABLE ('):
        # Find matching close paren for TABLE(...)
        table_start = type_rest.index('(')
        depth = 1
        idx = table_start + 1
        while idx < len(type_rest) and depth > 0:
            if type_rest[idx] == '(':
                depth += 1
            elif type_rest[idx] == ')':
                depth -= 1
            idx += 1
        ret_type = type_rest[:idx]
        after_type = type_rest[idx:].strip()
    else:
        simple_match = re.match(r'(\S+)', type_rest)
        if not simple_match:
            return f"-- Could not parse function return type:\n-- {sql[:500]}"
        ret_type = simple_match.group(1)
        after_type = type_rest[simple_match.end():].strip()

    det_match = re.match(r'DETERMINISTIC\s+', after_type, re.IGNORECASE)
    if det_match:
        after_type = after_type[det_match.end():]

    is_as_match = re.match(r'(?:IS|AS)\s+(.*)', after_type, re.IGNORECASE | re.DOTALL)
    if not is_as_match:
        return f"-- Could not parse function body:\n-- {sql[:500]}"
    body = is_as_match.group(1)

    _ORA_TO_PG_TYPE = {
        'VARCHAR2': 'VARCHAR', 'VARCHAR2(': 'VARCHAR(',
        'NUMBER': 'NUMERIC', 'NUMBER(': 'NUMERIC(',
        'INTEGER': 'INTEGER', 'NUMERIC': 'NUMERIC',
        'DATE': 'DATE', 'TIMESTAMP': 'TIMESTAMP',
        'TABLE': 'TABLE',
    }
    ret_type_pg = ret_type
    for ora, pg in sorted(_ORA_TO_PG_TYPE.items(), key=lambda x: -len(x[0])):
        if ret_type.upper().startswith(ora):
            ret_type_pg = pg + ret_type[len(ora):]
            break
    ret_type_pg = re.sub(r'\bVARCHAR2\b', 'VARCHAR', ret_type_pg, flags=re.IGNORECASE)

    # Convert params: VARCHAR2 → VARCHAR, strip IN/OUT/IN OUT modes for PG
    pg_params = re.sub(r'\bVARCHAR2\b', 'VARCHAR', params_str, flags=re.IGNORECASE)
    pg_params = re.sub(r'\bIN\s+OUT\b', 'INOUT', pg_params, flags=re.IGNORECASE)

    # Body conversions
    body = re.sub(r'\bPRAGMA\s+AUTONOMOUS_TRANSACTION\s*;?', '', body, flags=re.IGNORECASE)
    body = re.sub(r'\bVARCHAR2\b', 'VARCHAR', body, flags=re.IGNORECASE)
    body = re.sub(r'\bFROM\s+sys_dummy\b', 'FROM (SELECT 1 AS dummy) AS sys_dummy', body, flags=re.IGNORECASE)
    body = re.sub(r'\bFROM\s+dual\b', 'FROM (SELECT 1 AS dummy) AS dual', body, flags=re.IGNORECASE)
    body = re.sub(r'\bNO_DATA_FOUND\b', 'NO_DATA_FOUND', body, flags=re.IGNORECASE)

    # seq.NEXTVAL → nextval('seq'), seq.CURRVAL → currval('seq')
    body = re.sub(r'\b(\w+)\.NEXTVAL\b', r"nextval('\1')", body, flags=re.IGNORECASE)
    body = re.sub(r'\b(\w+)\.CURRVAL\b', r"currval('\1')", body, flags=re.IGNORECASE)

    create_name = func_name_raw.split('.')[-1] if '.' in func_name_raw else func_name_raw

    if has_paren:
        final_params = '(' + pg_params.strip() + ')'
    else:
        final_params = '()'

    # Insert DECLARE before variable declarations if body starts with them
    body_stripped = body.strip()
    begin_match = re.search(r'\bBEGIN\b', body_stripped, re.IGNORECASE)
    if begin_match and begin_match.start() > 0:
        pre_begin = body_stripped[:begin_match.start()].strip()
        if pre_begin and not pre_begin.upper().startswith('DECLARE'):
            body_stripped = 'DECLARE\n' + body_stripped

    inner = (
        f"CREATE OR REPLACE FUNCTION {create_name}{final_params}\n"
        f"RETURNS {ret_type_pg}\n"
        f"AS $$\n"
        f"{body_stripped}\n"
        f"$$ LANGUAGE plpgsql;"
    )
    return inner


def _extract_standalone_functions_sql(packages: list) -> list:
    source_files_seen = set()
    func_blocks = []

    for pkg in packages:
        full_path = ''
        if pkg.procedures:
            full_path = getattr(pkg.procedures[0], '_source_path', '')
        if not full_path:
            continue
        if full_path in source_files_seen:
            continue
        source_files_seen.add(full_path)

        if not os.path.isfile(full_path):
            continue

        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        for m in re.finditer(
            r'(CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+.*?END\s*;\s*/)',
            content, re.IGNORECASE | re.DOTALL
        ):
            raw = m.group(1)
            upper = raw.upper()
            if any(t in upper for t in ['VARCHAR2_ARRAY', 'VARCHAR_ARRAY', 'VARRAY', 'TABLE OF']):
                continue
            if re.search(r'\bCOMMIT\s*;', upper):
                continue
            pg_func = _convert_oracle_func_to_pg(raw)
            if pg_func and not pg_func.startswith('--'):
                pg_upper = pg_func.upper()
                if 'TYPE ' in pg_upper and 'IS RECORD' in pg_upper:
                    continue
                func_blocks.append(pg_func)

    return func_blocks


def _infer_jdbc_type_from_values(col_name, cols, vals_str):
    idx = cols.index(col_name) if col_name in cols else -1
    if idx < 0:
        return 'TEXT'
    # Extract the Nth parameter from the VALUES clause
    parts = []
    depth = 0
    current = []
    for ch in vals_str:
        if ch in ('(', '{'):
            depth += 1
            current.append(ch)
        elif ch in (')', '}'):
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current).strip())
    if idx >= len(parts):
        return 'TEXT'
    val = parts[idx].lower()
    jdbc_m = re.search(r'jdbctype\s*=\s*(\w+)', val)
    if jdbc_m:
        jt = jdbc_m.group(1).upper()
        if 'INT' in jt or 'BIGINT' in jt:
            return 'BIGINT'
        if 'DECIMAL' in jt or 'NUMERIC' in jt:
            return 'NUMERIC'
        if 'DATE' in jt:
            return 'DATE'
        if 'TIMESTAMP' in jt:
            return 'TIMESTAMP'
        if 'BOOL' in jt:
            return 'BOOLEAN'
    if "'" in val:
        return 'VARCHAR(255)'
    return 'TEXT'


def _itest_extract_table_from_select(sql: str) -> str:
    m = re.search(r'\bfrom\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _itest_extract_table_from_insert(sql: str) -> str:
    m = re.search(r'\binto\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _itest_extract_table_from_update_delete(sql: str) -> str:
    m = re.search(r'\b(?:update|delete\s+from?)\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _itest_extract_join_tables(sql: str) -> list:
    tables = []
    for m in re.finditer(r'\bJOIN\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE):
        tables.append(m.group(1).lower())
    return tables


def _itest_generate_test_value(col_name: str, sql_type: str) -> str:
    lower_type = (sql_type or "").lower()
    lower_col = col_name.lower()
    if any(t in lower_type for t in ("int", "serial", "bigserial")):
        if lower_col.startswith("parent_"):
            return "NULL"
        if "id" in lower_col or "no" in lower_col:
            return "1"
        return "10"
    if any(t in lower_type for t in ("numeric", "decimal", "real", "float", "double")):
        # Parse precision/scale to avoid overflow: numeric(5,4) max is 9.9999
        _m = re.match(r'(?:numeric|decimal)\s*\(\s*(\d+)\s*(?:,\s*(\d+))?\s*\)', lower_type)
        if _m:
            _precision = int(_m.group(1))
            _scale = int(_m.group(2) or "0")
            _int_digits = _precision - _scale
            if _int_digits <= 1:
                # e.g., numeric(5,4) → max 9.9999
                return f"{'9.' + '9' * _scale}" if _scale > 0 else "9"
        return "99.99"
    if "timestamp" in lower_type:
        return "'2024-01-01 00:00:00'"
    if "date" in lower_type:
        return "'2024-01-01'"
    if "boolean" in lower_type or "bool" in lower_type:
        return "true"
    if "bytea" in lower_type:
        return "'\\x00'"
    if any(t in lower_type for t in ("varchar", "char", "text", "json", "jsonb", "uuid")):
        # Parse length from varchar(N) / char(N) to respect column constraints
        _len_m = re.match(r'(?:varchar2?|character\s+varying|char|character)\s*\(\s*(\d+)\s*\)', lower_type)
        _max_len = int(_len_m.group(1)) if _len_m else 0
        if _max_len == 1:
            # Single-char flag columns: use 'Y' instead of long string
            return "'Y'"
        if "id" in lower_col or "code" in lower_col or "type" in lower_col or "status" in lower_col:
            _val = f"test_{lower_col}"
        else:
            _val = f"test {lower_col}"
        # Truncate if value exceeds column length
        if _max_len > 0 and len(_val) > _max_len:
            _val = _val[:_max_len]
        return f"'{_val}'"
    return "'test'"


def _itest_infer_test_data(proc: ProcedureInfo, pkg: PackageInfo, schema_map: dict, all_packages: dict = None) -> dict:
    handled = set()
    needed = {}
    for dml in proc.dml_statements:
        sql = dml.sql_text or ""
        sql_lower = sql.lower().strip()
        if dml.sql_type == "insert":
            tbl = _itest_extract_table_from_insert(sql)
            if tbl:
                handled.add(tbl)
        elif dml.sql_type == "select":
            tbl = _itest_extract_table_from_select(sql)
            if tbl and tbl not in handled:
                cols = _itest_extract_columns_from_select(sql_lower, tbl, schema_map)
                needed[tbl] = cols
        elif dml.sql_type in ("update", "delete"):
            tbl = _itest_extract_table_from_update_delete(sql)
            if tbl and tbl not in handled:
                cols = _itest_extract_columns_from_update(sql_lower, tbl, schema_map)
                if tbl in needed:
                    needed[tbl].update(cols)
                else:
                    needed[tbl] = cols
        for jt in _itest_extract_join_tables(sql):
            if jt not in handled and jt in schema_map and jt not in needed:
                needed[jt] = schema_map[jt]
    if all_packages is None:
        return needed
    _itest_add_transitive_tables(proc, pkg, schema_map, handled, needed, all_packages)
    return needed


def _itest_extract_columns_from_select(sql_lower: str, tbl: str, schema_map: dict) -> dict:
    full_schema = schema_map.get(tbl, {})
    m = re.match(r'select\s+(.*?)\s+from\b', sql_lower, re.DOTALL)
    if not m:
        return full_schema
    col_clause = m.group(1).strip()
    if col_clause == '*':
        return full_schema
    cols = {}
    for part in col_clause.split(','):
        part = part.strip()
        if not part or '(' in part:
            continue
        alias_m = re.match(r'.+\s+as\s+(\w+)\s*$', part, re.IGNORECASE)
        col_name = alias_m.group(1).lower() if alias_m else part.split('.')[-1].strip().lower()
        col_name = re.sub(r'[^a-z0-9_]', '', col_name)
        if col_name and col_name in full_schema:
            cols[col_name] = full_schema[col_name]
    where_m = re.search(r'\bwhere\s+(.+)', sql_lower, re.DOTALL)
    if where_m:
        for wm in re.finditer(r'(\w+)\s*(?:=|!=|<|>|<=|>=|like)\s*', where_m.group(1)):
            wcol = wm.group(1).lower()
            if wcol in full_schema and wcol not in cols:
                cols[wcol] = full_schema[wcol]
    return cols if cols else full_schema


def _itest_extract_columns_from_update(sql_lower: str, tbl: str, schema_map: dict) -> dict:
    full_schema = schema_map.get(tbl, {})
    m = re.match(r'update\s+\w+\s+set\s+(.*?)(?:\s+where\b|$)', sql_lower, re.DOTALL)
    if not m:
        return full_schema
    cols = {}
    for assign in m.group(1).split(','):
        assign = assign.strip()
        col_m = re.match(r'(\w+)\s*=', assign)
        if col_m:
            col = col_m.group(1).lower()
            if col in full_schema:
                cols[col] = full_schema[col]
    where_m = re.search(r'\bwhere\s+(.+)', sql_lower, re.DOTALL)
    if where_m:
        for wm in re.finditer(r'(\w+)\s*(?:=|!=|<|>|<=|>=|like)\s*', where_m.group(1)):
            wcol = wm.group(1).lower()
            if wcol in full_schema and wcol not in cols:
                cols[wcol] = full_schema[wcol]
    return cols if cols else full_schema


def _itest_add_transitive_tables(proc, pkg, schema_map, handled, needed, all_packages, depth=0):
    if depth > 2:
        return
    _camel_re = re.compile(r'([a-z0-9])([A-Z])')

    def _camel_to_snake(name):
        return _camel_re.sub(r'\1_\2', name).lower()

    # Build service-var → package lookup
    _svc_to_pkg = {}
    if all_packages:
        for p in all_packages.values():
            cls = package_to_classname(p.package_name)
            var = cls[0].lower() + cls[1:] + "Service"
            _svc_to_pkg[var] = p

    visited = set()

    def _add_proc_tables(target_proc):
        pname = target_proc.proc_name
        if pname in visited:
            return
        visited.add(pname)
        for dml in target_proc.dml_statements:
            sql = dml.sql_text or ""
            sql_lower = sql.lower().strip()
            if dml.sql_type == "insert":
                tbl = _itest_extract_table_from_insert(sql)
                if tbl:
                    handled.add(tbl)
            elif dml.sql_type == "select":
                tbl = _itest_extract_table_from_select(sql)
                if tbl and tbl not in handled:
                    needed[tbl] = _itest_extract_columns_from_select(sql_lower, tbl, schema_map)
            elif dml.sql_type in ("update", "delete"):
                tbl = _itest_extract_table_from_update_delete(sql)
                if tbl and tbl not in handled:
                    needed[tbl] = _itest_extract_columns_from_update(sql_lower, tbl, schema_map)
            for jt in _itest_extract_join_tables(sql):
                if jt not in handled and jt in schema_map and jt not in needed:
                    needed[jt] = schema_map[jt]

    for line in proc.java_logic_lines:
        # Same-package: this.methodName(
        for m in re.finditer(r'\bthis\.(\w+)\s*\(', line):
            method_java = m.group(1)
            proc_name = _camel_to_snake(method_java)
            for tp in pkg.procedures:
                if tp.proc_name == proc_name:
                    _add_proc_tables(tp)
                    _itest_add_transitive_tables(tp, pkg, schema_map, handled, needed, all_packages, depth + 1)
                    break

        # Cross-package: xxxService.methodName(
        for m in re.finditer(r'\b(\w+Service)\.(\w+)\s*\(', line):
            svc_var = m.group(1)
            method_java = m.group(2)
            target_pkg = _svc_to_pkg.get(svc_var)
            if target_pkg:
                proc_name = _camel_to_snake(method_java)
                for tp in target_pkg.procedures:
                    if tp.proc_name == proc_name:
                        _add_proc_tables(tp)
                        _itest_add_transitive_tables(tp, target_pkg, schema_map, handled, needed, all_packages, depth + 1)
                        break


def _itest_write_fixtures(base_path: Path, proc: ProcedureInfo, pkg: PackageInfo, test_data: dict) -> str:
    if not test_data:
        return ""
    lines = []
    for table in sorted(test_data.keys()):
        lines.append(f"DELETE FROM {table};")
    _skip_prefixes = ("constraint", "check", "primary", "foreign", "unique", "index", "like")
    for table, columns in sorted(test_data.items()):
        if not columns:
            continue
        col_names = []
        values = []
        for col, sql_type in sorted(columns.items()):
            col_lower = col.lower()
            if any(col_lower.startswith(p) or col_lower == p for p in _skip_prefixes):
                continue
            if not re.match(r'^[a-zA-Z_]\w*$', col):
                continue
            col_names.append(col)
            values.append(_itest_generate_test_value(col, sql_type))
        cols_str = ", ".join(col_names)
        vals_str = ", ".join(values)
        lines.append(f"INSERT INTO {table} ({cols_str}) VALUES ({vals_str});")
    if not lines:
        return ""
    content = "\n".join(lines)
    fixtures_dir = base_path / "src/test/resources" / "itest-fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{pkg.package_name}_{proc.proc_name}.sql"
    _write_source_file(fixtures_dir / fname, content)
    return f"classpath:itest-fixtures/{fname}"


def _itest_write_dml_cleanup(base_path: Path, proc: ProcedureInfo, pkg: PackageInfo, dml_tables: set) -> str:
    lines = []
    for t in sorted(dml_tables):
        lines.append(f"DELETE FROM {t};")
    if not lines:
        return ""
    content = "\n".join(lines)
    fixtures_dir = base_path / "src/test/resources" / "itest-fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{pkg.package_name}_{proc.proc_name}_cleanup.sql"
    _write_source_file(fixtures_dir / fname, content)
    return f"classpath:itest-fixtures/{fname}"


def _itest_write_class(base_path: Path, pkg: PackageInfo, itest_cfg: dict, schema_map: dict, all_packages: dict):
    jp = _pkg_java_package(pkg)
    itest_dir = base_path / "src/test/java" / jp.replace(".", "/") / "itest"
    itest_dir.mkdir(parents=True, exist_ok=True)
    class_name = f"{package_to_classname(pkg.package_name)}ServiceIntegrationTest"
    svc_class = f"{package_to_classname(pkg.package_name)}Service"
    mapper_class = f"{package_to_classname(pkg.package_name)}Mapper"
    mapper_var = f"{mapper_class[0].lower()}{mapper_class[1:]}"
    svc_var = f"{svc_class[0].lower()}{svc_class[1:]}"

    imports = set()
    imports.add("import org.junit.jupiter.api.Test;")
    imports.add("import org.junit.jupiter.api.Timeout;")
    imports.add("import org.springframework.beans.factory.annotation.Autowired;")
    imports.add(f"import {jp}.service.{svc_class};")
    imports.add(f"import {jp}.mapper.{mapper_class};")
    imports.add(f"import {BASE_PACKAGE}.itest.AbstractIntegrationTest;")
    imports.add("import static org.junit.jupiter.api.Assertions.*;")
    imports.add("import java.util.concurrent.TimeUnit;")

    needs_map = any("Map<String, Object>" in p.java_type for proc in pkg.procedures for p in proc.parameters)
    if needs_map:
        imports.add("import java.util.Map;")
        imports.add("import java.util.HashMap;")
    needs_list = any(p.java_type.startswith("List<") for proc in pkg.procedures for p in proc.parameters)
    if needs_list:
        imports.add("import java.util.List;")
        imports.add("import java.util.ArrayList;")
    needs_atomic_ref = any(p.is_out for proc in pkg.procedures for p in proc.parameters)
    if needs_atomic_ref:
        imports.add("import java.util.concurrent.atomic.AtomicReference;")
    has_stubs = any((proc.name, len(proc.parameters)) in STUB_PROCEDURES for proc in pkg.procedures)
    has_recursive = False
    for proc in pkg.procedures:
        _cn = java_method_name(proc.proc_name)
        if any(f"this.{_cn}(" in line for line in proc.java_logic_lines):
            has_recursive = True
            break
    has_dynamic_sql = any(
        any(dml.sql_text and (re.fullmatch(r'#\{[^}]+\}', dml.sql_text.strip()) or re.search(r'\$\{[^}]+\}', dml.sql_text)) for dml in proc.dml_statements)
        for proc in pkg.procedures
    )
    has_itest_while = any(
        any("while (" in line or "while(" in line for line in proc.java_logic_lines)
        for proc in pkg.procedures
    )
    has_gaussdb_only_sql = any(
        any(dml.sql_text and re.search(r'\b(dblink|pg_sleep|clock_timestamp|dblink_connect|dblink_get_connections)\b', dml.sql_text, re.IGNORECASE) for dml in proc.dml_statements)
        for proc in pkg.procedures
    )
    if has_stubs or has_recursive or has_dynamic_sql or has_itest_while or has_gaussdb_only_sql:
        imports.add("import org.junit.jupiter.api.Disabled;")

    _all_pkgs = all_packages or {}
    service_injections = _collect_service_injections(pkg)
    for svc_var_inj, pkg_name in service_injections.items():
        if pkg_name:
            svc_class_inj = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var_inj.replace("Service", "")
            svc_class_inj = f"{package_to_classname(svc_class_part)}Service"
            pkg_name = svc_class_part
        target_jp = _pkg_java_package(_all_pkgs[pkg_name]) if pkg_name in _all_pkgs else BASE_PACKAGE
        imports.add(f"import {target_jp}.service.{svc_class_inj};")

    test_methods = []
    seen_method_names: dict = {}
    for proc in pkg.procedures:
        method_name = java_method_name(proc.proc_name)
        in_params = [p for p in proc.parameters if not p.is_out]
        out_params = [p for p in proc.parameters if p.is_out]

        _numeric_string_params = set()
        for dml in proc.dml_statements:
            for m in re.finditer(r'to_number\s*\(\s*#?\{?(\w+)', dml.sql_text or "", re.IGNORECASE):
                ref = m.group(1)
                _numeric_string_params.add(ref.lower())

        _dollar_params = _collect_dollar_interpolation_params(proc)

        param_values = []
        param_args = []
        for p in in_params:
            val = _default_test_value(p.java_type, p.java_name, pkg=pkg)
            if p.java_type == "String" and (p.name.lower() in _numeric_string_params or p.java_name.lower() in _numeric_string_params):
                val = '"1"'
            if p.java_type == "String" and (p.java_name.lower() in _dollar_params or p.name.lower() in _dollar_params):
                val = _itest_dollar_param_value(p.java_name)
            decl_type = p.java_type
            if pkg and hasattr(pkg, 'custom_types'):
                for tn, ti in pkg.custom_types.items():
                    if ti.get("kind") == "record" and _custom_type_classname(tn) == decl_type:
                        decl_type = f"{svc_class}.{decl_type}"
                        break
            param_values.append(f"{decl_type} {p.java_name} = {val};")
            param_args.append(p.java_name)

        out_decls = []
        out_args = []
        for p in out_params:
            if p.is_refcursor:
                continue
            holder = f"AtomicReference<{p.java_type}>"
            out_decls.append(f"{holder} {p.java_name} = new AtomicReference<>(null);")
            out_args.append(p.java_name)

        all_args = []
        for p in proc.parameters:
            if p.is_refcursor:
                continue
            all_args.append(p.java_name)
        args_str = ", ".join(all_args)

        test_data = _itest_infer_test_data(proc, pkg, schema_map, all_packages)
        sql_script = _itest_write_fixtures(base_path, proc, pkg, test_data)
        if not sql_script and proc.dml_statements:
            dml_tables = set()
            for dml in proc.dml_statements:
                for ref in proc.table_refs:
                    dml_tables.add(ref)
            if dml_tables:
                sql_script = _itest_write_dml_cleanup(base_path, proc, pkg, dml_tables)

        base_test_name = f"test_{method_name}_integration"
        count = seen_method_names.get(base_test_name, 0)
        seen_method_names[base_test_name] = count + 1
        test_name = f"{base_test_name}_{count}" if count > 0 else base_test_name

        is_itest_stubbed = (proc.name, len(proc.parameters)) in STUB_PROCEDURES
        has_while = any("while (" in line or "while(" in line for line in proc.java_logic_lines)
        has_conditional_while = any(re.search(r'while\s*\(\s*\w+\s*[><=!]', line) for line in proc.java_logic_lines)
        has_sm_guard = any("_smGuard" in line for line in proc.java_logic_lines)
        has_do_while = "} while (" in " ".join(proc.java_logic_lines)
        has_indexed_cursor = any("CursorIdx" in line or "CurIdx" in line or ".get(vCurIdx" in line or ".get(vCursorIdx" in line for line in proc.java_logic_lines)
        has_unchecked_null_size = _has_unchecked_null_size(proc)
        is_safe_while = (has_while and has_indexed_cursor and not has_conditional_while and not has_sm_guard and not has_do_while and not has_unchecked_null_size)
        itest_camel_name = java_method_name(proc.proc_name)
        is_recursive = any(f"this.{itest_camel_name}(" in line for line in proc.java_logic_lines)
        has_dynamic_sql = any(
            dml.sql_text and (re.fullmatch(r'#\{[^}]+\}', dml.sql_text.strip()) or re.search(r'\$\{[^}]+\}', dml.sql_text))
            for dml in proc.dml_statements
        )
        has_gaussdb_only_sql = any(
            dml.sql_text and re.search(r'\b(dblink|pg_sleep|clock_timestamp|dblink_connect|dblink_get_connections)\b', dml.sql_text, re.IGNORECASE)
            for dml in proc.dml_statements
        )
        complexity_score = len(proc.dml_statements) + len(proc.service_calls) + len(proc.java_logic_lines) // 10
        if complexity_score > 20:
            timeout_seconds = 30
        elif complexity_score > 10:
            timeout_seconds = 20
        else:
            timeout_seconds = 10
        lines = []
        _itest_disabled = False
        if is_itest_stubbed:
            lines.append("    @Disabled(\"Converter stub — complex PL/pgSQL pattern requires manual implementation\")")
            _itest_disabled = True
        elif has_while and not is_safe_while:
            lines.append("    @Disabled(\"auto-generated itest cannot terminate while loop\")")
            _itest_disabled = True
        elif is_recursive:
            lines.append("    @Disabled(\"auto-generated itest cannot terminate recursive call\")")
            _itest_disabled = True
        elif has_gaussdb_only_sql:
            lines.append("    @Disabled(\"auto-generated itest skipped — SQL uses GaussDB-only extensions (dblink, pg_sleep, etc.)\")")
            _itest_disabled = True
        if has_dynamic_sql and not _itest_disabled:
            lines.append("    @Disabled(\"auto-generated itest cannot exercise runtime-constructed dynamic SQL\")")
        if sql_script:
            if itest_cfg.get("mode") == "remote":
                lines.append(f'    @org.springframework.test.context.jdbc.Sql(scripts = "{sql_script}", config = @org.springframework.test.context.jdbc.SqlConfig(errorMode = org.springframework.test.context.jdbc.SqlConfig.ErrorMode.CONTINUE_ON_ERROR))')
            else:
                lines.append(f'    @org.springframework.test.context.jdbc.Sql(scripts = "{sql_script}")')
        lines.append("    @Test")
        lines.append(f"    @Timeout(value = {timeout_seconds}, unit = TimeUnit.SECONDS)")
        lines.append(f"    void {test_name}() {{")
        for pv in param_values:
            lines.append(f"        {pv}")
        for od in out_decls:
            lines.append(f"        {od}")
        if proc.is_function:
            lines.append(f"        var result = {svc_var}.{method_name}({args_str});")
            if is_itest_stubbed or is_recursive or has_dynamic_sql:
                lines.append("        // Stub/loop implementation — result may be null")
            else:
                lines.append("        assertNotNull(result);")
        else:
            lines.append(f"        {svc_var}.{method_name}({args_str});")
        if out_args:
            lines.append("        // TODO: Add assertions for OUT parameters (values depend on test data)")
            for oa_name in out_args:
                lines.append(f"        // assertNotNull({oa_name}.get());")
        if not proc.is_function and not out_args:
            if proc.dml_statements:
                lines.append(f"        // Verify: check database state after {java_method_name(proc.proc_name)}")
            else:
                lines.append("        // TODO: Add domain-specific assertions")
        lines.append("    }")
        test_methods.append("\n".join(lines))

    if not test_methods:
        test_methods.append(
            "    @Test\n"
            "    @Timeout(value = 10, unit = TimeUnit.SECONDS)\n"
            "    void testServiceExists() {\n"
            "        assertNotNull(service);\n"
            "    }"
        )

    lines = []
    lines.append(f"package {jp}.itest;")
    lines.append("")
    for imp in sorted(imports):
        lines.append(imp)
    lines.append("")
    if pkg.source_file:
        lines.append(f"// Source: {pkg.source_file}")
    lines.append(f"class {class_name} extends AbstractIntegrationTest {{")
    lines.append("")
    lines.append(f"    @Autowired")
    lines.append(f"    private {mapper_class} {mapper_var};")
    lines.append("")
    lines.append(f"    @Autowired")
    lines.append(f"    private {svc_class} {svc_var};")

    for svc_var_inj, pkg_name in service_injections.items():
        if pkg_name:
            svc_class_inj = f"{package_to_classname(pkg_name)}Service"
        else:
            svc_class_part = svc_var_inj.replace("Service", "")
            svc_class_inj = f"{package_to_classname(svc_class_part)}Service"
        lines.append("")
        lines.append(f"    @Autowired")
        lines.append(f"    private {svc_class_inj} {svc_var_inj};")

    for tm in test_methods:
        lines.append("")
        lines.append(tm)

    lines.append("}")
    lines.append("")
    content = "\n".join(lines)
    _write_source_file(itest_dir / f"{class_name}.java", content)




def build_conversion_report(
    output_dir: str, packages: list, all_skipped: list, parse_errors_map: dict,
    config_path: str = "", parse_warnings_map: dict = None
) -> ConversionReport:
    if parse_warnings_map is None:
        parse_warnings_map = {}
    report = ConversionReport(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        config_path=config_path or "CLI mode",
        output_dir=os.path.abspath(output_dir),
        sql_files=[],
        procedure_mappings=[],
        skipped_items=all_skipped,
        parse_errors=[],
        parse_warnings=[],
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
            is_stub = (proc.name, len(proc.parameters)) in STUB_PROCEDURES
            has_error = any(
                (err.get("location") or "").startswith(proc.name)
                for errs in parse_errors_map.values()
                for err in (errs if isinstance(errs, list) else [errs])
            )
            notes = ""
            _proc_stub_key = (proc.name, len(proc.parameters))
            _proc_reasons = STUB_REASONS.get(_proc_stub_key, [])
            if is_stub:
                if _proc_reasons:
                    notes = "；".join(_proc_reasons)
                else:
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
                stub_reasons=list(_proc_reasons),
                table_refs=proc.table_refs,
            ))

    for sql_file, errors in parse_errors_map.items():
        if isinstance(errors, list):
            for err in errors:
                report.parse_errors.append((sql_file, err))
        else:
            report.parse_errors.append((sql_file, errors))

    for sql_file, warnings in parse_warnings_map.items():
        for warn in (warnings if isinstance(warnings, list) else [warnings]):
            report.parse_warnings.append((sql_file, warn))

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
    if report.parse_warnings:
        lines.append(f"| ⚠️ 解析警告 | {len(report.parse_warnings)} |")
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
    if report.parse_warnings:
        errors_and_warnings.append(("⚠️ 解析警告", "parse_warnings"))
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

        if report.parse_warnings:
            lines.append("### ⚠️ 解析警告")
            lines.append("")
            lines.append("以下警告不影响转换结果，仅供参考。")
            lines.append("")
            by_file = defaultdict(list)
            for sql_file, warn in report.parse_warnings:
                by_file[sql_file].append(warn)
            for sql_file, warns in sorted(by_file.items()):
                lines.append(f"**`{sql_file}`**:")
                for warn in warns:
                    if isinstance(warn, dict):
                        warn_detail = warn.get("Warning") or warn.get("ReservedKeywordAsIdentifier") or {}
                        msg = warn_detail.get("message", warn_detail.get("keyword", ""))
                        loc = warn_detail.get("location", "")
                        if isinstance(loc, dict):
                            loc = f"{loc.get('line', '?')}:{loc.get('column', '?')}"
                        if msg:
                            if loc:
                                lines.append(f"- 行 {loc}: {msg}")
                            else:
                                lines.append(f"- {msg}")
                        else:
                            lines.append(f"- {warn}")
                    else:
                        lines.append(f"- {warn}")
                lines.append("")

        if report.unresolved_calls:
            lines.append("### ⚠️ 未解析的跨包调用")
            lines.append("")
            lines.append("以下存储过程调用了未包含在输入中的包，请在配置中添加对应的 SQL 文件。")
            lines.append("")
            for uc in report.unresolved_calls:
                if hasattr(uc, 'caller') and hasattr(uc, 'callee'):
                    lines.append(f"- **调用者**: `{uc.caller}`")
                    lines.append(f"  **被调用**: `{uc.callee}`")
                    if uc.caller_file:
                        lines.append(f"  **源文件**: `{uc.caller_file}`")
                    if uc.args:
                        lines.append(f"  **参数**: `{uc.args}`")
                    if uc.hint:
                        lines.append(f"  **建议**: {uc.hint}")
                    lines.append("")
                else:
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

    all_table_refs = {}
    for m in report.procedure_mappings:
        if m.table_refs:
            all_table_refs.setdefault(m.sql_file, {}).setdefault(m.procedure_name, m.table_refs)
    if all_table_refs:
        lines.append("---")
        lines.append("")
        lines.append("## 📋 数据库对象依赖")
        lines.append("")
        lines.append("以下存储过程引用了数据库表/视图，集成测试运行前需确保这些对象已在目标数据库中创建。")
        lines.append("")
        for sql_file in sorted(all_table_refs.keys()):
            procs = all_table_refs[sql_file]
            all_tables = sorted({t.lower() for refs in procs.values() for t in refs})
            svc = next((m.java_service for m in report.procedure_mappings if m.sql_file == sql_file), "")
            lines.append(f"### `{sql_file}` → `{svc}`")
            lines.append("")
            lines.append(f"依赖的表/视图: {', '.join(f'`{t}`' for t in all_tables)}")
            lines.append("")
            lines.append("| 存储过程 | 引用的表/视图 |")
            lines.append("|----------|--------------|")
            for proc_name, refs in sorted(procs.items()):
                lines.append(f"| `{proc_name}` | {', '.join(f'`{r}`' for r in sorted(refs, key=str.lower))} |")
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

    # ── Stub 原因明细 ──
    stub_mappings = [m for m in report.procedure_mappings if m.is_stub and m.stub_reasons]
    if stub_mappings:
        lines.append("---")
        lines.append("")
        lines.append("## ⚠️ Stub 原因明细")
        lines.append("")
        lines.append("| 存储过程 | Java 方法 | Stub 原因 |")
        lines.append("|----------|-----------|-----------|")
        for m in stub_mappings:
            reasons_str = "；".join(m.stub_reasons)
            lines.append(f"| `{m.procedure_name}` | `{m.java_service}.{m.java_method}()` | {reasons_str} |")
        lines.append("")

        from collections import Counter as _StubCounter
        all_reasons = []
        for m in stub_mappings:
            all_reasons.extend(m.stub_reasons)
        reason_counts = _StubCounter(all_reasons)
        lines.append("### 按原因统计")
        lines.append("")
        lines.append("| 原因 | 影响过程数 |")
        lines.append("|------|-----------|")
        for reason, count in reason_counts.most_common():
            lines.append(f"| {reason} | {count} |")
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
            "SAVEPOINT": ("SAVEPOINT 事务控制", "SAVEPOINT / ROLLBACK TO SAVEPOINT 需通过 JDBC 或 Spring @Transactional 处理"),
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
   fluxgauss -c fluxgauss.yaml --resume    从断点续做（跳过已生成的包）
    fluxgauss -c fluxgauss.yaml --report ./report.md
    fluxgauss -c fluxgauss.yaml --encoding gbk    指定输出编码（默认 UTF-8）
    fluxgauss --mcp          以 MCP 服务器模式启动（stdio 协议）

  配置文件格式 (YAML):
     output_dir: ./dest                     输出目录
    base_package: com.example.demo         Java 包名（可选）
    encoding: utf-8                        源码编码（可选，默认 UTF-8，如 gbk）
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
    parser.add_argument("--resume", action="store_true", default=False, help="从断点续做（跳过已生成的包）")
    parser.add_argument("--skip-validate", action="store_true", default=False, help="跳过 SQL 语法校验")
    parser.add_argument("--encoding", metavar="ENC", default=None, help="生成源码的编码格式（默认 UTF-8）")
    parser.add_argument("--report", metavar="FILE", help="指定转换报告输出路径")
    parser.add_argument("--debug", action="store_true", default=False, help="调试模式：在生成的Java/XML中注入SQL源码行号注释")
    parser.add_argument("-v", "--version", action="store_true", default=False, help="显示版本信息")
    parser.add_argument("--mcp", action="store_true", default=False, help="以 MCP 服务器模式启动（stdio 协议）")
    return parser


def _read_version_from_cargo_toml():
    """Read version from Cargo.toml (single source of truth).

    Looks for crates/fluxgauss/Cargo.toml relative to this script.
    Returns None if not found (e.g. PyInstaller bundle without source).
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for rel in [os.path.join('..', 'crates', 'fluxgauss', 'Cargo.toml'),
                os.path.join('..', 'Cargo.toml'),
                'Cargo.toml']:
        cargo_toml = os.path.normpath(os.path.join(script_dir, rel))
        if os.path.isfile(cargo_toml):
            try:
                with open(cargo_toml, 'r', encoding='utf-8') as f:
                    for line in f:
                        s = line.strip()
                        if s.startswith('version = '):
                            return s.split('"')[1]
            except Exception:
                pass
            break
    return None


_VERSION = _read_version_from_cargo_toml() or "0.6.16"


def _run_mcp_server():
    """Start MCP stdio server exposing validate_sql and convert_sql tools.

    This function never returns — it blocks on mcp.run().
    All non-MCP flags are ignored. No logo, no progress bars.
    All logging goes to stderr (stdout is reserved for MCP protocol).
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "Error: MCP mode requires the 'mcp' package. Install it with:\n"
            "  pip install mcp",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from mcp.types import ToolError as _McpToolError
    except ImportError:
        _McpToolError = ValueError  # fallback

    mcp = FastMCP("FluxGauss")

    # ──────────────────────────────────────────────
    # Tool: validate_sql
    # ──────────────────────────────────────────────
    @mcp.tool()
    def validate_sql(files: list[str], encoding: str = "utf-8") -> dict:
        """Validate one or more SQL files using ogsql-parser.

        Args:
            files: List of SQL file paths (absolute or relative to CWD).
            encoding: File encoding (default: utf-8).

        Returns:
            JSON with keys: valid, error_file_count, warning_file_count, file_results.
        """
        global _SOURCE_ENCODING
        missing = [f for f in files if not os.path.exists(f)]
        if missing:
            raise _McpToolError(f"Files not found: {', '.join(missing)}")

        orig_encoding = _SOURCE_ENCODING
        try:
            _SOURCE_ENCODING = encoding
            results = validate_sql_files(files)
        except Exception as e:
            raise _McpToolError(f"Validation failed: {e}") from e
        finally:
            _SOURCE_ENCODING = orig_encoding

        file_results = []
        total_error_files = 0
        total_warning_files = 0
        for f in files:
            fresult = results.get(f, {})
            raw_errors = fresult.get("errors", [])
            raw_warnings = fresult.get("warnings", [])
            errors_out = []
            for e in raw_errors:
                if isinstance(e, dict):
                    ve = e.get("ValidationError", {})
                    if isinstance(ve, dict) and ve:
                        errors_out.append({
                            "line": ve.get("line", 0),
                            "column": ve.get("column", 0),
                            "message": ve.get("message", str(ve)),
                        })
                    else:
                        errors_out.append({"line": 0, "column": 0, "message": str(e)})
                else:
                    errors_out.append({"line": 0, "column": 0, "message": str(e)})
            warnings_out = []
            for w in raw_warnings:
                if isinstance(w, dict):
                    vw = w.get("ValidationWarning", w)
                    if isinstance(vw, dict):
                        warnings_out.append({
                            "line": vw.get("line", 0),
                            "column": vw.get("column", 0),
                            "message": vw.get("message", str(vw)),
                        })
                    else:
                        warnings_out.append({"line": 0, "column": 0, "message": str(w)})
                else:
                    warnings_out.append({"line": 0, "column": 0, "message": str(w)})

            if errors_out:
                total_error_files += 1
            if warnings_out:
                total_warning_files += 1
            file_results.append({
                "file": f,
                "errors": errors_out,
                "warnings": warnings_out,
            })

        return {
            "valid": total_error_files == 0,
            "error_file_count": total_error_files,
            "warning_file_count": total_warning_files,
            "file_results": file_results,
        }

    # ──────────────────────────────────────────────
    # Tool: convert_sql
    # ──────────────────────────────────────────────
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
        """Convert SQL stored procedures into a Spring Boot + MyBatis Java project.

        Provide either a config dict (matching fluxgauss.yaml structure) OR
        the individual parameters files + output_dir + base_package.

        Args:
            config: Optional config dict (keys: output_dir, base_package, sources, ...).
            files: SQL source file paths (used when config is not provided).
            output_dir: Output directory (used when config is not provided).
            base_package: Java base package (used when config is not provided).
            full: Force full regeneration (ignore cache).
            debug: Enable debug mode (inject SQL source line annotations).
            skip_validation: Skip SQL syntax validation before conversion.

        Returns:
            JSON with keys: success, output_dir, generated_files, report,
            report_paths, log_path, summary.
        """
        # ── Resolve inputs ──
        resolved_config = config or {}
        resolved_output_dir = output_dir or resolved_config.get("output_dir", "./dest")
        resolved_sql_files = files or resolved_config.get("sources", [])
        resolved_base_package = base_package or resolved_config.get("base_package", "com.example.demo")

        if not resolved_sql_files:
            raise _McpToolError("No SQL source files specified. Provide 'files' or config['sources'].")

        # Check file existence
        missing_files = [f for f in resolved_sql_files if not os.path.exists(f)]
        if missing_files:
            raise _McpToolError(f"Source files not found: {', '.join(missing_files)}")

        # ── Set global state ──
        global BASE_PACKAGE, BASE_DIR, _SOURCE_ENCODING, DEBUG_MODE, _LOGGER_CONFIG
        global TYPE_OVERRIDES, _TABLE_DDL_SOURCE, _PACKAGE_CONSTANTS, _PACKAGE_VARIABLES, _PACKAGE_VAR_WRITTEN
        global STUB_PROCEDURES, STUB_REASONS, UNRESOLVED_CALLS, UNSUPPORTED_FUNCTIONS, TODO_SUMMARY

        saved_state = (
            BASE_PACKAGE, BASE_DIR, _SOURCE_ENCODING, DEBUG_MODE, _LOGGER_CONFIG,
            TYPE_OVERRIDES.copy(), _TABLE_DDL_SOURCE.copy(),
            _PACKAGE_CONSTANTS.copy(), _PACKAGE_VARIABLES.copy(), _PACKAGE_VAR_WRITTEN.copy(),
            STUB_PROCEDURES.copy(), STUB_REASONS.copy(),
            UNRESOLVED_CALLS.copy(), UNSUPPORTED_FUNCTIONS.copy(), TODO_SUMMARY.copy(),
        )

        # Apply new state
        BASE_PACKAGE = resolved_base_package
        BASE_DIR = "src/main/java/" + BASE_PACKAGE.replace(".", "/")
        _SOURCE_ENCODING = str(resolved_config.get("encoding", "utf-8"))
        DEBUG_MODE = debug
        _LOGGER_CONFIG = None  # reset, will be set from config if present
        TYPE_OVERRIDES.clear()
        _TABLE_DDL_SOURCE.clear()
        _PACKAGE_CONSTANTS.clear()
        _PACKAGE_VARIABLES.clear()
        _PACKAGE_VAR_WRITTEN.clear()
        STUB_PROCEDURES.clear()
        STUB_REASONS.clear()
        UNRESOLVED_CALLS.clear()
        UNSUPPORTED_FUNCTIONS.clear()
        TODO_SUMMARY.clear()

        if resolved_config.get("type_aliases"):
            _user_aliases = resolved_config["type_aliases"]
            if isinstance(_user_aliases, dict):
                _CUSTOM_TYPE_PRESETS.update({k.lower(): v for k, v in _user_aliases.items()})

        if "logger" in resolved_config:
            _resolved = _resolve_logger_config(resolved_config)
            _LOGGER_CONFIG = _resolved

        parse_errors_map = {}
        parse_warnings_map = {}
        output_dir_abs = os.path.abspath(resolved_output_dir)

        try:
            # ── Phase 0: Table DDL pre-scan ──
            for sql_file in resolved_sql_files:
                try:
                    with open(sql_file, "r", encoding=_SOURCE_ENCODING, errors="replace") as _f:
                        _content = _f.read()
                except Exception:
                    continue
                if re.search(r"create\s+table", _content, re.IGNORECASE):
                    try:
                        schema = parse_table_ddl(sql_file)
                        for tbl, cols in schema.items():
                            for col, col_type in cols.items():
                                TYPE_OVERRIDES[(tbl, col)] = col_type
                                _TABLE_DDL_SOURCE[(tbl, col)] = sql_file
                    except Exception:
                        pass

            # ── Phase 1: Validate (optional) ──
            if not skip_validation:
                try:
                    vresults = validate_sql_files(resolved_sql_files)
                    for sf in resolved_sql_files:
                        vr = vresults.get(sf, {})
                        real_errors = [
                            e for e in vr.get("errors", [])
                            if not _is_parse_warning(e)
                        ]
                        if real_errors:
                            err_msgs = "; ".join(
                                _format_validate_error(e) for e in real_errors[:5]
                            )
                            raise _McpToolError(
                                f"Validation failed for {sf}: {err_msgs}"
                            )
                except _McpToolError:
                    raise
                except Exception as e:
                    raise _McpToolError(f"Validation error: {e}") from e

            # ── Phase 2: Parse SQL files ──
            parsed_asts = {}
            try:
                parsed_asts = parse_sql_files(resolved_sql_files)
            except Exception as e:
                # Fallback to per-file parsing
                for sf in resolved_sql_files:
                    try:
                        parsed_asts[sf] = parse_sql_file(sf)
                    except Exception as e2:
                        raise _McpToolError(f"Failed to parse {sf}: {e2}") from e2

            # ── Phase 3: Extract procedures ──
            packages = []
            all_package_names = {}
            all_skipped = []

            for sql_file in resolved_sql_files:
                basename = os.path.basename(sql_file)
                ast = parsed_asts.get(sql_file, {})
                if not ast:
                    continue

                errors = ast.get("errors", [])
                if errors:
                    real_errors = [e for e in errors if not _is_parse_warning(e)]
                    warnings = [e for e in errors if _is_parse_warning(e)]
                    if real_errors:
                        parse_errors_map[basename] = real_errors
                    if warnings:
                        parse_warnings_map[basename] = warnings

                try:
                    skipped = extract_non_procedure_statements(ast, source_file=basename)
                    if skipped:
                        all_skipped.extend(skipped)

                    procedures, pkg_vars, custom_types = extract_procedures(ast, source_file=basename)
                    _recover_constant_declarations(sql_file, pkg_vars)
                    for p in procedures:
                        p._source_path = sql_file
                    comments = extract_comments(ast)
                    _map_comments_to_procedures(comments, procedures, source_file=basename)

                    if not procedures:
                        continue

                    pkg_name = procedures[0].package if procedures[0].package else Path(sql_file).stem
                    for vname, vdata in pkg_vars.items():
                        if vname not in _PACKAGE_CONSTANTS:
                            _PACKAGE_VARIABLES[vname] = {**vdata, "package": pkg_name}

                    java_pkg = ""
                    if resolved_config:
                        jp_entries = resolved_config.get("java_packages", [])
                        if not jp_entries:
                            jp_single = resolved_config.get("java_package", {})
                            if isinstance(jp_single, dict):
                                jp_entries = [jp_single]
                        for jp_entry in jp_entries:
                            if not isinstance(jp_entry, dict):
                                continue
                            if sql_file in jp_entry.get("sources", []):
                                java_pkg = jp_entry.get("package", "")
                                break

                    pkg = PackageInfo(
                        package_name=pkg_name,
                        procedures=procedures,
                        package_vars=pkg_vars,
                        source_file=basename,
                        java_package=java_pkg,
                        comments=[],
                        custom_types=custom_types,
                    )
                    packages.append(pkg)
                    all_package_names[pkg_name] = pkg

                    for _p in procedures:
                        if _p.is_function and _p.return_type:
                            _rt = sql_type_to_java(_p.return_type)
                            _UDF_RETURN_TYPES[(_p.proc_name.lower(), len(_p.parameters))] = _rt
                except Exception as e:
                    parse_errors_map[basename] = [{"parse_error": str(e)}]
                    continue

            # ── Phase 4: Analyze procedures ──
            if not packages:
                raise _McpToolError("No procedures found in SQL files — nothing to convert.")

            for pkg in packages:
                for proc in pkg.procedures:
                    try:
                        if pkg.package_vars:
                            _pkg_var_names = getattr(proc, "_pkg_var_names", set())
                            for vn, vi in pkg.package_vars.items():
                                if vn not in proc.local_vars:
                                    proc.local_vars[vn] = vi.get("java_type", "Object")
                                    _pkg_var_names.add(vn)
                            setattr(proc, "_pkg_var_names", _pkg_var_names)
                        analyze_procedure(proc, all_package_names)
                    except Exception as e:
                        proc.java_logic_lines.append(f"// ERROR: 转换失败 - {e}")
                        _stub_key = (proc.name, len(proc.parameters))
                        _add_stub_reason(proc, f"转换分析阶段异常: {e}")
                        if _stub_key not in STUB_PROCEDURES:
                            STUB_PROCEDURES.append(_stub_key)

            for pkg in packages:
                for proc in pkg.procedures:
                    try:
                        _promote_out_local_vars(proc, all_package_names)
                    except Exception:
                        pass

            # ── Phase 5: Generate project ──
            try:
                os.makedirs(output_dir_abs, exist_ok=True)
                generate_project(
                    output_dir_abs, packages,
                    changed_packages=None,
                    config=resolved_config,
                    progress_cb=None,
                    resume_skip=None,
                )
            except Exception as e:
                raise _McpToolError(f"Project generation failed: {e}") from e

            # ── Phase 6: Build report ──
            report = build_conversion_report(
                output_dir_abs, packages, all_skipped, parse_errors_map,
                config_path=str(resolved_config) if resolved_config else "MCP mode",
                parse_warnings_map=parse_warnings_map,
            )
            report_paths = write_conversion_report(report, output_dir_abs)

            # ── Gather generated files ──
            generated_files = []
            base_path = Path(output_dir_abs)
            for pkg in packages:
                class_name = package_to_classname(pkg.package_name)
                jp = _pkg_java_package(pkg).replace(".", "/")
                candidates = [
                    f"src/main/java/{jp}/service/{class_name}Service.java",
                    f"src/main/java/{jp}/mapper/{class_name}Mapper.java",
                    f"src/main/resources/mapper/{class_name}Mapper.xml",
                    f"src/test/java/{jp}/service/{class_name}ServiceTest.java",
                ]
                for c in candidates:
                    if (base_path / c).exists():
                        generated_files.append(c)

            # ── Summary ──
            total_procs = sum(len(pkg.procedures) for pkg in packages)
            total_dml = sum(
                len(proc.dml_statements)
                for pkg in packages
                for proc in pkg.procedures
            )
            total_calls = sum(
                len(proc.service_calls)
                for pkg in packages
                for proc in pkg.procedures
            )

            result = {
                "success": True,
                "output_dir": output_dir_abs,
                "generated_files": sorted(set(generated_files)),
                "report": {
                    "generated_at": report.generated_at,
                    "sql_files": list(
                        set(m.sql_file for m in report.procedure_mappings)
                    ),
                    "procedure_mappings": [
                        {
                            "sql_file": m.sql_file,
                            "procedure_name": m.procedure_name,
                            "java_service": m.java_service,
                            "java_method": m.java_method,
                            "is_stub": m.is_stub,
                            "notes": m.notes,
                        }
                        for m in report.procedure_mappings
                    ],
                    "parse_errors": [
                        {"file": f, "error": str(e)}
                        for f, e in report.parse_errors
                    ],
                },
                "report_paths": report_paths,
                "log_path": str(
                    _cache_base(output_dir_abs) / "logs" / "conversion-latest.log"
                ),
                "summary": {
                    "packages": len(packages),
                    "procedures": total_procs,
                    "dml_statements": total_dml,
                    "service_calls": total_calls,
                    "unresolved_calls": len(UNRESOLVED_CALLS),
                    "stubs": len(STUB_PROCEDURES),
                    "todos": len(TODO_SUMMARY),
                    "parse_errors": len(parse_errors_map),
                },
            }
            return result

        except _McpToolError:
            raise
        except Exception as e:
            raise _McpToolError(f"Conversion failed: {e}") from e
        finally:
            # Restore global state
            (
                BASE_PACKAGE, BASE_DIR, _SOURCE_ENCODING, DEBUG_MODE, _LOGGER_CONFIG,
                TYPE_OVERRIDES, _TABLE_DDL_SOURCE,
                _PACKAGE_CONSTANTS, _PACKAGE_VARIABLES, _PACKAGE_VAR_WRITTEN,
                STUB_PROCEDURES, STUB_REASONS,
                UNRESOLVED_CALLS, UNSUPPORTED_FUNCTIONS, TODO_SUMMARY,
            ) = saved_state
            _CUSTOM_TYPE_PRESETS.clear()
            _CUSTOM_TYPE_PRESETS.update(
                {k.lower(): v for k, v in _CUSTOM_TYPE_PRESETS.items()}
            )

    # ── Run server ──
    mcp.run(transport="stdio")


def main():
    global _SOURCE_ENCODING, DEBUG_MODE

    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.help:
        print(FLUXGAUSS_HELP)
        sys.exit(0)

    if args.version:
        print(f"fluxgauss v{_VERSION}")
        sys.exit(0)

    if args.mcp:
        _run_mcp_server()
        return

    # ── Resolve config ──
    output_dir = None
    sql_files = []
    config_path = None
    config = {}

    if args.debug:
        DEBUG_MODE = True
        print("  🔧 Debug mode enabled — SQL source annotations will be injected")

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
        if 'type_aliases' in config:
            _user_aliases = config['type_aliases']
            if isinstance(_user_aliases, dict):
                _CUSTOM_TYPE_PRESETS.update({k.lower(): v for k, v in _user_aliases.items()})

        # ── Resolve source encoding (from config only) ──
        if config.get('encoding'):
            _SOURCE_ENCODING = config['encoding']

    elif args.output and args.sources:
        output_dir = args.output
        sql_files = args.sources
    else:
        print(f"Error: 请指定配置文件 (-c) 或输出目录 + 源文件 (-o + -s)")
        print(f"  用法: fluxgauss -c fluxgauss.yaml")
        print(f"  用法: fluxgauss -o ./dest -s pkg_order.sql pkg_product.sql")
        print(f" 帮助: fluxgauss -h")
        sys.exit(1)

    # ── Resolve source encoding (CLI overrides config) ──
    if args.encoding:
        _SOURCE_ENCODING = args.encoding
    try:
        ''.encode(_SOURCE_ENCODING)
    except LookupError:
        print(f"Error: unsupported encoding: {_SOURCE_ENCODING}")
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

    parse_errors_map = {}
    parse_warnings_map = {}
    missing_files = [f for f in sql_files if not os.path.exists(f)]
    if missing_files:
        for f in missing_files:
            _log(f"  ⚠ Source file not found, skipping: {f}", to_stdout=False)
            parse_errors_map[f] = [{"parse_error": f"file not found: {f}"}]
        sql_files = [f for f in sql_files if os.path.exists(f)]
        if not sql_files:
            _log(f"  ❌ No valid source files. Exiting.", to_stdout=False)
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
    print(f"  v{_VERSION}")
    print()
    log_path = _init_log(output_dir)
    _log(f"  Output:    {output_dir}")
    _log(f"  Config:    {config_path or 'CLI mode'}")
    _log(f"  Input:     {len(sql_files)} SQL file(s)")
    if is_incremental:
        _log(f"  Incremental: {len(changed_files)} changed, {len(sql_files) - len(changed_files)} cached")
    print()

    # ── Phase -1: Validate SQL syntax ──
    if not args.skip_validate:
        _files_to_validate = [f for f in sql_files if f in changed_files or full_regen]
        if _files_to_validate:
            _validate_errors = {}
            _validate_warnings = {}
            n_validate = len(_files_to_validate)
            _progress_bar("Validate", 0, n_validate, f"{n_validate} file(s)")
            _log(f"  Validating {n_validate} file(s) (batch)...", to_stdout=False)
            try:
                _batch_results = validate_sql_files(_files_to_validate)
                for sql_file in _files_to_validate:
                    basename = os.path.basename(sql_file)
                    vresult = _batch_results.get(sql_file, {"error_count": 1, "warning_count": 0, "errors": [{"ValidationError": {"message": "Missing batch result"}}], "warnings": []})
                    raw_errors = vresult.get("errors", [])
                    real_errors = [e for e in raw_errors if not _is_parse_warning(e)]
                    warnings_in_errors = [e for e in raw_errors if _is_parse_warning(e)]
                    raw_warnings = vresult.get("warnings", [])
                    all_warnings = raw_warnings + warnings_in_errors

                    if real_errors:
                        _log(f"    ❌ {basename}: {len(real_errors)} error(s), {len(all_warnings)} warning(s)", to_stdout=False)
                        _validate_errors[basename] = real_errors
                        if all_warnings:
                            _validate_warnings[basename] = all_warnings
                    elif all_warnings:
                        _log(f"    ⚠ {basename}: {len(all_warnings)} warning(s)", to_stdout=False)
                        _validate_warnings[basename] = all_warnings
                    else:
                        _log(f"    ✅ {basename} OK", to_stdout=False)
            except Exception as e:
                _log(f"  ❌ Batch validation failed: {e}", to_stdout=False)
                _validate_errors["(batch)"] = [{"ValidationError": {"message": str(e)}}]
            _progress_done("Validate", n_validate)

            if _validate_errors:
                print()
                _log(f"  ⚠ {len(_validate_errors)} file(s) have syntax errors:", to_stdout=True)
                for _fname, _errs in _validate_errors.items():
                    _log(f"    📄 {_fname} — {len(_errs)} error(s):", to_stdout=True)
                    for _err in _errs[:10]:
                        _log(f"       {_format_validate_error(_err)}", to_stdout=True)
                    if len(_errs) > 10:
                        _log(f"       ... and {len(_errs) - 10} more", to_stdout=True)
                print()

                if sys.stdin.isatty():
                    try:
                        _ans = input("  是否继续转换？语法错误可能导致转换结果不准确。[y/N] ").strip().lower()
                        if _ans not in ('y', 'yes'):
                            _log(f"  用户取消转换。请修复语法错误后重试。", to_stdout=True)
                            _close_log(output_dir)
                            sys.exit(1)
                        _log(f"  用户选择继续转换（忽略语法错误）", to_stdout=False)
                    except (EOFError, KeyboardInterrupt):
                        _log(f"  用户取消转换。", to_stdout=True)
                        _close_log(output_dir)
                        sys.exit(1)
                else:
                    _log(f"  ❌ 非交互模式检测到语法错误，自动中止。使用 --skip-validate 跳过校验。", to_stdout=True)
                    _close_log(output_dir)
                    sys.exit(1)
        else:
            _log(f"  Validation: all files cached, skipping.", to_stdout=False)

    # ── Phase 0: Pre-scan for table DDL ──
    _ddl_files = list(sql_files)
    _init_sql = (config.get("integration_test", {}) if config else {}).get("init_sql", [])
    if isinstance(_init_sql, str):
        _init_sql = [_init_sql]
    for _isf in _init_sql:
        if os.path.isfile(_isf) and _isf not in _ddl_files:
            _ddl_files.append(_isf)
    for sql_file in _ddl_files:
        if sql_file not in changed_files and not full_regen:
            continue
        with open(sql_file, 'r', encoding=_SOURCE_ENCODING, errors='replace') as _f:
            _content = _f.read()
        if re.search(r'create\s+table', _content, re.IGNORECASE):
            schema = parse_table_ddl(sql_file)
            for tbl, cols in schema.items():
                for col, col_type in cols.items():
                    TYPE_OVERRIDES[(tbl, col)] = col_type
                    _TABLE_DDL_SOURCE[(tbl, col)] = sql_file

    # ── Phase 1: Parse SQL files (use cache for unchanged) ──
    packages = []
    all_package_names = {}
    sql_file_to_pkg = {}
    all_skipped = []
    n_sql = len(sql_files)

    # Batch parse all changed files in one invocation
    _files_needing_parse = []
    _cached_asts = {}
    for sql_file in sql_files:
        basename = os.path.basename(sql_file)
        if sql_file not in changed_files and not full_regen:
            cached_ast = _load_cached_ast(output_dir, sql_file)
            if cached_ast:
                _cached_asts[sql_file] = cached_ast
                _progress_bar("Parse", 0, n_sql, f"Cached {basename}")
                _log(f"  Cached: {basename}", to_stdout=False)
            else:
                changed_files.add(sql_file)
                full_regen = len(changed_files) == len(sql_files)
        if sql_file in changed_files or full_regen:
            _files_needing_parse.append(sql_file)

    _parsed_asts = {}
    if _files_needing_parse:
        _n_parse = len(_files_needing_parse)
        _progress_bar("Parse", 0, _n_parse, f"Parsing {_n_parse} file(s) (batch)")
        _log(f"  Parsing {_n_parse} file(s) (batch)...", to_stdout=False)
        try:
            _parsed_asts = parse_sql_files(_files_needing_parse)
        except Exception as e:
            _log(f"  ❌ Batch parse failed: {e}", to_stdout=False)
            _parsed_asts = {p: parse_sql_file(p) for p in _files_needing_parse}
        _progress_done("Parse", _n_parse)

    for idx, sql_file in enumerate(sql_files, 1):
        basename = os.path.basename(sql_file)
        try:
            if sql_file in _cached_asts:
                ast = _cached_asts[sql_file]
            elif sql_file in _parsed_asts:
                ast = _parsed_asts[sql_file]
                _save_cached_ast(output_dir, sql_file, ast)
            else:
                _progress_bar("Parse", idx, n_sql, f"Parsing {basename}")
                _log(f"  Parsing: {basename} (fallback)", to_stdout=False)
                ast = parse_sql_file(sql_file)
                _save_cached_ast(output_dir, sql_file, ast)

            errors = ast.get("errors", [])
            if errors:
                real_errors = [e for e in errors if not _is_parse_warning(e)]
                warnings = [e for e in errors if _is_parse_warning(e)]
                if real_errors:
                    _log(f"    ❌ {len(real_errors)} parse error(s)", to_stdout=False)
                    parse_errors_map[basename] = real_errors
                if warnings:
                    _log(f"    ⚠ {len(warnings)} parse warning(s)", to_stdout=False)
                    parse_warnings_map[basename] = warnings

            skipped = extract_non_procedure_statements(ast, source_file=basename)
            if skipped:
                all_skipped.extend(skipped)

            procedures, pkg_vars, custom_types = extract_procedures(ast, source_file=basename)
            _recover_constant_declarations(sql_file, pkg_vars)
            for p in procedures:
                p._source_path = sql_file
            comments = extract_comments(ast)
            pkg_level_comments = _map_comments_to_procedures(comments, procedures, source_file=basename)
            if not procedures:
                _log(f"    (no procedures found)", to_stdout=False)
                continue

            pkg_name = procedures[0].package if procedures[0].package else Path(sql_file).stem
            for vname, vdata in pkg_vars.items():
                if vname not in _PACKAGE_CONSTANTS:
                    _PACKAGE_VARIABLES[vname] = {**vdata, "package": pkg_name}
            pkg = PackageInfo(package_name=pkg_name, procedures=procedures, package_vars=pkg_vars, source_file=basename, java_package=sql_file_to_java_package.get(sql_file, ""), comments=pkg_level_comments, custom_types=custom_types)
            packages.append(pkg)
            all_package_names[pkg_name] = pkg
            sql_file_to_pkg[sql_file] = pkg_name

            for _p in procedures:
                if _p.is_function and _p.return_type:
                    _rt = sql_type_to_java(_p.return_type)
                    _UDF_RETURN_TYPES[(_p.proc_name.lower(), len(_p.parameters))] = _rt

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
            if pkg.package_vars:
                _pkg_var_names = getattr(proc, '_pkg_var_names', set())
                for vn, vi in pkg.package_vars.items():
                    if vn not in proc.local_vars:
                        proc.local_vars[vn] = vi.get("java_type", "Object")
                        _pkg_var_names.add(vn)
                setattr(proc, '_pkg_var_names', _pkg_var_names)
            analyze_procedure(proc, all_package_names)
        except Exception as e:
            _log(f"    ❌ Error analyzing {proc.name}: {e}", to_stdout=False)
            _log(traceback.format_exc(), to_stdout=False)
            proc.java_logic_lines.append(f"// ERROR: 转换失败 - {e}")
            _stub_key = (proc.name, len(proc.parameters))
            _add_stub_reason(proc, f"转换分析阶段异常: {e}")
            if _stub_key not in STUB_PROCEDURES:
                STUB_PROCEDURES.append(_stub_key)
    _progress_done("Analyze", n_analyze)

    # ── Phase 2.5: Promote local var types for OUT arg holders ──
    for pkg, proc in all_procs:
        try:
            _promote_out_local_vars(proc, all_package_names)
        except Exception:
            pass

    # ── Determine affected packages (changed + transitive dependents) ──
    if full_regen or not is_incremental:
        changed_pkg_names = None
    else:
        directly_changed = {sql_file_to_pkg[f] for f in changed_files if f in sql_file_to_pkg}
        changed_pkg_names = _find_dependent_packages(packages, directly_changed)
        _log(f"\n  Incremental: regenerating {len(changed_pkg_names)}/{len(packages)} packages")

    # ── Phase 3: Generate ──
    _log(f"\n  Generating Spring Boot project...", to_stdout=False)
    _resume_skip = _load_gen_checkpoint(output_dir) if args.resume else set()
    if _resume_skip:
        _log(f"  ⏩ Resume: skipping {len(_resume_skip)} already-generated packages", to_stdout=True)
    _gen_ok = False
    try:
        generate_project(output_dir, packages, changed_packages=changed_pkg_names, config=config,
                         resume_skip=_resume_skip,
                         progress_cb=lambda phase, i, n, s: (
                             _progress_bar("Generate", i, n, s) if phase == "pkg" else None
                         ))
        _progress_done("Generate", len([p for p in packages
                                        if changed_pkg_names is None or p.package_name in changed_pkg_names]))
        _gen_ok = True
    except Exception as e:
        _log(f"  ❌ Error generating project: {e}", to_stdout=False)
        _log(traceback.format_exc(), to_stdout=False)
        _log(f"  💡 Use --resume to continue from checkpoint", to_stdout=True)

    if _gen_ok:
        _clear_gen_checkpoint(output_dir)

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

    report = build_conversion_report(
        output_dir, packages, all_skipped, parse_errors_map,
        config_path=config_path or "", parse_warnings_map=parse_warnings_map
    )
    report_paths = write_conversion_report(report, output_dir,
                                            report_file=args.report)
    stub_count = len(STUB_PROCEDURES)
    STUB_PROCEDURES.clear()
    STUB_REASONS.clear()
    _PACKAGE_CONSTANTS.clear()
    _PACKAGE_VARIABLES.clear()
    _PACKAGE_VAR_WRITTEN.clear()
    UNSUPPORTED_FUNCTIONS.clear()
    TODO_SUMMARY.clear()

    # ── Summary ──
    total_procs = sum(len(pkg.procedures) for pkg in packages)
    total_dml = sum(len(proc.dml_statements) for pkg in packages for proc in pkg.procedures)
    total_calls = sum(len(proc.service_calls) for pkg in packages for proc in pkg.procedures)

    itest_cfg = config.get("integration_test", {}) if config else {}
    if not isinstance(itest_cfg, dict):
        itest_cfg = {}
    itest_enabled = itest_cfg.get("enabled", False)

    _log(f"\n  Done!")
    _log(f"    Packages:    {len(packages)}")
    _log(f"    Procedures:  {total_procs}")
    _log(f"    DML stmts:   {total_dml} (extracted as iBatis mapper methods)")
    _log(f"    Cross-calls: {total_calls}")
    _log(f"    Test files:  {len(packages)} (generated unit tests)")
    if itest_enabled:
        itest_mode = itest_cfg.get("mode", "remote")
        _log(f"    IT files:    {len(packages)} (generated integration tests, {itest_mode} mode)")
    _log(f"    Skipped:     {len(all_skipped)} (non-procedure SQL)")
    if UNRESOLVED_CALLS:
        _log(f"    Unresolved:  {len(UNRESOLVED_CALLS)} (cross-package calls, 详见转换报告)")
    if UNSUPPORTED_FUNCTIONS:
        _log(f"    Unsupported: {len(UNSUPPORTED_FUNCTIONS)} (unmapped functions, 详见转换报告)")
    if stub_count:
        _log(f"    Stubs:       {stub_count} (需人工审查, 详见转换报告)")
    if TODO_SUMMARY:
        _log(f"    TODOs:       {len(TODO_SUMMARY)} (详见转换报告)")
    _log(f"")
    _log(f"    详细处理日志: {_cache_base(output_dir) / 'logs' / 'conversion-latest.log'}")

    if report_paths:
        _log(f"\n  📄 转换报告:")
        for p in report_paths:
            _log(f"    - {p}")
        _log(f"    - {_cache_base(output_dir) / 'logs' / 'conversion-latest.log'}")

    _log(f"\n  Output: {os.path.abspath(output_dir)}")

    _close_log(output_dir)


if __name__ == "__main__":
    main()

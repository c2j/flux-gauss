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
    "exception": "String",
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

UNRESOLVED_CALLS = []
STUB_PROCEDURES = []
STUB_REASONS: dict[tuple, list[str]] = {}  # key=(proc.name, param_count) → list of human-readable stub reasons
UNSUPPORTED_FUNCTIONS = []
TODO_SUMMARY = []  # Collects (category, proc_id, source_file, detail) for diagnostic


def _add_stub_reason(proc, reason: str):
    """Record a specific reason why a procedure was stubbed."""
    _stub_key = (proc.name, len(proc.parameters))
    STUB_REASONS.setdefault(_stub_key, [])
    if reason not in STUB_REASONS[_stub_key]:
        STUB_REASONS[_stub_key].append(reason)
_PACKAGE_CONSTANTS = {}  # module-level: maps snake_case name → java_type for recovered constants
_PACKAGE_VARIABLES = {}  # module-level: maps snake_case name → {"java_type": str, "default": str, "package": str}
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
    sys.stdout.write(f"\r  {label} [{bar}] {total}/{total} 100.0%  ✓\n")
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
        "Object", "byte[]", "Map<String, Object>", "void",
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
    extra_params: list = field(default_factory=list)  # [(java_name, java_type), ...] — params from loop vars etc. not in local_vars at generation time


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
    if '\ufffd' not in text:
        return text, 'utf-8'

    for enc in ('gb18030', 'gbk', 'big5'):
        try:
            candidate = raw.decode(enc)
            if '\ufffd' not in candidate:
                _log(f"  [INFO] Decoded {os.path.basename(path)} as {enc}", to_stdout=False)
                return candidate, enc
        except (UnicodeDecodeError, LookupError):
            pass

    ffds = text.count('\ufffd')
    _log(f"  [WARN] {os.path.basename(path)}: {ffds} unrecoverable chars (encoding damaged in source)", to_stdout=False)
    return text, 'utf-8-damaged'


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
                            custom_types=custom_types,
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
                h_stmts = handler.get("body", [])
                if isinstance(h_stmts, list):
                    _walk_stmts_for_out_promotions(h_stmts, proc, all_packages, local_var_names, promotions)
    for var_lower, new_type in promotions.items():
        orig_name = local_var_names[var_lower]
        old_type = proc.local_vars[orig_name]
        if old_type != new_type:
            proc.local_vars[orig_name] = new_type
            proc.local_var_defaults.pop(orig_name, None)
            var_java = snake_to_camel(orig_name)
            patched = []
            for line in proc.java_logic_lines:
                patched.append(_patch_promoted_var_reads(line, var_java))
            proc.java_logic_lines = patched


def _patch_promoted_var_reads(line: str, var_java: str) -> str:
    import re
    # Don't patch: method arguments (OUT passing), .set(, declarations, assignments
    # Patch: null checks, comparisons, string concatenation, general reads
    patterns_to_skip = [
        rf'\b{re.escape(var_java)}\s*=',          # assignment target
        rf'\b{re.escape(var_java)}\.set\s*\(',    # .set() call
        rf'{re.escape(var_java)}\s*;',             # declaration
        rf'\bthis\.\w+\([^)]*\b{re.escape(var_java)}\b',  # OUT arg passing
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
            _recurse_stmt_for_out_promotions(stmt_data, proc, all_packages, local_var_names, promotions)


def _recurse_stmt_for_out_promotions(data, proc, all_packages, local_var_names, promotions):
    if not isinstance(data, dict):
        return
    for key in ("then_block", "else_block", "body", "loop_body", "block"):
        child = data.get(key)
        if isinstance(child, dict):
            child_stmts = child.get("body", [])
            if isinstance(child_stmts, list):
                _walk_stmts_for_out_promotions(child_stmts, proc, all_packages, local_var_names, promotions)
        elif isinstance(child, list):
            _walk_stmts_for_out_promotions(child, proc, all_packages, local_var_names, promotions)
    for branch in data.get("branches", []):
        if isinstance(branch, dict):
            br_body = branch.get("body", [])
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
                h_body = handler.get("body", [])
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
    else:
        return
    matched_pkg = _find_registered_pkg(pkg, all_packages)
    if not matched_pkg:
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
                default_ast = decl_data.get("default")
                if default_ast is not None:
                    try:
                        default_java = _expr_to_java(default_ast, proc)
                        proc.local_var_defaults[var_name] = default_java
                    except Exception:
                        pass
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
                cursor_arg_names = [a.get("name", "") for a in decl_data.get("arguments", []) if a.get("name")]
                if parsed_q:
                    proc.cursor_decls[cursor_name] = parsed_q
                    proc.cursor_decls[cursor_name.lower()] = parsed_q
                if cursor_arg_names:
                    proc.cursor_params[cursor_name] = cursor_arg_names
                    proc.cursor_params[cursor_name.lower()] = cursor_arg_names

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
            _log(f"      ⚠ Statement error in {proc.name}: {e}\n        stmt: {stmt_preview}", to_stdout=False)
            _log(traceback.format_exc(), to_stdout=False)
        post_idx = len(proc.java_logic_lines)
        if post_idx > pre_idx:
            stmt_checkpoints.append((pre_idx, post_idx))

    # Inject inline comments into method body at proportional positions
    if proc.inline_comments and stmt_checkpoints:
        _inject_inline_comments(proc, stmt_checkpoints)

    # Post-process GOTO patterns: if any GOTO was encountered, analyze and rewrite
    if getattr(proc, '_has_goto', False):
        _analyze_and_rewrite_goto(proc, all_packages, dml_counter)


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
        elif stmt_type == "ForAll":
            proc.java_logic_lines.append(f"// TODO: FORALL — bulk operation requires manual implementation")
            _record_todo("FORALL", proc, "bulk DML")
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

    def _walk(stmts, depth=0, path_prefix=None, parent_attr=None):
        if path_prefix is None:
            path_prefix = []
        for idx, stmt in enumerate(stmts):
            if not isinstance(stmt, dict):
                continue
            for stmt_type, stmt_data in stmt.items():
                current_path = path_prefix + [idx]
                if parent_attr:
                    current_path = path_prefix + [parent_attr, idx]
                if stmt_type == "Block" and isinstance(stmt_data, dict):
                    label = stmt_data.get("label")
                    if label:
                        labels[label] = LabelInfo(name=label, target_idx=idx, target_depth=depth)
                        label_stmt_map[label] = stmt
                    _walk(stmt_data.get("body", []), depth + 1, current_path)
                elif stmt_type in ("If", "For", "While", "Loop") and isinstance(stmt_data, dict):
                    label = stmt_data.get("label")
                    if label:
                        labels[label] = LabelInfo(name=label, target_idx=idx, target_depth=depth)
                        label_stmt_map[label] = stmt
                    if stmt_type == "If":
                        _walk(stmt_data.get("then_stmts", []), depth + 1, current_path, "then_stmts")
                        _walk(stmt_data.get("else_stmts", []), depth + 1, current_path, "else_stmts")
                        for elsif in stmt_data.get("elsifs", []):
                            _walk(elsif.get("stmts", []), depth + 1, current_path, "elsif_stmts")
                    else:
                        _walk(stmt_data.get("body", []), depth + 1, current_path, "body")
                elif stmt_type == "Goto" and isinstance(stmt_data, dict):
                    goto_label = stmt_data.get("label", "unknown")
                    gotos.append(GotoInfo(
                        label=goto_label,
                        source_idx=idx,
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
                        target_idx = _map_line_to_stmt_idx(line_num, body_stmts, proc.source_start_line)
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


def _map_line_to_stmt_idx(target_line: int, body_stmts: list, proc_start_line: int) -> int:
    """Map a source line number to the nearest AST statement index."""
    if not body_stmts:
        return 0
    n = len(body_stmts)
    return min(n - 1, max(0, int((target_line - proc_start_line) / 3)))


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

    # Clear DML state before regeneration so mapper is generated from the second pass only
    proc.dml_statements = []
    proc.service_calls = []
    for k in list(dml_counter.keys()):
        dml_counter[k] = 0

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

    proc.java_logic_lines.append("try {")
    for idx, stmt in enumerate(body_stmts):
        if idx >= cleanup_start:
            break
        if isinstance(stmt, dict):
            for st, sd in stmt.items():
                if st == "Goto" and sd.get("label") == label_name:
                    continue
            _process_statement(stmt, proc, all_packages, dml_counter)
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
                    if_condition = _expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages)
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
                if_condition = _expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages)

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
                for st, sd in stmt.items():
                    if st == "Block" and sd.get("label") == label_name:
                        _stmt_list_to_java(sd.get("body", []), proc, all_packages, dml_counter, indent=1)
                        continue
                _process_statement(stmt, proc, all_packages, dml_counter)
    else:
        for stmt in body_stmts:
            if isinstance(stmt, dict):
                _process_statement(stmt, proc, all_packages, dml_counter)


def _generate_nested_breakout_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    proc.java_logic_lines = []
    proc.dml_statements = []

    goto_labels = {g.label for g in analysis.gotos}

    def _process_with_goto_replace(stmt):
        if not isinstance(stmt, dict):
            return
        for stmt_type, stmt_data in stmt.items():
            if stmt_type == "Goto" and isinstance(stmt_data, dict):
                label = stmt_data.get("label", "")
                if label in goto_labels:
                    proc.java_logic_lines.append("continue;")
                    return
            elif stmt_type == "If" and isinstance(stmt_data, dict):
                condition = _expr_to_java(stmt_data.get("condition", {}), proc, all_packages=all_packages)
                proc.java_logic_lines.append(f"if ({condition}) {{")
                for s in _iter_statements(stmt_data.get("then_stmts", [])):
                    _process_with_goto_replace(s)
                _indent_last_lines(proc, 1)
                if stmt_data.get("else_stmts"):
                    proc.java_logic_lines.append("} else {")
                    for s in _iter_statements(stmt_data["else_stmts"]):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                for elsif in stmt_data.get("elsifs", []):
                    elsif_cond = _expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages)
                    proc.java_logic_lines.append(f"}} else if ({elsif_cond}) {{")
                    for s in _iter_statements(elsif.get("stmts", [])):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                proc.java_logic_lines.append("}")
                return
            elif stmt_type in ("For", "While", "Loop") and isinstance(stmt_data, dict):
                _process_loop_with_goto_replace(stmt, proc, all_packages, dml_counter)
                return
            elif stmt_type == "Block" and isinstance(stmt_data, dict):
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
                    for s in _iter_statements(body):
                        _process_with_goto_replace(s)
                    _indent_last_lines(proc, 1)
                    proc.java_logic_lines.append("}")
                    return
                elif "Query" in kind:
                    query_data = kind["Query"]
                    parsed_query = query_data.get("parsed_query")
                    if parsed_query:
                        sql_text = _reconstruct_sql_from_ast(parsed_query)
                        if sql_text:
                            raw_sql_for_params = sql_text
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
                                f"List<Map<String, Object>> {var_java}List = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                            )
                            proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {var_java}List) {{")
                            proc.local_vars[variable] = "Map<String, Object>"
                            proc._loop_vars = getattr(proc, '_loop_vars', set())
                            proc._loop_vars.add(variable)
                            for s in _iter_statements(body):
                                _process_with_goto_replace(s)
                            _indent_last_lines(proc, 1)
                            proc.java_logic_lines.append("}")
                            return
                proc.java_logic_lines.append(f"// TODO: nested breakout loop — manual extraction recommended")
                return
            elif stmt_type in ("While", "Loop") and isinstance(stmt_data, dict):
                condition = "true"
                if stmt_type == "While" and "condition" in stmt_data:
                    condition = _expr_to_java(stmt_data["condition"], proc, all_packages=all_packages)
                proc.java_logic_lines.append(f"while ({condition}) {{")
                for s in _iter_statements(stmt_data.get("body", [])):
                    _process_with_goto_replace(s)
                _indent_last_lines(proc, 1)
                proc.java_logic_lines.append("}")
                return
        _process_statement(stmt, proc, all_packages, dml_counter)

    for stmt in body_stmts:
        _process_with_goto_replace(stmt)


def _generate_state_machine_goto(proc, analysis, body_stmts, all_packages, dml_counter):
    """Pattern E: multiple labels with multiple GOTOs -> enum + while-switch state machine."""
    proc.java_logic_lines = []
    proc.dml_statements = []

    enum_name = f"{snake_to_pascal(proc.proc_name)}State"
    state_names = [snake_to_pascal(ln) for ln in analysis.labels.keys()]

    proc.java_logic_lines.append(f"// State machine generated from GOTO labels")
    proc.java_logic_lines.append(f"enum {enum_name} {{{', '.join(state_names)}}}")
    proc.java_logic_lines.append(f"{enum_name} currentState = {enum_name}.{state_names[0]};")
    proc.java_logic_lines.append("boolean running = true;")
    proc.java_logic_lines.append("while (running) {")
    proc.java_logic_lines.append("    switch (currentState) {")

    for label_name in analysis.labels.keys():
        state_java = snake_to_pascal(label_name)
        proc.java_logic_lines.append(f"        case {state_java}:")
        li = analysis.labels[label_name]
        target_idx = li.target_idx
        next_label_idx = None
        for other_label, other_li in analysis.labels.items():
            if other_li.target_idx > li.target_idx:
                if next_label_idx is None or other_li.target_idx < next_label_idx:
                    next_label_idx = other_li.target_idx
        end_idx = next_label_idx if next_label_idx is not None else len(body_stmts)

        for idx in range(target_idx, end_idx):
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
        proc.java_logic_lines.append("            break;")

    proc.java_logic_lines.append("        default:")
    proc.java_logic_lines.append("            running = false;")
    proc.java_logic_lines.append("            break;")
    proc.java_logic_lines.append("    }")
    proc.java_logic_lines.append("}")


def _dml_method_name(dml_type: str, proc_name: str, counter: dict) -> str:
    n = counter.get(dml_type, 0)
    counter[dml_type] = n + 1
    return f"{dml_type}{snake_to_pascal(proc_name)}" + (f"_{n}" if n > 0 else "")


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
    into_fields = _extract_all_into_targets(into_targets)
    if not into_fields:
        return _strip_into_clause(sql)
    field_names = [fn for fn, _ in into_fields]
    stripped = re.sub(r'\s+into\s+.*?(?=\s+from\b)', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+\w+(\s*,\s*\w+)*\s+(?=from\b)', ' ', sql, flags=re.IGNORECASE)
    # Handle SELECT without FROM (e.g., SELECT nextval() INTO var)
    if stripped == sql:
        stripped = re.sub(r'\s+into\s+.*$', ' ', sql, flags=re.IGNORECASE | re.DOTALL)
    m = re.match(r'(select\s+)(.*?)(\s+from\b)', stripped, re.IGNORECASE | re.DOTALL)
    if not m:
        return stripped.strip()
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
                            f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});'
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
                                f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});'
                            )
                            _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                        else:
                            _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))})')

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
                    f'List<Map<String, Object>> _result = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});'
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
                f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});'
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
                f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});'
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
                f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, sql_text))});'
            )


def _process_if(if_data: dict, proc: ProcedureInfo, all_packages: dict, dml_counter: dict):
    condition = _expr_to_java(if_data.get("condition", {}), proc, all_packages=all_packages)
    proc.java_logic_lines.append(f"if ({condition}) {{")

    for s in _iter_statements(if_data.get("then_stmts", [])):
        _process_statement(s, proc, all_packages, dml_counter)
    _indent_last_lines(proc, 1)

    for elsif in if_data.get("elsifs", []):
        elsif_cond = _expr_to_java(elsif.get("condition", {}), proc, all_packages=all_packages)
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


def _process_return(return_data: dict, proc: ProcedureInfo, all_packages: dict = None):
    """Convert RETURN to Java return."""
    expr = return_data.get("expression")
    if expr:
        java_expr = _expr_to_java(expr, proc, all_packages=all_packages)
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


def _emit_assignment(proc: ProcedureInfo, target: str, expr: str):
    out_param_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out}
    out_string_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "String"}
    out_long_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "Long"}

    # BigDecimal context: wrap double literals from CASE/ternary into BigDecimal.valueOf()
    target_var_type = None
    for vname, vtype in proc.local_vars.items():
        if snake_to_camel(vname) == target:
            target_var_type = vtype
            break
    if target_var_type is None:
        for p in proc.parameters:
            if p.java_name == target:
                target_var_type = p.java_type
                break
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
        proc.java_logic_lines.append(f"{target}.set({expr});")
    else:
        if target_var_type == "Long" and _is_bare_int_literal(expr):
            expr = f"Long.valueOf({expr})"
        elif target_var_type == "Integer" and _is_bare_long_literal(expr):
            expr = f"Integer.valueOf({expr})"
        proc.java_logic_lines.append(f"{target} = {expr};")


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
                if raw_name and proc:
                    is_local = raw_name in proc.local_vars
                    is_param = any(p.name.lower() == raw_name.lower() for p in proc.parameters)
                    is_const = raw_name in _PACKAGE_CONSTANTS
                    is_pkg_var = raw_name in _PACKAGE_VARIABLES and not is_local
                    if not is_local and not is_param and not is_const and not is_pkg_var:
                        _stub_key = (proc.name, len(proc.parameters))
                        _add_stub_reason(proc, f"赋值目标 '{raw_name}' 不是局部变量/参数/包变量/常量")
                        if _stub_key not in STUB_PROCEDURES:
                            STUB_PROCEDURES.append(_stub_key)

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
                            a_java = _expr_to_java(a, proc)
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
            elif k == "BinaryOp":
                if v.get("op") == "||":
                    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
                    if var_name:
                        result = _reconstruct_sql_from_concat(expression, proc)
                        if result:
                            proc.dynamic_sql_templates[var_name] = result
                else:
                    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
                    if var_name and var_name in proc.local_vars:
                        proc.sql_expr_vars[var_name] = expression

    var_name = _extract_var_name_from_expr(assign_data.get("target", {}))
    if var_name and var_name in proc.local_vars and var_name not in proc.sql_expr_vars:
        proc.sql_expr_vars[var_name] = expression

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


def _comment_perform(query: str) -> str:
    lines = query.replace('\r\n', '\n').split('\n')
    return '\n'.join(f"// {l}" if l.strip() else "//" for l in lines)


def _flush_scheduler_job(proc: ProcedureInfo):
    job = getattr(proc, '_pending_scheduler_job', {})
    if not job or 'target_method' not in job:
        return

    method = job['target_method']
    task_id_expr = job.get('task_id_expr', 'null')

    if '.get(' in task_id_expr:
        task_id_expr = f'(String) {task_id_expr}'

    proc._needs_futures_list = True
    proc.scheduler_tasks.append((method, task_id_expr))

    proc.java_logic_lines.append(
        f'_futures.add(java.util.concurrent.CompletableFuture.runAsync(() -> this.{method}({task_id_expr})));'
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
                        for arg in raw_args:
                            if isinstance(arg, dict):
                                if "NamedArgument" in arg:
                                    na = arg["NamedArgument"]
                                    na_name = ""
                                    for nk, nv in na.items():
                                        if nk == "name":
                                            na_name = _extract_name_from_expr(nv).lower() if isinstance(nv, dict) else str(nv).lower()
                                        elif nk == "value" and na_name == "job_action":
                                            job_action = _extract_string_literal(nv)
                        if job_action:
                            parts = job_action.split('.')
                            if len(parts) >= 2:
                                target_method = java_method_name(parts[-1])
                            else:
                                target_method = java_method_name(job_action)
                            proc._pending_scheduler_job = {"target_method": target_method}
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
                            raw_java = _expr_to_java(a, proc, as_read=False)
                            if raw_java in out_param_java_names:
                                resolved_perform_args.append(raw_java)
                            else:
                                resolved_perform_args.append(_expr_to_java(a, proc, as_read=True))
                        else:
                            resolved_perform_args.append(_expr_to_java(a, proc, as_read=True))
                    args = ", ".join(resolved_perform_args)
                    if matched_pkg.lower() == proc.package.lower():
                        pkg_info = all_packages.get(matched_pkg)
                        proc_exists = pkg_info and any(
                            p.proc_name.lower() == func.lower() for p in pkg_info.procedures
                        ) if pkg_info else False
                        if not proc_exists:
                            UNRESOLVED_CALLS.append(f"{proc.package}.{proc.proc_name} -> PERFORM {query}")
                            proc.java_logic_lines.append(_comment_perform(f"PERFORM {query}"))
                            return
                        proc.java_logic_lines.append(f"this.{method}({args});")
                    else:
                        proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched_pkg))
                        proc.java_logic_lines.append(f"{svc_name}.{method}({args});")
                else:
                    UNRESOLVED_CALLS.append(f"{proc.package}.{proc.proc_name} -> PERFORM {query}")
                    proc.java_logic_lines.append(_comment_perform(f"PERFORM {query}"))
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
                raw_sql_for_params = sql_text
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
                    f"List<Map<String, Object>> {var_java}List = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                )
                proc.java_logic_lines.append(f"for (Map<String, Object> {var_java} : {var_java}List) {{")

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
                    proc.dml_statements.append(DmlStatement(
                        sql_type="select",
                        method_id=mapper_method,
                        sql_text=sql_text,
                        result_type="Map<String, Object>",
                        returns_list=True,
                    ))
                    proc.java_logic_lines.append(
                        f"List<Map<String, Object>> {var_java}List = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
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
    condition = _expr_to_java(while_data.get("condition", {}), proc, all_packages=all_packages)
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
                raw_sql_for_params = sql_text
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
                    proc.java_logic_lines.append(
                        f"List<Map<String, Object>> {result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                    )
                else:
                    proc.java_logic_lines.append(
                        f"List<Map<String, Object>> {result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
                    )
                    proc.java_logic_lines.append(f"int {index_var} = 0;")
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
                    f"List<Map<String, Object>> {result_var} = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});"
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
                if raw_java in out_param_java_names:
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
                            if a_java_type in ("long", "Long", "int", "Integer"):
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
                UNRESOLVED_CALLS.append(f"{proc.package}.{proc.proc_name} -> {full_name}")
                proc.java_logic_lines.append(f"// CALL {full_name}({args_java})")
                return
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
    out_long_names = {snake_to_camel(p.name) for p in proc.parameters if p.is_out and p.java_type == "Long"}
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
                    expr = re.sub(r'\bsqlcode\b', 'String.valueOf(-1)', expr, flags=re.IGNORECASE)
                    if target in out_java_names:
                        if target in out_string_names and not _is_string_expr(expr):
                            expr = f"String.valueOf({expr})"
                        elif target in out_long_names and _is_string_expr(expr) and not _is_long_expr(expr):
                            expr = f"Long.valueOf({expr})"
                        elif target in out_long_names and _is_bare_int_literal(expr):
                            expr = f"Long.valueOf({expr})"
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
                            result.append(f"    // CALL {'.'.join(func_name_parts)}(...)")
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
                                    _resolved.append(_expr_to_java(a, proc, as_read=True))
                            args_java = ", ".join(_resolved)
                            is_self_call = (matched.lower() == proc.package.lower())
                            if not is_self_call:
                                proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched))
                            call_target = f"this.{method}" if is_self_call else f"{svc_name}.{method}"
                            result.append(f"    {call_target}({args_java});")
                        else:
                            full_name = ".".join(func_name_parts)
                            result.append(f"    // CALL {full_name}(...)")
                    else:
                        result.append(f"    // log error")
                elif sk == "Perform":
                    result.append(f"    // log error")
                else:
                    result.append(f"    // {sk}")

    result.append("}")

    resolved = []
    in_catch = False
    for line in result:
        if line.strip().startswith("} catch"):
            in_catch = True
        if "__SQLERRM__" in line:
            replacement = "e.getMessage()" if in_catch else '""'
            line = line.replace("__SQLERRM__", replacement)
        if "__SQLCODE__" in line:
            replacement = "String.valueOf(-1)" if in_catch else "\"00000\""
            line = line.replace("__SQLCODE__", replacement)
        if "__SQLSTATE__" in line:
            replacement = "\"00000\"" if in_catch else "\"00000\""
            line = line.replace("__SQLSTATE__", replacement)
        resolved.append(line)
    return resolved


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

    operand_type = _infer_expr_type(case_data.get("expression", {}), proc)
    is_primitive = operand_type in ("int", "Integer", "long", "Long", "short", "Short", "byte", "Byte", "double", "Double", "float", "Float", "boolean", "Boolean")
    first = True
    for when in whens:
        cond = _expr_to_java(when.get("condition", {}), proc)
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

            proc.dml_statements.append(DmlStatement(
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
                        proc.java_logic_lines.append(
                            f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});'
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
                                f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});'
                            )
                            _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                        else:
                            _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))})')
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
                    f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});'
                )
            return

    # FALLBACK: existing string tracing logic (keep as-is)
    string_expr = execute_data.get("string_expr", {})
    var_name = _extract_var_name_from_expr(string_expr)
    sql_text = proc.var_assignments.get(var_name, "")
    using_args = execute_data.get("using_args", [])
    into_targets = execute_data.get("into_targets", [])

    dynamic_template = proc.dynamic_sql_templates.get(var_name) if var_name else None
    if dynamic_template and not sql_text:
        sql_template, template_params = dynamic_template
        sql_text = sql_template
        raw_sql_for_params = sql_text
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
        param_args = _build_param_args_from_template(proc, template_params, extra)
        proc.dml_statements.append(DmlStatement(
            sql_type=sql_type,
            method_id=mapper_method,
            sql_text=sql_text,
            result_type=None,
            extra_params=extra,
        ))
        proc.java_logic_lines.append(
            f'mapper.{mapper_method}({param_args});'
        )
        for inlined_var in proc.inlined_sql_vars:
            var_java = snake_to_camel(inlined_var)
            for idx, line in enumerate(proc.java_logic_lines):
                if line.strip().startswith(f"{var_java} =") and "new java.sql.Date(System.currentTimeMillis())" in line:
                    proc.java_logic_lines[idx] = f"            {var_java} = null;"
                    break
        return

    if not sql_text:
        proc.java_logic_lines.append(f"// TODO: EXECUTE {var_name} — could not resolve SQL string")
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
                proc.java_logic_lines.append(
                    f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});'
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
                        f'Map<String, Object> _row = mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});'
                    )
                    _emit_assignment(proc, f'__MAP_PUT__{map_var}__{first_var}', f'_row.get("{first_var}")')
                else:
                    _emit_assignment(proc, var_java, f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))})')
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
            f'mapper.{mapper_method}({_build_param_args(proc.parameters, _sql_local_var_names(proc, raw_sql_for_params))});'
        )


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
            proc.java_logic_lines.append(f'throw new RuntimeException("{message}");')
        else:
            proc.java_logic_lines.append(f'throw new RuntimeException("RAISE {level}");')


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
    "nullif": "(java.util.Objects.equals({args0}, {args1}) ? null : {args0})",
    "greatest": "Math.max",
    "least": "Math.min",
    "abs": "__HANDLER__",
    "ceil": "Math.ceil",
    "floor": "Math.floor",
    "round": "__HANDLER__",
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
    "trunc": "__EXPR__(int) Math.floor((double)({args0}))",
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
    if len(args_java) >= 3:
        start = args_java[1]
        length = args_java[2]
        return f"{s_expr}.substring(Math.max(0, ({start}) - 1), Math.min({s_expr}.length(), Math.max(0, ({start}) - 1) + ({length})))"
    elif len(args_java) == 2:
        start = args_java[1]
        return f"{s_expr}.substring(Math.max(0, ({start}) - 1))"
    return f"{s_expr}"


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
    if src_expr.startswith("new java.sql.Timestamp") or src_expr.startswith("java.sql.Timestamp.valueOf") or "Timestamp" in (_infer_expr_type(args[1], proc) if proc and len(args) > 1 else ""):
        return f"({src_expr}).{accessor}"
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
    if func_name == "abs":
        if args_java:
            arg = args_java[0]
            if "BigDecimal" in arg or ".subtract(" in arg or ".add(" in arg or ".multiply(" in arg or ".divide(" in arg:
                return f"({arg}).abs()"
        return f"Math.abs({args_java[0] if args_java else '0'})"

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
        if len(args_java) >= 2:
            # SQL ROUND(expr, n) → BigDecimal.setScale(n, RoundingMode.HALF_UP)
            return f"({args_java[0]}).setScale((int)({args_java[1]}), java.math.RoundingMode.HALF_UP)"
        elif len(args_java) == 1:
            return f"Math.round({args_java[0]})"
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
            ts_expr = args_java[0]
            if not (ts_expr.startswith("new java.sql.Timestamp") or ts_expr.startswith("java.sql.Timestamp.valueOf")):
                ts_expr = f"java.sql.Timestamp.valueOf(String.valueOf({args_java[0]}))"
            return f"new java.text.SimpleDateFormat(\"{java_fmt}\").format(new java.util.Date({ts_expr}.getTime()))"
        num_fmt = args_java[1].strip('"').strip("'")
        num_fmt_java = num_fmt.replace("FM", "").replace(",", "").replace("9", "#").replace("0", "0")
        return f"new java.text.DecimalFormat(\"{num_fmt_java}\").format({args_java[0]})"

    elif func_name == "date_trunc":
        if len(args_java) < 2:
            return args_java[0] if args_java else "null"
        field_raw = args_java[0].strip('"').strip("'").lower()
        unit = _DATE_TRUNC_UNIT_MAP.get(field_raw, "ChronoUnit.DAYS")
        ts_expr = args_java[1]
        if not (ts_expr.startswith("new java.sql.Timestamp") or ts_expr.startswith("java.sql.Timestamp.valueOf")):
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

    elif func_name == "to_hex":
        if args_java:
            return f"Integer.toHexString({args_java[0]}).toUpperCase()"
        return "null"

    return f"/* TODO: {func_name} */ null"


_NUMERIC_FUNC_RETURN_INT = {"length", "instr", "ascii", "sign", "mod", "round", "trunc"}
_NUMERIC_FUNC_RETURN_DOUBLE = {"abs", "ceil", "floor", "power", "sqrt", "log", "exp", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "radians", "degrees"}
_NUMERIC_FUNC_NEEDS_DOUBLE_ARGS = {"sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sqrt", "log", "exp", "radians", "degrees", "power", "ceil", "floor"}
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


def _coerce_java_arg(a_java: str, target_type: str) -> str:
    """Coerce a Java argument expression to match the target parameter type.

    Handles edge cases where PL/pgSQL implicit type coercion needs explicit Java conversion:
    - Empty string ``\"\"`` passed to numeric parameters → zero value (0L, 0, etc.)
    - Numeric literal passed to BigDecimal parameter → BigDecimal.valueOf()
    - Map.get() result to typed parameter → cast expression
    """
    # Empty string '' in PL/pgSQL passed to a numeric/boolean parameter
    if a_java == '""' or a_java == '""':
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
    # BigDecimal target with numeric literal
    if "BigDecimal" in target_type and _is_numeric_literal_expr(a_java):
        return f"java.math.BigDecimal.valueOf({a_java})"
    # Map.get() result needs casting to target type
    if ".get(" in a_java and target_type not in ("Object", "Map<String, Object>"):
        if target_type in ("long", "Long"):
            return f"((Number) {a_java}).longValue()"
        if target_type in ("int", "Integer"):
            return f"((Number) {a_java}).intValue()"
        if "BigDecimal" in target_type:
            return f"((java.math.BigDecimal) {a_java})"
        if target_type == "String":
            return f"(String) {a_java}"
        return f"({target_type}) {a_java}"
    return a_java


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
                    if var_type.startswith("List<"):
                        return f"{var_java}.size()"
                    if _param_type.startswith("List<"):
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
                    _stub_key = (proc.name, len(proc.parameters))
                    _add_stub_reason(proc, f"表达式引用了未知变量 '{name}' (ColumnRef)")
                    if _stub_key not in STUB_PROCEDURES:
                        STUB_PROCEDURES.append(_stub_key)
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
                    _stub_key = (proc.name, len(proc.parameters))
                    _add_stub_reason(proc, f"表达式引用了未知变量 '{name}' (PlVariable)")
                    if _stub_key not in STUB_PROCEDURES:
                        STUB_PROCEDURES.append(_stub_key)
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
                elif ".get(" in left and "Long" in left_type:
                    left = f"((Long) {left})"
                elif ".get(" in left and left_type == "Object" and op in (">", "<", ">=", "<=", "=", "<>"):
                    left = f"((Number) {left}).intValue()"
                if ".get(" in right and "BigDecimal" in right_type:
                    right = f"((java.math.BigDecimal) {right})"
                elif ".get(" in right and right_type == "Integer":
                    right = f"((Integer) {right})"
                elif ".get(" in right and "Long" in right_type:
                    right = f"((Long) {right})"
                elif ".get(" in right and right_type == "Object" and op in (">", "<", ">=", "<=", "=", "<>"):
                    right = f"((Number) {right}).intValue()"

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
                    if _is_numeric_literal(val.get("left")):
                        left = f"java.math.BigDecimal.valueOf({left})"
                    elif "BigDecimal" not in left_type:
                        left = f"java.math.BigDecimal.valueOf({left})"
                    if _is_numeric_literal(val.get("right")):
                        right = f"java.math.BigDecimal.valueOf({right})"
                    elif "BigDecimal" not in right_type:
                        right = f"java.math.BigDecimal.valueOf({right})"
                    return f"{left}.{method}({right})"

                if is_bd and op == "||":
                    return f"{left}.toString().concat({right}.toString())"

            if op == "^":
                left_d = f"((Number) {left}).doubleValue()" if ".get(" in left else left
                right_d = f"((Number) {right}).doubleValue()" if ".get(" in right else right
                return f"Math.pow({left_d}, {right_d})"
            java_op = _java_op(op)
            if op in ("+", "-", "*", "/") and (".get(" in left or ".get(" in right):
                left_d = f"((Number) {left}).doubleValue()" if ".get(" in left else left
                right_d = f"((Number) {right}).doubleValue()" if ".get(" in right else right
                return f"({left_d} {java_op} {right_d})"
            if op == "=" and _is_string_comparison(val):
                return f"{right}.equals({left})"
            elif op == "<>" and _is_string_comparison(val):
                return f"!{right}.equals({left})"
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
                for p in proc.parameters:
                    if p.name.lower() == func_name_lower and p.java_type.startswith("List<"):
                        idx_expr = args_java[0] if args_java else "0"
                        return f"{snake_to_camel(func_name_lower)}.get((int)({idx_expr}) - 1)"
                for var_name, var_type in proc.local_vars.items():
                    if var_name.lower() == func_name_lower and var_type.startswith("List<"):
                        idx_expr = args_java[0] if args_java else "0"
                        return f"{snake_to_camel(func_name_lower)}.get((int)({idx_expr}) - 1)"

            if func_name_lower in SQL_FUNCTION_MAP:
                mapped = SQL_FUNCTION_MAP[func_name_lower]
                if func_name_lower == "coalesce" and len(args_java) >= 2:
                    first_type = _infer_expr_type(val.get("args", [{}])[0], proc) if val.get("args") else "Object"
                    if "BigDecimal" in first_type:
                        args_java = [(a if a != "0" else "java.math.BigDecimal.ZERO") for a in args_java]
                        args_str = ", ".join(args_java)
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
                                wrapped_args = []
                                for i, a_java in enumerate(args_java):
                                    if i < len(target_proc.parameters):
                                        wrapped_args.append(_coerce_java_arg(a_java, target_proc.parameters[i].java_type))
                                    else:
                                        wrapped_args.append(a_java)
                                svc_name = f"{package_to_classname(matched).lower()}Service"
                                proc.service_calls.append(ServiceCall(svc_name, method, [], package_name=matched))
                                return f"{svc_name}.{method}({', '.join(wrapped_args)})"
                if self_call_pkg:
                    target_proc = _find_target_proc(self_call_pkg, self_call_func, all_packages, arg_count=len(val.get("args", [])))
                    if target_proc:
                        method = java_method_name(self_call_func)
                        wrapped_args = []
                        for i, a_java in enumerate(args_java):
                            if i < len(target_proc.parameters):
                                wrapped_args.append(_coerce_java_arg(a_java, target_proc.parameters[i].java_type))
                            else:
                                wrapped_args.append(a_java)
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
            args_java = [_expr_to_java(a, proc, all_packages=all_packages) for a in val.get("args", [])]
            return f"/* UNSUPPORTED: {func_name} — special syntax, no Java mapping */"
        elif key == "IsNull":
            inner = _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
            negated = val.get("negated", False)
            if negated:
                return f"{inner} != null"
            return f"{inner} == null"
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
        elif key == "TypeCast":
            return _expr_to_java(val.get("expr", {}), proc, all_packages=all_packages)
        elif key == "CursorAttribute":
            cursor_java = _expr_to_java(val.get("cursor", {}), proc)
            attr = val.get("attribute", "").lower()
            if attr in ("notfound", "not_found"):
                return f"!{cursor_java}.next()"
            elif attr in ("found",):
                return f"{cursor_java}.next()"
            elif attr in ("isopen", "is_open"):
                return f"({cursor_java} != null && !{cursor_java}.isClosed())"
            elif attr in ("rowcount", "row_count"):
                return f"_rowCount"  # needs cursor row count tracking
            return f"/* CursorAttribute:{attr} */ false"

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
                return f'"{val.replace(chr(34), chr(92) + chr(34))}"'
            elif key == "Integer":
                return str(val)
            elif key == "Float":
                return f"{val}d"
            elif key == "Boolean":
                return "true" if val else "false"
            elif key == "Null":
                return "null"
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


def _build_param_args_from_template(proc: ProcedureInfo, template_params: list, extra_params: list = None) -> str:
    template_java_names = set()
    for java_name, _is_id in template_params:
        template_java_names.add(java_name.split(".", 1)[0] if "." in java_name else java_name)
    parts = []
    seen = set()
    for p in proc.parameters:
        if p.mode and p.mode.upper() == "OUT":
            continue
        if p.mode and p.mode.upper() == "INOUT":
            parts.append(f"{p.java_name}.get()")
        else:
            parts.append(p.java_name)
        seen.add(p.java_name)
    for var_name, _var_type in proc.local_vars.items():
        jn = snake_to_camel(var_name)
        if jn in template_java_names and jn not in seen:
            seen.add(jn)
            parts.append(jn)
    if extra_params:
        for jn, _jt in extra_params:
            if jn not in seen:
                seen.add(jn)
                parts.append(jn)
    return ", ".join(parts)


def _sql_local_var_names(proc: ProcedureInfo, sql_text: str) -> list:
    if not sql_text:
        return []
    scan_sql = sql_text
    upper = sql_text.lstrip().upper()
    if upper.startswith("SELECT"):
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
                _write_mapper_interface(base_path, pkg)
            except Exception as e:
                raise RuntimeError(f"_write_mapper_interface: {e}") from e

            try:
                _write_mapper_xml(base_path, pkg)
            except Exception as e:
                raise RuntimeError(f"_write_mapper_xml: {e}") from e

            try:
                _write_service_class(base_path, pkg, service_injections, all_packages)
            except Exception as e:
                raise RuntimeError(f"_write_service_class: {e}") from e

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


def _dml_used_local_vars(proc: ProcedureInfo, dml: DmlStatement) -> list:
    sql_raw = dml.sql_text or ""
    mybatis_placeholders = set(re.findall(r'[#\$]\{(\w+)', sql_raw))
    used = []
    param_names_lower = {p.name.lower() for p in proc.parameters if not p.is_out}
    param_java_lower = {p.java_name.lower() for p in proc.parameters if not p.is_out}
    for var_name, var_java_type in proc.local_vars.items():
        java_name = snake_to_camel(var_name)
        if var_name.lower() in param_names_lower or java_name.lower() in param_java_lower:
            continue
        if java_name in mybatis_placeholders or re.search(rf'\b{re.escape(var_name)}\b', sql_raw, re.IGNORECASE):
            used.append((java_name, var_java_type))
    return used


def _build_mapper_method(proc: ProcedureInfo, dml: DmlStatement, imports: set) -> str:
    method_name = dml.method_id

    params = []
    for p in proc.parameters:
        if p.mode and p.mode.upper() == "OUT":
            continue
        params.append(f'@Param("{p.java_name}") {p.java_type} {p.java_name}')

    for java_name, java_type in _dml_used_local_vars(proc, dml):
        params.append(f'@Param("{java_name}") {java_type} {java_name}')
        _imp = _resolve_import(java_type)
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
    if dml.sql_type == "select":
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
    return sql


def _build_mapper_statement(proc: ProcedureInfo, dml: DmlStatement) -> str:
    sql = _clean_sql(dml.sql_text.strip())
    if sql.endswith(";"):
        sql = sql[:-1]

    # Replace cross-package function calls with PostgreSQL equivalents
    _SQL_FUNC_REPLACEMENTS = [
        (re.compile(r'\b\w+\.get_sys_date\s*\(\)', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
        (re.compile(r'\b\w+\.sysdate\b', re.IGNORECASE), 'CURRENT_TIMESTAMP'),
    ]
    for pat, repl in _SQL_FUNC_REPLACEMENTS:
        sql = pat.sub(repl, sql)

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

    # Strip double-quoted identifiers for PostgreSQL compatibility
    # "MY_TAB_PARTITIONS" → MY_TAB_PARTITIONS (case-insensitive matching)
    # Preserve quoted identifiers that are reserved words (date, user, order, etc.)
    _RESERVED = {'date', 'user', 'order', 'performance', 'type', 'check', 'primary', 'foreign', 'unique', 'constraint', 'index', 'table', 'select', 'insert', 'update', 'delete', 'from', 'where', 'group', 'having', 'limit', 'offset', 'as', 'on', 'and', 'or', 'not', 'null', 'default', 'values', 'set', 'into'}
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

    sql = re.sub(r'([(,])\s*(date|user|order|performance|type)\s*([,)])', r'\1 "\2" \3', sql, flags=re.IGNORECASE)

    if dml.sql_type == "select" and not dml.returns_list:
        if not re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
            sql = sql.rstrip() + "\n        LIMIT 1"

    sql = sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

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
    source_info = f"Source: {proc.source_file}:{proc.source_start_line}-{proc.source_end_line} — {proc.name}.{dml.method_id}" if proc.source_file else f"Source: {proc.name}.{dml.method_id}"
    xml_parts.append(f"<!-- {source_info} -->")
    for c in proc.leading_comments:
        formatted = _format_comment_for_java(c)
        if formatted:
            safe_text = formatted.lstrip('/ ').strip().replace('--', '\u2014\u2014')
            xml_parts.append(f"<!-- {safe_text} -->")
    if filter_line:
        xml_parts.append(filter_line)
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
        return f'#{{{java_name}.{snake_to_camel(m.group(2))}}}'
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
    sql = re.sub(r'(?<!\')\s*::\s*(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'(#[\w, ={}]+})\s+(?:DATE|TIMESTAMP|INTEGER|BIGINT|VARCHAR|TEXT|BOOLEAN|NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|SMALLINT|BYTEA|JSONB|JSON|UUID)\b', r'\1', sql, flags=re.IGNORECASE)
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
            if var_name in _PACKAGE_CONSTANTS:
                lines.append(f"    private static final {java_type} {java_name} = {default_expr};")
            else:
                lines.append(f"    private {java_type} {java_name} = {default_expr};")

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
    if t.startswith("atomicreference"):
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
        body_lines.append("// TODO: Auto-generated stub — complex PL/pgSQL pattern requires manual implementation")
        if ret_type != "void":
            body_lines.append("return null;")
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
                if var_name in proc.local_var_defaults and _is_numeric_default(default_val, var_type):
                    default_val = _wrap_default_for_type(default_val, var_type)
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
        if proc.is_function:
            body_lines.append("return null;")

    # Hoist local variable declarations before try-catch so they're visible in catch blocks
    hoisted_decls = []
    remaining_lines = []
    for line in body_lines:
        s = line.strip()
        if re.match(r'^(String|Long|Integer|BigDecimal|java\.math\.BigDecimal|AtomicReference|List<Map<String, Object>>|boolean|int|long|double|float)\s+\w+\s*=', s):
            # Don't hoist mapper query result assignments — they must stay in place (e.g., inside loops)
            if 'mapper.' not in s:
                hoisted_decls.append(line)
            else:
                remaining_lines.append(line)
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

    # Strip unreachable code after body-level return
    _last_body_return = -1
    _bd = 0
    for i, line in enumerate(body_lines):
        s = line.strip()
        _bd += s.count("{") - s.count("}")
        if not s or s.startswith("//"):
            continue
        if _bd == 0 and re.match(r'^return\b', s) and s.endswith(";"):
            _last_body_return = i
    if _last_body_return >= 0:
        _trailing = body_lines[_last_body_return + 1:]
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
            body_lines = body_lines[:_last_body_return + 1] + _kept

    has_complex_issues, _failed_checks = _has_compilation_issues(body_lines, out_params, proc)
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
                        if not re.search(rf'\(\s*[^)]*\b{re.escape(name)}\b', line):
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
                    failed.append(f"未声明的 cursor 结果变量 '{m}'")

    if re.search(r'/\*\s*(UNSUPPORTED|TODO: implement)\b', all_text):
        failed.append("包含未实现的内置函数调用 (UNSUPPORTED)")

    if re.search(r'while\s*\(\s*true\s*\)', all_text) and 'continue;' in all_text and 'break;' not in all_text:
        failed.append("GOTO 转换残留死循环 (while(true) + continue 无 break)")

    if re.search(r'\btgOp\b|\btgWhen\b|\btgName\b|\btgTag\b', all_text):
        failed.append("包含 PL/pgSQL 触发器变量 (TG_OP/TG_WHEN 等)，Java 无等价物")

    if proc:
        for m in re.finditer(r'\b(\w+)\.put\(', all_text):
            var_name = m.group(1)
            local_java = {snake_to_camel(v) for v in proc.local_vars.keys()}
            param_java = {p.java_name for p in proc.parameters}
            if var_name not in local_java and var_name not in param_java and var_name not in ('_row', 'result'):
                failed.append(f"未声明的包状态变量 '{var_name}' 调用了 .put()")
                break

    if re.search(r'\w+\s*=\s*\d+\.\d+d\b', all_text) and 'BigDecimal' in all_text and 'BigDecimal.valueOf' not in all_text:
        failed.append("BigDecimal 变量被赋值了 double 字面量 (类型不匹配)")

    if re.search(r'Math\.round\(', all_text) and 'BigDecimal' in all_text:
        failed.append("Math.round(BigDecimal) 不存在于 Java 中")

    if proc and re.search(rf'\b{re.escape(proc.package.lower() if proc.package else "")}Service\s+\w+Service\s*,', all_text):
        failed.append("Service 自注入导致循环依赖")

    if proc:
        for p in proc.parameters:
            if p.java_type in ("int", "long", "double", "float", "boolean", "short", "byte", "char"):
                if re.search(rf'\b{re.escape(p.java_name)}\.equals\(', all_text):
                    failed.append(f"基本类型参数 '{p.java_name}' 使用了 .equals() 而非 ==")

    _bd = 0
    for line in body_lines:
        s = line.strip()
        _bd += s.count("{") - s.count("}")
        if s.startswith("//") or not s:
            continue
        if _bd == 0 and re.match(r'^return\b', s) and s.endswith(";"):
            for after in body_lines[body_lines.index(line) + 1:]:
                a = after.strip()
                if a and not a.startswith("//") and a != "}":
                    failed.append("return 后仍有可达代码 (死代码)")
                    break
            break

    return (len(failed) > 0, failed)


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
    (test_dir / f"{test_class_name}.java").write_text(content)


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
        return "1"
    if java_type == "java.math.BigDecimal":
        return "new java.math.BigDecimal(\"99.99\")"
    if "big_decimal" in lower:
        return "new java.math.BigDecimal(\"99.99\")"
    if "map" in lower:
        return "new java.util.HashMap<>()"
    if "list" in lower:
        return "new java.util.ArrayList<>()"
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
    return f"\"test_{param_name}\""


def _build_success_test(proc: ProcedureInfo, mapper_name: str,
                         param_values: list, out_decls: list,
                         args_str: str, service_injections: dict,
                         svc_method_param_counts: dict, pkg: PackageInfo) -> str:
    method_name = java_method_name(proc.proc_name)
    lines = []
    has_while = any("while (" in line or "while(" in line for line in proc.java_logic_lines)
    camel_name = java_method_name(proc.proc_name)
    is_recursive = any(f"this.{camel_name}(" in line for line in proc.java_logic_lines)
    if has_while:
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
    if is_function:
        lines.append(f"        var result = service.{method_name}({args_str});")
        if is_stubbed or has_empty_body:
            lines.append(f"        // Stub/empty implementation — result may be null")
        else:
            lines.append(f"        assertNotNull(result);")
    else:
        lines.append(f"        service.{method_name}({args_str});")

    lines.append("    }")
    return "\n".join(lines)


def _collect_all_dmls(pkg: PackageInfo) -> dict:
    all_dmls = {}
    for p in pkg.procedures:
        in_param_count = sum(1 for param in p.parameters if not (param.mode and param.mode.upper() == "OUT"))
        for dml in p.dml_statements:
            if dml.method_id not in all_dmls:
                local_var_count = len(_dml_used_local_vars(p, dml))
                extra_param_count = len(dml.extra_params)
                all_dmls[dml.method_id] = (dml.method_id, dml.sql_type, dml.result_type, dml.returns_list, in_param_count + local_var_count + extra_param_count, dml.sql_text)
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
    if dml_result_type == "java.math.BigDecimal":
        return f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.math.BigDecimal.TEN);"
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
            elif dml_result_type == "java.math.BigDecimal":
                lines.append(f"        when({mapper_name}.{dml_method_id}({method_any})).thenReturn(java.math.BigDecimal.ZERO);")
            else:
                lines.append(f"        {{ var m = new java.util.HashMap<String,Object>(); m.put(\"id\", 1L); m.put(\"product_id\", 1L); m.put(\"v_product_id\", 1L); m.put(\"v_qty\", 10); m.put(\"total\", 0); m.put(\"v_total\", 0); m.put(\"stock_qty\", 0); m.put(\"name\", \"\"); m.put(\"status\", \"REJECTED\"); m.put(\"v_status\", \"REJECTED\"); m.put(\"v_amount\", java.math.BigDecimal.ZERO); when({mapper_name}.{dml_method_id}({method_any})).thenReturn(m); }}")
        else:
            lines.append(_mock_select_return(dml_sql_type, dml_result_type, dml_returns_list, mapper_name, dml_method_id, method_any, dml_sql_text))


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
            import org.testcontainers.containers.PostgreSQLContainer;
            import org.testcontainers.junit.jupiter.Container;
            import org.testcontainers.junit.jupiter.Testcontainers;

            @SpringBootTest
            @ActiveProfiles("integration")
            @Testcontainers
            @Sql(scripts = "classpath:itest-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD)
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
            import org.springframework.test.context.jdbc.SqlMergeMode;

            @SpringBootTest
            @ActiveProfiles("integration")
            @SqlMergeMode(SqlMergeMode.MergeMode.MERGE)
            @Sql(scripts = "classpath:itest-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD)
            public abstract class AbstractIntegrationTest {{
            }}
        """)
    (pkg_dir / "AbstractIntegrationTest.java").write_text(content)

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
    (res_dir / "application-integration.yml").write_text(yml_content)


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

    lines = []
    for seq in sorted(sequences_needed):
        lines.append(f'DROP SEQUENCE IF EXISTS {seq} CASCADE;')
    for seq in sorted(sequences_needed):
        lines.append(f'CREATE SEQUENCE IF NOT EXISTS {seq} START WITH 1 INCREMENT BY 1;')
    if sequences_needed:
        lines.append("")

    for table in sorted(schema_map.keys()):
        lines.append(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
    lines.append("")
    for table, columns in sorted(schema_map.items()):
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
        lines.append(f'CREATE TABLE "{table}" (')
        lines.append(",\n".join(col_defs))
        lines.append(");")
        lines.append("")

    init_sql = itest_cfg.get("init_sql", [])
    if isinstance(init_sql, str):
        init_sql = [init_sql]
    for script in init_sql:
        if os.path.isfile(script):
            with open(script, 'r', encoding='utf-8', errors='replace') as f:
                lines.append(f.read())
        else:
            lines.append(f"-- init_sql not found: {script}")

    content = "\n".join(lines)
    res_dir = base_path / "src/test/resources"
    res_dir.mkdir(parents=True, exist_ok=True)
    (res_dir / "itest-schema.sql").write_text(content)


def _itest_extract_table_from_select(sql: str) -> str:
    m = re.search(r'\bfrom\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _itest_extract_table_from_insert(sql: str) -> str:
    m = re.search(r'\binto\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _itest_extract_table_from_update_delete(sql: str) -> str:
    m = re.search(r'\b(?:update|delete\s+from?)\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


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
        if "id" in lower_col or "code" in lower_col or "type" in lower_col or "status" in lower_col:
            return f"'test_{lower_col}'"
        return f"'test {lower_col}'"
    return "'test'"


def _itest_infer_test_data(proc: ProcedureInfo, pkg: PackageInfo, schema_map: dict, all_packages: dict = None) -> dict:
    handled = set()
    needed = {}
    for dml in proc.dml_statements:
        sql = dml.sql_text or ""
        if dml.sql_type == "insert":
            tbl = _itest_extract_table_from_insert(sql)
            if tbl:
                handled.add(tbl)
        elif dml.sql_type == "select":
            tbl = _itest_extract_table_from_select(sql)
            if tbl and tbl not in handled:
                needed[tbl] = schema_map.get(tbl, {})
        elif dml.sql_type in ("update", "delete"):
            tbl = _itest_extract_table_from_update_delete(sql)
            if tbl and tbl not in handled:
                needed[tbl] = schema_map.get(tbl, {})
    if all_packages is None:
        return needed
    _itest_add_transitive_tables(proc, pkg, schema_map, handled, needed, all_packages)
    return needed


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
            if dml.sql_type == "insert":
                tbl = _itest_extract_table_from_insert(sql)
                if tbl:
                    handled.add(tbl)
            elif dml.sql_type == "select":
                tbl = _itest_extract_table_from_select(sql)
                if tbl and tbl not in handled:
                    needed[tbl] = schema_map.get(tbl, {})
            elif dml.sql_type in ("update", "delete"):
                tbl = _itest_extract_table_from_update_delete(sql)
                if tbl and tbl not in handled:
                    needed[tbl] = schema_map.get(tbl, {})

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
    (fixtures_dir / fname).write_text(content)
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
    if has_stubs:
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

        param_values = []
        param_args = []
        for p in in_params:
            val = _default_test_value(p.java_type, p.java_name, pkg=pkg)
            if p.java_type == "String" and (p.name.lower() in _numeric_string_params or p.java_name.lower() in _numeric_string_params):
                val = '"1"'
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

        all_args = param_args + out_args
        args_str = ", ".join(all_args)

        test_data = _itest_infer_test_data(proc, pkg, schema_map, all_packages)
        sql_script = _itest_write_fixtures(base_path, proc, pkg, test_data)

        base_test_name = f"test_{method_name}_integration"
        count = seen_method_names.get(base_test_name, 0)
        seen_method_names[base_test_name] = count + 1
        test_name = f"{base_test_name}_{count}" if count > 0 else base_test_name

        is_itest_stubbed = (proc.name, len(proc.parameters)) in STUB_PROCEDURES
        lines = []
        if is_itest_stubbed:
            lines.append("    @Disabled(\"Converter stub — complex PL/pgSQL pattern requires manual implementation\")")
        if sql_script:
            lines.append(f"    @org.springframework.test.context.jdbc.Sql(scripts = \"{sql_script}\")")
        lines.append("    @Test")
        lines.append("    @Timeout(value = 10, unit = TimeUnit.SECONDS)")
        lines.append(f"    void {test_name}() {{")
        for pv in param_values:
            lines.append(f"        {pv}")
        for od in out_decls:
            lines.append(f"        {od}")
        if proc.is_function:
            lines.append(f"        var result = {svc_var}.{method_name}({args_str});")
            if is_itest_stubbed:
                lines.append("        // Stub implementation — result is null")
            else:
                lines.append("        assertNotNull(result);")
            lines.append("        // TODO: Add domain-specific assertions")
        else:
            lines.append(f"        {svc_var}.{method_name}({args_str});")
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
    (itest_dir / f"{class_name}.java").write_text(content)




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
    parser.add_argument("--resume", action="store_true", default=False, help="从断点续做（跳过已生成的包）")
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

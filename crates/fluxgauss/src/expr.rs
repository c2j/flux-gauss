use crate::naming::snake_to_camel;
use crate::types::{GlobalFnEntry, ProcedureInfo};

fn flatten_comment(text: &str) -> String {
    text.replace("/*", "").replace("*/", "")
}

pub(crate) fn is_nullish_java_expr(expr: &str) -> bool {
    let t = expr.trim();
    if t == "null" {
        return true;
    }
    t.ends_with("null") && t.contains("/*") && !t.starts_with('"')
}

/// Strip one layer of wrapping parentheses when they enclose the whole
/// expression (`(null)` -> `null`), ignoring parens inside literals is not
/// attempted — callers pass generated code where this is rare.
fn strip_outer_parens(t: &str) -> &str {
    let t = t.trim();
    if !t.starts_with('(') || !t.ends_with(')') {
        return t;
    }
    let mut depth = 0i32;
    for (i, c) in t.char_indices() {
        match c {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 && i + 1 != t.len() {
                    return t;
                }
            }
            _ => {}
        }
    }
    if depth == 0 {
        &t[1..t.len() - 1]
    } else {
        t
    }
}

/// SQL NULL is not a Java boolean primitive — `x && NULL` / `x || NULL`
/// cannot compile. In boolean context SQL NULL is falsy, so the operand
/// degrades to `false` (delivery postprocess patched `&& (null)` -> `&&
/// false` for the same reason). The other operand is still evaluated, so
/// side effects are preserved.
fn boolean_operand_java(expr: &str) -> String {
    if is_nullish_java_expr(strip_outer_parens(expr)) {
        "false".to_string()
    } else {
        expr.to_string()
    }
}

fn as_double_expr(expr: &str) -> String {
    let t = expr.trim();
    if is_nullish_java_expr(t) {
        return "0.0".to_string();
    }
    if t.parse::<f64>().is_ok() {
        return t.to_string();
    }
    if t.ends_with(".doubleValue()") || t.starts_with("Math.") {
        return t.to_string();
    }
    if t.contains("BigDecimal")
        || t.contains(".divide(")
        || t.contains(".multiply(")
        || t.contains(".add(")
        || t.contains(".subtract(")
        || t.contains(".setScale(")
    {
        return format!("({}).doubleValue()", t);
    }
    if t.contains(".get(") {
        return format!("((Number) java.util.Objects.requireNonNullElse({}, 0)).doubleValue()", t);
    }
    format!("Double.parseDouble(String.valueOf({}))", t)
}

fn parse_string_math_arg(expr: &str, proc: &ProcedureInfo) -> String {
    if expr.contains(".get(")
        || resolve_var_java_type(expr, proc).as_deref() == Some("String")
        || resolve_var_java_type(expr, proc).as_deref() == Some("Object")
    {
        as_double_expr(expr)
    } else {
        expr.to_string()
    }
}

pub fn expr_to_java(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    expr_to_java_impl(expr, proc)
}

pub fn bool_expr_to_java(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let val = expr_to_java(expr, proc);
    if is_nullish_java_expr(&val) {
        "/* unhandled */ false".into()
    } else {
        // M5 (#114 review): a promoted local var used as a boolean condition is
        // an AtomicReference — deref before evaluating (bare ref is always non-null).
        maybe_deref_promoted(&val, proc)
    }
}

pub fn assignment_to_java(
    target: &ogsql_parser::ast::Expr,
    value: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
) -> String {
    let target_name = get_column_ref_name(target);
    if let Some(ref_name) = &target_name {
        // Check if it's a dotted reference like rec.field_name
        if ref_name.contains('.') {
            let parts: Vec<&str> = ref_name.splitn(2, '.').collect();
            let base = parts[0];
            let field = parts[1];
            let base_camel = snake_to_camel(base);
            let field_camel = snake_to_camel(field);

            // composite_type_shims: array-typed record fields are parallel
            // java.util.List slots — `f := f || e` appends, `'{}'` re-seats
            // an empty list, anything else is a typed put.
            if let Some(list_ty) = record_field_java_type(proc, base, field).filter(|t| t.contains("List<")) {
                return composite_list_assign(base, field, &list_ty, value, proc);
            }

            if is_out_param(base, proc) {
                let val = expr_to_java(value, proc);
                return format!("{}.get().put(\"{}\", {});", base_camel, field_camel, val);
            }

            let base_type = proc.local_vars.get(&base.to_lowercase()).map(|s| s.as_str()).unwrap_or("");
            if base_type.contains("Map") {
                let val = expr_to_java(value, proc);
                return format!("{}.put(\"{}\", {});", base_camel, field_camel, val);
            }

            if base_type == "Object" || base_type.is_empty() {
                // Issue #136 corpus: `known_pkg.var := v` — a cross-package
                // package-var WRITE. Emit `<pkg>Service.<var> = v` (the field
                // is marked `public` by mark_externally_shared_vars); writes
                // to `final` (constant) vars degrade to a comment.
                if base_type.is_empty() {
                    if let Some((access, var_info)) = cross_pkg_var_info(proc, base, field) {
                        let val = expr_to_java(value, proc);
                        if var_info.is_constant {
                            return format!(
                                "// {} := <value> — cross-package constant package var (issue #136)",
                                ref_name
                            );
                        }
                        return format!("{} = {};", access, val);
                    }
                }
                let val = expr_to_java(value, proc);
                return format!("((java.util.Map<String, Object>) {}).put(\"{}\", {});", base_camel, field_camel, val);
            }
        }

        let camel = snake_to_camel(ref_name);
        // Issue #136-B: OUT refcursor params are promoted to a `List` return
        // value and removed from the method signature — residual references
        // (e.g. the exception-branch `p_result := NULL`) must not emit
        // `.set()` on a variable that no longer exists. Degrade to a comment.
        if proc.parameters.iter().any(|p| {
            p.is_out()
                && p.is_refcursor()
                && p.name.to_lowercase().replace('_', "") == ref_name.to_lowercase().replace('_', "")
        }) {
            return format!("// {} := <value> — refcursor OUT param promoted to List return (issue #136)", ref_name);
        }
        if is_out_param(ref_name, proc) {
            let val = expr_to_java(value, proc);
            let param = proc.parameters.iter().find(|p| {
                let base = if ref_name.contains('.') { ref_name.split('.').next().unwrap() } else { ref_name };
                p.name == base && p.is_out()
            });
            let coerced = if let Some(p) = param {
                let ptype = p.java_type.trim();
                if ptype == "String" && !val.starts_with('"') && val != "null" && !is_nullish_java_expr(&val) {
                    format!("String.valueOf({})", val)
                } else if ptype == "Long" && val.starts_with('"') {
                    format!("Long.valueOf({})", val)
                } else if ptype == "Long" && val.chars().all(|c| c.is_ascii_digit()) {
                    format!("Long.valueOf({})", java_int_lit(&val))
                } else if ptype == "Long" && (val.contains("BigDecimal") || val.contains("java.math.BigDecimal")) {
                    format!("({}).longValue()", val)
                } else if ptype == "Long" {
                    // `.set(123)` / `.set((-1))` — a bare int literal cannot
                    // widen+box to Long in one method-invocation step; emit an
                    // explicit long literal instead.
                    if let Some(lit) = int_literal_digits(&val) {
                        format!("({}L)", lit)
                    } else {
                        let trimmed = val.trim();
                        let matched = proc.local_vars.iter().any(|(k, t)| {
                            k.to_lowercase().replace("_", "") == trimmed.to_lowercase().replace("_", "")
                                && t.contains("BigDecimal")
                        });
                        if matched {
                            format!("({}).longValue()", val)
                        } else {
                            val
                        }
                    }
                } else if ptype == "java.math.BigDecimal" {
                    if val.starts_with("String.valueOf(") {
                        let inner = val.trim_start_matches("String.valueOf(").trim_end_matches(')');
                        format!("((java.math.BigDecimal) {})", inner)
                    } else if val.starts_with("(-") && val.ends_with(')') {
                        // Negative numeric literal: (-1) → BigDecimal.valueOf(-1L)
                        let inner = val.trim_start_matches("(-").trim_end_matches(')');
                        if inner.chars().all(|c| c.is_ascii_digit()) {
                            format!("java.math.BigDecimal.valueOf(-{}L)", inner)
                        } else {
                            val
                        }
                    } else if val.chars().all(|c| c.is_ascii_digit()) {
                        format!("java.math.BigDecimal.valueOf({}L)", val)
                    } else if produces_double(&val) {
                        format!("java.math.BigDecimal.valueOf({})", val)
                    } else {
                        val
                    }
                } else if ptype == "Integer" || ptype == "int" {
                    // Narrow long-producing expressions to int; plain int literals
                    // autobox fine and must stay unchanged. (#114 review minor 9):
                    // `contains("0L")` also matches 100L/1000L — match the exact
                    // `0L` literal token instead; and don't re-narrow a value that
                    // already went through .intValue().
                    if is_long_literal(&val)
                        || val.contains(".longValue()")
                        || val.contains("Long.parseLong")
                        || val.contains(".doubleValue()")
                    {
                        format!("((Number)({})).intValue()", val)
                    } else {
                        val
                    }
                } else if !ptype.is_empty() && ptype != "Object" && ptype != "String" {
                    if val.starts_with("String.valueOf(") {
                        let inner = val.trim_start_matches("String.valueOf(").trim_end_matches(')');
                        format!("(({}) {})", ptype, inner)
                    } else {
                        val
                    }
                } else {
                    val
                }
            } else {
                val
            };
            return format!("{}.set({});", camel, coerced);
        }
        let mut var_type = proc.local_vars.get(&ref_name.to_lowercase()).cloned().or_else(|| {
            let ref_lower = ref_name.to_lowercase().replace("_", "");
            proc.package_vars
                .iter()
                .find(|(k, _)| k.to_lowercase().replace("_", "") == ref_lower)
                .map(|(_, vi)| vi.java_type.clone())
        });
        // Check if promoted to AtomicReference<> by out_local_vars (OUT-arg usage)
        let ref_lower_no_underscore = ref_name.to_lowercase().replace("_", "");
        if let Some((_, inner_type)) =
            proc.out_local_vars.iter().find(|(k, _)| k.to_lowercase().replace("_", "") == ref_lower_no_underscore)
        {
            var_type = Some(format!("AtomicReference<{}>", inner_type));
        }
        let mut val = expr_to_java(value, proc);
        // M5 (#114 review): a promoted local var on the RHS is an AtomicReference —
        // deref to the value before assignment/.set(), or javac fails on the type.
        val = maybe_deref_promoted(&val, proc);
        // Local vars promoted to AtomicReference for OUT-arg usage need .set()
        if let Some(vt) = &var_type {
            if vt.contains("AtomicReference<") {
                let inner_type = vt.trim_start_matches("AtomicReference<").trim_end_matches('>');
                let coerced = if (inner_type == "Integer" || inner_type == "int")
                    && (val.contains(".longValue()") || is_long_literal(&val))
                {
                    format!("((Number)({})).intValue()", val)
                } else {
                    coerce_for_type(&val, Some(inner_type), proc)
                };
                return format!("{}.set({});", camel, coerced);
            }
        }
        let skip_coerce = var_type.as_ref().is_some_and(|t| t.contains("BigDecimal")) && is_bigdecimal_var(&val, proc);
        if !skip_coerce {
            val = coerce_for_type(&val, var_type.as_deref(), proc);
        }
        return format!("{} = {};", camel, val);
    }
    let var = expr_to_java(target, proc);
    let mut val = expr_to_java(value, proc);
    // M5 (#114 review): a promoted local var on the RHS is an AtomicReference —
    // deref to the value before plain assignment, or javac fails on the type.
    val = maybe_deref_promoted(&val, proc);
    format!("{} = {};", var, val)
}

/// Integer literal possibly wrapped in one pair of parens: `123`, `(-1)`,
/// `(123)`. Returns the signed digits, or None for anything else.
fn int_literal_digits(expr: &str) -> Option<String> {
    let t = expr.trim();
    let inner = t
        .strip_prefix('(')
        .and_then(|s| s.strip_suffix(')'))
        .map(str::trim)
        .filter(|_| t.starts_with('(') && t.ends_with(')'))
        .unwrap_or(t);
    let digits = inner.strip_prefix('-').unwrap_or(inner);
    if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) {
        Some(inner.to_string())
    } else {
        None
    }
}

/// Render a Java integer literal with an `L` suffix when it exceeds the
/// `int` range (2^31-1). Unsuffixed literals are `int`-typed per JLS 3.10.1,
/// so `BigDecimal.valueOf(99999999999)` fails to compile — needs `...L`.
pub(crate) fn java_int_lit(digits: &str) -> String {
    let trimmed = digits.trim();
    if !trimmed.is_empty() && trimmed.chars().all(|c| c.is_ascii_digit()) {
        if let Ok(v) = trimmed.parse::<i64>() {
            if v > i32::MAX as i64 {
                return format!("{}L", trimmed);
            }
        }
    }
    trimmed.to_string()
}

/// Resolve the declared Java type of a variable expression.
/// Handles already-rendered camelCase Java expressions against snake_case keys.
/// Cascade: local_vars -> parameters -> package_vars -> out_local_vars.
pub(crate) fn resolve_var_java_type(expr: &str, proc: &ProcedureInfo) -> Option<String> {
    let name = expr.trim();
    let base = name.split(['.', '(']).next().unwrap_or(name);
    if base.is_empty() {
        return None;
    }
    if let Some(ty) = proc.local_vars.get(&base.to_lowercase()) {
        return Some(ty.clone());
    }
    let base_key = base.to_lowercase().replace('_', "");
    for (var_name, var_type) in &proc.local_vars {
        if var_name.to_lowercase().replace('_', "") == base_key {
            return Some(var_type.clone());
        }
    }
    for p in &proc.parameters {
        if p.name.to_lowercase().replace('_', "") == base_key || crate::naming::snake_to_camel(&p.name) == base {
            return Some(p.java_type.clone());
        }
    }
    for (var_name, info) in &proc.package_vars {
        if var_name.to_lowercase().replace('_', "") == base_key {
            return Some(info.java_type.clone());
        }
    }
    for (var_name, ty) in &proc.out_local_vars {
        if var_name.to_lowercase().replace('_', "") == base_key {
            return Some(ty.clone());
        }
    }
    None
}

/// True when the expression is a single unqualified variable reference, i.e. the
/// only shape for which `resolve_var_java_type` describes the EXPRESSION rather
/// than merely its receiver.
fn looks_like_string_expr(expr: &str) -> bool {
    let t = expr.trim();
    t.starts_with('"') || t.starts_with("String.valueOf(")
}

fn looks_like_timestamp_expr(expr: &str, proc: &ProcedureInfo) -> bool {
    let t = expr.trim();
    if t.starts_with("new java.sql.Timestamp") || t.starts_with("java.sql.Timestamp") {
        return true;
    }
    is_bare_identifier(t) && resolve_var_java_type(t, proc).as_deref().map(|s| s.contains("Timestamp")).unwrap_or(false)
}

fn looks_like_numeric_expr(expr: &str) -> bool {
    let t = expr.trim();
    t.ends_with("L")
        || t.ends_with("l")
        || t.contains(".longValue()")
        || t.contains(".intValue()")
        || t.contains(".doubleValue()")
        || t.contains("Long.parseLong")
        || t.contains("Integer.parseInt")
}

fn is_bare_identifier(expr: &str) -> bool {
    let t = expr.trim();
    !t.is_empty()
        && t.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '$')
        && !t.chars().next().map(|c| c.is_ascii_digit()).unwrap_or(true)
}

/// True when `inner` is a bare identifier naming an IN parameter that the
/// service emitter declares as a Java primitive (`long`, `int`, ...). Only
/// parameters are emitted via `boxed_to_primitive`; locals stay boxed, so a
/// `Long` local legitimately supports `== null`.
fn is_primitive_emitted_param(inner: &str, proc: &ProcedureInfo) -> bool {
    let base = inner.trim().trim_start_matches('(').trim_end_matches(')').split(['.', '(']).next().unwrap_or("");
    if base.is_empty() || !is_bare_identifier(base) {
        return false;
    }
    proc.parameters.iter().any(|p| {
        if p.is_out() {
            return false;
        }
        let java_name = crate::naming::snake_to_camel(&p.name);
        if java_name != base && !p.name.eq_ignore_ascii_case(base) {
            return false;
        }
        // Share the service emitter's signature decision: `default null` and
        // `IS [NOT] NULL` sentinel params stay boxed, so `== null` is legal.
        matches!(
            crate::generate::service::in_param_signature_type(p, proc),
            "long" | "int" | "boolean" | "double" | "float" | "short" | "byte"
        )
    })
}

/// Whether a rendered value contains the Long literal `0L` as a standalone
/// token (not as a digit suffix: 100L/1000L contain "0L" as a substring but
/// are not the zero literal). Used to decide integer narrowing.
fn is_long_literal(expr: &str) -> bool {
    static RE: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"(?:\D|^)0[lL](?:\D|$)").unwrap());
    RE.is_match(expr)
}

fn has_decimal_literal(expr: &str) -> bool {
    let bytes = expr.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_digit() && (i == 0 || (!bytes[i - 1].is_ascii_alphanumeric() && bytes[i - 1] != b'_')) {
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            if i + 1 < bytes.len() && bytes[i] == b'.' && bytes[i + 1].is_ascii_digit() {
                return true;
            }
        }
        i += 1;
    }
    false
}

/// True if the rendered expression yields a Java `double`.
fn produces_double(expr: &str) -> bool {
    expr.contains("Double.parseDouble")
        || expr.contains(".doubleValue()")
        || expr.contains("Math.pow")
        || expr.contains("Math.sqrt")
        || expr.contains(" / ")
        || has_decimal_literal(expr)
}

/// True if the expression yields a PRIMITIVE int. Java will not convert `int` to
/// a boxed `Long`, so such an expression must never be passed through unboxed to
/// a Long target — it has to be routed via `((Number) expr).longValue()`.
fn produces_primitive_int(expr: &str) -> bool {
    expr.contains(".length()")
        || expr.contains(".size()")
        || expr.contains(".indexOf(")
        || expr.contains(".lastIndexOf(")
        || expr.contains(".compareTo(")
        || expr.contains(".hashCode()")
        || expr.contains(".charAt(")
        || expr.contains(".ordinal()")
        || expr.contains(".intValue()")
        || expr.contains("(int)")
}

pub(crate) fn coerce_for_type(expr: &str, target_type: Option<&str>, proc: &ProcedureInfo) -> String {
    let trimmed = expr.trim();
    if let Some(t) = target_type {
        if t == "String"
            && (trimmed.starts_with("Double.parseDouble")
                || trimmed.starts_with("Long.parseLong")
                || trimmed.starts_with("Integer.parseInt")
                || trimmed.starts_with("Math."))
        {
            return format!("String.valueOf({})", trimmed);
        }
        if t.contains("Timestamp") {
            if let Some(s) = simplify_ts_expr(trimmed) {
                return s;
            }
        }
        if (t.contains("java.sql.Date") || t == "Date") && trimmed.starts_with("new java.sql.Timestamp") {
            return format!("new java.sql.Date(({}).getTime())", trimmed);
        }
        if (t.contains("java.sql.Date") || t == "Date")
            && (trimmed.contains("java.time.LocalDate") || trimmed.contains(".toLocalDate()"))
            && !trimmed.starts_with("java.sql.Date.valueOf(")
        {
            return format!("java.sql.Date.valueOf({})", trimmed);
        }
    }
    // Early-exit: if target is BigDecimal and expr is already a BigDecimal value,
    // don't double-wrap with BigDecimal.valueOf()
    if let Some(t) = target_type {
        if t.contains("BigDecimal") && is_bigdecimal_var(trimmed, proc) {
            return trimmed.to_string();
        }
    }
    if is_nullish_java_expr(trimmed) {
        let mut default: Option<String> = None;
        if let Some(t) = target_type {
            if t.contains("BigDecimal") {
                default = Some("java.math.BigDecimal.ZERO".to_string());
            } else if t == "Double" || t == "double" {
                default = Some("0.0d".to_string());
            } else if t == "Float" || t == "float" {
                default = Some("0.0f".to_string());
            } else if t == "Integer" || t == "int" {
                default = Some("0".to_string());
            } else if t == "Long" || t == "long" {
                default = Some("0L".to_string());
            } else if t == "Boolean" || t == "boolean" {
                default = Some("false".to_string());
            } else if t == "String" {
                default = Some("null".to_string());
            }
        }
        let default = default.unwrap_or_else(|| if trimmed == "null" { "null".to_string() } else { "0".to_string() });
        // TOBEFIX marker must survive coercion to a type default, otherwise the
        // unresolved-name warning silently disappears from generated code (Task 6).
        if trimmed.contains("/* TOBEFIX") {
            if let Some(end) = trimmed.find("*/") {
                // find() returns the index of `*`; `*/` is 2 bytes wide, so the
                // slice must extend past the trailing `/` (…[..=end] would drop it).
                return format!("{} {}", &trimmed[..end + 2], default);
            }
        }
        return default;
    }
    if trimmed == "\"\"" || trimmed == "''" {
        if let Some(t) = target_type {
            if t.contains("Timestamp") {
                return "new java.sql.Timestamp(0)".to_string();
            }
            if t.contains("java.sql.Date") || t == "Date" {
                return "new java.sql.Date(0)".to_string();
            }
            // Issue #57: empty string '' → NUMBER is implicitly NULL in GaussDB
            if t == "Long"
                || t == "long"
                || t == "Integer"
                || t == "int"
                || t == "Double"
                || t == "double"
                || t == "Float"
                || t == "float"
                || t.contains("BigDecimal")
                || t.contains("BigInteger")
            {
                return "null".to_string();
            }
        }
        return trimmed.to_string();
    }
    // String literal → numeric. `"1"` / `"3.1"` must parse; `(Number)("1")` does not compile.
    if trimmed.len() >= 2 && trimmed.starts_with('"') && trimmed.ends_with('"') {
        let inner = &trimmed[1..trimmed.len() - 1];
        match target_type {
            Some(t) if t == "Long" || t == "long" => {
                if inner.is_empty() {
                    return "0L".to_string();
                }
                if inner.parse::<i64>().is_ok() {
                    return format!("{}L", inner);
                }
                return format!("((Number)(Double.parseDouble({}))).longValue()", trimmed);
            }
            Some(t) if t == "Integer" || t == "int" => {
                if inner.is_empty() {
                    return "0".to_string();
                }
                if inner.parse::<i32>().is_ok() {
                    return inner.to_string();
                }
                return format!("((Number)(Double.parseDouble({}))).intValue()", trimmed);
            }
            Some(t) if t == "Double" || t == "double" => {
                if inner.is_empty() {
                    return "0.0d".to_string();
                }
                return format!("Double.parseDouble({})", trimmed);
            }
            Some(t) if t.contains("BigDecimal") => {
                if inner.is_empty() {
                    return "java.math.BigDecimal.ZERO".to_string();
                }
                return format!("new java.math.BigDecimal({})", trimmed);
            }
            _ => {}
        }
    }
    // Issue #72: never emit `(Number) <String>`. When the source resolves to a
    // String and the target is numeric, parse instead of cast.
    //
    // Only a BARE identifier qualifies. resolve_var_java_type() base-splits on
    // '.'/'(' and so reports the RECEIVER's type: for `strVar.length()` it says
    // "String" even though the expression is an int. Wrapping that would emit
    // Long.parseLong(int), which does not compile. String.valueOf() is used
    // defensively so any future mis-resolution degrades to a runtime concern
    // rather than a compile break.
    if is_bare_identifier(trimmed) && resolve_var_java_type(trimmed, proc).as_deref() == Some("String") {
        match target_type {
            Some(t) if t == "Long" || t == "long" => {
                return format!("Long.parseLong(String.valueOf({}))", trimmed);
            }
            Some(t) if t == "Integer" || t == "int" => {
                return format!("Integer.parseInt(String.valueOf({}))", trimmed);
            }
            Some(t) if t == "Double" || t == "double" => {
                return format!("Double.parseDouble(String.valueOf({}))", trimmed);
            }
            Some(t) if t.contains("BigDecimal") => {
                return format!("new java.math.BigDecimal(String.valueOf({}))", trimmed);
            }
            _ => {}
        }
    }
    // Issue #136-D: same String→numeric parse when the source is a CALL whose
    // return type resolves to String (`this.fncText(...)`, `svc.fncText(...)`).
    // `(Number)` cannot cast a String — route through String.valueOf like #72.
    if call_expr_return_type(trimmed, proc).as_deref() == Some("String") {
        match target_type {
            Some(t) if t == "Long" || t == "long" => {
                return format!("Long.parseLong(String.valueOf({}))", trimmed);
            }
            Some(t) if t == "Integer" || t == "int" => {
                return format!("Integer.parseInt(String.valueOf({}))", trimmed);
            }
            Some(t) if t == "Double" || t == "double" => {
                return format!("Double.parseDouble(String.valueOf({}))", trimmed);
            }
            Some(t) if t.contains("BigDecimal") => {
                return format!("new java.math.BigDecimal(String.valueOf({}))", trimmed);
            }
            _ => {}
        }
    }
    match target_type {
        Some(t) if t.contains("BigDecimal") && trimmed.starts_with('"') => {
            format!("new java.math.BigDecimal({})", trimmed)
        }
        Some(t)
            if t.contains("BigDecimal")
                && !trimmed.starts_with("java.math.BigDecimal")
                && !trimmed.starts_with("new java.math.BigDecimal")
                && !trimmed.starts_with("((java.math.BigDecimal)")
                && (trimmed.starts_with("Math.")
                    || (trimmed.contains("Math.")
                        && trimmed
                            .chars()
                            .next()
                            .map(|c| c.is_ascii_digit() || c == '(' || c == '-')
                            .unwrap_or(false))) =>
        {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t) if t.contains("BigDecimal") && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t)
            if t.contains("BigDecimal")
                && trimmed.starts_with("java.util.Optional")
                && trimmed.ends_with(".orElse(null)") =>
        {
            // Optional<Integer>-or-null → BigDecimal-or-null; preserves SQL
            // NULL instead of NPE-ing on ((Number) optional).longValue().
            optional_unwrap(trimmed, "__n -> java.math.BigDecimal.valueOf(__n.longValue())", "null")
                .unwrap_or_else(|| trimmed.to_string())
        }
        Some(t)
            if t.contains("BigDecimal")
                && trimmed.contains(".get(")
                && !trimmed.starts_with("new java.math.BigDecimal") =>
        {
            // If arithmetic operators are present beyond .get(), the result is already a primitive
            let has_arithmetic = trimmed.contains(" * ")
                || trimmed.contains(" + ")
                || trimmed.contains(" - ")
                || trimmed.contains(" / ");
            if has_arithmetic {
                format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
            } else {
                format!("java.math.BigDecimal.valueOf(((Number) {}).longValue())", trimmed)
            }
        }
        Some(t) if t.contains("BigDecimal") && trimmed.contains("this.") && !trimmed.starts_with('(') => {
            // Issue #136-A: a blind `(BigDecimal)` cast only compiles when the
            // callee actually returns BigDecimal — `RETURN NUMBER` functions
            // emit `Long`. Resolve the sibling return type and convert
            // numerics via valueOf instead.
            match call_expr_return_type(trimmed, proc).as_deref() {
                Some(rt)
                    if !rt.contains("BigDecimal")
                        && (rt.contains("Long")
                            || rt.contains("long")
                            || rt.contains("Integer")
                            || rt.contains("int")
                            || rt.contains("Double")
                            || rt.contains("double")
                            || rt.contains("Float")
                            || rt.contains("float")
                            || rt.contains("Short")
                            || rt.contains("short")
                            || rt.contains("Byte")
                            || rt.contains("byte")) =>
                {
                    format!("java.math.BigDecimal.valueOf({})", trimmed)
                }
                _ => format!("((java.math.BigDecimal) {})", trimmed),
            }
        }
        Some(t)
            if t.contains("BigDecimal")
                && trimmed.contains('.')
                && !trimmed.starts_with('"')
                && !is_nullish_java_expr(trimmed)
                && !trimmed.contains("this.")
                && !trimmed.contains(".multiply(")
                && !trimmed.contains(".add(")
                && !trimmed.contains(".subtract(")
                && !trimmed.contains(".divide(")
                && !trimmed.contains(".setScale(")
                && !trimmed.contains("BigDecimal.valueOf(")
                && !trimmed.contains("BigDecimal.ZERO")
                && !trimmed.contains("BigDecimal.ONE")
                && !trimmed.starts_with("new java.math.BigDecimal") =>
        {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t)
            if t.contains("BigDecimal")
                && !trimmed.contains("BigDecimal")
                && !trimmed.contains("String.valueOf(")
                && !trimmed.starts_with('"')
                && !trimmed.contains("this.")
                && !trimmed.contains(".multiply(")
                && !trimmed.contains(".add(")
                && !trimmed.contains(".subtract(")
                && !trimmed.contains(".divide(")
                && !trimmed.contains(".setScale(")
                && !trimmed.contains(".abs(")
                && !trimmed.contains(".negate(")
                && !trimmed.contains(".remainder(")
                && !trimmed.contains(".pow(")
                && !trimmed.contains(".max(")
                && !trimmed.contains(".min(")
                && !trimmed.contains(".stripTrailingZeros(") =>
        {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        // java.util.Optional producers (ARRAY_POSITION/ARRAY_LENGTH) already
        // yield boxed Integer-or-null — ((Number)..).intValue() would NPE.
        Some(t)
            if (t == "Integer" || t == "int")
                && trimmed.contains(".get(")
                && !trimmed.starts_with("java.util.Optional") =>
        {
            format!("((Number) {}).intValue()", trimmed)
        }
        Some(t)
            if (t == "Long" || t == "long")
                && trimmed.contains(".get(")
                && !trimmed.contains(" * ")
                && !trimmed.contains(" + ")
                && !trimmed.contains(" - ")
                && !trimmed.contains(" / ") =>
        {
            format!("((Number) {}).longValue()", trimmed)
        }
        Some(t) if t == "java.sql.Timestamp" && trimmed.contains(".get(") => {
            format!("((java.sql.Timestamp) {})", trimmed)
        }
        Some(t) if t == "java.sql.Date" && trimmed.contains(".get(") => {
            format!("((java.sql.Date) {})", trimmed)
        }
        Some(t) if t == "Long" && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("Long.valueOf({})", java_int_lit(trimmed))
        }
        Some(t)
            if t == "String"
                && trimmed != "null"
                && !trimmed.starts_with("String.valueOf(")
                && !trimmed.contains(".concat(String")
                && !trimmed.contains(".substring(")
                && !trimmed.contains(".toUpperCase()")
                && !trimmed.contains(".toLowerCase()")
                && !trimmed.contains(".trim()")
                && !trimmed.contains(".replace(")
                && !(trimmed.starts_with('"')
                    && !trimmed.contains(".length()")
                    && !trimmed.contains(".indexOf(")
                    && !trimmed.contains(".charAt(")
                    && !trimmed.contains(" + ")
                    && !trimmed.contains(" - ")) =>
        {
            format!("String.valueOf({})", trimmed)
        }
        Some(t) if t.starts_with("Map<") && trimmed.contains("this.") && !trimmed.starts_with('(') => {
            format!("(Map<String, Object>) {}", trimmed)
        }
        Some(t) if (t == "Long" || t == "long") && trimmed.contains(".indexOf(") && !trimmed.contains("(long)") => {
            format!("(long)({})", trimmed)
        }
        Some(t) if (t == "Long" || t == "long") && trimmed.contains("BigDecimal") => {
            format!("({}).longValue()", trimmed)
        }
        Some(t)
            if (t == "Long" || t == "long")
                && (trimmed.contains(" * ")
                    || trimmed.contains(" + ")
                    || trimmed.contains(" - ")
                    || trimmed.contains(" / "))
                && !trimmed.starts_with("java.util.Optional")
                && !trimmed.starts_with("new ")
                && !produces_double(trimmed)
                && !produces_primitive_int(trimmed) =>
        {
            trimmed.to_string()
        }
        // Nullable boxed-Long producers must NOT be routed through
        // Number.longValue() — that unboxes null into an NPE where Oracle
        // semantics assign NULL. Keep the Long expression as-is. Covers
        // coll.NEXT/PRIOR/FIRST/LAST (`cond ? Long.valueOf(..) : null`) and
        // bounds-guarded element reads (`cond ? list.get(..) : null`); the
        // `List<?>` carve-out keeps the (Number) wrap that untyped captures
        // need to compile.
        Some(t)
            if t == "Long"
                && trimmed.contains(" : null)")
                && (trimmed.contains("Long.valueOf(")
                    || (trimmed.contains(".get(") && !trimmed.contains("List<?>"))) =>
        {
            trimmed.to_string()
        }
        Some(t)
            if (t == "Integer" || t == "int")
                && trimmed.starts_with("java.util.Optional")
                && trimmed.ends_with(".orElse(null)") =>
        {
            // Optional<Integer>-or-null → Integer-or-null / 0 for primitives.
            let default = if t == "int" { "0" } else { "null" };
            optional_unwrap(trimmed, "Number::intValue", default).unwrap_or_else(|| trimmed.to_string())
        }
        Some(t) if (t == "Long" || t == "long") => {
            // Optional<Integer>-or-null: `((Number) opt).longValue()` NPEs on
            // SQL NULL — unwrap to Long-or-null (boxed) / 0L (primitive).
            if trimmed.starts_with("java.util.Optional") && trimmed.ends_with(".orElse(null)") {
                let default = if t == "long" { "0L" } else { "null" };
                return optional_unwrap(trimmed, "Number::longValue", default).unwrap_or_else(|| trimmed.to_string());
            }
            if looks_like_timestamp_expr(trimmed, proc) {
                format!("{}.getTime()", trimmed)
            } else if looks_like_string_expr(trimmed) {
                format!("Long.parseLong(String.valueOf({}))", trimmed)
            } else {
                format!("((Number)({})).longValue()", trimmed)
            }
        }
        Some(t) if t == "String" && looks_like_numeric_expr(trimmed) => {
            format!("String.valueOf({})", trimmed)
        }
        // Issue #136 batch-4 F4: Object-typed sources into a Map slot need an
        // explicit cast (record reads produce Object from .get()). `.get(` is
        // only safe when the container is Map-typed — `List<Long>.get()`
        // returns Long and a Long→Map cast does not compile.
        Some(t)
            if (t.starts_with("Map<") || t.contains("Map<String"))
                && !trimmed.contains("Map<")
                && !trimmed.starts_with("((")
                && {
                    let src = resolve_var_java_type(trimmed, proc).unwrap_or_default();
                    src == "Object"
                        || trimmed.contains("getOrDefault(")
                        || (trimmed.contains(".get(") && (src.contains("Map<") || src.contains("Map<String")))
                } =>
        {
            format!("((Map<String, Object>) {})", trimmed)
        }
        // Issue #136 batch-4 F4 / batch-5 R2: Map<->List assignments have no
        // sound Java coercion, but `null` silently dropped the value and a
        // bare assignment did not compile. Emit a compile-safe cast through
        // Object with the TOBEFIX marker kept visible — javac accepts the
        // unchecked cast, and a genuine type mismatch surfaces as a runtime
        // ClassCastException at the assignment site instead of vanishing.
        Some(t)
            if (t.contains("List<") || t.contains("java.util.List"))
                && (resolve_var_java_type(trimmed, proc).is_some_and(|s| s.contains("Map<"))
                    || call_expr_return_type(trimmed, proc).is_some_and(|s| s.contains("Map<"))) =>
        {
            format!("/* TOBEFIX: Map->List assignment has no sound coercion */ (({}) (Object) {})", t, trimmed)
        }
        Some(t)
            if (t.starts_with("Map<") || t.contains("Map<String"))
                && (resolve_var_java_type(trimmed, proc).is_some_and(|s| s.contains("List<"))
                    || call_expr_return_type(trimmed, proc).is_some_and(|s| s.contains("List<"))) =>
        {
            format!("/* TOBEFIX: List->Map assignment has no sound coercion */ (({}) (Object) {})", t, trimmed)
        }
        _ => expr.to_string(),
    }
}

fn coerce_arg_to_type(arg: &str, target_type: &str, proc: &ProcedureInfo) -> String {
    let trimmed = arg.trim();
    let target_is_bigdecimal = target_type.contains("BigDecimal");
    let target_is_long = target_type == "long" || target_type == "Long";

    // Issue #136-B: refcursor OUT params were removed from the signature
    // (promoted to a List return) — passing one as an argument degrades to
    // `null` rather than `.get()` on a non-existent variable.
    if proc.parameters.iter().any(|p| {
        p.is_out()
            && p.is_refcursor()
            && p.name.to_lowercase().replace('_', "") == trimmed.to_lowercase().replace('_', "")
    }) {
        return "null".to_string();
    }

    // Unwrap AtomicReference OUT params when passed as regular-type args
    if is_out_param(trimmed, proc)
        || proc
            .out_local_vars
            .iter()
            .any(|(k, _)| k.to_lowercase().replace("_", "") == trimmed.to_lowercase().replace("_", ""))
    {
        // OUT param / promoted local — pass .get() value, then coerce further
        return coerce_arg_to_type(&format!("{}.get()", trimmed), target_type, proc);
    }

    // Optional<Integer>-or-null producers (array_position/array_length):
    // unwrap per target type — casting the Optional to Number or parsing
    // "Optional[...]"/"null" as a number is a compile error or runtime bug.
    if trimmed.starts_with("java.util.Optional") && trimmed.ends_with(".orElse(null)") {
        if target_is_bigdecimal {
            return optional_unwrap(trimmed, "__n -> java.math.BigDecimal.valueOf(__n.longValue())", "null")
                .unwrap_or_else(|| trimmed.to_string());
        }
        if target_is_long {
            let default = if target_type == "long" { "0L" } else { "null" };
            return optional_unwrap(trimmed, "Number::longValue", default).unwrap_or_else(|| trimmed.to_string());
        }
        if target_type == "int" || target_type == "Integer" {
            let default = if target_type == "int" { "0" } else { "null" };
            return optional_unwrap(trimmed, "Number::intValue", default).unwrap_or_else(|| trimmed.to_string());
        }
    }

    if target_is_bigdecimal {
        if trimmed.chars().all(|c| c.is_ascii_digit()) {
            return format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed));
        }
        if trimmed.chars().all(|c| c.is_ascii_digit() || c == '.') && trimmed.contains('.') {
            return format!("new java.math.BigDecimal(\"{}\")", trimmed);
        }
        if is_integer_type(trimmed, proc) {
            return format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed));
        }
        if trimmed.contains(".get(") {
            return format!("java.math.BigDecimal.valueOf(((Number) {}).longValue())", trimmed);
        }
    }

    let target_is_int = target_type == "int" || target_type == "Integer";

    // java.util.Optional producers (ARRAY_POSITION/ARRAY_LENGTH) already
    // yield a boxed Integer-or-null — wrapping in ((Number)..).intValue()
    // would NPE on the SQL NULL case.
    if target_is_int && trimmed.contains(".get(") && !trimmed.starts_with("java.util.Optional") {
        return format!("((Number) {}).intValue()", trimmed);
    }

    if target_is_long {
        let arg_type = infer_arg_type_from_expr(trimmed, proc);
        if arg_type == "BigDecimal" {
            return format!("({}).longValue()", trimmed);
        }
        if arg_type == "Object" {
            return format!("Long.parseLong(String.valueOf({}))", trimmed);
        }
        if arg_type == "String" {
            return format!("Long.parseLong(String.valueOf({}))", trimmed);
        }
    }

    // Issue #136-D: numeric args into a String parameter — a bare `1` does not
    // widen to String; `String.valueOf` preserves the implicit conversion.
    // `infer_arg_type_from_expr` reports bare digit literals as "long".
    if target_type == "String"
        && (trimmed.contains(".get(")
            || matches!(infer_arg_type_from_expr(trimmed, proc), "Object" | "long" | "BigDecimal"))
    {
        return format!("String.valueOf({})", trimmed);
    }

    let target_is_date = target_type.contains("java.sql.Date") || target_type == "Date";
    let target_is_timestamp = target_type.contains("Timestamp");
    if (target_is_date || target_is_timestamp) && trimmed.contains(".get(") {
        let cast_type = if target_is_timestamp { "java.sql.Timestamp" } else { "java.sql.Date" };
        return format!("({}) ({})", cast_type, trimmed);
    }

    arg.to_string()
}

fn infer_arg_type_from_expr(expr: &str, proc: &ProcedureInfo) -> &'static str {
    let trimmed = expr.trim();
    if let Some(ty) = proc.local_vars.get(&trimmed.to_lowercase()) {
        return match ty.as_str() {
            "int" | "long" | "Integer" | "Long" => "long",
            "String" => "String",
            t if t.contains("BigDecimal") => "BigDecimal",
            t if t.starts_with("Map<") => "Map",
            t if t.starts_with("List<") => "List",
            _ => "Object",
        };
    }
    if trimmed.starts_with('"') {
        return "String";
    }
    if trimmed.chars().all(|c| c.is_ascii_digit()) {
        return "long";
    }
    if trimmed.contains(".get(") {
        return "Object";
    }
    "Object"
}

fn is_out_param(name: &str, proc: &ProcedureInfo) -> bool {
    // Already-dereferenced or call expressions (e.g. `x.get()`) are not param names;
    // matching them would make coerce_arg_to_type recurse forever appending `.get()`.
    if name.contains('(') {
        return false;
    }
    let base_name = if name.contains('.') { name.split('.').next().unwrap() } else { name };
    let bn = base_name.to_lowercase().replace("_", "");
    proc.parameters.iter().any(|p| p.name.to_lowercase().replace("_", "") == bn && p.is_out())
}

thread_local! {
    // Bare local vars queued by `emit_cross_pkg_call` for promotion to
    // `AtomicReference<T>`. Expr-generation code only has `&ProcedureInfo` (read-only)
    // available, so it can't write `proc.out_local_vars` directly; the caller further
    // up the stack (which holds `&mut ProcedureInfo`) drains this via
    // `take_pending_out_promotions` and applies it once statement processing settles.
    static PENDING_OUT_PROMOTIONS: std::cell::RefCell<Vec<(String, String)>> = const { std::cell::RefCell::new(Vec::new()) };
}

fn queue_out_promotion(var_name_lower: String, java_type: String) {
    PENDING_OUT_PROMOTIONS.with(|q| q.borrow_mut().push((var_name_lower, java_type)));
}

/// Peek whether a var has a queued (not yet applied) OUT promotion. Expr
/// rendering runs before analyze drains the queue, so arithmetic/reads of a
/// var queued by an earlier cross-package OUT-arg call must still deref it.
/// Accepts the camelCase Java name; queue keys are snake_case lowercase.
fn has_pending_out_promotion(var_camel: &str) -> bool {
    let camel_lower = var_camel.to_lowercase();
    PENDING_OUT_PROMOTIONS
        .with(|q| q.borrow().iter().any(|(v, _)| crate::naming::snake_to_camel(v).to_lowercase() == camel_lower))
}

/// Drains all pending OUT-arg promotions queued by `emit_cross_pkg_call` since the
/// last drain. Called from `analyze_procedure` once a procedure's statements are done
/// generating, to apply them onto `proc.out_local_vars`.
pub fn take_pending_out_promotions() -> Vec<(String, String)> {
    PENDING_OUT_PROMOTIONS.with(|q| q.borrow_mut().drain(..).collect())
}

/// Java expression used to pad a missing call argument for `param`'s
/// declared signature type. Mirrors the service emitter: `default null`
/// params stay boxed (→ `null`); otherwise the signature is the
/// primitive-narrowed type, so a bare `null` would not compile — numeric
/// params get `0L`/`0`, booleans `false`. Explicit DEFAULT literals are
/// rendered for the effective type (`DEFAULT 0` → `0L` on `long`,
/// `DEFAULT ''` → `""` on `String`). Issue #136-A/G.
fn default_padding_for_param(param: &crate::types::Parameter) -> String {
    if param.is_out() {
        return "new java.util.concurrent.atomic.AtomicReference<>(null)".into();
    }
    let null_default = param.default_value.as_ref().is_some_and(|d| d.trim().eq_ignore_ascii_case("null"));
    let eff = if null_default {
        param.java_type.as_str()
    } else {
        crate::generate::service::boxed_to_primitive(&param.java_type)
    };
    if let Some(dv) = param.default_value.as_deref().filter(|d| !d.trim().eq_ignore_ascii_case("null")) {
        let d = dv.trim();
        if d.starts_with('\'') {
            let inner = d.trim_matches('\'').replace("''", "'");
            return format!("\"{}\"", inner.replace('\\', "\\\\").replace('"', "\\\""));
        }
        if eff == "String" {
            return format!("\"{}\"", d);
        }
        if eff == "long" {
            return if d.chars().all(|c| c.is_ascii_digit() || c == '-') { format!("{}L", d) } else { "0L".into() };
        }
        if eff == "int" {
            return if d.chars().all(|c| c.is_ascii_digit() || c == '-') { d.to_string() } else { "0".into() };
        }
        if eff == "double" {
            return format!("{}d", d);
        }
        if eff == "float" {
            return format!("{}f", d);
        }
        if eff.contains("BigDecimal") {
            return format!("new java.math.BigDecimal(\"{}\")", d);
        }
        if eff == "boolean" {
            return if d.eq_ignore_ascii_case("true") { "true".into() } else { "false".into() };
        }
        return "null".into();
    }
    match eff {
        "long" => "0L".into(),
        "int" => "0".into(),
        "boolean" => "false".into(),
        "double" => "0.0".into(),
        "float" => "0.0f".into(),
        _ => "null".into(),
    }
}

/// `.get()`-unwrap a call argument that is the caller's own OUT/IN-OUT param
/// or an already-promoted OUT local (both are `AtomicReference<T>` on this
/// side). Issue #136-C: passing such an arg to a callee's plain IN parameter
/// must dereference it — the bare `AtomicReference` does not compile against
/// `String`/`Long`/etc.
fn unwrap_out_arg(a: &str, proc: &ProcedureInfo) -> String {
    if is_out_param(a, proc)
        || proc
            .out_local_vars
            .iter()
            .any(|(k, _)| k.to_lowercase().replace("_", "") == a.to_lowercase().replace("_", ""))
    {
        format!("{}.get()", a)
    } else {
        a.to_string()
    }
}

/// Render a cross-package service call: IN arguments are coerced to the callee's
/// declared param type (mirrors the is_self_call branch); OUT/IN-OUT arguments are
/// either passed through unchanged (already `AtomicReference<T>` on this side — the
/// caller's own OUT param, or a local var already promoted) or, if they're a bare
/// local variable declared with a plain type (e.g. `String vName = null;`), queued
/// for promotion to `AtomicReference<T>` via `queue_out_promotion` (see below) so the
/// callee's `AtomicReference<T>` signature type-checks. `target_params` is `None`
/// when the callee's params couldn't be resolved, which falls back to `.get()`-only
/// unwrapping (no coercion, no promotion — can't know the callee's OUT-ness).
fn emit_cross_pkg_call(
    cross_pkg_svc: &str,
    method: &str,
    jargs: Vec<String>,
    target_params: Option<&[crate::types::Parameter]>,
    proc: &ProcedureInfo,
) -> String {
    let unwrap_if_out = |a: &str| unwrap_out_arg(a, proc);

    // Handle an OUT/IN-OUT argument of the callee. Already-AtomicReference args
    // (caller's own OUT param, or already-promoted local) pass through unchanged.
    // A bare local variable (no `.`, no `(`) that isn't yet an AtomicReference gets
    // queued for promotion (see `queue_out_promotion`) and is passed by reference
    // as-is — no `.get()`, since after promotion it *is* an AtomicReference.
    // Anything else (expressions, Map access, etc.) is left untouched — we can't
    // safely rewrite it here.
    let promote_or_pass_through = |a: &str, java_type: &str| -> String {
        // Already-`AtomicReference` args (caller's own OUT param or promoted
        // local) pass through — UNLESS the wrapper's generic argument differs
        // from the callee slot's (e.g. caller `AtomicReference<Long>` into a
        // callee `AtomicReference<String>` param). Rewrap with the dereferenced
        // value coerced through the standard matrix (issue #136 batch-4 F3).
        let caller_inner: Option<String> = if is_out_param(a, proc) {
            let bn = a.split('.').next().unwrap_or(a).to_lowercase().replace("_", "");
            proc.parameters
                .iter()
                .find(|p| p.is_out() && p.name.to_lowercase().replace("_", "") == bn)
                .map(|p| p.java_type.clone())
        } else {
            proc.out_local_vars
                .iter()
                .find(|(k, _)| k.to_lowercase().replace("_", "") == a.to_lowercase().replace("_", ""))
                .map(|(_, t)| t.clone())
        };
        if let Some(inner) = caller_inner {
            if inner != java_type {
                let coerced = coerce_arg_to_type(&format!("{}.get()", a), java_type, proc);
                return format!("new java.util.concurrent.atomic.AtomicReference<>({})", coerced);
            }
            return a.to_string();
        }
        let trimmed = a.trim();
        if !trimmed.contains('.') && !trimmed.contains('(') {
            if let Some(vname) = proc.local_vars.keys().find(|v| snake_to_camel(v) == trimmed) {
                queue_out_promotion(vname.to_lowercase(), java_type.to_string());
            }
        }
        a.to_string()
    };

    let mut x_args: Vec<String> = Vec::new();
    if let Some(params) = target_params {
        // Iterate the callee's declared params positionally: jargs[i]
        // corresponds to params[i]. Params dropped from the Java signature
        // (refcursor OUT) skip BOTH the param and its call-site argument.
        for (i, param) in params.iter().enumerate() {
            if param.dropped_from_signature() {
                continue;
            }
            match jargs.get(i) {
                Some(a) if param.is_out() => x_args.push(promote_or_pass_through(a, &param.java_type)),
                // Issue #136-C: an IN parameter still needs the caller-side OUT
                // wrapper (`AtomicReference<T>`) dereferenced before coercion —
                // `coerce_arg_to_type` alone passes `pCode` through unchanged.
                Some(a) => x_args.push(coerce_arg_to_type(&unwrap_if_out(a), &param.java_type, proc)),
                None => x_args.push(default_padding_for_param(param)),
            }
        }
        // Surplus call-site args beyond the callee's param list cannot map to
        // a parameter — pass them through unwrapped (pre-#136 behaviour for
        // unresolved arity; selection upstream should prevent this).
        for a in jargs.iter().skip(params.len()) {
            x_args.push(unwrap_if_out(a));
        }
    } else {
        x_args = jargs.iter().map(|a| unwrap_if_out(a)).collect();
    }

    format!("{}.{}({})", cross_pkg_svc, method, x_args.join(", "))
}

fn might_be_long(expr: &str, proc: &ProcedureInfo) -> bool {
    let stripped = expr.trim();
    for (var_name, var_type) in &proc.local_vars {
        if crate::naming::snake_to_camel(var_name) == stripped {
            return var_type == "Long" || var_type == "long";
        }
    }
    if stripped.contains(" + ") || stripped.contains(" - ") || stripped.contains(" * ") || stripped.contains(" / ") {
        for (var_name, var_type) in &proc.local_vars {
            let camel = crate::naming::snake_to_camel(var_name);
            if stripped.contains(&camel) && (var_type == "Long" || var_type == "long") {
                return true;
            }
        }
    }
    false
}

pub(crate) fn get_column_ref_name(expr: &ogsql_parser::ast::Expr) -> Option<String> {
    use ogsql_parser::ast::Expr;
    match expr {
        Expr::ColumnRef(name) | Expr::PlVariable(name) => Some(name.join(".")),
        _ => None,
    }
}

fn expr_to_java_impl(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    use ogsql_parser::ast::Expr;
    match expr {
        Expr::Literal(lit) => literal_to_java(lit),
        Expr::ColumnRef(name) => resolve_column_ref(&name.join("."), proc),
        Expr::PlVariable(name) => resolve_column_ref(&name.join("."), proc),
        Expr::BinaryOp { left, op, right } => binary_op_to_java(left, op, right, proc),
        Expr::UnaryOp { op, expr } => unary_op_to_java(op, expr, proc),
        Expr::FunctionCall { name, args, .. } => function_call_to_java(&name.join("."), args, proc),
        Expr::SpecialFunction { name, args, .. } => special_function_to_java(name, args, proc),
        Expr::IsNull { expr, negated } => {
            let inner = expr_to_java(expr, proc);
            // A primitive-typed parameter can never be null — `prim == null`
            // does not compile, so fold at generation time (postprocess
            // migration: primitive == null -> false, != null -> true).
            if is_primitive_emitted_param(&inner, proc) {
                return if *negated { "true".to_string() } else { "false".to_string() };
            }
            if *negated {
                format!("{} != null", inner)
            } else {
                format!("{} == null", inner)
            }
        }
        Expr::IsBoolean { expr, value, negated } => {
            let inner = expr_to_java(expr, proc);
            match (*value, *negated) {
                (true, false) => format!("Boolean.TRUE.equals({})", inner),
                (true, true) => format!("!Boolean.TRUE.equals({})", inner),
                (false, false) => format!("Boolean.FALSE.equals({})", inner),
                (false, true) => format!("!Boolean.FALSE.equals({})", inner),
            }
        }
        Expr::InList { expr, list, negated } => {
            let left = expr_to_java(expr, proc);
            let items: Vec<String> = list.iter().map(|e| expr_to_java(e, proc)).collect();
            let arr = format!("Arrays.asList({})", items.join(", "));
            if *negated {
                format!("!{}.contains({})", arr, left)
            } else {
                format!("{}.contains({})", arr, left)
            }
        }
        Expr::Like { expr, pattern, negated, .. } => like_to_java(expr, pattern, *negated, proc),
        Expr::TypeCast { expr, type_name, .. } => type_cast_to_java(expr, &format!("{:?}", type_name), proc),
        Expr::Case { operand, whens, else_expr } => case_to_java(operand, whens, else_expr, proc),
        Expr::Parenthesized(inner) => format!("({})", expr_to_java(inner, proc)),
        Expr::Between { expr, low, high, negated } => {
            let _e = expr_to_java(expr, proc);
            let _lo = expr_to_java(low, proc);
            let _hi = expr_to_java(high, proc);
            let ge = binary_op_to_java(expr, ">=", low, proc);
            let le = binary_op_to_java(expr, "<=", high, proc);
            if *negated {
                format!("!({} && {})", ge, le)
            } else {
                format!("({} && {})", ge, le)
            }
        }
        Expr::Exists(_) => "/* EXISTS */ true".into(),
        Expr::Subquery(_) => "null".into(),
        Expr::QualifiedStar(_) => "null".into(),
        Expr::Parameter(_n) => "null".to_string(),
        Expr::MyBatisParam(p) => format!("#{{{}}}", p),
        Expr::MyBatisRawExpr(e) => format!("${{{}}}", e),
        Expr::Array(items) => {
            let java_items: Vec<String> = items.iter().map(|e| expr_to_java(e, proc)).collect();
            format!("new Object[]{{{}}}", java_items.join(", "))
        }
        Expr::Subscript { object, lower, upper, is_slice } => {
            if *is_slice {
                // Array slice: obj[lower:upper] — not common in PL/pgSQL→Java
                let lo = lower.as_ref().map(|e| expr_to_java(e, proc)).unwrap_or_else(|| "0".into());
                let hi = upper.as_ref().map(|e| expr_to_java(e, proc)).unwrap_or_else(|| "obj.size()".into());
                format!("{}.subList(({}) - 1, {})", expr_to_java(object, proc), lo, hi)
            } else {
                // Single index: obj[i] → obj.get(i). Record/composite shim
                // list fields are PL/pgSQL 1-based arrays → get(i - 1).
                let idx =
                    lower.as_ref().or(upper.as_ref()).map(|e| expr_to_java(e, proc)).unwrap_or_else(|| "0".into());
                let obj_java = expr_to_java(object, proc);
                // Issue #136 batch-4 F4: `null.get(i)` does not compile —
                // degrade unresolved/null receivers to a compile-safe marker.
                if obj_java.trim() == "null" {
                    return "/* TOBEFIX: subscript on unresolved/null receiver */ null".into();
                }
                if record_list_field_access(object, proc) {
                    format!("{}.get((int) (({}) - 1))", obj_java, idx)
                } else {
                    format!("{}.get({})", obj_java, idx)
                }
            }
        }
        Expr::FieldAccess { object, field } => {
            let mut object_java = expr_to_java(object, proc);
            // Issue #136 batch-4 F4: `null.get("f")` does not compile.
            if object_java.trim() == "null" {
                return "/* TOBEFIX: field access on unresolved/null receiver */ null".into();
            }
            // Review #129 §4.3: `coll(i).field` reads must be gated on the
            // collection's element type — scalar elements (List<Long> etc.)
            // have no fields and `.getOrDefault` on them is a javac error;
            // Object/? elements need an explicit Map cast to compile.
            match coll_field_access_elem(object, proc) {
                Some(CollElem::Scalar) => {
                    return "/* TOBEFIX: coll(i).field — collection element type is not a record map */ null".into();
                }
                Some(CollElem::Unknown) => {
                    object_java = format!("((java.util.Map<String, Object>) ({}))", object_java);
                }
                _ => {}
            }
            let camel = snake_to_camel(field);
            let raw = if camel != field.to_lowercase() {
                format!(
                    "{}.getOrDefault(\"{}\", {}.get(\"{}\"))",
                    object_java,
                    camel,
                    object_java,
                    field.to_lowercase()
                )
            } else {
                format!("{}.get(\"{}\")", object_java, camel)
            };
            // composite_type_shims: array-typed record fields hold a
            // java.util.List inside the map — cast so indexOf/subscript/
            // size compile against the typed list.
            let base_var = match object.as_ref() {
                Expr::PlVariable(n) | Expr::ColumnRef(n) => Some(n.join(".")),
                _ => None,
            };
            if let Some(list_ty) =
                base_var.as_deref().and_then(|b| record_field_java_type(proc, b, field)).filter(|t| t.contains("List<"))
            {
                return format!("(({}) {})", list_ty, raw);
            }
            raw
        }
        Expr::CursorAttribute { cursor, attribute } => {
            let cursor_name_raw = match cursor.as_ref() {
                Expr::ColumnRef(name) | Expr::PlVariable(name) => name.join("."),
                _ => "cursor".into(),
            };
            let cursor_name = crate::naming::snake_to_camel(&cursor_name_raw);
            // Check if the cursor variable is an OUT param (AtomicReference)
            let is_out = proc.parameters.iter().any(|p| p.name == cursor_name_raw && p.is_out())
                || proc.out_local_vars.iter().any(|(n, _)| *n == cursor_name_raw);
            match attribute {
                ogsql_parser::ast::CursorAttributeKind::RowCount => "__ROWCOUNT__".into(),
                ogsql_parser::ast::CursorAttributeKind::Found => "found".into(),
                ogsql_parser::ast::CursorAttributeKind::NotFound => "!found".into(),
                ogsql_parser::ast::CursorAttributeKind::IsOpen => {
                    if is_out {
                        format!("{}.get() != null", cursor_name)
                    } else {
                        format!("{} != null", cursor_name)
                    }
                }
                ogsql_parser::ast::CursorAttributeKind::BulkExceptions => "java.util.Collections.emptyList()".into(),
            }
        }
        Expr::Default => "null".into(),
        Expr::Prior(_) => "null".into(),
        Expr::SysDate => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
        Expr::AtTimeZone { expr, zone } => {
            // `expr AT TIME ZONE zone` → rezoned Timestamp (downstream contexts
            // like DATE_TRUNC call .toInstant() on the result)
            let inner = expr_to_java(expr, proc);
            let zone_java = expr_to_java(zone, proc);
            // (#114 review minor 4): java.sql.Date.toInstant() throws
            // UnsupportedOperationException — wrap Date-typed inputs in a
            // Timestamp first (mirrors the DATE_TRUNC guard).
            let inner_ts = {
                let a = inner.trim();
                if a.starts_with("new java.sql.Date")
                    || a.starts_with("java.sql.Date.valueOf")
                    || resolve_var_java_type(a, proc)
                        .map(|t| t.contains("java.sql.Date") && !t.contains("Timestamp"))
                        .unwrap_or(false)
                {
                    format!("new java.sql.Timestamp({}.getTime())", a)
                } else {
                    inner.clone()
                }
            };
            format!(
                "java.sql.Timestamp.valueOf({}.toInstant().atZone(java.time.ZoneId.of({})).toLocalDateTime())",
                inner_ts, zone_java
            )
        }
        _ => "null".into(),
    }
}

fn java_string_literal(s: &str) -> String {
    let escaped =
        s.replace('\\', "\\\\").replace('"', "\\\"").replace('\n', "\\n").replace('\r', "\\r").replace('\t', "\\t");
    format!("\"{}\"", escaped)
}

fn literal_to_java(lit: &ogsql_parser::ast::Literal) -> String {
    use ogsql_parser::ast::Literal;
    match lit {
        Literal::Integer(n) => n.to_string(),
        Literal::Float(f) => f.clone(),
        Literal::String(s) => java_string_literal(s),
        Literal::EscapeString(s) => java_string_literal(s),
        Literal::BitString(_) => "null".into(),
        Literal::HexString(_) => "null".into(),
        Literal::NationalString(n) => java_string_literal(n),
        Literal::DollarString { body, .. } => java_string_literal(body),
        Literal::Boolean(b) => {
            if *b {
                "true".into()
            } else {
                "false".into()
            }
        }
        Literal::Null => "null".into(),
    }
}

/// Element-kind classification for `coll(i).field` reads (review #129 §4.3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CollElem {
    /// Element type contains Map — `.get/.getOrDefault` compile directly.
    Map,
    /// Element type is Object/?/unresolved — needs an explicit Map cast.
    Unknown,
    /// Concrete scalar element (Long, String, …) — field access is invalid.
    Scalar,
}

/// If `object` is a single-argument collection element access `coll(i)` on a
/// `List<Elem>`-typed variable/param/package-var, classify `Elem`. Returns
/// `None` for non-collection objects (records, plain vars, nested exprs).
fn coll_field_access_elem(object: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> Option<CollElem> {
    use ogsql_parser::ast::Expr;
    let Expr::FunctionCall { name, args, .. } = object else { return None };
    if args.len() != 1 {
        return None;
    }
    let base = name.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(".");
    let base_lower = base.to_lowercase();
    let jt = proc
        .local_vars
        .get(&base_lower)
        .map(|s| s.as_str())
        .or_else(|| proc.parameters.iter().find(|p| p.name.eq_ignore_ascii_case(&base)).map(|p| p.java_type.as_str()))
        .or_else(|| proc.package_vars.get(&base_lower).map(|v| v.java_type.as_str()))
        .or_else(|| proc.out_local_vars.get(&base_lower).map(|s| s.as_str()))?;
    let t = jt.trim();
    let start = t.find('<')?;
    let end = t.rfind('>')?;
    if !t[..start].to_lowercase().contains("list") || end <= start {
        return None;
    }
    let elem = t[start + 1..end].trim();
    Some(if elem.contains("Map") {
        CollElem::Map
    } else if elem == "Object" || elem == "?" || elem.is_empty() {
        CollElem::Unknown
    } else {
        CollElem::Scalar
    })
}

fn resolve_column_ref(name: &str, proc: &ProcedureInfo) -> String {
    let lower = name.to_lowercase();
    match lower.as_str() {
        "found" => "found".into(),
        "sqlerrm" => "__SQLERRM__".into(),
        "sqlcode" => "__SQLCODE__".into(),
        "sqlstate" => "__SQLSTATE__".into(),
        "rowcount" => "__ROWCOUNT__".into(),
        "true" => "true".into(),
        "false" => "false".into(),
        "null" => "null".into(),
        "sysdate" | "current_timestamp" | "systimestamp" | "localtimestamp" => {
            "new java.sql.Timestamp(System.currentTimeMillis())".into()
        }
        "current_date" => "new java.sql.Date(System.currentTimeMillis())".into(),
        _ => {
            if name.contains('.') {
                let parts: Vec<&str> = name.splitn(2, '.').collect();
                // Check if this is a same-package variable reference (e.g. PKG_NAME.var_name)
                if parts.len() == 2 {
                    let pkg_candidate = parts[0];
                    let field_candidate = parts[1];
                    let pkg_normalized = pkg_candidate.to_lowercase().replace("_", "");
                    let proc_pkg_normalized = proc.package.to_lowercase().replace("_", "");
                    if pkg_normalized == proc_pkg_normalized {
                        let field_lower = field_candidate.to_lowercase();
                        let field_lower_no_underscore = field_lower.replace("_", "");
                        let matched = proc.package_vars.iter().find(|(k, _)| {
                            let k_lower = k.to_lowercase();
                            k_lower == field_lower || k_lower.replace("_", "") == field_lower_no_underscore
                        });
                        if let Some((_, _vi)) = matched {
                            let field_camel = crate::naming::snake_to_camel(field_candidate);
                            return format!("this.{}", field_camel);
                        }
                    }
                }
                let var_name = snake_to_camel(parts[0]);
                let field = parts[1].to_lowercase();
                let snake_base = parts[0];
                let is_out = proc.parameters.iter().any(|p| p.name == parts[0] && p.is_out());
                let base_type = proc
                    .local_vars
                    .get(&snake_base.to_lowercase())
                    .map(|s| s.as_str())
                    .or_else(|| {
                        proc.parameters
                            .iter()
                            .find(|p| p.name.eq_ignore_ascii_case(snake_base))
                            .map(|p| p.java_type.as_str())
                    })
                    .or_else(|| proc.package_vars.get(&snake_base.to_lowercase()).map(|v| v.java_type.as_str()))
                    .unwrap_or("");
                // Issue #136 batch-4 F4: a dotted reference whose base is not
                // any declared var/param/package-var would emit an undeclared
                // Java identifier (`base.getOrDefault(...)`). Cross-package
                // package-var accessors do not exist — degrade to a
                // compile-safe TOBEFIX marker instead.
                // Before degrading, check the record-field registries and the
                // shared package-var registry: `pkg_shared.counter` has an
                // empty base_type (the base is a package qualifier, not a
                // var) but the FIELD resolves in package_vars — that shape
                // must keep the legacy getOrDefault form (issue #38).
                let base_resolvable = !base_type.is_empty()
                    || proc.rowtype_field_types.contains_key(&snake_base.to_lowercase())
                    || proc.param_rowtype_fields.contains_key(&snake_base.to_lowercase())
                    || proc.package_vars.contains_key(&parts[1].to_lowercase())
                    || proc.package_vars.keys().any(|k| k.eq_ignore_ascii_case(parts[1]));
                if !base_resolvable {
                    // Issue #136 corpus: `known_pkg.var` where the base is
                    // another package's name and the field is its package-level
                    // variable → `<pkg>Service.<var>`.
                    if let Some(access) = cross_pkg_var_access(proc, parts[0], parts[1]) {
                        return access;
                    }
                    return format!(
                        "/* TOBEFIX: unresolved ref {}.{} - caller={}:{} */ null",
                        flatten_comment(parts[0]),
                        flatten_comment(parts[1]),
                        flatten_comment(&proc.source_file),
                        flatten_comment(&proc.proc_name)
                    );
                }
                if field == "count" {
                    return format!("((java.util.List<?>) {}).size()", var_name);
                }
                // Issue #124: collection pseudo-attributes FIRST/LAST return
                // the boundary index (1-based); NULL on empty collections.
                if base_type.contains("List") && (field == "first" || field == "last") {
                    let lb = if is_out { format!("{}.get()", var_name) } else { var_name.clone() };
                    return if field == "first" {
                        format!("(({}).isEmpty() ? null : java.lang.Long.valueOf(1))", lb)
                    } else {
                        format!("(({}).isEmpty() ? null : java.lang.Long.valueOf(({}).size()))", lb, lb)
                    };
                }
                let field_camel = snake_to_camel(parts[1]);
                let map_base = if is_out {
                    format!("{}.get()", var_name)
                } else if base_type.contains("Map") {
                    var_name.clone()
                } else if base_type == "Object" || base_type.is_empty() {
                    format!("((java.util.Map<String, Object>) {})", var_name)
                } else {
                    var_name.clone()
                };
                if field_camel != parts[1] {
                    let raw =
                        format!("{}.getOrDefault(\"{}\", {}.get(\"{}\"))", map_base, field_camel, map_base, parts[1]);
                    typed_rowtype_field(raw, snake_base, parts[1], proc)
                } else {
                    let raw = format!("{}.get(\"{}\")", map_base, field_camel);
                    typed_rowtype_field(raw, snake_base, parts[1], proc)
                }
            } else {
                let camel = snake_to_camel(name);
                let is_out = proc.parameters.iter().any(|p| p.name == name && p.is_out() && !p.is_refcursor());
                if is_out {
                    return format!("{}.get()", camel);
                }
                let name_lower = name.to_lowercase().replace("_", "");
                let param_match = proc.parameters.iter().find(|p| p.name.to_lowercase().replace("_", "") == name_lower);
                if let Some(p) = param_match {
                    // Issue #136-B: refcursor OUT params are promoted to a List
                    // return and removed from the signature — a read degrades
                    // to `null` (the cursor is never a readable value anyway).
                    if p.is_refcursor() {
                        return "null".to_string();
                    }
                    let param_camel = snake_to_camel(&p.name);
                    if p.is_out() {
                        return format!("{}.get()", param_camel);
                    }
                    return param_camel;
                }
                // Local variable / package variable declarations always shadow a same-named
                // function (PL/SQL scoping semantics): if the bare name is a declared
                // variable, never divert into function resolution below — no name-prefix
                // heuristic (func_/fn_) is consulted anywhere in this branch (Task 5).
                let name_key = name.to_lowercase().replace('_', "");
                let is_declared_var = proc.local_vars.contains_key(&name.to_lowercase())
                    || proc.local_vars.keys().any(|k| k.to_lowercase().replace('_', "") == name_key)
                    || proc.package_vars.keys().any(|k| k.to_lowercase().replace('_', "") == name_key);
                if !is_declared_var {
                    // Issue #136 corpus: a bare identifier naming a known
                    // package whose package vars include a same-named var
                    // (`pkg_inst_crossday_deal` shape) → `<pkg>Service.<var>`.
                    if let Some(access) = cross_pkg_self_var_access(proc, name) {
                        return access;
                    }
                    // 函数注册表判定（无前缀启发式，Task 5）：同包零参兄弟 → this.fn()；
                    // 跨包零参且候选唯一 → svcVar.fn()；否则 TOBEFIX + null（Task 6）
                    // （不区分"注册表未命中"与"命中但歧义"——两者都不是可安全生成的函数调用）。
                    let method_name = crate::naming::java_method_name(name);
                    if proc
                        .package_proc_params
                        .get(&method_name)
                        .is_some_and(|overloads| overloads.iter().any(|ps| ps.is_empty()))
                    {
                        return format!("this.{}()", method_name);
                    }
                    // Check if it exists in another package (cross-package call)
                    if let Some(candidates) = proc.all_proc_params.get(&method_name) {
                        let proc_pkg_lower = proc.package.to_lowercase();
                        let cross: Vec<&GlobalFnEntry> = candidates
                            .iter()
                            .filter(|e| e.params.is_empty() && e.package.to_lowercase() != proc_pkg_lower)
                            .collect();
                        if cross.len() == 1 {
                            return format!("{}.{}()", cross[0].svc_var, method_name);
                        }
                    }
                    // 裸名既不是参数/局部变量/包变量，也不是任何已知函数 → TOBEFIX + null（编译安全）
                    return format!(
                        "/* TOBEFIX: unresolved name {}(no args) - pkg=?, caller={}:{} */ null",
                        flatten_comment(name),
                        flatten_comment(&proc.source_file),
                        flatten_comment(&proc.proc_name)
                    );
                }
                camel
            }
        }
    }
}

pub(crate) fn is_bigdecimal_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let mut base = name.split(['.', '(']).next().unwrap_or(name);
    // Parenthesised receivers like `(vAmt).negate()` yield an empty base —
    // strip the paren and re-split so the var lookup still fires.
    if base.is_empty() {
        base = name.trim_start_matches('(').split(['.', ')', '(']).next().unwrap_or(name);
    }
    // Direct lookup (snake_case key)
    if let Some(ty) = proc.local_vars.get(&base.to_lowercase()) {
        return ty.contains("BigDecimal");
    }
    // Reverse lookup: local_vars keys are snake_case, but expr may produce camelCase
    let base_lower = base.to_lowercase().replace("_", "");
    for (var_name, var_type) in &proc.local_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower {
            return var_type.contains("BigDecimal");
        }
    }
    for p in &proc.parameters {
        let p_lower = p.name.to_lowercase().replace("_", "");
        if p_lower == base_lower || crate::naming::snake_to_camel(&p.name) == base {
            return p.java_type.contains("BigDecimal");
        }
    }
    // Check if expression is a .get() call on a Map variable whose field type is BigDecimal
    if let Some(dot_get_pos) = name.find(".get(") {
        let var_part = &name[..dot_get_pos];
        // Try the variable part against custom_types fields
        let var_lower = var_part.to_lowercase();
        for custom_type in proc.custom_types.values() {
            for (field_name, field_type) in &custom_type.fields {
                if field_name.to_lowercase() == var_lower {
                    return field_type.contains("BigDecimal");
                }
            }
        }
    }
    if name.starts_with("Math.") {
        return false;
    }
    // double arithmetic that merely nests BigDecimal.doubleValue() args
    if name.contains("Math.")
        && name.chars().next().map(|c| c.is_ascii_digit() || c == '(' || c == '-').unwrap_or(false)
        && !name.starts_with("java.math.BigDecimal")
        && !name.starts_with("new java.math.BigDecimal")
    {
        return false;
    }
    if name.starts_with("new java.math.BigDecimal") || name.starts_with("java.math.BigDecimal.valueOf") {
        return true;
    }
    // Check if expression already contains BigDecimal method calls (intermediate result)
    if name.contains(".multiply(")
        || name.contains(".add(")
        || name.contains(".subtract(")
        || name.contains(".divide(")
        || (name.contains(".abs(") && !name.contains("Math.abs("))
        || name.contains(".setScale(")
    {
        return true;
    }
    if let Some(vi) = proc.package_vars.get(base) {
        return vi.java_type.contains("BigDecimal");
    }
    let base_lower_for_pkg = base.to_lowercase().replace("_", "");
    for (var_name, var_info) in &proc.package_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower_for_pkg || crate::naming::snake_to_camel(var_name) == base {
            return var_info.java_type.contains("BigDecimal");
        }
    }
    false
}

fn wrap_bigdecimal(expr: &str, already_bd: bool, proc: &ProcedureInfo) -> String {
    if already_bd {
        return expr.to_string();
    }
    let trimmed = expr.trim();
    if is_nullish_java_expr(trimmed) {
        return "java.math.BigDecimal.ZERO".to_string();
    }
    if trimmed.starts_with("new java.math.BigDecimal") {
        return trimmed.to_string();
    }
    if looks_bd_expr(trimmed) {
        return trimmed.to_string();
    }
    if call_expr_return_type(trimmed, proc).is_some_and(|t| t.contains("BigDecimal")) {
        return trimmed.to_string();
    }
    // Unwrap AtomicReference vars promoted for OUT arg usage (type-aware:
    // String-typed refs hold numeric text and need Long.parseLong, not .longValue()).
    let trimmed_lower = trimmed.to_lowercase();
    let ar_type = proc
        .out_local_vars
        .iter()
        .find(|(k, _)| k.to_lowercase().replace("_", "") == trimmed_lower.replace("_", ""))
        .map(|(_, v)| v.clone())
        .or_else(|| {
            proc.parameters
                .iter()
                .find(|p| p.is_out() && p.name.to_lowercase().replace("_", "") == trimmed_lower.replace("_", ""))
                .map(|p| p.java_type.clone())
        });
    if let Some(t) = ar_type {
        if t.contains("String") {
            return format!(
                "java.math.BigDecimal.valueOf({}.get() != null ? Long.parseLong({}.get()) : 0L)",
                trimmed, trimmed
            );
        }
        return format!("java.math.BigDecimal.valueOf({}.get().longValue())", trimmed);
    }
    if trimmed.starts_with("Math.") {
        return format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed));
    }
    if trimmed.contains(".get(") {
        // For arithmetic expressions, BigDecimal.valueOf() with the raw expression works
        // because the result is primitive long/double. The (Number) cast + .doubleValue()
        // pattern would bind the cast to the first operand only (Java precedence).
        if trimmed.contains(" * ") || trimmed.contains(" + ") || trimmed.contains(" - ") || trimmed.contains(" / ") {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        } else {
            // String-typed AtomicReference deref: `(Number) str` is an inconvertible cast —
            // parse the numeric text instead.
            let base = trimmed.trim_end_matches(".get()").trim();
            let base_is_string_ar = proc.out_local_vars.iter().any(|(k, v)| {
                k.to_lowercase().replace("_", "") == base.to_lowercase().replace("_", "") && v.contains("String")
            }) || proc.parameters.iter().any(|p| {
                p.is_out()
                    && p.name.to_lowercase().replace("_", "") == base.to_lowercase().replace("_", "")
                    && p.java_type.contains("String")
            });
            if base_is_string_ar {
                return format!(
                    "java.math.BigDecimal.valueOf({}.get() != null ? Long.parseLong({}.get()) : 0L)",
                    base, base
                );
            }
            format!("java.math.BigDecimal.valueOf(((Number) {}).doubleValue())", trimmed)
        }
    } else if trimmed.chars().all(|c| c.is_ascii_digit() || c == '.') && !trimmed.is_empty() {
        format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
    } else {
        if trimmed.contains("BigDecimal") {
            trimmed.to_string()
        } else {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
    }
}

/// to_char(sysdate, f) rendered as `SimpleDateFormat(f).format(<date>)` is a String;
/// when the context needs a Timestamp, the whole expression is equivalent to
/// `<date>` itself (date arg) or must be re-parsed (string arg).
pub(crate) fn simplify_ts_expr(expr: &str) -> Option<String> {
    let mut s = expr.trim().to_string();
    if s.starts_with("((java.sql.Timestamp)") && s.ends_with(')') {
        let inner = &s["((java.sql.Timestamp)".len()..s.len() - 1];
        if inner.contains("SimpleDateFormat") {
            s = inner.trim().to_string();
        }
    }
    if !s.starts_with("new java.text.SimpleDateFormat(") {
        return None;
    }
    let pos = s.find(").format(")?;
    let after = &s[pos + ").format(".len()..];
    let arg = after.strip_suffix(')').unwrap_or(after).trim().to_string();
    if arg.is_empty() {
        return None;
    }
    let arg_is_date = arg.starts_with("new java.sql.Timestamp")
        || arg.starts_with("java.sql.Timestamp")
        || arg.starts_with("new java.util.Date")
        || arg.starts_with("java.sql.Date");
    if arg_is_date {
        Some(arg)
    } else {
        Some(format!("java.sql.Timestamp.valueOf(String.valueOf({}))", arg))
    }
}

fn is_string_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(['.', '(']).next().unwrap_or(name);
    if let Some(ty) = proc.local_vars.get(&base.to_lowercase()) {
        return ty == "String";
    }
    let base_lower = base.to_lowercase().replace("_", "");
    for (var_name, var_type) in &proc.local_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower {
            return var_type == "String";
        }
    }
    for p in &proc.parameters {
        let p_lower = p.name.to_lowercase().replace("_", "");
        if p_lower == base_lower || crate::naming::snake_to_camel(&p.name) == base {
            return p.java_type == "String";
        }
    }
    false
}

fn looks_string_expr(s: &str, proc: &ProcedureInfo) -> bool {
    let t = s.trim();
    if t.starts_with("Double.parseDouble")
        || t.starts_with("Long.parseLong")
        || t.starts_with("Integer.parseInt")
        || t.starts_with("Float.parseFloat")
        || t.starts_with("Math.")
        || t.starts_with("((Number)")
        || t.starts_with("java.math.BigDecimal")
        || t.starts_with("(double)")
        || t.starts_with("(int)")
        || t.starts_with("(long)")
        || t.ends_with(".length()")
        || t.ends_with(".intValue()")
        || t.ends_with(".longValue()")
        || t.ends_with(".doubleValue()")
        || t.contains(".indexOf(")
        || t.contains(".charAt(")
    {
        return false;
    }
    t.contains(".substring(") || t.contains(".concat(") || t.starts_with('"') || is_string_var(t, proc)
}

fn looks_bd_expr(s: &str) -> bool {
    let t = s.trim();
    if t.starts_with("Math.") || t.starts_with("Double.parseDouble") || t.starts_with("Long.parseLong") {
        return false;
    }
    t.contains("BigDecimal")
        || t.contains(".multiply(")
        || t.contains(".subtract(")
        || t.contains(".add(")
        || t.contains(".divide(")
        || t.contains(".setScale(")
        || t.contains(".abs(")
        || t.contains(".negate(")
}

/// Root cause B3 (#107 follow-up): if `expr_str` is *entirely* a single call to a
/// known cross-package service method (`<svcVar>.<method>(...)`), return that
/// method's declared Java return type. `looks_bd_expr`/`is_bigdecimal_var` only
/// recognize BigDecimal via textual markers on the call site itself (`.multiply(`,
/// a literal `"BigDecimal"`, a known local/param type) — a call expression like
/// `fooService.barFn(x)` carries none of those, even when `barFn`'s registered
/// return type is BigDecimal. Same-package `this.method(...)` calls are not
/// resolved here: `package_proc_params` (unlike `all_proc_params`) does not carry
/// return types, and adding it is out of scope for this fix.
fn call_expr_return_type(expr_str: &str, proc: &ProcedureInfo) -> Option<String> {
    let t = expr_str.trim();
    let paren_pos = t.find('(')?;
    if !t.ends_with(')') {
        return None;
    }
    // Confirm the parens starting at `paren_pos` are balanced and close exactly at
    // the end of `t` — rejects chained calls like `svc.foo(x).bar()`, where the
    // return type of the outer `.bar()` call (not `foo`) would be the one that
    // matters, and this function has no way to resolve that.
    let mut depth = 0i32;
    let mut closes_at_end = false;
    for (i, c) in t.char_indices().skip(paren_pos) {
        match c {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 {
                    closes_at_end = i == t.len() - 1;
                    break;
                }
            }
            _ => {}
        }
    }
    if !closes_at_end {
        return None;
    }
    let head = &t[..paren_pos];
    let dot_pos = head.rfind('.')?;
    let svc_var = &head[..dot_pos];
    let method = &head[dot_pos + 1..];
    if svc_var.is_empty() || method.is_empty() {
        return None;
    }
    // Issue #136-A: same-package self-calls emit `this.<fn>(...)` — resolve
    // the return type from the sibling-return table (all overloads must
    // agree, mirroring the cross-package rule below).
    if svc_var == "this" {
        let rets = proc.package_proc_returns.get(method)?;
        let first = rets.first()?.clone()?;
        return if rets.iter().all(|r| r.as_deref() == Some(first.as_str())) { Some(first) } else { None };
    }
    let candidates = proc.all_proc_params.get(method)?;
    let matches: Vec<&GlobalFnEntry> = candidates.iter().filter(|e| e.svc_var == svc_var).collect();
    let first = matches.first()?.return_type.clone()?;
    if matches.iter().all(|e| e.return_type.as_deref() == Some(first.as_str())) {
        Some(first)
    } else {
        None
    }
}

/// Normalize a package/var name for cross-registry comparison: lowercase
/// with underscores stripped (matches `pkgInstCrossdayDeal` ↔
/// `pkg_inst_crossday_deal`).
fn norm_name_key(s: &str) -> String {
    s.to_lowercase().replace('_', "")
}

/// `other_pkg.var` → `<pkg>Service.<var>` when `other_pkg` is a known
/// package (proc_summaries snapshot in `cross_pkg_vars`) and `var` is one
/// of its package-level variables. Returns None otherwise — caller falls
/// back to TOBEFIX.
fn cross_pkg_var_access(proc: &ProcedureInfo, pkg_ref: &str, field: &str) -> Option<String> {
    cross_pkg_var_info(proc, pkg_ref, field).map(|(access, _)| access)
}

/// Lookup variant returning the emitted access plus the target VarInfo —
/// assignment lowering needs `is_constant` to refuse writes to `final`
/// package vars (issue #136 corpus).
fn cross_pkg_var_info<'a>(
    proc: &'a ProcedureInfo,
    pkg_ref: &str,
    field: &str,
) -> Option<(String, &'a crate::types::VarInfo)> {
    let pkg_key = norm_name_key(pkg_ref);
    let (svc_var, vars) = proc.cross_pkg_vars.iter().find(|(k, _)| norm_name_key(k) == pkg_key).map(|(_, v)| v)?;
    let field_key = norm_name_key(field);
    let (var_name, var_info) = vars
        .iter()
        .find(|(k, _)| norm_name_key(k) == field_key)
        .or_else(|| vars.iter().find(|(k, _)| k.eq_ignore_ascii_case(field)))?;
    Some((format!("{}.{}", svc_var, snake_to_camel(var_name)), var_info))
}

/// Bare-name variant: identifier IS a known package name and that package
/// has a package variable of the same name (`pkg_inst_crossday_deal`
/// shape) → `<pkg>Service.<var>`.
fn cross_pkg_self_var_access(proc: &ProcedureInfo, name: &str) -> Option<String> {
    let key = norm_name_key(name);
    let (svc_var, vars) = proc.cross_pkg_vars.iter().find(|(k, _)| norm_name_key(k) == key).map(|(_, v)| v)?;
    let var_name =
        vars.keys().find(|k| norm_name_key(k) == key).or_else(|| vars.keys().find(|k| k.eq_ignore_ascii_case(name)))?;
    Some(format!("{}.{}", svc_var, snake_to_camel(var_name)))
}

/// Java type of `base_var.field` for record-typed variables/parameters
/// (%ROWTYPE locals, record params, composite_type_shims fields). Checks
/// locals first, then OUT/IN parameters.
pub(crate) fn record_field_java_type(proc: &ProcedureInfo, base_var: &str, field: &str) -> Option<String> {
    let bl = base_var.to_lowercase();
    proc.rowtype_field_types
        .get(&bl)
        .or_else(|| proc.param_rowtype_fields.get(&bl))
        .and_then(|ft| ft.get(&field.to_lowercase()).cloned())
}

/// True when `expr` is a `base.field` reference whose record field type is a
/// `java.util.List` (composite_type_shims parallel-list field).
fn record_list_field_access(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> bool {
    let (base, field) = match expr {
        ogsql_parser::ast::Expr::FieldAccess { object, field } => {
            let base = match object.as_ref() {
                ogsql_parser::ast::Expr::PlVariable(n) | ogsql_parser::ast::Expr::ColumnRef(n) => n.join("."),
                _ => return false,
            };
            (base, field.clone())
        }
        ogsql_parser::ast::Expr::PlVariable(n) | ogsql_parser::ast::Expr::ColumnRef(n) => {
            let joined = n.join(".");
            match joined.rsplit_once('.') {
                Some((b, f)) => (b.to_string(), f.to_string()),
                None => return false,
            }
        }
        _ => return false,
    };
    record_field_java_type(proc, &base, &field).map(|t| t.contains("List<")).unwrap_or(false)
}

/// `base.field` reference check used by the parallel-list append lowering.
fn expr_is_field_ref(expr: &ogsql_parser::ast::Expr, base: &str, field: &str) -> bool {
    record_list_field_ref(expr).map(|(b, f)| b.eq_ignore_ascii_case(base) && f.eq_ignore_ascii_case(field))
        == Some(true)
}

fn record_list_field_ref(expr: &ogsql_parser::ast::Expr) -> Option<(String, String)> {
    match expr {
        ogsql_parser::ast::Expr::FieldAccess { object, field } => {
            let base = match object.as_ref() {
                ogsql_parser::ast::Expr::PlVariable(n) | ogsql_parser::ast::Expr::ColumnRef(n) => n.join("."),
                _ => return None,
            };
            Some((base, field.clone()))
        }
        ogsql_parser::ast::Expr::PlVariable(n) | ogsql_parser::ast::Expr::ColumnRef(n) => {
            let joined = n.join(".");
            joined.rsplit_once('.').map(|(b, f)| (b.to_string(), f.to_string()))
        }
        _ => None,
    }
}

/// Flatten a left-associative `a || b || c` concat chain into operands.
fn flatten_concat<'a>(expr: &'a ogsql_parser::ast::Expr, out: &mut Vec<&'a ogsql_parser::ast::Expr>) {
    if let ogsql_parser::ast::Expr::BinaryOp { left, op, right } = expr {
        if op == "||" {
            flatten_concat(left, out);
            flatten_concat(right, out);
            return;
        }
    }
    out.push(expr);
}

/// Map-typed Java expression for a record/composite variable or OUT param
/// (`m` → `m`, OUT `m` → `(Map) m.get()`, Object-typed → cast).
fn composite_map_expr(base: &str, proc: &ProcedureInfo) -> String {
    let camel = snake_to_camel(base);
    if is_out_param(base, proc) {
        return format!("((java.util.Map<String, Object>) {}.get())", camel);
    }
    let base_type = proc.local_vars.get(&base.to_lowercase()).map(|s| s.as_str()).unwrap_or("");
    if base_type == "Object" || base_type.is_empty() {
        format!("((java.util.Map<String, Object>) {})", camel)
    } else {
        camel
    }
}

/// True for the SQL empty-array literal `'{}'`, including a `::type[]` cast.
fn is_empty_array_literal(expr: &ogsql_parser::ast::Expr) -> bool {
    use ogsql_parser::ast::{Expr, Literal};
    match expr {
        Expr::Literal(Literal::String(s)) => s.trim() == "{}",
        Expr::TypeCast { expr, .. } => is_empty_array_literal(expr),
        _ => false,
    }
}

/// Assignment to a `java.util.List` record field of a composite_type_shims
/// type. `'{}'` (empty array literal, possibly cast) re-seats an empty list;
/// `field || e1 || e2 …` appends each element to the parallel list (SQL array
/// `||` is append — appending `'{}'` adds zero elements, a no-op); anything
/// else falls back to a typed `put`.
fn composite_list_assign(
    base: &str,
    field: &str,
    list_ty: &str,
    value: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
) -> String {
    let map_expr = composite_map_expr(base, proc);
    let field_key = snake_to_camel(field);
    let mut ops: Vec<&ogsql_parser::ast::Expr> = Vec::new();
    flatten_concat(value, &mut ops);
    if ops.len() == 1 && is_empty_array_literal(ops[0]) {
        return format!("{}.put(\"{}\", new {}());", map_expr, field_key, list_arraylist_type(list_ty));
    }
    if ops.len() >= 2 && expr_is_field_ref(ops[0], base, field) {
        let arr_ty = list_arraylist_type(list_ty);
        let adds: Vec<String> = ops[1..]
            .iter()
            .filter(|op| !is_empty_array_literal(op))
            .map(|op| {
                let elem = expr_to_java(op, proc);
                format!(
                    "(({}) {}.computeIfAbsent(\"{}\", _k -> new {}())).add({});",
                    list_ty, map_expr, field_key, arr_ty, elem
                )
            })
            .collect();
        if adds.is_empty() {
            return "// no-op: append of empty array literal".to_string();
        }
        return adds.join(" ");
    }
    let val_java = expr_to_java(value, proc);
    format!("{}.put(\"{}\", {});", map_expr, field_key, val_java)
}

/// `java.util.List<X>` → `java.util.ArrayList<X>` initializer type.
fn list_arraylist_type(list_ty: &str) -> String {
    if let Some(inner) = list_ty
        .strip_prefix("java.util.List<")
        .or_else(|| list_ty.strip_prefix("List<"))
        .and_then(|s| s.strip_suffix('>'))
    {
        format!("java.util.ArrayList<{}>", inner)
    } else {
        "java.util.ArrayList<Object>".into()
    }
}

pub(crate) fn typed_rowtype_field(raw_get: String, base_var: &str, field: &str, proc: &ProcedureInfo) -> String {
    let field_type = match record_field_java_type(proc, base_var, field) {
        Some(t) => t,
        None => return raw_get,
    };
    let field_type = field_type.as_str();
    if field_type.contains("List<") {
        // composite_type_shims parallel-list field: the map slot holds a
        // java.util.List — cast the raw Object get so callers get a typed
        // List (indexOf/subscript/size compile).
        return format!("(({}) {})", field_type, raw_get);
    }
    if field_type.contains("Timestamp") {
        // Cast of null is null — no NPE, and the arithmetic timestamp-branch
        // recognizes the ((java.sql.Timestamp) ...) prefix.
        format!("((java.sql.Timestamp) {})", raw_get)
    } else if field_type == "Long" || field_type == "long" {
        format!("({} == null ? null : ((Number) {}).longValue())", raw_get, raw_get)
    } else if field_type == "Integer" || field_type == "int" {
        format!("({} == null ? null : ((Number) {}).intValue())", raw_get, raw_get)
    } else if field_type.contains("BigDecimal") {
        format!("({} == null ? null : new java.math.BigDecimal(String.valueOf({})))", raw_get, raw_get)
    } else {
        raw_get
    }
}

fn is_temporal_java_expr(t: &str, proc: &ProcedureInfo) -> bool {
    looks_like_timestamp_expr(t, proc)
        || is_timestamp_or_date_var(t, proc)
        || t.contains("((java.sql.Timestamp)")
        || t.contains("java.sql.Timestamp.valueOf(")
        || t.contains("java.sql.Date.valueOf(")
        || t.contains(".toLocalDateTime()")
}

fn temporal_to_local_date(t: &str) -> String {
    format!("new java.sql.Date(({}).getTime()).toLocalDate()", t)
}

/// Render `arg` as a `java.time.LocalDate` expression.
///
/// Timestamp/Date-typed arguments must NOT round-trip through
/// `Timestamp.toString()` + `LocalDate.parse`: `toString()` yields
/// `yyyy-MM-dd HH:mm:ss.SSS`, which matches no date-only
/// `DateTimeFormatter` pattern and threw DateTimeParseException on every
/// call (issue #128). Temporal args convert via epoch millis instead.
fn local_date_arg(arg: &str, proc: &ProcedureInfo) -> String {
    let t = arg.trim();
    if is_temporal_java_expr(t, proc) {
        temporal_to_local_date(t)
    } else {
        format!("java.time.LocalDate.parse(String.valueOf({}))", t)
    }
}

/// `local_date_arg` variant that keeps the `DateTimeFormatter` pattern for
/// string arguments (TO_DATE with an explicit format).
fn local_date_arg_fmt(arg: &str, fmt_pattern: &str, proc: &ProcedureInfo) -> String {
    let t = arg.trim();
    if is_temporal_java_expr(t, proc) {
        temporal_to_local_date(t)
    } else {
        format!(
            "java.time.LocalDate.parse(String.valueOf({}), java.time.format.DateTimeFormatter.ofPattern(\"{}\"))",
            t, fmt_pattern
        )
    }
}

fn is_timestamp_or_date_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(['.', '(']).next().unwrap_or(name);
    if let Some(ty) = proc.local_vars.get(&base.to_lowercase()) {
        return ty.contains("Timestamp") || ty.contains("java.sql.Date") || ty == "Date";
    }
    let base_lower = base.to_lowercase().replace("_", "");
    for (var_name, var_type) in &proc.local_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower {
            return var_type.contains("Timestamp") || var_type.contains("java.sql.Date") || var_type == "Date";
        }
    }
    for p in &proc.parameters {
        let p_lower = p.name.to_lowercase().replace("_", "");
        if p_lower == base_lower || crate::naming::snake_to_camel(&p.name) == base {
            return p.java_type.contains("Timestamp") || p.java_type.contains("java.sql.Date") || p.java_type == "Date";
        }
    }
    if name.contains("new java.sql.Timestamp")
        && !name.contains(" - ")
        && !name.contains(" + ")
        && !name.contains(" / ")
        && !name.contains(" * ")
    {
        return true;
    }
    if name == "new java.sql.Timestamp(System.currentTimeMillis())"
        || (name.contains("System.currentTimeMillis()")
            && !name.contains(" - ")
            && !name.contains(" + ")
            && !name.contains(".getYear()")
            && !name.contains(".getMonthValue()")
            && !name.contains(".getDayOfMonth()"))
    {
        return true;
    }
    if name.contains("new java.sql.Date")
        && !name.contains(" - ")
        && !name.contains(" + ")
        && !name.contains(".getYear()")
        && !name.contains(".getMonthValue()")
        && !name.contains(".getDayOfMonth()")
    {
        return true;
    }
    false
}

fn needs_get_unwrap(expr: &str) -> bool {
    (expr.contains(".get(") || expr.contains(".getOrDefault("))
        && !expr.contains(".longValue()")
        && !expr.contains("::longValue")
        && !expr.contains(".intValue()")
        && !expr.contains(".doubleValue()")
        && !expr.contains(".floatValue()")
}

/// Generate null-safe Number extraction from Map .get() or .getOrDefault() result
fn safe_long_value(expr: &str) -> String {
    // Parenthesize the operand: `(Number) a + 1` parses as `((Number) a) + 1`
    // (Number + int is a compile error); the cast must cover the whole expr.
    format!("java.util.Optional.ofNullable((Number) ({})).map(Number::longValue).orElse(0L)", expr)
}

/// Coerce a numeric-range FOR bound to the loop counter's Java type
/// (issue #136-E). Record/Map field reads (`.get`/`.getOrDefault`) yield
/// `Object`, which cannot appear in `counter <= bound` — route them through
/// the null-safe long extraction. A boxed `Long` counter also rejects a
/// bare int literal in `for (v = 1; ...)`, so suffix `L`.
pub(crate) fn coerce_range_for_bound(bound: &str, counter_java_type: &str) -> String {
    let b = bound.trim();
    let b = if needs_get_unwrap(b) { safe_long_value(b) } else { b.to_string() };
    if counter_java_type == "Long" {
        if let Some(digits) = int_literal_digits(&b) {
            return format!("{}L", digits);
        }
    }
    b
}

/// Unwrap an `Optional...orElse(null)` operand produced by array_position /
/// array_length into `Optional.map(<fn>).orElse(<default>)`. `map_fn` is a
/// method reference/lambda body applied to the boxed element; `default` is
/// the Java literal for the empty case (`null` preserves SQL NULL, `0L`
/// matches the arithmetic convention).
fn optional_unwrap(expr: &str, map_fn: &str, default: &str) -> Option<String> {
    let t = expr.trim();
    const SUFFIX: &str = ".orElse(null)";
    if t.starts_with("java.util.Optional") && t.ends_with(SUFFIX) {
        Some(format!("{}.map({}).orElse({})", &t[..t.len() - SUFFIX.len()], map_fn, default))
    } else {
        None
    }
}

/// An operand that already lowers to `java.util.Optional...orElse(null)`
/// (array_position / array_length) cannot be cast to Number or concatenated
/// into arithmetic — unwrap it to a primitive long with the same NULL->0
/// convention as `safe_long_value`. SQL NULL in arithmetic is an error; the
/// generated code cannot raise it, so the delivery-aligned default is 0.
fn optional_numeric_operand(expr: &str) -> Option<String> {
    optional_unwrap(expr, "Number::longValue", "0L")
}

/// Extract (value_java, unit) from an interval expression (literal or concat).
/// Unit is normalized to month/year/day/hour — the caller converts to Java.
fn interval_count_unit(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> Option<(String, String)> {
    use ogsql_parser::ast::{DataType, Expr};
    let inner = match expr {
        Expr::TypeCast { expr, type_name: DataType::Interval(_), .. } => expr.as_ref(),
        _ => return None,
    };
    interval_count_unit_inner(inner, proc)
}

/// Like `interval_count_unit` but takes the already-unwrapped interval body.
fn interval_count_unit_inner(inner: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> Option<(String, String)> {
    use ogsql_parser::ast::{Expr, Literal};
    let inner = match inner {
        Expr::Parenthesized(p) => p.as_ref(),
        other => other,
    };
    let (value, unit) = match inner {
        Expr::Literal(Literal::String(value)) => {
            let mut parts = value.split_whitespace();
            (parts.next()?.to_string(), parts.next()?.to_ascii_lowercase())
        }
        Expr::BinaryOp { left, op, right } if op == "||" => {
            let unit = match right.as_ref() {
                Expr::Literal(Literal::String(value)) => value.trim().to_ascii_lowercase(),
                _ => return None,
            };
            (expr_to_java(left, proc), unit)
        }
        _ => return None,
    };
    let unit = if unit.starts_with("month") {
        "month"
    } else if unit.starts_with("year") {
        "year"
    } else if unit.starts_with("day") {
        "day"
    } else if unit.starts_with("hour") {
        "hour"
    } else {
        return None;
    };
    Some((value, unit.to_string()))
}

/// Deref a rendered value expression if it is a bare promoted local var
/// (AtomicReference holder for an OUT arg) — the bare name is the reference,
/// not the value. Call-argument OUT positions are handled separately
/// (emit_cross_pkg_call passes the AtomicReference itself), so callers use
/// this only for value contexts: arithmetic/comparison operands, assignment
/// RHS, IF/WHILE conditions, RETURN expressions. (M5, #114 review)
pub(crate) fn maybe_deref_promoted(e: &str, proc: &ProcedureInfo) -> String {
    let t = e.trim();
    if is_bare_identifier(t) {
        let is_promoted =
            proc.out_local_vars.keys().any(|k| crate::naming::snake_to_camel(k) == t) || has_pending_out_promotion(t);
        if is_promoted {
            return format!("{}.get()", t);
        }
    }
    t.to_string()
}

fn binary_op_to_java(
    left: &ogsql_parser::ast::Expr,
    op: &str,
    right: &ogsql_parser::ast::Expr,
    proc: &ProcedureInfo,
) -> String {
    let mut l = expr_to_java(left, proc);
    let mut r = expr_to_java(right, proc);

    l = maybe_deref_promoted(&l, proc);
    r = maybe_deref_promoted(&r, proc);

    // Unwrap (Number) prefix for arithmetic contexts
    if matches!(op, "*" | "/" | "+" | "-") {
        if l.trim().starts_with("(Number)") && !l.contains(".longValue()") && !l.contains(".doubleValue()") {
            l = format!("((Number) {}).longValue()", l.trim().trim_start_matches("(Number)").trim());
        }
        if r.trim().starts_with("(Number)") && !r.contains(".longValue()") && !r.contains(".doubleValue()") {
            r = format!("((Number) {}).longValue()", r.trim().trim_start_matches("(Number)").trim());
        }
    }

    let is_arith = matches!(op, "*" | "+" | "-" | "/");
    let l_trim = l.trim();
    let r_trim = r.trim();
    let l_is_ts = is_timestamp_or_date_var(&l, proc)
        || l_trim.contains("((java.sql.Timestamp)")
        || l_trim.starts_with("java.sql.Date.valueOf(")
        || l_trim.starts_with("java.sql.Timestamp.valueOf(")
        || l_trim.starts_with("new java.sql.Date(")
        || l_trim.starts_with("new java.sql.Timestamp(");
    let r_is_ts = is_timestamp_or_date_var(&r, proc)
        || r_trim.contains("((java.sql.Timestamp)")
        || r_trim.starts_with("java.sql.Date.valueOf(")
        || r_trim.starts_with("java.sql.Timestamp.valueOf(")
        || r_trim.starts_with("new java.sql.Date(")
        || r_trim.starts_with("new java.sql.Timestamp(");
    if is_arith {
        if op == "-" && (l_is_ts || r_is_ts) {
            if l_is_ts && r_is_ts {
                return format!("(({}).getTime() - ({}).getTime()) / (24 * 60 * 60 * 1000)", l, r);
            }
            if l_is_ts && !r_is_ts {
                // #95: `ts - interval '3 month'` must use minusMonths (not
                // Long.parseLong("3 month")); day/hour units use epoch math.
                if let Some((value_java, unit)) = interval_count_unit(right, proc) {
                    let l_trimmed = l.trim();
                    let date_form =
                        l_trimmed.starts_with("new java.sql.Date") || l_trimmed.starts_with("java.sql.Date.valueOf");
                    match unit.as_str() {
                        "month" => {
                            if date_form {
                                return format!(
                                    "java.sql.Date.valueOf({}.toLocalDate().minusMonths((long)({})))",
                                    l, value_java
                                );
                            }
                            return format!(
                                "java.sql.Timestamp.valueOf({}.toLocalDateTime().minusMonths((long)({})))",
                                l, value_java
                            );
                        }
                        "year" => {
                            if date_form {
                                return format!(
                                    "java.sql.Date.valueOf({}.toLocalDate().minusYears((long)({})))",
                                    l, value_java
                                );
                            }
                            return format!(
                                "java.sql.Timestamp.valueOf({}.toLocalDateTime().minusYears((long)({})))",
                                l, value_java
                            );
                        }
                        "day" => {
                            return format!(
                                "new java.sql.Timestamp({}.getTime() - (long)({}) * 24 * 60 * 60 * 1000)",
                                l, value_java
                            );
                        }
                        _ => {
                            return format!(
                                "new java.sql.Timestamp({}.getTime() - (long)({}) * 60 * 60 * 1000)",
                                l, value_java
                            );
                        }
                    }
                }
                let r_coerced = if r.contains("concat(String.valueOf(\" days\"))") || r.contains("concat(\" days\")") {
                    let stripped =
                        r.replace(".concat(String.valueOf(\" days\"))", "").replace(".concat(\" days\")", "");
                    format!("Long.parseLong(String.valueOf({}))", stripped.trim())
                } else if r == "null" || r.contains("String.valueOf(") || is_string_var(&r, proc) {
                    format!("Long.parseLong(String.valueOf({}))", r)
                } else if is_bigdecimal_var(&r, proc) || r.contains("BigDecimal") {
                    format!("({}).longValue()", r)
                } else {
                    r.clone()
                };
                return format!(
                    "new java.sql.Timestamp({}.getTime() - (long)({}) * 24 * 60 * 60 * 1000)",
                    l, r_coerced
                );
            }
            return format!("new java.sql.Timestamp({}.getTime() - {}.getTime())", l, r);
        }
        if op == "+" && (l_is_ts || r_is_ts) {
            if l_is_ts && !r_is_ts {
                if let Some((value_java, unit)) = interval_count_unit(right, proc) {
                    let l_trimmed = l.trim();
                    let date_form =
                        l_trimmed.starts_with("new java.sql.Date") || l_trimmed.starts_with("java.sql.Date.valueOf");
                    match unit.as_str() {
                        "month" => {
                            if date_form {
                                return format!(
                                    "java.sql.Date.valueOf({}.toLocalDate().plusMonths((long)({})))",
                                    l, value_java
                                );
                            }
                            return format!(
                                "java.sql.Timestamp.valueOf({}.toLocalDateTime().plusMonths((long)({})))",
                                l, value_java
                            );
                        }
                        "year" => {
                            // (#115 review #8): align with month — a java.sql.Date
                            // operand must stay Date (Date has no toLocalDateTime).
                            if date_form {
                                return format!(
                                    "java.sql.Date.valueOf({}.toLocalDate().plusYears((long)({})))",
                                    l, value_java
                                );
                            }
                            return format!(
                                "java.sql.Timestamp.valueOf({}.toLocalDateTime().plusYears((long)({})))",
                                l, value_java
                            );
                        }
                        "day" => {
                            return format!(
                                "new java.sql.Timestamp({}.getTime() + (long)({}) * 24 * 60 * 60 * 1000)",
                                l, value_java
                            );
                        }
                        _ => {
                            return format!(
                                "new java.sql.Timestamp({}.getTime() + (long)({}) * 60 * 60 * 1000)",
                                l, value_java
                            );
                        }
                    }
                }
                let r_coerced = if r.contains("concat(String.valueOf(\" days\"))") || r.contains("concat(\" days\")") {
                    let stripped =
                        r.replace(".concat(String.valueOf(\" days\"))", "").replace(".concat(\" days\")", "");
                    format!("Long.parseLong(String.valueOf({}))", stripped.trim())
                } else if r == "null" || r.contains("String.valueOf(") || is_string_var(&r, proc) {
                    format!("Long.parseLong(String.valueOf({}))", r)
                } else if is_bigdecimal_var(&r, proc) || r.contains("BigDecimal") {
                    format!("({}).longValue()", r)
                } else {
                    r.clone()
                };
                return format!(
                    "new java.sql.Timestamp({}.getTime() + (long)({}) * 24 * 60 * 60 * 1000)",
                    l, r_coerced
                );
            }
            if !l_is_ts && r_is_ts {
                let l_coerced = if l.contains("concat(String.valueOf(\" days\"))") || l.contains("concat(\" days\")") {
                    let stripped =
                        l.replace(".concat(String.valueOf(\" days\"))", "").replace(".concat(\" days\")", "");
                    format!("Long.parseLong(String.valueOf({}))", stripped.trim())
                } else if l == "null" || l.contains("String.valueOf(") || is_string_var(&l, proc) {
                    format!("Long.parseLong(String.valueOf({}))", l)
                } else {
                    l.clone()
                };
                return format!(
                    "new java.sql.Timestamp((long)({}) * 24 * 60 * 60 * 1000 + {}.getTime())",
                    l_coerced, r
                );
            }
        }
        // TOBEFIX-marked nullish operands fold to `0` but keep their marker
        // comment — dropping it would hide the unresolved-name warning
        // (issue #136 batch-4: `/* TOBEFIX */ null + 1` used to become `0 + 1`).
        let fold_nullish = |e: &str| -> String {
            if e.contains("/* TOBEFIX") {
                if let (Some(cs), Some(ce)) = (e.find("/*"), e.find("*/")) {
                    if ce > cs {
                        return format!("{} 0", &e[cs..ce + 2]);
                    }
                }
            }
            "0".to_string()
        };
        if is_nullish_java_expr(&l) && !l_is_ts {
            l = fold_nullish(&l);
        }
        if is_nullish_java_expr(&r) && !r_is_ts {
            r = fold_nullish(&r);
        }

        let l_is_string = looks_string_expr(&l, proc);
        let r_is_string = looks_string_expr(&r, proc);
        if (l_is_string || r_is_string) && !looks_bd_expr(&l) && !looks_bd_expr(&r) {
            // Oracle/Gauss `+` on VARCHAR2 is numeric addition; concat is `||`.
            if l_is_string {
                l = format!("Double.parseDouble(String.valueOf({}))", l);
            }
            if r_is_string {
                r = format!("Double.parseDouble(String.valueOf({}))", r);
            }
        }
    }

    match op {
        "||" => {
            // TOBEFIX-marked operands must keep their marker (and null value)
            // so unresolved-name warnings stay visible; plain nullish operands
            // concat as the empty string as before.
            let l_sv = if l.contains("/* TOBEFIX") {
                format!("String.valueOf({})", l)
            } else if is_nullish_java_expr(&l) {
                "\"\"".to_string()
            } else {
                format!("String.valueOf({})", l)
            };
            let r_sv = if r.contains("/* TOBEFIX") {
                format!("String.valueOf({})", r)
            } else if is_nullish_java_expr(&r) {
                "\"\"".to_string()
            } else {
                format!("String.valueOf({})", r)
            };
            format!("{}.concat({})", l_sv, r_sv)
        }
        "<@" | "@>" => format!("((Object) {}) != null", l),
        "->" => format!("this.jsonbGet({}, {})", l, r),
        "->>" => format!("this.jsonbGetText({}, {})", l, r),
        "=" => {
            // Issue #136-A2: `p = NULL` on a primitive-emitted IN param can
            // never be true (mirrors the IS NULL fold — the param cannot
            // hold null, so the comparison is constant-false).
            if (is_nullish_java_expr(strip_outer_parens(&l)) && is_primitive_emitted_param(&r, proc))
                || (is_nullish_java_expr(strip_outer_parens(&r)) && is_primitive_emitted_param(&l, proc))
            {
                return "false".to_string();
            }
            let l_kind = boxed_num_kind(&l, proc);
            let r_kind = boxed_num_kind(&r, proc);
            // Boxed numeric vs numeric literal: Objects.equals compares Java
            // types, so the literal must carry the matching boxed suffix —
            // `Objects.equals(vLong, 0)` is Long vs Integer and always false.
            if let Some(k) = l_kind {
                if let Some(lit) = boxed_literal(&r, k) {
                    return format!("java.util.Objects.equals({}, {})", l, lit);
                }
            }
            if let Some(k) = r_kind {
                if let Some(lit) = boxed_literal(&l, k) {
                    return format!("java.util.Objects.equals({}, {})", lit, r);
                }
            }
            // Same-kind boxed pairs: `==`/`!=` on objects is reference
            // equality — wrong for values outside the Integer cache.
            if let (Some(a), Some(b)) = (l_kind, r_kind) {
                if a == b {
                    return format!("java.util.Objects.equals({}, {})", l, r);
                }
            }
            let l_numeric = is_integer_type(&l, proc) || l.chars().all(|c: char| c.is_ascii_digit());
            let r_numeric = is_integer_type(&r, proc) || r.chars().all(|c: char| c.is_ascii_digit());
            if l_numeric && r_numeric {
                format!("{} == {}", l, r)
            } else if is_string_var(&l, proc) && r.chars().all(|c: char| c.is_ascii_digit()) && !r.is_empty() {
                format!("java.util.Objects.equals({}, \"{}\")", l, r)
            } else if is_string_var(&r, proc) && l.chars().all(|c: char| c.is_ascii_digit()) && !l.is_empty() {
                format!("java.util.Objects.equals(\"{}\", {})", l, r)
            } else {
                format!("java.util.Objects.equals({}, {})", l, r)
            }
        }
        "!=" | "<>" => {
            // Issue #136-A2: `p != NULL` / `p <> NULL` on a primitive-emitted
            // IN param is constant-true (the param cannot be null).
            if (is_nullish_java_expr(strip_outer_parens(&l)) && is_primitive_emitted_param(&r, proc))
                || (is_nullish_java_expr(strip_outer_parens(&r)) && is_primitive_emitted_param(&l, proc))
            {
                return "true".to_string();
            }
            let l_kind = boxed_num_kind(&l, proc);
            let r_kind = boxed_num_kind(&r, proc);
            if let Some(k) = l_kind {
                if let Some(lit) = boxed_literal(&r, k) {
                    return format!("!java.util.Objects.equals({}, {})", l, lit);
                }
            }
            if let Some(k) = r_kind {
                if let Some(lit) = boxed_literal(&l, k) {
                    return format!("!java.util.Objects.equals({}, {})", lit, r);
                }
            }
            if let (Some(a), Some(b)) = (l_kind, r_kind) {
                if a == b {
                    return format!("!java.util.Objects.equals({}, {})", l, r);
                }
            }
            let l_numeric = is_integer_type(&l, proc) || l.chars().all(|c: char| c.is_ascii_digit());
            let r_numeric = is_integer_type(&r, proc) || r.chars().all(|c: char| c.is_ascii_digit());
            if l_numeric && r_numeric {
                format!("{} != {}", l, r)
            } else {
                format!("!java.util.Objects.equals({}, {})", l, r)
            }
        }
        "AND" => format!("({} && {})", boolean_operand_java(&l), boolean_operand_java(&r)),
        "OR" => format!("({} || {})", boolean_operand_java(&l), boolean_operand_java(&r)),
        "IS" => format!("{} == {}", l, r),
        "IS NOT" => format!("{} != {}", l, r),
        ">" | "<" | ">=" | "<=" => {
            let l_null = is_nullish_java_expr(&l);
            let r_null = is_nullish_java_expr(&r);
            if l_null || r_null {
                return "true".to_string();
            }
            let l_is_str = looks_string_expr(&l, proc);
            let r_is_str = looks_string_expr(&r, proc);
            if l_is_str || r_is_str {
                // Issue #40: String.compareTo() is lexicographic ("10" < "3").
                // When comparing against a numeric string literal, use BigDecimal.
                let l_numeric_str = l.starts_with('"') && l.trim_matches('"').parse::<f64>().is_ok();
                let r_numeric_str = r.starts_with('"') && r.trim_matches('"').parse::<f64>().is_ok();
                if (l_numeric_str && r_numeric_str)
                    || (l_numeric_str && !r_is_str && !r.contains('.'))
                    || (r_numeric_str && !l_is_str && !l.contains('.'))
                {
                    let cmp_str = match op {
                        ">" => " > 0",
                        "<" => " < 0",
                        ">=" => " >= 0",
                        "<=" => " <= 0",
                        _ => " != 0",
                    };
                    let l_bd = if l_is_str && !l_numeric_str {
                        format!("new java.math.BigDecimal(String.valueOf({}).replace(\"-\", \"\"))", l)
                    } else if !l_is_str {
                        format!("new java.math.BigDecimal({} != null ? String.valueOf({}) : \"0\")", l, l)
                    } else {
                        format!("new java.math.BigDecimal({})", l)
                    };
                    let r_bd = if r_is_str && !r_numeric_str {
                        format!("new java.math.BigDecimal(String.valueOf({}).replace(\"-\", \"\"))", r)
                    } else if !r_is_str {
                        format!("new java.math.BigDecimal({} != null ? String.valueOf({}) : \"0\")", r, r)
                    } else {
                        format!("new java.math.BigDecimal({})", r)
                    };
                    return format!("{}.compareTo({}){}", l_bd, r_bd, cmp_str);
                }
                let cmp_method = match op {
                    ">" => " > 0",
                    "<" => " < 0",
                    ">=" => " >= 0",
                    "<=" => " <= 0",
                    _ => " != 0",
                };
                if l_is_str {
                    format!("{}.compareTo(String.valueOf({})){}", l, r, cmp_method)
                } else {
                    format!("String.valueOf({}).compareTo({}){}", l, r, cmp_method)
                }
            } else if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc) {
                let cmp_method = match op {
                    ">" => " > 0",
                    "<" => " < 0",
                    ">=" => " >= 0",
                    "<=" => " <= 0",
                    _ => " != 0",
                };
                let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
                let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
                format!("{}.compareTo({}){}", l_bd, r_bd, cmp_method)
            } else if l_is_ts || r_is_ts {
                let cmp_method = match op {
                    ">" => " > 0",
                    "<" => " < 0",
                    ">=" => " >= 0",
                    "<=" => " <= 0",
                    _ => " != 0",
                };
                // Review #129 P0-1: operands ending in `.toLocalDate()` yield
                // java.time.LocalDate, which is not a java.util.Date — wrap
                // as Timestamp so compareTo compiles for both Date and
                // Timestamp receivers.
                format!("{}.compareTo({}){}", localdate_operand_to_ts(&l), localdate_operand_to_ts(&r), cmp_method)
            } else {
                // Optional-producing operands (array_position/array_length)
                // unwrap to long regardless of `.get(` presence —
                // safe_long_value would cast the Optional itself to Number.
                if let (Some(lo), Some(ro)) = (optional_numeric_operand(&l), optional_numeric_operand(&r)) {
                    return format!("{} {} {}", lo, op, ro);
                }
                if let Some(lo) = optional_numeric_operand(&l) {
                    let ro = if needs_get_unwrap(&r) { safe_long_value(&r) } else { r.clone() };
                    return format!("{} {} {}", lo, op, ro);
                }
                if let Some(ro) = optional_numeric_operand(&r) {
                    let lo = if needs_get_unwrap(&l) { safe_long_value(&l) } else { l.clone() };
                    return format!("{} {} {}", lo, op, ro);
                }
                let has_get_l = needs_get_unwrap(&l);
                let has_get_r = needs_get_unwrap(&r);
                let (l_out, r_out) = if has_get_l || has_get_r {
                    let lo = if has_get_l { safe_long_value(&l) } else { l.clone() };
                    let ro = if has_get_r { safe_long_value(&r) } else { r.clone() };
                    (lo, ro)
                } else {
                    (l.clone(), r.clone())
                };
                format!("{} {} {}", l_out, op, r_out)
            }
        }
        "*" if is_bigdecimal_var(&l, proc)
            || is_bigdecimal_var(&r, proc)
            || looks_bd_expr(&l)
            || looks_bd_expr(&r)
            || call_expr_return_type(&l, proc).is_some_and(|t| t.contains("BigDecimal"))
            || call_expr_return_type(&r, proc).is_some_and(|t| t.contains("BigDecimal")) =>
        {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.multiply({})", l_bd, r_bd)
        }
        "+" if is_bigdecimal_var(&l, proc)
            || is_bigdecimal_var(&r, proc)
            || looks_bd_expr(&l)
            || looks_bd_expr(&r)
            || call_expr_return_type(&l, proc).is_some_and(|t| t.contains("BigDecimal"))
            || call_expr_return_type(&r, proc).is_some_and(|t| t.contains("BigDecimal")) =>
        {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.add({})", l_bd, r_bd)
        }
        "-" if is_bigdecimal_var(&l, proc)
            || is_bigdecimal_var(&r, proc)
            || looks_bd_expr(&l)
            || looks_bd_expr(&r)
            || call_expr_return_type(&l, proc).is_some_and(|t| t.contains("BigDecimal"))
            || call_expr_return_type(&r, proc).is_some_and(|t| t.contains("BigDecimal")) =>
        {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.subtract({})", l_bd, r_bd)
        }
        "/" if is_bigdecimal_var(&l, proc)
            || is_bigdecimal_var(&r, proc)
            || looks_bd_expr(&l)
            || looks_bd_expr(&r)
            || call_expr_return_type(&l, proc).is_some_and(|t| t.contains("BigDecimal"))
            || call_expr_return_type(&r, proc).is_some_and(|t| t.contains("BigDecimal")) =>
        {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            let r_safe = if r_bd.chars().all(|c: char| c.is_ascii_digit() || c == '.') && !r_bd.is_empty() {
                r_bd
            } else {
                format!("({}.signum() != 0 ? {} : java.math.BigDecimal.ONE)", r_bd, r_bd)
            };
            format!("{}.divide({}, 10, java.math.RoundingMode.HALF_UP)", l_bd, r_safe)
        }

        "^" => {
            let l_pow = as_double_expr(&l);
            let r_pow = as_double_expr(&r);
            let pow = format!("Math.pow({}, {})", l_pow, r_pow);
            if is_bigdecimal_var(&l, proc)
                || is_bigdecimal_var(&r, proc)
                || l.contains("BigDecimal")
                || r.contains("BigDecimal")
                || l.contains(".divide(")
                || r.contains(".divide(")
            {
                format!("java.math.BigDecimal.valueOf({})", pow)
            } else {
                pow
            }
        }
        _ => {
            let has_get_l = needs_get_unwrap(&l);
            let has_get_r = needs_get_unwrap(&r);
            let is_comparison = matches!(op, ">" | "<" | ">=" | "<=");
            let is_arith = matches!(op, "+" | "-" | "*" | "/");
            let (l_out, r_out) = if is_comparison {
                let lo = if has_get_l {
                    let l_base = l.split(".get(").next().unwrap_or(&l);
                    if is_string_var(l_base, proc) {
                        format!("Long.valueOf({}.toString())", l)
                    } else {
                        safe_long_value(&l)
                    }
                } else {
                    l.clone()
                };
                let ro = if has_get_r {
                    let r_base = r.split(".get(").next().unwrap_or(&r);
                    if is_string_var(r_base, proc) {
                        format!("Long.valueOf({}.toString())", r)
                    } else {
                        safe_long_value(&r)
                    }
                } else {
                    r.clone()
                };
                (lo, ro)
            } else if is_arith
                && (has_get_l
                    || has_get_r
                    || optional_numeric_operand(&l).is_some()
                    || optional_numeric_operand(&r).is_some())
            {
                // Optional-producing operands unwrap to long first —
                // safe_long_value would cast the Optional itself to Number.
                let lo = if let Some(u) = optional_numeric_operand(&l) {
                    u
                } else if has_get_l {
                    safe_long_value(&l)
                } else if l.trim().starts_with("(Number)") {
                    format!("({}).longValue()", l)
                } else {
                    l.clone()
                };
                let ro = if let Some(u) = optional_numeric_operand(&r) {
                    u
                } else if has_get_r {
                    safe_long_value(&r)
                } else if r.trim().starts_with("(Number)") {
                    format!("({}).longValue()", r)
                } else {
                    r.clone()
                };
                (lo, ro)
            } else {
                (l.clone(), r.clone())
            };
            if op == "/"
                && r_out.trim().matches(|c: char| c.is_ascii_digit()).count() > 0
                && r_out.trim().trim_start_matches('-').parse::<i64>().is_ok_and(|v| v == 0)
            {
                format!("({} != 0 ? {} {} {} : 0)", r_out, l_out, op, r_out)
            } else {
                format!("{} {} {}", l_out, op, r_out)
            }
        }
    }
}

fn unary_op_to_java(op: &str, operand: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let inner = expr_to_java(operand, proc);
    if is_nullish_java_expr(&inner) {
        if op == "-" && is_timestamp_or_date_var(&inner, proc) {
            return "null".to_string();
        }
        if op == "NOT" {
            return "true".to_string();
        }
        return "0".to_string();
    }
    match op {
        // P1-4 (review #129): BigDecimal has no Java unary minus — use negate().
        "-" if is_bigdecimal_var(&inner, proc) || inner.contains("BigDecimal") => {
            format!("({}).negate()", inner)
        }
        "-" => format!("(-{})", inner),
        "NOT" => format!("!{}", inner),
        _ => format!("{}{}", op, inner),
    }
}

/// True when `s`'s outermost value is `java.time.LocalDate`: the first
/// top-level `.toLocalDate()` is followed only by LocalDate-returning
/// method calls (`plus*`/`minus*`/`with*`). Substring vetoes are unsafe —
/// `new java.sql.Date(...).toLocalDate().plusMonths(x)` still yields
/// LocalDate even though it contains `java.sql.Date.valueOf(`, while
/// `String.valueOf(x.toLocalDate())` or `x.toLocalDate().toString()` do
/// not (review #129 §3-A).
fn expr_yields_localdate(s: &str) -> bool {
    let Some(pos) = s.find(".toLocalDate()") else {
        return false;
    };
    let mut rest = s[pos + ".toLocalDate()".len()..].trim_start();
    while !rest.is_empty() {
        if !rest.starts_with('.') {
            return false;
        }
        rest = &rest[1..];
        let name_len = rest.find(|c: char| !(c.is_alphanumeric() || c == '_')).unwrap_or(rest.len());
        let name = &rest[..name_len];
        if !(name.starts_with("plus") || name.starts_with("minus") || name.starts_with("with")) {
            return false;
        }
        rest = rest[name_len..].trim_start();
        if !rest.starts_with('(') {
            return false;
        }
        let mut depth = 0usize;
        let mut close = None;
        for (i, c) in rest.char_indices() {
            match c {
                '(' => depth += 1,
                ')' => {
                    depth -= 1;
                    if depth == 0 {
                        close = Some(i);
                        break;
                    }
                }
                _ => {}
            }
        }
        match close {
            Some(i) => rest = rest[i + 1..].trim_start(),
            None => return false,
        }
    }
    true
}

/// Wrap an expression that yields `java.time.LocalDate` (temporal lowering
/// ends in `.toLocalDate()`) into a `java.sql.Timestamp` so it can be passed
/// to `java.util.Date`/`Timestamp.compareTo` and `SimpleDateFormat.format`.
/// No-op for expressions already producing a Date/Timestamp.
fn localdate_operand_to_ts(s: &str) -> String {
    let t = s.trim();
    if expr_yields_localdate(t)
        && !t.starts_with("new java.sql.Timestamp")
        && !t.starts_with("new java.sql.Date.valueOf")
    {
        format!("new java.sql.Timestamp(java.sql.Date.valueOf({}).getTime())", t)
    } else {
        s.to_string()
    }
}

/// PL/SQL collection indices are 1-based and typically `Long`/`Integer`
/// boxed or `long` arithmetic — a bare `(int)(expr)` cast fails javac on
/// boxed types (`Long`↛`int` in one cast). `(int)(long)(expr)` is legal for
/// every integral shape: primitives widen/narrow, boxed types unbox first.
pub(crate) fn coll_idx_java(idx: &str) -> String {
    format!("(int)(long)({})", idx)
}

/// Issue #124: lower PL/SQL collection pseudo-methods used in expression
/// position (`coll.EXISTS(i)`, `coll.NEXT(i)`, ...) to List operations.
/// Returns the Java expression; indices stay 1-based per PL/SQL semantics.
fn collection_expr_method(method: &str, obj: &str, jargs: &[String], proc: &ProcedureInfo) -> String {
    let var = snake_to_camel(obj);
    let deref = proc.parameters.iter().any(|p| p.name.eq_ignore_ascii_case(obj) && p.is_out())
        || proc.out_local_vars.contains_key(&obj.to_lowercase());
    let base = if deref { format!("{}.get()", var) } else { var };
    let idx = jargs.first().map(|s| s.as_str()).unwrap_or("1");
    match method {
        "EXISTS" => format!("(({}) >= 1 && ({}) <= ({}).size())", idx, idx, base),
        // Box both ternary arms as Long (review #129 P2): mixing a primitive
        // numeric with `null` produces uncompilable or unboxing-surprise code.
        "NEXT" => format!("((({}) + 1) <= ({}).size() ? java.lang.Long.valueOf(({}) + 1) : null)", idx, base, idx),
        "PRIOR" => format!("((({}) - 1) >= 1 ? java.lang.Long.valueOf(({}) - 1) : null)", idx, idx),
        "COUNT" => format!("({}).size()", base),
        "FIRST" => format!("(({}).isEmpty() ? null : java.lang.Long.valueOf(1))", base),
        "LAST" => format!("(({}).isEmpty() ? null : java.lang.Long.valueOf(({}).size()))", base, base),
        _ => format!("/* TOBEFIX: coll.{} */ null", method.to_lowercase()),
    }
}

fn function_call_to_java(name: &str, args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
    {
        let name_parts: Vec<&str> = name.split('.').collect();
        if name_parts.len() == 1 && args.len() == 1 {
            let snake_name = name_parts[0];
            // Issue #124: resolve through local vars, params, package vars and
            // OUT-promoted vars so typed collections lower to List.get(i-1).
            let list_type = resolve_var_java_type(snake_name, proc);
            let type_str = list_type.as_deref().unwrap_or("");
            let is_list = type_str.contains("List")
                || (type_str == "Object" && {
                    let n = snake_name.to_lowercase();
                    n.contains("array")
                        || n.contains("list")
                        || n.contains("spectrum")
                        || n.ends_with("_arr")
                        || n.ends_with("arr")
                });
            if is_list {
                let var_java = snake_to_camel(snake_name);
                let idx = expr_to_java(&args[0], proc);
                // Untyped/Object vars lower through a `List<?>` cast whose
                // element type is unknowable — keep the bare `.get` so
                // downstream `(String)`/`(Number)` cast rules still match the
                // `.get(` shape (review #129 R2).
                if type_str.is_empty() || type_str == "Object" {
                    return format!("((java.util.List<?>) {}).get({} - 1)", var_java, coll_idx_java(&idx));
                }
                let base =
                    if is_out_param(snake_name, proc) || proc.out_local_vars.contains_key(&snake_name.to_lowercase()) {
                        format!("{}.get()", var_java)
                    } else {
                        var_java
                    };
                // Bounds-guarded read (review #129): Oracle raises
                // INDEX_OUT_OF_BOUNDS on missing elements, but generated
                // callers/tests treat collection reads as nullable — emit
                // `null` instead of crashing on `List.get`. Only applied to
                // typed lists so the ternary yields the concrete element type.
                return format!(
                    "(({}) >= 1 && ({}) <= ({}).size() ? {}.get({} - 1) : null)",
                    idx,
                    idx,
                    base,
                    base,
                    coll_idx_java(&idx)
                );
            }
        }
        // Issue #124: collection pseudo-methods in expression position —
        // coll.EXISTS(i) / NEXT(i) / PRIOR(i) / COUNT / FIRST / LAST.
        if name_parts.len() == 2 {
            let meth = name_parts[1].to_uppercase();
            if matches!(meth.as_str(), "EXISTS" | "NEXT" | "PRIOR" | "COUNT" | "FIRST" | "LAST") {
                if let Some(t) = resolve_var_java_type(name_parts[0], proc) {
                    if t.contains("List") {
                        let margs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
                        return collection_expr_method(&meth, name_parts[0], &margs, proc);
                    }
                }
            }
        }
    }
    let upper = name.to_uppercase();
    let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
    match upper.as_str() {
        "NVL" | "COALESCE" if jargs.len() >= 2 => {
            let else_val = if is_bigdecimal_var(&jargs[0], proc) && jargs[1].trim().chars().all(|c| c.is_ascii_digit()) {
                format!("java.math.BigDecimal.valueOf({})", java_int_lit(&jargs[1]))
            } else {
                jargs[1].clone()
            };
            let arg0 = jargs[0].trim();
            // Issue #136-A2: NVL/COALESCE on a primitive-emitted IN param —
            // the arg can never be null, so the result is the arg itself,
            // boxed so downstream `((Number)..)` casts still compile.
            if is_primitive_emitted_param(arg0, proc) {
                let sig = proc
                    .parameters
                    .iter()
                    .find(|p| {
                        !p.is_out()
                            && (crate::naming::snake_to_camel(&p.name) == arg0.trim()
                                || p.name.eq_ignore_ascii_case(arg0.trim()))
                    })
                    .map(|p| crate::generate::service::in_param_signature_type(p, proc));
                let boxed = match sig {
                    Some("long") => "java.lang.Long.valueOf",
                    Some("int") => "java.lang.Integer.valueOf",
                    Some("double") => "java.lang.Double.valueOf",
                    Some("float") => "java.lang.Float.valueOf",
                    Some("boolean") => "java.lang.Boolean.valueOf",
                    _ => "",
                };
                if !boxed.is_empty() {
                    return format!("{}({})", boxed, arg0);
                }
            }
            let arg0_is_ar = proc.out_local_vars.iter().any(|(k,_)|
                k.to_lowercase().replace("_", "") == arg0.to_lowercase().replace("_", ""))
                || proc.parameters.iter().any(|p| p.is_out()
                    && p.name.to_lowercase().replace("_", "") == arg0.to_lowercase().replace("_", ""));
            let true_val = if arg0_is_ar {
                format!("{}.get()", arg0)
            } else {
                arg0.to_string()
            };
            // Optional producers (ARRAY_LENGTH etc.) yield Integer — box a
            // numeric else-branch too or the ternary unboxes to int and
            // downstream ((Number)..) coercions fail to compile.
            let else_val = if jargs[0].trim().starts_with("java.util.Optional")
                && else_val.chars().all(|c| c.is_ascii_digit())
                && !else_val.is_empty()
            {
                format!("java.lang.Integer.valueOf({})", else_val)
            } else {
                else_val
            };
            format!("({} != null ? {} : {})", arg0, true_val, else_val)
        }
        "UPPER" => format!("String.valueOf({}).toUpperCase()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        // #112: `quote_literal(x)` must not degrade to a TOBEFIX null placeholder
        // (callers concat it into SQL strings — a null NPEs at runtime).
        "QUOTE_LITERAL" => format!(
            // (#115 review #5): escape single quotes so the value can be
            // embedded in a SQL string literal without breaking out of it.
            "String.valueOf({}).replace(\"'\", \"''\")",
            jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")
        ),
        // #112: clock_timestamp()/statement_timestamp() are timestamps, not nulls
        "CLOCK_TIMESTAMP" | "STATEMENT_TIMESTAMP" | "TRANSACTION_TIMESTAMP" => {
            "new java.sql.Timestamp(System.currentTimeMillis())".to_string()
        }
        "LOWER" => format!("String.valueOf({}).toLowerCase()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        // Issue #136 batch-5 R2: `null.trim()`/`null.length()` do not compile
        // (`<null>` dereference) — SQL NULL propagates, so the call lowers to
        // `null` when the receiver arg is the null literal.
        "TRIM" => {
            let a = jargs.first().map(|s| s.as_str()).unwrap_or("\"\"");
            if a.trim() == "null" { "null".into() } else { format!("{}.trim()", a) }
        }
        "LENGTH" => {
            let a = jargs.first().map(|s| s.as_str()).unwrap_or("\"\"");
            if a.trim() == "null" { "null".into() } else { format!("{}.length()", a) }
        }
        "INTERVALTONUM" | "INTERVAL_TO_NUM" => {
            jargs.first().cloned().unwrap_or_else(|| "0L".to_string())
        }
        "ABS" => {
            let arg = jargs.first().map(|s| s.as_str()).unwrap_or("0");
            if is_bigdecimal_var(arg, proc) {
                format!("{}.abs()", arg)
            } else {
                format!("Math.abs({})", parse_string_math_arg(arg, proc))
            }
        }
        "FLOOR" => format!("Math.floor({})", parse_string_math_arg(jargs.first().map(|s| s.as_str()).unwrap_or("0"), proc)),
        "CEIL" | "CEILING" => {
            let arg = jargs.first().map(|s| s.as_str()).unwrap_or("0");
            if is_bigdecimal_var(arg, proc) {
                format!("{}.setScale(0, java.math.RoundingMode.CEILING)", arg)
            } else {
                format!("Math.ceil({})", parse_string_math_arg(arg, proc))
            }
        }
        "ROUND" => {
            if jargs.len() >= 2 {
                let arg = jargs[0].as_str();
                if is_bigdecimal_var(arg, proc) {
                    let scale_int = if is_bigdecimal_var(&jargs[1], proc) {
                        format!("{}.intValue()", jargs[1])
                    } else {
                        format!("(int)({})", jargs[1])
                    };
                    format!("{}.setScale({}, java.math.RoundingMode.HALF_UP)", arg, scale_int)
                } else {
                    let scale_dbl = if is_bigdecimal_var(&jargs[1], proc) {
                        format!("{}.doubleValue()", jargs[1])
                    } else {
                        parse_string_math_arg(&jargs[1], proc)
                    };
                    format!("Math.round({} * Math.pow(10, {})) / Math.pow(10, {})", parse_string_math_arg(arg, proc), scale_dbl, scale_dbl)
                }
            } else {
                let arg = jargs.first().map(|s| s.as_str()).unwrap_or("0");
                if is_bigdecimal_var(arg, proc) {
                    format!("{}.setScale(0, java.math.RoundingMode.HALF_UP)", arg)
                } else {
                    format!("Math.round({})", parse_string_math_arg(arg, proc))
                }
            }
        }
        "POWER" | "POW" if jargs.len() >= 2 => format!("Math.pow({}, {})", as_double_expr(&jargs[0]), as_double_expr(&jargs[1])),
        "SQRT" => format!("Math.sqrt({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "SIGN" => format!("Math.signum({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "RADIANS" => format!("Math.toRadians({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "DEGREES" => format!("Math.toDegrees({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "SIN" => format!("Math.sin({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "COS" => format!("Math.cos({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "TAN" => format!("Math.tan({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "ASIN" => format!("Math.asin({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "ACOS" => format!("Math.acos({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "ATAN" => format!("Math.atan({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "ATAN2" if jargs.len() >= 2 => format!("Math.atan2({}, {})", as_double_expr(&jargs[0]), as_double_expr(&jargs[1])),
        "LN" | "LOG" if jargs.len() == 1 => format!("Math.log({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("1"))),
        "LOG10" => format!("Math.log10({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("1"))),
        "EXP" => format!("Math.exp({})", as_double_expr(jargs.first().map(|s| s.as_str()).unwrap_or("0"))),
        "PI" | "PG_PI" => "Math.PI".into(),
        "RANDOM" => "Math.random()".into(),
        "MOD" if jargs.len() >= 2 => format!("({} % {})", parse_string_math_arg(&jargs[0], proc), parse_string_math_arg(&jargs[1], proc)),
        "GREATEST" if !jargs.is_empty() => {
            jargs.iter().skip(1).fold(jargs[0].clone(), |acc, arg| format!("Math.max({}, {})", acc, arg))
        }
        "LEAST" if !jargs.is_empty() => {
            jargs.iter().skip(1).fold(jargs[0].clone(), |acc, arg| format!("Math.min({}, {})", acc, arg))
        }
        "REPLACE" if jargs.len() >= 3 => {
            let s = jargs[0].trim();
            let a1 = jargs[1].trim();
            let a2 = jargs[2].trim();
            // Review #129 S2r: nullable reads come in two shapes — List.get
            // element reads AND Map.getOrDefault record-field reads (the
            // latter dominates the delivery corpus). Guard both.
            let is_nullable_read = |a: &str| a.contains(".get(") || a.contains("getOrDefault(");
            let has_get = is_nullable_read(a1) || is_nullable_read(a2);
            // Issue #136 batch-4 F4: an Object-typed receiver has no
            // `.replace` — wrap in String.valueOf (S2 pattern) unless the
            // receiver is already known-String.
            let recv = {
                let known_string = s.starts_with('"')
                    || s.starts_with('\'')
                    || s.contains("String.valueOf")
                    || s.ends_with(".toString()")
                    || resolve_var_java_type(s, proc).is_some_and(|t| t == "String" || t == "java.lang.String");
                if known_string { s.to_string() } else { format!("String.valueOf({})", s) }
            };
            if has_get {
                // Review #129 S2: PL/SQL string functions return NULL when any
                // arg is NULL — `String.valueOf(null)` would silently replace
                // the literal "null" text. Emit an explicit NULL short-circuit
                // and only String.valueOf the untyped `.get` args
                // (capture-of-? is not a CharSequence).
                let r1 = if is_nullable_read(a1) { format!("String.valueOf({})", a1) } else { a1.to_string() };
                let r2 = if is_nullable_read(a2) { format!("String.valueOf({})", a2) } else { a2.to_string() };
                format!(
                    "(({}) == null || ({}) == null || ({}) == null ? null : ({}).replace({}, {}))",
                    s, a1, a2, recv, r1, r2
                )
            } else {
                format!("{}.replace({}, {})", recv, a1, a2)
            }
        }
        "SUBSTRING" | "SUBSTR" => {
            let wrap_s = |s: &str| -> String {
                if s.starts_with('"') || s.starts_with('\'') {
                    s.to_string()
                } else {
                    let needs_parens = s.contains(" + ") || s.contains(" - ") || s.contains(" * ") || s.contains(" / ");
                    if needs_parens {
                        format!("(String.valueOf({}))", s)
                    } else {
                        format!("String.valueOf({})", s)
                    }
                }
            };
            if jargs.len() >= 3 {
                let s = wrap_s(&jargs[0]);
                let start = &jargs[1];
                let len = &jargs[2];
                let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
                let len_cast = if might_be_long(len, proc) { format!("(int)({})", len) } else { format!("({})", len) };
                format!("{}.substring(Math.min({}.length(), Math.max(0, {} - 1)), Math.min({}.length(), Math.max(0, {} - 1) + {}))", s, s, start_cast, s, start_cast, len_cast)
            } else if jargs.len() >= 2 {
                let s = wrap_s(&jargs[0]);
                let start = &jargs[1];
                let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
                format!("{}.substring(Math.min({}.length(), Math.max(0, {} - 1)))", s, s, start_cast)
            } else {
                "null".into()
            }
        }
        "SPLIT_PART" if jargs.len() >= 3 => format!("((String[]){}.split({}))[{} - 1]", jargs[0], jargs[1], jargs[2]),
        "TO_CHAR" => {
            if jargs.len() >= 2 {
                let arg = jargs.first().map(|s| s.as_str()).unwrap_or("null");
                let raw_fmt = jargs.get(1).map(|s| s.as_str()).unwrap_or("");
                let fmt_clean = raw_fmt.trim_matches('"').trim_matches('\'').to_lowercase();
                let has_date_token = fmt_clean.contains("yyyy") || fmt_clean.contains("yy") || fmt_clean.contains("mm") || fmt_clean.contains("mon") || fmt_clean.contains("dd") || fmt_clean.contains("hh") || fmt_clean.contains("mi") || fmt_clean.contains("ss");
                if has_date_token {
                    let mut java_fmt = fmt_clean.clone();
                    let date_pats = [
                        ("yyyy", "yyyy"), ("yy", "yy"), ("mm", "MM"), ("mon", "MMM"), ("month", "MMMM"),
                        ("dd", "dd"), ("dy", "EEE"), ("day", "EEEE"),
                        ("hh24", "HH"), ("hh12", "hh"), ("hh", "HH"),
                        ("mi", "mm"), ("ss", "ss"), ("ff3", "SSS"), ("ms", "SSS"),
                    ];
                    for (sql_pat, java_pat) in &date_pats {
                        java_fmt = java_fmt.replace(sql_pat, java_pat);
                    }
                    // Review #129 P0-1: `.toLocalDate()` operands are
                    // java.time.LocalDate — SimpleDateFormat.format(Object)
                    // accepts them at compile time but throws at runtime;
                    // route through Timestamp instead.
                    let fmt_arg = localdate_operand_to_ts(arg);
                    if is_timestamp_or_date_var(arg, proc)
                        || arg.contains("Timestamp")
                        || arg.contains("currentTimeMillis")
                        || fmt_arg != arg
                    {
                        format!("new java.text.SimpleDateFormat(\"{}\").format({})", java_fmt, fmt_arg)
                    } else {
                        format!("new java.text.SimpleDateFormat(\"{}\").format(new java.util.Date(java.sql.Timestamp.valueOf(String.valueOf({})).getTime()))", java_fmt, arg)
                    }
                } else {
                    let num_fmt = fmt_clean.replace("fm", "").replace(",", "").replace("9", "#");
                    format!("new java.text.DecimalFormat(\"{}\").format({})", num_fmt, arg)
                }
            } else {
                format!("String.valueOf({})", jargs.first().map(|s| s.as_str()).unwrap_or("null"))
            }
        }
        "NULLIF" if jargs.len() >= 2 => format!("(java.util.Objects.equals({}, {}) ? 1 : {})", jargs[0], jargs[1], jargs[0]),
        // array_length/array_upper → boxed Integer-or-NULL (SQL semantics:
        // NULL array → NULL). Optional keeps the arg single-evaluated and
        // null-safe; downstream COALESCE/ternary need a boxed operand.
        "ARRAY_LENGTH" | "ARRAY_UPPER" => format!(
            "java.util.Optional.ofNullable({}).map(__l -> __l instanceof java.util.Collection ? ((java.util.Collection<?>) __l).size() : null).orElse(null)",
            jargs.first().map(|s| s.as_str()).unwrap_or("null")
        ),
        // array_position(arr, elem) → 1-based index or NULL. Optional keeps
        // both args single-evaluated and propagates NULL for missing keys.
        "ARRAY_POSITION" if jargs.len() >= 2 => format!(
            "java.util.Optional.ofNullable({}).map(__l -> __l instanceof java.util.List ? ((java.util.List<?>) __l).indexOf({}) : -1).filter(__i -> __i >= 0).map(__i -> __i + 1).orElse(null)",
            jargs[0], jargs[1]
        ),
        "ARRAY_APPEND" if jargs.len() >= 2 => format!("_appendList({}, {})", jargs[0], jargs[1]),
        "ARRAY_APPEND" => jargs.first().map(|s| s.as_str()).unwrap_or("null").to_string(),
        "ARRAY_TO_STRING" if jargs.len() >= 2 => {
            let delim = &jargs[1];
            let delim_expr = if delim.starts_with('"') || delim.starts_with('\'') { delim.clone() } else { format!("String.valueOf({})", delim) };
            format!("({}).stream().map(Object::toString).collect(java.util.stream.Collectors.joining({}))", jargs[0], delim_expr)
        }
        "NEXTVAL" if !jargs.is_empty() => format!("this.nextval({})", jargs.join(", ")),
        "NEXTVAL" => "null".into(),
        "CURRVAL" if !jargs.is_empty() => format!("this.currval({})", jargs.join(", ")),
        "CURRVAL" => "null".into(),
        "ADD_MONTHS" if jargs.len() >= 2 => format!("{}.plusMonths(Long.parseLong(String.valueOf({})))", local_date_arg(&jargs[0], proc), jargs[1]),
        "LAST_DAY" => {
            let d = local_date_arg(jargs.first().map(|s| s.as_str()).unwrap_or("null"), proc);
            format!("{}.withDayOfMonth({}.lengthOfMonth())", d, d)
        }
        "NEXT_DAY" if !jargs.is_empty() => format!("{}.plusWeeks(1)", local_date_arg(&jargs[0], proc)),
        "NEXT_DAY" => "null".into(),
        "EXTRACT" => "/* EXTRACT */ 0".into(),
        "AGE" if jargs.len() >= 2 => format!("java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate(), new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate())", jargs[1], jargs[0]),
        "AGE" if jargs.len() == 1 => format!("java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate(), java.time.LocalDate.now())", jargs[0]),
        "DATE_TRUNC" if jargs.len() >= 2 => {
            // java.sql.Date.toInstant() throws UnsupportedOperationException —
            // wrap Date-typed args in a Timestamp before calling .toInstant().
            let ts_arg = {
                let a = jargs[1].trim();
                if a.starts_with("new java.sql.Date")
                    || a.starts_with("java.sql.Date.valueOf")
                    || resolve_var_java_type(a, proc)
                        .map(|t| t.contains("java.sql.Date") && !t.contains("Timestamp"))
                        .unwrap_or(false)
                {
                    format!("new java.sql.Timestamp({}.getTime())", a)
                } else {
                    jargs[1].clone()
                }
            };
            let unit_raw = jargs[0].trim_matches('"').trim_matches('\'').to_lowercase();
            let chrono_unit = match unit_raw.as_str() {
                "microsecond" | "microseconds" => "java.time.temporal.ChronoUnit.MICROS",
                "millisecond" | "milliseconds" => "java.time.temporal.ChronoUnit.MILLIS",
                "second" | "seconds" => "java.time.temporal.ChronoUnit.SECONDS",
                "minute" | "minutes" => "java.time.temporal.ChronoUnit.MINUTES",
                "hour" | "hours" => "java.time.temporal.ChronoUnit.HOURS",
                "day" | "days" => "java.time.temporal.ChronoUnit.DAYS",
                "week" | "weeks" => "java.time.temporal.ChronoUnit.WEEKS",
                "month" | "months" => "java.time.temporal.ChronoUnit.MONTHS",
                "quarter" => "java.time.temporal.ChronoUnit.MONTHS",
                "year" | "years" => "java.time.temporal.ChronoUnit.YEARS",
                _ => "java.time.temporal.ChronoUnit.DAYS",
            };
            if unit_raw == "quarter" {
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).truncatedTo(java.time.temporal.ChronoUnit.DAYS).withMonth(((java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).getMonthValue() - 1) / 3) * 3 + 1).withDayOfMonth(1))", ts_arg, ts_arg)
            } else if chrono_unit.contains("MONTHS") {
                // LocalDateTime.truncatedTo only accepts time units; date units
                // need withDayOfMonth(1) + truncate to DAYS.
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).withDayOfMonth(1).truncatedTo(java.time.temporal.ChronoUnit.DAYS))", ts_arg)
            } else if chrono_unit.contains("YEARS") {
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).withDayOfYear(1).truncatedTo(java.time.temporal.ChronoUnit.DAYS))", ts_arg)
            } else if chrono_unit.contains("WEEKS") {
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).with(java.time.DayOfWeek.MONDAY).truncatedTo(java.time.temporal.ChronoUnit.DAYS))", ts_arg)
            } else {
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).truncatedTo({}))", ts_arg, chrono_unit)
            }
        }
        "MONTHS_BETWEEN" if jargs.len() >= 2 => format!("java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate(), new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate()).toTotalMonths()", jargs[1], jargs[0]),
        "TO_TIMESTAMP" | "MAKE_DATE" | "MAKE_TIMESTAMP" => format!("/* {} */ null", upper),
         "ROW_NUMBER" | "RANK" | "DENSE_RANK" | "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" => format!("/* aggregate:{} */ 0", upper),
        "BIT_AND" | "BIT_OR" | "BIT_XOR" => format!("/* {} */ 0", upper),
        "GET_BIT" => "/* GET_BIT */ 0".into(),
        "SET_BIT" => "/* SET_BIT */".into(),
        "DECODE" if jargs.len() >= 3 => {
            let expr = &jargs[0];
            let has_default = (jargs.len() - 1) % 2 == 1;
            let default = if has_default { jargs.last().unwrap().clone() } else { "null".into() };
            let mut result = default;
            let mut i: i32 = if has_default { (jargs.len() - 3) as i32 } else { (jargs.len() - 2) as i32 };
            while i >= 1 {
                result = format!("(java.util.Objects.equals({}, {}) ? {} : {})", expr, jargs[i as usize], jargs[(i + 1) as usize], result);
                i -= 2;
            }
            result
        }
        "ENCODE" if jargs.len() >= 2 => {
            let fmt = jargs[1].trim_matches('"').trim_matches('\'').to_lowercase();
            if fmt == "base64" {
                let arg0 = &jargs[0];
                let arg0_expr = if arg0.starts_with('"') || arg0.starts_with('\'') { arg0.clone() } else { format!("String.valueOf({})", arg0) };
                format!("java.util.Base64.getEncoder().encodeToString({}.getBytes())", arg0_expr)
            } else {
                format!("/* TODO: encode({}, {}) */ null", jargs[0], jargs[1])
            }
        }
        "ENCODE" => "/* ENCODE */ null".to_string(),
        "TRANSLATE" if jargs.len() >= 3 => {
            let s = &jargs[0];
            let from_chars = &jargs[1];
            let to_chars = &jargs[2];
            let fc = if from_chars.starts_with('"') || from_chars.starts_with('\'') { from_chars.clone() } else { format!("String.valueOf({})", from_chars) };
            let tc = if to_chars.starts_with('"') || to_chars.starts_with('\'') { to_chars.clone() } else { format!("String.valueOf({})", to_chars) };
            format!("String.valueOf({}).chars().mapToObj(c -> {{ int idx = {}.indexOf(c); return idx >= 0 && idx < {}.length() ? String.valueOf({}.charAt(idx)) : String.valueOf((char) c); }}).collect(java.util.stream.Collectors.joining())", s, fc, tc, tc)
        }
        "TO_HEX" if !jargs.is_empty() => format!("Long.toHexString({}).toUpperCase()", jargs[0]),
        "TO_DATE" if jargs.len() >= 2 => {
            let fmt_raw = jargs[1].trim_matches('"').trim_matches('\'').to_lowercase();
            let date_expr = if fmt_raw == "yyyy-mm-dd" {
                // #96: month/day may be single-digit ("2026-5-01") from manual
                // string concat — accept lenient single-digit forms too.
                // #128: temporal args bypass String.valueOf→parse entirely.
                local_date_arg_fmt(&jargs[0], "[yyyy-MM-dd][yyyyMMdd][yyyy-M-d]", proc)
            } else {
                let java_fmt = fmt_raw
                    .replace("mm", "MM").replace("mon", "MMM").replace("month", "MMMM")
                    .replace("dy", "EEE").replace("day", "EEEE")
                    .replace("hh24", "HH").replace("hh12", "hh").replace("hh", "HH")
                    .replace("mi", "mm")
                    .replace("ff3", "SSS").replace("ms", "SSS");
                local_date_arg_fmt(&jargs[0], &format!("[{}][yyyy-MM-dd]", java_fmt), proc)
            };
            format!("java.sql.Date.valueOf({})", date_expr)
        }
        "TO_DATE" => format!("java.sql.Date.valueOf(String.valueOf({}))", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "MD5" => {
            let arg = jargs.first().map(|s| {
                if s.starts_with('"') || s.starts_with('\'') { s.clone() }
                else { format!("String.valueOf({})", s) }
            }).unwrap_or_else(|| "\"\"".to_string());
            format!("this._md5({})", arg)
        }
        "SHA224" | "SHA256" | "SHA384" | "SHA512" => format!("/* {} */ null", upper),
        "HMAC_MD5" | "HMAC_SHA1" | "HMAC_SHA256" => format!("/* {} */ null", upper),
        "PG_SLEEP" => "/* PG_SLEEP */".into(),
        "SET_CONFIG" => "/* SET_CONFIG */ null".into(),
        "CURRENT_SETTING" => "/* CURRENT_SETTING */ null".into(),
        "PG_BACKEND_PID" => "Thread.currentThread().getId()".into(),
        "LISTAGG" | "STRING_AGG" => format!("/* {} */ null", upper),
        "REGEXP_REPLACE" if jargs.len() >= 3 => format!("{}.replaceAll({}, {})", jargs[0], jargs[1], jargs[2]),
        "LEFT" if jargs.len() >= 2 => format!("{}.substring(0, Math.min({}.length(), {}))", jargs[0], jargs[0], jargs[1]),
        "RIGHT" if jargs.len() >= 2 => format!("{}.substring(Math.max(0, {}.length() - {}))", jargs[0], jargs[0], jargs[1]),
        "LPAD" => {
            match jargs.len() {
                0 | 1 => jargs.first().map(|s| s.as_str()).unwrap_or("\"\"").to_string(),
                2 => format!("String.format(\"%\" + ({}) + \"s\", {})", jargs[1], jargs[0]),
                _ => format!("String.format(\"%\" + ({}) + \"s\", {}).replace(\" \", {})", jargs[1], jargs[0], jargs[2]),
            }
        }
        "RPAD" => {
            match jargs.len() {
                0 | 1 => jargs.first().map(|s| s.as_str()).unwrap_or("\"\"").to_string(),
                2 => format!("String.format(\"%-\" + ({}) + \"s\", {})", jargs[1], jargs[0]),
                _ => format!("String.format(\"%-\" + ({}) + \"s\", {}).replace(\" \", {})", jargs[1], jargs[0], jargs[2]),
            }
        }
        "LTRIM" => {
            match jargs.len() {
                0 => "\"\"".to_string(),
                1 if jargs[0].trim() == "null" => "null".to_string(),
                1 => format!("{}.replaceAll(\"^\\\\s+\", \"\")", jargs[0]),
                _ if jargs[0].trim() == "null" => "null".to_string(),
                _ => format!("{}.replaceAll(\"^\" + {} + \"+\", \"\")", jargs[0], jargs[1]),
            }
        }
        "RTRIM" => {
            match jargs.len() {
                0 => "\"\"".to_string(),
                1 if jargs[0].trim() == "null" => "null".to_string(),
                1 => format!("{}.replaceAll(\"\\\\s+$\", \"\")", jargs[0]),
                _ if jargs[0].trim() == "null" => "null".to_string(),
                _ => format!("{}.replaceAll(\"\" + {} + \"+$\", \"\")", jargs[0], jargs[1]),
            }
        }
        "REPEAT" if jargs.len() >= 2 => {
            if jargs[0].trim() == "null" { "null".into() } else { format!("{}.repeat({})", jargs[0], jargs[1]) }
        }
        "REVERSE" => {
            let a = jargs.first().map(|s| s.as_str()).unwrap_or("null");
            // `new StringBuilder(null)` is an ambiguous ctor call AND NPEs —
            // SQL NULL propagates instead.
            if a.trim() == "null" { "null".into() } else { format!("new StringBuilder({}).reverse().toString()", a) }
        }
        "CONCAT" | "CONCAT_WS" if !jargs.is_empty() => {
            let inner = jargs.join(").concat(String.valueOf(");
            let needs_close = inner.matches('(').count() as isize - inner.matches(')').count() as isize;
            format!("String.valueOf({}{})", inner, ")".repeat(needs_close.max(0) as usize))
        }
        "FORMAT" => format!("String.format({})", jargs.join(", ")),
        "PG_TABLE_IS_VISIBLE" | "HAS_SCHEMA_PRIVILEGE" | "HAS_TABLE_PRIVILEGE" => "true".into(),
        "VERSION" => "\"PostgreSQL/compatible\"".into(),
        "INET_CLIENT_ADDR" | "INET_SERVER_ADDR" => "null".into(),
        "TXID_CURRENT" => "Thread.currentThread().getId()".into(),
        "SYSDATE" | "CURRENT_TIMESTAMP" | "NOW" | "SYSTIMESTAMP" | "LOCALTIMESTAMP" => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
        "CURRENT_DATE" => "new java.sql.Date(System.currentTimeMillis())".into(),
        "CHR" if !jargs.is_empty() => format!("String.valueOf((char)({}))", jargs[0]),
        "ASCII" if !jargs.is_empty() => {
            if jargs[0].trim() == "null" { "null".into() } else { format!("(int){}.charAt(0)", jargs[0]) }
        }
        "TO_NUMBER" => {
            let arg = jargs.first().map(|s| s.as_str()).unwrap_or("\"0\"");
            // Issue #57: TO_NUMBER('') should produce BigDecimal.ZERO, not new BigDecimal("")
            if arg == "\"\"" || arg == "''" {
                "java.math.BigDecimal.ZERO".to_string()
            } else {
                format!("new java.math.BigDecimal({})", arg)
            }
        },
        "INSTR" if jargs.len() >= 3 => {
            let s = if jargs[0].starts_with('"') || jargs[0].starts_with('\'') { jargs[0].clone() } else { format!("String.valueOf({})", jargs[0]) };
            let sub = if jargs[1].starts_with('"') || jargs[1].starts_with('\'') { jargs[1].clone() } else { format!("String.valueOf({})", jargs[1]) };
            let start = &jargs[2];
            let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
            format!("{}.indexOf({}, Math.max(0, {} - 1)) + 1", s, sub, start_cast)
        }
        "INSTR" if jargs.len() >= 2 => {
            let s = if jargs[0].starts_with('"') || jargs[0].starts_with('\'') { jargs[0].clone() } else { format!("String.valueOf({})", jargs[0]) };
            let sub = if jargs[1].starts_with('"') || jargs[1].starts_with('\'') { jargs[1].clone() } else { format!("String.valueOf({})", jargs[1]) };
            format!("{}.indexOf({}) + 1", s, sub)
        }
        "TRUNC" if !jargs.is_empty() => format!("(int) Math.floor((double)({}))", parse_string_math_arg(&jargs[0], proc)),
        "JSONB_ARRAY_LENGTH" => format!("this.jsonbArrayLength({})", jargs.join(", ")),
        "JSONB_BUILD_OBJECT" => {
            let coerced: Vec<String> = jargs.iter().enumerate().map(|(i, a)| {
                if i % 2 == 0 { format!("String.valueOf({})", a) } else { a.clone() }
            }).collect();
            format!("this.jsonbBuildObject({})", coerced.join(", "))
        }
        "STRING_TO_ARRAY" | "REGEXP_SPLIT_TO_ARRAY" => {
            if jargs.len() >= 2 {
                format!("java.util.Arrays.asList(String.valueOf({}).split(String.valueOf({})))", jargs[0], jargs[1])
            } else {
                "java.util.Collections.emptyList()".into()
            }
        }
        "STRING_SPLIT" => {
            if jargs.len() >= 2 {
                format!("java.util.Arrays.asList(String.valueOf({}).split(java.util.regex.Pattern.quote(String.valueOf({}))))", jargs[0], jargs[1])
            } else {
                "java.util.Collections.emptyList()".into()
            }
        }
        _ => {
            // Issue #136-E: `t_num_table()` is a collection-type constructor
            // (`TYPE t IS TABLE OF NUMBER` → `List<Long>`), not a function —
            // emit an empty typed list instead of the unresolved-fn marker.
            if jargs.is_empty() {
                if let Some(ct) = crate::extract::find_custom_type(&proc.custom_types, name) {
                    if ct.is_collection() {
                        let elem = ct.elem_java_type.clone().unwrap_or_else(|| "Object".into());
                        return format!("new java.util.ArrayList<{}>()", elem);
                    }
                }
            }
            let name_parts: Vec<&str> = name.split('.').collect();
            let mut ambig_candidates: Vec<String> = Vec::new();
            let mut ambig_note: Option<Vec<String>> = None;
            let (method, is_self_call, cross_pkg_svc, target_params): (
                String,
                bool,
                String,
                Option<Vec<crate::types::Parameter>>,
            ) = if name_parts.len() >= 2 {
                let pkg_hint = if name_parts.len() >= 3 {
                    name_parts[name_parts.len() - 2]
                } else {
                    name_parts[0]
                };
                let func_name = name_parts[name_parts.len() - 1];
                let pkg_lower = proc.package.to_lowercase();
                let hint_lower = pkg_hint.to_lowercase().replace("_", "");
                let pkg_no_prefix = pkg_lower.trim_start_matches("pkg_").replace("_", "");
                // Oracle/GaussDB-supplied API packages (DBE_XMLDOM.*, DBMS_*,
                // UTL_*, ...) are never same-package methods — check BEFORE the
                // self-call match, whose `ends_with` suffix test would otherwise
                // claim `dbexmldom` for a package named `pkg_xmldom` (issue #136).
                // The underscore-anchored prefixes keep user packages like
                // `DBEMP` safe; the explicit list keeps covering underscore-free
                // spellings (DBMSOUTPUT, UTLFILE, ...).
                let hint_upper = pkg_hint.to_uppercase();
                let is_builtin_pkg = ["DBE_", "DBMS_", "UTL_", "CTX_", "ORD_", "APEX_", "WWV_", "DBS_"]
                    .iter()
                    .any(|p| hint_upper.starts_with(p))
                    || ["dbescheduler", "dbmsoutput", "dbmsrandom", "dbmslob", "dbeoutput", "utlfile", "dbmssql", "dbmsjob", "dbexmldom"]
                        .iter()
                        .any(|sp| hint_lower.starts_with(sp));
                if is_builtin_pkg {
                    (crate::naming::java_method_name(func_name), false, String::new(), None)
                } else if hint_lower == pkg_lower || hint_lower == pkg_no_prefix || hint_lower.ends_with(&pkg_no_prefix) {
                    (crate::naming::java_method_name(func_name), true, String::new(), None)
                } else {
                    let method_name = crate::naming::java_method_name(func_name);
                    // Qualified calls (pkg.func / schema.pkg.func) must resolve against the
                    // global registry: emitting `{pkg}Service.m(...)` for a package that is
                    // not among the sources produces a call to a service that is never
                    // generated nor injected — uncompilable Java. Only emit the call when a
                    // registry entry matches the package hint; otherwise fall through to the
                    // TOBEFIX unresolved marker (same as the unqualified path).
                    let mut pkg_matches: Vec<&GlobalFnEntry> = proc
                        .all_proc_params
                        .get(&method_name)
                        .map(|candidates| {
                            candidates
                                .iter()
                                .filter(|e| {
                                    let e_pkg_lower = e.package.to_lowercase();
                                    let e_pkg_no_prefix =
                                        e_pkg_lower.trim_start_matches("pkg_").replace("_", "");
                                    hint_lower == e_pkg_lower.replace("_", "")
                                        || hint_lower == e_pkg_no_prefix
                                        || hint_lower.ends_with(&e_pkg_no_prefix)
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    // Same determinism requirement as the unqualified path
                    // (review #129 P0-2): HashMap iteration order must not
                    // leak into generated code.
                    pkg_matches.sort_by(|a, b| {
                        (&a.package, &a.svc_var)
                            .cmp(&(&b.package, &b.svc_var))
                            .then_with(|| {
                                let pa = a.params.iter().map(|p| p.java_type.as_str()).collect::<Vec<_>>().join(",");
                                let pb = b.params.iter().map(|p| p.java_type.as_str()).collect::<Vec<_>>().join(",");
                                pa.cmp(&pb)
                            })
                            .then_with(|| a.return_type.cmp(&b.return_type))
                    });
                    if pkg_matches.is_empty() {
                        (String::new(), false, String::new(), None)
                    } else {
                        let svc_var = pkg_matches[0].svc_var.clone();
                        let arity_matches: Vec<&&GlobalFnEntry> = pkg_matches
                            .iter()
                            .filter(|e| e.params.len() == jargs.len())
                            .collect();
                        // Only trust a unique (pkg + arity) match for arg coercion;
                        // ambiguous/absent arity matches still emit the call without
                        // coercion (same as the previous unconditional behaviour).
                        // Issue #136-G: when no exact-arity candidate exists, fall
                        // back to the smallest arity >= jargs so DEFAULT parameters
                        // can be padded (emit_cross_pkg_call appends them).
                        let target_params = if arity_matches.len() == 1 {
                            Some(arity_matches[0].params.clone())
                        } else if arity_matches.is_empty() {
                            pkg_matches
                                .iter()
                                .filter(|e| e.params.len() > jargs.len())
                                .min_by_key(|e| e.params.len())
                                .map(|e| e.params.clone())
                        } else {
                            None
                        };
                        // Issue #136 batch-4: when every candidate is NARROWER
                        // than the call site there is no signature to pad
                        // into — emitting anyway produces "required N found
                        // M" (over-padding). Degrade to the unresolved marker
                        // instead of a broken call.
                        if arity_matches.is_empty() && target_params.is_none() {
                            (String::new(), false, String::new(), None)
                        } else {
                            (method_name, false, svc_var, target_params)
                        }
                    }
                }
            } else if name_parts.len() == 1 {
                let method_name = crate::naming::java_method_name(name_parts[0]);
                if proc.package_proc_params.contains_key(&method_name) {
                    (method_name, true, String::new(), None)
                } else if let Some(candidates) = proc.all_proc_params.get(&method_name) {
                    let proc_pkg_lower = proc.package.to_lowercase();
                    // P0-2 (review #129): candidate order comes from HashMap
                    // iteration in pipeline.rs and is non-deterministic across
                    // processes — sort by (package, svc_var, signature) so the
                    // identical-signature pick and the TOBEFIX candidate list
                    // are reproducible.
                    let mut cross: Vec<&GlobalFnEntry> = candidates
                        .iter()
                        .filter(|e| e.package.to_lowercase() != proc_pkg_lower)
                        // Issue #136-G: strict arity equality first; when no
                        // exact match exists, relax to the smallest arity
                        // >= jargs.len() so DEFAULT parameters can be padded
                        // (emit_cross_pkg_call appends default_padding_for_param).
                        .filter(|e| e.params.len() == jargs.len())
                        .collect();
                    if cross.is_empty() {
                        let mut ge: Vec<&GlobalFnEntry> = candidates
                            .iter()
                            .filter(|e| e.package.to_lowercase() != proc_pkg_lower)
                            .filter(|e| e.params.len() >= jargs.len())
                            .collect();
                        if let Some(min_len) = ge.iter().map(|e| e.params.len()).min() {
                            ge.retain(|e| e.params.len() == min_len);
                            cross = ge;
                        }
                    }
                    cross.sort_by(|a, b| {
                        (&a.package, &a.svc_var)
                            .cmp(&(&b.package, &b.svc_var))
                            .then_with(|| {
                                let pa = a.params.iter().map(|p| p.java_type.as_str()).collect::<Vec<_>>().join(",");
                                let pb = b.params.iter().map(|p| p.java_type.as_str()).collect::<Vec<_>>().join(",");
                                pa.cmp(&pb)
                            })
                            .then_with(|| a.return_type.cmp(&b.return_type))
                    });
                    match cross.len() {
                        1 => (method_name, false, cross[0].svc_var.clone(), Some(cross[0].params.clone())),
                        0 => (String::new(), false, String::new(), None),
                        _ => {
                            // Issue #127: multiple cross-package candidates.
                            // When every candidate has an identical signature
                            // (param java types + return type), the call is
                            // compilable and semantically equivalent (e.g. a
                            // schema-level wrapper delegating to the package
                            // impl) — pick the first and annotate the choice.
                            // Differing signatures stay TOBEFIX, but the
                            // marker now carries each candidate's signature.
                            let sig_of = |e: &GlobalFnEntry| -> String {
                                let params = e
                                    .params
                                    .iter()
                                    .map(|p| p.java_type.as_str())
                                    .collect::<Vec<_>>()
                                    .join(",");
                                format!("({})=>{}", params, e.return_type.as_deref().unwrap_or("?"))
                            };
                            let first_sig = sig_of(cross[0]);
                            if cross.iter().all(|e| sig_of(e) == first_sig) {
                                ambig_note = Some(cross.iter().map(|e| e.svc_var.clone()).collect());
                                (method_name, false, cross[0].svc_var.clone(), Some(cross[0].params.clone()))
                            } else {
                                ambig_candidates =
                                    cross.iter().map(|e| format!("{}{}", e.svc_var, sig_of(e))).collect();
                                (String::new(), false, String::new(), None)
                            }
                        }
                    }
                } else {
                    (String::new(), false, String::new(), None)
                }
            } else {
                (String::new(), false, String::new(), None)
            };

            if is_self_call {
                let target_params = proc.package_proc_params.get(&method)
                    .and_then(|overloads| {
                        overloads.iter().find(|params| params.len() == jargs.len())
                            .or_else(|| {
                                overloads.iter()
                                    .filter(|params| params.len() >= jargs.len())
                                    .min_by_key(|params| params.len())
                            })
                    });
                let coerced_args: Vec<String> = if let Some(target_params) = target_params {
                    let mut out = Vec::new();
                    for (i, param) in target_params.iter().enumerate() {
                        // Refcursor OUT params have no Java signature slot —
                        // skip the param AND its positional call-site arg.
                        if param.dropped_from_signature() {
                            continue;
                        }
                        if i < jargs.len() {
                            let arg = &jargs[i];
                            if param.is_out() {
                                out.push(arg.clone());
                            } else {
                                // Issue #136-C: dereference the caller's own OUT
                                // wrapper before coercing into an IN parameter.
                                out.push(coerce_arg_to_type(&unwrap_out_arg(arg, proc), &param.java_type, proc));
                            }
                        } else {
                            out.push(default_padding_for_param(param));
                        }
                    }
                    for a in jargs.iter().skip(target_params.len()) {
                        out.push(a.clone());
                    }
                    out
                } else {
                    jargs
                };
                return format!("this.{}({})", method, coerced_args.join(", "));
            }
            if !cross_pkg_svc.is_empty() && !method.is_empty() {
                let call = emit_cross_pkg_call(&cross_pkg_svc, &method, jargs, target_params.as_deref(), proc);
                if let Some(alts) = &ambig_note {
                    let others: Vec<&str> =
                        alts.iter().map(|s| s.as_str()).filter(|s| *s != cross_pkg_svc).collect();
                    if !others.is_empty() {
                        return format!(
                            "/* ambiguous: chose {} over [{}] */ {}",
                            cross_pkg_svc,
                            others.join(", "),
                            call
                        );
                    }
                }
                return call;
            }
            let func_short = name_parts.last().unwrap_or(&name);
            let pkg_hint = if name_parts.len() >= 2 { name_parts[0] } else { "?" };
            let ambig_suffix = if ambig_candidates.is_empty() {
                String::new()
            } else {
                format!(", candidates=[{}]", ambig_candidates.join(", "))
            };
            format!(
                "/* TOBEFIX: unresolved fn {}({}) - pkg={}, caller={}:{}{} */ null",
                flatten_comment(func_short),
                flatten_comment(&jargs.join(", ")),
                flatten_comment(pkg_hint),
                flatten_comment(&proc.source_file),
                flatten_comment(&proc.proc_name),
                ambig_suffix,
            )
        }
    }
}

fn special_function_to_java(name: &str, args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
    let lower = name.to_lowercase();
    match lower.as_str() {
        "substring" | "substr" => {
            let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
            let wrap_s = |s: &str| -> String {
                if s.starts_with('"') || s.starts_with('\'') {
                    s.to_string()
                } else {
                    let needs_parens = s.contains(" + ") || s.contains(" - ") || s.contains(" * ") || s.contains(" / ");
                    if needs_parens {
                        format!("(String.valueOf({}))", s)
                    } else {
                        format!("String.valueOf({})", s)
                    }
                }
            };
            if jargs.len() >= 3 {
                let s = wrap_s(&jargs[0]);
                let start = &jargs[1];
                let len = &jargs[2];
                let start_cast =
                    if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
                let len_cast = if might_be_long(len, proc) { format!("(int)({})", len) } else { format!("({})", len) };
                format!("{}.substring(Math.min({}.length(), Math.max(0, {} - 1)), Math.min({}.length(), Math.max(0, {} - 1) + {}))", s, s, start_cast, s, start_cast, len_cast)
            } else if jargs.len() >= 2 {
                let s = wrap_s(&jargs[0]);
                let start = &jargs[1];
                let start_cast =
                    if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
                format!("{}.substring(Math.min({}.length(), Math.max(0, {} - 1)))", s, s, start_cast)
            } else {
                jargs.first().cloned().unwrap_or_else(|| "null".into())
            }
        }
        "extract" if args.len() >= 2 => {
            let field = match &args[0] {
                ogsql_parser::ast::Expr::ColumnRef(parts) => parts.join(".").to_lowercase(),
                _ => String::new(),
            };
            let ts_expr = expr_to_java(&args[1], proc);
            match field.as_str() {
                 "year" => format!("java.time.Instant.ofEpochMilli({}.getTime()).atZone(java.time.ZoneId.systemDefault()).toLocalDate().getYear()", ts_expr),
                 "month" => format!("java.time.Instant.ofEpochMilli({}.getTime()).atZone(java.time.ZoneId.systemDefault()).toLocalDate().getMonthValue()", ts_expr),
                 "day" => format!("java.time.Instant.ofEpochMilli({}.getTime()).atZone(java.time.ZoneId.systemDefault()).toLocalDate().getDayOfMonth()", ts_expr),
                 "hour" => format!("java.time.Instant.ofEpochMilli({}.getTime()).atZone(java.time.ZoneId.systemDefault()).toLocalDateTime().getHour()", ts_expr),
                 "minute" => format!("java.time.Instant.ofEpochMilli({}.getTime()).atZone(java.time.ZoneId.systemDefault()).toLocalDateTime().getMinute()", ts_expr),
                 "second" => format!("java.time.Instant.ofEpochMilli({}.getTime()).atZone(java.time.ZoneId.systemDefault()).toLocalDateTime().getSecond()", ts_expr),
                _ => format!("/* EXTRACT({}) */ 0", field),
            }
        }
        "overlay" if args.len() >= 3 => {
            let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
            let s = if jargs[0].starts_with('"') || jargs[0].starts_with('\'') {
                jargs[0].clone()
            } else {
                format!("String.valueOf({})", jargs[0])
            };
            let repl = if jargs[1].starts_with('"') || jargs[1].starts_with('\'') {
                jargs[1].clone()
            } else {
                format!("String.valueOf({})", jargs[1])
            };
            let start = &jargs[2];
            if jargs.len() >= 4 {
                let len = &jargs[3];
                format!(
                    "({}).substring(0, Math.max(0, ({}) - 1)) + {} + ({}).substring(Math.max(0, ({}) - 1 + ({})))",
                    s, start, repl, s, start, len
                )
            } else {
                format!("({}).substring(0, Math.max(0, ({}) - 1)) + {} + ({}).substring(Math.max(0, ({}) - 1 + {}.length()))", s, start, repl, s, start, repl)
            }
        }
        "position" if args.len() >= 2 => {
            let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
            let substr = &jargs[0];
            let s = &jargs[1];
            let substr_expr = if substr.starts_with('"') || substr.starts_with('\'') {
                substr.clone()
            } else {
                format!("String.valueOf({})", substr)
            };
            format!("(String.valueOf({}).indexOf({}) + 1)", s, substr_expr)
        }
        "interval" if args.len() >= 2 => {
            let n_expr = expr_to_java(&args[0], proc);
            let n_clean = n_expr.trim_matches('"').trim_matches('\'').to_string();
            let unit_parts = match &args[1] {
                ogsql_parser::ast::Expr::ColumnRef(parts) => parts.last().cloned().unwrap_or_default().to_lowercase(),
                _ => String::new(),
            };
            match unit_parts.as_str() {
                "hour" | "hours" => format!("java.time.Duration.ofHours((long){}).toMillis()", n_clean),
                "minute" | "minutes" => format!("java.time.Duration.ofMinutes((long){}).toMillis()", n_clean),
                "second" | "seconds" => format!("java.time.Duration.ofSeconds((long){}).toMillis()", n_clean),
                "day" | "days" => format!("java.time.Duration.ofDays((long){}).toMillis()", n_clean),
                "month" | "months" => format!("(long){} * 30L * 24L * 60L * 60L * 1000L", n_clean),
                "year" | "years" => format!("(long){} * 365L * 24L * 60L * 60L * 1000L", n_clean),
                _ => "/* INTERVAL */ 0L".to_string(),
            }
        }
        "trim" if args.len() >= 2 => {
            let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
            let direction = match &args[0] {
                ogsql_parser::ast::Expr::ColumnRef(parts) => parts.join(".").to_uppercase(),
                _ => "BOTH".into(),
            };
            match direction.as_str() {
                "LEADING" => {
                    if jargs.len() >= 3 {
                        let chars = &jargs[1];
                        let s = &jargs[2];
                        let chars_expr = if chars.starts_with('"') || chars.starts_with('\'') {
                            chars.clone()
                        } else {
                            format!("String.valueOf({})", chars)
                        };
                        if chars.trim_matches('"').trim_matches('\'') == " " {
                            format!("String.valueOf({}).replaceAll(\"^\\\\s+\", \"\")", s)
                        } else {
                            format!("String.valueOf({}).replaceAll(\"^\" + java.util.regex.Pattern.quote({}) + \"+\", \"\")", s, chars_expr)
                        }
                    } else {
                        "null".into()
                    }
                }
                "TRAILING" => {
                    if jargs.len() >= 3 {
                        let chars = &jargs[1];
                        let s = &jargs[2];
                        let chars_expr = if chars.starts_with('"') || chars.starts_with('\'') {
                            chars.clone()
                        } else {
                            format!("String.valueOf({})", chars)
                        };
                        if chars.trim_matches('"').trim_matches('\'') == " " {
                            format!("String.valueOf({}).replaceAll(\"\\\\s+$\", \"\")", s)
                        } else {
                            format!(
                                "String.valueOf({}).replaceAll(java.util.regex.Pattern.quote({}) + \"+$\", \"\")",
                                s, chars_expr
                            )
                        }
                    } else {
                        "null".into()
                    }
                }
                _ => {
                    if jargs.len() >= 3 {
                        let chars = &jargs[1];
                        let s = &jargs[2];
                        let chars_expr = if chars.starts_with('"') || chars.starts_with('\'') {
                            chars.clone()
                        } else {
                            format!("String.valueOf({})", chars)
                        };
                        if chars.trim_matches('"').trim_matches('\'') == " " {
                            format!("String.valueOf({}).trim()", s)
                        } else {
                            format!("String.valueOf({}).replaceAll(\"^\" + java.util.regex.Pattern.quote({}) + \"+|\" + java.util.regex.Pattern.quote({}) + \"+$\", \"\")", s, chars_expr, chars_expr)
                        }
                    } else {
                        "null".into()
                    }
                }
            }
        }
        _ => "null".into(),
    }
}

fn like_to_java(
    expr: &ogsql_parser::ast::Expr,
    pattern: &ogsql_parser::ast::Expr,
    negated: bool,
    proc: &ProcedureInfo,
) -> String {
    // Parser produces LIKE(a, AND('x', LIKE(b,'y'))) for `a LIKE 'x' AND b LIKE 'y'`
    if let ogsql_parser::ast::Expr::BinaryOp { left: bop_left, op, right: bop_right } = pattern {
        if op == "AND" {
            let left_java = like_to_java(expr, bop_left, negated, proc);
            let right_java = expr_to_java(bop_right, proc);
            return format!("{} && {}", left_java, right_java);
        }
    }
    let left = expr_to_java(expr, proc);
    let pat_str = match pattern {
        ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(s)) => Some(s.as_str()),
        _ => None,
    };
    if let Some(pat) = pat_str {
        if pat.starts_with('%')
            && pat.ends_with('%')
            && !pat[1..pat.len() - 1].contains('%')
            && !pat[1..pat.len() - 1].contains('_')
        {
            let inner = &pat[1..pat.len() - 1];
            if negated {
                format!("!{}.contains(\"{}\")", left, inner)
            } else {
                format!("{}.contains(\"{}\")", left, inner)
            }
        } else if pat.ends_with('%') && !pat[..pat.len() - 1].contains('%') && !pat[..pat.len() - 1].contains('_') {
            let prefix = &pat[..pat.len() - 1];
            if negated {
                format!("!{}.startsWith(\"{}\")", left, prefix)
            } else {
                format!("{}.startsWith(\"{}\")", left, prefix)
            }
        } else if pat.starts_with('%') && !pat[1..].contains('%') && !pat[1..].contains('_') {
            let suffix = &pat[1..];
            if negated {
                format!("!{}.endsWith(\"{}\")", left, suffix)
            } else {
                format!("{}.endsWith(\"{}\")", left, suffix)
            }
        } else {
            let regex = pat.replace(".", "\\.").replace("%", ".*").replace("_", ".");
            if negated {
                format!("!{}.matches(\"{}\")", left, regex)
            } else {
                format!("{}.matches(\"{}\")", left, regex)
            }
        }
    } else {
        let pattern_java = expr_to_java(pattern, proc);
        if negated {
            format!("!{}.matches({})", left, pattern_java)
        } else {
            format!("{}.matches({})", left, pattern_java)
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum BoxedNum {
    Long,
    Integer,
    Double,
    Float,
}

/// Boxed numeric type produced by `expr_str`, if it resolves to a boxed
/// numeric local. Method-call suffixes (.longValue() etc.) win over the
/// declared receiver type because they determine the produced Java type.
fn boxed_num_kind(expr_str: &str, proc: &ProcedureInfo) -> Option<BoxedNum> {
    let name = expr_str.trim();
    let lower = name.to_lowercase();
    for (suffix, kind) in [
        (".longvalue()", BoxedNum::Long),
        (".intvalue()", BoxedNum::Integer),
        (".doublevalue()", BoxedNum::Double),
        (".floatvalue()", BoxedNum::Float),
    ] {
        if lower.ends_with(suffix) {
            return Some(kind);
        }
    }
    let kind_of = |ty: &str| match ty {
        "Long" => Some(BoxedNum::Long),
        "Integer" => Some(BoxedNum::Integer),
        "Double" => Some(BoxedNum::Double),
        "Float" => Some(BoxedNum::Float),
        _ => None,
    };
    let base = name.split(['.', '(']).next().unwrap_or(name);
    if let Some(k) = proc.local_vars.get(&base.to_lowercase()).and_then(|t| kind_of(t)) {
        return Some(k);
    }
    let base_lower = base.to_lowercase().replace("_", "");
    for (var_name, var_type) in &proc.local_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower {
            return kind_of(var_type);
        }
    }
    None
}

/// Rewrite an emitted numeric literal so `Objects.equals` against a
/// `kind`-typed boxed value compares equal Java types: Long needs `L`,
/// Float needs `f`, Double needs a decimal point. Returns None when the
/// operand is not a plain numeric literal (or cannot match `kind`).
fn boxed_literal(lit: &str, kind: BoxedNum) -> Option<String> {
    let t = lit.trim();
    let (sign, mut body) = match t.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", t),
    };
    if let Some(last) = body.chars().last() {
        if matches!(last, 'f' | 'F' | 'd' | 'D') {
            body = &body[..body.len() - 1];
        }
    }
    let is_int = !body.is_empty() && body.chars().all(|c| c.is_ascii_digit());
    let is_dec = {
        let mut parts = body.splitn(2, '.');
        match (parts.next(), parts.next()) {
            (Some(a), Some(b)) => {
                !a.is_empty()
                    && !b.is_empty()
                    && a.chars().all(|c| c.is_ascii_digit())
                    && b.chars().all(|c| c.is_ascii_digit())
            }
            _ => false,
        }
    };
    if !is_int && !is_dec {
        return None;
    }
    match kind {
        BoxedNum::Integer => {
            if is_int {
                Some(format!("{}{}", sign, body))
            } else {
                None
            }
        }
        BoxedNum::Long => {
            if is_int {
                Some(format!("{}{}L", sign, body))
            } else {
                None
            }
        }
        BoxedNum::Double => {
            if is_int {
                Some(format!("{}{}.0", sign, body))
            } else {
                Some(format!("{}{}", sign, body))
            }
        }
        BoxedNum::Float => {
            if is_int {
                Some(format!("{}{}.0f", sign, body))
            } else {
                Some(format!("{}{}f", sign, body))
            }
        }
    }
}

fn type_cast_to_java(expr: &ogsql_parser::ast::Expr, type_name: &str, proc: &ProcedureInfo) -> String {
    let inner = expr_to_java(expr, proc);
    let lower = type_name.to_lowercase();
    match lower.as_str() {
        s if s.contains("bigint") || s.contains("int8") => format!("((Long) {})", inner),
        s if s.contains("integer") || s == "int" || s == "int4" => format!("((Integer) {})", inner),
        s if s.contains("numeric") || s.contains("decimal") => {
            if inner.contains("String.valueOf(") && !inner.starts_with('"') {
                "java.math.BigDecimal.ZERO".to_string()
            } else if inner.starts_with("new java.math.BigDecimal")
                || (inner.contains(".valueOf(") && inner.contains("BigDecimal"))
            {
                format!("((java.math.BigDecimal) {})", inner)
            } else if inner == "null" || inner.contains("/* aggregate:") {
                "java.math.BigDecimal.ZERO".to_string()
            } else {
                let inner_trimmed = inner.trim();
                let is_literal = inner_trimmed.chars().all(|c| c.is_ascii_digit() || c == '.');
                if is_literal && !inner_trimmed.is_empty() {
                    format!("new java.math.BigDecimal(\"{}\")", inner_trimmed)
                } else {
                    format!("java.math.BigDecimal.valueOf(((Number) java.util.Objects.requireNonNullElse({}, 0L)).longValue())", inner)
                }
            }
        }
        s if s.contains("varchar") || s.contains("text") || s.contains("char") => {
            if inner == "null" {
                "\"\"".to_string()
            } else {
                format!("String.valueOf({})", inner)
            }
        }
        s if s.contains("bool") => format!("((Boolean) {})", inner),
        s if s.contains("timestamp") => {
            if inner.starts_with('"') {
                // timestamptz '1970-01-01' — string literal must parse, not cast;
                // Timestamp.valueOf requires the time part, pad date-only literals.
                let lit = &inner[1..inner.len() - 1];
                let padded =
                    if lit.len() <= 10 && !lit.contains(':') { format!("{} 00:00:00", lit) } else { lit.to_string() };
                format!("java.sql.Timestamp.valueOf(\"{}\")", padded)
            } else {
                format!("((java.sql.Timestamp) {})", inner)
            }
        }
        s if s.contains("date") => {
            if inner.starts_with('"') {
                format!("java.sql.Date.valueOf({})", inner)
            } else if inner.contains("Timestamp") {
                format!("new java.sql.Date(({}).getTime())", inner)
            } else if inner.contains("java.time.LocalDate") || inner.contains(".toLocalDate()") {
                format!("java.sql.Date.valueOf({})", inner)
            } else if resolve_var_java_type(&inner, proc).map(|t| t.contains("Timestamp")).unwrap_or(false) {
                // A Timestamp-typed variable must not be sibling-cast to
                // java.sql.Date (Timestamp/Date both extend java.util.Date).
                format!("new java.sql.Date(({}).getTime())", inner)
            } else {
                format!("((java.sql.Date) {})", inner)
            }
        }
        s if s.contains("interval") => {
            // `(x || ' day')::interval` in a numeric context must produce the
            // numeric value, not a "N day" string that later parseLong rejects.
            if let Some((value_java, _unit)) = interval_count_unit_inner(expr, proc) {
                format!("(long)({})", value_java)
            } else {
                format!("String.valueOf({})", inner)
            }
        }
        _ => format!("String.valueOf({})", inner),
    }
}

fn is_integer_type(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(['.', '(']).next().unwrap_or(name);
    if let Some(ty) = proc.local_vars.get(&base.to_lowercase()) {
        let t = ty.as_str();
        return t == "int" || t == "long" || t == "Integer" || t == "Long";
    }
    let base_lower = base.to_lowercase().replace("_", "");
    for (var_name, var_type) in &proc.local_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower {
            let t = var_type.as_str();
            return t == "int" || t == "long" || t == "Integer" || t == "Long";
        }
    }
    for p in &proc.parameters {
        let p_lower = p.name.to_lowercase().replace("_", "");
        if p_lower == base_lower || crate::naming::snake_to_camel(&p.name) == base {
            return p.java_type == "int" || p.java_type == "long" || p.java_type == "Integer" || p.java_type == "Long";
        }
    }
    if let Some(vi) = proc.package_vars.get(base) {
        let t = vi.java_type.as_str();
        return t == "int" || t == "long" || t == "Integer" || t == "Long";
    }
    false
}

fn case_to_java(
    operand: &Option<Box<ogsql_parser::ast::Expr>>,
    whens: &[ogsql_parser::ast::WhenClause],
    else_expr: &Option<Box<ogsql_parser::ast::Expr>>,
    proc: &ProcedureInfo,
) -> String {
    let mut parts = Vec::new();
    let results_java: Vec<String> = whens.iter().map(|w| expr_to_java(&w.result, proc)).collect();
    let else_java = else_expr.as_ref().map(|e| expr_to_java(e, proc));
    let has_bd_branch = results_java.iter().any(|r| is_bigdecimal_var(r, proc))
        || else_java.as_ref().is_some_and(|e| is_bigdecimal_var(e, proc));
    for (i, when) in whens.iter().enumerate() {
        let cond = match operand {
            Some(op) => {
                let op_java = expr_to_java(op, proc);
                let when_java = expr_to_java(&when.condition, proc);
                if is_integer_type(&op_java, proc) || is_integer_type(&when_java, proc) {
                    format!("{} == {}", op_java, when_java)
                } else {
                    format!("{}.equals({})", op_java, when_java)
                }
            }
            None => expr_to_java(&when.condition, proc),
        };
        let result = if has_bd_branch && !is_bigdecimal_var(&results_java[i], proc) {
            wrap_bigdecimal(&results_java[i], false, proc)
        } else {
            results_java[i].clone()
        };
        if i == 0 {
            parts.push(format!("({} ? {} ", cond, result));
        } else {
            parts.push(format!(": {} ? {} ", cond, result));
        }
    }
    match &else_java {
        Some(ej) => {
            let result = if has_bd_branch && !is_bigdecimal_var(ej, proc) {
                wrap_bigdecimal(ej, false, proc)
            } else {
                ej.clone()
            };
            parts.push(format!(": {})", result));
        }
        None => parts.push(": null)".into()),
    }
    parts.join("")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_proc() -> ProcedureInfo {
        ProcedureInfo::new("pkg.test".into(), "pkg".into(), "test".into())
    }

    #[test]
    fn test_resolve_var_java_type_maps_rendered_camel_case_to_snake_case() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_flag".into(), "String".into());

        assert_eq!(resolve_var_java_type("vFlag", &proc), Some("String".into()));
    }

    #[test]
    fn test_produces_double_detects_decimal_and_double_operations() {
        assert!(produces_double("vFlag * 0.5"));
        assert!(produces_double("Double.parseDouble(vFlag) * 2"));
        assert!(produces_double("total / count"));
        assert!(produces_double("Math.sqrt(total)"));
        assert!(produces_double("value * 1.0e3"));
    }

    #[test]
    fn test_produces_double_preserves_integral_arithmetic_passthrough() {
        assert!(!produces_double("left + right"));
        assert!(!produces_double("left * 2"));
    }

    #[test]
    fn test_literal_integer() {
        let proc = empty_proc();
        assert_eq!(
            expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(42)), &proc),
            "42"
        );
    }

    #[test]
    fn test_literal_string() {
        let proc = empty_proc();
        assert_eq!(
            expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("hello".into())), &proc),
            "\"hello\""
        );
    }

    #[test]
    fn test_literal_boolean_true() {
        let proc = empty_proc();
        assert_eq!(
            expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(true)), &proc),
            "true"
        );
    }

    #[test]
    fn test_literal_boolean_false() {
        let proc = empty_proc();
        assert_eq!(
            expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(false)), &proc),
            "false"
        );
    }

    #[test]
    fn test_literal_null() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null), &proc), "null");
    }

    #[test]
    fn test_and_or_null_operand_degrades_to_false() {
        // `x && NULL` / `x || NULL` cannot compile (SQL NULL is not a Java
        // boolean). In boolean context NULL is falsy -> `false` operand; the
        // delivery postprocess patched `&& (null)` -> `&& false` the same way.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_flag".into(), "boolean".into());
        let flag =
            || Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_flag".into()])) as Box<ogsql_parser::ast::Expr>;
        let nul = || Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null));
        let and = ogsql_parser::ast::Expr::BinaryOp { left: flag(), op: "AND".into(), right: nul() };
        assert_eq!(expr_to_java(&and, &proc), "(vFlag && false)");
        let or = ogsql_parser::ast::Expr::BinaryOp { left: flag(), op: "OR".into(), right: nul() };
        assert_eq!(expr_to_java(&or, &proc), "(vFlag || false)");
        // Parenthesized NULL (the delivery-observed `(null)` shape) too.
        let paren_null = || {
            Box::new(ogsql_parser::ast::Expr::Parenthesized(Box::new(ogsql_parser::ast::Expr::Literal(
                ogsql_parser::ast::Literal::Null,
            )))) as Box<ogsql_parser::ast::Expr>
        };
        let and_p = ogsql_parser::ast::Expr::BinaryOp { left: flag(), op: "AND".into(), right: paren_null() };
        assert_eq!(expr_to_java(&and_p, &proc), "(vFlag && false)");
        // Non-null operands pass through unchanged.
        let and_ok = ogsql_parser::ast::Expr::BinaryOp {
            left: flag(),
            op: "AND".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(true))),
        };
        assert_eq!(expr_to_java(&and_ok, &proc), "(vFlag && true)");
    }

    #[test]
    fn test_column_ref_simple() {
        // v_status is declared as a local var: Task 6 removes the compile-unsafe
        // "any bare name → camel" fallback, so this must resolve via a known
        // variable, not merely rely on being an unresolved-name coincidence.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_status".into(), "String".into());
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["v_status".into()]), &proc), "vStatus");
    }

    #[test]
    fn test_column_ref_found() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["FOUND".into()]), &proc), "found");
    }

    #[test]
    fn test_column_ref_null() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["NULL".into()]), &proc), "null");
    }

    #[test]
    fn test_type_cast_timestamp_var_to_date_uses_get_time() {
        // #103 DwdService:100/137 — `vM::date` where vM is a Timestamp local must
        // compile as new java.sql.Date(vM.getTime()); java.sql.Timestamp and
        // java.sql.Date are sibling classes, so ((java.sql.Date) vM) fails javac.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_m".into(), "java.sql.Timestamp".into());
        let cast = ogsql_parser::ast::Expr::TypeCast {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_m".into()])),
            type_name: ogsql_parser::ast::DataType::Date,
            default: None,
            format: None,
        };
        let out = expr_to_java(&cast, &proc);
        assert!(out.starts_with("new java.sql.Date("), "Timestamp var cast to date must use getTime(), got: {}", out);
        assert!(!out.contains("(java.sql.Date) vM"), "must not sibling-cast Timestamp to Date, got: {}", out);
    }

    #[test]
    fn test_assignment_out_integer_param_long_expr_uses_int_value() {
        // #103 DwdService:102/139 — `o_months := o_months + 1` produces a long
        // expression (safe_long_value), but oMonths is AtomicReference<Integer>;
        // .set(long) fails javac — must narrow with ((Number)(...)).intValue().
        let mut proc = empty_proc();
        let p = crate::types::Parameter {
            name: "o_months".into(),
            java_type: "Integer".into(),
            sql_type: "number".into(),
            mode: Some(crate::types::ParamMode::Out),
            default_value: None,
        };
        proc.parameters.push(p);
        let target = ogsql_parser::ast::Expr::PlVariable(vec!["o_months".into()]);
        let value = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::PlVariable(vec!["o_months".into()])),
            op: "+".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(1))),
        };
        let out = assignment_to_java(&target, &value, &proc);
        assert!(out.contains(".intValue()"), "Integer OUT param must narrow long expression, got: {}", out);
        assert!(out.starts_with("oMonths.set("), "must use .set(), got: {}", out);
    }

    #[test]
    fn test_integer_narrow_does_not_match_100l_substring() {
        // (#114 review minor 9): `contains("0L")` matched the substring inside
        // 100L/1000L, wrongly narrowing them to int. Only the exact `0L` literal
        // (or a long-producing call) must be narrowed.
        assert!(is_long_literal("0L"));
        assert!(!is_long_literal("100L"));
        assert!(!is_long_literal("1000L"));
        assert!(!is_long_literal("999L"));
    }

    #[test]
    fn test_at_time_zone_expr_generates_zone_chain() {
        // ogagila EtlCore:63 — `now() AT TIME ZONE 'UTC'` must map to the
        // atZone(ZoneId.of(...)) chain; the previous fall-through emitted `null`
        // → `null.toInstant()` compile error.
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::AtTimeZone {
            expr: Box::new(ogsql_parser::ast::Expr::FunctionCall {
                name: vec!["now".into()],
                args: vec![],
                distinct: false,
                over: None,
                filter: None,
                within_group: vec![],
                separator: None,
                default: None,
                conversion_format: None,
                builtin: None,
                agg_from: None,
            }),
            zone: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("UTC".into()))),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("atZone(java.time.ZoneId.of(\"UTC\"))"), "got: {}", out);
        assert!(!out.contains("null.toInstant"), "must not emit null.toInstant, got: {}", out);
        assert!(!out.starts_with("null"), "AtTimeZone must not fall through to null, got: {}", out);
    }

    #[test]
    fn test_at_time_zone_wraps_date_input_before_to_instant() {
        // (#114 review minor 4): `d AT TIME ZONE 'UTC'` where d is java.sql.Date —
        // Date.toInstant() throws UnsupportedOperationException, so the input
        // must be wrapped in a Timestamp first (mirrors the DATE_TRUNC guard).
        let mut proc = empty_proc();
        proc.local_vars.insert("v_d".into(), "java.sql.Date".into());
        let expr = ogsql_parser::ast::Expr::AtTimeZone {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_d".into()])),
            zone: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("UTC".into()))),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(
            out.contains("new java.sql.Timestamp(vD.getTime()).toInstant()"),
            "Date input must be wrapped in Timestamp before toInstant, got: {}",
            out
        );
    }

    #[test]
    fn test_binary_op_promoted_local_deref() {
        // fastaas SplitTradeStep3Sh:4118 — `cjje * v_return_rate_org` where the
        // RHS is a promoted local (AtomicReference) must deref to .get().
        let mut proc = empty_proc();
        proc.out_local_vars.insert("v_return_rate_org".into(), "AtomicReference<Long>".into());
        proc.local_vars.insert("v_return_rate_org".into(), "Long".into());
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(5))),
            op: "*".into(),
            right: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_return_rate_org".into()])),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("vReturnRateOrg.get()"), "promoted var must be deref'd in arithmetic, got: {}", out);
    }

    #[test]
    fn test_binary_op_derefs_pending_promotion() {
        // fastaas SplitTradeStep3Sh:4118 — promotion is queued by an earlier
        // cross-pkg OUT call but only drained at analyze end; arithmetic
        // processed in between must still deref via the pending queue.
        crate::expr::queue_out_promotion("v_return_rate_org".into(), "AtomicReference<Long>".into());
        let mut proc = empty_proc();
        proc.local_vars.insert("v_return_rate_org".into(), "Long".into());
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(5))),
            op: "*".into(),
            right: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_return_rate_org".into()])),
        };
        let out = expr_to_java(&expr, &proc);
        // (#115 review #10): drain before the assert — if the assert panics the
        // thread-local queue must still be empty or it leaks into other tests.
        let drained = crate::expr::take_pending_out_promotions();
        assert!(drained.len() == 1, "expected one queued promotion, got: {:?}", drained);
        assert!(out.contains("vReturnRateOrg.get()"), "queued promotion must be deref'd in arithmetic, got: {}", out);
    }

    #[test]
    fn test_assignment_rhs_derefs_promoted_var() {
        // M5 (#114 review): `v_sum := v_return_rate_org` where the RHS is a
        // promoted local (AtomicReference<Long>) must deref to the value —
        // a bare AtomicReference on the RHS fails javac.
        let mut proc = empty_proc();
        proc.out_local_vars.insert("v_return_rate_org".into(), "AtomicReference<Long>".into());
        proc.local_vars.insert("v_return_rate_org".into(), "Long".into());
        proc.local_vars.insert("v_sum".into(), "Long".into());
        let target = ogsql_parser::ast::Expr::PlVariable(vec!["v_sum".into()]);
        let value = ogsql_parser::ast::Expr::PlVariable(vec!["v_return_rate_org".into()]);
        let out = assignment_to_java(&target, &value, &proc);
        assert!(out.contains("vReturnRateOrg.get()"), "assignment RHS must deref promoted var, got: {}", out);
    }

    #[test]
    fn test_bool_cond_derefs_promoted_var() {
        // M5 (#114 review): `IF v_flag THEN` where v_flag is a promoted local
        // (AtomicReference<Boolean>) must deref — a bare ref is always non-null
        // and wrong for the condition.
        let mut proc = empty_proc();
        proc.out_local_vars.insert("v_flag".into(), "AtomicReference<Boolean>".into());
        proc.local_vars.insert("v_flag".into(), "Boolean".into());
        let expr = ogsql_parser::ast::Expr::ColumnRef(vec!["v_flag".into()]);
        let out = bool_expr_to_java(&expr, &proc);
        assert!(out == "vFlag.get()", "boolean condition must deref promoted var, got: {}", out);
    }

    #[test]
    fn test_rowtype_field_access_typed_extraction() {
        // ogagila plan_increment: v_wm is dw.etl_watermark%ROWTYPE; field
        // access must emit typed extraction (Timestamp cast / null-safe Long).
        let mut proc = empty_proc();
        proc.local_vars.insert("v_wm".into(), "Map<String, Object>".into());
        let mut fields = std::collections::HashMap::new();
        fields.insert("wm_ts_value".to_string(), "java.sql.Timestamp".to_string());
        fields.insert("wm_id_value".to_string(), "Long".to_string());
        fields.insert("src_table".to_string(), "String".to_string());
        proc.rowtype_field_types.insert("v_wm".into(), fields);

        let ts = resolve_column_ref("v_wm.wm_ts_value", &proc);
        assert!(ts.contains("((java.sql.Timestamp)"), "got: {}", ts);
        assert!(ts.contains("getOrDefault"), "got: {}", ts);

        let id = resolve_column_ref("v_wm.wm_id_value", &proc);
        assert!(
            id.contains("vWm.getOrDefault(\"wmIdValue\", vWm.get(\"wm_id_value\")) == null ? null : ((Number) vWm.getOrDefault(\"wmIdValue\", vWm.get(\"wm_id_value\"))).longValue()"),
            "got: {}",
            id
        );

        let st = resolve_column_ref("v_wm.src_table", &proc);
        assert_eq!(
            st, "vWm.getOrDefault(\"srcTable\", vWm.get(\"src_table\"))",
            "String field stays raw for null checks: {}",
            st
        );
    }

    #[test]
    fn test_binary_sub_rowtype_timestamp_field_uses_ts_branch() {
        // o_ts_from := v_wm.wm_ts_value - <interval>: the typed field access
        // produces ((java.sql.Timestamp) ...) which must route to the timestamp
        // subtraction branch, NOT the (Number) fallback.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_wm".into(), "Map<String, Object>".into());
        let mut fields = std::collections::HashMap::new();
        fields.insert("wm_ts_value".to_string(), "java.sql.Timestamp".to_string());
        proc.rowtype_field_types.insert("v_wm".into(), fields);
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_wm".into(), "wm_ts_value".into()])),
            op: "-".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(5))),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("new java.sql.Timestamp("), "timestamp subtraction branch must fire, got: {}", out);
        assert!(!out.contains("(Number)"), "no (Number) cast on a Timestamp operand, got: {}", out);
    }

    #[test]
    fn test_interval_cast_numeric() {
        let proc = empty_proc();
        // (v_wm.lookback_days || ' day')::interval must not parseLong the string
        let cast = ogsql_parser::ast::Expr::TypeCast {
            expr: Box::new(ogsql_parser::ast::Expr::BinaryOp {
                left: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_wm".into(), "lookback_days".into()])),
                op: "||".into(),
                right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(" day".into()))),
            }),
            type_name: ogsql_parser::ast::DataType::Interval(None),
            default: None,
            format: None,
        };
        let out = expr_to_java(&cast, &proc);
        assert!(!out.contains("parseLong"), "no parseLong on interval, got: {}", out);
    }

    #[test]
    fn test_date_plus_interval_year_uses_to_local_date() {
        // (#115 review #8): `Date + interval '1 year'` must stay java.sql.Date —
        // java.sql.Date has no toLocalDateTime, so the old year branch (which
        // always emitted Timestamp) produced a compile error.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_d".into(), "java.sql.Date".into());
        let date_expr = ogsql_parser::ast::Expr::TypeCast {
            expr: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("2024-01-01".into()))),
            type_name: ogsql_parser::ast::DataType::Date,
            default: None,
            format: None,
        };
        let year_expr = ogsql_parser::ast::Expr::TypeCast {
            expr: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("1 year".into()))),
            type_name: ogsql_parser::ast::DataType::Interval(None),
            default: None,
            format: None,
        };
        let expr =
            ogsql_parser::ast::Expr::BinaryOp { left: Box::new(date_expr), op: "+".into(), right: Box::new(year_expr) };
        let out = expr_to_java(&expr, &proc);
        assert!(
            out.contains(".toLocalDate().plusYears("),
            "Date + interval year must use toLocalDate().plusYears, got: {}",
            out
        );
    }

    #[test]
    fn test_is_null() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_status".into(), "String".into());
        let expr = ogsql_parser::ast::Expr::IsNull {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_status".into()])),
            negated: false,
        };
        assert_eq!(expr_to_java(&expr, &proc), "vStatus == null");
    }

    #[test]
    fn test_is_not_null() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_status".into(), "String".into());
        let expr = ogsql_parser::ast::Expr::IsNull {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_status".into()])),
            negated: true,
        };
        assert_eq!(expr_to_java(&expr, &proc), "vStatus != null");
    }

    #[test]
    fn test_binary_op_and() {
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(true))),
            op: "AND".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(false))),
        };
        assert_eq!(expr_to_java(&expr, &proc), "(true && false)");
    }

    #[test]
    fn test_unary_negate() {
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::UnaryOp {
            op: "-".into(),
            expr: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(42))),
        };
        assert_eq!(expr_to_java(&expr, &proc), "(-42)");
    }

    #[test]
    fn test_parenthesized() {
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::Parenthesized(Box::new(ogsql_parser::ast::Expr::Literal(
            ogsql_parser::ast::Literal::Integer(1),
        )));
        assert_eq!(expr_to_java(&expr, &proc), "(1)");
    }

    #[test]
    fn test_column_ref_bare_prefixed_function_resolves_via_registry_not_prefix() {
        // Task 5: fnc_/fn_ prefixed bare zero-arg names resolve through the
        // cross-package function registry — no name-prefix heuristic is consulted.
        let mut proc = empty_proc();
        proc.all_proc_params.insert(
            "fncComGetday".into(),
            vec![GlobalFnEntry {
                svc_var: "issue79UnqualifiedFnCalleeService".into(),
                package: "issue_79_unqualified_fn_callee".into(),
                params: vec![],
                return_type: None,
                param_sig_types: Vec::new(),
            }],
        );
        assert_eq!(
            expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["fnc_com_getday".into()]), &proc),
            "issue79UnqualifiedFnCalleeService.fncComGetday()"
        );
    }

    #[test]
    fn test_column_ref_local_var_shadows_same_name_zero_arg_function() {
        // PL/SQL shadowing: a declared local variable always wins over a
        // same-named sibling zero-arg procedure — variable lookup runs first.
        let mut proc = empty_proc();
        proc.local_vars.insert("fn_get_day".into(), "String".into());
        proc.package_proc_params.insert("fnGetDay".into(), vec![vec![]]);
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["fn_get_day".into()]), &proc), "fnGetDay");
    }

    #[test]
    fn test_column_ref_unknown_bare_name_falls_back_to_tobefix_null() {
        // Task 6: unknown bare identifier — absent from parameters/local vars/package
        // vars and from both function registries — must not emit a bare, undeclared
        // camelCase identifier (that doesn't compile). It emits a compile-safe
        // TOBEFIX-marked `null` instead, and the marker text carries enough info
        // (name + source file + proc) for the report scan (analyze::collect_tobefix_warnings).
        let proc = empty_proc();
        let out = expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["some_unknown_thing".into()]), &proc);
        assert!(out.contains("/* TOBEFIX: unresolved name some_unknown_thing(no args)"), "got: {}", out);
        assert!(out.trim_end().ends_with("null"), "got: {}", out);
    }

    #[test]
    fn test_column_ref_package_var_shadows_same_name_zero_arg_function() {
        // PL/SQL shadowing: a declared package variable (not just local var) also
        // wins over a same-named sibling zero-arg procedure — package var lookup
        // must run before function registry lookup (Task 5 review 🟡 follow-up).
        let mut proc = empty_proc();
        proc.package_vars.insert(
            "fn_get_day".into(),
            crate::types::VarInfo {
                name: "fn_get_day".into(),
                java_type: "String".into(),
                sql_type: "varchar".into(),
                default_value: None,
                is_constant: false,
            },
        );
        proc.package_proc_params.insert("fnGetDay".into(), vec![vec![]]);
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["fn_get_day".into()]), &proc), "fnGetDay");
    }

    #[test]
    fn test_column_ref_underscore_normalization_prefers_declared_camel_var() {
        // A local var declared under its camelCase key ("fnGetDay") must still
        // shadow a same-named (snake_case-referenced) sibling zero-arg function
        // via the underscore-insensitive variable lookup (Task 5 review 🟡 follow-up).
        let mut proc = empty_proc();
        proc.local_vars.insert("fnGetDay".into(), "String".into());
        proc.package_proc_params.insert("fnGetDay".into(), vec![vec![]]);
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::ColumnRef(vec!["fn_get_day".into()]), &proc), "fnGetDay");
    }

    #[test]
    fn test_coerce_for_type_wraps_localdate_expr_for_sql_date_target() {
        // #97: assigning a LocalDate-producing expression to a java.sql.Date
        // variable must wrap with java.sql.Date.valueOf(...), otherwise javac
        // reports 不兼容的类型.
        let proc = empty_proc();
        let input = "java.time.LocalDate.parse(\"2026-05-01\").withDayOfMonth(31)";
        let out = coerce_for_type(input, Some("java.sql.Date"), &proc);
        assert_eq!(out, format!("java.sql.Date.valueOf({})", input));
        // A plain LocalDate.now() must also be wrapped.
        let out2 = coerce_for_type("java.time.LocalDate.now()", Some("java.sql.Date"), &proc);
        assert_eq!(out2, "java.sql.Date.valueOf(java.time.LocalDate.now())");
        // An already-wrapped valueOf(...) must NOT be wrapped a second time.
        let out3 = coerce_for_type("java.sql.Date.valueOf(java.time.LocalDate.now())", Some("java.sql.Date"), &proc);
        assert_eq!(out3, "java.sql.Date.valueOf(java.time.LocalDate.now())");
    }

    #[test]
    fn test_coerce_for_type_preserves_tobefix_marker_terminator() {
        // Task 6 regression: an unresolved bare-name TOBEFIX marker must survive
        // coerce_for_type with its `*/` terminator intact. A dropped `/` leaves an
        // unterminated block comment that swallows the rest of the line (and the
        // null literal), producing non-compiling Java.
        let proc = empty_proc();
        let input = "/* TOBEFIX: unresolved name some_unknown_thing(no args) - pkg=?, caller=tests/regress/fixtures/x.sql:some_proc */ null";
        let out = coerce_for_type(input, Some("String"), &proc);
        assert!(out.contains("*/"), "TOBEFIX comment lost its `*/` terminator: {}", out);
        assert!(out.ends_with("null"), "TOBEFIX value must remain null: {}", out);
    }

    #[test]
    fn test_cross_pkg_ambiguity_falls_back_to_tobefix_with_candidates() {
        // Task 4/6 + #127: two packages defining the same method with the same
        // arity but DIFFERENT signatures is genuinely ambiguous — the
        // converter must NOT pick one silently. It falls back to a TOBEFIX
        // marker that lists each candidate's signature. (Identical signatures
        // now resolve to the first candidate — see
        // test_cross_pkg_identical_signatures_pick_first_with_note.)
        let mut proc = empty_proc();
        let entry = |svc: &str, pkg: &str, jt: &str| GlobalFnEntry {
            svc_var: svc.into(),
            package: pkg.into(),
            params: vec![crate::types::Parameter {
                name: "x".into(),
                java_type: jt.into(),
                sql_type: "varchar2".into(),
                mode: None,
                default_value: None,
            }],
            return_type: None,
            param_sig_types: Vec::new(),
        };
        proc.all_proc_params.insert(
            "getVal".into(),
            vec![entry("pkgAService", "pkg_a", "String"), entry("pkgBService", "pkg_b", "Long")],
        );
        let expr = ogsql_parser::ast::Expr::FunctionCall {
            name: vec!["get_val".into()],
            args: vec![ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("s".into()))],
            distinct: false,
            over: None,
            filter: None,
            within_group: Vec::new(),
            separator: None,
            default: None,
            conversion_format: None,
            agg_from: None,
            builtin: None,
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "ambiguous call must not resolve silently: {}", out);
        assert!(out.contains("pkgAService"), "candidate A must be listed: {}", out);
        assert!(out.contains("pkgBService"), "candidate B must be listed: {}", out);
    }

    #[test]
    fn test_qualified_call_to_unknown_package_falls_back_to_tobefix() {
        // Qualified `pkg.func(...)` where pkg is not among the sources must NOT emit
        // `{pkg}Service.m(...)` — that service is never generated nor injected, so the
        // Java does not compile. It must degrade to a TOBEFIX-marked `null` like the
        // unqualified unresolved path.
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::FunctionCall {
            name: vec!["pkg_not_in_sources".into(), "some_fn".into()],
            args: vec![],
            distinct: false,
            over: None,
            filter: None,
            within_group: Vec::new(),
            separator: None,
            default: None,
            conversion_format: None,
            agg_from: None,
            builtin: None,
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "unknown qualified pkg must not emit a service call: {}", out);
        assert!(out.trim_end().ends_with("null"), "got: {}", out);
        assert!(!out.contains("Service."), "must not reference a non-existent service: {}", out);
    }

    #[test]
    fn test_qualified_call_to_known_package_resolves_via_registry() {
        // Same qualified shape, but the package IS in the registry: the call must
        // resolve to the canonical svc_var from the GlobalFnEntry.
        let mut proc = empty_proc();
        proc.all_proc_params.insert(
            "fncComGetday".into(),
            vec![GlobalFnEntry {
                svc_var: "pkgCommonToolService".into(),
                package: "pkg_common_tool".into(),
                params: vec![],
                return_type: None,
                param_sig_types: Vec::new(),
            }],
        );
        let expr = ogsql_parser::ast::Expr::FunctionCall {
            name: vec!["pkg_common_tool".into(), "fnc_com_getday".into()],
            args: vec![],
            distinct: false,
            over: None,
            filter: None,
            within_group: Vec::new(),
            separator: None,
            default: None,
            conversion_format: None,
            agg_from: None,
            builtin: None,
        };
        let out = expr_to_java(&expr, &proc);
        assert_eq!(out, "pkgCommonToolService.fncComGetday()");
    }

    #[test]
    fn test_quote_literal_escapes_single_quotes() {
        // (#115 review #5): quote_literal(x) feeds a SQL string literal — a raw
        // single quote would break out of it (injection surface). Must escape.
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::FunctionCall {
            name: vec!["quote_literal".into()],
            args: vec![ogsql_parser::ast::Expr::ColumnRef(vec!["v_name".into()])],
            distinct: false,
            over: None,
            filter: None,
            within_group: Vec::new(),
            separator: None,
            default: None,
            conversion_format: None,
            agg_from: None,
            builtin: None,
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains(".replace(\"'\", \"''\")"), "quote_literal must escape single quotes, got: {}", out);
    }

    fn cmp_expr(left: &str, op: &str, right_lit: i64) -> ogsql_parser::ast::Expr {
        ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec![left.into()])),
            op: op.into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(right_lit))),
        }
    }

    #[test]
    fn test_boxed_long_var_equals_int_literal_emits_long_literal() {
        // Objects.equals(Long, int) is always false — the literal must carry
        // the boxed type's suffix or every `v_flag = 0` branch is dead.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_flag".into(), "Long".into());
        let out = expr_to_java(&cmp_expr("v_flag", "=", 0), &proc);
        assert_eq!(out, "java.util.Objects.equals(vFlag, 0L)");
    }

    #[test]
    fn test_boxed_integer_var_equals_int_literal_stays_plain() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_count".into(), "Integer".into());
        let out = expr_to_java(&cmp_expr("v_count", "=", 0), &proc);
        assert_eq!(out, "java.util.Objects.equals(vCount, 0)");
    }

    #[test]
    fn test_boxed_double_and_float_vars_equals_int_literal() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_dbl".into(), "Double".into());
        proc.local_vars.insert("v_flt".into(), "Float".into());
        assert_eq!(expr_to_java(&cmp_expr("v_dbl", "=", 0), &proc), "java.util.Objects.equals(vDbl, 0.0)");
        assert_eq!(expr_to_java(&cmp_expr("v_flt", "=", 0), &proc), "java.util.Objects.equals(vFlt, 0.0f)");
    }

    #[test]
    fn test_boxed_long_var_not_equals_int_literal() {
        // `!=` must mirror `=`: null-safe AND type-correct (was `vFlag != 0`,
        // which unboxes → NPE on null and compares correctly only by accident).
        let mut proc = empty_proc();
        proc.local_vars.insert("v_flag".into(), "Long".into());
        let out = expr_to_java(&cmp_expr("v_flag", "!=", 1), &proc);
        assert_eq!(out, "!java.util.Objects.equals(vFlag, 1L)");
    }

    #[test]
    fn test_boxed_vars_same_kind_use_objects_equals() {
        // Two Long locals compared with `==` is reference equality — wrong for
        // values outside the Integer cache. Same-kind boxed pairs must use
        // Objects.equals / !Objects.equals.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_a".into(), "Long".into());
        proc.local_vars.insert("v_b".into(), "Long".into());
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_a".into()])),
            op: "=".into(),
            right: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_b".into()])),
        };
        assert_eq!(expr_to_java(&expr, &proc), "java.util.Objects.equals(vA, vB)");
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_a".into()])),
            op: "<>".into(),
            right: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_b".into()])),
        };
        assert_eq!(expr_to_java(&expr, &proc), "!java.util.Objects.equals(vA, vB)");
    }

    #[test]
    fn test_boxed_long_var_equals_literal_on_left() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_flag".into(), "Long".into());
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(3))),
            op: "=".into(),
            right: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_flag".into()])),
        };
        assert_eq!(expr_to_java(&expr, &proc), "java.util.Objects.equals(3L, vFlag)");
    }

    // ── Issue #124: PL/SQL collection lowering ──

    fn fc_expr(name: &[&str], args: Vec<ogsql_parser::ast::Expr>) -> ogsql_parser::ast::Expr {
        ogsql_parser::ast::Expr::FunctionCall {
            name: name.iter().map(|s| ogsql_parser::ast::Ident::new(*s)).collect(),
            args,
            distinct: false,
            over: None,
            filter: None,
            within_group: Vec::new(),
            separator: None,
            default: None,
            conversion_format: None,
            agg_from: None,
            builtin: None,
        }
    }

    fn col_expr(names: &[&str]) -> ogsql_parser::ast::Expr {
        ogsql_parser::ast::Expr::ColumnRef(names.iter().map(|s| ogsql_parser::ast::Ident::new(*s)).collect())
    }

    fn int_lit(n: i64) -> ogsql_parser::ast::Expr {
        ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(n))
    }

    #[test]
    fn test_collection_element_access_uses_one_based_get() {
        // `v_coll(3)` on a `List<Long>` var → bounds-guarded 1-based get.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_coll".into(), "java.util.List<Long>".into());
        let out = expr_to_java(&fc_expr(&["v_coll"], vec![int_lit(3)]), &proc);
        assert_eq!(out, "((3) >= 1 && (3) <= (vColl).size() ? vColl.get((int)(long)(3) - 1) : null)");
    }

    #[test]
    fn test_collection_element_access_via_package_var() {
        // Package-level collection vars resolve through package_vars too.
        let mut proc = empty_proc();
        proc.package_vars.insert(
            "g_coll".into(),
            crate::types::VarInfo {
                name: "g_coll".into(),
                java_type: "java.util.List<String>".into(),
                sql_type: "num_table".into(),
                default_value: None,
                is_constant: false,
            },
        );
        let out = expr_to_java(&fc_expr(&["g_coll"], vec![int_lit(1)]), &proc);
        assert_eq!(out, "((1) >= 1 && (1) <= (gColl).size() ? gColl.get((int)(long)(1) - 1) : null)");
    }

    #[test]
    fn test_collection_exists_next_prior_expr() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_coll".into(), "java.util.List<Long>".into());
        proc.local_vars.insert("v_i".into(), "Long".into());
        let out = expr_to_java(&fc_expr(&["v_coll", "exists"], vec![col_expr(&["v_i"])]), &proc);
        assert!(out.contains(".size()") && out.contains(">= 1"), "EXISTS must check bounds: {}", out);
        let out = expr_to_java(&fc_expr(&["v_coll", "next"], vec![col_expr(&["v_i"])]), &proc);
        assert!(out.contains("+ 1") && out.contains(".size()"), "NEXT(i) → i+1 or null: {}", out);
        let out = expr_to_java(&fc_expr(&["v_coll", "prior"], vec![col_expr(&["v_i"])]), &proc);
        assert!(out.contains("- 1"), "PRIOR(i) → i-1 or null: {}", out);
    }

    #[test]
    fn test_collection_first_last_count_attrs() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_coll".into(), "java.util.List<Long>".into());
        let out = expr_to_java(&col_expr(&["v_coll", "first"]), &proc);
        assert!(out.contains("isEmpty()") && out.contains("Long.valueOf(1)"), "FIRST: {}", out);
        let out = expr_to_java(&col_expr(&["v_coll", "last"]), &proc);
        assert!(out.contains("isEmpty()") && out.contains(".size()"), "LAST: {}", out);
        let out = expr_to_java(&col_expr(&["v_coll", "count"]), &proc);
        assert!(out.contains(".size()"), "COUNT: {}", out);
    }

    #[test]
    fn test_collection_methods_do_not_fire_on_non_list_vars() {
        // `v_map.DELETE`-style names on a record (Map) var must keep the
        // record field-access path, not the collection-method path.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_rec".into(), "Map<String, Object>".into());
        let out = expr_to_java(&col_expr(&["v_rec", "first"]), &proc);
        assert!(!out.contains("isEmpty()"), "record field access must not become collection FIRST: {}", out);
    }

    #[test]
    fn test_unary_minus_bigdecimal_uses_negate() {
        // Review #129 P1-4: `-v_amt` on a BigDecimal must emit .negate(),
        // not Java unary minus (which BigDecimal does not support).
        let mut proc = empty_proc();
        proc.local_vars.insert("v_amt".into(), "java.math.BigDecimal".into());
        let expr = ogsql_parser::ast::Expr::UnaryOp {
            op: "-".into(),
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_amt".into()])),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains(".negate()"), "BigDecimal unary minus must use negate(): {}", out);
        assert!(!out.starts_with("(-"), "no Java unary minus on BigDecimal: {}", out);
    }

    #[test]
    fn test_coerce_localdate_expr_to_sql_date() {
        // Review #129 P0-1: temporal lowering ends in `.toLocalDate()` (a
        // LocalDate) — assigning to java.sql.Date must wrap in Date.valueOf.
        let proc = empty_proc();
        let expr = "new java.sql.Date((new java.sql.Timestamp(System.currentTimeMillis())).getTime()).toLocalDate().plusMonths(Long.parseLong(String.valueOf(1)))";
        let out = coerce_for_type(expr, Some("java.sql.Date"), &proc);
        assert!(out.starts_with("java.sql.Date.valueOf("), "LocalDate expr must be wrapped: {}", out);
        assert!(out.ends_with(')'), "wrapped expr must close: {}", out);
    }

    #[test]
    fn test_expr_yields_localdate_structural() {
        // Review #129 §3-A: substring vetoes misfire — an expr containing
        // `java.sql.Date.valueOf(` can still yield LocalDate, and a bare
        // `.toLocalDate()` can be nested inside String.valueOf(...).
        assert!(expr_yields_localdate(
            "new java.sql.Date((vD).getTime()).toLocalDate().plusMonths(Long.parseLong(String.valueOf(1)))"
        ));
        assert!(expr_yields_localdate(
            "new java.sql.Date((vD).getTime()).toLocalDate().withDayOfMonth(new java.sql.Date((vD).getTime()).toLocalDate().lengthOfMonth())"
        ));
        assert!(expr_yields_localdate("x.toLocalDate()"));
        assert!(!expr_yields_localdate("String.valueOf(x.toLocalDate())"));
        assert!(!expr_yields_localdate("x.toLocalDate().toString()"));
        assert!(!expr_yields_localdate("new java.sql.Timestamp(System.currentTimeMillis())"));
        assert!(!expr_yields_localdate("vD"));
    }

    #[test]
    fn test_localdate_operand_to_ts_wraps_only_localdate() {
        let localdate = "new java.sql.Date((vD).getTime()).toLocalDate().plusMonths(1L)";
        let wrapped = localdate_operand_to_ts(localdate);
        assert!(wrapped.starts_with("new java.sql.Timestamp(java.sql.Date.valueOf("), "got: {}", wrapped);
        // Already-Date exprs and non-localdate exprs pass through.
        assert_eq!(localdate_operand_to_ts("vD"), "vD");
        assert_eq!(
            localdate_operand_to_ts("new java.sql.Timestamp(System.currentTimeMillis())"),
            "new java.sql.Timestamp(System.currentTimeMillis())"
        );
        assert_eq!(localdate_operand_to_ts("String.valueOf(x.toLocalDate())"), "String.valueOf(x.toLocalDate())");
    }

    #[test]
    fn test_field_access_on_scalar_collection_elem_degrades() {
        // Review #129 §4.3: `v_tab(1).child_flag` on `List<Long>` must NOT
        // emit `.getOrDefault` on a Long (javac: cannot find symbol) —
        // degrade to a TOBEFIX null instead.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_tab".into(), "java.util.List<Long>".into());
        let expr = ogsql_parser::ast::Expr::FieldAccess {
            object: Box::new(fc_expr(&["v_tab"], vec![int_lit(1)])),
            field: "child_flag".into(),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "scalar elem field read must degrade: {}", out);
        assert!(!out.contains("getOrDefault"), "must not emit Map read on Long: {}", out);
    }

    #[test]
    fn test_field_access_on_map_collection_elem_emits_map_read() {
        // `v_rectab(1).item_id` on `List<Map<String,Object>>` keeps the
        // getOrDefault lowering (element IS a record map).
        let mut proc = empty_proc();
        proc.local_vars.insert("v_rectab".into(), "java.util.List<Map<String, Object>>".into());
        let expr = ogsql_parser::ast::Expr::FieldAccess {
            object: Box::new(fc_expr(&["v_rectab"], vec![int_lit(1)])),
            field: "item_id".into(),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("getOrDefault(\"itemId\""), "map elem field read: {}", out);
        assert!(!out.contains("TOBEFIX"), "{}", out);
    }

    #[test]
    fn test_field_access_on_object_collection_elem_casts() {
        // `List<Object>`/`List<?>` elements need an explicit Map cast or
        // `.getOrDefault` does not compile on Object.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_any".into(), "java.util.List<Object>".into());
        let expr = ogsql_parser::ast::Expr::FieldAccess {
            object: Box::new(fc_expr(&["v_any"], vec![int_lit(1)])),
            field: "item_id".into(),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("(java.util.Map<String, Object>)"), "object elem needs cast: {}", out);
    }

    #[test]
    fn test_replace_with_get_args_null_short_circuits() {
        // Review #129 S2: `REPLACE(s, coll(i), r)` must NULL-short-circuit
        // (PL/SQL semantics) instead of `String.valueOf(null)` → "null".
        let mut proc = empty_proc();
        proc.local_vars.insert("v_content".into(), "String".into());
        proc.local_vars.insert("v_old_char_list".into(), "Object".into());
        proc.local_vars.insert("v_i".into(), "Long".into());
        let coll_read = fc_expr(&["v_old_char_list"], vec![col_expr(&["v_i"])]);
        let expr = fc_expr(
            &["replace"],
            vec![
                col_expr(&["v_content"]),
                coll_read,
                ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("X".into())),
            ],
        );
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("== null ? null :"), "REPLACE must NULL-guard: {}", out);
        assert!(
            out.contains("String.valueOf(((java.util.List<?>) vOldCharList).get("),
            "get arg must be String.valueOf'd: {}",
            out
        );
        assert!(!out.contains("replace(String.valueOf(null)"), "must not stringify null: {}", out);
    }

    // ── Issue #127: cross-package disambiguation ──

    #[test]
    fn test_cross_pkg_identical_signatures_pick_first_with_note() {
        // #127: BIGFUND.fnc_com_getday (schema-level) and
        // PKG_UTIL_RATE.fnc_com_getday (package-level) share an identical
        // signature — the call is compilable either way, so pick the first
        // candidate and annotate the ambiguity instead of TOBEFIX null.
        let mut proc = empty_proc();
        let entry = |svc: &str, pkg: &str| GlobalFnEntry {
            svc_var: svc.into(),
            package: pkg.into(),
            params: vec![crate::types::Parameter {
                name: "p_d".into(),
                java_type: "java.sql.Date".into(),
                sql_type: "date".into(),
                mode: None,
                default_value: None,
            }],
            return_type: Some("Long".into()),
            param_sig_types: Vec::new(),
        };
        proc.all_proc_params.insert(
            "fncComGetday".into(),
            vec![entry("bigfundService", "bigfund"), entry("pkgUtilRateService", "pkg_util_rate")],
        );
        proc.local_vars.insert("v_d".into(), "java.sql.Date".into());
        let expr = fc_expr(&["fnc_com_getday"], vec![col_expr(&["v_d"])]);
        let out = expr_to_java(&expr, &proc);
        assert!(
            out.contains("bigfundService.fncComGetday("),
            "identical signatures resolve to first candidate: {}",
            out
        );
        assert!(out.contains("ambiguous"), "choice must be annotated: {}", out);
        assert!(!out.contains("TOBEFIX"), "identical signatures must not degrade: {}", out);
    }

    #[test]
    fn test_cross_pkg_differing_signatures_stay_tobefix_with_sigs() {
        // Differing signatures remain ambiguous: TOBEFIX marker must list
        // each candidate's signature, not just the service name.
        let mut proc = empty_proc();
        let entry = |svc: &str, pkg: &str, jt: &str| GlobalFnEntry {
            svc_var: svc.into(),
            package: pkg.into(),
            params: vec![crate::types::Parameter {
                name: "x".into(),
                java_type: jt.into(),
                sql_type: "varchar2".into(),
                mode: None,
                default_value: None,
            }],
            return_type: None,
            param_sig_types: Vec::new(),
        };
        proc.all_proc_params.insert(
            "getVal".into(),
            vec![entry("pkgAService", "pkg_a", "String"), entry("pkgBService", "pkg_b", "Long")],
        );
        let expr = fc_expr(&["get_val"], vec![col_expr(&["v_x"])]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "differing signatures must stay unresolved: {}", out);
        assert!(out.contains("pkgAService"), "candidate A listed: {}", out);
        assert!(out.contains("pkgBService"), "candidate B listed: {}", out);
        assert!(out.contains("String"), "signature detail included: {}", out);
    }

    // ── Issue #128-3: temporal lowering must not round-trip through String ──

    #[test]
    fn test_add_months_on_timestamp_never_string_parses() {
        // ADD_MONTHS(SYSDATE, 1): SYSDATE lowers to java.sql.Timestamp;
        // LocalDate.parse(String.valueOf(ts)) throws at runtime.
        let proc = empty_proc();
        let expr = fc_expr(&["add_months"], vec![fc_expr(&["sysdate"], vec![]), int_lit(1)]);
        let out = expr_to_java(&expr, &proc);
        assert!(
            !out.contains("LocalDate.parse(String.valueOf"),
            "temporal arg must not go through String.valueOf→parse: {}",
            out
        );
        assert!(out.contains("getTime()"), "expected Timestamp→Date via getTime(): {}", out);
    }

    #[test]
    fn test_last_day_on_timestamp_never_string_parses() {
        let mut proc = empty_proc();
        proc.local_vars.insert("v_ts".into(), "java.sql.Timestamp".into());
        let expr = fc_expr(&["last_day"], vec![col_expr(&["v_ts"])]);
        let out = expr_to_java(&expr, &proc);
        assert!(!out.contains("LocalDate.parse(String.valueOf"), "Timestamp local must not string-parse: {}", out);
    }

    // ── composite_type_shims: parallel-list record fields ──

    fn shim_fields() -> std::collections::HashMap<String, String> {
        let mut f = std::collections::HashMap::new();
        f.insert("k".to_string(), "java.util.List<String>".to_string());
        f.insert("v".to_string(), "java.util.List<String>".to_string());
        f
    }

    fn shim_proc() -> ProcedureInfo {
        let mut proc = empty_proc();
        // m: IN OUT map_object param (AtomicReference-backed)
        proc.parameters.push(crate::types::Parameter {
            name: "m".into(),
            java_type: "Object".into(),
            sql_type: "bigfund.map_object".into(),
            mode: Some(crate::types::ParamMode::InOut),
            default_value: None,
        });
        proc.param_rowtype_fields.insert("m".into(), shim_fields());
        // pm: IN map_object param (plain Map)
        proc.parameters.push(crate::types::Parameter {
            name: "pm".into(),
            java_type: "Map<String, Object>".into(),
            sql_type: "map_object".into(),
            mode: Some(crate::types::ParamMode::In),
            default_value: None,
        });
        proc.param_rowtype_fields.insert("pm".into(), shim_fields());
        // scalar params/locals referenced by the shim bodies
        proc.parameters.push(crate::types::Parameter {
            name: "k".into(),
            java_type: "String".into(),
            sql_type: "varchar2".into(),
            mode: Some(crate::types::ParamMode::In),
            default_value: None,
        });
        proc.local_vars.insert("i".into(), "Integer".into());
        // r: local map_object var
        proc.local_vars.insert("r".into(), "Map<String, Object>".into());
        proc.rowtype_field_types.insert("r".into(), shim_fields());
        proc
    }

    fn plvar(parts: &[&str]) -> ogsql_parser::ast::Expr {
        ogsql_parser::ast::Expr::PlVariable(parts.iter().map(|s| ogsql_parser::ast::Ident::new(*s)).collect())
    }

    #[test]
    fn test_shim_field_concat_assign_appends_to_parallel_list() {
        // ad_item: `m.k := m.k || k` → ((List) map.computeIfAbsent("k",...)).add(k)
        let proc = shim_proc();
        let target = plvar(&["m", "k"]);
        let value = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(plvar(&["m", "k"])),
            op: "||".into(),
            right: Box::new(plvar(&["k"])),
        };
        let out = assignment_to_java(&target, &value, &proc);
        assert!(
            out.contains("computeIfAbsent(\"k\", _k -> new java.util.ArrayList<String>())).add(k)"),
            "concat-assign must append to the parallel list, got: {}",
            out
        );
        assert!(!out.contains("StrUtil.concat"), "must not string-concat a list field: {}", out);
    }

    #[test]
    fn test_shim_field_empty_array_assign_reseats_list() {
        // `r.k := '{}'::text[]` → r.put("k", new ArrayList<String>())
        let proc = shim_proc();
        let target = plvar(&["r", "k"]);
        let value = ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("{}".into()));
        let out = assignment_to_java(&target, &value, &proc);
        assert_eq!(out, "r.put(\"k\", new java.util.ArrayList<String>());");
    }

    #[test]
    fn test_shim_field_concat_empty_array_operand_is_noop() {
        // Review P2-G: `m.k := m.k || '{}'` appends zero elements — must NOT
        // reseat the list (the old rendered-string check on "\"{}\"" wiped it).
        let proc = shim_proc();
        let target = plvar(&["m", "k"]);
        let mk_concat = |right: ogsql_parser::ast::Expr| ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(plvar(&["m", "k"])),
            op: "||".into(),
            right: Box::new(right),
        };
        // Plain '{}' literal.
        let out = assignment_to_java(
            &target,
            &mk_concat(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("{}".into()))),
            &proc,
        );
        assert!(out.contains("no-op"), "append of '{{}}' must be a no-op, got: {}", out);
        assert!(!out.contains("new java.util.ArrayList"), "must not reseat the list: {}", out);
        // Cast variant `'{}'::text[]`.
        let cast = ogsql_parser::ast::Expr::TypeCast {
            expr: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("{}".into()))),
            type_name: ogsql_parser::ast::DataType::Text,
            default: None,
            format: None,
        };
        let out2 = assignment_to_java(&target, &mk_concat(cast), &proc);
        assert!(out2.contains("no-op"), "cast '{{}}' append must be a no-op, got: {}", out2);
    }

    #[test]
    fn test_shim_field_nonempty_concat_still_appends() {
        // Guard against over-filtering: `m.k || k` still appends.
        let proc = shim_proc();
        let target = plvar(&["m", "k"]);
        let value = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(plvar(&["m", "k"])),
            op: "||".into(),
            right: Box::new(plvar(&["k"])),
        };
        let out = assignment_to_java(&target, &value, &proc);
        assert!(out.contains(".add(k)"), "non-empty concat must append, got: {}", out);
    }

    #[test]
    fn test_shim_field_read_casts_to_typed_list() {
        // `pm.k` read → ((java.util.List<String>) pm.getOrDefault/get(...))
        let proc = shim_proc();
        let out = expr_to_java(&plvar(&["pm", "k"]), &proc);
        assert!(
            out.starts_with("((java.util.List<String>)"),
            "list field read must be cast to the typed list, got: {}",
            out
        );
    }

    #[test]
    fn test_shim_subscript_is_one_based() {
        // `pm.v[i]` → typed list .get((int)((i) - 1))
        let proc = shim_proc();
        let sub = ogsql_parser::ast::Expr::Subscript {
            object: Box::new(plvar(&["pm", "v"])),
            lower: Some(Box::new(plvar(&["i"]))),
            upper: None,
            is_slice: false,
        };
        let out = expr_to_java(&sub, &proc);
        assert!(out.contains(".get((int) ((i) - 1))"), "1-based subscript expected, got: {}", out);
    }

    #[test]
    fn test_array_position_lowers_to_null_safe_indexof() {
        let proc = shim_proc();
        let call = fc_expr(&["array_position"], vec![plvar(&["pm", "k"]), plvar(&["k"])]);
        let out = expr_to_java(&call, &proc);
        assert!(out.contains("indexOf"), "array_position must lower to indexOf, got: {}", out);
        assert!(out.contains("orElse(null)"), "missing key must yield NULL, got: {}", out);
    }

    #[test]
    fn test_is_null_on_primitive_param_folds_to_false() {
        // `p_amount` is emitted as primitive `long` — `pAmount == null` cannot
        // compile, so IS NULL folds to false / IS NOT NULL to true.
        let mut proc = empty_proc();
        proc.parameters.push(crate::types::Parameter {
            name: "p_amount".into(),
            java_type: "Long".into(),
            sql_type: "number".into(),
            mode: Some(crate::types::ParamMode::In),
            default_value: None,
        });
        let col = || Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["p_amount".into()]));
        let is_null = ogsql_parser::ast::Expr::IsNull { expr: col(), negated: false };
        assert_eq!(expr_to_java(&is_null, &proc), "false");
        let is_not_null = ogsql_parser::ast::Expr::IsNull { expr: col(), negated: true };
        assert_eq!(expr_to_java(&is_not_null, &proc), "true");
    }

    #[test]
    fn test_is_null_on_is_null_sentinel_param_keeps_comparison() {
        // Review P3-I: `p_amount IS NULL` is the PL "argument not passed"
        // sentinel — the param stays boxed in the signature, so the check
        // must NOT be folded to `false`.
        let mut proc = empty_proc();
        proc.sql_text = "IF p_amount IS NULL THEN RETURN; END IF;".into();
        proc.parameters.push(crate::types::Parameter {
            name: "p_amount".into(),
            java_type: "Long".into(),
            sql_type: "number".into(),
            mode: Some(crate::types::ParamMode::In),
            default_value: None,
        });
        let expr = ogsql_parser::ast::Expr::IsNull {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["p_amount".into()])),
            negated: false,
        };
        assert_eq!(expr_to_java(&expr, &proc), "pAmount == null");
    }

    #[test]
    fn test_is_null_on_boxed_local_still_emits_comparison() {
        // Local vars stay boxed (`Long`), so `== null` remains valid Java.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_amount".into(), "Long".into());
        let expr = ogsql_parser::ast::Expr::IsNull {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_amount".into()])),
            negated: false,
        };
        assert_eq!(expr_to_java(&expr, &proc), "vAmount == null");
    }

    #[test]
    fn test_out_param_long_set_negative_literal_gets_l_suffix() {
        // `v_flag := -1` on an OUT Long param must emit `.set((-1L))` — a bare
        // int literal cannot widen+box to Long in one invocation step.
        let mut proc = empty_proc();
        proc.parameters.push(crate::types::Parameter {
            name: "v_flag".into(),
            java_type: "Long".into(),
            sql_type: "number".into(),
            mode: Some(crate::types::ParamMode::Out),
            default_value: None,
        });
        let target = plvar(&["v_flag"]);
        let value = ogsql_parser::ast::Expr::UnaryOp {
            op: "-".into(),
            expr: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(1))),
        };
        let out = assignment_to_java(&target, &value, &proc);
        assert!(out.contains(".set((-1L))"), "expected long literal in .set(), got: {}", out);
    }

    #[test]
    fn test_optional_operand_unwraps_in_arithmetic() {
        // Review P1-F: array_position/array_length yield Optional...orElse(null)
        // — arithmetic must unwrap to long, not cast the Optional to Number.
        let proc = shim_proc();
        let call = fc_expr(&["array_position"], vec![plvar(&["pm", "k"]), plvar(&["k"])]);
        let expr = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(call),
            op: "+".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(1))),
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains(".map(Number::longValue).orElse(0L) + 1"), "got: {}", out);
        assert!(!out.contains("(Number) (java.util.Optional"), "Optional must not be Number-cast: {}", out);

        let call2 = fc_expr(
            &["array_length"],
            vec![plvar(&["pm", "k"]), ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(1))],
        );
        let expr2 = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(call2),
            op: "*".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(2))),
        };
        let out2 = expr_to_java(&expr2, &proc);
        assert!(out2.contains(".map(Number::longValue).orElse(0L) * 2"), "got: {}", out2);

        // Comparison operand unwraps the same way.
        let call3 = fc_expr(&["array_position"], vec![plvar(&["pm", "k"]), plvar(&["k"])]);
        let expr3 = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(call3),
            op: ">".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(0))),
        };
        let out3 = expr_to_java(&expr3, &proc);
        assert!(out3.contains(".map(Number::longValue).orElse(0L) > 0"), "got: {}", out3);
    }

    #[test]
    fn test_optional_coercion_preserves_null_for_boxed_targets() {
        // `v := array_position(...)` into Long/BigDecimal must keep SQL NULL —
        // orElse(null) on the mapped Optional, not orElse(0L).
        let opt = "java.util.Optional.ofNullable(x).map(__i -> __i + 1).orElse(null)";
        let long_out = coerce_for_type(opt, Some("Long"), &empty_proc());
        assert!(long_out.ends_with(".map(Number::longValue).orElse(null)"), "got: {}", long_out);
        let bd_out = coerce_for_type(opt, Some("java.math.BigDecimal"), &empty_proc());
        assert!(
            bd_out.contains("BigDecimal.valueOf(__n.longValue())") && bd_out.ends_with("orElse(null)"),
            "got: {}",
            bd_out
        );
        // Primitive long target defaults NULL to 0L (cannot be null).
        let prim = coerce_for_type(opt, Some("long"), &empty_proc());
        assert!(prim.ends_with(".map(Number::longValue).orElse(0L)"), "got: {}", prim);
        // Integer targets unwrap via intValue; the " + " inside the Optional's
        // own map lambda must not trigger the arithmetic passthrough arm.
        let int_out = coerce_for_type(opt, Some("Integer"), &empty_proc());
        assert!(int_out.ends_with(".map(Number::intValue).orElse(null)"), "got: {}", int_out);
        let int_prim = coerce_for_type(opt, Some("int"), &empty_proc());
        assert!(int_prim.ends_with(".map(Number::intValue).orElse(0)"), "got: {}", int_prim);
        // Function-arg coercion to Long behaves identically.
        let arg_out = coerce_arg_to_type(opt, "Long", &empty_proc());
        assert!(arg_out.ends_with(".map(Number::longValue).orElse(null)"), "got: {}", arg_out);
    }

    #[test]
    fn test_safe_long_value_parenthesizes_cast_operand() {
        // `(Number) a.get("x") + 1` parses as `((Number) a.get("x")) + 1` —
        // Number + int is a compile error. The cast must cover the sum.
        let out = safe_long_value("m.get(\"x\") + 1");
        assert!(out.contains("(Number) (m.get(\"x\") + 1)"), "got: {}", out);
    }

    fn param(name: &str, java_type: &str, default: Option<&str>) -> crate::types::Parameter {
        crate::types::Parameter {
            name: name.into(),
            java_type: java_type.into(),
            sql_type: "number".into(),
            mode: Some(crate::types::ParamMode::In),
            default_value: default.map(|s| s.to_string()),
        }
    }

    #[test]
    fn test_issue136_default_padding_matrix() {
        // Primitive-narrowed params must never be padded with `null`.
        assert_eq!(default_padding_for_param(&param("p_a", "long", Some("0"))), "0L");
        assert_eq!(default_padding_for_param(&param("p_b", "Long", Some("null"))), "null");
        assert_eq!(default_padding_for_param(&param("p_c", "long", Some("0"))), "0L");
        assert_eq!(default_padding_for_param(&param("p_d", "long", None)), "0L");
        assert_eq!(default_padding_for_param(&param("p_e", "int", None)), "0");
        assert_eq!(default_padding_for_param(&param("p_f", "boolean", None)), "false");
        assert_eq!(default_padding_for_param(&param("p_g", "String", None)), "null");
        let mut sp = param("p_h", "String", Some("''"));
        sp.sql_type = "varchar2".into();
        assert_eq!(default_padding_for_param(&sp), "\"\"");
        let mut op = param("p_i", "String", None);
        op.mode = Some(crate::types::ParamMode::Out);
        assert!(default_padding_for_param(&op).contains("AtomicReference"));
    }

    #[test]
    fn test_issue136_self_call_pads_defaults_by_param_type() {
        // fnc_calc(1) against (p_a long DEFAULT 0, p_b Long DEFAULT NULL,
        // p_c long DEFAULT 0) must emit `this.fncCalc(1, null, 0L)` — the old
        // unconditional `null` padding did not compile against `long`.
        let mut proc = empty_proc();
        proc.package_proc_params.insert(
            "fncCalc".into(),
            vec![vec![
                param("p_a", "long", Some("0")),
                param("p_b", "Long", Some("null")),
                param("p_c", "long", Some("0")),
            ]],
        );
        proc.package_proc_returns.insert("fncCalc".into(), vec![Some("Long".into())]);
        let expr = fc_expr(&["fnc_calc"], vec![int_lit(1)]);
        let out = expr_to_java(&expr, &proc);
        assert_eq!(out, "this.fncCalc(1, null, 0L)");
    }

    #[test]
    fn test_issue136_primitive_param_null_compare_folds() {
        // `p_value != NULL` on a primitive-emitted IN param folds to `true`
        // (`=` folds to `false`); boxed params keep the null-safe comparison.
        let mut proc = empty_proc();
        proc.parameters.push(param("p_value", "long", None));
        proc.parameters.push(param("p_opt", "Long", Some("null")));
        let ne = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(col_expr(&["p_value"])),
            op: "!=".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null)),
        };
        assert_eq!(expr_to_java(&ne, &proc), "true");
        let eq = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(col_expr(&["p_value"])),
            op: "=".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null)),
        };
        assert_eq!(expr_to_java(&eq, &proc), "false");
        // Boxed (DEFAULT NULL) param: `p_opt != NULL` must NOT fold — it can
        // genuinely be null, so keep the Objects.equals form.
        let boxed_ne = ogsql_parser::ast::Expr::BinaryOp {
            left: Box::new(col_expr(&["p_opt"])),
            op: "!=".into(),
            right: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null)),
        };
        let out = expr_to_java(&boxed_ne, &proc);
        assert!(out.contains("Objects.equals"), "boxed param must keep null-safe compare: {}", out);
    }

    #[test]
    fn test_issue136_cross_pkg_relaxed_arity_pads_default() {
        // pkg_g_target.fnc_with_defaults('a','b') against a 3-param callee
        // whose p_c has DEFAULT '' resolves via the >= arity fallback and
        // pads `""` — strict equality used to drop every candidate.
        let mut proc = empty_proc();
        proc.package = "pkg_g_caller".into();
        proc.all_proc_params.insert(
            "fncWithDefaults".into(),
            vec![GlobalFnEntry {
                svc_var: "gTargetService".into(),
                package: "pkg_g_target".into(),
                params: vec![
                    param("p_a", "String", None),
                    param("p_b", "String", Some("null")),
                    param("p_c", "String", Some("''")),
                ],
                return_type: Some("String".into()),
                param_sig_types: Vec::new(),
            }],
        );
        let expr = fc_expr(
            &["pkg_g_target", "fnc_with_defaults"],
            vec![
                ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("a".into())),
                ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("b".into())),
            ],
        );
        let out = expr_to_java(&expr, &proc);
        assert_eq!(out, "gTargetService.fncWithDefaults(\"a\", \"b\", \"\")");
    }

    #[test]
    fn test_issue136b_refcursor_out_residual_assign_is_comment() {
        // `p_result := NULL` inside the exception branch of a proc whose
        // refcursor OUT param was promoted to a List return — the variable no
        // longer exists in the signature, so `.set()` must not be emitted.
        let mut proc = empty_proc();
        proc.parameters.push(crate::types::Parameter {
            name: "p_result".into(),
            java_type: "Object".into(),
            sql_type: "refcursor".into(),
            mode: Some(crate::types::ParamMode::Out),
            default_value: None,
        });
        let target = ogsql_parser::ast::Expr::PlVariable(vec!["p_result".into()]);
        let value = ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null);
        let out = assignment_to_java(&target, &value, &proc);
        assert!(out.starts_with("//"), "residual refcursor assign must be a comment, got: {}", out);
        assert!(out.contains("p_result"), "comment must name the variable: {}", out);
        assert!(!out.contains(".set("), "must not emit .set() on removed var: {}", out);
    }

    #[test]
    fn test_issue136_self_call_bigdecimal_coercion_uses_return_type() {
        // `v_result := fnc_calc(1)` where the sibling returns Long but the
        // target is BigDecimal: valueOf, not a blind (BigDecimal) cast.
        let mut proc = empty_proc();
        proc.package_proc_params.insert("fncCalc".into(), vec![vec![param("p_a", "long", Some("0"))]]);
        proc.package_proc_returns.insert("fncCalc".into(), vec![Some("Long".into())]);
        let call = expr_to_java(&fc_expr(&["fnc_calc"], vec![int_lit(1)]), &proc);
        assert_eq!(call, "this.fncCalc(1)");
        let out = coerce_for_type(&call, Some("java.math.BigDecimal"), &proc);
        assert_eq!(out, "java.math.BigDecimal.valueOf(this.fncCalc(1))");
    }

    #[test]
    fn test_issue136e_collection_type_constructor_emits_typed_list() {
        // `v_nums := t_num_table()` — `TYPE t_num_table IS TABLE OF NUMBER`
        // is a collection constructor, not a function: emit a typed list.
        let mut proc = empty_proc();
        proc.custom_types.insert(
            "t_num_table".into(),
            crate::types::CustomTypeInfo {
                fields: Vec::new(),
                field_sql_types: Vec::new(),
                is_record: false,
                elem_java_type: Some("Long".into()),
            },
        );
        let out = expr_to_java(&fc_expr(&["t_num_table"], Vec::new()), &proc);
        assert_eq!(out, "new java.util.ArrayList<Long>()");
        // Record types are NOT collection constructors — unchanged path.
        proc.custom_types.insert(
            "t_item".into(),
            crate::types::CustomTypeInfo {
                fields: vec![("item_count".into(), "Long".into())],
                field_sql_types: Vec::new(),
                is_record: true,
                elem_java_type: None,
            },
        );
        let rec = expr_to_java(&fc_expr(&["t_item"], Vec::new()), &proc);
        assert!(rec.contains("TOBEFIX"), "record ctor must stay unresolved: {}", rec);
    }

    #[test]
    fn test_issue136e_builtin_xml_package_call_is_tobefix() {
        // `DBE_XMLDOM.GETCHILDNODES(p_node)` inside PKG_XMLDOM: the builtin
        // prefix must win over the self-call `ends_with` suffix match —
        // `this.getchildnodes` does not exist and would not compile.
        let mut proc = empty_proc();
        proc.package = "pkg_xmldom".into();
        let expr = fc_expr(&["DBE_XMLDOM", "GETCHILDNODES"], vec![col_expr(&["p_node"])]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "got: {}", out);
        assert!(out.trim_end().ends_with("null"), "got: {}", out);
        assert!(!out.contains("this.getchildnodes"), "must not emit phantom self-call: {}", out);
    }

    #[test]
    fn test_issue136d_string_call_return_to_long_parses() {
        // `v_count := fnc_text('1')` — fncText returns String; `(Number)` cast
        // cannot compile, route through Long.parseLong(String.valueOf(..)).
        let mut proc = empty_proc();
        proc.package_proc_params.insert("fncText".into(), vec![vec![param("p_value", "String", None)]]);
        proc.package_proc_returns.insert("fncText".into(), vec![Some("String".into())]);
        let call = expr_to_java(
            &fc_expr(
                &["fnc_text"],
                vec![ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("1".into()))],
            ),
            &proc,
        );
        assert_eq!(call, "this.fncText(\"1\")");
        let out = coerce_for_type(&call, Some("Long"), &proc);
        assert_eq!(out, "Long.parseLong(String.valueOf(this.fncText(\"1\")))");
    }

    #[test]
    fn test_issue136d_numeric_arg_into_string_param_wraps() {
        // `fnc_text(1)` — a bare int literal cannot widen to String; the
        // implicit conversion becomes String.valueOf(1).
        assert_eq!(coerce_arg_to_type("1", "String", &empty_proc()), "String.valueOf(1)");
        let mut proc = empty_proc();
        proc.package_proc_params.insert("fncText".into(), vec![vec![param("p_value", "String", None)]]);
        proc.package_proc_returns.insert("fncText".into(), vec![Some("String".into())]);
        let out = expr_to_java(&fc_expr(&["fnc_text"], vec![int_lit(1)]), &proc);
        assert_eq!(out, "this.fncText(String.valueOf(1))");
    }

    #[test]
    fn test_issue136e_range_for_bound_coercion() {
        // `FOR v_i IN 1 .. v_item.item_count` with `Long v_i`: the record-field
        // read yields Object (null-safe long) and the literal needs an L.
        assert_eq!(coerce_range_for_bound("1", "Long"), "1L");
        assert_eq!(coerce_range_for_bound("10", "int"), "10");
        let bound = coerce_range_for_bound("vItem.getOrDefault(\"itemCount\", vItem.get(\"item_count\"))", "Long");
        assert!(bound.contains("orElse(0L)"), "got: {}", bound);
        assert!(bound.contains("(Number)"), "got: {}", bound);
    }

    fn out_param(name: &str, java_type: &str, sql_type: &str) -> crate::types::Parameter {
        crate::types::Parameter {
            name: name.into(),
            java_type: java_type.into(),
            sql_type: sql_type.into(),
            mode: Some(crate::types::ParamMode::Out),
            default_value: None,
        }
    }

    #[test]
    fn test_issue136b4_self_call_skips_refcursor_out_param() {
        // `fn_c(p_a, p_cur OUT refcursor)` — the Java signature drops p_cur,
        // so `fn_c(1, cur)` must emit `this.fnC(1)`, not `this.fnC(1, cur)`
        // ("required 1 found 2" over-padding, issue #136 batch-4 F2).
        let mut proc = empty_proc();
        proc.package_proc_params
            .insert("fnC".into(), vec![vec![param("p_a", "long", None), out_param("p_cur", "Object", "refcursor")]]);
        proc.package_proc_returns.insert("fnC".into(), vec![None]);
        let expr = fc_expr(&["fn_c"], vec![int_lit(1), col_expr(&["cur"])]);
        let out = expr_to_java(&expr, &proc);
        assert_eq!(out, "this.fnC(1)");
    }

    #[test]
    fn test_issue136b4_cross_pkg_call_skips_refcursor_out_param() {
        // Same skip through emit_cross_pkg_call: callee params
        // (p_a long, p_cur OUT refcursor) → `otherService.fnC(1)`.
        let proc = empty_proc();
        let callee_params = vec![param("p_a", "long", None), out_param("p_cur", "Object", "refcursor")];
        let out =
            emit_cross_pkg_call("otherService", "fnC", vec!["1".into(), "cur".into()], Some(&callee_params), &proc);
        assert_eq!(out, "otherService.fnC(1)");
    }

    #[test]
    fn test_issue136b4_cross_pkg_call_pads_only_emitted_params() {
        // Call `fnD(1)` against (p_a long, p_cur OUT refcursor, p_b Long
        // DEFAULT NULL): emitted args = p_a + p_b padding, cursor skipped.
        let proc = empty_proc();
        let mut p_b = param("p_b", "Long", Some("null"));
        p_b.sql_type = "number".into();
        let callee_params = vec![param("p_a", "long", None), out_param("p_cur", "Object", "refcursor"), p_b];
        let out = emit_cross_pkg_call("otherService", "fnD", vec!["1".into()], Some(&callee_params), &proc);
        assert_eq!(out, "otherService.fnD(1, null)");
    }

    #[test]
    fn test_issue136b4_qualified_call_narrower_than_args_is_tobefix() {
        // `other_pkg.fn_e(1,2,3)` where the only candidate takes 2 params —
        // no signature can accept 3 args; must degrade to TOBEFIX, not emit
        // a "required 2 found 3" call.
        let mut proc = empty_proc();
        proc.package = "pkg_inventory".into();
        proc.all_proc_params.insert(
            "fnE".into(),
            vec![crate::types::GlobalFnEntry {
                svc_var: "otherService".into(),
                package: "other_schema".into(),
                params: vec![param("p_a", "long", None), param("p_b", "long", None)],
                return_type: None,
                param_sig_types: vec![],
            }],
        );
        let expr = fc_expr(&["other_schema", "fn_e"], vec![int_lit(1), int_lit(2), int_lit(3)]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "got: {}", out);
        assert!(!out.contains("otherService.fnE("), "must not emit over-arity call: {}", out);
    }

    #[test]
    fn test_issue136b4_out_arg_generics_mismatch_rewraps() {
        // Caller OUT `AtomicReference<Long> pCode` into callee OUT
        // `AtomicReference<String>`: rewrap with String.valueOf(pCode.get()).
        let mut proc = empty_proc();
        proc.parameters.push(out_param("p_code", "Long", "number"));
        let callee_params = vec![out_param("p_code", "String", "varchar2")];
        let out = emit_cross_pkg_call("otherService", "fnF", vec!["pCode".into()], Some(&callee_params), &proc);
        assert!(
            out.contains("new java.util.concurrent.atomic.AtomicReference<>(String.valueOf(pCode.get()))"),
            "got: {}",
            out
        );
    }

    #[test]
    fn test_issue136b4_out_arg_generics_match_passes_through() {
        // Same generic on both sides → pass the AtomicReference unchanged.
        let mut proc = empty_proc();
        proc.parameters.push(out_param("p_code", "Long", "number"));
        let callee_params = vec![out_param("p_code", "Long", "number")];
        let out = emit_cross_pkg_call("otherService", "fnF", vec!["pCode".into()], Some(&callee_params), &proc);
        assert_eq!(out, "otherService.fnF(pCode)");
    }

    #[test]
    fn test_issue136b4_replace_wraps_object_receiver() {
        // `REPLACE(v_obj, 'a', 'b')` with `Object v_obj` — Object has no
        // .replace; wrap the receiver in String.valueOf.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_obj".into(), "Object".into());
        let lit = |s: &str| ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(s.into()));
        let expr = fc_expr(&["replace"], vec![plvar(&["v_obj"]), lit("a"), lit("b")]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("String.valueOf(vObj).replace(\"a\", \"b\")"), "got: {}", out);
    }

    #[test]
    fn test_issue136b4_map_list_assignment_degrades() {
        // `v_list := v_map` (Map→List) has no sound coercion → typed null +
        // TOBEFIX marker; `v_map := v_obj` (Object→Map) gets a cast.
        let mut proc = empty_proc();
        proc.local_vars.insert("v_map".into(), "Map<String, Object>".into());
        proc.local_vars.insert("v_list".into(), "java.util.List<Object>".into());
        proc.local_vars.insert("v_obj".into(), "Object".into());
        let out = coerce_for_type("vMap", Some("java.util.List<Object>"), &proc);
        assert!(out.contains("TOBEFIX"), "got: {}", out);
        assert!(out.contains("((java.util.List<Object>) (Object) vMap)"), "got: {}", out);
        let out2 = coerce_for_type("vObj", Some("Map<String, Object>"), &proc);
        assert_eq!(out2, "((Map<String, Object>) vObj)");
        // List→Map direction: compile-safe cast through Object + marker.
        let out3 = coerce_for_type("vList", Some("Map<String, Object>"), &proc);
        assert!(out3.contains("TOBEFIX: List->Map"), "got: {}", out3);
        assert!(out3.contains("((Map<String, Object>) (Object) vList)"), "got: {}", out3);
    }

    #[test]
    fn test_issue136b4_unresolved_dotted_ref_is_tobefix() {
        // `pkg_x.node_name` where neither base nor field is declared —
        // previously emitted `pkgX.getOrDefault(...)` (undeclared identifier).
        let proc = empty_proc();
        let expr = col_expr(&["pkg_x", "node_name"]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX: unresolved ref"), "got: {}", out);
        assert!(!out.contains("getOrDefault"), "must not emit undeclared receiver: {}", out);
    }

    #[test]
    fn test_issue136b4_subscript_on_null_receiver_is_tobefix() {
        // `null[i]` → `null.get(i)` does not compile; degrade to marker.
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::Subscript {
            object: Box::new(ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null)),
            lower: Some(Box::new(int_lit(1))),
            upper: None,
            is_slice: false,
        };
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("TOBEFIX"), "got: {}", out);
        assert!(!out.contains("null.get("), "got: {}", out);
    }

    #[test]
    fn test_issue136b4_shared_pkg_var_dotted_ref_keeps_getordefault() {
        // issue_38 shape: `pkg_issue38_shared.global_counter` — the base is a
        // package qualifier (empty base_type) but the FIELD resolves in the
        // shared package-var registry. Must keep the legacy getOrDefault
        // form, not degrade to TOBEFIX (counter would be pinned at 1).
        let mut proc = empty_proc();
        proc.package_vars.insert(
            "global_counter".into(),
            crate::types::VarInfo {
                name: "global_counter".into(),
                java_type: "Integer".into(),
                sql_type: "INT".into(),
                default_value: Some("0".into()),
                is_constant: false,
            },
        );
        let expr = col_expr(&["pkg_issue38_shared", "global_counter"]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("getOrDefault(\"globalCounter\""), "got: {}", out);
        assert!(!out.contains("TOBEFIX"), "got: {}", out);
    }

    #[test]
    fn test_issue136b4_rowtype_base_dotted_ref_keeps_getordefault() {
        // A record-typed base known only via rowtype_field_types (no
        // local_vars/parameters entry) is still resolvable — keep the
        // getOrDefault form instead of TOBEFIX.
        let mut proc = empty_proc();
        proc.rowtype_field_types
            .insert("v_rec".into(), [("f_one".to_string(), "Long".to_string())].into_iter().collect());
        let expr = col_expr(&["v_rec", "f_one"]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("getOrDefault(\"fOne\""), "got: {}", out);
        assert!(!out.contains("TOBEFIX"), "got: {}", out);
    }

    #[test]
    fn test_issue136b5_cross_pkg_var_dotted_ref() {
        // `pkg_other.shared_flag` where pkg_other is a known package with a
        // same-named package var → `<pkg>Service.<var>` accessor, not
        // TOBEFIX / undeclared identifier.
        let mut proc = empty_proc();
        proc.cross_pkg_vars.insert(
            "pkg_other".into(),
            (
                "pkgOtherService".into(),
                [(
                    "shared_flag".to_string(),
                    crate::types::VarInfo {
                        name: "shared_flag".into(),
                        java_type: "String".into(),
                        sql_type: "VARCHAR".into(),
                        default_value: None,
                        is_constant: false,
                    },
                )]
                .into_iter()
                .collect(),
            ),
        );
        let expr = col_expr(&["pkg_other", "shared_flag"]);
        let out = expr_to_java(&expr, &proc);
        assert_eq!(out, "pkgOtherService.sharedFlag");
        // Unknown field in a known package still degrades.
        let expr2 = col_expr(&["pkg_other", "no_such_var"]);
        let out2 = expr_to_java(&expr2, &proc);
        assert!(out2.contains("TOBEFIX"), "got: {}", out2);
    }

    #[test]
    fn test_issue136b5_cross_pkg_var_write() {
        // `pkg_other.shared_flag := 'x'` — cross-package package-var WRITE
        // emits `<pkg>Service.<var> = v` (field is marked public); a write
        // to a constant var degrades to a comment instead of assigning a
        // `final` field.
        let mut proc = empty_proc();
        proc.cross_pkg_vars.insert(
            "pkg_other".into(),
            (
                "pkgOtherService".into(),
                [
                    (
                        "shared_flag".to_string(),
                        crate::types::VarInfo {
                            name: "shared_flag".into(),
                            java_type: "String".into(),
                            sql_type: "VARCHAR".into(),
                            default_value: None,
                            is_constant: false,
                        },
                    ),
                    (
                        "shared_const".to_string(),
                        crate::types::VarInfo {
                            name: "shared_const".into(),
                            java_type: "String".into(),
                            sql_type: "VARCHAR".into(),
                            default_value: None,
                            is_constant: true,
                        },
                    ),
                ]
                .into_iter()
                .collect(),
            ),
        );
        let lit = |s: &str| ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(s.into()));
        let out = assignment_to_java(&plvar(&["pkg_other", "shared_flag"]), &lit("x"), &proc);
        assert_eq!(out, "pkgOtherService.sharedFlag = \"x\";");
        let out_const = assignment_to_java(&plvar(&["pkg_other", "shared_const"]), &lit("x"), &proc);
        assert!(out_const.starts_with("// pkg_other.shared_const"), "got: {}", out_const);
        // Unknown field keeps the legacy map-put degradation.
        let out_unknown = assignment_to_java(&plvar(&["pkg_other", "no_such"]), &lit("x"), &proc);
        assert!(
            out_unknown.contains("pkgOther") || out_unknown.contains("Map<String, Object>"),
            "got: {}",
            out_unknown
        );
    }

    #[test]
    fn test_issue136b5_cross_pkg_self_var_bare_name() {
        // Bare `pkg_other` where pkg_other is a known package AND has a
        // package var of the same name → `<pkg>Service.<var>`.
        let mut proc = empty_proc();
        proc.cross_pkg_vars.insert(
            "pkg_other".into(),
            (
                "pkgOtherService".into(),
                [(
                    "pkg_other".to_string(),
                    crate::types::VarInfo {
                        name: "pkg_other".into(),
                        java_type: "String".into(),
                        sql_type: "VARCHAR".into(),
                        default_value: None,
                        is_constant: false,
                    },
                )]
                .into_iter()
                .collect(),
            ),
        );
        let expr = col_expr(&["pkg_other"]);
        let out = expr_to_java(&expr, &proc);
        assert_eq!(out, "pkgOtherService.pkgOther");
    }

    #[test]
    fn test_issue136b5_null_receiver_string_funcs() {
        // `TRIM(NULL)`/`LENGTH(NULL)`/`LTRIM(NULL)`/`REPEAT(NULL,n)`/
        // `REVERSE(NULL)`/`ASCII(NULL)` → `null`, never `null.trim()` etc.
        let proc = empty_proc();
        let null_lit = || ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null);
        for fname in ["trim", "length", "ltrim", "rtrim", "reverse", "ascii"] {
            let expr = fc_expr(&[fname], vec![null_lit()]);
            let out = expr_to_java(&expr, &proc);
            assert_eq!(out, "null", "{} produced: {}", fname, out);
        }
        let expr = fc_expr(&["repeat"], vec![null_lit(), int_lit(3)]);
        assert_eq!(expr_to_java(&expr, &proc), "null");
        // Non-null args unaffected.
        let lit = |s: &str| ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(s.into()));
        let expr = fc_expr(&["trim"], vec![lit("  x  ")]);
        assert!(expr_to_java(&expr, &proc).contains(".trim()"));
    }

    #[test]
    fn test_issue136b5_replace_wraps_object_param_receiver() {
        // Object-typed IN param receiver — same String.valueOf wrap as the
        // local-var case (corpus: `Object.replace` on non-.get() receivers).
        let mut proc = empty_proc();
        proc.parameters.push(crate::types::Parameter {
            name: "p_obj".into(),
            java_type: "Object".into(),
            sql_type: "VARCHAR2".into(),
            mode: Some(crate::types::ParamMode::In),
            default_value: None,
        });
        let lit = |s: &str| ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String(s.into()));
        let expr = fc_expr(&["replace"], vec![plvar(&["p_obj"]), lit("a"), lit("b")]);
        let out = expr_to_java(&expr, &proc);
        assert!(out.contains("String.valueOf(pObj).replace(\"a\", \"b\")"), "got: {}", out);
    }
}

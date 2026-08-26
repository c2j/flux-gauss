use crate::naming::snake_to_camel;
use crate::types::ProcedureInfo;

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
        return format!(
            "((Number) java.util.Objects.requireNonNullElse({}, 0)).doubleValue()",
            t
        );
    }
    format!("Double.parseDouble(String.valueOf({}))", t)
}

fn parse_string_math_arg(expr: &str, proc: &ProcedureInfo) -> String {
    if resolve_var_java_type(expr, proc).as_deref() == Some("String") {
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
        val
    }
}

pub fn assignment_to_java(target: &ogsql_parser::ast::Expr, value: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let target_name = get_column_ref_name(target);
    if let Some(ref_name) = &target_name {
        // Check if it's a dotted reference like rec.field_name
        if ref_name.contains('.') {
            let parts: Vec<&str> = ref_name.splitn(2, '.').collect();
            let base = parts[0];
            let field = parts[1];
            let base_camel = snake_to_camel(base);
            let field_camel = snake_to_camel(field);

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
                let val = expr_to_java(value, proc);
                return format!(
                    "((java.util.Map<String, Object>) {}).put(\"{}\", {});",
                    base_camel, field_camel, val
                );
            }
        }

        let camel = snake_to_camel(ref_name);
        if is_out_param(ref_name, proc) {
            let val = expr_to_java(value, proc);
            let param = proc.parameters.iter().find(|p| {
                let base = if ref_name.contains('.') { ref_name.split('.').next().unwrap() } else { ref_name };
                p.name == base && p.is_out()
            });
            let coerced = if let Some(p) = param {
                let ptype = p.java_type.trim();
                if ptype == "String" && !val.starts_with('"') && val != "null" {
                    format!("String.valueOf({})", val)
                } else if ptype == "Long" && val.starts_with('"') {
                    format!("Long.valueOf({})", val)
                } else if ptype == "Long" && val.chars().all(|c| c.is_ascii_digit()) {
                    format!("Long.valueOf({})", java_int_lit(&val))
                } else if ptype == "Long" && (val.contains("BigDecimal") || val.contains("java.math.BigDecimal")) {
                    format!("({}).longValue()", val)
                } else if ptype == "Long" {
                    let trimmed = val.trim();
                    let matched = proc.local_vars.iter().any(|(k, t)| 
                        k.to_lowercase().replace("_", "") == trimmed.to_lowercase().replace("_", "")
                        && t.contains("BigDecimal")
                    );
                    if matched {
                        format!("({}).longValue()", val)
                    } else {
                        val
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
        let mut var_type = proc.local_vars.get(&ref_name.to_lowercase()).cloned()
            .or_else(|| {
                let ref_lower = ref_name.to_lowercase().replace("_", "");
                proc.package_vars.iter()
                    .find(|(k, _)| k.to_lowercase().replace("_", "") == ref_lower)
                    .map(|(_, vi)| vi.java_type.clone())
            });
        // Check if promoted to AtomicReference<> by out_local_vars (OUT-arg usage)
        let ref_lower_no_underscore = ref_name.to_lowercase().replace("_", "");
        if let Some((_, inner_type)) = proc.out_local_vars.iter()
            .find(|(k, _)| k.to_lowercase().replace("_", "") == ref_lower_no_underscore)
        {
            var_type = Some(format!("AtomicReference<{}>", inner_type));
        }
        let mut val = expr_to_java(value, proc);
        // Local vars promoted to AtomicReference for OUT-arg usage need .set()
        if let Some(vt) = &var_type {
            if vt.contains("AtomicReference<") {
                let inner_type = vt.trim_start_matches("AtomicReference<").trim_end_matches('>');
                let coerced = coerce_for_type(&val, Some(inner_type), proc);
                return format!("{}.set({});", camel, coerced);
            }
        }
        let skip_coerce = var_type.as_ref().map_or(false, |t| t.contains("BigDecimal"))
            && is_bigdecimal_var(&val, proc);
        if !skip_coerce {
            val = coerce_for_type(&val, var_type.as_deref(), proc);
        }
        return format!("{} = {};", camel, val);
    }
    let var = expr_to_java(target, proc);
    let val = expr_to_java(value, proc);
    format!("{} = {};", var, val)
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
    let base = name
        .split(|c: char| c == '.' || c == '(')
        .next()
        .unwrap_or(name);
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
        if p.name.to_lowercase().replace('_', "") == base_key
            || crate::naming::snake_to_camel(&p.name) == base
        {
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

fn has_decimal_literal(expr: &str) -> bool {
    let bytes = expr.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_digit()
            && (i == 0 || (!bytes[i - 1].is_ascii_alphanumeric() && bytes[i - 1] != b'_'))
        {
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

/// True if the rendered expression can yield a Java `double`.
fn produces_double(expr: &str) -> bool {
    expr.contains("Double.parseDouble")
        || expr.contains(".doubleValue()")
        || expr.contains("Math.pow")
        || expr.contains("Math.sqrt")
        || expr.contains(" / ")
        || has_decimal_literal(expr)
}

pub(crate) fn coerce_for_type(expr: &str, target_type: Option<&str>, proc: &ProcedureInfo) -> String {
    let trimmed = expr.trim();
    // Early-exit: if target is BigDecimal and expr is already a BigDecimal value,
    // don't double-wrap with BigDecimal.valueOf()
    if let Some(t) = target_type {
        if t.contains("BigDecimal") && is_bigdecimal_var(trimmed, proc) {
            return trimmed.to_string();
        }
    }
    if is_nullish_java_expr(trimmed) {
        if let Some(t) = target_type {
            if t.contains("BigDecimal") { return "java.math.BigDecimal.ZERO".to_string(); }
            if t == "Double" || t == "double" { return "0.0d".to_string(); }
            if t == "Float" || t == "float" { return "0.0f".to_string(); }
            if t == "Integer" || t == "int" { return "0".to_string(); }
            if t == "Long" || t == "long" { return "0L".to_string(); }
            if t == "Boolean" || t == "boolean" { return "false".to_string(); }
            if t == "String" { return "null".to_string(); }
        }
        if trimmed == "null" {
            return "null".to_string();
        }
        return "0".to_string();
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
            if t == "Long" || t == "long" || t == "Integer" || t == "int"
                || t == "Double" || t == "double" || t == "Float" || t == "float"
                || t.contains("BigDecimal") || t.contains("BigInteger")
            {
                return "null".to_string();
            }
        }
        return trimmed.to_string();
    }
    // Issue #72: never emit `(Number) <String>`. When the source resolves to a
    // String and the target is numeric, parse instead of cast.
    if let Some(src) = resolve_var_java_type(trimmed, proc) {
        if src == "String" {
            match target_type {
                Some(t) if t == "Long" || t == "long" => {
                    return format!("Long.parseLong({})", trimmed);
                }
                Some(t) if t == "Integer" || t == "int" => {
                    return format!("Integer.parseInt({})", trimmed);
                }
                Some(t) if t == "Double" || t == "double" => {
                    return format!("Double.parseDouble({})", trimmed);
                }
                Some(t) if t.contains("BigDecimal") => {
                    return format!("new java.math.BigDecimal({})", trimmed);
                }
                _ => {}
            }
        }
    }
    match target_type {
        Some(t) if t.contains("BigDecimal") && trimmed.starts_with('"') => {
            format!("new java.math.BigDecimal({})", trimmed)
        }
        Some(t) if t.contains("BigDecimal")
            && !trimmed.starts_with("java.math.BigDecimal")
            && !trimmed.starts_with("new java.math.BigDecimal")
            && !trimmed.starts_with("((java.math.BigDecimal)")
            && (trimmed.starts_with("Math.")
                || (trimmed.contains("Math.")
                    && trimmed.chars().next().map(|c| c.is_ascii_digit() || c == '(' || c == '-').unwrap_or(false))) => {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t) if t.contains("BigDecimal") && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t) if t.contains("BigDecimal")
            && trimmed.contains(".get(")
            && !trimmed.starts_with("new java.math.BigDecimal") => {
            // If arithmetic operators are present beyond .get(), the result is already a primitive
            let has_arithmetic = trimmed.contains(" * ") || trimmed.contains(" + ")
                || trimmed.contains(" - ") || trimmed.contains(" / ");
            if has_arithmetic {
                format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
            } else {
                format!("java.math.BigDecimal.valueOf(((Number) {}).longValue())", trimmed)
            }
        }
        Some(t) if t.contains("BigDecimal") && trimmed.contains("this.") && !trimmed.starts_with('(') => {
            format!("((java.math.BigDecimal) {})", trimmed)
        }
        Some(t) if t.contains("BigDecimal")
            && trimmed.contains('.') && !trimmed.starts_with('"')
            && !trimmed.contains("this.")
            && !trimmed.contains(".multiply(")
            && !trimmed.contains(".add(")
            && !trimmed.contains(".subtract(")
            && !trimmed.contains(".divide(")
            && !trimmed.contains(".setScale(")
            && !trimmed.contains("BigDecimal.valueOf(")
            && !trimmed.contains("BigDecimal.ZERO")
            && !trimmed.contains("BigDecimal.ONE")
            && !trimmed.starts_with("new java.math.BigDecimal") => {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t) if t.contains("BigDecimal")
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
            && !trimmed.contains(".stripTrailingZeros(")
            => {
            format!("java.math.BigDecimal.valueOf({})", java_int_lit(trimmed))
        }
        Some(t) if (t == "Integer" || t == "int") && trimmed.contains(".get(") => {
            format!("((Number) {}).intValue()", trimmed)
        }
        Some(t) if (t == "Long" || t == "long") && trimmed.contains(".get(") && !trimmed.contains(" * ") && !trimmed.contains(" + ") && !trimmed.contains(" - ") && !trimmed.contains(" / ") => {
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
        Some(t) if t == "String" && trimmed != "null"
            && !trimmed.contains("String.valueOf(")
            && !trimmed.contains(".concat(String")
            && !trimmed.contains(".substring(")
            && !trimmed.contains(".toUpperCase()")
            && !trimmed.contains(".toLowerCase()")
            && !trimmed.contains(".trim()")
            && !trimmed.contains(".replace(")
            && !(trimmed.starts_with('"') && !trimmed.contains(".length()") && !trimmed.contains(".indexOf(") && !trimmed.contains(".charAt(") && !trimmed.contains(" + ") && !trimmed.contains(" - "))
            => {
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
        Some(t) if (t == "Long" || t == "long")
            && (trimmed.contains(" * ") || trimmed.contains(" + ") || trimmed.contains(" - ") || trimmed.contains(" / "))
            && !produces_double(trimmed) => {
            trimmed.to_string()
        }
        Some(t) if (t == "Long" || t == "long") => {
            format!("((Number)({})).longValue()", trimmed)
        }
        _ => expr.to_string()
    }
}

fn coerce_arg_to_type(arg: &str, target_type: &str, proc: &ProcedureInfo) -> String {
    let trimmed = arg.trim();
    let target_is_bigdecimal = target_type.contains("BigDecimal");
    let target_is_long = target_type == "long" || target_type == "Long";

    // Unwrap AtomicReference OUT params when passed as regular-type args
    if is_out_param(trimmed, proc) || proc.out_local_vars.iter().any(|(k,_)|
        k.to_lowercase().replace("_", "") == trimmed.to_lowercase().replace("_", ""))
    {
        // OUT param / promoted local — pass .get() value, then coerce further
        return coerce_arg_to_type(&format!("{}.get()", trimmed), target_type, proc);
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

    if target_is_int && trimmed.contains(".get(") {
        return format!("((Number) {}).intValue()", trimmed);
    }

    if target_is_long {
        let arg_type = infer_arg_type_from_expr(trimmed, proc);
        if arg_type == "Object" {
            return format!("Long.parseLong(String.valueOf({}))", trimmed);
        }
        if arg_type == "String" {
            return format!("Long.parseLong(String.valueOf({}))", trimmed);
        }
    }

    if target_type == "String" && (trimmed.contains(".get(") || infer_arg_type_from_expr(trimmed, proc) == "Object") {
        return format!("String.valueOf({})", trimmed);
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
    if trimmed.starts_with('"') { return "String"; }
    if trimmed.chars().all(|c| c.is_ascii_digit()) { return "long"; }
    if trimmed.contains(".get(") { return "Object"; }
    "Object"
}

fn is_out_param(name: &str, proc: &ProcedureInfo) -> bool {
    let base_name = if name.contains('.') {
        name.split('.').next().unwrap()
    } else {
        name
    };
    let bn = base_name.to_lowercase().replace("_", "");
    proc.parameters.iter().any(|p| {
        p.name.to_lowercase().replace("_", "") == bn && p.is_out()
    })
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

fn get_column_ref_name(expr: &ogsql_parser::ast::Expr) -> Option<String> {
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
        Expr::BinaryOp { left, op, right } => {
            binary_op_to_java(left, op, right, proc)
        }
        Expr::UnaryOp { op, expr } => unary_op_to_java(op, expr, proc),
        Expr::FunctionCall { name, args, .. } => {
            function_call_to_java(&name.join("."), args, proc)
        }
        Expr::SpecialFunction { name, args, .. } => {
            special_function_to_java(name, args, proc)
        }
        Expr::IsNull { expr, negated } => {
            let inner = expr_to_java(expr, proc);
            if *negated { format!("{} != null", inner) } else { format!("{} == null", inner) }
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
            if *negated { format!("!{}.contains({})", arr, left) } else { format!("{}.contains({})", arr, left) }
        }
        Expr::Like { expr, pattern, negated, .. } => {
            like_to_java(expr, pattern, *negated, proc)
        }
        Expr::TypeCast { expr, type_name, .. } => {
            type_cast_to_java(expr, &format!("{:?}", type_name), proc)
        }
        Expr::Case { operand, whens, else_expr } => {
            case_to_java(operand, whens, else_expr, proc)
        }
        Expr::Parenthesized(inner) => format!("({})", expr_to_java(inner, proc)),
         Expr::Between { expr, low, high, negated } => {
            let e = expr_to_java(expr, proc);
            let lo = expr_to_java(low, proc);
            let hi = expr_to_java(high, proc);
            let ge = binary_op_to_java(expr, ">=", low, proc);
            let le = binary_op_to_java(expr, "<=", high, proc);
            if *negated { format!("!({} && {})", ge, le) } else { format!("({} && {})", ge, le) }
         }
        Expr::Exists(_) => "/* EXISTS */ true".into(),
        Expr::Subquery(_) => "null".into(),
        Expr::QualifiedStar(_) => "null".into(),
        Expr::Parameter(n) => format!("null", ),
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
                // Single index: obj[i] → obj.get(i)
                let idx = lower.as_ref()
                    .or(upper.as_ref())
                    .map(|e| expr_to_java(e, proc))
                    .unwrap_or_else(|| "0".into());
                format!("{}.get({})", expr_to_java(object, proc), idx)
            }
        }
        Expr::FieldAccess { object, field } => {
            let camel = snake_to_camel(field);
            if camel != field.to_lowercase() {
                format!("{}.getOrDefault(\"{}\", {}.get(\"{}\"))", expr_to_java(object, proc), camel, expr_to_java(object, proc), field.to_lowercase())
            } else {
                format!("{}.get(\"{}\")", expr_to_java(object, proc), camel)
            }
        }
        Expr::CursorAttribute { cursor, attribute } => {
            let cursor_name_raw = match cursor.as_ref() {
                Expr::ColumnRef(name) | Expr::PlVariable(name) => name.join("."),
                _ => "cursor".into(),
            };
            let cursor_name = crate::naming::snake_to_camel(&cursor_name_raw);
            // Check if the cursor variable is an OUT param (AtomicReference)
            let is_out = proc.parameters.iter().any(|p| {
                p.name == cursor_name_raw && p.is_out()
            }) || proc.out_local_vars.iter().any(|(n, _)| *n == cursor_name_raw);
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
                },
                ogsql_parser::ast::CursorAttributeKind::BulkExceptions => "java.util.Collections.emptyList()".into(),
            }
        }
         Expr::Default => "null".into(),
         Expr::Prior(_) => "null".into(),
         Expr::SysDate => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
         _ => "null".into(),
    }
}

fn java_string_literal(s: &str) -> String {
    let escaped = s
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t");
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
        Literal::Boolean(b) => if *b { "true".into() } else { "false".into() },
        Literal::Null => "null".into(),
    }
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
        "sysdate" | "current_timestamp" | "systimestamp" | "localtimestamp" => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
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
                        if let Some((_, vi)) = matched {
                            let field_camel = crate::naming::snake_to_camel(field_candidate);
                            return format!("this.{}", field_camel);
                        }
                    }
                }
                 let var_name = snake_to_camel(parts[0]);
                 let field = parts[1].to_lowercase();
                 let snake_base = parts[0];
                 if field == "count" {
                     return format!("((java.util.List<?>) {}).size()", var_name);
                 }
                  let field_camel = snake_to_camel(parts[1]);
                  let is_out = proc.parameters.iter().any(|p| p.name == parts[0] && p.is_out());
                  let base_type = proc.local_vars.get(&snake_base.to_lowercase()).map(|s| s.as_str())
                      .or_else(|| {
                          proc.parameters.iter()
                              .find(|p| p.name.eq_ignore_ascii_case(snake_base))
                              .map(|p| p.java_type.as_str())
                      })
                      .unwrap_or("");
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
                      format!("{}.getOrDefault(\"{}\", {}.get(\"{}\"))", map_base, field_camel, map_base, parts[1])
                  } else {
                      format!("{}.get(\"{}\")", map_base, field_camel)
                  }
            } else {
                let camel = snake_to_camel(name);
                let is_out = proc.parameters.iter().any(|p| p.name == name && p.is_out());
                if is_out { return format!("{}.get()", camel); }
                let name_lower = name.to_lowercase().replace("_", "");
                let param_match = proc.parameters.iter().find(|p| {
                    p.name.to_lowercase().replace("_", "") == name_lower
                });
                if let Some(p) = param_match {
                    let param_camel = snake_to_camel(&p.name);
                    if p.is_out() { return format!("{}.get()", param_camel); }
                    return param_camel;
                }
                let name_lower_raw = name.to_lowercase();
                if name_lower_raw.starts_with("func_") || name_lower_raw.starts_with("fn_") {
                    let method_name = crate::naming::java_method_name(name);
                    // Check if this function exists as a sibling procedure in the same package
                    if proc.package_proc_params.contains_key(&method_name) {
                        return format!("this.{}()", method_name);
                    }
                    // Check if it exists in another package (cross-package call)
                    if let Some(svc_var) = proc.all_proc_params.get(&method_name) {
                        return format!("{}.{}()", svc_var, method_name);
                    }
                    let (pkg_hint, func_short) = if let Some(dot_pos) = name.rfind('.') {
                        (Some(&name[..dot_pos]), &name[dot_pos+1..])
                    } else {
                        (None, name)
                    };
                    let hint = match pkg_hint {
                        Some(p) => format!("pkg={}", p),
                        None => "pkg=?".to_string(),
                    };
                    return format!(
                        "/* TODO: implement {}() - {}, caller={}:{} */ \"\"",
                        flatten_comment(func_short),
                        flatten_comment(&hint),
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
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
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
        for (_key, custom_type) in &proc.custom_types {
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
    if name.contains(".multiply(") || name.contains(".add(") || name.contains(".subtract(") || name.contains(".divide(") || (name.contains(".abs(") && !name.contains("Math.abs(")) || name.contains(".setScale(") {
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
    if already_bd { return expr.to_string(); }
    let trimmed = expr.trim();
    if is_nullish_java_expr(trimmed) { return "java.math.BigDecimal.ZERO".to_string(); }
    if trimmed.starts_with("new java.math.BigDecimal") { return trimmed.to_string(); }
    // Unwrap AtomicReference<Long> vars promoted for OUT arg usage
    let trimmed_lower = trimmed.to_lowercase();
    let has_ar_var = proc.out_local_vars.iter().any(|(k, _)| k.to_lowercase().replace("_", "") == trimmed_lower.replace("_", ""))
        || proc.parameters.iter().any(|p| p.is_out() && p.name.to_lowercase().replace("_", "") == trimmed_lower.replace("_", ""));
    if has_ar_var {
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

fn is_string_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
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

fn is_timestamp_or_date_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
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
    if name.contains("new java.sql.Timestamp") && !name.contains(" - ") && !name.contains(" + ") && !name.contains(" / ") && !name.contains(" * ") {
        return true;
    }
    if name == "new java.sql.Timestamp(System.currentTimeMillis())" || (name.contains("System.currentTimeMillis()") && !name.contains(" - ") && !name.contains(" + ") && !name.contains(".getYear()") && !name.contains(".getMonthValue()") && !name.contains(".getDayOfMonth()")) {
        return true;
    }
    if name.contains("new java.sql.Date") && !name.contains(" - ") && !name.contains(" + ") && !name.contains(".getYear()") && !name.contains(".getMonthValue()") && !name.contains(".getDayOfMonth()") {
        return true;
    }
    false
}

fn needs_get_unwrap(expr: &str) -> bool {
    (expr.contains(".get(") || expr.contains(".getOrDefault(")) && !expr.contains(".longValue()") && !expr.contains("::longValue") && !expr.contains(".intValue()")
        && !expr.contains(".doubleValue()") && !expr.contains(".floatValue()")
}

/// Generate null-safe Number extraction from Map .get() or .getOrDefault() result
fn safe_long_value(expr: &str) -> String {
    format!("java.util.Optional.ofNullable((Number) {}).map(Number::longValue).orElse(0L)", expr)
}

fn binary_op_to_java(left: &ogsql_parser::ast::Expr, op: &str, right: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let mut l = expr_to_java(left, proc);
    let mut r = expr_to_java(right, proc);

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
    let l_is_ts = (is_timestamp_or_date_var(&l, proc)
        || l.contains("java.sql.Date.valueOf(") || l.contains("java.sql.Timestamp.valueOf(") || l.contains("new java.sql.Date(") || l.contains("new java.sql.Timestamp("))
        && !l.contains(".getYear()") && !l.contains(".getMonthValue()") && !l.contains(".getDayOfMonth()")
        && !l.contains(".getHour()") && !l.contains(".getMinute()") && !l.contains(".getSecond()");
    let r_is_ts = (is_timestamp_or_date_var(&r, proc)
        || r.contains("java.sql.Date.valueOf(") || r.contains("java.sql.Timestamp.valueOf(") || r.contains("new java.sql.Date(") || r.contains("new java.sql.Timestamp("))
        && !r.contains(".getYear()") && !r.contains(".getMonthValue()") && !r.contains(".getDayOfMonth()")
        && !r.contains(".getHour()") && !r.contains(".getMinute()") && !r.contains(".getSecond()");
    if is_arith {
        if op == "-" && (l_is_ts || r_is_ts) {
            if l_is_ts && r_is_ts {
                return format!("(({}).getTime() - ({}).getTime()) / (24 * 60 * 60 * 1000)", l, r);
            }
            if l_is_ts && !r_is_ts {
                let r_coerced = if r.contains("concat(String.valueOf(\" days\"))") || r.contains("concat(\" days\")") {
                    let stripped = r.replace(".concat(String.valueOf(\" days\"))", "").replace(".concat(\" days\")", "");
                    format!("Long.parseLong(String.valueOf({}))", stripped.trim())
                } else if r == "null" || r.contains("String.valueOf(") || is_string_var(&r, proc) {
                    format!("Long.parseLong(String.valueOf({}))", r)
                } else if is_bigdecimal_var(&r, proc) || r.contains("BigDecimal") {
                    format!("({}).longValue()", r)
                } else {
                    r.clone()
                };
                return format!("new java.sql.Timestamp({}.getTime() - (long)({}) * 24 * 60 * 60 * 1000)", l, r_coerced);
            }
            return format!("new java.sql.Timestamp({}.getTime() - {}.getTime())", l, r);
        }
        if op == "+" && (l_is_ts || r_is_ts) {
            if l_is_ts && !r_is_ts {
                let r_coerced = if r.contains("concat(String.valueOf(\" days\"))") || r.contains("concat(\" days\")") {
                    let stripped = r.replace(".concat(String.valueOf(\" days\"))", "").replace(".concat(\" days\")", "");
                    format!("Long.parseLong(String.valueOf({}))", stripped.trim())
                } else if r == "null" || r.contains("String.valueOf(") || is_string_var(&r, proc) {
                    format!("Long.parseLong(String.valueOf({}))", r)
                } else if is_bigdecimal_var(&r, proc) || r.contains("BigDecimal") {
                    format!("({}).longValue()", r)
                } else {
                    r.clone()
                };
                return format!("new java.sql.Timestamp({}.getTime() + (long)({}) * 24 * 60 * 60 * 1000)", l, r_coerced);
            }
            if !l_is_ts && r_is_ts {
                let l_coerced = if l.contains("concat(String.valueOf(\" days\"))") || l.contains("concat(\" days\")") {
                    let stripped = l.replace(".concat(String.valueOf(\" days\"))", "").replace(".concat(\" days\")", "");
                    format!("Long.parseLong(String.valueOf({}))", stripped.trim())
                } else if l == "null" || l.contains("String.valueOf(") || is_string_var(&l, proc) {
                    format!("Long.parseLong(String.valueOf({}))", l)
                } else {
                    l.clone()
                };
                return format!("new java.sql.Timestamp((long)({}) * 24 * 60 * 60 * 1000 + {}.getTime())", l_coerced, r);
            }
        }
        if is_nullish_java_expr(&l) && !l_is_ts {
            l = "0".into();
        }
        if is_nullish_java_expr(&r) && !r_is_ts {
            r = "0".into();
        }

        let l_is_string = (is_string_var(&l, proc) && !l.contains(".length()") && !l.contains(".intValue()") && !l.contains(".longValue()")) || l.starts_with('"');
        let r_is_string = (is_string_var(&r, proc) && !r.contains(".length()") && !r.contains(".intValue()") && !r.contains(".longValue()")) || r.starts_with('"');
        if l_is_string || r_is_string {
            if op == "+" {
                return format!("\"\" + {} + {}", l, r);
            }
            // For * / - operators, coerce String operands to double (numeric arithmetic)
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
            let l_sv = if is_nullish_java_expr(&l) {
                "\"\"".to_string()
            } else {
                format!("String.valueOf({})", l)
            };
            let r_sv = if is_nullish_java_expr(&r) {
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
            let l_numeric = is_integer_type(&l, proc) || l.chars().all(|c: char| c.is_ascii_digit());
            let r_numeric = is_integer_type(&r, proc) || r.chars().all(|c: char| c.is_ascii_digit());
            let l_is_boxed = is_boxed_integer(&l, proc);
            let r_is_boxed = is_boxed_integer(&r, proc);
            if (l_is_boxed && r.chars().all(|c: char| c.is_ascii_digit()) && !r.is_empty())
                || (r_is_boxed && l.chars().all(|c: char| c.is_ascii_digit()) && !l.is_empty()) {
                format!("java.util.Objects.equals({}, {})", l, r)
            } else if l_numeric && r_numeric {
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
            let l_numeric = is_integer_type(&l, proc) || l.chars().all(|c: char| c.is_ascii_digit());
            let r_numeric = is_integer_type(&r, proc) || r.chars().all(|c: char| c.is_ascii_digit());
            if l_numeric && r_numeric {
                format!("{} != {}", l, r)
            } else {
                format!("!java.util.Objects.equals({}, {})", l, r)
            }
        }
        "AND" => format!("({} && {})", l, r),
        "OR" => format!("({} || {})", l, r),
        "IS" => format!("{} == {}", l, r),
        "IS NOT" => format!("{} != {}", l, r),
        ">" | "<" | ">=" | "<=" => {
            let l_null = is_nullish_java_expr(&l);
            let r_null = is_nullish_java_expr(&r);
            if l_null || r_null {
                return "true".to_string();
            }
            let l_is_str = (is_string_var(&l, proc) && !l.contains(".length()") && !l.contains(".intValue()") && !l.contains(".longValue()") && !l.contains(".indexOf(") && !l.contains(".charAt(")) || l.starts_with('"');
            let r_is_str = (is_string_var(&r, proc) && !r.contains(".length()") && !r.contains(".intValue()") && !r.contains(".longValue()") && !r.contains(".indexOf(") && !r.contains(".charAt(")) || r.starts_with('"');
            if l_is_str || r_is_str {
                // Issue #40: String.compareTo() is lexicographic ("10" < "3").
                // When comparing against a numeric string literal, use BigDecimal.
                let l_numeric_str = l.starts_with('"') && l.trim_matches('"').parse::<f64>().is_ok();
                let r_numeric_str = r.starts_with('"') && r.trim_matches('"').parse::<f64>().is_ok();
                if (l_numeric_str && r_numeric_str)
                    || (l_numeric_str && !r_is_str && !r.contains('.'))
                    || (r_numeric_str && !l_is_str && !l.contains('.')) {
                    let cmp_str = match op {
                        ">" => " > 0", "<" => " < 0", ">=" => " >= 0", "<=" => " <= 0", _ => " != 0",
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
                format!("{}.compareTo({}){}", l, r, cmp_method)
            } else {
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
        "*" if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc) => {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.multiply({})", l_bd, r_bd)
        }
        "+" if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc) => {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.add({})", l_bd, r_bd)
        }
        "-" if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc) => {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.subtract({})", l_bd, r_bd)
        }
        "/" if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc) => {
            let l_bd = wrap_bigdecimal(&l, is_bigdecimal_var(&l, proc), proc);
            let r_bd = wrap_bigdecimal(&r, is_bigdecimal_var(&r, proc), proc);
            format!("{}.divide({}, 10, java.math.RoundingMode.HALF_UP)", l_bd, r_bd)
        }
        "^" => {
            let l_pow = as_double_expr(&l);
            let r_pow = as_double_expr(&r);
            let pow = format!("Math.pow({}, {})", l_pow, r_pow);
            if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc)
                || l.contains("BigDecimal") || r.contains("BigDecimal")
                || l.contains(".divide(") || r.contains(".divide(")
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
                } else { l.clone() };
                let ro = if has_get_r {
                    let r_base = r.split(".get(").next().unwrap_or(&r);
                    if is_string_var(r_base, proc) {
                        format!("Long.valueOf({}.toString())", r)
                    } else {
                        safe_long_value(&r)
                    }
                } else { r.clone() };
                (lo, ro)
            } else if is_arith && (has_get_l || has_get_r) {
                let lo = if has_get_l {
                    safe_long_value(&l)
                } else if l.trim().starts_with("(Number)") {
                    format!("({}).longValue()", l)
                } else { l.clone() };
                let ro = if has_get_r {
                    safe_long_value(&r)
                } else if r.trim().starts_with("(Number)") {
                    format!("({}).longValue()", r)
                } else { r.clone() };
                (lo, ro)
            } else {
                (l.clone(), r.clone())
            };
            if op == "/" && r_out.trim().matches(|c: char| c.is_ascii_digit()).count() > 0
                && r_out.trim().trim_start_matches('-').parse::<i64>().map_or(false, |v| v == 0) {
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
        "-" => format!("(-{})", inner),
        "NOT" => format!("!{}", inner),
        _ => format!("{}{}", op, inner),
    }
}

fn function_call_to_java(name: &str, args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
    {
        let name_parts: Vec<&str> = name.split('.').collect();
        if name_parts.len() == 1 && args.len() == 1 {
            let snake_name = name_parts[0];
            let list_type = proc.local_vars.get(&snake_name.to_lowercase()).map(|t| t.as_str())
                .or_else(|| {
                    proc.parameters.iter()
                        .find(|p| p.name.eq_ignore_ascii_case(snake_name))
                        .map(|p| p.java_type.as_str())
                });
            let is_list = match list_type {
                Some(t) if t.starts_with("List<") || t.contains("List") => true,
                Some("Object") => {
                    let n = snake_name.to_lowercase();
                    n.contains("array") || n.contains("list") || n.contains("spectrum")
                        || n.ends_with("_arr") || n.ends_with("arr")
                }
                _ => false,
            };
            if is_list {
                let var_java = snake_to_camel(snake_name);
                let idx = expr_to_java(&args[0], proc);
                let base = if list_type == Some("Object") {
                    format!("((java.util.List<?>) {})", var_java)
                } else {
                    var_java
                };
                return format!("{}.get((int)({}) - 1)", base, idx);
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
            let arg0_is_ar = proc.out_local_vars.iter().any(|(k,_)|
                k.to_lowercase().replace("_", "") == arg0.to_lowercase().replace("_", ""))
                || proc.parameters.iter().any(|p| p.is_out()
                    && p.name.to_lowercase().replace("_", "") == arg0.to_lowercase().replace("_", ""));
            let true_val = if arg0_is_ar {
                format!("{}.get()", arg0)
            } else {
                arg0.to_string()
            };
            format!("({} != null ? {} : {})", arg0, true_val, else_val)
        }
        "UPPER" => format!("String.valueOf({}).toUpperCase()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "LOWER" => format!("String.valueOf({}).toLowerCase()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "TRIM" => format!("{}.trim()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "LENGTH" => format!("{}.length()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
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
        "REPLACE" if jargs.len() >= 3 => format!("{}.replace({}, {})", jargs[0], jargs[1], jargs[2]),
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
                    if is_timestamp_or_date_var(arg, proc) || arg.contains("Timestamp") || arg.contains("currentTimeMillis") {
                        format!("new java.text.SimpleDateFormat(\"{}\").format({})", java_fmt, arg)
                    } else {
                        format!("new java.text.SimpleDateFormat(\"{}\").format(new java.util.Date(java.sql.Timestamp.valueOf(String.valueOf({})).getTime()))", java_fmt, arg)
                    }
                } else {
                    let num_fmt = fmt_clean.replace("fm", "").replace(",", "").replace("9", "#").replace("0", "0");
                    format!("new java.text.DecimalFormat(\"{}\").format({})", num_fmt, arg)
                }
            } else {
                format!("String.valueOf({})", jargs.first().map(|s| s.as_str()).unwrap_or("null"))
            }
        }
        "NULLIF" if jargs.len() >= 2 => format!("(java.util.Objects.equals({}, {}) ? 1 : {})", jargs[0], jargs[1], jargs[0]),
        "ARRAY_LENGTH" | "ARRAY_UPPER" => format!("({}).size()", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
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
        "ADD_MONTHS" if jargs.len() >= 2 => format!("java.time.LocalDate.parse(String.valueOf({})).plusMonths(Long.parseLong(String.valueOf({})))", jargs[0], jargs[1]),
        "LAST_DAY" => format!("java.time.LocalDate.parse(String.valueOf({})).withDayOfMonth(java.time.LocalDate.parse(String.valueOf({})).lengthOfMonth())", jargs.first().map(|s| s.as_str()).unwrap_or("null"), jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "NEXT_DAY" if !jargs.is_empty() => format!("java.time.LocalDate.parse(String.valueOf({})).plusWeeks(1)", jargs[0]),
        "NEXT_DAY" => "null".into(),
        "EXTRACT" => "/* EXTRACT */ 0".into(),
        "AGE" if jargs.len() >= 2 => format!("java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate(), new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate())", jargs[1], jargs[0]),
        "AGE" if jargs.len() == 1 => format!("java.time.Period.between(new java.sql.Date(((java.sql.Timestamp){}).getTime()).toLocalDate(), java.time.LocalDate.now())", jargs[0]),
        "DATE_TRUNC" if jargs.len() >= 2 => {
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
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).truncatedTo(java.time.temporal.ChronoUnit.DAYS).withMonth(((java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).getMonthValue() - 1) / 3) * 3 + 1).withDayOfMonth(1))", jargs[1], jargs[1])
            } else {
                format!("java.sql.Timestamp.valueOf(java.time.LocalDateTime.ofInstant({}.toInstant(), java.time.ZoneId.systemDefault()).truncatedTo({}))", jargs[1], chrono_unit)
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
        "ENCODE" => format!("/* ENCODE */ null"),
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
            if fmt_raw == "yyyy-mm-dd" {
                format!("java.sql.Date.valueOf(java.time.LocalDate.parse(String.valueOf({}), java.time.format.DateTimeFormatter.ofPattern(\"[yyyy-MM-dd][yyyyMMdd]\")))", jargs[0])
            } else {
                let java_fmt = fmt_raw.replace("yyyy", "yyyy").replace("yy", "yy")
                    .replace("mm", "MM").replace("mon", "MMM").replace("month", "MMMM")
                    .replace("dd", "dd").replace("dy", "EEE").replace("day", "EEEE")
                    .replace("hh24", "HH").replace("hh12", "hh").replace("hh", "HH")
                    .replace("mi", "mm").replace("ss", "ss")
                    .replace("ff3", "SSS").replace("ms", "SSS");
                format!("java.sql.Date.valueOf(java.time.LocalDate.parse(String.valueOf({}), java.time.format.DateTimeFormatter.ofPattern(\"[{}][yyyy-MM-dd]\")))", jargs[0], java_fmt)
            }
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
                1 => format!("{}.replaceAll(\"^\\\\s+\", \"\")", jargs[0]),
                _ => format!("{}.replaceAll(\"^\" + {} + \"+\", \"\")", jargs[0], jargs[1]),
            }
        }
        "RTRIM" => {
            match jargs.len() {
                0 => "\"\"".to_string(),
                1 => format!("{}.replaceAll(\"\\\\s+$\", \"\")", jargs[0]),
                _ => format!("{}.replaceAll(\"\" + {} + \"+$\", \"\")", jargs[0], jargs[1]),
            }
        }
        "REPEAT" if jargs.len() >= 2 => format!("{}.repeat({})", jargs[0], jargs[1]),
        "REVERSE" => format!("new StringBuilder({}).reverse().toString()", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
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
        "ASCII" if !jargs.is_empty() => format!("(int){}.charAt(0)", jargs[0]),
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
        "TRUNC" if jargs.len() >= 1 => format!("(int) Math.floor((double)({}))", parse_string_math_arg(&jargs[0], proc)),
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
            let name_parts: Vec<&str> = name.split('.').collect();
            let (method, is_self_call, cross_pkg_svc) = if name_parts.len() >= 2 {
                let pkg_hint = if name_parts.len() >= 3 {
                    name_parts[name_parts.len() - 2]
                } else {
                    name_parts[0]
                };
                let func_name = name_parts[name_parts.len() - 1];
                let pkg_lower = proc.package.to_lowercase();
                let hint_lower = pkg_hint.to_lowercase().replace("_", "");
                let pkg_no_prefix = pkg_lower.trim_start_matches("pkg_").replace("_", "");
                if hint_lower == pkg_lower || hint_lower == pkg_no_prefix || hint_lower.ends_with(&pkg_no_prefix) {
                    (crate::naming::java_method_name(func_name), true, String::new())
                } else if ["dbescheduler", "dbmsoutput", "dbmsrandom", "dbmslob", "dbeoutput", "utlfile", "dbmssql", "dbmsjob"].iter().any(|sp| hint_lower.starts_with(sp)) {
                    (crate::naming::java_method_name(func_name), false, String::new())
                } else {
                    let svc_name = format!("{}Service", {
                        let cn = crate::naming::package_to_classname(&pkg_hint.to_lowercase());
                        let mut c = cn.chars();
                        match c.next() {
                            Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                            None => String::new(),
                        }
                    });
                    (crate::naming::java_method_name(func_name), false, svc_name)
                }
            } else if name_parts.len() == 1 {
                let method_name = crate::naming::java_method_name(name_parts[0]);
                if proc.package_proc_params.contains_key(&method_name) {
                    (method_name, true, String::new())
                } else {
                    (String::new(), false, String::new())
                }
            } else {
                (String::new(), false, String::new())
            };

            if is_self_call {
                let target_params = proc.package_proc_params.get(&method)
                    .and_then(|overloads| overloads.iter().find(|params| params.len() == jargs.len()));
                let coerced_args: Vec<String> = if let Some(target_params) = target_params {
                    jargs.iter().enumerate().map(|(i, arg)| {
                        if i < target_params.len() {
                            let param = &target_params[i];
                            if param.is_out() {
                                return arg.clone();
                            }
                            coerce_arg_to_type(arg, &param.java_type, proc)
                        } else {
                            arg.clone()
                        }
                    }).collect()
                } else {
                    jargs
                };
                return format!("this.{}({})", method, coerced_args.join(", "));
            }
            if !cross_pkg_svc.is_empty() && !method.is_empty() {
                let x_args: Vec<String> = jargs.iter().map(|a| {
                    if is_out_param(a, proc) || proc.out_local_vars.iter().any(|(k,_)|
                        k.to_lowercase().replace("_", "") == a.to_lowercase().replace("_", ""))
                    {
                        format!("{}.get()", a)
                    } else {
                        a.clone()
                    }
                }).collect();
                return format!("{}.{}({})", cross_pkg_svc, method, x_args.join(", "));
            }
            let func_short = name_parts.last().unwrap_or(&name);
            let pkg_hint = if name_parts.len() >= 2 { name_parts[0] } else { "?" };
            format!(
                "/* TODO: implement {}({}) - pkg={}, caller={}:{} */ null",
                flatten_comment(func_short),
                flatten_comment(&jargs.join(", ")),
                flatten_comment(pkg_hint),
                flatten_comment(&proc.source_file),
                flatten_comment(&proc.proc_name)
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
                let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
                let len_cast = if might_be_long(len, proc) { format!("(int)({})", len) } else { format!("({})", len) };
                format!("{}.substring(Math.min({}.length(), Math.max(0, {} - 1)), Math.min({}.length(), Math.max(0, {} - 1) + {}))", s, s, start_cast, s, start_cast, len_cast)
            } else if jargs.len() >= 2 {
                let s = wrap_s(&jargs[0]);
                let start = &jargs[1];
                let start_cast = if might_be_long(start, proc) { format!("(int)({})", start) } else { format!("({})", start) };
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
            let s = if jargs[0].starts_with('"') || jargs[0].starts_with('\'') { jargs[0].clone() } else { format!("String.valueOf({})", jargs[0]) };
            let repl = if jargs[1].starts_with('"') || jargs[1].starts_with('\'') { jargs[1].clone() } else { format!("String.valueOf({})", jargs[1]) };
            let start = &jargs[2];
            if jargs.len() >= 4 {
                let len = &jargs[3];
                format!("({}).substring(0, Math.max(0, ({}) - 1)) + {} + ({}).substring(Math.max(0, ({}) - 1 + ({})))", s, start, repl, s, start, len)
            } else {
                format!("({}).substring(0, Math.max(0, ({}) - 1)) + {} + ({}).substring(Math.max(0, ({}) - 1 + {}.length()))", s, start, repl, s, start, repl)
            }
        }
        "position" if args.len() >= 2 => {
            let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
            let substr = &jargs[0];
            let s = &jargs[1];
            let substr_expr = if substr.starts_with('"') || substr.starts_with('\'') { substr.clone() } else { format!("String.valueOf({})", substr) };
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
                _ => format!("/* INTERVAL */ 0L"),
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
                        let chars_expr = if chars.starts_with('"') || chars.starts_with('\'') { chars.clone() } else { format!("String.valueOf({})", chars) };
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
                        let chars_expr = if chars.starts_with('"') || chars.starts_with('\'') { chars.clone() } else { format!("String.valueOf({})", chars) };
                        if chars.trim_matches('"').trim_matches('\'') == " " {
                            format!("String.valueOf({}).replaceAll(\"\\\\s+$\", \"\")", s)
                        } else {
                            format!("String.valueOf({}).replaceAll(java.util.regex.Pattern.quote({}) + \"+$\", \"\")", s, chars_expr)
                        }
                    } else {
                        "null".into()
                    }
                }
                _ => {
                    if jargs.len() >= 3 {
                        let chars = &jargs[1];
                        let s = &jargs[2];
                        let chars_expr = if chars.starts_with('"') || chars.starts_with('\'') { chars.clone() } else { format!("String.valueOf({})", chars) };
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

fn like_to_java(expr: &ogsql_parser::ast::Expr, pattern: &ogsql_parser::ast::Expr, negated: bool, proc: &ProcedureInfo) -> String {
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
        if pat.starts_with('%') && pat.ends_with('%') && !pat[1..pat.len()-1].contains('%') && !pat[1..pat.len()-1].contains('_') {
            let inner = &pat[1..pat.len()-1];
            if negated { format!("!{}.contains(\"{}\")", left, inner) } else { format!("{}.contains(\"{}\")", left, inner) }
        } else if pat.ends_with('%') && !pat[..pat.len()-1].contains('%') && !pat[..pat.len()-1].contains('_') {
            let prefix = &pat[..pat.len()-1];
            if negated { format!("!{}.startsWith(\"{}\")", left, prefix) } else { format!("{}.startsWith(\"{}\")", left, prefix) }
        } else if pat.starts_with('%') && !pat[1..].contains('%') && !pat[1..].contains('_') {
            let suffix = &pat[1..];
            if negated { format!("!{}.endsWith(\"{}\")", left, suffix) } else { format!("{}.endsWith(\"{}\")", left, suffix) }
        } else {
            let regex = pat.replace(".", "\\.").replace("%", ".*").replace("_", ".");
            if negated { format!("!{}.matches(\"{}\")", left, regex) } else { format!("{}.matches(\"{}\")", left, regex) }
        }
    } else {
        let pattern_java = expr_to_java(pattern, proc);
        if negated { format!("!{}.matches({})", left, pattern_java) } else { format!("{}.matches({})", left, pattern_java) }
    }
}

fn is_boxed_integer(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
    if let Some(ty) = proc.local_vars.get(&base.to_lowercase()) {
        return ty == "Integer" || ty == "Long" || ty == "Double" || ty == "Float";
    }
    let base_lower = base.to_lowercase().replace("_", "");
    for (var_name, var_type) in &proc.local_vars {
        let var_lower = var_name.to_lowercase().replace("_", "");
        if var_lower == base_lower {
            return var_type == "Integer" || var_type == "Long" || var_type == "Double" || var_type == "Float";
        }
    }
    false
}

fn type_cast_to_java(expr: &ogsql_parser::ast::Expr, type_name: &str, proc: &ProcedureInfo) -> String {
    let inner = expr_to_java(expr, proc);
    let lower = type_name.to_lowercase();
    match lower.as_str() {
        s if s.contains("bigint") || s.contains("int8") => format!("((Long) {})", inner),
        s if s.contains("integer") || s == "int" || s == "int4" => format!("((Integer) {})", inner),
        s if s.contains("numeric") || s.contains("decimal") => {
            if inner.contains("String.valueOf(") && !inner.starts_with('"') {
                format!("java.math.BigDecimal.ZERO")
            } else if inner.starts_with("new java.math.BigDecimal") || (inner.contains(".valueOf(") && inner.contains("BigDecimal")) {
                format!("((java.math.BigDecimal) {})", inner)
            } else if inner == "null" || inner.contains("/* aggregate:") {
                format!("java.math.BigDecimal.ZERO")
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
             if inner == "null" { "\"\"".to_string() } else { format!("String.valueOf({})", inner) }
         }
        s if s.contains("bool") => format!("((Boolean) {})", inner),
        s if s.contains("timestamp") => format!("((java.sql.Timestamp) {})", inner),
        s if s.contains("date") => {
            if inner.starts_with('"') {
                format!("java.sql.Date.valueOf({})", inner)
            } else {
                format!("((java.sql.Date) {})", inner)
            }
        }
        s if s.contains("interval") => format!("String.valueOf({})", inner),
        _ => format!("String.valueOf({})", inner),
    }
}

fn is_integer_type(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
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
        || else_java.as_ref().map_or(false, |e| is_bigdecimal_var(e, proc));
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
        if i == 0 { parts.push(format!("({} ? {} ", cond, result)); }
        else { parts.push(format!(": {} ? {} ", cond, result)); }
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
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(42)), &proc), "42");
    }

    #[test]
    fn test_literal_string() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::String("hello".into())), &proc), "\"hello\"");
    }

    #[test]
    fn test_literal_boolean_true() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(true)), &proc), "true");
    }

    #[test]
    fn test_literal_boolean_false() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Boolean(false)), &proc), "false");
    }

    #[test]
    fn test_literal_null() {
        let proc = empty_proc();
        assert_eq!(expr_to_java(&ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Null), &proc), "null");
    }

    #[test]
    fn test_column_ref_simple() {
        let proc = empty_proc();
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
    fn test_is_null() {
        let proc = empty_proc();
        let expr = ogsql_parser::ast::Expr::IsNull {
            expr: Box::new(ogsql_parser::ast::Expr::ColumnRef(vec!["v_status".into()])),
            negated: false,
        };
        assert_eq!(expr_to_java(&expr, &proc), "vStatus == null");
    }

    #[test]
    fn test_is_not_null() {
        let proc = empty_proc();
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
        let expr = ogsql_parser::ast::Expr::Parenthesized(Box::new(
            ogsql_parser::ast::Expr::Literal(ogsql_parser::ast::Literal::Integer(1)),
        ));
        assert_eq!(expr_to_java(&expr, &proc), "(1)");
    }
}

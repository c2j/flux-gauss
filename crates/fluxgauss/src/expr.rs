use crate::naming::snake_to_camel;
use crate::types::ProcedureInfo;

pub fn expr_to_java(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    expr_to_java_impl(expr, proc)
}

pub fn bool_expr_to_java(expr: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let val = expr_to_java(expr, proc);
    if val == "null" {
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

            let base_type = proc.local_vars.get(base).map(|s| s.as_str()).unwrap_or("");
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
                } else if ptype == "java.math.BigDecimal" {
                    if val.starts_with("String.valueOf(") {
                        let inner = val.trim_start_matches("String.valueOf(").trim_end_matches(')');
                        format!("((java.math.BigDecimal) {})", inner)
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
        let var_type = proc.local_vars.get(ref_name).cloned()
            .or_else(|| {
                let ref_lower = ref_name.to_lowercase().replace("_", "");
                proc.package_vars.iter()
                    .find(|(k, _)| k.to_lowercase().replace("_", "") == ref_lower)
                    .map(|(_, vi)| vi.java_type.clone())
            });
        let mut val = expr_to_java(value, proc);
        let skip_coerce = var_type.as_ref().map_or(false, |t| t.contains("BigDecimal"))
            && is_bigdecimal_var(&val, proc);
        if !skip_coerce {
            val = coerce_for_type(&val, var_type.as_deref());
        }
        return format!("{} = {};", camel, val);
    }
    let var = expr_to_java(target, proc);
    let val = expr_to_java(value, proc);
    format!("{} = {};", var, val)
}

fn coerce_for_type(expr: &str, target_type: Option<&str>) -> String {
    let trimmed = expr.trim();
    if trimmed == "null" {
        if let Some(t) = target_type {
            if t.contains("BigDecimal") { return "java.math.BigDecimal.ZERO".to_string(); }
            if t == "Double" || t == "double" { return "0.0d".to_string(); }
            if t == "Float" || t == "float" { return "0.0f".to_string(); }
            if t == "Integer" || t == "int" { return "0".to_string(); }
            if t == "Long" || t == "long" { return "0L".to_string(); }
        }
        return "null".to_string();
    }
    if trimmed == "\"\"" || trimmed == "''" {
        if let Some(t) = target_type {
            if t.contains("Timestamp") {
                return "new java.sql.Timestamp(0)".to_string();
            }
            if t.contains("java.sql.Date") || t == "Date" {
                return "new java.sql.Date(0)".to_string();
            }
        }
        return trimmed.to_string();
    }
    match target_type {
        Some(t) if t.contains("BigDecimal") && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("java.math.BigDecimal.valueOf({})", trimmed)
        }
        Some(t) if t.contains("BigDecimal")
            && trimmed.contains(".get(")
            && !trimmed.starts_with("new java.math.BigDecimal") => {
            // If arithmetic operators are present beyond .get(), the result is already a primitive
            let has_arithmetic = trimmed.contains(" * ") || trimmed.contains(" + ")
                || trimmed.contains(" - ") || trimmed.contains(" / ");
            if has_arithmetic {
                format!("java.math.BigDecimal.valueOf({})", trimmed)
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
            format!("java.math.BigDecimal.valueOf({})", trimmed)
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
            format!("java.math.BigDecimal.valueOf({})", trimmed)
        }
        Some(t) if (t == "Integer" || t == "int") && trimmed.contains(".get(") => {
            format!("((Number) {}).intValue()", trimmed)
        }
        Some(t) if (t == "Long" || t == "long") && trimmed.contains(".get(") => {
            format!("((Number) {}).longValue()", trimmed)
        }
        Some(t) if t == "java.sql.Timestamp" && trimmed.contains(".get(") => {
            format!("((java.sql.Timestamp) {})", trimmed)
        }
        Some(t) if t == "java.sql.Date" && trimmed.contains(".get(") => {
            format!("((java.sql.Date) {})", trimmed)
        }
        Some(t) if t == "Long" && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("Long.valueOf({})", trimmed)
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
        _ => expr.to_string()
    }
}

fn coerce_arg_to_type(arg: &str, target_type: &str, proc: &ProcedureInfo) -> String {
    let trimmed = arg.trim();
    let target_is_bigdecimal = target_type.contains("BigDecimal");
    let target_is_long = target_type == "long" || target_type == "Long";

    if target_is_bigdecimal {
        if trimmed.chars().all(|c| c.is_ascii_digit()) {
            return format!("java.math.BigDecimal.valueOf({})", trimmed);
        }
        if trimmed.chars().all(|c| c.is_ascii_digit() || c == '.') && trimmed.contains('.') {
            return format!("new java.math.BigDecimal(\"{}\")", trimmed);
        }
        if is_integer_type(trimmed, proc) {
            return format!("java.math.BigDecimal.valueOf({})", trimmed);
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

    arg.to_string()
}

fn infer_arg_type_from_expr(expr: &str, proc: &ProcedureInfo) -> &'static str {
    let trimmed = expr.trim();
    if let Some(ty) = proc.local_vars.get(trimmed) {
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
    proc.parameters.iter().any(|p| p.name == base_name && p.is_out())
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
        Expr::SpecialFunction { name, args } => {
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
            if *negated { format!("!({} >= {} && {} <= {})", e, lo, e, hi) } else { format!("({} >= {} && {} <= {})", e, lo, e, hi) }
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
        Expr::Subscript { object, index } => {
            format!("{}.get({})", expr_to_java(object, proc), expr_to_java(index, proc))
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
        "rowcount" => "__ROWCOUNT__".into(),
        "true" => "true".into(),
        "false" => "false".into(),
        "null" => "null".into(),
        "sysdate" | "current_timestamp" | "systimestamp" | "localtimestamp" => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
        "current_date" => "new java.sql.Date(System.currentTimeMillis())".into(),
        _ => {
            if name.contains('.') {
                let parts: Vec<&str> = name.splitn(2, '.').collect();
                let var_name = snake_to_camel(parts[0]);
                let field = parts[1].to_lowercase();
                let snake_base = parts[0];
                if field == "count" {
                    return format!("((java.util.List<?>) {}).size()", var_name);
                }
                 let field_camel = snake_to_camel(parts[1]);
                 let is_out = proc.parameters.iter().any(|p| p.name == parts[0] && p.is_out());
                 if is_out {
                     if field_camel != parts[1] {
                         format!("{}.get().getOrDefault(\"{}\", {}.get().get(\"{}\"))", var_name, field_camel, var_name, parts[1])
                     } else {
                         format!("{}.get().get(\"{}\")", var_name, field_camel)
                     }
                 } else {
                     if field_camel != parts[1] {
                         format!("{}.getOrDefault(\"{}\", {}.get(\"{}\"))", var_name, field_camel, var_name, parts[1])
                     } else {
                         format!("{}.get(\"{}\")", var_name, field_camel)
                     }
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
                    return format!("/* TODO: {}() */ \"\"", name);
                }
                camel
            }
        }
    }
}

fn is_bigdecimal_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
    // Direct lookup (snake_case key)
    if let Some(ty) = proc.local_vars.get(base) {
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

fn wrap_bigdecimal(expr: &str, already_bd: bool, _proc: &ProcedureInfo) -> String {
    if already_bd { return expr.to_string(); }
    let trimmed = expr.trim();
    if trimmed == "null" { return "java.math.BigDecimal.ZERO".to_string(); }
    if trimmed.starts_with("new java.math.BigDecimal") { return trimmed.to_string(); }
    if trimmed.contains(".get(") {
        format!("java.math.BigDecimal.valueOf(((Number) {}).longValue())", trimmed)
    } else if trimmed.chars().all(|c| c.is_ascii_digit() || c == '.') && !trimmed.is_empty() {
        format!("java.math.BigDecimal.valueOf({})", trimmed)
    } else {
        format!("java.math.BigDecimal.valueOf({})", trimmed)
    }
}

fn is_string_var(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
    if let Some(ty) = proc.local_vars.get(base) {
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
    if let Some(ty) = proc.local_vars.get(base) {
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
    if name == "new java.sql.Timestamp(System.currentTimeMillis())" || name.contains("System.currentTimeMillis()") && !name.contains(" - ") && !name.contains(" + ") {
        return true;
    }
    if name.contains("new java.sql.Date") && !name.contains(" - ") && !name.contains(" + ") {
        return true;
    }
    false
}

fn needs_get_unwrap(expr: &str) -> bool {
    (expr.contains(".get(") || expr.contains(".getOrDefault(")) && !expr.contains(".longValue()") && !expr.contains(".intValue()")
        && !expr.contains(".doubleValue()") && !expr.contains(".floatValue()")
}

/// Generate null-safe Number extraction from Map .get() or .getOrDefault() result
fn safe_long_value(expr: &str) -> String {
    format!("java.util.Optional.ofNullable((Number) {}).map(Number::longValue).orElse(0L)", expr)
}

fn binary_op_to_java(left: &ogsql_parser::ast::Expr, op: &str, right: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let mut l = expr_to_java(left, proc);
    let mut r = expr_to_java(right, proc);

    let is_arith = matches!(op, "*" | "+" | "-" | "/");
    let l_is_ts = is_timestamp_or_date_var(&l, proc);
    let r_is_ts = is_timestamp_or_date_var(&r, proc);
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
        if l == "null" && !l_is_ts {
            l = "0".into();
        }
        if r == "null" && !r_is_ts {
            r = "0".into();
        }

        let l_is_string = (is_string_var(&l, proc) && !l.contains(".length()") && !l.contains(".intValue()") && !l.contains(".longValue()")) || l.starts_with('"');
        let r_is_string = (is_string_var(&r, proc) && !r.contains(".length()") && !r.contains(".intValue()") && !r.contains(".longValue()")) || r.starts_with('"');
        if l_is_string || r_is_string {
            return format!("\"\" + {} + {}", l, r);
        }
    }

    match op {
        "||" => {
            let l_sv = if l == "null" { "\"\"".to_string() } else { format!("String.valueOf({})", l) };
            let r_sv = if r == "null" { "\"\"".to_string() } else { format!("String.valueOf({})", r) };
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
            let l_null = l.trim() == "null";
            let r_null = r.trim() == "null";
            if l_null || r_null {
                let safe_l = if l_null { "0".to_string() } else { l.clone() };
                let safe_r = if r_null { "0".to_string() } else { r.clone() };
                return format!("/* unresolved */ {} {} {}", safe_l, op, safe_r);
            }
            let l_is_str = is_string_var(&l, proc) && !l.contains(".length()") && !l.contains(".intValue()") && !l.contains(".longValue()") && !l.contains(".indexOf(") && !l.contains(".charAt(");
            let r_is_str = is_string_var(&r, proc) && !r.contains(".length()") && !r.contains(".intValue()") && !r.contains(".longValue()") && !r.contains(".indexOf(") && !r.contains(".charAt(");
            if l_is_str || r_is_str {
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
        "^" => format!("Math.pow({}, {})", l, r),
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
                } else { l.clone() };
                let ro = if has_get_r {
                    safe_long_value(&r)
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
    if inner.trim() == "null" || inner.trim().ends_with("null") && !inner.trim().starts_with('"') {
        if op == "-" && is_timestamp_or_date_var(&inner, proc) {
            return "null".to_string();
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
            let is_list = proc.local_vars.get(snake_name)
                .map(|t| t.starts_with("List<"))
                .unwrap_or(false);
            if is_list {
                let var_java = snake_to_camel(snake_name);
                let idx = expr_to_java(&args[0], proc);
                return format!("{}.get((int)({}) - 1)", var_java, idx);
            }
        }
    }
    let upper = name.to_uppercase();
    let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
    match upper.as_str() {
        "NVL" | "COALESCE" if jargs.len() >= 2 => {
            let else_val = if is_bigdecimal_var(&jargs[0], proc) && jargs[1].trim().chars().all(|c| c.is_ascii_digit()) {
                format!("java.math.BigDecimal.valueOf({})", jargs[1].trim())
            } else {
                jargs[1].clone()
            };
            format!("({} != null ? {} : {})", jargs[0], jargs[0], else_val)
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
                format!("Math.abs({})", arg)
            }
        }
        "FLOOR" => format!("Math.floor({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "CEIL" | "CEILING" => {
            let arg = jargs.first().map(|s| s.as_str()).unwrap_or("0");
            if is_bigdecimal_var(arg, proc) {
                format!("{}.setScale(0, java.math.RoundingMode.CEILING)", arg)
            } else {
                format!("Math.ceil({})", arg)
            }
        }
        "ROUND" => {
            if jargs.len() >= 2 {
                let arg = jargs[0].as_str();
                if is_bigdecimal_var(arg, proc) {
                    format!("{}.setScale((int){}, java.math.RoundingMode.HALF_UP)", arg, jargs[1])
                } else {
                    format!("Math.round({} * Math.pow(10, {})) / Math.pow(10, {})", jargs[0], jargs[1], jargs[1])
                }
            } else {
                let arg = jargs.first().map(|s| s.as_str()).unwrap_or("0");
                if is_bigdecimal_var(arg, proc) {
                    format!("{}.setScale(0, java.math.RoundingMode.HALF_UP)", arg)
                } else {
                    format!("Math.round({})", arg)
                }
            }
        }
        "POWER" | "POW" if jargs.len() >= 2 => format!("Math.pow({}, {})", jargs[0], jargs[1]),
        "SQRT" => format!("Math.sqrt({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "SIGN" => format!("Math.signum({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "RADIANS" => format!("Math.toRadians({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "DEGREES" => format!("Math.toDegrees({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "PI" | "PG_PI" => "Math.PI".into(),
        "RANDOM" => "Math.random()".into(),
        "MOD" if jargs.len() >= 2 => format!("({} % {})", jargs[0], jargs[1]),
        "GREATEST" if !jargs.is_empty() => {
            jargs.iter().skip(1).fold(jargs[0].clone(), |acc, arg| format!("Math.max({}, {})", acc, arg))
        }
        "LEAST" if !jargs.is_empty() => {
            jargs.iter().skip(1).fold(jargs[0].clone(), |acc, arg| format!("Math.min({}, {})", acc, arg))
        }
        "REPLACE" if jargs.len() >= 3 => format!("{}.replace({}, {})", jargs[0], jargs[1], jargs[2]),
        "SUBSTRING" => {
            if jargs.len() >= 3 {
                let s = &jargs[0];
                let start = &jargs[1];
                let len = &jargs[2];
                format!("{}.substring(Math.min({}.length(), Math.max(0, ({}) - 1)), Math.min({}.length(), Math.min({}.length(), Math.max(0, ({}) - 1)) + ({})))", s, s, start, s, s, start, len)
            } else if jargs.len() >= 2 {
                format!("{}.substring(Math.min({}.length(), Math.max(0, ({}) - 1)))", jargs[0], jargs[0], jargs[1])
            } else {
                "null".into()
            }
        }
        "SPLIT_PART" if jargs.len() >= 3 => format!("((String[]){}.split({}))[{} - 1]", jargs[0], jargs[1], jargs[2]),
        "TO_CHAR" => {
            if jargs.len() >= 2 {
                let arg = jargs.first().map(|s| s.as_str()).unwrap_or("null");
                let raw_fmt = jargs.get(1).map(|s| s.as_str()).unwrap_or("");
                let fmt_clean = raw_fmt.trim_matches('"').trim_matches('\'');
                if fmt_clean.contains("yyyy") || fmt_clean.contains("YYYY") || fmt_clean.contains("yyyymm") {
                    let java_fmt = fmt_clean.replace("yyyy", "yyyy").replace("YYYY", "yyyy")
                        .replace("mm", "MM").replace("MM", "MM")
                        .replace("dd", "dd").replace("DD", "dd")
                        .replace("hh24", "HH").replace("HH24", "HH")
                        .replace("mi", "mm").replace("MI", "mm")
                        .replace("ss", "ss").replace("SS", "ss");
                    if is_timestamp_or_date_var(arg, proc) || arg.contains("Timestamp") || arg.contains("currentTimeMillis") {
                        format!("new java.text.SimpleDateFormat(\"{}\").format({})", java_fmt, arg)
                    } else {
                        format!("String.valueOf({})", arg)
                    }
                } else {
                    format!("String.valueOf({})", arg)
                }
            } else {
                format!("String.valueOf({})", jargs.first().map(|s| s.as_str()).unwrap_or("null"))
            }
        }
        "NULLIF" if jargs.len() >= 2 => format!("(java.util.Objects.equals({}, {}) ? 1 : {})", jargs[0], jargs[1], jargs[0]),
        "ARRAY_LENGTH" | "ARRAY_UPPER" => format!("({}).size()", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "ARRAY_APPEND" => format!("/* ARRAY_APPEND */ {}", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "ADD_MONTHS" if jargs.len() >= 2 => format!("/* ADD_MONTHS({}, {}) */ null", jargs[0], jargs[1]),
        "LAST_DAY" => format!("/* LAST_DAY({}) */ null", jargs.first().map(|s| s.as_str()).unwrap_or("")),
        "NEXT_DAY" => "/* NEXT_DAY */ null".into(),
        "EXTRACT" => "/* EXTRACT */ 0".into(),
        "AGE" | "DATE_TRUNC" | "TO_TIMESTAMP" | "MAKE_DATE" | "MAKE_TIMESTAMP" => format!("/* {} */ null", upper),
         "ROW_NUMBER" | "RANK" | "DENSE_RANK" | "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" => format!("/* aggregate:{} */ 0", upper),
        "BIT_AND" | "BIT_OR" | "BIT_XOR" => format!("/* {} */ 0", upper),
        "GET_BIT" => "/* GET_BIT */ 0".into(),
        "SET_BIT" => "/* SET_BIT */".into(),
        "ENCODE" | "DECODE" => format!("/* {} */ null", upper),
        "MD5" | "SHA224" | "SHA256" | "SHA384" | "SHA512" => format!("/* {} */ null", upper),
        "HMAC_MD5" | "HMAC_SHA1" | "HMAC_SHA256" => format!("/* {} */ null", upper),
        "PG_SLEEP" => "/* PG_SLEEP */".into(),
        "SET_CONFIG" => "/* SET_CONFIG */ null".into(),
        "CURRENT_SETTING" => "/* CURRENT_SETTING */ null".into(),
        "PG_BACKEND_PID" => "Thread.currentThread().getId()".into(),
        "LISTAGG" | "STRING_AGG" => format!("/* {} */ null", upper),
        "REGEXP_REPLACE" if jargs.len() >= 3 => format!("{}.replaceAll({}, {})", jargs[0], jargs[1], jargs[2]),
        "LEFT" if jargs.len() >= 2 => format!("{}.substring(0, Math.min({}.length(), {}))", jargs[0], jargs[0], jargs[1]),
        "RIGHT" if jargs.len() >= 2 => format!("{}.substring(Math.max(0, {}.length() - {}))", jargs[0], jargs[0], jargs[1]),
        "LPAD" | "RPAD" => format!("/* {} */ {}", upper, jargs.first().map(|s| s.as_str()).unwrap_or("null")),
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
        "TO_NUMBER" => format!("new BigDecimal({})", jargs.first().map(|s| s.as_str()).unwrap_or("\"0\"")),
        "INSTR" if jargs.len() >= 2 => format!("{}.indexOf({}) + 1", jargs[0], jargs[1]),
        "TRUNC" if jargs.len() >= 1 => format!("(int) Math.floor((double)({}))", jargs[0]),
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
        _ => {
            let name_parts: Vec<&str> = name.split('.').collect();
            let (method, is_self_call) = if name_parts.len() >= 2 {
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
                    (crate::naming::java_method_name(func_name), true)
                } else {
                    (String::new(), false)
                }
            } else if name_parts.len() == 1 {
                let method_name = crate::naming::java_method_name(name_parts[0]);
                if proc.package_proc_params.contains_key(&method_name) {
                    (method_name, true)
                } else {
                    (String::new(), false)
                }
            } else {
                (String::new(), false)
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
            "null".into()
        }
    }
}

fn special_function_to_java(name: &str, args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
    let lower = name.to_lowercase();
    match lower.as_str() {
        "substring" | "substr" => {
            let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
            if jargs.len() >= 3 {
                let s = &jargs[0];
                let start = &jargs[1];
                let len = &jargs[2];
                format!("{}.substring(Math.min({}.length(), Math.max(0, ({}) - 1)), Math.min({}.length(), Math.min({}.length(), Math.max(0, ({}) - 1)) + ({})))", s, s, start, s, s, start, len)
            } else if jargs.len() >= 2 {
                format!("{}.substring(Math.min({}.length(), Math.max(0, ({}) - 1)))", jargs[0], jargs[0], jargs[1])
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
                "year" => format!("({}).toLocalDateTime().toLocalDate().getYear()", ts_expr),
                "month" => format!("({}).toLocalDateTime().toLocalDate().getMonthValue()", ts_expr),
                "day" => format!("({}).toLocalDateTime().toLocalDate().getDayOfMonth()", ts_expr),
                "hour" => format!("({}).toLocalDateTime().getHour()", ts_expr),
                "minute" => format!("({}).toLocalDateTime().getMinute()", ts_expr),
                "second" => format!("({}).toLocalDateTime().getSecond()", ts_expr),
                _ => format!("/* EXTRACT({}) */ 0", field),
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
    if let Some(ty) = proc.local_vars.get(base) {
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
    if let Some(ty) = proc.local_vars.get(base) {
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

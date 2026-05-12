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
        let var_type = proc.local_vars.get(ref_name).cloned();
        let mut val = expr_to_java(value, proc);
        val = coerce_for_type(&val, var_type.as_deref());
        return format!("{} = {};", camel, val);
    }
    let var = expr_to_java(target, proc);
    let val = expr_to_java(value, proc);
    format!("{} = {};", var, val)
}

fn coerce_for_type(expr: &str, target_type: Option<&str>) -> String {
    let trimmed = expr.trim();
    match target_type {
        Some(t) if t.contains("BigDecimal") && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("java.math.BigDecimal.valueOf({})", trimmed)
        }
        Some(t) if t.contains("BigDecimal")
            && trimmed.contains('.') && !trimmed.starts_with('"')
            && !trimmed.contains(".multiply(")
            && !trimmed.contains(".add(")
            && !trimmed.contains(".subtract(")
            && !trimmed.contains(".divide(")
            && !trimmed.contains("BigDecimal.valueOf(")
            && !trimmed.contains("BigDecimal.ZERO")
            && !trimmed.contains("BigDecimal.ONE")
            && !trimmed.starts_with("new java.math.BigDecimal") => {
            format!("java.math.BigDecimal.valueOf({})", trimmed)
        }
        Some(t) if t == "Long" && trimmed.chars().all(|c| c.is_ascii_digit()) => {
            format!("Long.valueOf({})", trimmed)
        }
        Some(t) if t == "String" && !trimmed.starts_with('"') && trimmed != "null" => {
            format!("String.valueOf({})", trimmed)
        }
        _ => expr.to_string()
    }
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
            format!("{}.get(\"{}\")", expr_to_java(object, proc), snake_to_camel(field))
        }
        Expr::CursorAttribute { cursor, attribute } => {
            let cursor_name = match cursor.as_ref() {
                Expr::ColumnRef(name) | Expr::PlVariable(name) => name.join("."),
                _ => "cursor".into(),
            };
            match attribute {
                ogsql_parser::ast::CursorAttributeKind::RowCount => "__ROWCOUNT__".into(),
                ogsql_parser::ast::CursorAttributeKind::Found => "found".into(),
                ogsql_parser::ast::CursorAttributeKind::NotFound => "!found".into(),
                ogsql_parser::ast::CursorAttributeKind::IsOpen => format!("{} != null", cursor_name),
                ogsql_parser::ast::CursorAttributeKind::BulkExceptions => "java.util.Collections.emptyList()".into(),
            }
        }
        Expr::Default => "null".into(),
        Expr::Prior(_) => "null".into(),
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
        "current_timestamp" => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
        _ => {
            if name.contains('.') {
                let parts: Vec<&str> = name.splitn(2, '.').collect();
                let var_name = snake_to_camel(parts[0]);
                let field = snake_to_camel(parts[1]);
                let is_out = proc.parameters.iter().any(|p| p.name == parts[0] && p.is_out());
                if is_out {
                    format!("{}.get().get(\"{}\")", var_name, field)
                } else {
                    format!("{}.get(\"{}\")", var_name, field)
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
    // Check if expression already contains BigDecimal method calls (intermediate result)
    if name.contains(".multiply(") || name.contains(".add(") || name.contains(".subtract(") || name.contains(".divide(") {
        return true;
    }
    false
}

fn wrap_bigdecimal(expr: &str, already_bd: bool, _proc: &ProcedureInfo) -> String {
    if already_bd {
        return expr.to_string();
    }
    let trimmed = expr.trim();
    if trimmed.contains(".get(") {
        // .get() returns Object, cast to BigDecimal instead of using valueOf()
        format!("((java.math.BigDecimal) {})", trimmed)
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

fn binary_op_to_java(left: &ogsql_parser::ast::Expr, op: &str, right: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let mut l = expr_to_java(left, proc);
    let mut r = expr_to_java(right, proc);

    let is_arith = matches!(op, "*" | "+" | "-" | "/");
    if is_arith {
        if l == "null" {
            l = "(0 /* null */)".into();
        }
        if r == "null" {
            r = "(0 /* null */)".into();
        }

        // When arithmetic involves a String-typed operand, the PL/SQL is building
        // a SQL fragment (e.g. to_char(sysdate-v_date-i,'yyyymmdd')). Fall back to
        // string concatenation to produce valid Java.
        let l_is_string = is_string_var(&l, proc) || l.starts_with('"');
        let r_is_string = is_string_var(&r, proc) || r.starts_with('"');
        if l_is_string || r_is_string {
            return format!("\"\" + {} + {}", l, r);
        }
    }

    match op {
        "||" => format!("String.valueOf({}).concat(String.valueOf({}))", l, r),
        "<@" | "@>" => format!("((Object) {}) != null", l),
        "=" => format!("java.util.Objects.equals({}, {})", l, r),
        "!=" | "<>" => format!("!java.util.Objects.equals({}, {})", l, r),
        "AND" => format!("({} && {})", l, r),
        "OR" => format!("({} || {})", l, r),
        "IS" => format!("{} == {}", l, r),
        "IS NOT" => format!("{} != {}", l, r),
        ">" | "<" | ">=" | "<=" if is_bigdecimal_var(&l, proc) || is_bigdecimal_var(&r, proc) => {
            let cmp_method = match op {
                ">" => " > 0",
                "<" => " < 0",
                ">=" => " >= 0",
                "<=" => " <= 0",
                _ => " != 0",
            };
            let r_bd = if r.chars().all(|c| c.is_ascii_digit() || c == '.') {
                format!("java.math.BigDecimal.valueOf({})", r)
            } else if r == "0" {
                "java.math.BigDecimal.ZERO".to_string()
            } else {
                r.clone()
            };
            format!("{}.compareTo({}){}", l, r_bd, cmp_method)
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
        _ => {
            let has_get_l = l.contains(".get(");
            let has_get_r = r.contains(".get(");
            let is_comparison = matches!(op, ">" | "<" | ">=" | "<=");
            let (l_out, r_out) = if is_comparison {
                let lo = if has_get_l {
                    let l_base = l.split(".get(").next().unwrap_or(&l);
                    if is_string_var(l_base, proc) {
                        format!("Long.valueOf({}.toString())", l)
                    } else {
                        format!("((Number) {}).longValue()", l)
                    }
                } else { l.clone() };
                let ro = if has_get_r {
                    let r_base = r.split(".get(").next().unwrap_or(&r);
                    if is_string_var(r_base, proc) {
                        format!("Long.valueOf({}.toString())", r)
                    } else {
                        format!("((Number) {}).longValue()", r)
                    }
                } else { r.clone() };
                (lo, ro)
            } else {
                (l.clone(), r.clone())
            };
            format!("{} {} {}", l_out, op, r_out)
        }
    }
}

fn unary_op_to_java(op: &str, operand: &ogsql_parser::ast::Expr, proc: &ProcedureInfo) -> String {
    let inner = expr_to_java(operand, proc);
    match op {
        "-" => format!("(-{})", inner),
        "NOT" => format!("!{}", inner),
        _ => format!("{}{}", op, inner),
    }
}

fn function_call_to_java(name: &str, args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
    let upper = name.to_uppercase();
    let jargs: Vec<String> = args.iter().map(|a| expr_to_java(a, proc)).collect();
    match upper.as_str() {
        "NVL" | "COALESCE" if jargs.len() >= 2 => {
            format!("({} != null ? {} : {})", jargs[0], jargs[0], jargs[1])
        }
        "UPPER" => format!("{}.toUpperCase()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "LOWER" => format!("{}.toLowerCase()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "TRIM" => format!("{}.trim()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "LENGTH" => format!("{}.length()", jargs.first().map(|s| s.as_str()).unwrap_or("\"\"")),
        "ABS" => format!("Math.abs({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "FLOOR" => format!("Math.floor({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "CEIL" | "CEILING" => format!("Math.ceil({})", jargs.first().map(|s| s.as_str()).unwrap_or("0")),
        "ROUND" => {
            if jargs.len() >= 2 {
                format!("Math.round({} * Math.pow(10, {})) / Math.pow(10, {})", jargs[0], jargs[1], jargs[1])
            } else {
                format!("Math.round({})", jargs.first().map(|s| s.as_str()).unwrap_or("0"))
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
                format!("{}.substring({} - 1, {} - 1 + {})", jargs[0], jargs[1], jargs[1], jargs[2])
            } else if jargs.len() >= 2 {
                format!("{}.substring({} - 1)", jargs[0], jargs[1])
            } else {
                "null".into()
            }
        }
        "SPLIT_PART" if jargs.len() >= 3 => format!("((String[]){}.split({}))[{} - 1]", jargs[0], jargs[1], jargs[2]),
        "TO_CHAR" => format!("String.valueOf({})", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "ARRAY_LENGTH" | "ARRAY_UPPER" => format!("((Object[]){}).length", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "ARRAY_APPEND" => format!("/* ARRAY_APPEND */ {}", jargs.first().map(|s| s.as_str()).unwrap_or("null")),
        "ADD_MONTHS" if jargs.len() >= 2 => format!("/* ADD_MONTHS({}, {}) */ null", jargs[0], jargs[1]),
        "LAST_DAY" => format!("/* LAST_DAY({}) */ null", jargs.first().map(|s| s.as_str()).unwrap_or("")),
        "NEXT_DAY" => "/* NEXT_DAY */ null".into(),
        "EXTRACT" => "/* EXTRACT */ 0".into(),
        "AGE" | "DATE_TRUNC" | "TO_TIMESTAMP" | "MAKE_DATE" | "MAKE_TIMESTAMP" => format!("/* {} */ null", upper),
        "ROW_NUMBER" | "RANK" | "DENSE_RANK" | "COUNT" | "SUM" | "AVG" | "MIN" | "MAX" => format!("/* aggregate:{} */ null", upper),
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
        "LEFT" if jargs.len() >= 2 => format!("{}.substring(0, {})", jargs[0], jargs[1]),
        "RIGHT" if jargs.len() >= 2 => format!("{}.substring({}.length() - {})", jargs[0], jargs[0], jargs[1]),
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
        "SYSDATE" | "CURRENT_TIMESTAMP" | "NOW" => "new java.sql.Timestamp(System.currentTimeMillis())".into(),
        "CURRENT_DATE" => "new java.sql.Date(System.currentTimeMillis())".into(),
        "CHR" if !jargs.is_empty() => format!("String.valueOf((char)({}))", jargs[0]),
        "ASCII" if !jargs.is_empty() => format!("(int){}.charAt(0)", jargs[0]),
        "TO_NUMBER" => format!("new BigDecimal({})", jargs.first().map(|s| s.as_str()).unwrap_or("\"0\"")),
        "INSTR" if jargs.len() >= 2 => format!("{}.indexOf({}) + 1", jargs[0], jargs[1]),
        _ => "null".into(),
    }
}

fn special_function_to_java(name: &str, args: &[ogsql_parser::ast::Expr], proc: &ProcedureInfo) -> String {
        format!("null")
}

fn like_to_java(expr: &ogsql_parser::ast::Expr, pattern: &ogsql_parser::ast::Expr, negated: bool, proc: &ProcedureInfo) -> String {
    let left = expr_to_java(expr, proc);
    let right = expr_to_java(pattern, proc);
    if negated { format!("!{}.matches({})", left, right) } else { format!("{}.matches({})", left, right) }
}

fn type_cast_to_java(expr: &ogsql_parser::ast::Expr, type_name: &str, proc: &ProcedureInfo) -> String {
    let inner = expr_to_java(expr, proc);
    let lower = type_name.to_lowercase();
    match lower.as_str() {
        s if s.contains("bigint") || s.contains("int8") => format!("((Long) {})", inner),
        s if s.contains("integer") || s == "int" || s == "int4" => format!("((Integer) {})", inner),
        s if s.contains("numeric") || s.contains("decimal") => format!("((java.math.BigDecimal) {})", inner),
        s if s.contains("varchar") || s.contains("text") || s.contains("char") => format!("String.valueOf({})", inner),
        s if s.contains("bool") => format!("((Boolean) {})", inner),
        s if s.contains("timestamp") => format!("((java.sql.Timestamp) {})", inner),
        s if s.contains("date") => format!("((java.sql.Date) {})", inner),
        _ => format!("((Object) {})", inner),
    }
}

fn is_integer_type(expr_str: &str, proc: &ProcedureInfo) -> bool {
    let name = expr_str.trim();
    let base = name.split(|c: char| c == '.' || c == '(').next().unwrap_or(name);
    let ty = proc.local_vars.get(base)
        .or_else(|| proc.parameters.iter().find(|p| p.name == base).map(|p| &p.java_type));
    match ty {
        Some(t) => {
            let t = t.as_str();
            t == "int" || t == "long" || t == "Integer" || t == "Long"
        }
        None => false,
    }
}

fn case_to_java(
    operand: &Option<Box<ogsql_parser::ast::Expr>>,
    whens: &[ogsql_parser::ast::WhenClause],
    else_expr: &Option<Box<ogsql_parser::ast::Expr>>,
    proc: &ProcedureInfo,
) -> String {
    let mut parts = Vec::new();
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
        let result = expr_to_java(&when.result, proc);
        if i == 0 { parts.push(format!("({} ? {} ", cond, result)); }
        else { parts.push(format!(": {} ? {} ", cond, result)); }
    }
    match else_expr {
        Some(e) => parts.push(format!(": {})", expr_to_java(e, proc))),
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

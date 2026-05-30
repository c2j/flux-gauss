/// Sanitize an identifier for Java: strip non-alphanumeric chars, prefix digits, escape keywords.
pub fn java_safe_identifier(s: &str) -> String {
    let s: String = s.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
    if s.is_empty() || s == "_" {
        return "_unnamed".to_string();
    }
    let s = if s.starts_with(|c: char| c.is_ascii_digit()) {
        format!("_{}", s)
    } else {
        s
    };

    const JAVA_KEYWORDS: &[&str] = &[
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
        "class", "const", "continue", "default", "do", "double", "else", "enum",
        "extends", "final", "finally", "float", "for", "goto", "if", "implements",
        "import", "instanceof", "int", "interface", "long", "native", "new", "package",
        "private", "protected", "public", "return", "short", "static", "strictfp",
        "super", "switch", "synchronized", "this", "throw", "throws", "transient",
        "try", "void", "volatile", "while", "true", "false", "null",
        "old", "new", "raise",
    ];

    if JAVA_KEYWORDS.contains(&s.to_lowercase().as_str()) {
        format!("_{}", s)
    } else {
        s
    }
}

/// Convert snake_case to camelCase.
/// Examples: "create_order" → "createOrder", "status" → "status", "" → ""
pub fn snake_to_camel(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut capitalize_next = false;
    for c in s.chars() {
        if c == '_' {
            capitalize_next = true;
        } else if capitalize_next {
            result.push(c.to_ascii_uppercase());
            capitalize_next = false;
        } else {
            result.push(c);
        }
    }
    result
}

/// Convert snake_case to PascalCase.
/// Examples: "create_order" → "CreateOrder", "status" → "Status"
pub fn snake_to_pascal(s: &str) -> String {
    let camel = snake_to_camel(s);
    let mut chars = camel.chars();
    let pascal = match chars.next() {
        Some(c) => c.to_ascii_uppercase().to_string() + chars.as_str(),
        None => String::new(),
    };
    java_safe_identifier(&pascal)
}

/// Extract the Java class name from a SQL package name.
/// Strips schema prefix and common prefixes ("pkg_", "PKG_", "pack_"), then lowercases before PascalCase.
/// Examples: "bigfund.PKG_2008802001_MGT" → "_2008802001Mgt", "pkg_order" → "Order"
pub fn package_to_classname(pkg_name: &str) -> String {
    let short_name = pkg_name.rsplit('.').next().unwrap_or(pkg_name);
    let stripped = if short_name.starts_with("pkg_") {
        &short_name[4..]
    } else if short_name.starts_with("PKG_") {
        &short_name[4..]
    } else if short_name.starts_with("pack_") {
        &short_name[5..]
    } else {
        short_name
    };
    snake_to_pascal(&stripped.to_lowercase())
}

/// Convert a SQL procedure/function name to a Java method name.
/// Strips common "p_" or "v_" prefixes, then applies camelCase.
pub fn java_method_name(proc_name: &str) -> String {
    let lower = proc_name.to_lowercase();
    let stripped = if lower.starts_with("p_") {
        &proc_name[2..]
    } else if lower.starts_with("v_") {
        &proc_name[2..]
    } else {
        proc_name
    };
    java_safe_identifier(&snake_to_camel(stripped))
}

pub fn java_method_to_snake(method_name: &str) -> String {
    let mut result = String::new();
    for (i, c) in method_name.char_indices() {
        if c.is_uppercase() {
            if i > 0 {
                result.push('_');
            }
            result.extend(c.to_lowercase());
        } else {
            result.push(c);
        }
    }
    result
}

pub fn classname_to_package(class_name: &str) -> String {
    format!("pkg_{}", java_method_to_snake(class_name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_snake_to_camel_simple() {
        assert_eq!(snake_to_camel("create_order"), "createOrder");
    }

    #[test]
    fn test_snake_to_camel_single_word() {
        assert_eq!(snake_to_camel("status"), "status");
    }

    #[test]
    fn test_snake_to_camel_empty() {
        assert_eq!(snake_to_camel(""), "");
    }

    #[test]
    fn test_snake_to_camel_multiple_underscores() {
        assert_eq!(snake_to_camel("a_b_c"), "aBC");
    }

    #[test]
    fn test_snake_to_camel_trailing_underscore() {
        assert_eq!(snake_to_camel("foo_"), "foo");
    }

    #[test]
    fn test_snake_to_camel_leading_underscore() {
        assert_eq!(snake_to_camel("_foo"), "Foo");
    }

    #[test]
    fn test_snake_to_pascal_simple() {
        assert_eq!(snake_to_pascal("create_order"), "CreateOrder");
    }

    #[test]
    fn test_snake_to_pascal_single_word() {
        assert_eq!(snake_to_pascal("status"), "Status");
    }

    #[test]
    fn test_snake_to_pascal_empty() {
        assert_eq!(snake_to_pascal(""), "_unnamed");
    }

    #[test]
    fn test_snake_to_pascal_strips_dots() {
        assert_eq!(snake_to_pascal("bigfund.packlog"), "Bigfundpacklog");
    }

    #[test]
    fn test_package_to_classname_with_pkg_prefix() {
        assert_eq!(package_to_classname("pkg_order"), "Order");
    }

    #[test]
    fn test_package_to_classname_with_pack_prefix() {
        assert_eq!(package_to_classname("pack_inventory"), "Inventory");
    }

    #[test]
    fn test_package_to_classname_without_prefix() {
        assert_eq!(package_to_classname("inventory"), "Inventory");
    }

    #[test]
    fn test_package_to_classname_with_p_prefix() {
        assert_eq!(package_to_classname("p_user"), "PUser");
    }

    #[test]
    fn test_package_to_classname_schema_prefix() {
        assert_eq!(package_to_classname("bigfund.pkg_order"), "Order");
    }

    #[test]
    fn test_package_to_classname_uppercase() {
        assert_eq!(package_to_classname("PKG_WARPDRIVER_STRESS_TEST"), "WarpdriverStressTest");
    }

    #[test]
    fn test_package_to_classname_schema_uppercase() {
        assert_eq!(package_to_classname("BIGFUND.PKG_2008802001_MGT"), "_2008802001Mgt");
    }

    #[test]
    fn test_package_to_classname_pack_uppercase() {
        assert_eq!(package_to_classname("BIGFUND.PACK_LOG"), "PackLog");
    }

    #[test]
    fn test_package_to_classname_mixed_case() {
        assert_eq!(package_to_classname("proc_GOto"), "ProcGoto");
    }

    #[test]
    fn test_java_method_name_simple() {
        assert_eq!(java_method_name("create_order"), "createOrder");
    }

    #[test]
    fn test_java_method_name_with_p_prefix() {
        assert_eq!(java_method_name("p_user_id"), "userId");
    }

    #[test]
    fn test_java_method_name_without_prefix() {
        assert_eq!(java_method_name("get_status"), "getStatus");
    }
}

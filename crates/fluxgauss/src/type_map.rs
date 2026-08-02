/// SQL type name → Java type name
pub static SQL_TO_JAVA: &[(&str, &str)] = &[
    ("bigint", "Long"),
    ("biginteger", "Long"),
    ("integer", "Integer"),
    ("int", "Integer"),
    ("int4", "Integer"),
    ("int8", "Long"),
    ("smallint", "Integer"),
    ("serial", "Integer"),
    ("bigserial", "Long"),
    ("number", "Long"),
    ("numeric", "java.math.BigDecimal"),
    ("decimal", "java.math.BigDecimal"),
    ("real", "Float"),
    ("float4", "Float"),
    ("float8", "Double"),
    ("double precision", "Double"),
    ("double", "Double"),
    ("varchar", "String"),
    ("varchar2", "String"),
    ("character varying", "String"),
    ("char", "String"),
    ("text", "String"),
    ("string", "String"),
    ("boolean", "Boolean"),
    ("bool", "Boolean"),
    ("timestamp", "java.sql.Timestamp"),
    ("timestamp without time zone", "java.sql.Timestamp"),
    ("timestamp with time zone", "java.sql.Timestamp"),
    ("date", "java.sql.Date"),
    ("time", "java.sql.Time"),
    ("bytea", "byte[]"),
    ("blob", "byte[]"),
    ("clob", "String"),
    ("json", "String"),
    ("jsonb", "String"),
    ("uuid", "String"),
    ("record", "Map<String, Object>"),
    ("exception", "Throwable"),
];

/// SQL type name → MyBatis JdbcType
pub static SQL_TO_JDBC_TYPE: &[(&str, &str)] = &[
    ("bigint", "BIGINT"),
    ("biginteger", "BIGINT"),
    ("integer", "INTEGER"),
    ("int", "INTEGER"),
    ("int4", "INTEGER"),
    ("int8", "BIGINT"),
    ("smallint", "SMALLINT"),
    ("serial", "INTEGER"),
    ("bigserial", "BIGINT"),
    ("number", "NUMERIC"),
    ("numeric", "NUMERIC"),
    ("decimal", "DECIMAL"),
    ("real", "REAL"),
    ("float4", "REAL"),
    ("float8", "DOUBLE"),
    ("double precision", "DOUBLE"),
    ("double", "DOUBLE"),
    ("varchar", "VARCHAR"),
    ("varchar2", "VARCHAR"),
    ("character varying", "VARCHAR"),
    ("char", "CHAR"),
    ("text", "LONGVARCHAR"),
    ("string", "VARCHAR"),
    ("boolean", "BOOLEAN"),
    ("bool", "BOOLEAN"),
    ("timestamp", "TIMESTAMP"),
    ("timestamp without time zone", "TIMESTAMP"),
    ("timestamp with time zone", "TIMESTAMP"),
    ("date", "DATE"),
    ("time", "TIME"),
    ("bytea", "BINARY"),
    ("blob", "BLOB"),
    ("clob", "CLOB"),
    ("json", "VARCHAR"),
    ("jsonb", "VARCHAR"),
    ("uuid", "OTHER"),
    ("exception", "VARCHAR"),
];

/// Look up Java type from SQL type name (case-insensitive)
pub fn sql_type_to_java(sql_type: &str) -> Option<&'static str> {
    let normalized = sql_type.to_lowercase();
    let base = normalized.trim();

    if base.ends_with("[]") {
        return Some("java.util.List<String>");
    }

    SQL_TO_JAVA
        .iter()
        .find(|(k, _)| *k == base)
        .map(|(_, v)| *v)
}

/// Look up JDBC type from SQL type name (case-insensitive)
pub fn sql_type_to_jdbc(sql_type: &str) -> Option<&'static str> {
    let normalized = sql_type.to_lowercase();
    SQL_TO_JDBC_TYPE
        .iter()
        .find(|(k, _)| *k == normalized)
        .map(|(_, v)| *v)
}

/// Infer SQL type from a column name (heuristic, mirrors Python _infer_type_from_column_name).
/// Used as fallback when %TYPE anchoring can't resolve the actual column type.
pub fn infer_sql_type_from_column_name(column_name: &str) -> &'static str {
    let col = column_name.to_lowercase();
    if col.contains("name") || col.contains("txt") || col.contains("text")
        || col.contains("info") || col.contains("desc") || col.contains("msg")
        || col.contains("remark") || col.contains("comment")
    {
        return "varchar";
    }
    if col.contains("id") || col.contains("no") || col.contains("seq") {
        if col.contains("num") {
            return "integer";
        }
        return "bigint";
    }
    if col.contains("amount") || col.contains("balance") || col.contains("price")
        || col.contains("qty") || col.contains("quantity") || col.contains("total")
        || col.contains("salary") || col.contains("pmll") || col.contains("rate")
        || col.contains("digits") || col.contains("scale") || col.contains("days")
    {
        return "numeric";
    }
    if col.contains("date") || col.contains("time") || col.contains("stamp") {
        return "timestamp";
    }
    if col.contains("flag") || col.contains("status") || col.contains("level")
        || col.contains("type") || col.contains("code")
    {
        return "varchar";
    }
    "varchar"
}

/// Map Java type back to JDBC type for MyBatis result mapping
pub fn java_type_to_jdbc(java_type: &str) -> &'static str {
    match java_type {
        "Long" | "long" => "BIGINT",
        "Integer" | "int" => "INTEGER",
        "Float" | "float" => "REAL",
        "Double" | "double" => "DOUBLE",
        "Boolean" | "boolean" => "BOOLEAN",
        "java.sql.Timestamp" => "TIMESTAMP",
        "java.sql.Date" => "DATE",
        "java.sql.Time" => "TIME",
        "java.math.BigDecimal" => "NUMERIC",
        "byte[]" => "BINARY",
        _ => "VARCHAR",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sql_type_to_java_basic() {
        assert_eq!(sql_type_to_java("bigint"), Some("Long"));
        assert_eq!(sql_type_to_java("varchar"), Some("String"));
        assert_eq!(sql_type_to_java("numeric"), Some("java.math.BigDecimal"));
        assert_eq!(sql_type_to_java("boolean"), Some("Boolean"));
    }

    #[test]
    fn test_sql_type_to_java_case_insensitive() {
        assert_eq!(sql_type_to_java("BIGINT"), Some("Long"));
        assert_eq!(sql_type_to_java("VarChar"), Some("String"));
        assert_eq!(sql_type_to_java("TIMESTAMP"), Some("java.sql.Timestamp"));
    }

    #[test]
    fn test_sql_type_to_java_unknown() {
        assert_eq!(sql_type_to_java("unknown_type"), None);
        assert_eq!(sql_type_to_java(""), None);
    }

    #[test]
    fn test_sql_type_to_jdbc_basic() {
        assert_eq!(sql_type_to_jdbc("bigint"), Some("BIGINT"));
        assert_eq!(sql_type_to_jdbc("varchar"), Some("VARCHAR"));
        assert_eq!(sql_type_to_jdbc("numeric"), Some("NUMERIC"));
    }

    #[test]
    fn test_sql_type_to_jdbc_case_insensitive() {
        assert_eq!(sql_type_to_jdbc("INTEGER"), Some("INTEGER"));
        assert_eq!(sql_type_to_jdbc("Boolean"), Some("BOOLEAN"));
    }

    #[test]
    fn test_sql_type_to_jdbc_unknown() {
        assert_eq!(sql_type_to_jdbc("unknown"), None);
    }

    #[test]
    fn test_java_type_to_jdbc() {
        assert_eq!(java_type_to_jdbc("Long"), "BIGINT");
        assert_eq!(java_type_to_jdbc("String"), "VARCHAR");
        assert_eq!(java_type_to_jdbc("java.math.BigDecimal"), "NUMERIC");
        assert_eq!(java_type_to_jdbc("java.sql.Timestamp"), "TIMESTAMP");
        assert_eq!(java_type_to_jdbc("byte[]"), "BINARY");
    }

    #[test]
    fn test_all_sql_to_java_entries_unique() {
        let mut seen = std::collections::HashSet::new();
        for (k, _) in SQL_TO_JAVA {
            assert!(seen.insert(*k), "Duplicate SQL type key: {}", k);
        }
    }

    #[test]
    fn test_all_sql_to_jdbc_entries_unique() {
        let mut seen = std::collections::HashSet::new();
        for (k, _) in SQL_TO_JDBC_TYPE {
            assert!(seen.insert(*k), "Duplicate SQL type key: {}", k);
        }
    }
}

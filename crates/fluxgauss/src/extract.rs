use std::collections::HashMap;
use std::path::Path;

use ogsql_parser::ast::{
    CreateFunctionStatement, CreatePackageBodyStatement, CreatePackageStatement,
    CreateProcedureStatement, PackageFunction, PackageItem, PackageProcedure, RoutineParam,
    Statement, StatementInfo,
};
use ogsql_parser::parser::ParseOutput;

use crate::naming::{java_method_name, package_to_classname, snake_to_camel};
use crate::type_map::{sql_type_to_java, sql_type_to_jdbc};
use crate::types::{
    CommentBlock, ConversionError, CustomTypeInfo, PackageInfo, ParamMode, Parameter,
    ProcedureInfo, SkippedItem, VarInfo,
};

pub struct ExtractionResult {
    pub packages: Vec<PackageInfo>,
    pub skipped: Vec<SkippedItem>,
    pub errors: Vec<ConversionError>,
}

pub fn extract_from_parse_output(
    parse_output: &ParseOutput,
    source_file: &str,
    base_package: &str,
) -> ExtractionResult {
    let mut packages = Vec::new();
    let mut skipped = Vec::new();
    let errors = Vec::new();

    let mut current_pkg_name = String::new();
    let mut procedures = Vec::new();
    let mut package_vars: HashMap<String, VarInfo> = HashMap::new();
    let mut custom_types: HashMap<String, CustomTypeInfo> = HashMap::new();
    let mut table_refs = std::collections::HashSet::new();

    for stmt_info in &parse_output.statements {
        let line_number = stmt_info.start_line as u32;

        match &stmt_info.statement {
            Statement::CreatePackage(pkg_spec) => {
                current_pkg_name = object_name_to_string(&pkg_spec.node.name);
                // Extract package spec items (variables, types, etc.) — not just procedures
                for item in &pkg_spec.node.items {
                    extract_package_item(
                        item,
                        &current_pkg_name,
                        source_file,
                        &mut Vec::new(), // don't collect spec procedures (body has implementations)
                        &mut package_vars,
                        &mut custom_types,
                    );
                }
            }
            Statement::CreatePackageBody(pkg_body) => {
                if !current_pkg_name.is_empty() && !procedures.is_empty() {
                    packages.push(build_package_info(
                        &current_pkg_name,
                        procedures.split_off(0),
                        package_vars.drain().collect(),
                        custom_types.drain().collect(),
                        table_refs.drain().collect(),
                        source_file,
                        base_package,
                    ));
                }
                current_pkg_name = object_name_to_string(&pkg_body.node.name);

                for item in &pkg_body.node.items {
                    extract_package_item(
                        item,
                        &current_pkg_name,
                        source_file,
                        &mut procedures,
                        &mut package_vars,
                        &mut custom_types,
                    );
                }
            }
            Statement::CreateProcedure(proc_stmt) => {
                let full_obj_name = object_name_to_string(&proc_stmt.node.name);
                let (pkg_name, proc_name) = if full_obj_name.contains('.') {
                    let parts: Vec<&str> = full_obj_name.split('.').collect();
                    (parts[..parts.len()-1].join("."), parts.last().unwrap().to_string())
                } else {
                    (current_pkg_name.clone(), full_obj_name.clone())
                };
                let full_name = if pkg_name.is_empty() {
                    proc_name.clone()
                } else {
                    format!("{}.{}", pkg_name, proc_name)
                };

                let params = convert_params(&proc_stmt.node.parameters);
                let proc_info = build_procedure_info(
                    full_name,
                    pkg_name,
                    proc_name,
                    false,
                    None,
                    params,
                    &stmt_info.sql_text,
                    source_file,
                    line_number,
                    proc_stmt.node.block.clone(),
                );
                procedures.push(proc_info);
            }
            Statement::CreateFunction(func_stmt) => {
                let full_obj_name = object_name_to_string(&func_stmt.node.name);
                let (pkg_name, proc_name) = if full_obj_name.contains('.') {
                    let parts: Vec<&str> = full_obj_name.split('.').collect();
                    (parts[..parts.len()-1].join("."), parts.last().unwrap().to_string())
                } else {
                    (current_pkg_name.clone(), full_obj_name.clone())
                };
                let full_name = if pkg_name.is_empty() {
                    proc_name.clone()
                } else {
                    format!("{}.{}", pkg_name, proc_name)
                };

                let params = convert_params(&func_stmt.node.parameters);
                let return_type = func_stmt.node.return_type.as_deref().and_then(|rt| {
                    sql_type_to_java(rt).map(|s| s.to_string())
                });
                let proc_info = build_procedure_info(
                    full_name,
                    pkg_name,
                    proc_name,
                    true,
                    return_type,
                    params,
                    &stmt_info.sql_text,
                    source_file,
                    line_number,
                    func_stmt.node.block.clone(),
                );
                procedures.push(proc_info);
            }
            Statement::CreateTable(ct) => {
                let table_name = object_name_to_string(&ct.node.name);
                table_refs.insert(table_name.clone());
                skipped.push(SkippedItem {
                    item_type: "DDL".into(),
                    name: format!("CREATE TABLE {}", table_name),
                    source_file: source_file.into(),
                    line_number,
                    reason: "Table creation not converted".into(),
                });
            }
            Statement::Grant(_) => {
                skipped.push(SkippedItem {
                    item_type: "GRANT".into(),
                    name: stmt_info.sql_text.chars().take(80).collect(),
                    source_file: source_file.into(),
                    line_number,
                    reason: "GRANT not converted".into(),
                });
            }
            _ => {
                if !stmt_info.sql_text.trim().is_empty() {
                    skipped.push(SkippedItem {
                        item_type: "OTHER".into(),
                        name: stmt_info.sql_text.chars().take(60).collect(),
                        source_file: source_file.into(),
                        line_number,
                        reason: "Non-procedure statement skipped".into(),
                    });
                }
            }
        }
    }

    if !procedures.is_empty() {
        let pkg_name = if current_pkg_name.is_empty() {
            std::path::Path::new(source_file)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("unknown")
                .to_string()
        } else {
            current_pkg_name
        };
        packages.push(build_package_info(
            &pkg_name,
            procedures,
            package_vars.into_iter().collect(),
            custom_types.into_iter().collect(),
            table_refs,
            source_file,
            base_package,
        ));
    }

    ExtractionResult {
        packages,
        skipped,
        errors,
    }
}

fn extract_package_item(
    item: &PackageItem,
    pkg_name: &str,
    source_file: &str,
    procedures: &mut Vec<ProcedureInfo>,
    package_vars: &mut HashMap<String, VarInfo>,
    custom_types: &mut HashMap<String, CustomTypeInfo>,
) {
    match item {
        PackageItem::Procedure(proc) => {
            let proc_name = object_name_to_string(&proc.name);
            let full_name = format!("{}.{}", pkg_name, proc_name);
            let params = convert_params(&proc.parameters);
            let proc_info = build_procedure_info(
                full_name,
                pkg_name.to_string(),
                proc_name,
                false,
                None,
                params,
                "",
                source_file,
                proc.start_line as u32,
                proc.block.clone(),
            );
            procedures.push(proc_info);
        }
        PackageItem::Function(func) => {
            let proc_name = object_name_to_string(&func.name);
            let full_name = format!("{}.{}", pkg_name, proc_name);
            let params = convert_params(&func.parameters);
            let return_type = func.return_type.as_deref().and_then(|rt| {
                sql_type_to_java(rt).map(|s| s.to_string())
            });
            let proc_info = build_procedure_info(
                full_name,
                pkg_name.to_string(),
                proc_name,
                true,
                return_type,
                params,
                "",
                source_file,
                func.start_line as u32,
                func.block.clone(),
            );
            procedures.push(proc_info);
        }
        PackageItem::Variable(var_decl) => {
            let sql_type = format_pl_data_type(&var_decl.data_type);
            let java_type = sql_type_to_java(&sql_type)
                .map(|s| s.to_string())
                .unwrap_or_else(|| "Object".into());
            let default_value = var_decl.default.as_ref().and_then(|expr| {
                use ogsql_parser::ast::Expr;
                match expr {
                    Expr::Literal(lit) => match lit {
                        ogsql_parser::ast::Literal::Integer(n) => Some(n.to_string()),
                        ogsql_parser::ast::Literal::Float(f) => Some(f.clone()),
                        ogsql_parser::ast::Literal::String(s) => Some(format!("\"{}\"", s)),
                        ogsql_parser::ast::Literal::Boolean(b) => Some(if *b { "true".into() } else { "false".into() }),
                        ogsql_parser::ast::Literal::Null => Some("null".into()),
                        _ => None,
                    },
                    _ => None,
                }
            });
            package_vars.insert(
                var_decl.name.clone(),
                VarInfo {
                    name: var_decl.name.clone(),
                    java_type,
                    sql_type,
                    default_value,
                    is_constant: var_decl.constant,
                },
            );
        }
        PackageItem::Type(type_decl) => {
            use ogsql_parser::ast::plpgsql::PlTypeDecl;
            match type_decl {
                PlTypeDecl::Record { name, fields } => {
                    let java_fields: Vec<(String, String)> = fields
                        .iter()
                        .map(|f| {
                            let sql_t = format_pl_data_type(&f.data_type);
                            let jt = sql_type_to_java(&sql_t)
                                .map(|s| s.to_string())
                                .unwrap_or_else(|| "Object".into());
                            (f.name.clone(), jt)
                        })
                        .collect();
                    custom_types.insert(
                        name.clone(),
                        CustomTypeInfo {
                            fields: java_fields,
                            is_record: true,
                        },
                    );
                }
                PlTypeDecl::TableOf { name, elem_type, .. } => {
                    let sql_t = format_pl_data_type(elem_type);
                    let jt = sql_type_to_java(&sql_t)
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| "Object".into());
                    custom_types.insert(
                        name.clone(),
                        CustomTypeInfo {
                            fields: vec![("element".into(), jt)],
                            is_record: false,
                        },
                    );
                }
                PlTypeDecl::VarrayOf { name, elem_type, .. } => {
                    let sql_t = format_pl_data_type(elem_type);
                    let jt = sql_type_to_java(&sql_t)
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| "Object".into());
                    custom_types.insert(
                        name.clone(),
                        CustomTypeInfo {
                            fields: vec![("element".into(), jt)],
                            is_record: false,
                        },
                    );
                }
                PlTypeDecl::RefCursor { name } => {
                    custom_types.insert(
                        name.clone(),
                        CustomTypeInfo {
                            fields: Vec::new(),
                            is_record: false,
                        },
                    );
                }
            }
        }
        PackageItem::Raw(_) => {}
    }
}

pub fn format_pl_data_type(dt: &ogsql_parser::ast::plpgsql::PlDataType) -> String {
    use ogsql_parser::ast::plpgsql::PlDataType;
    match dt {
        PlDataType::TypeName(name) => name.clone(),
        PlDataType::PercentType { table, column } => {
            format!("{}.{}%TYPE", table, column)
        }
        PlDataType::PercentRowType(table) => format!("{}%ROWTYPE", table),
        PlDataType::Record => "RECORD".into(),
        PlDataType::Cursor => "CURSOR".into(),
        PlDataType::RefCursor => "REFCURSOR".into(),
    }
}

fn convert_params(params: &[RoutineParam]) -> Vec<Parameter> {
    params
        .iter()
        .map(|p| {
            let mode = parse_param_mode(p.mode.as_deref());
            let sql_type = normalize_sql_type(&p.data_type);
            let java_type = sql_type_to_java(&sql_type)
                .map(|s| s.to_string())
                .unwrap_or_else(|| "Object".into());
            Parameter {
                name: p.name.clone(),
                java_type,
                sql_type,
                mode,
            }
        })
        .collect()
}

fn parse_param_mode(mode: Option<&str>) -> Option<ParamMode> {
    match mode.map(|m| m.to_uppercase()).as_deref() {
        Some("IN") => Some(ParamMode::In),
        Some("OUT") => Some(ParamMode::Out),
        Some("INOUT") | Some("IN OUT") => Some(ParamMode::InOut),
        None => Some(ParamMode::In),
        _ => Some(ParamMode::In),
    }
}

pub fn normalize_sql_type(sql_type: &str) -> String {
    let lower = sql_type.to_lowercase();
    let trimmed = lower.trim();
    if trimmed.starts_with("character varying") {
        return "varchar".into();
    }
    if trimmed.starts_with("character(") || trimmed == "character" {
        return "char".into();
    }
    if trimmed.starts_with("numeric(") {
        return "numeric".into();
    }
    if trimmed.starts_with("decimal(") {
        return "numeric".into();
    }
    if trimmed.starts_with("varchar(") {
        return "varchar".into();
    }
    if trimmed.starts_with("bigint(") || trimmed == "bigint" {
        return "bigint".into();
    }
    if trimmed.starts_with("integer") || trimmed == "int" || trimmed == "int4" {
        return "integer".into();
    }
    if trimmed.starts_with("smallint") {
        return "smallint".into();
    }
    if trimmed.starts_with("timestamp") {
        return "timestamp".into();
    }
    if trimmed == "bool" {
        return "boolean".into();
    }
    if trimmed == "float8" || trimmed == "double precision" {
        return "double precision".into();
    }
    if trimmed == "float4" || trimmed == "real" {
        return "real".into();
    }
    if trimmed == "int2" {
        return "smallint".into();
    }
    if trimmed == "int8" {
        return "bigint".into();
    }
    if trimmed == "string" {
        return "text".into();
    }
    trimmed.into()
}

fn object_name_to_string(name: &ogsql_parser::ast::ObjectName) -> String {
    name.join(".")
}

fn build_procedure_info(
    name: String,
    package: String,
    proc_name: String,
    is_function: bool,
    return_type: Option<String>,
    parameters: Vec<Parameter>,
    sql_text: &str,
    source_file: &str,
    start_line: u32,
    body: Option<ogsql_parser::ast::plpgsql::PlBlock>,
) -> ProcedureInfo {
    let mut proc = ProcedureInfo::new(name, package, proc_name);
    proc.is_function = is_function;
    proc.return_type = return_type;
    proc.parameters = parameters;
    proc.sql_text = sql_text.into();
    proc.body = body;
    proc.source_file = source_file.into();
    proc.source_path = source_file.into();
    proc.source_start_line = start_line;
    proc
}

fn build_package_info(
    package_name: &str,
    procedures: Vec<ProcedureInfo>,
    package_vars: HashMap<String, VarInfo>,
    custom_types: HashMap<String, CustomTypeInfo>,
    table_refs: std::collections::HashSet<String>,
    source_file: &str,
    base_package: &str,
) -> PackageInfo {
    let java_package = format!("{}.service", base_package);
    let procedures = dedup_procedures(procedures);
    PackageInfo {
        package_name: package_name.into(),
        procedures,
        table_refs,
        package_vars,
        source_file: source_file.into(),
        comments: Vec::new(),
        java_package,
        custom_types,
    }
}

fn dedup_procedures(procs: Vec<ProcedureInfo>) -> Vec<ProcedureInfo> {
    let mut seen: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    let mut result: Vec<ProcedureInfo> = Vec::new();
    for proc in procs {
        let short_name = proc.name.split('.').last().unwrap_or(&proc.name);
        let key = crate::naming::java_method_name(short_name);
        if let Some(&existing_idx) = seen.get(&key) {
            let existing_has_body = result[existing_idx].body.is_some();
            let new_has_body = proc.body.is_some();
            if new_has_body && !existing_has_body {
                result[existing_idx] = proc;
            }
        } else {
            seen.insert(key, result.len());
            result.push(proc);
        }
    }
    result
}

pub fn map_comments(
    comments: &[ogsql_parser::parser::CommentInfo],
    procedures: &mut [ProcedureInfo],
) {
    for comment in comments {
        let block = CommentBlock {
            text: comment.text.clone(),
            start_line: comment.line as u32,
            end_line: comment.end_line as u32,
            is_block: comment.comment_type == "block",
        };

        for proc in procedures.iter_mut() {
            if comment.end_line < proc.source_start_line as usize {
                let gap = proc.source_start_line as usize - comment.end_line;
                if gap <= 5 {
                    proc.leading_comments.push(block.clone());
                }
            } else if comment.line >= proc.source_start_line as usize
                && comment.line <= proc.source_end_line as usize
            {
                proc.inline_comments.push(block.clone());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_param_mode() {
        assert_eq!(parse_param_mode(Some("IN")), Some(ParamMode::In));
        assert_eq!(parse_param_mode(Some("OUT")), Some(ParamMode::Out));
        assert_eq!(parse_param_mode(Some("INOUT")), Some(ParamMode::InOut));
        assert_eq!(parse_param_mode(Some("IN OUT")), Some(ParamMode::InOut));
        assert_eq!(parse_param_mode(None), Some(ParamMode::In));
    }

    #[test]
    fn test_normalize_sql_type_varchar() {
        assert_eq!(normalize_sql_type("VARCHAR(100)"), "varchar");
        assert_eq!(normalize_sql_type("character varying(200)"), "varchar");
    }

    #[test]
    fn test_normalize_sql_type_numeric() {
        assert_eq!(normalize_sql_type("NUMERIC(10,2)"), "numeric");
        assert_eq!(normalize_sql_type("DECIMAL(10,2)"), "numeric");
    }

    #[test]
    fn test_normalize_sql_type_integers() {
        assert_eq!(normalize_sql_type("INTEGER"), "integer");
        assert_eq!(normalize_sql_type("int"), "integer");
        assert_eq!(normalize_sql_type("int4"), "integer");
        assert_eq!(normalize_sql_type("BIGINT"), "bigint");
        assert_eq!(normalize_sql_type("int8"), "bigint");
        assert_eq!(normalize_sql_type("SMALLINT"), "smallint");
        assert_eq!(normalize_sql_type("int2"), "smallint");
    }

    #[test]
    fn test_normalize_sql_type_bool() {
        assert_eq!(normalize_sql_type("BOOLEAN"), "boolean");
        assert_eq!(normalize_sql_type("bool"), "boolean");
    }

    #[test]
    fn test_normalize_sql_type_timestamp() {
        assert_eq!(normalize_sql_type("TIMESTAMP"), "timestamp");
        assert_eq!(
            normalize_sql_type("TIMESTAMP WITH TIME ZONE"),
            "timestamp"
        );
    }

    #[test]
    fn test_normalize_sql_type_floats() {
        assert_eq!(normalize_sql_type("double precision"), "double precision");
        assert_eq!(normalize_sql_type("real"), "real");
        assert_eq!(normalize_sql_type("float4"), "real");
        assert_eq!(normalize_sql_type("float8"), "double precision");
    }

    #[test]
    fn test_normalize_sql_type_string() {
        assert_eq!(normalize_sql_type("STRING"), "text");
    }

    #[test]
    fn test_convert_params_basic() {
        let params = vec![RoutineParam {
            name: "p_id".into(),
            mode: Some("IN".into()),
            data_type: "BIGINT".into(),
            default_value: None,
        }];
        let result = convert_params(&params);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].name, "p_id");
        assert_eq!(result[0].java_type, "Long");
        assert_eq!(result[0].sql_type, "bigint");
        assert_eq!(result[0].mode, Some(ParamMode::In));
    }

    #[test]
    fn test_convert_params_out() {
        let params = vec![RoutineParam {
            name: "p_result".into(),
            mode: Some("OUT".into()),
            data_type: "VARCHAR".into(),
            default_value: None,
        }];
        let result = convert_params(&params);
        assert!(result[0].is_out());
        assert_eq!(result[0].java_type, "String");
    }

    #[test]
    fn test_convert_params_unknown_type() {
        let params = vec![RoutineParam {
            name: "p_data".into(),
            mode: None,
            data_type: "custom_type".into(),
            default_value: None,
        }];
        let result = convert_params(&params);
        assert_eq!(result[0].java_type, "Object");
        assert_eq!(result[0].mode, Some(ParamMode::In));
    }

    #[test]
    fn test_extract_no_procedures() {
        use ogsql_parser::{Parser, Tokenizer};
        let sql = "CREATE TABLE orders (id INTEGER PRIMARY KEY, name VARCHAR(100));";
        let tokens = Tokenizer::new(sql).tokenize().unwrap();
        let stmts = Parser::new(tokens).parse();
        let output = ogsql_parser::parser::ParseOutput {
            statements: stmts.into_iter().map(|s| ogsql_parser::ast::StatementInfo {
                sql_text: String::new(),
                start_line: 0,
                start_col: 0,
                end_line: 0,
                end_col: 0,
                statement: s,
            }).collect(),
            errors: Vec::new(),
            comments: Vec::new(),
        };

        let result = extract_from_parse_output(&output, "test.sql", "com.example");
        assert!(result.packages.is_empty());
        assert!(!result.skipped.is_empty());
    }

    #[test]
    fn test_extract_package_body_procedure() {
        use ogsql_parser::{Parser, Tokenizer};
        let sql = r#"
            CREATE OR REPLACE PACKAGE BODY pkg_test AS
                PROCEDURE do_something(p_id IN BIGINT) IS
                BEGIN
                    NULL;
                END;
            END pkg_test;
        "#;
        let tokens = Tokenizer::new(sql).tokenize().unwrap();
        let stmts = Parser::new(tokens).parse();
        let output = ogsql_parser::parser::ParseOutput {
            statements: stmts.into_iter().map(|s| ogsql_parser::ast::StatementInfo {
                sql_text: String::new(),
                start_line: 0,
                start_col: 0,
                end_line: 0,
                end_col: 0,
                statement: s,
            }).collect(),
            errors: Vec::new(),
            comments: Vec::new(),
        };

        let result = extract_from_parse_output(&output, "test.sql", "com.example");
        assert_eq!(result.packages.len(), 1);
        let pkg = &result.packages[0];
        assert_eq!(pkg.package_name, "pkg_test");
        assert!(!pkg.procedures.is_empty());

        let proc = &pkg.procedures[0];
        assert_eq!(proc.proc_name, "do_something");
        assert!(!proc.is_function);
        assert_eq!(proc.parameters.len(), 1);
        assert_eq!(proc.parameters[0].name, "p_id");
        assert_eq!(proc.parameters[0].java_type, "Long");
    }

    #[test]
    fn test_extract_package_body_function() {
        use ogsql_parser::{Parser, Tokenizer};
        let sql = r#"
            CREATE OR REPLACE PACKAGE BODY pkg_math AS
                FUNCTION add_numbers(a IN INTEGER, b IN INTEGER) RETURN INTEGER IS
                BEGIN
                    RETURN a + b;
                END;
            END pkg_math;
        "#;
        let tokens = Tokenizer::new(sql).tokenize().unwrap();
        let stmts = Parser::new(tokens).parse();
        let output = ogsql_parser::parser::ParseOutput {
            statements: stmts.into_iter().map(|s| ogsql_parser::ast::StatementInfo {
                sql_text: String::new(),
                start_line: 0,
                start_col: 0,
                end_line: 0,
                end_col: 0,
                statement: s,
            }).collect(),
            errors: Vec::new(),
            comments: Vec::new(),
        };

        let result = extract_from_parse_output(&output, "test.sql", "com.example");
        assert_eq!(result.packages.len(), 1);
        let proc = &result.packages[0].procedures[0];
        assert!(proc.is_function);
        assert_eq!(proc.parameters.len(), 2);
        assert_eq!(proc.parameters[0].name, "a");
        assert_eq!(proc.parameters[1].name, "b");
    }

    #[test]
    fn test_map_comments_before_procedure() {
        let mut proc = ProcedureInfo::new("pkg.test".into(), "pkg".into(), "test".into());
        proc.source_start_line = 10;

        let comments = vec![ogsql_parser::parser::CommentInfo {
            text: "-- This is a test".into(),
            line: 8,
            end_line: 8,
            column: 1,
            comment_type: "single-line".into(),
        }];

        map_comments(&comments, &mut [proc]);
    }

    #[test]
    fn test_normalize_sql_type_passthrough() {
        assert_eq!(normalize_sql_type("text"), "text");
        assert_eq!(normalize_sql_type("date"), "date");
        assert_eq!(normalize_sql_type("bytea"), "bytea");
    }

    #[test]
    fn test_convert_params_multiple() {
        let params = vec![
            RoutineParam {
                name: "p_id".into(),
                mode: Some("IN".into()),
                data_type: "BIGINT".into(),
                default_value: None,
            },
            RoutineParam {
                name: "p_name".into(),
                mode: Some("IN".into()),
                data_type: "VARCHAR(100)".into(),
                default_value: None,
            },
            RoutineParam {
                name: "p_result".into(),
                mode: Some("OUT".into()),
                data_type: "INTEGER".into(),
                default_value: None,
            },
        ];
        let result = convert_params(&params);
        assert_eq!(result.len(), 3);
        assert_eq!(result[0].java_type, "Long");
        assert_eq!(result[1].java_type, "String");
        assert_eq!(result[2].java_type, "Integer");
        assert!(result[2].is_out());
     }
}

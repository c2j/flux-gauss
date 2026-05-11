use crate::types::{ConversionError, ProcedureInfo};

fn is_terminal_statement(line: &str) -> bool {
    let trimmed = line.trim_start();
    trimmed.starts_with("return ")
        || trimmed.starts_with("return;")
        || trimmed.starts_with("throw ")
        || trimmed.starts_with("break;")
        || trimmed.starts_with("break ")
        || trimmed.starts_with("continue;")
        || trimmed.starts_with("continue ")
}

fn is_unreachable_after_terminal(java_logic_lines: &[String]) -> bool {
    let mut depth: i32 = 0;
    let mut last_terminal: Option<(i32, String)> = None;
    for line in java_logic_lines {
        let t = line.trim_start();
        if t.starts_with("//") || t.is_empty() {
            continue;
        }
        let opens = t.chars().filter(|&c| c == '{').count() as i32;
        let closes = t.chars().filter(|&c| c == '}').count() as i32;
        depth += opens - closes;
        if is_terminal_statement(t) {
            last_terminal = Some((depth, t.to_string()));
        }
    }
    match &last_terminal {
        Some((d, stmt)) => {
            let trimmed = stmt.trim_start();
            if trimmed.starts_with("return") || trimmed.starts_with("throw") {
                return true;
            }
            if trimmed.starts_with("break") {
                return false;
            }
            // continue; — check if there's any break at the same or enclosing scope
            let has_break_at_scope = java_logic_lines.iter().any(|l| {
                let t = l.trim_start();
                t.starts_with("break")
            });
            if has_break_at_scope {
                return false;
            }
            // continue without any break — loop is infinite, code after } is unreachable
            *d <= 1
        }
        None => false,
    }
}

fn is_control_structure_line(line: &str) -> bool {
    let t = line.trim_start();
    t.starts_with('}')
        || t.starts_with("if ")
        || t.starts_with("if(")
        || t.starts_with("} else")
        || t.starts_with("else ")
        || t.starts_with("else{")
        || t.starts_with("while ")
        || t.starts_with("while(")
        || t.starts_with("for ")
        || t.starts_with("for(")
        || t.starts_with("try")
        || t.starts_with("catch")
        || t.starts_with("switch ")
        || t.starts_with("switch(")
}

fn push_logic_line(proc: &mut ProcedureInfo, line: String) {
    let trimmed_line = line.trim_start();
    if is_control_structure_line(&line) {
        proc.java_logic_lines.push(line);
        return;
    }
    if is_unreachable_after_terminal(&proc.java_logic_lines) {
        proc.java_logic_lines.push(format!("// UNREACHABLE: {}", line));
        return;
    }
    proc.java_logic_lines.push(line);
}

pub fn process_statement(
    stmt: &ogsql_parser::ast::plpgsql::PlStatement,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    use ogsql_parser::ast::plpgsql::PlStatement;
    match stmt {
        PlStatement::Assignment { target, expression } => {
            let line = crate::expr::assignment_to_java(target, expression, proc);
            push_logic_line(proc, line);
            Ok(())
        }
        PlStatement::Return { expression } => {
            if let Some(expr) = expression {
                let val = crate::expr::expr_to_java(expr, proc);
                push_logic_line(proc, format!("return {};", val));
            } else if proc.return_type.as_ref().map_or(false, |rt| rt != "void") {
                push_logic_line(proc, "return null;".into());
            } else if proc.parameters.iter().any(|p| p.is_out() && p.is_refcursor()) {
                push_logic_line(proc, "return java.util.Collections.emptyList();".into());
            } else {
                push_logic_line(proc, "return;".into());
            }
            Ok(())
        }
        PlStatement::ReturnNext { expression } => {
            let val = crate::expr::expr_to_java(expression, proc);
            push_logic_line(proc, format!("// return next: {};", val));
            Ok(())
        }
        PlStatement::ReturnQuery(_) => {
            push_logic_line(proc, "// return query;".into());
            Ok(())
        }
        PlStatement::Null => Ok(()),
        PlStatement::Perform { parsed_expr, query, .. } => {
            if let Some(expr) = parsed_expr {
                let val = crate::expr::expr_to_java(expr, proc);
                let trimmed = val.trim();
                if trimmed.starts_with(|c: char| c.is_ascii_digit()) || trimmed == "null" {
                    push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
                } else {
                    push_logic_line(proc, format!("{};", val));
                }
            } else {
                push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
            }
            Ok(())
        }
        PlStatement::Raise(raise_stmt) => {
            let level_str = match &raise_stmt.node.level {
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Exception) => "exception",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Notice) => "notice",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Info) => "info",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Debug) => "debug",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Log) => "log",
                Some(ogsql_parser::ast::plpgsql::RaiseLevel::Warning) => "warning",
                None => "info",
            };
            let msg = raise_stmt.node.message.as_deref().unwrap_or("");
            match level_str {
                "exception" => {
                    proc.imports.insert("import com.example.demo.exception.BusinessException;".into());
                    push_logic_line(proc, format!("throw new BusinessException(\"{}\");", msg));
                }
                "notice" | "info" => {
                    push_logic_line(proc, format!("log.info(\"{}\");", msg));
                }
                "debug" => {
                    push_logic_line(proc, format!("log.debug(\"{}\");", msg));
                }
                "warning" => {
                    push_logic_line(proc, format!("log.warn(\"{}\");", msg));
                }
                _ => {
                    push_logic_line(proc, format!("log.info(\"{}\");", msg));
                }
            }
            Ok(())
        }
        PlStatement::If(if_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
            push_logic_line(proc, format!("if ({}) {{", cond));
            for s in &if_stmt.node.then_stmts {
                process_statement(s, proc)?;
            }
            for elsif in &if_stmt.node.elsifs {
                let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                push_logic_line(proc, format!("}} else if ({}) {{", elsif_cond));
                for s in &elsif.stmts {
                    process_statement(s, proc)?;
                }
            }
            if !if_stmt.node.else_stmts.is_empty() {
                push_logic_line(proc, "} else {".into());
                for s in &if_stmt.node.else_stmts {
                    process_statement(s, proc)?;
                }
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::Case(case_stmt) => {
            if let Some(expr) = &case_stmt.node.expression {
                let case_expr = crate::expr::expr_to_java(expr, proc);
                push_logic_line(proc, format!("// case {}:", case_expr));
            }
            for (i, when) in case_stmt.node.whens.iter().enumerate() {
                let cond = crate::expr::expr_to_java(&when.condition, proc);
                let prefix = if i == 0 { "if" } else { "} else if" };
                push_logic_line(proc, format!("{} ({}) {{", prefix, cond));
                for s in &when.stmts {
                    process_statement(s, proc)?;
                }
            }
            if !case_stmt.node.else_stmts.is_empty() {
                push_logic_line(proc, "} else {".into());
                for s in &case_stmt.node.else_stmts {
                    process_statement(s, proc)?;
                }
            }
            if !case_stmt.node.whens.is_empty() || !case_stmt.node.else_stmts.is_empty() {
                push_logic_line(proc, "}".into());
            }
            Ok(())
        }
        PlStatement::Block(block_stmt) => {
            // Promote inner Block declarations to method-level scope
            for decl in &block_stmt.node.declarations {
                crate::analyze::process_declaration(decl, proc);
            }
            let has_exceptions = block_stmt.node.exception_block.is_some();
            if has_exceptions {
                push_logic_line(proc, "try {".into());
            }
            for s in &block_stmt.node.body {
                process_statement(s, proc)?;
            }
            if let Some(exc_block) = &block_stmt.node.exception_block {
                for handler in &exc_block.handlers {
                    push_logic_line(proc, "} catch (Exception e) {".into());
                    for s in &handler.statements {
                        process_statement(s, proc)?;
                    }
                }
            }
            if has_exceptions {
                push_logic_line(proc, "}".into());
            }
            Ok(())
        }
        PlStatement::Loop(loop_stmt) => {
            if let Some(label) = &loop_stmt.node.label {
                push_logic_line(proc, format!("{}: while (true) {{", label));
            } else {
                push_logic_line(proc, "while (true) {".into());
            }
            for s in &loop_stmt.node.body {
                process_statement(s, proc)?;
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::While(while_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&while_stmt.node.condition, proc);
            if let Some(label) = &while_stmt.node.label {
                push_logic_line(proc, format!("{}: while ({}) {{", label, cond));
            } else {
                push_logic_line(proc, format!("while ({}) {{", cond));
            }
            for s in &while_stmt.node.body {
                process_statement(s, proc)?;
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::For(for_stmt) => {
            let var = crate::naming::snake_to_camel(&for_stmt.node.variable);
            match &for_stmt.node.kind {
                ogsql_parser::ast::plpgsql::PlForKind::Range { low, high, step, reverse } => {
                    let lo = crate::expr::expr_to_java(low, proc);
                    let hi = crate::expr::expr_to_java(high, proc);
                    let iter_var = var.clone();
                    let already_declared = proc.local_vars.contains_key(&for_stmt.node.variable);
                    let step_code = match step {
                        Some(s) => {
                            let s_val = crate::expr::expr_to_java(s, proc);
                            format!("{} += {}", iter_var, s_val)
                        }
                        None => format!("{}++", iter_var),
                    };
                    if already_declared {
                        if *reverse {
                            push_logic_line(proc, format!("{} = {}; while ({} >= {}) {{ {}--;",
                                iter_var, hi, iter_var, lo, iter_var));
                        } else {
                            push_logic_line(proc, format!("{} = {}; while ({} <= {}) {{ {};",
                                iter_var, lo, iter_var, hi, step_code));
                        }
                    } else {
                        if *reverse {
                            push_logic_line(proc, format!("for (int {} = {}; {} >= {}; {}--) {{",
                                iter_var, hi, iter_var, lo, iter_var));
                        } else {
                            push_logic_line(proc, format!("for (int {} = {}; {} <= {}; {}) {{",
                                iter_var, lo, iter_var, hi, step_code));
                        }
                    }
                }
                ogsql_parser::ast::plpgsql::PlForKind::Query { query, .. } => {
                    push_logic_line(proc, format!("// for {} in query: {}", var, query.replace('\n', " ")));
                    proc.local_vars.remove(&for_stmt.node.variable);
                    proc.local_var_defaults.remove(&for_stmt.node.variable);
                    push_logic_line(proc, format!("for (Map<String, Object> {} : java.util.Collections.<Map<String, Object>>emptyList()) {{", var));
                }
                ogsql_parser::ast::plpgsql::PlForKind::Cursor { cursor_name, .. } => {
                    let cursor_java = crate::expr::expr_to_java(cursor_name, proc);
                    push_logic_line(proc, format!("// for {} in cursor {}", var, cursor_java));
                    proc.local_vars.remove(&for_stmt.node.variable);
                    proc.local_var_defaults.remove(&for_stmt.node.variable);
                    push_logic_line(proc, format!("for (Map<String, Object> {} : java.util.Collections.<Map<String, Object>>emptyList()) {{", var));
                }
            }
            for s in &for_stmt.node.body {
                process_statement(s, proc)?;
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::ForEach(foreach_stmt) => {
            let var = crate::naming::snake_to_camel(&foreach_stmt.node.variable);
            let expr = crate::expr::expr_to_java(&foreach_stmt.node.expression, proc);
            push_logic_line(proc, format!("for (Object {} : (Iterable<?>) ({})) {{", var, expr));
            for s in &foreach_stmt.node.body {
                process_statement(s, proc)?;
            }
            push_logic_line(proc, "}".into());
            Ok(())
        }
        PlStatement::Exit { label, condition } => {
            match (label, condition) {
                (Some(l), Some(c)) => {
                    let cond = crate::expr::bool_expr_to_java(c, proc);
                    push_logic_line(proc, format!("if ({}) {{ break {}; }}", cond, l));
                }
                (Some(l), None) => {
                    push_logic_line(proc, format!("break {};", l));
                }
                (None, Some(c)) => {
                    let cond = crate::expr::bool_expr_to_java(c, proc);
                    push_logic_line(proc, format!("if ({}) {{ break; }}", cond));
                }
                (None, None) => {
                    push_logic_line(proc, "break;".into());
                }
            }
            Ok(())
        }
        PlStatement::Continue { label, condition } => {
            match (label, condition) {
                (Some(l), Some(c)) => {
                    let cond = crate::expr::bool_expr_to_java(c, proc);
                    push_logic_line(proc, format!("if ({}) {{ continue {}; }}", cond, l));
                }
                (Some(l), None) => {
                    push_logic_line(proc, format!("continue {};", l));
                }
                (None, Some(c)) => {
                    let cond = crate::expr::bool_expr_to_java(c, proc);
                    push_logic_line(proc, format!("if ({}) {{ continue; }}", cond));
                }
                (None, None) => {
                    push_logic_line(proc, "continue;".into());
                }
            }
            Ok(())
        }
        PlStatement::Open(_) => {
            push_logic_line(proc, "// OPEN cursor;".into());
            Ok(())
        }
        PlStatement::Fetch(_) => {
            push_logic_line(proc, "// FETCH cursor;".into());
            Ok(())
        }
        PlStatement::Close { cursor } => {
            let cur = crate::expr::expr_to_java(cursor, proc);
            push_logic_line(proc, format!("// CLOSE {};", cur));
            Ok(())
        }
        PlStatement::Move { cursor, .. } => {
            let cur = crate::expr::expr_to_java(cursor, proc);
            push_logic_line(proc, format!("// MOVE {};", cur));
            Ok(())
        }
        PlStatement::GetDiagnostics(_) => {
            push_logic_line(proc, "// GET DIAGNOSTICS;".into());
            Ok(())
        }
        PlStatement::Commit { .. } => {
            push_logic_line(proc, "// COMMIT;".into());
            Ok(())
        }
        PlStatement::Rollback { to_savepoint, .. } => {
            if let Some(sp) = to_savepoint {
                push_logic_line(proc, format!("// ROLLBACK TO {};", sp));
            } else {
                proc.imports
                    .insert("import org.springframework.transaction.interceptor.TransactionAspectSupport;".into());
                push_logic_line(proc,
                    "TransactionAspectSupport.currentTransactionStatus().setRollbackOnly();".into(),
                );
            }
            Ok(())
        }
        PlStatement::Savepoint { name } => {
            push_logic_line(proc, format!("// SAVEPOINT {};", name));
            Ok(())
        }
        PlStatement::ReleaseSavepoint { name } => {
            push_logic_line(proc, format!("// RELEASE SAVEPOINT {};", name));
            Ok(())
        }
        PlStatement::SetTransaction { .. } => {
            push_logic_line(proc, "// SET TRANSACTION;".into());
            Ok(())
        }
        PlStatement::Goto { label } => {
            push_logic_line(proc, format!("// GOTO {} — will be rewritten by pattern analysis", label));
            Ok(())
        }
        PlStatement::Execute(_) => {
            push_logic_line(proc, "// EXECUTE dynamic SQL;".into());
            Ok(())
        }
        PlStatement::ProcedureCall(call) => {
            let name_parts: Vec<&str> = call.node.name.iter().map(|s| s.as_str()).collect();
            let full_name = name_parts.join(".");
            let method = crate::naming::java_method_name(name_parts.last().unwrap_or(&"unknown"));
            let args: Vec<String> = call.node.arguments.iter()
                .map(|a| crate::expr::expr_to_java(a, proc))
                .collect();
            push_logic_line(proc, format!("// TODO: call {}.{}({});", full_name, method, args.join(", ")));
            Ok(())
        }
        PlStatement::Sql(sql_text) => {
            push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
            Ok(())
        }
        PlStatement::SqlStatement { sql_text, .. } => {
            push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
            Ok(())
        }
        PlStatement::ForAll(_) => {
            push_logic_line(proc, "// FORALL;".into());
            Ok(())
        }
        PlStatement::PipeRow { expression } => {
            let val = crate::expr::expr_to_java(expression, proc);
            push_logic_line(proc, format!("// PIPE ROW: {};", val));
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_proc() -> ProcedureInfo {
        ProcedureInfo::new("pkg.test".into(), "pkg".into(), "test".into())
    }

    #[test]
    fn test_process_null() {
        let mut proc = empty_proc();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Null;
        process_statement(&stmt, &mut proc).unwrap();
        assert!(proc.java_logic_lines.is_empty());
    }

    #[test]
    fn test_process_return_no_value() {
        let mut proc = empty_proc();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Return { expression: None };
        process_statement(&stmt, &mut proc).unwrap();
        assert_eq!(proc.java_logic_lines[0], "return;");
    }

    #[test]
    fn test_process_commit() {
        let mut proc = empty_proc();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Commit { and_chain: false };
        process_statement(&stmt, &mut proc).unwrap();
        assert_eq!(proc.java_logic_lines[0], "// COMMIT;");
    }

    #[test]
    fn test_process_raise_exception() {
        use ogsql_parser::ast::Spanned;
        let mut proc = empty_proc();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Raise(Spanned::without_span(
            ogsql_parser::ast::plpgsql::PlRaiseStmt {
                level: Some(ogsql_parser::ast::plpgsql::RaiseLevel::Exception),
                message: Some("test error".into()),
                params: Vec::new(),
                options: Vec::new(),
                condname: None,
                sqlstate: None,
            },
        ));
        process_statement(&stmt, &mut proc).unwrap();
        assert!(proc.java_logic_lines[0].contains("throw new BusinessException"));
    }

    #[test]
    fn test_process_raise_notice() {
        use ogsql_parser::ast::Spanned;
        let mut proc = empty_proc();
        let stmt = ogsql_parser::ast::plpgsql::PlStatement::Raise(Spanned::without_span(
            ogsql_parser::ast::plpgsql::PlRaiseStmt {
                level: Some(ogsql_parser::ast::plpgsql::RaiseLevel::Notice),
                message: Some("info msg".into()),
                params: Vec::new(),
                options: Vec::new(),
                condname: None,
                sqlstate: None,
            },
        ));
        process_statement(&stmt, &mut proc).unwrap();
        assert!(proc.java_logic_lines[0].contains("log.info"));
    }
}

use std::collections::{HashMap, HashSet};

use ogsql_parser::ast::plpgsql::PlStatement;

use crate::types::{ConversionError, GotoAnalysis, GotoInfo, GotoPattern, ProcedureInfo};

pub fn analyze_goto_patterns(body: &[PlStatement]) -> GotoAnalysis {
    let mut labels: HashMap<String, usize> = HashMap::new();
    let mut gotos: Vec<GotoInfo> = Vec::new();

    collect_labels_and_gotos(body, 0, &mut labels, &mut gotos);

    let mut has_backward = false;
    let mut has_forward = false;
    let mut cross_block = false;

    for goto in &gotos {
        if let Some(&target) = labels.get(&goto.label) {
            if target < goto.stmt_index {
                has_backward = true;
            } else if target > goto.stmt_index {
                has_forward = true;
            }
            if goto.nesting_depth > 0 {
                cross_block = true;
            }
        }
    }

    let mut analysis = GotoAnalysis {
        pattern: None,
        labels,
        gotos,
        has_backward,
        has_forward,
        cross_block,
    };

    analysis.pattern = classify_goto_pattern(&analysis, body.len());
    analysis
}

fn collect_labels_and_gotos(
    body: &[PlStatement],
    depth: usize,
    labels: &mut HashMap<String, usize>,
    gotos: &mut Vec<GotoInfo>,
) {
    for (idx, stmt) in body.iter().enumerate() {
        match stmt {
            PlStatement::Block(block) => {
                if let Some(label) = &block.node.label {
                    labels.insert(label.clone(), idx);
                }
                collect_labels_and_gotos(&block.node.body, depth + 1, labels, gotos);
                if let Some(exc) = &block.node.exception_block {
                    for handler in &exc.handlers {
                        collect_labels_and_gotos(&handler.statements, depth + 1, labels, gotos);
                    }
                }
            }
            PlStatement::Loop(loop_stmt) => {
                if let Some(label) = &loop_stmt.node.label {
                    labels.insert(label.clone(), idx);
                }
                collect_labels_and_gotos(&loop_stmt.node.body, depth + 1, labels, gotos);
            }
            PlStatement::While(while_stmt) => {
                if let Some(label) = &while_stmt.node.label {
                    labels.insert(label.clone(), idx);
                }
                collect_labels_and_gotos(&while_stmt.node.body, depth + 1, labels, gotos);
            }
            PlStatement::For(for_stmt) => {
                if let Some(label) = &for_stmt.node.label {
                    labels.insert(label.clone(), idx);
                }
                collect_labels_and_gotos(&for_stmt.node.body, depth + 1, labels, gotos);
            }
            PlStatement::If(if_stmt) => {
                collect_labels_and_gotos(&if_stmt.node.then_stmts, depth + 1, labels, gotos);
                for elsif in &if_stmt.node.elsifs {
                    collect_labels_and_gotos(&elsif.stmts, depth + 1, labels, gotos);
                }
                collect_labels_and_gotos(&if_stmt.node.else_stmts, depth + 1, labels, gotos);
            }
            PlStatement::Goto { label } => {
                gotos.push(GotoInfo {
                    label: label.clone(),
                    stmt_index: idx,
                    nesting_depth: depth,
                });
            }
            _ => {}
        }
    }
}

fn classify_goto_pattern(
    analysis: &GotoAnalysis,
    body_len: usize,
) -> Option<GotoPattern> {
    if analysis.gotos.is_empty() {
        return None;
    }

    let label_count = analysis.labels.len();
    let goto_count = analysis.gotos.len();

    if label_count >= 3 && goto_count >= 3 {
        return Some(GotoPattern::StateMachine);
    }

    if analysis.cross_block {
        return Some(GotoPattern::DeepNestedBreak);
    }

    if analysis.has_backward {
        return Some(GotoPattern::LoopSimulation);
    }

    let last_label_index = analysis.labels.values().copied().max().unwrap_or(0);
    let all_forward = !analysis.has_backward;
    let label_at_end = body_len > 0 && last_label_index >= (body_len * 4) / 5;

    if all_forward && label_at_end && goto_count > 1 {
        return Some(GotoPattern::CleanupExit);
    }

    if all_forward && goto_count == 1 {
        return Some(GotoPattern::LogicSkip);
    }

    None
}

pub fn rewrite_with_pattern(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    let pattern = analysis.pattern.ok_or_else(|| ConversionError::Analysis {
        procedure: proc.name.clone(),
        message: "No GOTO pattern classified".into(),
    })?;

    match pattern {
        GotoPattern::CleanupExit => generate_cleanup_goto(body, analysis, proc),
        GotoPattern::LoopSimulation => generate_loop_goto(body, analysis, proc),
        GotoPattern::LogicSkip => generate_logic_skip_goto(body, analysis, proc),
        GotoPattern::DeepNestedBreak => generate_deep_nested_goto(body, analysis, proc),
        GotoPattern::StateMachine => generate_state_machine_goto(body, analysis, proc),
    }
}

fn invert_condition(cond: &str) -> String {
    let c = cond.trim();
    if c.starts_with("(!") && c.ends_with(')') {
        c[2..c.len()-1].to_string()
    } else if c.starts_with('(') && c.ends_with(')') {
        format!("!{}", c)
    } else if !c.contains(' ') {
        format!("!{}", c)
    } else {
        format!("!({})", c)
    }
}

fn generate_cleanup_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    // Find the cleanup label — it's the label closest to the end
    let cleanup_label = analysis.labels.iter()
        .max_by_key(|(_, &idx)| idx)
        .map(|(name, _)| name.clone())
        .unwrap_or_default();
    let cleanup_idx = *analysis.labels.get(&cleanup_label).unwrap_or(&0);

    proc.java_logic_lines.push("try {".to_string());

    for (idx, stmt) in body.iter().enumerate() {
        if idx >= cleanup_idx {
            break;
        }
        // Skip GOTOs that target the cleanup label
        if let PlStatement::Goto { label } = stmt {
            if label == &cleanup_label {
                continue;
            }
        }
        crate::statement::process_statement(stmt, proc)?;
    }

    proc.java_logic_lines.push("} finally {".to_string());

    for (idx, stmt) in body.iter().enumerate() {
        if idx < cleanup_idx {
            continue;
        }
        // For Block statements with the cleanup label, process the body
        if let PlStatement::Block(block) = stmt {
            if block.node.label.as_deref() == Some(&cleanup_label) {
                for s in &block.node.body {
                    crate::statement::process_statement(s, proc)?;
                }
                continue;
            }
        }
        crate::statement::process_statement(stmt, proc)?;
    }

    proc.java_logic_lines.push("}".to_string());
    Ok(())
}

fn generate_loop_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    let backward_goto = analysis.gotos.iter()
        .find(|g| {
            if let Some(&target_idx) = analysis.labels.get(&g.label) {
                target_idx < g.stmt_index
            } else {
                false
            }
        });

    let backward_goto = match backward_goto {
        Some(g) => g,
        None => return Err(ConversionError::Analysis {
            procedure: proc.name.clone(),
            message: "No backward GOTO found for loop pattern".into(),
        }),
    };

    let label_name = &backward_goto.label;
    let target_idx = *analysis.labels.get(label_name).unwrap_or(&0);
    let source_idx = backward_goto.stmt_index;

    // Process statements before the loop target
    for (idx, stmt) in body.iter().enumerate() {
        if idx >= target_idx {
            break;
        }
        crate::statement::process_statement(stmt, proc)?;
    }

    proc.java_logic_lines.push("do {".to_string());

    let mut loop_condition: Option<String> = None;
    for (idx, stmt) in body.iter().enumerate() {
        if idx < target_idx || idx > source_idx {
            continue;
        }
        // Skip the backward GOTO itself
        if let PlStatement::Goto { label } = stmt {
            if label == label_name {
                continue;
            }
        }
        // Check if this is an IF containing the backward GOTO
        if let PlStatement::If(if_stmt) = stmt {
            let has_backward = if_stmt.node.then_stmts.iter().any(|s| {
                if let PlStatement::Goto { label } = s {
                    label == label_name
                } else {
                    false
                }
            });
            if has_backward {
                let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
                loop_condition = Some(cond);
                // Process else branch of the IF (it's part of the loop body)
                for s in &if_stmt.node.else_stmts {
                    crate::statement::process_statement(s, proc)?;
                }
                for elsif in &if_stmt.node.elsifs {
                    let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                    proc.java_logic_lines.push(format!("}} else if ({}) {{", elsif_cond));
                    for s in &elsif.stmts {
                        crate::statement::process_statement(s, proc)?;
                    }
                }
                continue;
            }
        }
        crate::statement::process_statement(stmt, proc)?;
    }

    let cond = loop_condition.unwrap_or_else(|| "true".to_string());
    proc.java_logic_lines.push(format!("}} while ({});", cond));

    // Process statements after the loop
    for (idx, stmt) in body.iter().enumerate() {
        if idx <= source_idx {
            continue;
        }
        crate::statement::process_statement(stmt, proc)?;
    }

    Ok(())
}

fn generate_logic_skip_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    let goto_info = &analysis.gotos[0];
    let label_name = &goto_info.label;
    let target_idx = *analysis.labels.get(label_name).unwrap_or(&0);

    // Find the IF statement containing this GOTO
    let mut enclosing_if_idx: Option<usize> = None;
    let mut if_condition: Option<String> = None;

    for (idx, stmt) in body.iter().enumerate() {
        if let PlStatement::If(if_stmt) = stmt {
            let has_goto = if_stmt.node.then_stmts.iter().any(|s| {
                if let PlStatement::Goto { label } = s {
                    label == label_name
                } else {
                    false
                }
            });
            if has_goto {
                let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
                if_condition = Some(cond);
                enclosing_if_idx = Some(idx);
                break;
            }
        }
    }

    let enclosing_idx = enclosing_if_idx.unwrap_or(goto_info.stmt_index);

    if let Some(cond) = if_condition {
        let inverted = invert_condition(&cond);

        // Process statements before the enclosing IF
        for (idx, stmt) in body.iter().enumerate() {
            if idx >= enclosing_idx {
                break;
            }
            crate::statement::process_statement(stmt, proc)?;
        }

        proc.java_logic_lines.push(format!("if ({}) {{", inverted));

        // Process statements between the IF and the label target
        for (idx, stmt) in body.iter().enumerate() {
            if idx <= enclosing_idx {
                continue;
            }
            if idx >= target_idx {
                break;
            }
            crate::statement::process_statement(stmt, proc)?;
        }

        proc.java_logic_lines.push("}".to_string());

        // Process statements from label target onwards
        for (idx, stmt) in body.iter().enumerate() {
            if idx < target_idx {
                continue;
            }
            // For Block with the target label, process its body
            if let PlStatement::Block(block) = stmt {
                if block.node.label.as_deref() == Some(label_name.as_str()) {
                    for s in &block.node.body {
                        crate::statement::process_statement(s, proc)?;
                    }
                    continue;
                }
            }
            crate::statement::process_statement(stmt, proc)?;
        }
    } else {
        // Fallback: just process normally
        for stmt in body {
            crate::statement::process_statement(stmt, proc)?;
        }
    }

    Ok(())
}

fn generate_deep_nested_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    let goto_labels: HashSet<String> = analysis.gotos.iter()
        .map(|g| g.label.clone())
        .collect();

    proc.java_logic_lines.push("mainLoop: while (true) {".to_string());

    process_with_goto_replace(body, &goto_labels, proc)?;

    let has_terminal = proc.java_logic_lines.iter().rev()
        .find(|l| {
            let t = l.trim();
            !t.starts_with("//") && !t.is_empty() && !t.starts_with("}")
        })
        .map_or(false, |l| {
            let t = l.trim();
            t.starts_with("return ") || t == "return;" || t.starts_with("throw ")
        });
    if !has_terminal {
        proc.java_logic_lines.push("    break mainLoop;".to_string());
    }
    proc.java_logic_lines.push("}".to_string());

    Ok(())
}

fn process_with_goto_replace(
    stmts: &[PlStatement],
    goto_labels: &HashSet<String>,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    for stmt in stmts {
        match stmt {
            PlStatement::Goto { label } if goto_labels.contains(label) => {
                proc.java_logic_lines.push("continue;".to_string());
            }
            PlStatement::If(if_stmt) => {
                let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
                proc.java_logic_lines.push(format!("if ({}) {{", cond));
                process_with_goto_replace(&if_stmt.node.then_stmts, goto_labels, proc)?;
                for elsif in &if_stmt.node.elsifs {
                    let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                    proc.java_logic_lines.push(format!("}} else if ({}) {{", elsif_cond));
                    process_with_goto_replace(&elsif.stmts, goto_labels, proc)?;
                }
                if !if_stmt.node.else_stmts.is_empty() {
                    proc.java_logic_lines.push("} else {".to_string());
                    process_with_goto_replace(&if_stmt.node.else_stmts, goto_labels, proc)?;
                }
                proc.java_logic_lines.push("}".to_string());
            }
            PlStatement::Block(block) => {
                // Process block body without the label wrapper
                process_with_goto_replace(&block.node.body, goto_labels, proc)?;
            }
            PlStatement::Loop(loop_stmt) => {
                proc.java_logic_lines.push("while (true) {".to_string());
                process_with_goto_replace(&loop_stmt.node.body, goto_labels, proc)?;
                proc.java_logic_lines.push("}".to_string());
            }
            PlStatement::While(while_stmt) => {
                let cond = crate::expr::bool_expr_to_java(&while_stmt.node.condition, proc);
                proc.java_logic_lines.push(format!("while ({}) {{", cond));
                process_with_goto_replace(&while_stmt.node.body, goto_labels, proc)?;
                proc.java_logic_lines.push("}".to_string());
            }
            PlStatement::For(for_stmt) => {
                let var = crate::naming::snake_to_camel(&for_stmt.node.variable);
                match &for_stmt.node.kind {
                    ogsql_parser::ast::plpgsql::PlForKind::Range { low, high, step, reverse } => {
                        let lo = crate::expr::expr_to_java(low, proc);
                        let hi = crate::expr::expr_to_java(high, proc);
                        let already_declared = proc.local_vars.contains_key(&for_stmt.node.variable);
                        if already_declared {
                            // Variable already declared — use for loop without type
                            if *reverse {
                                proc.java_logic_lines.push(format!("for ({0} = {1}; {0} >= {2}; {0}--) {{", var, hi, lo));
                            } else {
                                let step_code = match step {
                                    Some(s) => format!("{} += {}", var, crate::expr::expr_to_java(s, proc)),
                                    None => format!("{}++", var),
                                };
                                proc.java_logic_lines.push(format!("for ({} = {}; {} <= {}; {}) {{", var, lo, var, hi, step_code));
                            }
                        } else {
                            if *reverse {
                                proc.java_logic_lines.push(format!("for (int {0} = {1}; {0} >= {2}; {0}--) {{", var, hi, lo));
                            } else {
                                let step_code = match step {
                                    Some(s) => format!("{} += {}", var, crate::expr::expr_to_java(s, proc)),
                                    None => format!("{}++", var),
                                };
                                proc.java_logic_lines.push(format!("for (int {} = {}; {} <= {}; {}) {{", var, lo, var, hi, step_code));
                            }
                        }
                    }
                    _ => {
                        // Query/Cursor FOR loops
                        proc.java_logic_lines.push(
                            format!("for (Map<String, Object> {} : java.util.Collections.<Map<String, Object>>emptyList()) {{", var)
                        );
                    }
                }
                process_with_goto_replace(&for_stmt.node.body, goto_labels, proc)?;
                proc.java_logic_lines.push("}".to_string());
            }
            _ => {
                crate::statement::process_statement(stmt, proc)?;
            }
        }
    }
    Ok(())
}

fn generate_state_machine_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
    let enum_name = format!("{}State", crate::naming::snake_to_pascal(&proc.proc_name));

    // Build ordered label list
    let mut ordered_labels: Vec<(String, usize)> = analysis.labels.iter()
        .map(|(name, &idx)| (name.clone(), idx))
        .collect();
    ordered_labels.sort_by_key(|(_, idx)| *idx);

    let state_names: Vec<String> = ordered_labels.iter()
        .map(|(name, _)| crate::naming::snake_to_pascal(name))
        .collect();

    proc.java_logic_lines.push("// State machine generated from GOTO labels".to_string());
    proc.java_logic_lines.push(format!("enum {} {{ {} }}", enum_name, state_names.join(", ")));
    proc.java_logic_lines.push(format!("{} currentState = {}.{};", enum_name, enum_name, state_names[0]));
    proc.java_logic_lines.push("boolean running = true;".to_string());
    proc.java_logic_lines.push("while (running) {".to_string());
    proc.java_logic_lines.push("    switch (currentState) {".to_string());

    for (label_name, target_idx) in &ordered_labels {
        let state_java = crate::naming::snake_to_pascal(label_name);
        proc.java_logic_lines.push(format!("        case {}:", state_java));

        let target = *target_idx;
        let end_idx = ordered_labels.iter()
            .filter(|(_, idx)| *idx > target)
            .min_by_key(|(_, idx)| *idx)
            .map(|(_, idx)| *idx)
            .unwrap_or(body.len());

        for idx in target..end_idx {
            let stmt = &body[idx];
            match stmt {
                PlStatement::Goto { label } => {
                    if analysis.labels.contains_key(label) {
                        let goto_state = crate::naming::snake_to_pascal(label);
                        proc.java_logic_lines.push(format!("            currentState = {}.{};", enum_name, goto_state));
                    } else {
                        proc.java_logic_lines.push("            running = false;".to_string());
                    }
                }
                PlStatement::Block(block) => {
                    if block.node.label.as_deref() == Some(label_name.as_str()) {
                        for s in &block.node.body {
                            crate::statement::process_statement(s, proc)?;
                        }
                        continue;
                    }
                    crate::statement::process_statement(stmt, proc)?;
                }
                _ => {
                    crate::statement::process_statement(stmt, proc)?;
                }
            }
        }
        proc.java_logic_lines.push("            break;".to_string());
    }

    proc.java_logic_lines.push("        default:".to_string());
    proc.java_logic_lines.push("            running = false;".to_string());
    proc.java_logic_lines.push("            break;".to_string());
    proc.java_logic_lines.push("    }".to_string());
    proc.java_logic_lines.push("}".to_string());

    Ok(())
}

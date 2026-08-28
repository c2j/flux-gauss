use std::collections::{HashMap, HashSet};

use ogsql_parser::ast::plpgsql::PlStatement;

use crate::types::{ConversionError, GotoAnalysis, GotoInfo, GotoPattern, ProcedureInfo};

pub fn analyze_goto_patterns(
    body: &[PlStatement],
    proc: &ProcedureInfo,
    source_cache: &mut std::collections::HashMap<String, Vec<String>>,
) -> GotoAnalysis {
    let mut labels: HashMap<String, usize> = HashMap::new();
    let mut gotos: Vec<GotoInfo> = Vec::new();

    collect_labels_and_gotos(body, 0, false, 0, &mut labels, &mut gotos);

    let (text_labels, goto_lines, label_lines) = scan_source_text_for_labels(proc, body, source_cache);

    // Merge text-discovered labels into the AST-discovered ones.
    // Text labels take precedence when they don't exist in AST.
    for (label_name, target_idx) in &text_labels {
        if !labels.contains_key(label_name) {
            labels.insert(label_name.clone(), *target_idx);
        }
    }

    let mut has_backward = false;
    let mut has_forward = false;
    let mut cross_block = false;

    for goto in &mut gotos {
        // Determine forward/backward using source line numbers when available
        if let Some(goto_line_list) = goto_lines.get(&goto.label) {
            if let Some(&label_line) = label_lines.get(&goto.label) {
                goto.is_forward = goto_line_list.iter().any(|&l| l < label_line);
                goto.is_backward = goto_line_list.iter().any(|&l| l > label_line);
            }
        }

        if let Some(&target) = labels.get(&goto.label) {
            // Fallback: use AST indices if line numbers weren't available
            if !goto.is_forward && !goto.is_backward {
                if target < goto.stmt_index {
                    goto.is_backward = true;
                } else if target > goto.stmt_index {
                    goto.is_forward = true;
                }
            }
            if target < goto.stmt_index {
                has_backward = true;
            } else if target > goto.stmt_index {
                has_forward = true;
            }
            if goto.inside_loop {
                cross_block = true;
            }
        }
    }

    let mut analysis = GotoAnalysis { pattern: None, labels, gotos, has_backward, has_forward, cross_block };

    analysis.pattern = classify_goto_pattern(&analysis, body.len());

    analysis
}

/// Scan the raw SQL source text for <<label>> and GOTO label patterns.
/// Returns:
///   1. HashMap of label_name -> estimated statement index
///   2. HashMap of label_name -> list of GOTO source line numbers
///   3. HashMap of label_name -> label declaration line number
fn scan_source_text_for_labels(
    proc: &ProcedureInfo,
    body: &[PlStatement],
    source_cache: &mut HashMap<String, Vec<String>>,
) -> (HashMap<String, usize>, HashMap<String, Vec<usize>>, HashMap<String, usize>) {
    let mut text_labels: HashMap<String, usize> = HashMap::new();
    let mut goto_lines: HashMap<String, Vec<usize>> = HashMap::new();
    let mut label_lines: HashMap<String, usize> = HashMap::new();

    if proc.source_file.is_empty() || proc.source_start_line == 0 || proc.source_end_line == 0 {
        return (text_labels, goto_lines, label_lines);
    }

    let all_lines = source_cache.entry(proc.source_file.clone()).or_insert_with(|| {
        std::fs::read_to_string(&proc.source_file).map(|c| c.lines().map(String::from).collect()).unwrap_or_default()
    });
    if all_lines.is_empty() {
        return (text_labels, goto_lines, label_lines);
    }
    let start = (proc.source_start_line as usize).saturating_sub(1);
    let end = (proc.source_end_line as usize).min(all_lines.len());

    if start >= end || start >= all_lines.len() {
        return (text_labels, goto_lines, label_lines);
    }

    // Scan for <<label>> patterns — use index-based access on owned Vec<String>
    for line_idx in start..end {
        let line = &all_lines[line_idx];
        let line_num = line_idx + 1; // 1-based line number
                                     // Find all <<label>> on this line
        let mut chars = line.char_indices().peekable();
        while let Some((i, c)) = chars.next() {
            if c == '<' {
                if let Some(&(_, next_c)) = chars.peek() {
                    if next_c == '<' {
                        chars.next(); // consume second '<'
                        let label_start = i + 2;
                        let mut label_end = label_start;
                        let mut found_end = false;
                        while let Some((j, ch)) = chars.next() {
                            if ch == '>' {
                                if let Some(&(_, next_ch)) = chars.peek() {
                                    if next_ch == '>' {
                                        chars.next(); // consume second '>'
                                        label_end = j;
                                        found_end = true;
                                        break;
                                    }
                                }
                            }
                            label_end = j + ch.len_utf8();
                        }
                        if found_end && label_end > label_start {
                            let label_name = line[label_start..label_end].trim().to_string();
                            if !label_name.is_empty() {
                                label_lines.insert(label_name.clone(), line_num);
                            }
                        }
                    }
                }
            }
        }

        // Scan for GOTO label (case-insensitive)
        let upper = line.to_uppercase();
        for (goto_pos, _) in upper.match_indices("GOTO") {
            let after_goto = &line[goto_pos + 4..];
            let trimmed = after_goto.trim();
            // Extract the label name after GOTO
            if let Some(first_word) = trimmed.split_whitespace().next() {
                let label = first_word.trim_end_matches(';').to_string();
                // Skip false positives like "GOTO" inside procedure names
                if !label.is_empty() && !label.contains('(') && !label.contains(')') {
                    goto_lines.entry(label).or_default().push(line_num);
                }
            }
        }
    }

    // Map text-discovered labels to approximate statement indices.
    // Use the AST body length if available; otherwise fall back to line count.
    let text_body_len = end - start;
    let ast_body_len = body.len();
    let effective_body_len = if ast_body_len > 0 { ast_body_len } else { text_body_len };
    for (label_name, label_line) in &label_lines {
        if !goto_lines.contains_key(label_name) {
            continue;
        }
        let offset_from_start = label_line.saturating_sub(proc.source_start_line as usize);
        let estimated_idx = if effective_body_len > 0 && text_body_len > 0 {
            let ratio = offset_from_start as f64 / text_body_len as f64;
            ((ratio * effective_body_len as f64) as usize).min(effective_body_len - 1)
        } else {
            0
        };
        text_labels.insert(label_name.clone(), estimated_idx);
    }

    (text_labels, goto_lines, label_lines)
}

fn collect_labels_and_gotos(
    body: &[PlStatement],
    depth: usize,
    inside_loop: bool,
    top_level_idx: usize,
    labels: &mut HashMap<String, usize>,
    gotos: &mut Vec<GotoInfo>,
) {
    for (idx, stmt) in body.iter().enumerate() {
        let current_top = if depth == 0 { idx } else { top_level_idx };
        match stmt {
            PlStatement::Block(block) => {
                if let Some(label) = &block.node.label {
                    labels.insert(label.clone(), current_top);
                }
                collect_labels_and_gotos(&block.node.body, depth + 1, inside_loop, current_top, labels, gotos);
                if let Some(exc) = &block.node.exception_block {
                    for handler in &exc.handlers {
                        collect_labels_and_gotos(
                            &handler.statements,
                            depth + 1,
                            inside_loop,
                            current_top,
                            labels,
                            gotos,
                        );
                    }
                }
            }
            PlStatement::Loop(loop_stmt) => {
                if let Some(label) = &loop_stmt.node.label {
                    labels.insert(label.clone(), current_top);
                }
                collect_labels_and_gotos(&loop_stmt.node.body, depth + 1, true, current_top, labels, gotos);
            }
            PlStatement::While(while_stmt) => {
                if let Some(label) = &while_stmt.node.label {
                    labels.insert(label.clone(), current_top);
                }
                collect_labels_and_gotos(&while_stmt.node.body, depth + 1, true, current_top, labels, gotos);
            }
            PlStatement::For(for_stmt) => {
                if let Some(label) = &for_stmt.node.label {
                    labels.insert(label.clone(), current_top);
                }
                collect_labels_and_gotos(&for_stmt.node.body, depth + 1, true, current_top, labels, gotos);
            }
            PlStatement::If(if_stmt) => {
                collect_labels_and_gotos(&if_stmt.node.then_stmts, depth + 1, inside_loop, current_top, labels, gotos);
                for elsif in &if_stmt.node.elsifs {
                    collect_labels_and_gotos(&elsif.stmts, depth + 1, inside_loop, current_top, labels, gotos);
                }
                collect_labels_and_gotos(&if_stmt.node.else_stmts, depth + 1, inside_loop, current_top, labels, gotos);
            }
            PlStatement::Goto { label } => {
                gotos.push(GotoInfo {
                    label: label.clone(),
                    stmt_index: current_top,
                    nesting_depth: depth,
                    inside_loop,
                    is_forward: false,
                    is_backward: false,
                });
            }
            _ => {}
        }
    }
}

fn classify_goto_pattern(analysis: &GotoAnalysis, body_len: usize) -> Option<GotoPattern> {
    if analysis.gotos.is_empty() {
        return None;
    }

    let label_count = analysis.labels.len();
    let goto_count = analysis.gotos.len();

    // All GOTO targets must have known labels; otherwise pattern is unknown.
    let goto_labels: HashSet<String> = analysis.gotos.iter().map(|g| g.label.clone()).collect();
    let known_labels: HashSet<String> = analysis.labels.keys().cloned().collect();
    if !goto_labels.is_subset(&known_labels) {
        return None;
    }

    // E. StateMachine: many labels and gotos forming a net
    if label_count >= 3 && goto_count >= 3 {
        return Some(GotoPattern::StateMachine);
    }

    let all_forward = analysis.gotos.iter().all(|g| g.is_forward);
    let all_backward = analysis.gotos.iter().all(|g| g.is_backward);

    // D. DeepNestedBreak: GOTOs inside loops crossing block boundaries
    if analysis.cross_block {
        return Some(GotoPattern::DeepNestedBreak);
    }

    // B. LoopSimulation: backward GOTO
    if analysis.has_backward || all_backward {
        return Some(GotoPattern::LoopSimulation);
    }

    // A. CleanupExit: all forward GOTOs to a single label near the end
    if all_forward && label_count == 1 && goto_count >= 1 {
        let last_label_index = analysis.labels.values().copied().max().unwrap_or(0);
        let label_at_top_level = last_label_index < body_len;
        let label_at_end = body_len > 0 && last_label_index >= (body_len * 4) / 5;
        if label_at_end && label_at_top_level {
            return Some(GotoPattern::CleanupExit);
        }
    }

    // C. LogicSkip: single forward GOTO (fallback)
    if all_forward && goto_count == 1 {
        return Some(GotoPattern::LogicSkip);
    }

    None
}

pub fn rewrite_with_pattern(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    summaries: &std::collections::HashMap<String, crate::types::PackageSummary>,
) -> Result<(), ConversionError> {
    let mut stmt_ctx = crate::context::StatementContext::new(summaries);
    rewrite_with_pattern_ctx(body, analysis, proc, &mut stmt_ctx)
}

fn rewrite_with_pattern_ctx(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    let pattern = analysis.pattern.ok_or_else(|| ConversionError::Analysis {
        procedure: proc.name.clone(),
        message: "No GOTO pattern classified".into(),
    })?;

    match pattern {
        GotoPattern::CleanupExit => generate_cleanup_goto(body, analysis, proc, stmt_ctx),
        GotoPattern::LoopSimulation => generate_loop_goto(body, analysis, proc, stmt_ctx),
        GotoPattern::LogicSkip => generate_logic_skip_goto(body, analysis, proc, stmt_ctx),
        GotoPattern::DeepNestedBreak => generate_deep_nested_goto(body, analysis, proc, stmt_ctx),
        GotoPattern::StateMachine => generate_state_machine_goto(body, analysis, proc, stmt_ctx),
    }
}

fn invert_condition(cond: &str) -> String {
    let c = cond.trim();
    if c.starts_with("(!") && c.ends_with(')') {
        c[2..c.len() - 1].to_string()
    } else if c.starts_with('(') && c.ends_with(')') {
        format!("!{}", c)
    } else if !c.contains(' ') {
        format!("!{}", c)
    } else {
        format!("!({})", c)
    }
}

// ── Pattern A: CleanupExit (try-finally) ──

fn generate_cleanup_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    // Find the cleanup label — it's the label closest to the end
    let cleanup_label =
        analysis.labels.iter().max_by_key(|(_, &idx)| idx).map(|(name, _)| name.clone()).unwrap_or_default();
    let cleanup_idx = *analysis.labels.get(&cleanup_label).unwrap_or(&0);

    proc.java_logic_lines.push("try {".to_string());

    for (idx, stmt) in body.iter().enumerate() {
        if idx >= cleanup_idx {
            break;
        }
        process_cleanup_stmt(stmt, &cleanup_label, proc, stmt_ctx)?;
    }

    proc.java_logic_lines.push("} finally {".to_string());

    for (idx, stmt) in body.iter().enumerate() {
        if idx < cleanup_idx {
            continue;
        }
        if let PlStatement::Block(block) = stmt {
            for decl in &block.node.declarations {
                crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new(), None);
            }
            if block.node.label.as_deref() == Some(&cleanup_label) {
                for s in &block.node.body {
                    crate::statement::process_statement(s, proc, stmt_ctx)?;
                }
                continue;
            }
        }
        crate::statement::process_statement(stmt, proc, stmt_ctx)?;
    }

    proc.java_logic_lines.push("}".to_string());
    Ok(())
}

fn process_cleanup_stmt(
    stmt: &PlStatement,
    cleanup_label: &str,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    match stmt {
        PlStatement::Goto { label } if label == cleanup_label => {
            proc.java_logic_lines.push("return;".to_string());
            Ok(())
        }
        PlStatement::If(if_stmt) => {
            let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
            proc.java_logic_lines.push(format!("if ({}) {{", cond));
            for s in &if_stmt.node.then_stmts {
                process_cleanup_stmt(s, cleanup_label, proc, stmt_ctx)?;
            }
            for elsif in &if_stmt.node.elsifs {
                let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                proc.java_logic_lines.push(format!("}} else if ({}) {{", elsif_cond));
                for s in &elsif.stmts {
                    process_cleanup_stmt(s, cleanup_label, proc, stmt_ctx)?;
                }
            }
            if !if_stmt.node.else_stmts.is_empty() {
                proc.java_logic_lines.push("} else {".to_string());
                for s in &if_stmt.node.else_stmts {
                    process_cleanup_stmt(s, cleanup_label, proc, stmt_ctx)?;
                }
            }
            proc.java_logic_lines.push("}".to_string());
            Ok(())
        }
        PlStatement::Block(block) => {
            for decl in &block.node.declarations {
                crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new(), None);
            }
            let has_exceptions = block.node.exception_block.is_some();
            if has_exceptions {
                proc.java_logic_lines.push("try {".to_string());
            }
            for s in &block.node.body {
                process_cleanup_stmt(s, cleanup_label, proc, stmt_ctx)?;
            }
            if let Some(exc_block) = &block.node.exception_block {
                for handler in &exc_block.handlers {
                    let is_others = handler.conditions.is_empty()
                        || handler.conditions.iter().any(|c| c.eq_ignore_ascii_case("others"));
                    let evar = format!("__e{}", {
                        let n = proc.catch_counter;
                        proc.catch_counter += 1;
                        n + 1
                    });
                    if is_others {
                        proc.java_logic_lines.push(format!("}} catch (Exception {evar}) {{"));
                    } else {
                        let cond = handler.conditions.join(", ");
                        proc.java_logic_lines.push(format!("}} catch (BusinessException {evar}) {{ // {}", cond));
                    }
                    proc.java_logic_lines.push(format!("    __SQLERRM__ = {evar}.getMessage();"));
                    proc.java_logic_lines.push("    __SQLCODE__ = -1;".to_string());
                    for s in &handler.statements {
                        process_cleanup_stmt(s, cleanup_label, proc, stmt_ctx)?;
                    }
                    if crate::statement::is_unreachable_after_terminal(&proc.java_logic_lines) {
                        break;
                    }
                }
            }
            if has_exceptions {
                proc.java_logic_lines.push("}".to_string());
            }
            Ok(())
        }
        _ => crate::statement::process_statement(stmt, proc, stmt_ctx),
    }
}

// ── Pattern B: LoopSimulation (do-while) ──

fn generate_loop_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    let backward_goto = analysis.gotos.iter().find(|g| {
        if let Some(&target_idx) = analysis.labels.get(&g.label) {
            target_idx < g.stmt_index
        } else {
            false
        }
    });

    let backward_goto = match backward_goto {
        Some(g) => g,
        None => {
            return Err(ConversionError::Analysis {
                procedure: proc.name.clone(),
                message: "No backward GOTO found for loop pattern".into(),
            })
        }
    };

    let label_name = &backward_goto.label;
    let target_idx = *analysis.labels.get(label_name).unwrap_or(&0);
    let source_idx = backward_goto.stmt_index;

    for (idx, stmt) in body.iter().enumerate() {
        if idx >= target_idx {
            break;
        }
        crate::statement::process_statement(stmt, proc, stmt_ctx)?;
    }

    proc.java_logic_lines.push("do {".to_string());

    let mut loop_condition: Option<String> = None;
    for (idx, stmt) in body.iter().enumerate() {
        if idx < target_idx || idx > source_idx {
            continue;
        }
        if let PlStatement::Goto { label } = stmt {
            if label == label_name {
                continue;
            }
        }
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
                for s in &if_stmt.node.else_stmts {
                    crate::statement::process_statement(s, proc, stmt_ctx)?;
                }
                for elsif in &if_stmt.node.elsifs {
                    let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                    proc.java_logic_lines.push(format!("}} else if ({}) {{", elsif_cond));
                    for s in &elsif.stmts {
                        crate::statement::process_statement(s, proc, stmt_ctx)?;
                    }
                }
                continue;
            }
        }
        crate::statement::process_statement(stmt, proc, stmt_ctx)?;
    }

    let cond = loop_condition.unwrap_or_else(|| "true".to_string());
    proc.java_logic_lines.push(format!("}} while ({});", cond));

    for (idx, stmt) in body.iter().enumerate() {
        if idx <= source_idx {
            continue;
        }
        crate::statement::process_statement(stmt, proc, stmt_ctx)?;
    }

    Ok(())
}

// ── Pattern C: LogicSkip (inverted if) ──

fn generate_logic_skip_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
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
            crate::statement::process_statement(stmt, proc, stmt_ctx)?;
        }

        proc.java_logic_lines.push(format!("if ({}) {{", inverted));

        for (idx, stmt) in body.iter().enumerate() {
            if idx <= enclosing_idx {
                continue;
            }
            if idx >= target_idx {
                break;
            }
            crate::statement::process_statement(stmt, proc, stmt_ctx)?;
        }

        proc.java_logic_lines.push("}".to_string());

        for (idx, stmt) in body.iter().enumerate() {
            if idx < target_idx {
                continue;
            }
            if let PlStatement::Block(block) = stmt {
                for decl in &block.node.declarations {
                    crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new(), None);
                }
                if block.node.label.as_deref() == Some(label_name.as_str()) {
                    for s in &block.node.body {
                        crate::statement::process_statement(s, proc, stmt_ctx)?;
                    }
                    continue;
                }
            }
            crate::statement::process_statement(stmt, proc, stmt_ctx)?;
        }
    } else {
        for stmt in body {
            crate::statement::process_statement(stmt, proc, stmt_ctx)?;
        }
    }

    Ok(())
}

// ── Pattern D: DeepNestedBreak (mainLoop + continue/break) ──

fn generate_deep_nested_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    let goto_labels: HashSet<String> = analysis.gotos.iter().map(|g| g.label.clone()).collect();

    proc.java_logic_lines.push("mainLoop: while (true) {".to_string());

    process_with_goto_replace(body, &goto_labels, proc, stmt_ctx)?;

    let has_terminal = proc
        .java_logic_lines
        .iter()
        .rev()
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
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    for stmt in stmts {
        match stmt {
            PlStatement::Goto { label } if goto_labels.contains(label) => {
                proc.java_logic_lines.push("continue;".to_string());
            }
            PlStatement::If(if_stmt) => {
                let cond = crate::expr::bool_expr_to_java(&if_stmt.node.condition, proc);
                proc.java_logic_lines.push(format!("if ({}) {{", cond));
                process_with_goto_replace(&if_stmt.node.then_stmts, goto_labels, proc, stmt_ctx)?;
                for elsif in &if_stmt.node.elsifs {
                    let elsif_cond = crate::expr::bool_expr_to_java(&elsif.condition, proc);
                    proc.java_logic_lines.push(format!("}} else if ({}) {{", elsif_cond));
                    process_with_goto_replace(&elsif.stmts, goto_labels, proc, stmt_ctx)?;
                }
                if !if_stmt.node.else_stmts.is_empty() {
                    proc.java_logic_lines.push("} else {".to_string());
                    process_with_goto_replace(&if_stmt.node.else_stmts, goto_labels, proc, stmt_ctx)?;
                }
                proc.java_logic_lines.push("}".to_string());
            }
            PlStatement::Block(block) => {
                for decl in &block.node.declarations {
                    crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new(), None);
                }
                let has_exceptions = block.node.exception_block.is_some();
                if has_exceptions {
                    proc.java_logic_lines.push("try {".to_string());
                }
                process_with_goto_replace(&block.node.body, goto_labels, proc, stmt_ctx)?;
                if let Some(exc_block) = &block.node.exception_block {
                    for handler in &exc_block.handlers {
                        let is_others = handler.conditions.is_empty()
                            || handler.conditions.iter().any(|c| c.eq_ignore_ascii_case("others"));
                        let evar = format!("__e{}", {
                            let n = proc.catch_counter;
                            proc.catch_counter += 1;
                            n + 1
                        });
                        if is_others {
                            proc.java_logic_lines.push(format!("}} catch (Exception {evar}) {{"));
                        } else {
                            let cond = handler.conditions.join(", ");
                            proc.java_logic_lines.push(format!("}} catch (BusinessException {evar}) {{ // {}", cond));
                        }
                        proc.java_logic_lines.push(format!("    __SQLERRM__ = {evar}.getMessage();"));
                        proc.java_logic_lines.push("    __SQLCODE__ = -1;".to_string());
                        process_with_goto_replace(&handler.statements, goto_labels, proc, stmt_ctx)?;
                    }
                }
                if has_exceptions {
                    proc.java_logic_lines.push("}".to_string());
                }
            }
            PlStatement::Loop(loop_stmt) => {
                proc.java_logic_lines.push("while (true) {".to_string());
                process_with_goto_replace(&loop_stmt.node.body, goto_labels, proc, stmt_ctx)?;
                proc.java_logic_lines.push("}".to_string());
            }
            PlStatement::While(while_stmt) => {
                let cond = crate::expr::bool_expr_to_java(&while_stmt.node.condition, proc);
                proc.java_logic_lines.push(format!("while ({}) {{", cond));
                process_with_goto_replace(&while_stmt.node.body, goto_labels, proc, stmt_ctx)?;
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
                            if *reverse {
                                proc.java_logic_lines
                                    .push(format!("for ({0} = {1}; {0} >= {2}; {0}--) {{", var, hi, lo));
                            } else {
                                let step_code = match step {
                                    Some(s) => format!("{} += {}", var, crate::expr::expr_to_java(s, proc)),
                                    None => format!("{}++", var),
                                };
                                proc.java_logic_lines
                                    .push(format!("for ({} = {}; {} <= {}; {}) {{", var, lo, var, hi, step_code));
                            }
                        } else {
                            if *reverse {
                                proc.java_logic_lines
                                    .push(format!("for (int {0} = {1}; {0} >= {2}; {0}--) {{", var, hi, lo));
                            } else {
                                let step_code = match step {
                                    Some(s) => format!("{} += {}", var, crate::expr::expr_to_java(s, proc)),
                                    None => format!("{}++", var),
                                };
                                proc.java_logic_lines
                                    .push(format!("for (int {} = {}; {} <= {}; {}) {{", var, lo, var, hi, step_code));
                            }
                        }
                    }
                    _ => {
                        let already_declared = proc.local_vars.contains_key(&for_stmt.node.variable);
                        if already_declared {
                            let iter_var = format!("_{}", var);
                            proc.java_logic_lines.push(
                            format!("for (Map<String, Object> {} : java.util.Collections.<Map<String, Object>>emptyList()) {{", iter_var));
                            proc.java_logic_lines.push(format!("{} = {};", var, iter_var));
                        } else {
                            proc.java_logic_lines.push(
                            format!("for (Map<String, Object> {} : java.util.Collections.<Map<String, Object>>emptyList()) {{", var));
                        }
                    }
                }
                process_with_goto_replace(&for_stmt.node.body, goto_labels, proc, stmt_ctx)?;
                proc.java_logic_lines.push("}".to_string());
            }
            _ => {
                crate::statement::process_statement(stmt, proc, stmt_ctx)?;
            }
        }
    }
    Ok(())
}

// ── Pattern E: StateMachine (while-switch) ──

fn generate_state_machine_goto(
    body: &[PlStatement],
    analysis: &GotoAnalysis,
    proc: &mut ProcedureInfo,
    stmt_ctx: &mut crate::context::StatementContext,
) -> Result<(), ConversionError> {
    let enum_name = format!("{}State", crate::naming::snake_to_pascal(&proc.proc_name));

    // Set state machine mode so nested Gotos are converted to transitions
    let all_labels: HashSet<String> = analysis.labels.keys().cloned().collect();
    stmt_ctx.sm_enum_name = Some(enum_name.clone());
    stmt_ctx.sm_labels = all_labels;

    // Build ordered label list
    let mut ordered_labels: Vec<(String, usize)> =
        analysis.labels.iter().map(|(name, &idx)| (name.clone(), idx)).collect();
    ordered_labels.sort_by_key(|(_, idx)| *idx);

    let state_names: Vec<String> =
        ordered_labels.iter().map(|(name, _)| crate::naming::snake_to_pascal(name)).collect();

    proc.java_logic_lines.push("// State machine generated from GOTO labels".to_string());
    proc.java_logic_lines.push(format!("enum {} {{ {} }}", enum_name, state_names.join(", ")));
    proc.java_logic_lines.push(format!("{} currentState = {}.{};", enum_name, enum_name, state_names[0]));
    proc.java_logic_lines.push("boolean running = true;".to_string());
    proc.java_logic_lines.push("int _smGuard = 0;".to_string());
    proc.java_logic_lines.push("while (running && _smGuard++ < 10000) {".to_string());

    let first_label_idx = ordered_labels.first().map(|(_, idx)| *idx).unwrap_or(0);
    for idx in 0..first_label_idx {
        let stmt = &body[idx];
        crate::statement::process_statement(stmt, proc, stmt_ctx)?;
        if let Some(last) = proc.java_logic_lines.last() {
            let t = last.trim();
            if t == "break;" || t.starts_with("return ") || t == "return;" {
                break;
            }
        }
    }

    proc.java_logic_lines.push("    switch (currentState) {".to_string());

    for (label_name, target_idx) in &ordered_labels {
        let state_java = crate::naming::snake_to_pascal(label_name);
        proc.java_logic_lines.push(format!("        case {}:", state_java));

        let target = *target_idx;
        let end_idx = ordered_labels
            .iter()
            .filter(|(_, idx)| *idx > target)
            .min_by_key(|(_, idx)| *idx)
            .map(|(_, idx)| *idx)
            .unwrap_or(body.len());

        for idx in target..end_idx {
            let stmt = &body[idx];
            let mut hit_goto = false;
            match stmt {
                PlStatement::Goto { label } => {
                    if analysis.labels.contains_key(label) {
                        let goto_state = crate::naming::snake_to_pascal(label);
                        proc.java_logic_lines.push(format!("            currentState = {}.{};", enum_name, goto_state));
                    } else {
                        proc.java_logic_lines.push("            running = false;".to_string());
                    }
                    proc.java_logic_lines.push("            break;".to_string());
                    hit_goto = true;
                }
                PlStatement::Block(block) => {
                    for decl in &block.node.declarations {
                        crate::analyze::process_declaration(decl, proc, &std::collections::HashMap::new(), None);
                    }
                    if block.node.label.as_deref() == Some(label_name.as_str()) {
                        for s in &block.node.body {
                            crate::statement::process_statement(s, proc, stmt_ctx)?;
                            if let Some(last) = proc.java_logic_lines.last() {
                                let t = last.trim();
                                if t == "break;" || t.starts_with("return ") || t == "return;" || t == "continue;" {
                                    break;
                                }
                            }
                        }
                        if proc.java_logic_lines.last().map_or(false, |l| {
                            let t = l.trim();
                            t == "break;" || t.starts_with("return ") || t == "return;" || t == "continue;"
                        }) {
                            break;
                        }
                        continue;
                    }
                    crate::statement::process_statement(stmt, proc, stmt_ctx)?;
                }
                _ => {
                    crate::statement::process_statement(stmt, proc, stmt_ctx)?;
                }
            }
            if hit_goto {
                break;
            }
            if let Some(last) = proc.java_logic_lines.last() {
                let t = last.trim();
                if t == "break;" || t.starts_with("return ") || t == "return;" {
                    break;
                }
            }
        }
        let last_meaningful = proc.java_logic_lines.iter().rev().find(|l| {
            let t = l.trim();
            !t.is_empty() && t != "}" && !t.starts_with("//")
        });
        let needs_break = !last_meaningful.map_or(true, |l| {
            let t = l.trim();
            t == "break;"
                || t == "continue;"
                || t.starts_with("return ")
                || t == "return;"
                || t.starts_with("throw ")
                || t == "running = false;"
        });
        if needs_break {
            proc.java_logic_lines.push("            break;".to_string());
        }
    }

    proc.java_logic_lines.push("        default:".to_string());
    proc.java_logic_lines.push("            running = false;".to_string());
    proc.java_logic_lines.push("            break;".to_string());
    proc.java_logic_lines.push("    }".to_string());
    proc.java_logic_lines.push("}".to_string());

    // Clear state machine mode
    stmt_ctx.sm_enum_name = None;
    stmt_ctx.sm_labels.clear();

    Ok(())
}

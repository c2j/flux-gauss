use std::path::Path;

use crate::context::AnalysisContext;
use crate::types::ProcedureInfo;

pub fn get_sql_file_lines(source_path: &str, ctx: &mut AnalysisContext) -> Vec<String> {
    if let Some(lines) = ctx.source_cache.get(source_path) {
        return lines.clone();
    }
    let lines = std::fs::read_to_string(source_path)
        .map(|content| content.lines().map(|s| s.to_string()).collect::<Vec<_>>())
        .unwrap_or_default();
    ctx.source_cache.insert(source_path.to_string(), lines.clone());
    lines
}

pub fn find_body_stmt_lines(proc: &ProcedureInfo, ctx: &mut AnalysisContext) -> Vec<u32> {
    let source_path = if !proc.source_path.is_empty() {
        proc.source_path.clone()
    } else if !proc.source_file.is_empty() {
        proc.source_file.clone()
    } else {
        return Vec::new();
    };
    if !Path::new(&source_path).exists() {
        return Vec::new();
    }
    let lines = get_sql_file_lines(&source_path, ctx);
    if lines.is_empty() {
        return Vec::new();
    }
    let start = (proc.source_start_line.saturating_sub(1)) as usize;
    let end = (proc.source_end_line as usize).min(lines.len());

    let mut body_start: Option<usize> = None;
    for i in start..end {
        let trimmed = lines[i].trim();
        if trimmed.eq_ignore_ascii_case("begin") || trimmed.to_uppercase() == "BEGIN" {
            body_start = Some(i);
            break;
        }
    }
    let body_start = match body_start {
        Some(bs) => bs + 1,
        None => return Vec::new(),
    };

    let mut body_end = end;
    for i in (body_start + 1)..end {
        let trimmed = lines[i].trim();
        let up = trimmed.to_uppercase();
        if up == "END" || up == "END;" || up.starts_with("END;") || up.starts_with("END ") {
            body_end = i;
            break;
        }
    }

    let mut stmt_lines: Vec<u32> = Vec::new();
    let mut blk_depth: i32 = 0;
    for i in body_start..body_end {
        let up = lines[i].trim().to_uppercase();
        let has_loop = up.contains("LOOP") && (up.contains("FOR ") || up.contains("WHILE ") || up == "LOOP");
        let is_if = up.contains("IF ") && up.contains("THEN");
        let is_case = up.starts_with("CASE ") || up == "CASE" || up.starts_with("CASE;");
        if has_loop || is_if || is_case {
            if blk_depth == 0 {
                stmt_lines.push((i + 1) as u32);
            }
            blk_depth += 1;
            continue;
        }
        if blk_depth > 0 {
            let up2 = up.as_str();
            if up2 == "END IF"
                || up2 == "END IF;"
                || up2.starts_with("END IF;")
                || up2.starts_with("END IF ")
                || up2 == "END LOOP"
                || up2 == "END LOOP;"
                || up2.starts_with("END LOOP;")
                || up2.starts_with("END LOOP ")
                || up2 == "END CASE"
                || up2 == "END CASE;"
            {
                blk_depth -= 1;
            }
            continue;
        }
        let mut in_str = false;
        let mut in_bc = false;
        let line_bytes = lines[i].as_bytes();
        let mut found = false;
        let mut j = 0;
        while j < line_bytes.len() {
            let ch = line_bytes[j];
            if in_bc {
                if ch == b'*' && j + 1 < line_bytes.len() && line_bytes[j + 1] == b'/' {
                    in_bc = false;
                    j += 1;
                }
            } else if in_str {
                if ch == b'\'' {
                    in_str = false;
                }
            } else if ch == b'\'' {
                in_str = true;
            } else if ch == b'-' && j + 1 < line_bytes.len() && line_bytes[j + 1] == b'-' {
                break;
            } else if ch == b'/' && j + 1 < line_bytes.len() && line_bytes[j + 1] == b'*' {
                in_bc = true;
                j += 1;
            } else if ch == b';' {
                found = true;
            }
            j += 1;
        }
        if found {
            stmt_lines.push((i + 1) as u32);
        }
    }
    stmt_lines
}
pub fn find_var_decl_line(proc: &ProcedureInfo, var_name: &str, ctx: &mut AnalysisContext) -> Option<u32> {
    let source_path = if !proc.source_path.is_empty() {
        proc.source_path.clone()
    } else if !proc.source_file.is_empty() {
        proc.source_file.clone()
    } else {
        return None;
    };
    if !Path::new(&source_path).exists() {
        return None;
    }
    let lines = get_sql_file_lines(&source_path, ctx);
    let start = (proc.source_start_line.saturating_sub(1)) as usize;
    let end = (proc.source_end_line as usize).min(lines.len());
    let target = var_name.trim_matches('"').to_lowercase();

    for i in start..end {
        let stripped = lines[i].trim();
        let up = stripped.to_uppercase();
        if up == "BEGIN" || up.starts_with("BEGIN ") {
            break;
        }
        let lower = stripped.to_lowercase();
        if lower.contains(&target) {
            if lower.split_whitespace().any(|w| {
                let clean: String = w.chars().filter(|c| c.is_alphanumeric() || *c == '_').collect();
                clean == target
            }) {
                return Some((i + 1) as u32);
            }
        }
    }
    None
}

pub fn map_stmt_idx_to_sql_line(stmt_idx: usize, stmt_lines: &[u32]) -> u32 {
    if stmt_idx < stmt_lines.len() {
        stmt_lines[stmt_idx]
    } else {
        0
    }
}

pub fn format_debug_comment(source_path: &str, line_number: u32, max_len: usize) -> String {
    if line_number == 0 || source_path.is_empty() || !Path::new(source_path).exists() {
        return format!("// [DEBUG] L{}", line_number);
    }
    let content = std::fs::read_to_string(source_path).unwrap_or_default();
    let lines: Vec<&str> = content.lines().collect();
    if line_number < 1 || line_number as usize > lines.len() {
        return format!("// [DEBUG] L{}", line_number);
    }
    let raw = lines[line_number as usize - 1].trim();
    let fname = Path::new(source_path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let truncated: String = raw.chars().take(max_len).collect();
    let suffix = if raw.chars().count() > max_len { "..." } else { "" };
    format!("// [DEBUG] {}:{} → {}{}", fname, line_number, truncated, suffix)
}

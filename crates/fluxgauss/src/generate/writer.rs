use std::path::Path;

use encoding_rs::Encoding;

pub struct CodeWriter {
    lines: Vec<String>,
    indent_level: usize,
}

impl CodeWriter {
    pub fn new() -> Self {
        Self {
            lines: Vec::new(),
            indent_level: 0,
        }
    }

    pub fn line(&mut self, text: &str) {
        let indent = "    ".repeat(self.indent_level);
        self.lines.push(format!("{}{}", indent, text));
    }

    /// Pre-formatted line; skips current indent (for fully-indented method blocks).
    pub fn raw_line(&mut self, text: &str) {
        self.lines.push(text.to_string());
    }

    pub fn blank(&mut self) {
        self.lines.push(String::new());
    }

    pub fn push_indent(&mut self) {
        self.indent_level += 1;
    }

    pub fn pop_indent(&mut self) {
        if self.indent_level > 0 {
            self.indent_level -= 1;
        }
    }

    pub fn indented<F: FnOnce(&mut Self)>(&mut self, f: F) {
        self.push_indent();
        f(self);
        self.pop_indent();
    }

    pub fn to_string(&self) -> String {
        self.lines.join("\n")
    }

    pub fn write_to_file(&self, path: &Path, encoding: &'static Encoding) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let content = format!("{}\n", self.to_string());
        let (cow, _, _) = encoding.encode(&content);
        std::fs::write(path, cow.into_owned())
    }
}

/// Brace-depth re-indent (Python parity). `base` = depth-0 indent (usually 8 spaces).
/// `}`-prefix decreases depth first; trailing `{` increases after; single-line `{...}` ignored.
pub fn indent_java_body(lines: &[String], base: &str) -> Vec<String> {
    let mut depth: i32 = 0;
    let mut indented = Vec::with_capacity(lines.len());
    for line in lines {
        let stripped = line.trim();
        if stripped.is_empty() {
            indented.push(String::new());
            continue;
        }

        let last_open = stripped.rfind('{');
        let last_close = stripped.rfind('}');
        let closes = stripped.starts_with('}');
        let opens = match (last_open, last_close) {
            (Some(o), Some(c)) => o > c,
            (Some(_), None) => true,
            _ => false,
        };
        let single_line = matches!((last_open, last_close), (Some(o), Some(c)) if o < c);

        if closes && !single_line {
            depth -= 1;
            if depth < 0 {
                depth = 0;
            }
        }

        indented.push(format!(
            "{}{}{}",
            base,
            "    ".repeat(depth as usize),
            stripped
        ));

        if opens && !single_line {
            depth += 1;
        }
    }
    indented
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn test_basic_line_with_indent() {
        let mut w = CodeWriter::new();
        w.line("hello");
        w.push_indent();
        w.line("world");
        w.pop_indent();
        w.line("done");
        assert_eq!(w.to_string(), "hello\n    world\ndone");
    }

    #[test]
    fn test_push_pop_indent() {
        let mut w = CodeWriter::new();
        w.line("level0");
        w.push_indent();
        w.line("level1");
        w.push_indent();
        w.line("level2");
        w.pop_indent();
        w.line("back1");
        w.pop_indent();
        w.line("back0");
        assert_eq!(w.to_string(), "level0\n    level1\n        level2\n    back1\nback0");
    }

    #[test]
    fn test_blank_lines() {
        let mut w = CodeWriter::new();
        w.line("first");
        w.blank();
        w.line("second");
        assert_eq!(w.to_string(), "first\n\nsecond");
    }

    #[test]
    fn test_to_string_empty() {
        let w = CodeWriter::new();
        assert_eq!(w.to_string(), "");
    }

    #[test]
    fn test_write_to_file() -> std::io::Result<()> {
        let dir = tempfile::tempdir()?;
        let path = dir.path().join("output.txt");
        let mut w = CodeWriter::new();
        w.line("hello");
        w.write_to_file(&path, encoding_rs::UTF_8)?;
        let mut content = String::new();
        std::fs::File::open(&path)?.read_to_string(&mut content)?;
        assert_eq!(content, "hello\n");
        Ok(())
    }

    #[test]
    fn test_raw_line_ignores_indent_level() {
        let mut w = CodeWriter::new();
        w.push_indent();
        w.line("indented");
        w.raw_line("    already_formatted");
        w.raw_line("        nested");
        assert_eq!(
            w.to_string(),
            "    indented\n    already_formatted\n        nested"
        );
    }

    #[test]
    fn test_indent_java_body_nested_control_flow() {
        let lines = vec![
            "int x = 0;".to_string(),
            "if (x > 0) {".to_string(),
            "doSomething();".to_string(),
            "for (Map<String, Object> r : rList) {".to_string(),
            "process(r);".to_string(),
            "}".to_string(),
            "} else {".to_string(),
            "other();".to_string(),
            "}".to_string(),
            "return;".to_string(),
        ];
        let out = indent_java_body(&lines, "        ");
        assert_eq!(
            out,
            vec![
                "        int x = 0;",
                "        if (x > 0) {",
                "            doSomething();",
                "            for (Map<String, Object> r : rList) {",
                "                process(r);",
                "            }",
                "        } else {",
                "            other();",
                "        }",
                "        return;",
            ]
        );
    }

    #[test]
    fn test_indent_java_body_single_line_braces_no_depth_change() {
        let lines = vec![
            "if (x == null) { x = 0; }".to_string(),
            "y = 1;".to_string(),
        ];
        let out = indent_java_body(&lines, "        ");
        assert_eq!(
            out,
            vec![
                "        if (x == null) { x = 0; }",
                "        y = 1;",
            ]
        );
    }

    #[test]
    fn test_indent_java_body_strips_existing_indent() {
        let lines = vec![
            "    try {".to_string(),
            "        __SQLERRM__ = e.getMessage();".to_string(),
            "    } catch (Exception e) {".to_string(),
            "        handle();".to_string(),
            "    }".to_string(),
        ];
        let out = indent_java_body(&lines, "        ");
        assert_eq!(
            out,
            vec![
                "        try {",
                "            __SQLERRM__ = e.getMessage();",
                "        } catch (Exception e) {",
                "            handle();",
                "        }",
            ]
        );
    }
}

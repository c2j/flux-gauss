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
}

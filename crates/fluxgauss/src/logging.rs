use std::io::Write;
use std::path::{Path, PathBuf};

pub struct ConversionLog {
    log_path: PathBuf,
    latest_path: PathBuf,
    fh: std::fs::File,
}

impl ConversionLog {
    pub fn new(output_dir: &str) -> std::io::Result<Self> {
        let log_dir = Path::new(output_dir)
            .join(".fluxgauss")
            .join("logs");
        std::fs::create_dir_all(&log_dir)?;

        let ts = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
        let log_path = log_dir.join(format!("conversion-{}.log", ts));
        let latest_path = log_dir.join("conversion-latest.log");

        let mut fh = std::fs::File::create(&log_path)?;
        writeln!(fh, "FluxGauss Conversion Log")?;
        writeln!(fh, "Started: {}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"))?;
        writeln!(fh, "Output: {}", output_dir)?;
        writeln!(fh)?;
        fh.flush()?;

        Ok(Self { log_path, latest_path, fh })
    }

    pub fn log(&mut self, msg: &str) {
        let clean = strip_ansi(msg);
        let ts = chrono::Local::now().format("%H:%M:%S");
        let _ = writeln!(self.fh, "[{}] {}", ts, clean);
        let _ = self.fh.flush();
    }

    pub fn latest_log_path(&self) -> &Path {
        &self.latest_path
    }

    pub fn close(self) -> std::io::Result<PathBuf> {
        drop(self.fh);
        let _ = std::fs::copy(&self.log_path, &self.latest_path);
        Ok(self.log_path)
    }
}

fn strip_ansi(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            if chars.peek() == Some(&'[') {
                chars.next();
                while let Some(&nc) = chars.peek() {
                    chars.next();
                    if nc.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
        } else {
            result.push(c);
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_ansi() {
        assert_eq!(strip_ansi("hello"), "hello");
        assert_eq!(strip_ansi("\x1b[32mgreen\x1b[0m"), "green");
        assert_eq!(strip_ansi("no\x1b[1;31mcolor\x1b[0m here"), "nocolor here");
    }

    #[test]
    fn test_log_creation() {
        let dir = tempfile::tempdir().unwrap();
        let output = dir.path().to_string_lossy().into_owned();
        let mut log = ConversionLog::new(&output).unwrap();
        log.log("test message");
        let path = log.close().unwrap();
        assert!(path.exists());
        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("test message"));
        assert!(dir.path().join(".fluxgauss/logs/conversion-latest.log").exists());
    }
}

use std::collections::HashMap;
use std::io::{IsTerminal, Write};
use std::sync::Mutex;
use std::sync::OnceLock;
use std::time::Instant;

const BAR_WIDTH: usize = 72;
const LINE_PAD: usize = 120;

fn phase_starts() -> &'static Mutex<HashMap<String, Instant>> {
    static STARTS: OnceLock<Mutex<HashMap<String, Instant>>> = OnceLock::new();
    STARTS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn format_elapsed(duration: std::time::Duration) -> String {
    let secs = duration.as_secs();
    if secs < 60 {
        format!("{}s", secs)
    } else if secs < 3600 {
        format!("{}m {:02}s", secs / 60, secs % 60)
    } else {
        format!("{}h {:02}m {:02}s", secs / 3600, (secs % 3600) / 60, secs % 60)
    }
}

pub fn progress_bar(phase: &str, current: usize, total: usize, status: &str) {
    if !std::io::stderr().is_terminal() {
        return;
    }

    {
        let mut starts = phase_starts().lock().unwrap();
        if !starts.contains_key(phase) {
            starts.insert(phase.to_string(), Instant::now());
        }
    }

    let pct = if total > 0 { current as f64 / total as f64 } else { 1.0 };
    let filled = (BAR_WIDTH as f64 * pct) as usize;
    let bar: String = "\u{2588}".repeat(filled) + &"\u{2591}".repeat(BAR_WIDTH.saturating_sub(filled));

    let elapsed = {
        let starts = phase_starts().lock().unwrap();
        starts.get(phase).map(|i| i.elapsed()).unwrap_or_default()
    };
    let elapsed_str = format_elapsed(elapsed);

    let label = format!("{:<8}", phase);
    let mut line = format!("\r  {} [{}] {}/{} {:5.1}%  {}", label, bar, current, total, pct * 100.0, elapsed_str);

    if !status.is_empty() {
        let truncated = if status.len() > 34 { format!("{}\u{2026}", &status[..33]) } else { status.to_string() };
        line.push_str(&format!("  {:<35}", truncated));
    }

    let pad = if line.len() < LINE_PAD { " ".repeat(LINE_PAD - line.len()) } else { String::new() };

    let mut stderr = std::io::stderr();
    let _ = write!(stderr, "{}{}", line, pad);
    let _ = stderr.flush();
}

pub fn progress_done(phase: &str, total: usize) {
    if !std::io::stderr().is_terminal() {
        return;
    }

    let elapsed = {
        let starts = phase_starts().lock().unwrap();
        starts.get(phase).map(|i| i.elapsed()).unwrap_or_default()
    };
    let elapsed_str = format_elapsed(elapsed);
    {
        let mut starts = phase_starts().lock().unwrap();
        starts.remove(phase);
    }

    let bar: String = "\u{2588}".repeat(BAR_WIDTH);
    let label = format!("{:<8}", phase);
    let line = format!("\r  {} [{}] {}/{} 100.0%  {}  \u{2713}\n", label, bar, total, total, elapsed_str);

    let mut stderr = std::io::stderr();
    let _ = write!(stderr, "{}", line);
    let _ = stderr.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bar_format() {
        progress_bar("Parse", 5, 10, "test.sql");
        progress_bar("Analyze", 100, 100, "");
        progress_done("Generate", 25);
    }

    #[test]
    fn test_zero_total() {
        progress_bar("Parse", 0, 0, "");
        progress_done("Parse", 0);
    }

    #[test]
    fn test_long_status_truncated() {
        let long_status = "a".repeat(100);
        progress_bar("Parse", 1, 10, &long_status);
    }

    #[test]
    fn test_format_elapsed() {
        assert_eq!(format_elapsed(std::time::Duration::from_secs(5)), "5s");
        assert_eq!(format_elapsed(std::time::Duration::from_secs(83)), "1m 23s");
        assert_eq!(format_elapsed(std::time::Duration::from_secs(3725)), "1h 02m 05s");
    }
}

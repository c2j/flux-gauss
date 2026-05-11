use std::io::{IsTerminal, Write};

const BAR_WIDTH: usize = 72;
const LINE_PAD: usize = 120;

pub fn progress_bar(phase: &str, current: usize, total: usize, status: &str) {
    if !std::io::stderr().is_terminal() {
        return;
    }
    let pct = if total > 0 {
        current as f64 / total as f64
    } else {
        1.0
    };
    let filled = (BAR_WIDTH as f64 * pct) as usize;
    let bar: String = "\u{2588}".repeat(filled) + &"\u{2591}".repeat(BAR_WIDTH.saturating_sub(filled));

    let label = format!("{:<8}", phase);
    let mut line = format!(
        "\r  {} [{}] {}/{} {:5.1}%",
        label, bar, current, total, pct * 100.0
    );

    if !status.is_empty() {
        let truncated = if status.len() > 41 {
            format!("{}\u{2026}", &status[..40])
        } else {
            status.to_string()
        };
        line.push_str(&format!("  {:<42}", truncated));
    }

    let pad = if line.len() < LINE_PAD {
        " ".repeat(LINE_PAD - line.len())
    } else {
        String::new()
    };

    let mut stderr = std::io::stderr();
    let _ = write!(stderr, "{}{}", line, pad);
    let _ = stderr.flush();
}

pub fn progress_done(phase: &str, total: usize) {
    if !std::io::stderr().is_terminal() {
        return;
    }
    let bar: String = "\u{2588}".repeat(BAR_WIDTH);
    let label = format!("{:<8}", phase);
    let line = format!(
        "\r  {} [{}] {}/{} 100.0%  \u{2713}\n",
        label, bar, total, total
    );

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
}

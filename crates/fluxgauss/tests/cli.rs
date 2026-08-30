use std::fs;
use std::process::Command;

/// Regression for #93/#74: CLI `-o <dir>` must be respected — generated
/// Java/XML/pom must land in `-o`, not in CWD's default `./dest`.
#[test]
fn cli_output_dir_is_respected() {
    let bin = env!("CARGO_BIN_EXE_fluxgauss");
    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path();

    let sql_path = cwd.join("a.sql");
    fs::write(
        &sql_path,
        "CREATE OR REPLACE FUNCTION BIGFUND.FNC_A (p_i_x VARCHAR2)\n\
         \x20 RETURN VARCHAR2 IS\n\
         BEGIN\n\
         \x20 RETURN p_i_x || '_A';\n\
         END;\n\
         /\n",
    )
    .unwrap();

    let out = cwd.join("out_ru");
    let status = Command::new(bin)
        .args(["-o", out.to_str().unwrap(), "-s", sql_path.to_str().unwrap(), "--full", "--skip-validate"])
        .current_dir(cwd)
        .status()
        .unwrap();
    assert!(status.success(), "fluxgauss CLI should exit 0");

    assert!(out.join("pom.xml").exists(), "-o dir must contain pom.xml (got output written elsewhere)");
    let mut java_files = 0;
    let src = out.join("src");
    if src.exists() {
        for entry in walk_java(&src) {
            if entry.extension().is_some_and(|e| e == "java") {
                java_files += 1;
            }
        }
    }
    assert!(java_files >= 1, "-o dir must contain generated .java sources");

    let cwd_dest = cwd.join("dest");
    assert!(!cwd_dest.exists(), "generated files must NOT land in CWD/dest when -o is given");
}

fn walk_java(dir: &std::path::Path) -> Vec<std::path::PathBuf> {
    let mut out = vec![];
    if let Ok(entries) = fs::read_dir(dir) {
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                out.extend(walk_java(&p));
            } else {
                out.push(p);
            }
        }
    }
    out
}

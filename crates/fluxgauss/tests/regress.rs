use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use fluxgauss::config::AppConfig;
use fluxgauss::incremental::IncrementalState;
use fluxgauss::naming::package_to_classname;
use fluxgauss::pipeline::run_pipeline;

const FIXTURES_REL: &str = "../../tests/regress/fixtures";
const GOLDEN_REL: &str = "../../tests/regress/golden/ru";
const BASE_PACKAGE: &str = "com.example.demo";

const KNOWN_BROKEN: &[&str] = &[];

const FOUR_FILE_TYPES: &[(&str, FilePathFn)] = &[
    ("Service.java", service_path as FilePathFn),
    ("Mapper.java", mapper_path as FilePathFn),
    ("Mapper.xml", xml_path as FilePathFn),
    ("ServiceTest.java", test_path as FilePathFn),
];

type FilePathFn = fn(&Path, &str) -> PathBuf;

fn service_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir.join("src/main/java/com/example/demo/service").join(format!("{}Service.java", class_name))
}

fn mapper_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir.join("src/main/java/com/example/demo/mapper").join(format!("{}Mapper.java", class_name))
}

fn xml_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir.join("src/main/resources/mapper").join(format!("{}Mapper.xml", class_name))
}

fn test_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir.join("src/test/java/com/example/demo/service").join(format!("{}ServiceTest.java", class_name))
}

fn fixture_files() -> Vec<PathBuf> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let dir = Path::new(manifest_dir).join(FIXTURES_REL);
    let mut files: Vec<_> = fs::read_dir(&dir)
        .expect(&format!("Fixture dir not found: {}", dir.display()))
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map_or(false, |e| e == "sql"))
        .filter(|p| {
            let name = p.file_name().unwrap().to_string_lossy();
            !KNOWN_BROKEN.contains(&name.as_ref())
        })
        .collect();
    files.sort();
    files
}

fn pkg_name_from_path(path: &Path) -> String {
    let stem = path.file_stem().unwrap().to_string_lossy();
    let name = stem.as_ref();
    if let Some(s) = name.strip_prefix("pkg_") {
        s.to_string()
    } else if let Some(s) = name.strip_prefix("PKG_") {
        s.to_string()
    } else {
        name.to_string()
    }
}

fn normalize(content: &str) -> String {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let fixture_root = Path::new(manifest_dir).join(FIXTURES_REL);
    let fixture_prefix = fixture_root.to_string_lossy().to_string();

    let lines: Vec<String> =
        content.lines().map(|l| l.trim_end().replace(&fixture_prefix, "{FIXTURES_ROOT}")).collect();

    let mut result = String::new();
    let mut blank_count = 0;
    for line in lines {
        if line.is_empty() {
            blank_count += 1;
            if blank_count <= 2 {
                result.push('\n');
            }
        } else {
            blank_count = 0;
            result.push_str(&line);
            result.push('\n');
        }
    }
    result.trim_end().to_string()
}

fn is_golden_gen_mode() -> bool {
    env::var("REGEN_RUST_GOLDEN").is_ok()
}

struct GeneratedFiles {
    files: HashMap<String, String>,
    total_cross_calls: usize,
    unresolved_calls: usize,
}

fn run_conversion(sql_file: &Path, out_dir: &Path) -> GeneratedFiles {
    let pkg_name = pkg_name_from_path(sql_file);
    let class_name = package_to_classname(&pkg_name);

    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(vec![sql_file.to_string_lossy().into()]),
        ..Default::default()
    };

    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), false);
    inc.initialize().expect("Failed to initialize incremental state");

    let result = run_pipeline(&[sql_file.to_path_buf()], &config, &mut inc, false);

    let mut files = HashMap::new();
    let base = Path::new(out_dir);
    let pkg_path = BASE_PACKAGE.replace('.', "/");
    let scan_dirs: [(&str, PathBuf); 4] = [
        ("Service.java", base.join("src/main/java").join(&pkg_path).join("service")),
        ("Mapper.java", base.join("src/main/java").join(&pkg_path).join("mapper")),
        ("Mapper.xml", base.join("src/main/resources/mapper")),
        ("ServiceTest.java", base.join("src/test/java").join(&pkg_path).join("service")),
    ];
    for (ft, dir) in &scan_dirs {
        // Collect ALL files of this type, sorted by filename, so the comparison
        // is deterministic (fs::read_dir order is filesystem-dependent) and
        // covers every generated file — a fixture with N packages produces N
        // services/mappers/xmls/tests, and the golden must guard all of them.
        if let Ok(entries) = fs::read_dir(dir) {
            let mut matched: Vec<(String, String)> = entries
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.file_name().map_or(false, |n| n.to_string_lossy().ends_with(ft)))
                .filter_map(|p| {
                    let name = p.file_name().unwrap().to_string_lossy().to_string();
                    fs::read_to_string(&p).ok().map(|content| (name, content))
                })
                .collect();
            matched.sort_by(|a, b| a.0.cmp(&b.0));
            if matched.len() == 1 {
                files.insert(ft.to_string(), matched.into_iter().next().unwrap().1);
            } else if matched.len() > 1 {
                let content = matched
                    .into_iter()
                    .map(|(name, c)| format!("// ===== FILE: {} =====\n{}", name, c))
                    .collect::<Vec<_>>()
                    .join("\n");
                files.insert(ft.to_string(), content);
            }
        }
    }

    GeneratedFiles {
        files,
        total_cross_calls: result.total_cross_calls,
        unresolved_calls: result.unresolved_calls.len(),
    }
}

fn run_multi_file_conversion(sql_files: &[PathBuf], out_dir: &Path) -> GeneratedFiles {
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(sql_files.iter().map(|path| path.to_string_lossy().into()).collect()),
        ..Default::default()
    };

    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), false);
    inc.initialize().expect("Failed to initialize incremental state");
    let result = run_pipeline(sql_files, &config, &mut inc, false);
    let service = fs::read_to_string(service_path(out_dir, "Bigfund")).expect("BigfundService.java was not generated");
    let mut files = HashMap::new();
    files.insert("Service.java".to_string(), service);
    GeneratedFiles {
        files,
        total_cross_calls: result.total_cross_calls,
        unresolved_calls: result.unresolved_calls.len(),
    }
}

#[test]
fn issue_70_same_schema_routines_share_one_service() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files = vec![fixtures.join("issue_70_fnc_a.sql"), fixtures.join("issue_70_fnc_b.sql")];
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let generated = run_multi_file_conversion(&sql_files, &tmp.path().join("dest"));
    let service = generated.files.get("Service.java").unwrap();

    assert!(service.contains("fncA("), "fncA missing from BigfundService");
    assert!(service.contains("fncB("), "fncB missing from BigfundService");
}

#[test]
fn issue_70_other_source_change_preserves_merged_service_incrementally() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let source_dir = tmp.path().join("sql");
    fs::create_dir_all(&source_dir).unwrap();
    let sql_files: Vec<PathBuf> = ["issue_70_fnc_a.sql", "issue_70_fnc_b.sql"]
        .iter()
        .map(|name| {
            let destination = source_dir.join(name);
            fs::copy(fixtures.join(name), &destination).unwrap();
            destination
        })
        .collect();
    let out_dir = tmp.path().join("dest");

    let first = run_multi_file_conversion(&sql_files, &out_dir);
    assert!(first.files["Service.java"].contains("fncA("));
    assert!(first.files["Service.java"].contains("fncB("));

    let changed = fs::read_to_string(&sql_files[1]).unwrap().replace("'_B'", "'_B2'");
    fs::write(&sql_files[1], changed).unwrap();
    let second = run_multi_file_conversion(&sql_files, &out_dir);
    let service = &second.files["Service.java"];
    assert!(service.contains("fncA("));
    assert!(service.contains("fncB("));
    assert!(service.contains("\"_B2\""));

    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(out_dir.join(".fluxgauss/manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["files"][sql_files[1].to_string_lossy().as_ref()]["packages"][0], "BIGFUND");
}

#[test]
fn regress_golden_compare() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let fixtures = fixture_files();

    if fixtures.is_empty() {
        panic!("No SQL fixtures found");
    }

    let gen_mode = is_golden_gen_mode();

    for sql_file in &fixtures {
        let tmp = tempfile::tempdir().expect("Failed to create temp dir");
        let out_dir = tmp.path().join("dest");
        let generated = run_conversion(sql_file, &out_dir);
        let pkg_name = pkg_name_from_path(sql_file);
        let golden_dir = manifest_dir.join(GOLDEN_REL).join(&pkg_name);

        assert!(!generated.files.is_empty(), "No files generated for {}", sql_file.display());

        if gen_mode {
            fs::create_dir_all(&golden_dir).expect("Failed to create golden dir");
            for (ft, content) in &generated.files {
                let normalized = normalize(content);
                let golden_path = golden_dir.join(format!("{}.golden", ft));
                fs::write(&golden_path, &normalized).unwrap();
            }
        } else {
            for (ft, content) in &generated.files {
                let actual = normalize(content);
                let golden_path = golden_dir.join(format!("{}.golden", ft));
                let expected = fs::read_to_string(&golden_path).unwrap_or_else(|_| {
                    panic!(
                        "Golden file missing for {}/{}.golden\nRun: REGEN_RUST_GOLDEN=1 cargo test --test regress",
                        pkg_name, ft
                    )
                });
                assert_eq!(
                    normalize(&expected),
                    actual,
                    "Golden mismatch for {} / {}. Run REGEN_RUST_GOLDEN=1 to update.",
                    pkg_name,
                    ft
                );
            }
        }
    }
}

#[test]
fn regress_pipeline_smoke() {
    let fixtures = fixture_files();
    assert!(!fixtures.is_empty(), "No fixtures found");

    for sql_file in &fixtures {
        let tmp = tempfile::tempdir().expect("Failed to create temp dir");
        let out_dir = tmp.path().join("dest");
        let generated = run_conversion(sql_file, &out_dir);

        let name = sql_file.display();
        assert!(generated.files.contains_key("Service.java"), "{}: missing Service.java", name);
        assert!(generated.files.contains_key("Mapper.java"), "{}: missing Mapper.java", name);
        assert!(generated.files.contains_key("Mapper.xml"), "{}: missing Mapper.xml", name);
        assert!(generated.files.contains_key("ServiceTest.java"), "{}: missing ServiceTest.java", name);

        for (_ft, content) in &generated.files {
            assert!(content.len() > 50, "{}: {} too small ({} bytes)", name, _ft, content.len());
        }
    }
}

#[test]
fn issue_72_string_to_number_coercion_compiles() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let sql_file = manifest_dir.join(FIXTURES_REL).join("issue_72_string_to_number.sql");
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let generated = run_conversion(&sql_file, &tmp.path().join("dest"));
    let service = generated.files.get("Service.java").expect("Service.java not generated");

    assert!(!service.contains("((Number)(vFlag))"), "String variable must never be cast to Number:\n{}", service);
    assert!(
        service.contains("Long.parseLong(String.valueOf(vFlag))"),
        "String-to-Long assignment must use parse-style conversion:\n{}",
        service
    );
    assert!(
        !service.contains("vAmt = Double.parseDouble"),
        "double-producing arithmetic must be coerced before Long assignment:\n{}",
        service
    );

    for bad in [
        "Long.parseLong(pIn.length())",
        "Integer.parseInt(pIn.length())",
        "Long.parseLong(vStr.length())",
        "Integer.parseInt(vStr.length())",
    ] {
        assert!(
            !service.contains(bad),
            "a method call ON a String is not itself a String — parsing its int \
             result does not compile. Found `{}` in:\n{}",
            bad,
            service
        );
    }
}

#[test]
fn issue_72b_math_string_arguments_are_parsed() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let sql_file = manifest_dir.join(FIXTURES_REL).join("issue_72b_math_string_args.sql");
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let generated = run_conversion(&sql_file, &tmp.path().join("dest"));
    let service = generated.files.get("Service.java").expect("Service.java not generated");

    assert!(
        service.contains("Math.abs(Double.parseDouble(String.valueOf(vFlag)))"),
        "ABS(String) must parse its argument before calling Math.abs:\n{}",
        service
    );
    assert!(
        service.contains("Math.round(Double.parseDouble(String.valueOf(vFlag)))"),
        "ROUND(String) must parse its argument before calling Math.round:\n{}",
        service
    );
}

#[test]
fn issue_71_schema_qualified_cross_package_call_resolves() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let sql_file = manifest_dir.join(FIXTURES_REL).join("issue_71_cross_pkg_schema.sql");
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let out_dir = tmp.path().join("dest");
    let generated = run_conversion(&sql_file, &out_dir);
    let service = fs::read_to_string(service_path(&out_dir, "Biz")).expect("BizService.java not generated");

    assert!(service.contains("logService.instLog("), "schema-qualified package call must resolve:\n{}", service);
    assert!(!service.contains("// CALL"), "resolved call must not remain a CALL fallback:\n{}", service);
    assert!(
        service.contains("private final LogService logService;"),
        "LogService field must be injected:\n{}",
        service
    );
    assert!(
        service.contains("BizService(BizMapper bizMapper, LogService logService)"),
        "LogService must be a constructor parameter:\n{}",
        service
    );
    assert_eq!(generated.total_cross_calls, 1);
    assert_eq!(generated.unresolved_calls, 0);

    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(vec![sql_file.to_string_lossy().into()]),
        ..Default::default()
    };
    let report_dir = tmp.path().join("report-dest");
    let mut inc = IncrementalState::new(report_dir.to_string_lossy().into_owned(), true);
    inc.initialize().expect("Failed to initialize incremental state");
    let result = run_pipeline(&[sql_file], &config, &mut inc, false);
    let report = fluxgauss::report::build_report(
        &result.packages,
        result.skipped,
        result.warnings,
        result.unresolved_calls,
        result.stub_count,
        "regress",
        &report_dir.to_string_lossy(),
        1,
    );
    assert!(report
        .mappings
        .iter()
        .any(|mapping| { mapping.sql_package == "PKG_LOG" && mapping.sql_procedure == "inst_log" }));
    assert!(report
        .mappings
        .iter()
        .any(|mapping| { mapping.sql_package == "PKG_BIZ" && mapping.sql_procedure == "do_it" }));
}

#[test]
fn issue_71_cross_schema_package_collision_is_reported() {
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let source_a = tmp.path().join("a_pkg_dup.sql");
    let source_b = tmp.path().join("b_pkg_dup.sql");
    fs::write(
        &source_a,
        "CREATE OR REPLACE PACKAGE BODY A.PKG_DUP IS\nPROCEDURE from_a IS BEGIN NULL; END;\nEND PKG_DUP;\n/\n",
    )
    .unwrap();
    fs::write(
        &source_b,
        "CREATE OR REPLACE PACKAGE BODY B.PKG_DUP IS\nPROCEDURE from_b IS BEGIN NULL; END;\nEND PKG_DUP;\n/\n",
    )
    .unwrap();
    let out_dir = tmp.path().join("dest");
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(vec![source_a.to_string_lossy().into(), source_b.to_string_lossy().into()]),
        ..Default::default()
    };
    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), false);
    inc.initialize().expect("Failed to initialize incremental state");
    let result = run_pipeline(&[source_a, source_b], &config, &mut inc, false);

    assert!(
        result.warnings.iter().any(|warning| {
            warning.contains("PKG_DUP") && warning.contains("A.PKG_DUP") && warning.contains("B.PKG_DUP")
        }),
        "cross-schema package folding must emit a warning: {:?}",
        result.warnings
    );
    let report = fluxgauss::report::build_report(
        &result.packages,
        result.skipped.clone(),
        result.warnings.clone(),
        result.unresolved_calls.clone(),
        result.stub_count,
        "regress",
        &out_dir.to_string_lossy(),
        2,
    );
    assert!(
        report.to_markdown().contains("A.PKG_DUP, B.PKG_DUP"),
        "collision warning must be visible in the conversion report"
    );
}

#[test]
fn issue_70_distinct_paths_do_not_share_one_ast_cache_entry() {
    // "x/a.sql" and "x_a.sql" both sanitize to "x_a.sql". Before the path digest
    // was added they shared a cache file, so a warm run could load the WRONG
    // file's AST and silently drop a package.
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let root = tmp.path();
    std::fs::create_dir_all(root.join("x")).unwrap();
    std::fs::write(
        root.join("x/a.sql"),
        "CREATE OR REPLACE PACKAGE pkg_alpha IS\n  PROCEDURE alpha_one(p IN VARCHAR2);\nEND pkg_alpha;\n/\nCREATE OR REPLACE PACKAGE BODY pkg_alpha IS\n  PROCEDURE alpha_one(p IN VARCHAR2) IS BEGIN NULL; END;\nEND pkg_alpha;\n/\n",
    )
    .unwrap();
    std::fs::write(
        root.join("x_a.sql"),
        "CREATE OR REPLACE PACKAGE pkg_beta IS\n  PROCEDURE beta_one(p IN VARCHAR2);\nEND pkg_beta;\n/\nCREATE OR REPLACE PACKAGE BODY pkg_beta IS\n  PROCEDURE beta_one(p IN VARCHAR2) IS BEGIN NULL; END;\nEND pkg_beta;\n/\n",
    )
    .unwrap();

    let state = fluxgauss::incremental::IncrementalState::new(root.join("out"), false);
    let a = state.cached_ast_path_for_test(&root.join("x/a.sql"));
    let b = state.cached_ast_path_for_test(&root.join("x_a.sql"));
    assert_ne!(a, b, "distinct source paths must not share one AST cache file: {:?}", a);
}

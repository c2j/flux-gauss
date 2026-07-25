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

const KNOWN_BROKEN: &[&str] = &[
    "complex_clearing_pkg.sql",
    "issue_34_35_dto_naming.sql",
    "issue_38_map_put.sql",
    "issue_39_thread_safety.sql",
    "issue_40_string_compare.sql",
    "issue_41_type_system.sql",
    "issue_44_if_elsif_goto.sql",
    "issue_45_exception_handling.sql",
    "issue_46_chr_ascii_substr.sql",
    "issue_47_long_parse_string.sql",
    "issue_48_long_compareto_string.sql",
    "issue_49_varchar2_concat.sql",
    "issue_44_if_elsif_goto_2.sql",
];

const FOUR_FILE_TYPES: &[(&str, FilePathFn)] = &[
    ("Service.java", service_path as FilePathFn),
    ("Mapper.java", mapper_path as FilePathFn),
    ("Mapper.xml", xml_path as FilePathFn),
    ("ServiceTest.java", test_path as FilePathFn),
];

type FilePathFn = fn(&Path, &str) -> PathBuf;

fn service_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir
        .join("src/main/java/com/example/demo/service")
        .join(format!("{}Service.java", class_name))
}

fn mapper_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir
        .join("src/main/java/com/example/demo/mapper")
        .join(format!("{}Mapper.java", class_name))
}

fn xml_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir
        .join("src/main/resources/mapper")
        .join(format!("{}Mapper.xml", class_name))
}

fn test_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir
        .join("src/test/java/com/example/demo/service")
        .join(format!("{}ServiceTest.java", class_name))
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

    let lines: Vec<String> = content
        .lines()
        .map(|l| l.trim_end().replace(&fixture_prefix, "{FIXTURES_ROOT}"))
        .collect();

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

    let _result = run_pipeline(
        &[sql_file.to_path_buf()],
        &config,
        &mut inc,
        false,
    );

    let mut files = HashMap::new();
    for (ft, path_fn) in FOUR_FILE_TYPES {
        let filepath = path_fn(out_dir, &class_name);
        if filepath.exists() {
            let content = fs::read_to_string(&filepath)
                .unwrap_or_else(|_| panic!("Failed to read: {}", filepath.display()));
            files.insert(ft.to_string(), content);
        }
    }

    GeneratedFiles { files }
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

        assert!(
            !generated.files.is_empty(),
            "No files generated for {}",
            sql_file.display()
        );

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
                let expected = fs::read_to_string(&golden_path)
                    .unwrap_or_else(|_| panic!(
                        "Golden file missing for {}/{}.golden\nRun: REGEN_RUST_GOLDEN=1 cargo test --test regress",
                        pkg_name, ft
                    ));
                assert_eq!(
                    normalize(&expected), actual,
                    "Golden mismatch for {} / {}. Run REGEN_RUST_GOLDEN=1 to update.",
                    pkg_name, ft
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

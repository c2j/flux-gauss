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

fn service_path(out_dir: &Path, class_name: &str) -> PathBuf {
    out_dir.join("src/main/java/com/example/demo/service").join(format!("{}Service.java", class_name))
}

fn fixture_files() -> Vec<PathBuf> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let dir = Path::new(manifest_dir).join(FIXTURES_REL);
    let mut files: Vec<_> = fs::read_dir(&dir)
        .unwrap_or_else(|_| panic!("Fixture dir not found: {}", dir.display()))
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "sql"))
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
    let _class_name = package_to_classname(&pkg_name);

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
                .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with(ft)))
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

/// Runs a multi-file conversion and returns *all* generated `*Service.java`
/// files, keyed by filename (including the `.java` extension). Unlike
/// `run_multi_file_conversion` (which hardcodes reading `BigfundService.java`
/// for the issue_70 same-package-merge scenario), this scans the service
/// output directory and collects every service file, so it works for
/// fixtures that produce multiple distinct packages/services.
fn run_multi_file_services(sql_files: &[PathBuf], out_dir: &Path) -> HashMap<String, String> {
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(sql_files.iter().map(|path| path.to_string_lossy().into()).collect()),
        ..Default::default()
    };

    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), false);
    inc.initialize().expect("Failed to initialize incremental state");
    let _result = run_pipeline(sql_files, &config, &mut inc, false);

    let pkg_path = BASE_PACKAGE.replace('.', "/");
    let service_dir = out_dir.join("src/main/java").join(&pkg_path).join("service");
    let mut files: Vec<(String, String)> = fs::read_dir(&service_dir)
        .map(|entries| {
            entries
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with("Service.java")))
                .filter_map(|p| {
                    let name = p.file_name().unwrap().to_string_lossy().to_string();
                    fs::read_to_string(&p).ok().map(|content| (name, content))
                })
                .collect()
        })
        .unwrap_or_default();
    files.sort_by(|a, b| a.0.cmp(&b.0));
    files.into_iter().collect()
}

/// Runs a multi-file conversion and returns *all* generated `*ServiceTest.java`
/// files, keyed by filename (including the `.java` extension). Sibling of
/// `run_multi_file_services` — same pipeline invocation, but scans the test
/// output directory instead of the main service directory.
fn run_multi_file_service_tests(sql_files: &[PathBuf], out_dir: &Path) -> HashMap<String, String> {
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(sql_files.iter().map(|path| path.to_string_lossy().into()).collect()),
        ..Default::default()
    };

    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), false);
    inc.initialize().expect("Failed to initialize incremental state");
    let _result = run_pipeline(sql_files, &config, &mut inc, false);

    let pkg_path = BASE_PACKAGE.replace('.', "/");
    let test_dir = out_dir.join("src/test/java").join(&pkg_path).join("service");
    let mut files: Vec<(String, String)> = fs::read_dir(&test_dir)
        .map(|entries| {
            entries
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with("ServiceTest.java")))
                .filter_map(|p| {
                    let name = p.file_name().unwrap().to_string_lossy().to_string();
                    fs::read_to_string(&p).ok().map(|content| (name, content))
                })
                .collect()
        })
        .unwrap_or_default();
    files.sort_by(|a, b| a.0.cmp(&b.0));
    files.into_iter().collect()
}

#[test]
fn issue_79_unqualified_cross_pkg_fn_resolves() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files =
        vec![fixtures.join("issue_79_unqualified_fn_callee.sql"), fixtures.join("issue_79_unqualified_fn_caller.sql")];
    let tmp = tempfile::tempdir().expect("tempdir");
    let files = run_multi_file_services(&sql_files, &tmp.path().join("dest"));
    let caller = files.get("Issue79UnqualifiedFnCallerService.java").expect("caller service");
    assert!(
        caller.contains("issue79UnqualifiedFnCalleeService.fncComGetday("),
        "unqualified fn must resolve to cross-pkg service call, got:\n{}",
        caller
    );
    assert!(caller.contains("issue79UnqualifiedFnCalleeService"), "service must be injected");
    assert!(!caller.contains("TOBEFIX"), "resolved call must not carry TOBEFIX marker");
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

/// #107 引入的裸函数调用真实跨包 service 生成暴露了 4 类下游缺口（demo 全流程编译从 0 错误
/// 退化到 21 处）。本测试用 issue_108 fixture 对 B（实参强转）与 D（同名 range loop 计数器
/// 声明）两类直接断言；A（声明段调用触发注入）与 C（callee OUT 提升为 AtomicReference）在
/// issue_108 的两文件组合里无法独立观察 —— `prc_get_emp_name` 是一条语句级 CALL，已经走
/// statement.rs 里完全正确的 promote/inject 路径，其对同一 callee 包的注入会掩盖
/// declare-only 场景下 analyze.rs 扫描缺口（A）；而它是 PROCEDURE 语句调用（非表达式内的
/// FUNCTION 调用），也不会触发 C 的 bug（C 只在 `v := fn(...)` 这种表达式路径出现，走的是
/// expr.rs 而非 statement.rs）。因此 A / C 各自用一个内联的最小 probe（不落盘进
/// tests/regress/fixtures，不参与 golden 比对）单独隔离验证。
#[test]
fn issue_108_cross_pkg_call_arg_and_out_handling() {
    // #107 回归四类：B 实参强转 / C callee OUT 提升 / A 声明段注入 / D 同名 range loop 计数器声明
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files =
        vec![fixtures.join("issue_108_cross_pkg_callee.sql"), fixtures.join("issue_108_cross_pkg_caller.sql")];
    let tmp = tempfile::tempdir().expect("tempdir");
    let files = run_multi_file_services(&sql_files, &tmp.path().join("dest"));
    let caller = files.get("Issue108CrossPkgCallerService.java").expect("caller service");

    // B1: `fn_calc_bonus(12000, 0.10, 2)` — the callee's params are all `long` (plain NUMBER
    // has no scale/precision hint here), so passing the literal `0.10` straight through is a
    // narrowing double->long conversion that javac rejects without an explicit cast/parse.
    // emit_cross_pkg_call (expr.rs) currently does zero arg coercion for cross-pkg calls, so
    // the raw literal passes through unchanged. Once fixed to reuse `coerce_arg_to_type`
    // (already used by the same-package call path), `0.10` becomes
    // `Long.parseLong(String.valueOf(0.10))` (existing Object-fallback branch).
    assert!(
        caller.contains("fnCalcBonus(Long.valueOf(12000), Long.parseLong(String.valueOf(0.10)), Long.valueOf(2))"),
        "B1: numeric literal args to cross-pkg calls must be coerced to the callee's declared \
         param type (bare `0.10` cannot implicitly narrow to `long`):\n{}",
        caller
    );

    // D: two `FOR i IN ...` range loops reuse the same iterator name `i`. The first loop
    // registers `i` into `local_vars`, so the second loop's counter-declaration is skipped —
    // but no method-level `int i;` is emitted either, leaving `i` completely undeclared for
    // the second loop.
    assert!(
        !caller.contains("for (i ="),
        "D: every range loop must declare its counter (no bare, undeclared `for (i =`):\n{}",
        caller
    );

    // --- A: declaration-section-only cross-pkg call must still inject the callee service ---
    // Isolated probe: the ONLY cross-pkg reference is `v_decl_bonus`'s default initializer
    // (no body statement references the callee at all), so field injection depends entirely
    // on analyze.rs's `discover_cross_service_refs` scanning `local_var_defaults` — which it
    // currently does not (it only scans `java_logic_lines`, i.e. body statements).
    let a_dir = tmp.path().join("a_probe");
    fs::create_dir_all(&a_dir).unwrap();
    let a_callee = a_dir.join("a_decl_only_callee.sql");
    let a_caller = a_dir.join("a_decl_only_caller.sql");
    fs::copy(fixtures.join("issue_108_cross_pkg_callee.sql"), &a_callee).unwrap();
    fs::write(
        &a_caller,
        "CREATE OR REPLACE PROCEDURE a_decl_only_caller(p_i_date VARCHAR2)\n\
         IS\n\
         \u{20}v_decl_bonus NUMBER := fn_calc_bonus(12000, 0.10, 2);\n\
         BEGIN\n\
         \u{20}NULL;\n\
         END;\n/\n",
    )
    .unwrap();
    let a_files = run_multi_file_services(&[a_callee, a_caller], &tmp.path().join("a_dest"));
    let a_caller_java = a_files.get("ADeclOnlyCallerService.java").expect("A-probe caller service");
    assert!(
        a_caller_java.contains("private final ADeclOnlyCalleeService"),
        "A: a cross-pkg call appearing ONLY in a declare-section default initializer must still \
         inject the callee service (field/constructor param/import) — analyze.rs's cross-service \
         scan must also cover `local_var_defaults`, not just `java_logic_lines`:\n{}",
        a_caller_java
    );

    // --- C: callee OUT param must be promoted to AtomicReference, even cross-package ---
    // Isolated probe: `fn_get_emp_details_c`'s 2nd param is OUT, called as `v_ret := fn(...)`
    // (a FUNCTION-call-in-expression, routed through expr.rs's emit_cross_pkg_call) rather than
    // a standalone `CALL proc(...)` statement (which statement.rs already promotes correctly —
    // that's why `prc_get_emp_name(1002, v_name)` in the main fixture above is NOT a suitable
    // probe for this bug).
    let c_dir = tmp.path().join("c_probe");
    fs::create_dir_all(&c_dir).unwrap();
    let c_callee = c_dir.join("c_probe_callee.sql");
    let c_caller = c_dir.join("c_probe_caller.sql");
    fs::write(
        &c_callee,
        "CREATE OR REPLACE FUNCTION fn_get_emp_details_c(p_i_id NUMBER, p_o_name OUT VARCHAR2)\n\
         RETURN NUMBER IS\n\
         BEGIN\n\
         \u{20}p_o_name := 'emp';\n\
         \u{20}RETURN 1;\n\
         END;\n/\n",
    )
    .unwrap();
    fs::write(
        &c_caller,
        "CREATE OR REPLACE PROCEDURE c_probe_caller(p_i_date VARCHAR2)\n\
         IS\n\
         \u{20}v_ret  NUMBER;\n\
         \u{20}v_name VARCHAR2(64);\n\
         BEGIN\n\
         \u{20}v_ret := fn_get_emp_details_c(1002, v_name);\n\
         END;\n/\n",
    )
    .unwrap();
    let c_files = run_multi_file_services(&[c_callee, c_caller], &tmp.path().join("c_dest"));
    let c_caller_java = c_files.get("CProbeCallerService.java").expect("C-probe caller service");
    assert!(
        c_caller_java.contains("AtomicReference<String> vName"),
        "C: a bare local passed to a callee's OUT (AtomicReference) parameter must be promoted \
         to AtomicReference, not declared as a plain String — emit_cross_pkg_call must reuse the \
         promote-to-AtomicReference logic that statement.rs's standalone-CALL path already has:\n{}",
        c_caller_java
    );
}

/// Root cause B3 (#107 follow-up): a cross-package FUNCTION call's *return value*
/// participating in a binary op must be treated according to the callee's declared
/// return type. `looks_bd_expr` only does textual pattern-matching (`.multiply(`,
/// literal `"BigDecimal"`, etc.) — a call expression like
/// `gaussFunctionCallsService.fnAvgAmount(pIId)` has none of those markers even
/// though the callee returns `java.math.BigDecimal`, so `v * 1.2` was emitted as
/// raw `BigDecimal * double`, which javac rejects.
#[test]
fn issue_108c_cross_pkg_call_return_value_bigdecimal_arithmetic() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files = vec![
        fixtures.join("issue_108c_bd_return_arith_callee.sql"),
        fixtures.join("issue_108c_bd_return_arith_caller.sql"),
    ];
    let tmp = tempfile::tempdir().expect("tempdir");
    let files = run_multi_file_services(&sql_files, &tmp.path().join("dest"));
    let caller = files.get("Issue108cBdReturnArithCallerService.java").expect("caller service");

    assert!(
        !caller.contains("* 1.2)") && !caller.contains(") * 1.2"),
        "B3: a BigDecimal-returning cross-package call must not be used as a raw operand \
         in a `*` expression with a double literal (BigDecimal has no `*` operator):\n{}",
        caller
    );
    assert!(
        caller.contains(".multiply(java.math.BigDecimal.valueOf(1.2))"),
        "B3: the callee's BigDecimal return type must route the multiplication through \
         BigDecimal.multiply(...), not raw `*`:\n{}",
        caller
    );
}

/// Root cause G (#107 follow-up): the generated unit test never stubs an
/// injected cross-package service call, so Mockito returns `null` for e.g.
/// `issue108cBdReturnArithCalleeService.fnAvgAmount(pIId)`, and the caller's
/// `.multiply(...)` on that `null` throws NPE at test run time.
#[test]
fn issue_g_cross_service_call_is_stubbed_in_generated_test() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files = vec![
        fixtures.join("issue_108c_bd_return_arith_callee.sql"),
        fixtures.join("issue_108c_bd_return_arith_caller.sql"),
    ];
    let tmp = tempfile::tempdir().expect("tempdir");
    let files = run_multi_file_service_tests(&sql_files, &tmp.path().join("dest"));
    let caller_test = files.get("Issue108cBdReturnArithCallerServiceTest.java").expect("caller test");

    assert!(
        caller_test.contains("when(issue108cBdReturnArithCalleeService.fnAvgAmount("),
        "G: the generated test must stub the injected cross-package service call \
         `issue108cBdReturnArithCalleeService.fnAvgAmount(...)` so Mockito does not \
         return null for it (the caller calls `.multiply(...)` directly on the result):\n{}",
        caller_test
    );
    assert!(
        caller_test.contains(".thenReturn(new java.math.BigDecimal("),
        "G: the stub must return a non-null BigDecimal value matching the callee's \
         declared return type:\n{}",
        caller_test
    );
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
        &result.filter_dropped_packages,
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
        &result.filter_dropped_packages,
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

#[test]
fn issue_120_procedure_filter_selects_closure_only() {
    // #120: procedure_filter picks pkg.proc targets; same-package helpers and
    // cross-package callees enter the closure transitively; everything else
    // produces no mappings and is reported as excluded.
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let a = tmp.path().join("pkg_a.sql");
    let b = tmp.path().join("pkg_b.sql");
    let c = tmp.path().join("pkg_c.sql");
    fs::write(
        &a,
        "CREATE OR REPLACE PACKAGE BODY pkg_a IS\n\
         PROCEDURE helper IS BEGIN NULL; END;\n\
         PROCEDURE proc_a IS BEGIN pkg_b.proc_b(); helper(); END;\n\
         PROCEDURE proc_a_other IS BEGIN NULL; END;\n\
         END pkg_a;\n/\n",
    )
    .unwrap();
    fs::write(
        &b,
        "CREATE OR REPLACE PACKAGE BODY pkg_b IS\n\
         PROCEDURE proc_b IS BEGIN NULL; END;\n\
         PROCEDURE proc_b_other IS BEGIN NULL; END;\n\
         END pkg_b;\n/\n",
    )
    .unwrap();
    fs::write(
        &c,
        "CREATE OR REPLACE PACKAGE BODY pkg_c IS\n\
         PROCEDURE proc_c IS BEGIN NULL; END;\n\
         END pkg_c;\n/\n",
    )
    .unwrap();

    let out_dir = tmp.path().join("dest");
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(vec![a.to_string_lossy().into(), b.to_string_lossy().into(), c.to_string_lossy().into()]),
        procedure_filter: Some(vec!["pkg_a.proc_a".to_string()]),
        ..Default::default()
    };
    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), true);
    inc.initialize().expect("Failed to initialize incremental state");
    let result = run_pipeline(&[a, b, c], &config, &mut inc, false);

    let names: Vec<(&str, &str, &str)> = result
        .packages
        .iter()
        .flat_map(|p| {
            p.procedures.iter().map(move |pr| (p.package_name.as_str(), pr.proc_name.as_str(), pr.selection.as_str()))
        })
        .collect();
    assert!(names.contains(&("pkg_a", "proc_a", "target")), "{:?}", names);
    assert!(names.contains(&("pkg_a", "helper", "closure")), "{:?}", names);
    assert!(names.contains(&("pkg_b", "proc_b", "closure")), "{:?}", names);
    assert!(!names.iter().any(|(_, n, _)| *n == "proc_a_other"), "{:?}", names);
    assert!(!names.iter().any(|(_, n, _)| *n == "proc_b_other"), "{:?}", names);
    assert!(!names.iter().any(|(p, _, _)| *p == "pkg_c"), "dropped pkg must not generate: {:?}", names);

    // Dropped package survives for reporting via filter_dropped_packages.
    assert_eq!(result.filter_dropped_packages.len(), 1);
    assert_eq!(result.filter_dropped_packages[0].package_name, "pkg_c");
    assert_eq!(result.filter_dropped_packages[0].excluded_procedures.len(), 1);

    let mut report = fluxgauss::report::build_report(
        &result.packages,
        result.skipped,
        result.warnings,
        result.unresolved_calls,
        result.stub_count,
        "regress",
        &out_dir.to_string_lossy(),
        3,
        &result.filter_dropped_packages,
    );
    report.procedure_filter = config.procedure_filter.clone().unwrap_or_default();
    let md = report.to_markdown();
    assert!(md.contains("proc_c"), "excluded proc from dropped pkg must be reported:\n{}", md);
    assert!(report.procedure_filter.iter().any(|f| f.contains("pkg_a.proc_a")));
    // #121: markdown must distinguish target/closure per mapping row
    assert!(md.contains("筛选"), "mapping table must show selection column:\n{}", md);
    assert!(md.contains("🎯 目标") && md.contains("🔗 闭包"), "{}", md);
}

#[test]
fn issue_121_filter_edges_no_cross_layer_union_and_arity() {
    // #121 review: unqualified same-name calls resolve in the caller's package
    // only; qualified calls pick the overload matching the argument count;
    // schema-qualified filter entries match schema-stripped package names.
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let a = tmp.path().join("pkg_a.sql");
    let b = tmp.path().join("pkg_b.sql");
    let e = tmp.path().join("pkg_e.sql");
    let f = tmp.path().join("pkg_f.sql");
    fs::write(
        &a,
        "CREATE OR REPLACE PACKAGE BODY pkg_a IS\n\
         PROCEDURE log IS BEGIN NULL; END;\n\
         PROCEDURE proc_main IS BEGIN log(); END;\n\
         END pkg_a;\n/\n",
    )
    .unwrap();
    fs::write(
        &b,
        "CREATE OR REPLACE PACKAGE BODY pkg_b IS\n\
         PROCEDURE log IS BEGIN NULL; END;\n\
         PROCEDURE proc_b_other IS BEGIN NULL; END;\n\
         END pkg_b;\n/\n",
    )
    .unwrap();
    fs::write(
        &e,
        "CREATE OR REPLACE PACKAGE BODY pkg_e IS\n\
         PROCEDURE proc_ov(p IN NUMBER) IS BEGIN NULL; END;\n\
         PROCEDURE proc_ov(p IN NUMBER, q IN NUMBER) IS BEGIN NULL; END;\n\
         END pkg_e;\n/\n",
    )
    .unwrap();
    fs::write(
        &f,
        "CREATE OR REPLACE PACKAGE BODY pkg_f IS\n\
         PROCEDURE proc_caller IS BEGIN pkg_e.proc_ov(1); END;\n\
         END pkg_f;\n/\n",
    )
    .unwrap();

    let out_dir = tmp.path().join("dest");
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(vec![
            a.to_string_lossy().into(),
            b.to_string_lossy().into(),
            e.to_string_lossy().into(),
            f.to_string_lossy().into(),
        ]),
        procedure_filter: Some(vec!["pkg_a.proc_main".to_string(), "pkg_f.proc_caller".to_string()]),
        ..Default::default()
    };
    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), true);
    inc.initialize().expect("Failed to initialize incremental state");
    let result = run_pipeline(&[a, b, e, f], &config, &mut inc, false);

    let procs_of = |pkg: &str| -> Vec<(String, usize)> {
        result
            .packages
            .iter()
            .find(|p| p.package_name == pkg)
            .map(|p| p.procedures.iter().map(|pr| (pr.proc_name.clone(), pr.parameters.len())).collect())
            .unwrap_or_default()
    };
    // pkg_a.proc_main → closure pkg_a.log only; pkg_b.log must NOT be pulled in
    assert_eq!(procs_of("pkg_a"), vec![("log".to_string(), 0), ("proc_main".to_string(), 0)]);
    assert!(
        procs_of("pkg_b").is_empty(),
        "pkg_b.log must stay excluded: {:?}",
        result.packages.iter().map(|p| p.package_name.clone()).collect::<Vec<_>>()
    );
    assert_eq!(
        result.filter_dropped_packages.iter().map(|p| p.package_name.as_str()).collect::<Vec<_>>(),
        vec!["pkg_b"]
    );
    // qualified call with 1 arg → only the 1-param overload of proc_ov
    let ov = procs_of("pkg_e");
    assert_eq!(ov.len(), 1, "{:?}", ov);
    assert_eq!(ov[0].0, "proc_ov");
    assert_eq!(ov[0].1, 1, "only the 1-arg overload may enter the closure: {:?}", ov);
}

#[test]
fn issue_121_schema_qualified_filter_entry_selects_proc() {
    // #121: filter entry `bigfund.pkg_s.proc_s` selects pkg_s.proc_s even
    // though the schema is stripped from the package name at parse time.
    let tmp = tempfile::tempdir().expect("Failed to create temp dir");
    let s = tmp.path().join("pkg_s.sql");
    fs::write(
        &s,
        "CREATE OR REPLACE PACKAGE BODY BIGFUND.PKG_S IS\n\
         PROCEDURE proc_s IS BEGIN NULL; END;\n\
         END PKG_S;\n/\n",
    )
    .unwrap();
    let out_dir = tmp.path().join("dest");
    let config = AppConfig {
        output_dir: Some(out_dir.to_string_lossy().into()),
        base_package: Some(BASE_PACKAGE.to_string()),
        sources: Some(vec![s.to_string_lossy().into()]),
        procedure_filter: Some(vec!["bigfund.pkg_s.proc_s".to_string()]),
        ..Default::default()
    };
    let mut inc = IncrementalState::new(out_dir.to_string_lossy().into_owned(), true);
    inc.initialize().expect("Failed to initialize incremental state");
    let result = run_pipeline(&[s], &config, &mut inc, false);
    let pkg = result
        .packages
        .iter()
        .find(|p| p.package_name.eq_ignore_ascii_case("pkg_s"))
        .expect("pkg_s must survive filtering");
    assert_eq!(pkg.procedures.len(), 1);
    assert_eq!(pkg.procedures[0].proc_name, "proc_s");
    assert_eq!(pkg.procedures[0].selection, "target");
}

/// Issue #144 Rust-sync: 六个缺陷族的形态断言（对照 Python 批次
/// 047b23c / a9640e5 / ca45793 / 5a628ab 的 acceptance_shape）。
/// 断言生成产物形态，不做 golden 快照。
#[test]
fn issue_144_defect_family_shapes() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let fixtures = manifest_dir.join(FIXTURES_REL);
    let conv = |name: &str| {
        let sql = fixtures.join(name);
        let tmp = tempfile::tempdir().expect("tempdir");
        run_conversion(&sql, &tmp.path().join("dest"))
    };

    // ── batch1a 跨块声明提升（issue_144_crossblock_decl）──
    // OUT-carrier AtomicReference 声明必须在方法体首层，try 内只有赋值，
    // catch 分支引用合法（无重复声明）。
    {
        let gen = conv("issue_144_crossblock_decl.sql");
        let svc = gen.files.get("Service.java").expect("Service.java");
        assert!(
            svc.contains("AtomicReference<String> vRetmsg = new AtomicReference<>(null);"),
            "batch1a: vRetmsg AtomicReference decl must exist:\n{}",
            svc
        );
        assert_eq!(
            svc.matches("AtomicReference<String> vRetmsg =").count(),
            1,
            "batch1a: vRetmsg must be declared exactly once (hoisted, not re-declared in try):\n{}",
            svc
        );
        assert!(svc.contains("} catch (Exception __e1) {"), "batch1a: exception branch must be present:\n{}", svc);
    }

    // ── batch1b 动态 SQL 记录字段（issue_144_mapper_arg_fields）──
    // Rust 侧对应机制：游标 FOR 记录字段经 getOrDefault 提取为运行期字符串，
    // 直接进 concat 模板的 s.field 走 #{s_field} + s.get("field")（见
    // statement.rs flatten_concat_inner）。fixture 断言：记录字段提取存在、
    // mapper 调用实参全部是已声明局部变量、接口参数与实参个数一致。
    {
        let gen = conv("issue_144_mapper_arg_fields.sql");
        let svc = gen.files.get("Service.java").expect("Service.java");
        let mapper_java = gen.files.get("Mapper.java").expect("Mapper.java");
        let mapper_xml = gen.files.get("Mapper.xml").expect("Mapper.xml");
        assert!(
            svc.contains("s.getOrDefault(\"tableName\", s.get(\"table_name\"))"),
            "batch1b: cursor record field must extract via s.get(...):\n{}",
            svc
        );
        assert!(
            svc.contains("for (Map<String, Object> s : sList)"),
            "batch1b: cursor FOR var must be Map<String, Object>:\n{}",
            svc
        );
        let call_line =
            svc.lines().find(|l| l.contains("selectPrcModelLoop_1(")).expect("batch1b: selectPrcModelLoop_1 call");
        for bad in ["diffColumn", "diffValue", "tableName"] {
            assert!(
                !call_line.contains(&format!(" {}", bad)),
                "batch1b: call must not reference undeclared field local `{}`: {}",
                bad,
                call_line
            );
        }
        assert!(
            mapper_java.contains("@Param(\"vTableName\")"),
            "batch1b: mapper interface must declare vTableName param:\n{}",
            mapper_java
        );
        assert!(
            mapper_xml.contains("${vTableName}") && mapper_xml.contains("${vSqlDiff}"),
            "batch1b: dynamic SQL template must use ${{}} identifier placeholders:\n{}",
            mapper_xml
        );
    }

    // ── batch2a SQL 字面量掩码（issue_144_literal_mask）──
    // 'S'/'B' 字面量必须原样保留；不得出现名为 s 的幽灵 mapper 参数。
    {
        let gen = conv("issue_144_literal_mask.sql");
        let mapper_java = gen.files.get("Mapper.java").expect("Mapper.java");
        let mapper_xml = gen.files.get("Mapper.xml").expect("Mapper.xml");
        assert!(
            mapper_xml.contains("'S'") && mapper_xml.contains("'B'"),
            "batch2a: SQL literals must survive param conversion:\n{}",
            mapper_xml
        );
        assert!(
            !mapper_xml.contains("#{s}") && !mapper_xml.contains("${s}"),
            "batch2a: literal 'S' must not become a placeholder:\n{}",
            mapper_xml
        );
        assert!(
            !mapper_java.contains("@Param(\"s\")"),
            "batch2a: no phantom `s` mapper param allowed:\n{}",
            mapper_java
        );
        let sig = mapper_java
            .lines()
            .find(|l| l.contains("selectPrcDecodeLit("))
            .expect("batch2a: selectPrcDecodeLit signature");
        assert_eq!(
            sig.matches("@Param(").count(),
            3,
            "batch2a: selectPrcDecodeLit must have exactly 3 params:\n{}",
            sig
        );
    }

    // ── batch2b 循环记录字段片段过滤 + batch2c 占位符口径（issue_144_chain_loop_field）──
    // j.column_name 拼接只在 j 循环体内；insert mapper 调用实参不得含 j.get(...)；
    // ${vColumns} 标识符占位符与 @Param("vColumns") 一一对应。
    {
        let gen = conv("issue_144_chain_loop_field.sql");
        let svc = gen.files.get("Service.java").expect("Service.java");
        let mapper_java = gen.files.get("Mapper.java").expect("Mapper.java");
        let mapper_xml = gen.files.get("Mapper.xml").expect("Mapper.xml");
        let insert_call = svc
            .lines()
            .find(|l| l.contains("insertPrcMoveCols(") && l.contains("vColumns"))
            .expect("batch2b: insertPrcMoveCols call with vColumns");
        assert!(
            !insert_call.contains("j.get") && !insert_call.contains("j."),
            "batch2b: insert call must not leak loop record j outside its loop:\n{}",
            insert_call
        );
        assert!(
            svc.contains("j.getOrDefault(\"columnName\", j.get(\"column_name\"))"),
            "batch2b: j.column_name concat must stay inside the j loop:\n{}",
            svc
        );
        assert!(
            mapper_xml.contains("${vColumns}"),
            "batch2b: template must keep ${{vColumns}} value placeholder:\n{}",
            mapper_xml
        );
        assert!(
            mapper_java.contains("@Param(\"vColumns\")"),
            "batch2c: ${{vColumns}} placeholder needs matching @Param:\n{}",
            mapper_java
        );
        let insert_sig = mapper_java
            .lines()
            .find(|l| l.contains("insertPrcMoveCols("))
            .expect("batch2c: insertPrcMoveCols signature");
        assert!(
            insert_sig.contains("@Param(\"vTargetTable\")") && insert_sig.contains("@Param(\"vColumns\")"),
            "batch2c: ${{}} identifier placeholders must have @Param counterparts:\n{}",
            insert_sig
        );
    }

    // ── batch3 命名游标 FOR 循环变量注册 Map（issue_144_cursor_for_fields）──
    // r_date.field 必须生成 rDate.get("field") 取值，无 TODO null 退化。
    {
        let gen = conv("issue_144_cursor_for_fields.sql");
        let svc = gen.files.get("Service.java").expect("Service.java");
        assert!(
            svc.contains("for (Map<String, Object> rDate : rDateList)"),
            "batch3: cursor FOR var must be declared Map<String, Object>:\n{}",
            svc
        );
        assert!(
            svc.contains("rDate.get(\"withdrawal_date\")"),
            "batch3: r_date.withdrawal_date must lower to rDate.get(...):\n{}",
            svc
        );
        assert!(
            svc.contains("rDate.get(\"cap_date\")"),
            "batch3: r_date.cap_date must lower to rDate.get(...):\n{}",
            svc
        );
        assert!(
            !svc.contains("TODO: withdrawalDate()") && !svc.contains("TODO: capDate()"),
            "batch3: record field access must not degrade to TODO null:\n{}",
            svc
        );
    }

    // ── batch4a 数值自适应（issue_144_cmp_literal，Python 126d40a 对齐）──
    // 记录字段（名称启发式判数值）vs 字符串字面量/变量必须生成数值比较；
    // 禁止 Objects.equals(mapField, "...") 形态（Long vs String 恒 false，
    // 且前导零业务编码下与 Oracle NUMBER 比较语义分歧）。
    {
        let gen = conv("issue_144_cmp_literal.sql");
        let svc = gen.files.get("Service.java").expect("Service.java");
        assert!(
            svc.contains(".longValue() == 4801040001L"),
            "batch4a: operation_no = '4801040001' must be numeric long comparison:\n{}",
            svc
        );
        assert!(
            svc.contains(".longValue() != 888L"),
            "batch4a: operation_no <> '888' must be numeric long comparison:\n{}",
            svc
        );
        assert!(
            svc.contains("777L ==") && svc.contains(".longValue()"),
            "batch4a: '777' = operation_no must put numeric literal on the left:\n{}",
            svc
        );
        assert!(
            svc.contains(".intValue() == 42"),
            "batch4a: batch_cnt = '42' must be numeric int comparison:\n{}",
            svc
        );
        assert!(
            svc.contains("Long.parseLong(vSuccResult) ==") && svc.contains("Integer.parseInt(vSuccResult) !="),
            "batch4a: String var vs numeric record field must wrap Xxx.parseXxx:\n{}",
            svc
        );
        assert!(
            !svc.contains("Objects.equals(rInfo.getOrDefault(\"operationNo\"")
                && !svc.contains("Objects.equals(rInfo.getOrDefault(\"batchCnt\""),
            "batch4a: no Objects.equals string-compare on numeric record fields:\n{}",
            svc
        );
        assert!(
            !svc.contains("== \"4801040001\"") && !svc.contains("== \"888\"") && !svc.contains("== \"42\""),
            "batch4a: string literals must not remain as == operands:\n{}",
            svc
        );
    }
}

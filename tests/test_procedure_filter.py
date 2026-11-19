"""
Tests for procedure-level filtering and dependency closure (issue #120).

Covers:
- _parse_procedure_filter_entries: `pkg.proc`, `pkg.proc:N`, bare names
- _expand_source_patterns: files / directories / glob patterns
- _iter_routine_call_nodes: ProcedureCall + FunctionCall extraction
- _apply_procedure_filter: target/closure/excluded selection semantics
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import converter.flux_gauss as fg


def _param(name="p1"):
    return fg.Parameter(name=name, java_type="Long", sql_type="bigint", mode="IN")


def _proc(pkg_name, proc_name, body=None, argc=0, source_file="PKG_A.sql", is_function=False):
    params = [_param(f"p{i}") for i in range(argc)]
    full = f"{pkg_name}.{proc_name}" if pkg_name else proc_name
    return fg.ProcedureInfo(
        name=full,
        package=pkg_name,
        proc_name=proc_name,
        is_function=is_function,
        return_type="Long" if is_function else None,
        parameters=params,
        body=body or {},
        sql_text="",
        source_file=source_file,
    )


def _pkg(name, procs, source_file="PKG_A.sql"):
    return fg.PackageInfo(package_name=name, procedures=procs, source_file=source_file)


def _call_body(*calls):
    """calls: list of (name_parts, argc)."""
    stmts = []
    for parts, argc in calls:
        stmts.append({"ProcedureCall": {
            "name": parts,
            "arguments": [{"Literal": {"Integer": i}} for i in range(argc)],
        }})
    return {"statements": stmts}


class TestParseProcedureFilterEntries:
    def test_pkg_proc(self):
        assert fg._parse_procedure_filter_entries(["pkg_a.proc_x"]) == [("pkg_a.proc_x", None)]

    def test_arg_count_suffix(self):
        assert fg._parse_procedure_filter_entries(["pkg_a.proc_x:2"]) == [("pkg_a.proc_x", 2)]

    def test_bare_name(self):
        assert fg._parse_procedure_filter_entries(["proc_x"]) == [("proc_x", None)]

    def test_schema_qualified(self):
        assert fg._parse_procedure_filter_entries(["bigfund.pkg_a.proc_x:1"]) == [
            ("bigfund.pkg_a.proc_x", 1)]

    def test_empty_and_nonstring(self):
        assert fg._parse_procedure_filter_entries(["", None, 42, "  "]) == []


class TestExpandSourcePatterns:
    def test_file_passthrough(self, tmp_path):
        f = tmp_path / "a.sql"
        f.write_text("select 1")
        assert fg._expand_source_patterns([str(f)]) == [str(f)]

    def test_directory_rglob(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.sql").write_text("x")
        (tmp_path / "sub" / "b.sql").write_text("x")
        (tmp_path / "c.txt").write_text("x")
        out = fg._expand_source_patterns([str(tmp_path)])
        assert out == sorted([str(tmp_path / "a.sql"), str(tmp_path / "sub" / "b.sql")])

    def test_glob_pattern(self, tmp_path):
        (tmp_path / "x1.sql").write_text("x")
        (tmp_path / "x2.sql").write_text("x")
        (tmp_path / "y.txt").write_text("x")
        out = fg._expand_source_patterns([str(tmp_path / "*.sql")])
        assert out == sorted([str(tmp_path / "x1.sql"), str(tmp_path / "x2.sql")])

    def test_dedupe(self, tmp_path):
        f = tmp_path / "a.sql"
        f.write_text("x")
        assert fg._expand_source_patterns([str(f), str(f), str(tmp_path)]) == [
            str(f)]


class TestIterRoutineCallNodes:
    def test_procedure_call_top_level(self):
        body = _call_body((["pkg_b", "proc_b1"], 2))
        nodes = list(fg._iter_routine_call_nodes(body))
        assert nodes == [(["pkg_b", "proc_b1"], 2)]

    def test_function_call_nested_in_expr(self):
        body = {"statements": [{"If": {
            "condition": {"FunctionCall": {"name": ["pkg_b", "fnc_x"], "args": [{"Literal": {"Integer": 1}}]}},
            "then": [],
        }}]}
        nodes = list(fg._iter_routine_call_nodes(body))
        assert nodes == [(["pkg_b", "fnc_x"], 1)]

    def test_nested_call_inside_args(self):
        body = {"statements": [{"ProcedureCall": {
            "name": ["pkg_b", "outer"],
            "arguments": [{"FunctionCall": {"name": ["pkg_c", "inner"], "args": []}}],
        }}]}
        nodes = list(fg._iter_routine_call_nodes(body))
        assert (["pkg_b", "outer"], 1) in nodes
        assert (["pkg_c", "inner"], 0) in nodes

    def test_ignores_non_call_nodes(self):
        body = {"statements": [{"Assignment": {"target": {}, "value": {"Literal": {"Integer": 1}}}}]}
        assert list(fg._iter_routine_call_nodes(body)) == []


class TestApplyProcedureFilter:
    def _build(self):
        """pkg_a: a1(target), a2(calls pkg_b.b1 + sibling a3), a3
        pkg_b: b1(calls unqualified fnc_x in pkg_c), b2(uncalled)
        pkg_c: fnc_x standalone function"""
        a3 = _proc("pkg_a", "proc_a3", argc=0, source_file="PKG_A.sql")
        a2 = _proc("pkg_a", "proc_a2", body=_call_body(
            (["pkg_b", "proc_b1"], 1),
            (["proc_a3"], 0),
        ), argc=0, source_file="PKG_A.sql")
        a1 = _proc("pkg_a", "proc_a1", argc=0, source_file="PKG_A.sql")
        pkg_a = _pkg("pkg_a", [a1, a2, a3], "PKG_A.sql")

        fnc = _proc("pkg_c", "fnc_x", body={}, argc=1, source_file="FNC_X.sql",
                    is_function=True)
        pkg_c = _pkg("pkg_c", [fnc], "FNC_X.sql")

        b1 = _proc("pkg_b", "proc_b1", body={"statements": [{"FunctionCall": {
            "name": ["fnc_x"], "args": [{"Literal": {"Integer": 1}}]}}]},
            argc=1, source_file="PKG_B.sql")
        b2 = _proc("pkg_b", "proc_b2", argc=0, source_file="PKG_B.sql")
        pkg_b = _pkg("pkg_b", [b1, b2], "PKG_B.sql")

        # pkg_d: entirely unselected → dropped from packages
        d1 = _proc("pkg_d", "proc_d1", argc=0, source_file="PKG_D.sql")
        pkg_d = _pkg("pkg_d", [d1], "PKG_D.sql")

        packages = [pkg_a, pkg_b, pkg_c, pkg_d]
        all_packages = {p.package_name: p for p in packages}
        return packages, all_packages, (a1, a2, a3, b1, b2, fnc, d1)

    def test_closure_includes_direct_and_indirect_deps(self):
        packages, all_packages, (a1, a2, a3, b1, b2, fnc, d1) = self._build()
        stats = fg._apply_procedure_filter(
            packages, all_packages, [("pkg_a.proc_a1", None)])

        # a1 has no calls → only a1 selected; a2/a3/b1/b2/fnc/d1 excluded
        assert [p.proc_name for p in packages[0].procedures] == ["proc_a1"]
        assert a1._selection == "target"
        assert stats["targets"] == 1
        assert stats["excluded"] == 6

    def test_transitive_closure(self):
        packages, all_packages, (a1, a2, a3, b1, b2, fnc, d1) = self._build()
        # select a2 → pulls pkg_b.b1 (qualified) + a3 (unqualified sibling)
        # → b1 pulls fnc_x (unqualified global function)
        fg._apply_procedure_filter(packages, all_packages, [("pkg_a.proc_a2", None)])

        by_pkg = {p.package_name: [pr.proc_name for pr in p.procedures] for p in packages}
        assert by_pkg["pkg_a"] == ["proc_a2", "proc_a3"]
        assert by_pkg["pkg_b"] == ["proc_b1"]
        assert by_pkg["pkg_c"] == ["fnc_x"]
        assert "pkg_d" not in by_pkg  # fully excluded package dropped

        selections = {pr.proc_name: pr._selection for pr in packages[0].procedures}
        assert selections["proc_a2"] == "target"
        assert selections["proc_a3"] == "closure"
        b1_proc = packages[1].procedures[0]
        assert b1_proc._selection == "closure"

    def test_excluded_moved_to_excluded_procedures(self):
        packages, all_packages, _ = self._build()
        fg._apply_procedure_filter(packages, all_packages, [("pkg_a.proc_a1", None)])
        pkg_a = packages[0]
        assert sorted(p.proc_name for p in pkg_a.excluded_procedures) == ["proc_a2", "proc_a3"]
        # pkg_b/pkg_c/pkg_d had no selected procs → dropped from packages but
        # their PackageInfo objects remain in all_packages with procs moved
        # to excluded_procedures
        assert sorted(p.proc_name for p in all_packages["pkg_b"].excluded_procedures) == ["proc_b1", "proc_b2"]

    def test_arg_count_disambiguation(self):
        ov1 = _proc("pkg_e", "proc_ov", argc=1, source_file="PKG_E.sql")
        ov2 = _proc("pkg_e", "proc_ov", argc=2, source_file="PKG_E.sql")
        pkg_e = _pkg("pkg_e", [ov1, ov2], "PKG_E.sql")
        packages = [pkg_e]
        all_packages = {"pkg_e": pkg_e}
        fg._apply_procedure_filter(packages, all_packages, [("pkg_e.proc_ov", 2)])
        assert len(packages[0].procedures) == 1
        assert len(packages[0].procedures[0].parameters) == 2
        assert len(packages[0].excluded_procedures) == 1

    def test_unmatched_entry_reported(self):
        packages, all_packages, _ = self._build()
        stats = fg._apply_procedure_filter(
            packages, all_packages, [("pkg_a.nonexistent", None)])
        assert stats["unmatched"] == ["pkg_a.nonexistent"]
        assert stats["targets"] == 0

    def test_bare_name_seed(self):
        packages, all_packages, _ = self._build()
        fg._apply_procedure_filter(packages, all_packages, [("proc_b2", None)])
        pkg_b = next(p for p in packages if p.package_name == "pkg_b")
        assert [p.proc_name for p in pkg_b.procedures] == ["proc_b2"]

    def test_unqualified_same_name_stays_in_caller_package(self):
        """#121: pkg_a and pkg_b both define `log`; an unqualified `log` call in
        pkg_a must NOT drag pkg_b.log into the closure."""
        a_log = _proc("pkg_a", "log", argc=0, source_file="PKG_A.sql")
        a_main = _proc("pkg_a", "proc_main", body=_call_body((["log"], 0)),
                       argc=0, source_file="PKG_A.sql")
        pkg_a = _pkg("pkg_a", [a_main, a_log], "PKG_A.sql")
        b_log = _proc("pkg_b", "log", argc=0, source_file="PKG_B.sql")
        b_other = _proc("pkg_b", "proc_other", argc=0, source_file="PKG_B.sql")
        pkg_b = _pkg("pkg_b", [b_log, b_other], "PKG_B.sql")
        packages = [pkg_a, pkg_b]
        all_packages = {p.package_name: p for p in packages}

        fg._apply_procedure_filter(packages, all_packages, [("pkg_a.proc_main", None)])

        assert sorted(p.proc_name for p in packages[0].procedures) == ["log", "proc_main"]
        # pkg_b had no selected procs → dropped; its procs all excluded
        assert "pkg_b" not in [p.package_name for p in packages]
        assert sorted(p.proc_name for p in all_packages["pkg_b"].excluded_procedures) == ["log", "proc_other"]

    def test_qualified_call_picks_matching_arity_overload(self):
        """#121: pkg_e.proc_ov has 1-arg and 2-arg overloads; a call with one
        argument pulls only the 1-arg overload."""
        ov1 = _proc("pkg_e", "proc_ov", argc=1, source_file="PKG_E.sql")
        ov2 = _proc("pkg_e", "proc_ov", argc=2, source_file="PKG_E.sql")
        caller = _proc("pkg_f", "proc_caller",
                       body={"statements": [{"ProcedureCall": {
                           "name": ["pkg_e", "proc_ov"],
                           "arguments": [{"Literal": {"Integer": 1}}]}}]},
                       argc=0, source_file="PKG_F.sql")
        pkg_e = _pkg("pkg_e", [ov1, ov2], "PKG_E.sql")
        pkg_f = _pkg("pkg_f", [caller], "PKG_F.sql")
        packages = [pkg_e, pkg_f]
        all_packages = {p.package_name: p for p in packages}

        fg._apply_procedure_filter(packages, all_packages, [("pkg_f.proc_caller", None)])

        kept = packages[0].procedures
        assert len(kept) == 1 and len(kept[0].parameters) == 1
        assert len(packages[0].excluded_procedures) == 1

    def test_schema_qualified_filter_entry_matches(self):
        """#121: `bigfund.pkg_a.proc_x` must select the same routine as
        `pkg_a.proc_x` when the parser stripped the schema from the package."""
        proc = _proc("pkg_a", "proc_x", argc=0, source_file="PKG_A.sql")
        pkg_a = _pkg("pkg_a", [proc], "PKG_A.sql")
        packages = [pkg_a]
        all_packages = {"pkg_a": pkg_a}

        stats = fg._apply_procedure_filter(
            packages, all_packages, [("bigfund.pkg_a.proc_x", None)])
        assert stats["unmatched"] == []
        assert stats["targets"] == 1
        assert proc._selection == "target"

        # and the plain spelling selects the same routine
        proc2 = _proc("pkg_a", "proc_y", argc=0, source_file="PKG_A.sql")
        pkg_a2 = _pkg("pkg_a", [proc, proc2], "PKG_A.sql")
        packages2 = [pkg_a2]
        all_packages2 = {"pkg_a": pkg_a2}
        stats2 = fg._apply_procedure_filter(
            packages2, all_packages2, [("bigfund.pkg_a.proc_y", None)])
        assert stats2["unmatched"] == []
        assert proc2._selection == "target"

    def test_self_recursion_stays_selected(self):
        rec = _proc("pkg_r", "proc_rec", body=_call_body((["proc_rec"], 0)), argc=0,
                    source_file="PKG_R.sql")
        pkg_r = _pkg("pkg_r", [rec], "PKG_R.sql")
        packages = [pkg_r]
        all_packages = {"pkg_r": pkg_r}
        fg._apply_procedure_filter(packages, all_packages, [("pkg_r.proc_rec", None)])
        assert len(packages[0].procedures) == 1
        assert packages[0].procedures[0]._selection == "target"


class TestResolveConfigFileSets:
    def test_sources_dirs_and_ddl(self, tmp_path):
        routines = tmp_path / "routines"
        routines.mkdir()
        (routines / "p.sql").write_text("x")
        ddls = tmp_path / "ddl"
        ddls.mkdir()
        (ddls / "t.sql").write_text("create table t (i int)")
        cfg = {
            "sources": [str(routines)],
            "ddl_sources": [str(ddls / "*.sql")],
        }
        routine, ddl = fg._resolve_config_file_sets(cfg)
        assert routine == [str(routines / "p.sql")]
        assert ddl == [str(ddls / "t.sql")]
        assert str(ddls / "t.sql") not in routine

    def test_source_dirs_alias(self, tmp_path):
        d = tmp_path / "pkg"
        d.mkdir()
        (d / "a.sql").write_text("x")
        cfg = {"source_dirs": [str(d)]}
        routine, ddl = fg._resolve_config_file_sets(cfg)
        assert routine == [str(d / "a.sql")]
        assert ddl == []

def _ogsql_available() -> bool:
    return bool(fg.OGSQL_BIN) and (os.path.isfile(fg.OGSQL_BIN) or __import__("shutil").which(fg.OGSQL_BIN))


@pytest.mark.skipif(not _ogsql_available(), reason="ogsql binary not available")
class TestIncrementalFilter:
    """#121: with procedure_filter active, a caller change that pulls a new
    package into the closure must regenerate that package's Service on the
    next incremental run (changed_pkg_names forced to None)."""

    def _write(self, path: Path, text: str):
        path.write_text(text, encoding="utf-8")

    def test_newly_closed_package_gets_service_written(self, tmp_path):
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        pkg_a = sql_dir / "pkg_a.sql"
        pkg_c = sql_dir / "pkg_c.sql"
        self._write(pkg_a, """CREATE OR REPLACE PACKAGE BODY pkg_a IS
  PROCEDURE proc_main IS BEGIN NULL; END;
END pkg_a;
/
""")
        self._write(pkg_c, """CREATE OR REPLACE PACKAGE BODY pkg_c IS
  PROCEDURE proc_c IS BEGIN NULL; END;
  PROCEDURE proc_c_other IS BEGIN NULL; END;
END pkg_c;
/
""")
        out_dir = tmp_path / "dest"
        config = tmp_path / "fluxgauss.yaml"
        config.write_text(f"""output_dir: {out_dir}
base_package: com.example.demo
procedure_filter:
  - pkg_a.proc_main
sources:
  - {pkg_a}
  - {pkg_c}
""", encoding="utf-8")

        env = {**os.environ, "OGSQL_BIN": fg.OGSQL_BIN}
        cmd = [sys.executable, str(Path(fg.__file__).resolve()),
               "-c", str(config), "--skip-validate"]
        first = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        assert first.returncode == 0, first.stderr + first.stdout
        # pkg_c had no selected procs → dropped → no CService
        assert not (out_dir / "src/main/java/com/example/demo/service/CService.java").exists()

        # Step 2: caller now invokes pkg_c.proc_c → pkg_c enters the closure.
        # Only pkg_a.sql changed; pkg_c.sql is served from the AST cache.
        self._write(pkg_a, """CREATE OR REPLACE PACKAGE BODY pkg_a IS
  PROCEDURE proc_main IS BEGIN pkg_c.proc_c(); END;
END pkg_a;
/
""")
        second = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        assert second.returncode == 0, second.stderr + second.stdout

        svc_c = out_dir / "src/main/java/com/example/demo/service/CService.java"
        assert svc_c.exists(), "pkg_c entered the closure but its Service was not regenerated"
        content = svc_c.read_text(encoding="utf-8")
        assert "procC(" in content
        assert "procCOther(" not in content


    def test_ddl_sources_hashed_and_rescanned_each_run(self, tmp_path):
        """#121: ddl_sources files join the manifest hash set; a DDL-only change
        invalidates generation, and unchanged DDL is still scanned on
        incremental runs (TYPE_OVERRIDES drives the %TYPE column type)."""
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        pkg_a = sql_dir / "pkg_a.sql"
        ddl = sql_dir / "acct_ddl.sql"
        self._write(pkg_a, """CREATE OR REPLACE PACKAGE BODY pkg_a IS
  PROCEDURE proc_main IS
    v_x acct.status_note%TYPE;
  BEGIN
    v_x := 1;
  END;
END pkg_a;
/
""")
        self._write(ddl, """CREATE TABLE acct (
  id bigint,
  acct_name varchar(20)
);
""")
        out_dir = tmp_path / "dest"
        config = tmp_path / "fluxgauss.yaml"
        config.write_text(f"""output_dir: {out_dir}
base_package: com.example.demo
sources:
  - {pkg_a}
ddl_sources:
  - {ddl}
""", encoding="utf-8")

        env = {**os.environ, "OGSQL_BIN": fg.OGSQL_BIN}
        cmd = [sys.executable, str(Path(fg.__file__).resolve()),
               "-c", str(config), "--skip-validate"]
        first = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        assert first.returncode == 0, first.stderr + first.stdout

        manifest = json.loads((out_dir / ".fluxgauss" / "manifest.json").read_text())
        assert str(ddl) in manifest.get("ddl_files", {}), manifest.keys()

        svc = out_dir / "src/main/java/com/example/demo/service/AService.java"
        assert "String vX" in svc.read_text(encoding="utf-8")

        # Step 2: change ONLY the DDL file — routine sources untouched, so the
        # AST cache is reused but the new column type must still take effect.
        self._write(ddl, """CREATE TABLE acct (
  id bigint,
  acct_name varchar(20),
  status_note numeric(18,2)
);
""")
        second = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        assert second.returncode == 0, second.stderr + second.stdout

        content = svc.read_text(encoding="utf-8")
        assert "BigDecimal vX" in content, (
            "DDL hash change must invalidate generation and rescan TYPE_OVERRIDES"
        )
        log = (out_dir / ".fluxgauss" / "logs" / "conversion-latest.log").read_text(
            encoding="utf-8", errors="replace")
        assert "Incremental: regenerating" not in log, (
            "DDL hash change must force full regeneration"
        )

        manifest2 = json.loads((out_dir / ".fluxgauss" / "manifest.json").read_text())
        assert manifest2["ddl_files"][str(ddl)] != manifest["ddl_files"][str(ddl)]

"""
Tests for procedure-level filtering and dependency closure (issue #120).

Covers:
- _parse_procedure_filter_entries: `pkg.proc`, `pkg.proc:N`, bare names
- _expand_source_patterns: files / directories / glob patterns
- _iter_routine_call_nodes: ProcedureCall + FunctionCall extraction
- _apply_procedure_filter: target/closure/excluded selection semantics
"""
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
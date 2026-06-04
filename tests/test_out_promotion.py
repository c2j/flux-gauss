"""
Tests for OUT parameter type promotion logic.

Covers:
- _promote_out_local_vars: promotes local vars to AtomicReference when passed as OUT args
- _recurse_stmt_for_out_promotions: recursion into nested statement blocks
- _check_call_out_promotions: detection of OUT arg positions from target proc signatures
- Initial value preservation: promoted vars retain their defaults

Fix coverage:
- Fix A: initial value preservation (L2026 pop)
- Fix B: AtomicReference default wrapping in codegen
- Fix C: recursion key mismatch (IF then_stmts, CASE whens, elsifs)
- Fix D: FunctionCall args/arguments compatibility
"""
import pytest
import converter.flux_gauss as fg


# ── Helpers ──────────────────────────────────────────────────

def _make_out_param(name, java_type, sql_type="varchar"):
    """Create an OUT parameter."""
    return fg.Parameter(name=name, java_type=java_type, sql_type=sql_type, mode="OUT")


def _make_in_param(name, java_type="String", sql_type="varchar"):
    """Create an IN parameter."""
    return fg.Parameter(name=name, java_type=java_type, sql_type=sql_type, mode="IN")


def _make_target_proc(proc_name, parameters, package="pkg_target"):
    """Create a ProcedureInfo representing the callee."""
    return fg.ProcedureInfo(
        name=f"{package}.{proc_name}",
        package=package,
        proc_name=proc_name,
        is_function=False,
        return_type=None,
        parameters=parameters,
        body={"body": []},
        sql_text="BEGIN NULL; END;",
    )


def _make_caller_proc(local_vars=None, local_var_defaults=None, body_stmts=None, package="pkg_caller"):
    """Create a ProcedureInfo representing the caller with local vars and a body."""
    proc = fg.ProcedureInfo(
        name=f"{package}.proc_caller",
        package=package,
        proc_name="proc_caller",
        is_function=False,
        return_type=None,
        parameters=[],
        body={"body": body_stmts or []},
        sql_text="BEGIN NULL; END;",
    )
    if local_vars:
        proc.local_vars = dict(local_vars)
    if local_var_defaults:
        proc.local_var_defaults = dict(local_var_defaults)
    return proc


def _build_all_packages(target_proc, target_pkg="pkg_target"):
    """Build all_packages dict with one target package."""
    pkg_info = fg.PackageInfo(package_name=target_pkg, procedures=[target_proc])
    return {target_pkg: pkg_info}


# ── Test: Basic promotion at top-level ──────────────────────

class TestBasicPromotion:
    """Top-level ProcedureCall should promote local vars in OUT positions."""

    def test_promotes_local_var_in_out_position(self):
        """Local var passed as OUT arg should be promoted to AtomicReference."""
        target = _make_target_proc("do_stuff", [
            _make_in_param("p_in"),
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_result": "String"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [
                        {"Literal": {"String": "hello"}},
                        {"PlVariable": ["v_result"]},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_result"] == "AtomicReference<String>"

    def test_does_not_promote_non_out_position(self):
        """Local var passed as IN arg should NOT be promoted."""
        target = _make_target_proc("do_stuff", [
            _make_in_param("p_in"),
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_input": "String"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [
                        {"PlVariable": ["v_input"]},
                        {"Literal": {"String": "output"}},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_input"] == "String"

    def test_promotes_multiple_out_args(self):
        """Multiple local vars in OUT positions should all be promoted."""
        target = _make_target_proc("multi_out", [
            _make_in_param("p_in"),
            _make_out_param("p_flag", "Long"),
            _make_out_param("p_msg", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_flag": "Long", "v_msg": "String"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_target", "multi_out"],
                    "arguments": [
                        {"Literal": {"String": "x"}},
                        {"PlVariable": ["v_flag"]},
                        {"PlVariable": ["v_msg"]},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_flag"] == "AtomicReference<Long>"
        assert proc.local_vars["v_msg"] == "AtomicReference<String>"


# ── Test: Fix C — Recursion into nested structures ──────────

class TestRecursionIntoIf:
    """IF statement uses then_stmts/else_stmts/elsifs — not then_block/else_block/branches."""

    def test_promotes_in_if_then_stmts(self):
        """ProcedureCall inside IF then_stmts should trigger promotion."""
        target = _make_target_proc("do_stuff", [
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_outer_msg": "String"},
            body_stmts=[
                {"If": {
                    "condition": {"ColumnRef": ["v_date"]},
                    "then_stmts": [
                        {"ProcedureCall": {
                            "name": ["pkg_target", "do_stuff"],
                            "arguments": [{"PlVariable": ["v_outer_msg"]}],
                        }},
                    ],
                    "elsifs": [],
                    "else_stmts": [],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_outer_msg"] == "AtomicReference<String>", \
            "Local var inside IF then_stmts was NOT promoted — recursion key mismatch"

    def test_promotes_in_if_else_stmts(self):
        """ProcedureCall inside IF else_stmts should trigger promotion."""
        target = _make_target_proc("do_stuff", [
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_msg": "String"},
            body_stmts=[
                {"If": {
                    "condition": {"ColumnRef": ["v_flag"]},
                    "then_stmts": [],
                    "elsifs": [],
                    "else_stmts": [
                        {"ProcedureCall": {
                            "name": ["pkg_target", "do_stuff"],
                            "arguments": [{"PlVariable": ["v_msg"]}],
                        }},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_msg"] == "AtomicReference<String>", \
            "Local var inside IF else_stmts was NOT promoted"

    def test_promotes_in_if_elsifs(self):
        """ProcedureCall inside IF elsifs[].stmts should trigger promotion."""
        target = _make_target_proc("do_stuff", [
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_els": "String"},
            body_stmts=[
                {"If": {
                    "condition": {"ColumnRef": ["a"]},
                    "then_stmts": [],
                    "elsifs": [
                        {"condition": {"ColumnRef": ["b"]}, "stmts": [
                            {"ProcedureCall": {
                                "name": ["pkg_target", "do_stuff"],
                                "arguments": [{"PlVariable": ["v_els"]}],
                            }},
                        ]},
                    ],
                    "else_stmts": [],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_els"] == "AtomicReference<String>", \
            "Local var inside ELSIF stmts was NOT promoted"

    def test_promotes_in_nested_if_while(self):
        """IF → WHILE → ProcedureCall: the exact pattern from PKG_2008802001_MGT.sql."""
        target = _make_target_proc("proc_match", [
            _make_in_param("p1", "String"),
            _make_in_param("p2", "String"),
            _make_in_param("p3", "Long"),
            _make_in_param("p4", "Long"),
            _make_in_param("p5", "String"),
            _make_out_param("out_flag", "Long"),
            _make_out_param("out_msg", "String"),
            _make_out_param("out_date", "String"),
        ])
        all_pkgs = _build_all_packages(target, "PKG_2008802001_MGT")

        proc = _make_caller_proc(
            local_vars={
                "v_date": "String",
                "outer_msg": "String",
                "another_outer_msg": "String",
            },
            local_var_defaults={
                "outer_msg": '"2"',
                "another_outer_msg": '"2"',
            },
            package="PKG_2008802001_MGT",
            body_stmts=[
                # First call at top level — promotes another_outer_msg and v_date
                {"ProcedureCall": {
                    "name": ["proc_match"],
                    "arguments": [
                        {"PlVariable": ["in_accnt_id"]},
                        {"PlVariable": ["in_accnt_date"]},
                        {"PlVariable": ["in_seq_no"]},
                        {"PlVariable": ["in_interface_seq"]},
                        {"PlVariable": ["in_user_id"]},
                        {"PlVariable": ["out_flag"]},
                        {"PlVariable": ["another_outer_msg"]},
                        {"PlVariable": ["v_date"]},
                    ],
                }},
                # IF → WHILE → second call — should promote outer_msg
                {"If": {
                    "condition": {"BinaryOp": {"op": "IS NOT NULL", "left": {"PlVariable": ["v_date"]}, "right": {"Literal": {"Null": True}}}},
                    "then_stmts": [
                        {"While": {
                            "condition": {"BinaryOp": {"op": "IS NOT NULL", "left": {"PlVariable": ["v_date"]}, "right": {"Literal": {"Null": True}}}},
                            "body": [
                                {"ProcedureCall": {
                                    "name": ["proc_match"],
                                    "arguments": [
                                        {"PlVariable": ["in_accnt_id"]},
                                        {"PlVariable": ["in_accnt_date"]},
                                        {"PlVariable": ["in_seq_no"]},
                                        {"PlVariable": ["in_interface_seq"]},
                                        {"PlVariable": ["in_user_id"]},
                                        {"PlVariable": ["out_flag"]},
                                        {"PlVariable": ["outer_msg"]},
                                        {"PlVariable": ["v_date"]},
                                    ],
                                }},
                            ],
                        }},
                    ],
                    "elsifs": [],
                    "else_stmts": [],
                }},
            ],
        )

        fg._promote_out_local_vars(proc, all_pkgs)

        # All three should be promoted
        assert proc.local_vars["v_date"] == "AtomicReference<String>", \
            "v_date was NOT promoted"
        assert proc.local_vars["another_outer_msg"] == "AtomicReference<String>", \
            "another_outer_msg was NOT promoted"
        assert proc.local_vars["outer_msg"] == "AtomicReference<String>", \
            "outer_msg inside IF→WHILE was NOT promoted — this is the Fix C bug"


class TestRecursionIntoCase:
    """CASE statement uses whens[].stmts and else_stmts — not branches[].body."""

    def test_promotes_in_case_when(self):
        """ProcedureCall inside CASE WHEN should trigger promotion."""
        target = _make_target_proc("do_stuff", [
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_case_var": "String"},
            body_stmts=[
                {"Case": {
                    "expression": {"PlVariable": ["v_code"]},
                    "whens": [
                        {"condition": {"Literal": {"String": "A"}}, "stmts": [
                            {"ProcedureCall": {
                                "name": ["pkg_target", "do_stuff"],
                                "arguments": [{"PlVariable": ["v_case_var"]}],
                            }},
                        ]},
                    ],
                    "else_stmts": [],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_case_var"] == "AtomicReference<String>", \
            "Local var inside CASE WHEN stmts was NOT promoted"

    def test_promotes_in_case_else(self):
        """ProcedureCall inside CASE ELSE should trigger promotion."""
        target = _make_target_proc("do_stuff", [
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_else_var": "String"},
            body_stmts=[
                {"Case": {
                    "expression": {"PlVariable": ["v_code"]},
                    "whens": [],
                    "else_stmts": [
                        {"ProcedureCall": {
                            "name": ["pkg_target", "do_stuff"],
                            "arguments": [{"PlVariable": ["v_else_var"]}],
                        }},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_else_var"] == "AtomicReference<String>", \
            "Local var inside CASE ELSE was NOT promoted"


class TestRecursionIntoWhileForLoop:
    """While/For/Loop all use 'body' key — already covered by recursion."""

    def test_promotes_in_while_body(self):
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_w": "String"},
            body_stmts=[
                {"While": {
                    "condition": {"ColumnRef": ["flag"]},
                    "body": [
                        {"ProcedureCall": {
                            "name": ["pkg_target", "do_stuff"],
                            "arguments": [{"PlVariable": ["v_w"]}],
                        }},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_w"] == "AtomicReference<String>"

    def test_promotes_in_for_body(self):
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "Long")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_f": "Long"},
            body_stmts=[
                {"For": {
                    "variable": "i",
                    "kind": {"Range": {"low": {"Literal": {"Integer": 1}}, "high": {"Literal": {"Integer": 10}}, "reverse": False}},
                    "body": [
                        {"ProcedureCall": {
                            "name": ["pkg_target", "do_stuff"],
                            "arguments": [{"PlVariable": ["v_f"]}],
                        }},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_f"] == "AtomicReference<Long>"

    def test_promotes_in_loop_body(self):
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_l": "String"},
            body_stmts=[
                {"Loop": {
                    "body": [
                        {"ProcedureCall": {
                            "name": ["pkg_target", "do_stuff"],
                            "arguments": [{"PlVariable": ["v_l"]}],
                        }},
                    ],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_l"] == "AtomicReference<String>"


class TestRecursionIntoExceptionBlock:
    """Exception handlers use 'statements' key — should be covered by generic scan."""

    def test_promotes_in_exception_handler(self):
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_err": "String"},
            body_stmts=[
                {"SomeStatement": {"data": "value"}},
            ],
        )
        # Simulate exception block at body level
        proc.body = {
            "body": [],
            "exception_block": {
                "handlers": [
                    {
                        "conditions": ["OTHERS"],
                        "statements": [
                            {"ProcedureCall": {
                                "name": ["pkg_target", "do_stuff"],
                                "arguments": [{"PlVariable": ["v_err"]}],
                            }},
                        ],
                    },
                ],
            },
        }
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_err"] == "AtomicReference<String>", \
            "Local var inside exception handler was NOT promoted"


# ── Test: Fix A — Initial value preservation ────────────────

class TestInitialValuePreservation:
    """Promoted vars should retain their default values instead of becoming null."""

    def test_string_default_preserved(self):
        """VARCHAR2 := '2' should keep '"2"' after promotion."""
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_msg": "String"},
            local_var_defaults={"v_msg": '"2"'},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [{"PlVariable": ["v_msg"]}],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)

        # After promotion, type changes but default should be preserved
        assert proc.local_vars["v_msg"] == "AtomicReference<String>"
        assert "v_msg" in proc.local_var_defaults, \
            "Initial value was popped during promotion — Fix A bug"
        assert proc.local_var_defaults["v_msg"] == '"2"'

    def test_numeric_default_preserved(self):
        """NUMBER := 0 should keep '0' or '0L' after promotion."""
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "Long")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_cnt": "Long"},
            local_var_defaults={"v_cnt": "0L"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [{"PlVariable": ["v_cnt"]}],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)

        assert proc.local_vars["v_cnt"] == "AtomicReference<Long>"
        assert "v_cnt" in proc.local_var_defaults, \
            "Initial value was popped during promotion"
        assert proc.local_var_defaults["v_cnt"] == "0L"

    def test_no_default_stays_absent(self):
        """Vars without initial values should have no entry in local_var_defaults."""
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_nodate": "String"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [{"PlVariable": ["v_nodate"]}],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_nodate"] == "AtomicReference<String>"
        assert "v_nodate" not in proc.local_var_defaults


# ── Test: Fix D — FunctionCall args/arguments compatibility ─

class TestAssignmentFunctionCallPromotion:
    """Assignment with FunctionCall expression should also promote OUT arg vars."""

    def test_promotes_in_assignment_function_call(self):
        """v_result := pkg_target.get_value(p_in => 'x', p_out => v_result)"""
        target = _make_target_proc("get_value", [
            _make_in_param("p_in", "String"),
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_result": "String"},
            body_stmts=[
                {"Assignment": {
                    "target": {"PlVariable": ["v_result"]},
                    "expression": {"FunctionCall": {
                        "name": ["pkg_target", "get_value"],
                        "args": [  # Note: "args" key, not "arguments"
                            {"Literal": {"String": "x"}},
                            {"PlVariable": ["v_result"]},
                        ],
                    }},
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_result"] == "AtomicReference<String>", \
            "FunctionCall inside Assignment did not promote OUT arg var"

    def test_promotes_with_arguments_key(self):
        """Same test but using 'arguments' key instead of 'args'."""
        target = _make_target_proc("get_value", [
            _make_in_param("p_in", "String"),
            _make_out_param("p_out", "String"),
        ])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_result": "String"},
            body_stmts=[
                {"Assignment": {
                    "target": {"PlVariable": ["v_result"]},
                    "expression": {"FunctionCall": {
                        "name": ["pkg_target", "get_value"],
                        "arguments": [  # Note: "arguments" key
                            {"Literal": {"String": "x"}},
                            {"PlVariable": ["v_result"]},
                        ],
                    }},
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_result"] == "AtomicReference<String>", \
            "FunctionCall with 'arguments' key did not promote OUT arg var"


# ── Test: Perform statement ─────────────────────────────────

class TestPerformPromotion:
    """PERFORM wrapping a FunctionCall/ProcedureCall should promote."""

    def test_promotes_in_perform_procedure_call(self):
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_p": "String"},
            body_stmts=[
                {"Perform": {"ProcedureCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [{"PlVariable": ["v_p"]}],
                }}},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_p"] == "AtomicReference<String>"

    def test_promotes_in_perform_function_call(self):
        target = _make_target_proc("do_stuff", [_make_out_param("p_out", "String")])
        all_pkgs = _build_all_packages(target)
        proc = _make_caller_proc(
            local_vars={"v_pf": "String"},
            body_stmts=[
                {"Perform": {"FunctionCall": {
                    "name": ["pkg_target", "do_stuff"],
                    "arguments": [{"PlVariable": ["v_pf"]}],
                }}},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_pf"] == "AtomicReference<String>"


# ── Test: Self-call (same package) ─────────────────────────

class TestSelfCallPromotion:
    """Procedure calling another proc in the same package."""

    def test_promotes_self_call_single_part_name(self):
        target = _make_target_proc("helper", [
            _make_out_param("p_out", "String"),
        ], package="pkg_same")
        all_pkgs = _build_all_packages(target, "pkg_same")
        proc = _make_caller_proc(
            local_vars={"v_self": "String"},
            package="pkg_same",
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["helper"],
                    "arguments": [{"PlVariable": ["v_self"]}],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, all_pkgs)
        assert proc.local_vars["v_self"] == "AtomicReference<String>", \
            "Self-call with single-part name did not promote"


# ── Test: Target proc not found — graceful degradation ──────

class TestTargetNotFound:
    """When target proc is not in all_packages, should not crash."""

    def test_no_crash_when_target_missing(self):
        proc = _make_caller_proc(
            local_vars={"v_x": "String"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_nonexistent", "ghost_proc"],
                    "arguments": [{"PlVariable": ["v_x"]}],
                }},
            ],
        )
        # Should not raise
        fg._promote_out_local_vars(proc, {})
        # Should NOT promote since target not found
        assert proc.local_vars["v_x"] == "String"

    def test_no_crash_when_all_packages_none(self):
        proc = _make_caller_proc(
            local_vars={"v_y": "String"},
            body_stmts=[
                {"ProcedureCall": {
                    "name": ["pkg_x", "proc_y"],
                    "arguments": [{"PlVariable": ["v_y"]}],
                }},
            ],
        )
        fg._promote_out_local_vars(proc, None)
        assert proc.local_vars["v_y"] == "String"

"""
Tests for _process_statement and control flow handlers.

Covers: IF/FOR range/WHILE/LOOP, Assignment, RAISE, RETURN.
These functions modify proc.java_logic_lines as a side effect.
"""

import pytest

import converter.flux_gauss as fg


@pytest.fixture
def proc():
    return fg.ProcedureInfo(
        name="pkg_test.proc_a",
        package="pkg_test",
        proc_name="proc_a",
        is_function=False,
        return_type=None,
        parameters=[],
        body={"Block": {"body": {"statements": []}}},
        sql_text="BEGIN NULL; END;",
        local_vars={"v_count": "Integer", "v_name": "String", "v_flag": "Boolean"},
    )


@pytest.fixture
def all_packages():
    return {}


@pytest.fixture
def dml_counter():
    return {}


class TestProcessReturn:
    def test_return_void(self, proc, all_packages, dml_counter):
        stmt = {"Return": {"expr": None}}
        fg._process_return(stmt["Return"], proc, all_packages)
        assert any("return" in line for line in proc.java_logic_lines)

    def test_return_value(self, proc, all_packages, dml_counter):
        stmt = {"Return": {"expr": {"Literal": {"Integer": 42}}}}
        fg._process_return(stmt["Return"], proc, all_packages)
        assert any("return" in line for line in proc.java_logic_lines)


class TestProcessRaise:
    def test_raise_exception(self, proc, all_packages, dml_counter):
        # Real AST: message is a plain string, not an AST node
        raise_data = {"level": "Exception", "message": "Something went wrong"}
        fg._process_raise(raise_data, proc)
        assert any("BusinessException" in line or "throw" in line for line in proc.java_logic_lines)

    def test_raise_with_sqlstate(self, proc):
        raise_data = {"level": "Exception", "sqlstate": "45000", "message": "Custom error"}
        fg._process_raise(raise_data, proc)
        assert any("BusinessException" in line or "throw" in line for line in proc.java_logic_lines)


class TestProcessRaiseApplicationError:
    def test_raise_application_error_emits_business_exception(self, proc, all_packages, dml_counter):
        before = len(fg.UNRESOLVED_CALLS)
        stmt = {
            "ProcedureCall": {
                "name": ["RAISE_APPLICATION_ERROR"],
                "arguments": [
                    {"UnaryOp": {"op": "-", "expr": {"Literal": {"Integer": 20030}}}},
                    {"Literal": {"String": "bad mode"}},
                ],
            }
        }
        fg._process_statement(stmt, proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("throw new BusinessException" in l for l in lines)
        assert any('"bad mode"' in l for l in lines)
        assert len(fg.UNRESOLVED_CALLS) == before


class TestProcessIf:
    def test_simple_if(self, proc, all_packages, dml_counter):
        if_stmt = {
            "If": {
                "condition": {
                    "BinaryOp": {"op": ">", "left": {"ColumnRef": ["v_count"]}, "right": {"Literal": {"Integer": 0}}}
                },
                "then_stmts": [
                    {"Assignment": {"target": {"ColumnRef": ["v_flag"]}, "expr": {"Literal": {"Boolean": True}}}}
                ],
                "elsifs": [],
                "else_stmts": [],
            }
        }
        fg._process_if(if_stmt["If"], proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("if" in l for l in lines)
        assert any("}" in l for l in lines)

    def test_if_else(self, proc, all_packages, dml_counter):
        if_stmt = {
            "If": {
                "condition": {"ColumnRef": ["v_flag"]},
                "then_stmts": [
                    {"Assignment": {"target": {"ColumnRef": ["v_count"]}, "expr": {"Literal": {"Integer": 1}}}}
                ],
                "elsifs": [],
                "else_stmts": [
                    {"Assignment": {"target": {"ColumnRef": ["v_count"]}, "expr": {"Literal": {"Integer": 0}}}}
                ],
            }
        }
        fg._process_if(if_stmt["If"], proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("if" in l for l in lines)
        assert any("else" in l for l in lines)


class TestProcessFor:
    def test_for_range(self, proc, all_packages, dml_counter):
        for_stmt = {
            "For": {
                "variable": "i",
                "kind": {
                    "Range": {
                        "low": {"Literal": {"Integer": 1}},
                        "high": {"Literal": {"Integer": 10}},
                        "reverse": False,
                    }
                },
                "body": [{"Assignment": {"target": {"ColumnRef": ["v_count"]}, "expr": {"ColumnRef": ["i"]}}}],
            }
        }
        fg._process_for(for_stmt["For"], proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("for" in l for l in lines)

    def test_for_range_reverse(self, proc, all_packages, dml_counter):
        for_stmt = {
            "For": {
                "variable": "i",
                "kind": {
                    "Range": {"low": {"Literal": {"Integer": 1}}, "high": {"Literal": {"Integer": 5}}, "reverse": True}
                },
                "body": [],
            }
        }
        fg._process_for(for_stmt["For"], proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("for" in l for l in lines)


class TestProcessWhile:
    def test_simple_while(self, proc, all_packages, dml_counter):
        while_stmt = {
            "While": {
                "condition": {
                    "BinaryOp": {"op": "<", "left": {"ColumnRef": ["v_count"]}, "right": {"Literal": {"Integer": 100}}}
                },
                "body": [
                    {
                        "Assignment": {
                            "target": {"ColumnRef": ["v_count"]},
                            "expr": {
                                "BinaryOp": {
                                    "op": "+",
                                    "left": {"ColumnRef": ["v_count"]},
                                    "right": {"Literal": {"Integer": 1}},
                                }
                            },
                        }
                    }
                ],
            }
        }
        fg._process_while(while_stmt["While"], proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("while" in l for l in lines)


class TestProcessLoop:
    def test_simple_loop_with_exit(self, proc, all_packages, dml_counter):
        loop_stmt = {
            "Loop": {
                "body": [
                    {
                        "Assignment": {
                            "target": {"ColumnRef": ["v_count"]},
                            "expr": {
                                "BinaryOp": {
                                    "op": "+",
                                    "left": {"ColumnRef": ["v_count"]},
                                    "right": {"Literal": {"Integer": 1}},
                                }
                            },
                        }
                    },
                    {
                        "If": {
                            "condition": {
                                "BinaryOp": {
                                    "op": ">=",
                                    "left": {"ColumnRef": ["v_count"]},
                                    "right": {"Literal": {"Integer": 10}},
                                }
                            },
                            "then_stmts": [{"Exit": {"condition": None}}],
                            "elsifs": [],
                            "else_stmts": [],
                        }
                    },
                ],
            }
        }
        fg._process_loop(loop_stmt["Loop"], proc, all_packages, dml_counter)
        lines = proc.java_logic_lines
        assert any("while" in l or "do" in l or "for" in l for l in lines)


class TestProcessAssignment:
    def test_simple_assignment(self, proc, all_packages):
        assign_data = {
            "target": {"ColumnRef": ["v_count"]},
            "expr": {"Literal": {"Integer": 42}},
        }
        fg._process_assignment(assign_data, proc, all_packages)
        lines = proc.java_logic_lines
        assert len(lines) > 0
        assert any("vCount" in l for l in lines)


class TestProcessStatement:
    def test_dispatch_if(self, proc, all_packages, dml_counter):
        stmt = {
            "If": {
                "condition": {"ColumnRef": ["v_flag"]},
                "then_stmts": [],
                "elsifs": [],
                "else_stmts": [],
            }
        }
        fg._process_statement(stmt, proc, all_packages, dml_counter)
        assert len(proc.java_logic_lines) > 0

    def test_dispatch_assignment(self, proc, all_packages, dml_counter):
        stmt = {"Assignment": {"target": {"ColumnRef": ["v_count"]}, "expr": {"Literal": {"Integer": 0}}}}
        fg._process_statement(stmt, proc, all_packages, dml_counter)
        assert len(proc.java_logic_lines) > 0

    def test_dispatch_return(self, proc, all_packages, dml_counter):
        stmt = {"Return": {"expr": None}}
        fg._process_statement(stmt, proc, all_packages, dml_counter)
        assert len(proc.java_logic_lines) > 0

    def test_dispatch_raise(self, proc, all_packages, dml_counter):
        stmt = {"Raise": {"level": "Exception", "message": "err"}}
        fg._process_statement(stmt, proc, all_packages, dml_counter)
        assert len(proc.java_logic_lines) > 0

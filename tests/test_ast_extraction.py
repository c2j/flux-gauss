"""
Tests for AST extraction functions in converter/flux_gauss.py.

These functions convert raw ogsql-parser JSON AST into structured
Python dataclasses. Tests verify correct parsing of AST nodes.
"""

import converter.flux_gauss as fg


class TestExtractComments:
    """Test extract_comments() — AST comments → CommentInfo list."""

    def test_empty_ast(self):
        assert fg.extract_comments({}) == []

    def test_no_comments_key(self):
        assert fg.extract_comments({"statements": []}) == []

    def test_single_line_comment(self):
        ast = {"comments": [{"text": "-- hello", "line": 1, "end_line": 1, "column": 0, "type": "line"}]}
        result = fg.extract_comments(ast)
        assert len(result) == 1
        assert isinstance(result[0], fg.CommentInfo)
        assert result[0].text == "-- hello"
        assert result[0].comment_type == "line"

    def test_multiple_comments(self):
        ast = {
            "comments": [
                {"text": "-- first", "line": 1, "end_line": 1, "column": 0, "type": "line"},
                {"text": "/* second */", "line": 3, "end_line": 5, "column": 0, "type": "block"},
            ]
        }
        result = fg.extract_comments(ast)
        assert len(result) == 2
        assert result[1].comment_type == "block"

    def test_missing_fields_default(self):
        ast = {"comments": [{}]}
        result = fg.extract_comments(ast)
        assert len(result) == 1
        assert result[0].text == ""
        assert result[0].line == 0


class TestMapCommentsToProcedures:
    """Test _map_comments_to_procedures() — assign comments to procedures by line proximity."""

    def test_empty_inputs(self):
        assert fg._map_comments_to_procedures([], []) == []

    def test_no_procedures_returns_all_as_package_level(self):
        comments = [fg.CommentInfo(text="-- pkg comment", line=1, end_line=1, column=0, comment_type="line")]
        result = fg._map_comments_to_procedures(comments, [])
        assert len(result) == 1  # all become package-level

    def test_leading_comment_assigned_to_procedure(self):
        proc = fg.ProcedureInfo(
            name="pkg.p1",
            package="pkg",
            proc_name="p1",
            is_function=False,
            return_type=None,
            parameters=[],
            body={},
            sql_text="",
            source_start_line=5,
            source_end_line=10,
        )
        comment = fg.CommentInfo(text="-- doc for p1", line=3, end_line=3, column=0, comment_type="line")
        result = fg._map_comments_to_procedures([comment], [proc])
        assert len(proc.leading_comments) == 1
        assert proc.leading_comments[0].text == "-- doc for p1"
        assert len(result) == 0  # no package-level comments

    def test_inline_comment_inside_procedure(self):
        proc = fg.ProcedureInfo(
            name="pkg.p1",
            package="pkg",
            proc_name="p1",
            is_function=False,
            return_type=None,
            parameters=[],
            body={},
            sql_text="",
            source_start_line=1,
            source_end_line=10,
        )
        comment = fg.CommentInfo(text="-- inline", line=5, end_line=5, column=0, comment_type="line")
        fg._map_comments_to_procedures([comment], [proc])
        assert len(proc.inline_comments) == 1

    def test_comment_between_procedures_is_leading(self):
        proc1 = fg.ProcedureInfo(
            name="pkg.p1",
            package="pkg",
            proc_name="p1",
            is_function=False,
            return_type=None,
            parameters=[],
            body={},
            sql_text="",
            source_start_line=1,
            source_end_line=5,
        )
        proc2 = fg.ProcedureInfo(
            name="pkg.p2",
            package="pkg",
            proc_name="p2",
            is_function=False,
            return_type=None,
            parameters=[],
            body={},
            sql_text="",
            source_start_line=10,
            source_end_line=15,
        )
        comment = fg.CommentInfo(text="-- between", line=7, end_line=7, column=0, comment_type="line")
        fg._map_comments_to_procedures([comment], [proc1, proc2])
        assert len(proc2.leading_comments) == 1
        assert proc1.leading_comments == []


class TestIsDdlType:
    """Test _is_ddl_type() — identifies DDL statement types."""

    def test_create_table(self):
        assert fg._is_ddl_type("CreateTable") is True

    def test_alter_table(self):
        assert fg._is_ddl_type("AlterTable") is True

    def test_drop_table(self):
        assert fg._is_ddl_type("DropTable") is True

    def test_select_is_not_ddl(self):
        assert fg._is_ddl_type("Select") is False

    def test_insert_is_not_ddl(self):
        assert fg._is_ddl_type("Insert") is False

    def test_create_function_is_ddl_prefix(self):
        assert fg._is_ddl_type("CreateFunction") is True

    def test_create_procedure_is_ddl_prefix(self):
        assert fg._is_ddl_type("CreateProcedure") is True


class TestExtractNonProcedureStatements:
    """Test extract_non_procedure_statements() — identifies DDL/grants/types to skip."""

    def test_empty_ast(self):
        result = fg.extract_non_procedure_statements({"statements": []}, "test.sql")
        assert result == []

    def test_skips_create_table(self):
        ast = {
            "statements": [
                {"CreateTable": {"name": [{"Identifier": {"value": "orders"}}], "location": {"line": 1}}},
            ]
        }
        result = fg.extract_non_procedure_statements(ast, "test.sql")
        assert len(result) == 1
        assert result[0].category == "CREATE TABLE"

    def test_preserves_create_function(self):
        """CreateFunction should NOT be skipped — it's a procedure."""
        ast = {
            "statements": [
                {"CreateFunction": {"name": "test_func"}},
            ]
        }
        result = fg.extract_non_procedure_statements(ast, "test.sql")
        assert len(result) == 0

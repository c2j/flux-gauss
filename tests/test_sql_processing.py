"""
Tests for SQL processing functions in converter/flux_gauss.py.
"""

import converter.flux_gauss as fg


class TestSplitSqlStatements:
    """Test _split_sql_statements() — splits SQL text into statements."""

    def test_single_statement(self):
        sql = "CREATE OR REPLACE FUNCTION foo() RETURNS void $$ BEGIN NULL; END; $$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1
        assert stmts[0][1] == 1  # start line

    def test_multiple_statements(self):
        sql = (
            "CREATE OR REPLACE FUNCTION foo() RETURNS void $$\n"
            "BEGIN NULL; END;\n"
            "$$ LANGUAGE PLPGSQL;\n"
            "CREATE OR REPLACE FUNCTION bar() RETURNS void $$\n"
            "BEGIN NULL; END;\n"
            "$$ LANGUAGE PLPGSQL;"
        )
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 2

    def test_empty_input(self):
        assert fg._split_sql_statements("") == []

    def test_whitespace_only(self):
        assert fg._split_sql_statements("   \n  \n  ") == []

    def test_preserves_content(self):
        sql = "CREATE OR REPLACE PROCEDURE pkg_test.do_something(p_id IN BIGINT)\n$$\nBEGIN\n  INSERT INTO t VALUES(p_id);\nEND;\n$$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1
        assert "do_something" in stmts[0][0]

    def test_dollar_quote_with_tag(self):
        sql = "CREATE FUNCTION foo() RETURNS void $body$ BEGIN NULL; END; $body$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1

    def test_nested_dollar_quotes(self):
        sql = "CREATE FUNCTION outer() RETURNS void $$ BEGIN\n  NULL;\nEND; $$ LANGUAGE PLPGSQL;"
        stmts = fg._split_sql_statements(sql)
        assert len(stmts) == 1


class TestExtractCommentsFromText:
    """Test _extract_comments_from_text() — extracts comments with line numbers."""

    def test_single_line_comment(self):
        sql = "-- This is a comment\nSELECT 1;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 1
        assert comments[0]["text"] == "-- This is a comment"
        assert comments[0]["line"] == 1
        assert comments[0]["type"] == "line"

    def test_block_comment(self):
        sql = "/* block comment */\nSELECT 1;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 1
        assert comments[0]["type"] == "block"
        assert comments[0]["line"] == 1

    def test_multiline_block_comment(self):
        sql = "/* line 1\n   line 2\n   line 3 */\nSELECT 1;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 1
        assert comments[0]["line"] == 1
        assert comments[0]["end_line"] == 3

    def test_multiple_comments(self):
        sql = "-- comment 1\nSELECT 1;\n-- comment 2\nSELECT 2;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 2
        assert comments[0]["line"] == 1
        assert comments[1]["line"] == 3

    def test_no_comments(self):
        sql = "SELECT 1; SELECT 2;"
        comments = fg._extract_comments_from_text(sql)
        assert len(comments) == 0

    def test_empty_input(self):
        assert fg._extract_comments_from_text("") == []

    def test_comment_inside_dollar_body(self):
        sql = "CREATE FUNCTION f() $$ BEGIN\n-- inner comment\nNULL; END; $$ LANGUAGE PLPGSQL;"
        comments = fg._extract_comments_from_text(sql)
        assert any(c["text"] == "-- inner comment" for c in comments)


class TestIsParseWarning:
    """Test _is_parse_warning() — identifies non-fatal parse warnings."""

    def test_warning_dict(self):
        assert fg._is_parse_warning({"Warning": "something"}) is True

    def test_reserved_keyword(self):
        assert fg._is_parse_warning({"ReservedKeywordAsIdentifier": {"keyword": "user"}}) is True

    def test_real_error(self):
        assert fg._is_parse_warning({"UnexpectedToken": {"got": ";"}}) is False

    def test_string_input(self):
        assert fg._is_parse_warning("some error") is False

    def test_none_input(self):
        assert fg._is_parse_warning(None) is False


class TestFormatValidateError:
    """Test _format_validate_error() — formats parse errors for display."""

    def test_unexpected_token(self):
        err = {"UnexpectedToken": {"location": {"line": 5, "column": 10}, "expected": "';'", "got": "'END'"}}
        result = fg._format_validate_error(err)
        assert "line 5" in result
        assert "col 10" in result

    def test_simple_error_string(self):
        err = {"SomeError": "plain message"}
        result = fg._format_validate_error(err)
        assert "SomeError" in result

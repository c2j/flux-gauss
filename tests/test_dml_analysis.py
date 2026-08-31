"""
Tests for DML analysis functions in converter/flux_gauss.py.

These functions extract table names and targets from DML AST nodes.
AST format: table names are plain string lists like ["orders"] or ["public", "orders"].
"""

import converter.flux_gauss as fg


class TestExtractTableNames:
    """Test _extract_table_names() — FROM clause table name extraction."""

    def test_simple_table(self):
        from_clause = [{"Table": {"name": ["orders"]}}]
        result = fg._extract_table_names(from_clause)
        assert "orders" in result

    def test_multiple_tables(self):
        from_clause = [
            {"Table": {"name": ["orders"]}},
            {"Table": {"name": ["customers"]}},
        ]
        result = fg._extract_table_names(from_clause)
        assert "orders" in result
        assert "customers" in result

    def test_empty_from(self):
        assert fg._extract_table_names([]) == []

    def test_schema_qualified(self):
        from_clause = [
            {"Table": {"name": ["public", "orders"]}},
        ]
        result = fg._extract_table_names(from_clause)
        assert result == ["orders"]

    def test_empty_name(self):
        from_clause = [{"Table": {"name": []}}]
        result = fg._extract_table_names(from_clause)
        assert result == ["unknown"]


class TestExtractTableNamesFromInsert:
    """Test _extract_table_names_from_insert() — INSERT target table."""

    def test_simple_insert(self):
        insert_data = {"table": ["audit_log"]}
        result = fg._extract_table_names_from_insert(insert_data)
        assert "audit_log" in result

    def test_schema_qualified(self):
        insert_data = {"table": ["public", "audit_log"]}
        result = fg._extract_table_names_from_insert(insert_data)
        assert result == ["audit_log"]

    def test_empty(self):
        result = fg._extract_table_names_from_insert({})
        assert result == ["unknown"]


class TestExtractTableNamesFromUpdate:
    """Test _extract_table_names_from_update() — UPDATE target table."""

    def test_simple_update(self):
        update_data = {"tables": [{"Table": {"name": ["trade_record"]}}]}
        result = fg._extract_table_names_from_update(update_data)
        assert "trade_record" in result

    def test_empty(self):
        result = fg._extract_table_names_from_update({})
        assert result == []


class TestExtractTableNameFromDml:
    """Test _extract_table_name_from_dml() — generic DML table name extraction."""

    def test_insert_with_string_list(self):
        dml = {"table": ["audit_log"]}
        assert fg._extract_table_name_from_dml(dml) == "audit_log"

    def test_insert_schema_qualified(self):
        dml = {"table": ["public", "audit_log"]}
        assert fg._extract_table_name_from_dml(dml) == "audit_log"

    def test_update_with_table_objects(self):
        dml = {"table": ["products"]}
        assert fg._extract_table_name_from_dml(dml) == "products"

    def test_empty_returns_unknown(self):
        assert fg._extract_table_name_from_dml({}) == "unknown"


class TestExtractDmlTarget:
    """Test _extract_dml_target() — extracts target table from DML statement."""

    def test_insert(self):
        data = {"table": ["orders"]}
        assert fg._extract_dml_target(data, "Insert") == "orders"

    def test_update(self):
        data = {"tables": [{"Table": {"name": ["products"]}}]}
        assert fg._extract_dml_target(data, "Update") == "products"

    def test_delete_simple(self):
        data = {"table": ["logs"]}
        assert fg._extract_dml_target(data, "Delete") == "logs"

    def test_select(self):
        data = {"from": [{"Table": {"name": ["customers"]}}]}
        assert fg._extract_dml_target(data, "Select") == "customers"

    def test_non_dict_returns_unknown(self):
        assert fg._extract_dml_target("not a dict", "Insert") == "unknown"

    def test_empty_data_returns_unknown(self):
        assert fg._extract_dml_target({}, "Insert") == "unknown"

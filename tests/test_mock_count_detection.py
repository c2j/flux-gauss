"""Tests for mock-value generation in generated unit tests (M1, #114 review)."""

import converter.flux_gauss as fg


def _mock(method_id, result_type, sql_text=""):
    return fg._mock_select_return(
        "select",
        result_type,
        False,
        "orderMapper",
        method_id,
        "any()",
        dml_sql_text=sql_text,
    )


class TestCountDetectionWordBoundary:
    """A mapper method is a count query only when "count" is a standalone
    camelCase word (countOrders / ordersCount), not a substring (discount /
    account / encounter would force the scalar mock to 0 instead of 999)."""

    def test_discount_account_encounter_not_count(self):
        for mid in ("selectDiscount", "selectAccount", "selectEncounter"):
            out = _mock(mid, "Integer")
            assert "999" in out, f"{mid} must mock 999, got: {out}"

    def test_standalone_count_word_is_count(self):
        for mid in ("selectOrderCount", "selectCountOrders", "selectCountByStatus"):
            out = _mock(mid, "Integer")
            assert "thenReturn(0)" in out, f"{mid} must mock 0, got: {out}"

    def test_counter_counterparty_not_count(self):
        for mid in ("selectCounter", "selectCounterparty"):
            out = _mock(mid, "Integer")
            assert "999" in out, f"{mid} must mock 999, got: {out}"

    def test_long_type_uses_0l_not_999l(self):
        out = _mock("selectOrderCount", "Long")
        assert "thenReturn(0L)" in out, f"count Long must mock 0L, got: {out}"

    def test_count_in_sql_text_still_detected(self):
        out = _mock("selectCheckStock", "Integer", sql_text="select count(*) from t")
        assert "thenReturn(0)" in out, f"count(*) in SQL must mock 0, got: {out}"


class TestDomainTestValueQuotedDefault:
    """M2 (#114 review): a quoted DEFAULT on a non-String param must not emit a
    bare string literal (compile error); it falls back to the type-aware value."""

    def _proc(self):
        return fg.ProcedureInfo(
            name="pkg_test.proc_a", package="pkg_test", proc_name="proc_a",
            is_function=False, return_type=None, parameters=[],
            body={"Block": {"body": {"statements": []}}}, sql_text="BEGIN NULL; END;",
            local_vars={},
        )

    def test_quoted_date_default_uses_date_literal(self):
        proc = self._proc()
        param = fg.Parameter(name="p_start", java_type="java.sql.Date", sql_type="date",
                             default_value="'2024-01-01'")
        out = fg._domain_test_value(proc, param, pkg=None)
        assert "java.sql.Date.valueOf" in out, f"Date default must be type-aware, got: {out}"

    def test_quoted_timestamp_default_uses_timestamp_literal(self):
        proc = self._proc()
        param = fg.Parameter(name="p_ts", java_type="java.sql.Timestamp", sql_type="timestamp",
                             default_value="'2024-01-01 10:00:00'")
        out = fg._domain_test_value(proc, param, pkg=None)
        assert "java.sql.Timestamp.valueOf" in out, f"Timestamp default must be type-aware, got: {out}"

    def test_quoted_string_default_stays_string(self):
        proc = self._proc()
        param = fg.Parameter(name="p_mode", java_type="String", sql_type="varchar",
                             default_value="'REPLACE'")
        out = fg._domain_test_value(proc, param, pkg=None)
        assert out == '"REPLACE"', f"String default keeps its literal, got: {out}"

    def test_quoted_bigdecimal_default_uses_bigdecimal(self):
        proc = self._proc()
        param = fg.Parameter(name="p_amount", java_type="java.math.BigDecimal", sql_type="numeric",
                             default_value="'99.99'")
        out = fg._domain_test_value(proc, param, pkg=None)
        assert "BigDecimal" in out, f"BigDecimal default must be type-aware, got: {out}"


def _make_callee(name, params):
    return fg.ProcedureInfo(
        name=f"pkg_cal.{name}", package="pkg_cal", proc_name=name,
        is_function=False, return_type=None, parameters=params,
        body={"Block": {"body": {"statements": []}}}, sql_text="BEGIN NULL; END;",
        local_vars={},
    )


class TestStripGetForCrossPkgOutArgs:
    """M3 (#114 review): only the OUT-position argument token loses its .get();
    IN positions keep the deref, and ambiguous overloads fail safe."""

    def _proc_with_call(self):
        proc = fg.ProcedureInfo(
            name="pkg_ord.proc_a", package="pkg_ord", proc_name="proc_a",
            is_function=False, return_type=None, parameters=[],
            body={"Block": {"body": {"statements": []}}}, sql_text="BEGIN NULL; END;",
            local_vars={"v_id": "Long"},
        )
        proc.service_calls = [
            fg.ServiceCall(service_name="AcctService", method_name="transfer",
                           args=[], package_name="pkg_cal")
        ]
        return proc

    def test_only_out_position_stripped(self):
        proc = self._proc_with_call()
        callee = _make_callee("transfer", [
            fg.Parameter(name="p_in", java_type="Long", sql_type="bigint", mode="IN"),
            fg.Parameter(name="p_out", java_type="Long", sql_type="bigint", mode="OUT"),
        ])
        all_packages = {"pkg_cal": fg.PackageInfo(
            package_name="pkg_cal", procedures=[callee], table_refs={},
            package_vars={}, source_file="", source_files=[], comments=[],
            java_package="", custom_types={}, _extra_mapper_methods=[],
        )}
        line = "AcctService.transfer(vId.get(), vId.get());"
        out = fg._strip_get_for_cross_pkg_out_args(line, "vId", proc, all_packages)
        assert out == "AcctService.transfer(vId.get(), vId);", f"IN keeps .get(), OUT stripped: {out}"

    def test_nested_expression_out_position_rewritten(self):
        proc = self._proc_with_call()
        callee = _make_callee("transfer", [
            fg.Parameter(name="p_out", java_type="Long", sql_type="bigint", mode="OUT"),
        ])
        all_packages = {"pkg_cal": fg.PackageInfo(
            package_name="pkg_cal", procedures=[callee], table_refs={},
            package_vars={}, source_file="", source_files=[], comments=[],
            java_package="", custom_types={}, _extra_mapper_methods=[],
        )}
        line = "AcctService.transfer(String.valueOf(vId.get()));"
        out = fg._strip_get_for_cross_pkg_out_args(line, "vId", proc, all_packages)
        assert out == "AcctService.transfer(String.valueOf(vId));", f"OUT arg rewritten in place: {out}"

    def test_ambiguous_overload_fails_safe(self):
        proc = self._proc_with_call()
        callee_1 = _make_callee("transfer", [
            fg.Parameter(name="p_a", java_type="Long", sql_type="bigint", mode="IN"),
        ])
        callee_2 = _make_callee("transfer", [
            fg.Parameter(name="p_a", java_type="Long", sql_type="bigint", mode="IN"),
            fg.Parameter(name="p_b", java_type="Long", sql_type="bigint", mode="IN"),
        ])
        all_packages = {"pkg_cal": fg.PackageInfo(
            package_name="pkg_cal", procedures=[callee_1, callee_2], table_refs={},
            package_vars={}, source_file="", source_files=[], comments=[],
            java_package="", custom_types={}, _extra_mapper_methods=[],
        )}
        line = "AcctService.transfer(vId.get());"
        out = fg._strip_get_for_cross_pkg_out_args(line, "vId", proc, all_packages)
        assert out == line, f"ambiguous overload must leave line unchanged: {out}"

    def test_indentation_and_prefix_preserved(self):
        proc = self._proc_with_call()
        callee = _make_callee("transfer", [
            fg.Parameter(name="p_out", java_type="Long", sql_type="bigint", mode="OUT"),
        ])
        all_packages = {"pkg_cal": fg.PackageInfo(
            package_name="pkg_cal", procedures=[callee], table_refs={},
            package_vars={}, source_file="", source_files=[], comments=[],
            java_package="", custom_types={}, _extra_mapper_methods=[],
        )}
        indented = "    AcctService.transfer(vId.get());"
        out = fg._strip_get_for_cross_pkg_out_args(indented, "vId", proc, all_packages)
        assert out == "    AcctService.transfer(vId);", f"indentation must be preserved: {out!r}"
        prefixed = "vResult = AcctService.transfer(vId.get());"
        out2 = fg._strip_get_for_cross_pkg_out_args(prefixed, "vId", proc, all_packages)
        assert out2 == "vResult = AcctService.transfer(vId);", f"prefix must be preserved: {out2!r}"


class TestNumberMinusTimestamp:
    """#114 review minor 1: `number - timestamp` must subtract (the old code
    flipped the sign to `+` when the timestamp was on the right side)."""

    def _proc(self):
        return fg.ProcedureInfo(
            name="pkg_test.proc_a", package="pkg_test", proc_name="proc_a",
            is_function=False, return_type=None, parameters=[],
            body={"Block": {"body": {"statements": []}}}, sql_text="BEGIN NULL; END;",
            local_vars={"v_ts": "java.sql.Timestamp", "v_days": "Long"},
        )

    def test_number_minus_timestamp_subtracts(self):
        proc = self._proc()
        ast = {"BinaryOp": {
            "op": "-",
            "left": {"ColumnRef": ["v_days"]},
            "right": {"ColumnRef": ["v_ts"]},
        }}
        out = fg._expr_to_java(ast, proc)
        assert ".getTime() - " in out, f"number - timestamp must subtract, got: {out}"

    def test_timestamp_minus_number_subtracts(self):
        proc = self._proc()
        ast = {"BinaryOp": {
            "op": "-",
            "left": {"ColumnRef": ["v_ts"]},
            "right": {"ColumnRef": ["v_days"]},
        }}
        out = fg._expr_to_java(ast, proc)
        assert ".getTime() - " in out, f"timestamp - number must subtract, got: {out}"


class TestParamDefaultJavaEscaping:
    """#115 review #6: SQL source text embedded in Java string literals must be
    escaped (SQL `''` → raw quote, backslash/quote escaping for the literal)."""

    def test_sql_doubled_quote_becomes_raw_quote(self):
        param = fg.Parameter(name="p_msg", java_type="String", sql_type="varchar",
                             mode="IN", default_value="'it''s'")
        assert fg._param_default_java(param) == '"it\'s"'

    def test_embedded_double_quote_escaped(self):
        param = fg.Parameter(name="p_msg", java_type="String", sql_type="varchar",
                             mode="IN", default_value="'a\"b'")
        assert fg._param_default_java(param) == '"a\\"b"'

    def test_digit_string_stays_numeric(self):
        param = fg.Parameter(name="p_n", java_type="Integer", sql_type="int",
                             mode="IN", default_value="'5'")
        assert fg._param_default_java(param) == "5"


class TestCollectFkParents:
    """#114 review minor 10: `-- references ...` line comments must not create
    phantom FK edges; quoted identifiers must resolve to their unquoted name."""

    def test_line_comment_references_ignored(self, tmp_path):
        ddl = tmp_path / "ddl.sql"
        ddl.write_text(
            "-- references ghost_table\n"
            "create table child (\n"
            "  id bigint primary key,\n"
            "  parent_id bigint references parent_table(id)\n"
            ");\n",
            encoding="utf-8",
        )
        fg._TABLE_DDL_SOURCE[("child", "parent_id")] = str(ddl)
        try:
            fk = fg._collect_fk_parents()
        finally:
            fg._TABLE_DDL_SOURCE.clear()
        assert "ghost_table" not in fk.get("child", []), f"comment FK must be ignored: {fk}"
        assert "parent_table" in fk.get("child", []), f"real FK must be kept: {fk}"

    def test_quoted_identifier_resolves_unquoted(self, tmp_path):
        ddl = tmp_path / "ddl2.sql"
        ddl.write_text(
            'create table "OrderLine" (\n'
            '  id bigint primary key,\n'
            '  "OrderId" bigint references "Order"(id)\n'
            ");\n",
            encoding="utf-8",
        )
        fg._TABLE_DDL_SOURCE[("orderline", "orderid")] = str(ddl)
        try:
            fk = fg._collect_fk_parents()
        finally:
            fg._TABLE_DDL_SOURCE.clear()
        assert "order" in fk.get("orderline", []), f"quoted FK must resolve unquoted: {fk}"






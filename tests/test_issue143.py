"""Regression tests for PR #143 REQUEST_CHANGES review fixes.

Covers the 7 blocking items:
  1. XmlForest {alias, expr} items + XMLTYPE String mapping (Python)
  2. EXTRACTVALUE evaluates XPath against arg0 via _extractValue helper
  3. Integer->Long coercion: primitive-returning calls (size()) must not
     emit `.longValue()` on a primitive int
  4. FieldAccess preserves generic collection element type; RECORD fields
     keep their real type (no blanket .doubleValue())
  5. _qualify_record_type follows java_packages, not global BASE_PACKAGE
  6. (Rust side covered by crates/fluxgauss tests + goldens)
  7. ScalarSublink -> explicit _unsupportedScalarSublink throw + report TODO
"""
import converter.flux_gauss as fg


def _proc(**kw):
    base = dict(
        name="pkg_test.proc_a", package="pkg_test", proc_name="proc_a",
        is_function=False, return_type=None, parameters=[],
        body={}, sql_text="",
    )
    base.update(kw)
    return fg.ProcedureInfo(**base)


class TestXmlTypeMapping:
    def test_xmltype_maps_to_string(self):
        assert fg.sql_type_to_java("xmltype") == "String"
        assert fg.sql_type_to_java("XMLTYPE") == "String"


class TestXmlForest:
    def test_alias_expr_item_emits_xml_element(self):
        proc = _proc(parameters=[
            fg.Parameter(name="p_value", java_type="String", sql_type="varchar2", mode="IN"),
        ])
        ast = {"XmlForest": [{"alias": "foo", "expr": {"PlVariable": ["p_value"]}}]}
        out = fg._expr_to_java(ast, proc)
        assert '_xmlElement("foo", pValue)' in out
        assert "_xmlConcat(" in out
        assert "{'alias'" not in out and "dict" not in out

    def test_multiple_items_and_literal_expr(self):
        proc = _proc()
        ast = {"XmlForest": [
            {"alias": "foo", "expr": {"Literal": {"String": "abc"}}},
            {"alias": "bar", "expr": {"Literal": {"Integer": "1"}}},
        ]}
        out = fg._expr_to_java(ast, proc)
        assert out.count("_xmlElement(") == 2
        assert '_xmlElement("foo"' in out and '_xmlElement("bar"' in out


class TestExtractValue:
    def test_uses_arg0_xml_and_arg1_xpath(self):
        proc = _proc()
        ast = {"FunctionCall": {
            "name": ["EXTRACTVALUE"],
            "args": [
                {"Literal": {"String": "<foo>abc</foo>"}},
                {"Literal": {"String": "/foo/text()"}},
            ],
        }}
        out = fg._expr_to_java(ast, proc)
        assert out == '_extractValue("<foo>abc</foo>", "/foo/text()")'


class TestIntToLongCoercion:
    def test_primitive_call_not_treated_as_boxed_ident(self):
        # verify16 regression: vColumKey.size().longValue() — int has no
        # longValue(); must wrap in ((Number) ...).
        out = fg._coerce_type("vColumKey.size()", "Integer", "Long")
        assert out == "((Number) (vColumKey.size())).longValue()"
        assert ".size().longValue()" not in out

    def test_indexof_expression_uses_number_wrap(self):
        out = fg._coerce_type('idx.indexOf("x") + 1', "Integer", "Long")
        assert out == '((Number) (idx.indexOf("x") + 1)).longValue()'

    def test_bare_identifier_keeps_longvalue(self):
        out = fg._coerce_type("vLongVar", "Integer", "Long")
        assert out == "vLongVar.longValue()"

    def test_zero_arg_method_call_uses_number_wrap(self):
        out = fg._coerce_type("getCount()", "Integer", "Long")
        assert out == "((Number) (getCount())).longValue()"


class TestFieldAccess:
    def _coll_proc(self):
        return _proc(local_vars={"v_coll": "java.util.List<RecItem>"}, custom_types={
            "rec_item": {"kind": "record", "fields": [
                ("item_name", "String"), ("item_flag", "Boolean"), ("item_id", "Long"),
            ]},
        })

    def test_typed_collection_preserves_element_type(self):
        proc = self._coll_proc()
        ast = {"FieldAccess": {
            "object": {"FunctionCall": {"name": ["v_coll"], "args": [{"Literal": {"Integer": "1"}}]}},
            "field": "item_name",
        }}
        out = fg._expr_to_java(ast, proc)
        # raw ((java.util.List) var) cast would degrade the element to Object
        assert "((java.util.List)" not in out
        assert out == "vColl.get((int)(1) - 1).itemName"

    def test_record_boolean_field_no_doublevalue(self):
        proc = _proc(
            parameters=[fg.Parameter(name="p_rec", java_type="RecParam", sql_type="rec_param", mode="IN")],
            custom_types={"rec_param": {"kind": "record", "fields": [
                ("p_flag", "Boolean"), ("p_id", "Long"), ("p_name", "String"),
            ]}},
        )
        ast = {"FieldAccess": {"object": {"PlVariable": ["p_rec"]}, "field": "p_flag"}}
        out = fg._expr_to_java(ast, proc)
        assert out == "pRec.pFlag"
        assert ".doubleValue()" not in out

    def test_record_numeric_field_keeps_unbox(self):
        proc = _proc(
            parameters=[fg.Parameter(name="p_rec", java_type="RecParam", sql_type="rec_param", mode="IN")],
            custom_types={"rec_param": {"kind": "record", "fields": [
                ("p_id", "Long"),
            ]}},
        )
        ast = {"FieldAccess": {"object": {"PlVariable": ["p_rec"]}, "field": "p_id"}}
        out = fg._expr_to_java(ast, proc)
        assert out == "pRec.pId.doubleValue()"


class TestQualifyRecordType:
    def test_uses_pkg_java_package_not_base(self):
        proc = _proc()
        imports = set()
        out = fg._qualify_record_type("RecParam", proc, imports, "com.acme.custom")
        assert out == "TestService.RecParam"
        assert "import com.acme.custom.service.TestService;" in imports
        assert not any("com.example" in i for i in imports)

    def test_default_falls_back_to_base_package(self):
        proc = _proc()
        imports = set()
        fg._qualify_record_type("RecParam", proc, imports)
        assert any(i.startswith("import com.example.demo.service.TestService;") for i in imports)


class TestScalarSublink:
    def test_emits_explicit_unsupported_throw(self):
        proc = _proc()
        ast = {"ScalarSublink": {"op": "=", "sublink_type": "Any"}}
        out = fg._expr_to_java(ast, proc)
        assert "_unsupportedScalarSublink(" in out
        assert "pkg_test.proc_a" in out
        # must NOT degrade to a legal-but-wrong constant
        assert out.strip() != "false"
        assert "/* TODO" not in out

    def test_records_report_todo(self):
        proc = _proc()
        fg._expr_to_java({"ScalarSublink": {}}, proc)
        assert any(cat == "ScalarSublink" for cat, *_ in fg.TODO_SUMMARY)


class TestJavaPackagesMapperImport:
    """PR #143 review item 5: _qualify_record_type must follow the source's
    java_packages mapping — a RECORD param in a DML must not make the Mapper
    import the global BASE_PACKAGE service (compile failure)."""

    _SQL = """\
CREATE TABLE rec_t (id NUMBER, name VARCHAR2(50));
CREATE OR REPLACE PACKAGE pkg_rec AS
  TYPE rec_param IS RECORD (p_id NUMBER, p_name VARCHAR2(50));
  PROCEDURE prc_rec(p_rec IN rec_param);
END pkg_rec;
/
CREATE OR REPLACE PACKAGE BODY pkg_rec AS
  PROCEDURE prc_rec(p_rec IN rec_param) IS
  BEGIN
    INSERT INTO rec_t (id, name) VALUES (p_rec.p_id, p_rec.p_name);
  END prc_rec;
END pkg_rec;
/
"""

    def test_mapper_import_uses_java_packages(self, tmp_path):
        import os
        import subprocess
        import sys

        sql_path = tmp_path / "rec.sql"
        sql_path.write_text(self._SQL, encoding="utf-8")
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(
            "output_dir: " + str(tmp_path / "dest") + "\n"
            "base_package: com.example.review143\n"
            "sources:\n"
            "  - " + str(sql_path) + "\n"
            "java_packages:\n"
            "  - package: com.acme.custom\n"
            "    sources:\n"
            "      - " + str(sql_path) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, fg.__file__, "-c", str(cfg_path), "--skip-validate", "--full"],
            capture_output=True, text=True,
            env={**os.environ, "OGSQL_BIN": fg.OGSQL_BIN}, timeout=120,
        )
        assert result.returncode == 0, f"conversion failed:\n{result.stdout[-500:]}\n{result.stderr[-500:]}"

        mapper = tmp_path / "dest" / "src/main/java/com/acme/custom/mapper/RecMapper.java"
        assert mapper.exists(), "Mapper.java not generated under com.acme.custom"
        content = mapper.read_text(encoding="utf-8")
        assert "import com.acme.custom.service.RecService;" in content
        assert "com.example.review143.service" not in content

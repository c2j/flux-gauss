"""
Tests for naming/identifier functions in converter/flux_gauss.py.

These pure functions are critical for correct Java code generation —
every class name, method name, and variable name passes through them.
"""
import pytest
import converter.flux_gauss as fg


class TestSnakeToCamel:
    """Test snake_to_camel() — snake_case → camelCase."""

    def test_simple(self):
        assert fg.snake_to_camel("create_order") == "createOrder"

    def test_single_word(self):
        assert fg.snake_to_camel("order") == "order"

    def test_three_words(self):
        assert fg.snake_to_camel("get_product_info") == "getProductInfo"

    def test_uppercase_input(self):
        assert fg.snake_to_camel("PKG_ORDER") == "pkgOrder"

    def test_empty_string(self):
        assert fg.snake_to_camel("") == "_"

    def test_already_camel(self):
        # No underscores → returns as-is (lowered)
        assert fg.snake_to_camel("createOrder") == "createorder"


class TestSnakeToPascal:
    """Test snake_to_pascal() — snake_case → PascalCase."""

    def test_simple(self):
        assert fg.snake_to_pascal("pkg_order") == "PkgOrder"

    def test_single_word(self):
        assert fg.snake_to_pascal("order") == "Order"

    def test_three_words(self):
        assert fg.snake_to_pascal("get_product_info") == "GetProductInfo"

    def test_empty_string(self):
        assert fg.snake_to_pascal("") == "_"


class TestPackageToClassname:
    """Test package_to_classname() — SQL package name → Java class name."""

    def test_pkg_prefix(self):
        assert fg.package_to_classname("pkg_order") == "Order"

    def test_pkg_prefix_uppercase(self):
        assert fg.package_to_classname("PKG_ORDER") == "Order"

    def test_pack_prefix(self):
        assert fg.package_to_classname("pack_log") == "Log"

    def test_no_prefix(self):
        assert fg.package_to_classname("order") == "Order"

    def test_complex(self):
        assert fg.package_to_classname("pkg_product") == "Product"


class TestJavaMethodName:
    """Test java_method_name() — SQL proc name → Java method name."""

    def test_simple(self):
        assert fg.java_method_name("create_order") == "createOrder"

    def test_getter(self):
        assert fg.java_method_name("get_product_info") == "getProductInfo"

    def test_batch(self):
        assert fg.java_method_name("batch_create_orders") == "batchCreateOrders"


class TestJavaSafeIdentifier:
    """Test _java_safe_identifier() — sanitize for Java."""

    def test_normal_string(self):
        assert fg._java_safe_identifier("order_id") == "order_id"

    def test_starts_with_digit(self):
        assert fg._java_safe_identifier("123abc") == "_123abc"

    def test_java_keyword(self):
        assert fg._java_safe_identifier("return") == "_return"

    def test_java_keyword_case_insensitive(self):
        assert fg._java_safe_identifier("Return") == "_Return"

    def test_empty_string(self):
        assert fg._java_safe_identifier("") == "_"

    def test_special_chars_stripped(self):
        assert fg._java_safe_identifier("name$#") == "name"

    def test_underscore_only(self):
        assert fg._java_safe_identifier("_") == "_unnamed"

    def test_non_ascii_stripped(self):
        assert fg._java_safe_identifier("名称") == "_unnamed"

    def test_plpgsql_keyword_old(self):
        assert fg._java_safe_identifier("old") == "_old"

    def test_plpgsql_keyword_new(self):
        assert fg._java_safe_identifier("new") == "_new"

    def test_plpgsql_keyword_raise(self):
        assert fg._java_safe_identifier("raise") == "_raise"


class TestCustomTypeClassname:
    """Test _custom_type_classname() — SQL type name → Java class name."""

    def test_t_prefix(self):
        assert fg._custom_type_classname("t_coord_rec") == "CoordRec"

    def test_type_prefix(self):
        assert fg._custom_type_classname("type_order_item") == "OrderItem"

    def test_no_prefix(self):
        assert fg._custom_type_classname("order_detail") == "OrderDetail"

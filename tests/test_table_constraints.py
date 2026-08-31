import pytest

import converter.flux_gauss as fg


def test_collects_enum_checks_and_range_partition_metadata(tmp_path):
    ddl = tmp_path / "constraints.sql"
    ddl.write_text(
        """
        CREATE TYPE film_status AS ENUM ('draft', 'released');

        CREATE TABLE films (
            id bigint,
            status film_status,
            release_year integer CHECK (release_year > 1901),
            released_on date,
            CONSTRAINT release_year_cap CHECK (release_year <= 2100)
        ) PARTITION BY RANGE (released_on) (
            PARTITION old_films VALUES LESS THAN ('2024-01-01'),
            PARTITION current_films VALUES FROM ('2024-01-01') TO ('2025-01-01')
        );
        """,
        encoding="utf-8",
    )

    schema = fg.parse_table_ddl(str(ddl))
    fg._collect_table_constraints(str(ddl), schema)

    assert fg._TABLE_CONSTRAINTS == {
        "films": {
            "enums": {"status": ["draft", "released"]},
            "checks": {"release_year": ["release_year > 1901", "release_year <= 2100"]},
            "partition_key": "released_on",
            "partition_bounds": [("", "'2024-01-01'"), ("'2024-01-01'", "'2025-01-01'")],
        }
    }


def test_enum_type_can_be_declared_in_a_separate_ddl_file(tmp_path):
    enum_ddl = tmp_path / "enum.sql"
    enum_ddl.write_text("CREATE TYPE film_status AS ENUM ('draft', 'released');", encoding="utf-8")
    table_ddl = tmp_path / "table.sql"
    table_ddl.write_text("CREATE TABLE films (status film_status);", encoding="utf-8")

    fg._collect_table_constraints(str(enum_ddl), {})
    schema = fg.parse_table_ddl(str(table_ddl))
    fg._collect_table_constraints(str(table_ddl), schema)

    assert fg._TABLE_CONSTRAINTS["films"]["enums"] == {"status": ["draft", "released"]}


def test_collects_constraints_for_quoted_identifiers(tmp_path):
    ddl = tmp_path / "quoted.sql"
    ddl.write_text(
        "CREATE TYPE \"film_status\" AS ENUM ('draft');\n"
        'CREATE TABLE "films" ("status" "film_status", "year" integer CHECK ("year" >= 5));',
        encoding="utf-8",
    )

    schema = fg.parse_table_ddl(str(ddl))
    fg._collect_table_constraints(str(ddl), schema)

    assert schema == {"films": {"status": '"film_status"', "year": "integer"}}
    assert fg._TABLE_CONSTRAINTS["films"]["enums"] == {"status": ["draft"]}
    assert fg._TABLE_CONSTRAINTS["films"]["checks"] == {"year": ['"year" >= 5']}


def test_schema_emits_referenced_enum_before_table(tmp_path):
    fg.TYPE_OVERRIDES[("films", "status")] = "film_status"
    fg._TABLE_CONSTRAINTS["films"] = {
        "enums": {"status": ["draft", "released"]},
        "checks": {},
        "partition_key": None,
        "partition_bounds": [],
    }

    fg._itest_write_schema_sql(tmp_path, [], {"mode": "remote"})

    schema_sql = (tmp_path / "src/test/resources/itest-schema.sql").read_text(encoding="utf-8")
    enum_pos = schema_sql.index("CREATE TYPE film_status AS ENUM ('draft', 'released');")
    table_pos = schema_sql.index('CREATE TABLE IF NOT EXISTS "films"')
    assert enum_pos < table_pos


def test_sampler_uses_first_enum_member():
    constraints = {
        "enums": {"status": ["draft", "released"]},
        "checks": {},
        "partition_key": None,
        "partition_bounds": [],
    }
    assert fg._itest_generate_test_value("status", "film_status", constraints) == "'draft'"


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("release_year > 1901", "2001"),
        ("release_year >= 5", "5"),
        ("release_year < 10", "9"),
        ("release_year <= 10", "10"),
        ("release_year = 7", "7"),
    ],
)
def test_sampler_satisfies_numeric_check(expr, expected):
    constraints = {
        "enums": {},
        "checks": {"release_year": [expr]},
        "partition_key": None,
        "partition_bounds": [],
    }
    assert fg._itest_generate_test_value("release_year", "integer", constraints) == expected


def test_sampler_uses_date_inside_first_partition_bound():
    constraints = {
        "enums": {},
        "checks": {},
        "partition_key": "released_on",
        "partition_bounds": [("'2024-01-01'", "'2025-01-01'")],
    }
    assert fg._itest_generate_test_value("released_on", "date", constraints) == "'2024-01-01'"


def test_sampler_uses_date_below_less_than_partition_bound():
    constraints = {
        "enums": {},
        "checks": {},
        "partition_key": "released_on",
        "partition_bounds": [("", "'2024-01-01'")],
    }
    assert fg._itest_generate_test_value("released_on", "date", constraints) == "'2023-12-31'"


def test_sampler_partition_fallback_warns(monkeypatch):
    messages = []
    monkeypatch.setattr(fg, "_log", lambda message, **_kwargs: messages.append(message))
    constraints = {"enums": {}, "checks": {}, "partition_key": "released_on", "partition_bounds": []}
    assert fg._itest_generate_test_value("released_on", "date", constraints) == "'2024-01-01'"
    assert any("partition bounds not parsed" in message for message in messages)


def test_sampler_without_constraints_keeps_existing_behavior():
    assert fg._itest_generate_test_value("status", "varchar(20)") == "'test_status'"

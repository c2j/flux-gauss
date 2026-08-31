"""Regression guard for #90: AT TIME ZONE expression must generate valid Java.

ogsql-parser 0.10.1 之后 `now() AT TIME ZONE 'UTC'` 可解析；Python 转换器
在 0660199 已实现 AtTimeZone → atZone(ZoneId.of(...)) 映射。此测试锁定该
行为防回归（曾生成原始 dict 导致 EtlCoreService 156 处级联编译错误）。
"""

import json

import converter.flux_gauss as fg


def test_at_time_zone_literal_zone_generates_valid_java():
    probe = (
        '{"AtTimeZone": {"expr": {"FunctionCall": {"name": ["now"], "args": []}},'
        ' "zone": {"Literal": {"String": "UTC"}}}}'
    )
    java = fg._expr_to_java(json.loads(probe), None, all_packages=None)
    assert "ZoneId" in java, f"AtTimeZone must map to ZoneId, got: {java}"
    assert "UTC" in java, f"UTC literal zone must be preserved, got: {java}"
    assert "{" not in java, f"no raw dict leakage, got: {java}"
    assert "AtTimeZone" not in java, f"AST node name must not leak, got: {java}"


def test_at_time_zone_variable_zone_wraps_string_value():
    probe = (
        '{"AtTimeZone": {"expr": {"FunctionCall": {"name": ["now"], "args": []}}, "zone": {"PlVariable": ["v_tz"]}}}'
    )
    java = fg._expr_to_java(json.loads(probe), None, all_packages=None)
    assert "ZoneId.of" in java, f"variable zone must use ZoneId.of(...), got: {java}"

"""Tests for dependency_graph (codeweb file-level closure, #120).

Covers the pure graph→files logic `_depgraph_closure_from_graph` and the
config gating of `_resolve_dependency_graph_files`. Subprocess boundaries are
not exercised here; the codeweb CLI is mocked only where needed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "converter"))
import flux_gauss as fg  # noqa: E402


def _node(nid, ntype, name, file="a.sql", schema=None):
    return {"id": nid, "type": ntype, "name": name, "file": file, "schema": schema}


def _pkg(nid, name, schema=None):
    return {"id": nid, "type": "package", "name": name, "schema": schema}


def _edge(src, dst, etype="direct"):
    return {"source": src, "target": dst, "type": etype}


def test_disabled_returns_empty():
    files, stats = fg._resolve_dependency_graph_files({})
    assert files == [] and stats == {}
    files, stats = fg._resolve_dependency_graph_files(
        {"dependency_graph": {"enabled": False}})
    assert files == [] and stats == {}


def test_closure_follows_direct_and_dynamic_edges():
    graph = {
        "nodes": [
            _pkg(10, "pkg_a"), _pkg(11, "pkg_b"), _pkg(12, "pkg_c"),
            _node(0, "procedure", "top", "a.sql"),
            _node(1, "procedure", "mid", "b.sql"),
            _node(2, "function", "leaf", "c.sql"),
            _node(3, "procedure", "other", "d.sql"),
        ],
        "edges": [
            _edge(10, 0, "contains_routine"),
            _edge(11, 1, "contains_routine"),
            _edge(12, 2, "contains_routine"),
            _edge(12, 3, "contains_routine"),
            _edge(0, 1), _edge(1, 2, "dynamic"),
        ],
    }
    stats = fg._depgraph_closure_from_graph(graph, ["pkg_a.top"], "/proj")
    assert stats["routines"] == 3
    assert sorted(os.path.basename(f) for f in stats["files"]) == ["a.sql", "b.sql", "c.sql"]
    assert stats["matched"] == ["pkg_a.top"]
    assert stats["unmatched"] == []


def test_schema_qualified_seed_matches_pkg_proc_tail():
    graph = {
        "nodes": [
            _pkg(10, "pkg", schema="sch"),
            _node(0, "procedure", "proc", "s.sql"),
        ],
        "edges": [_edge(10, 0, "contains_routine")],
    }
    stats = fg._depgraph_closure_from_graph(graph, ["sch.pkg.proc"], "/proj")
    assert stats["routines"] == 1
    assert stats["files"] == [os.path.join("/proj", "s.sql")]


def test_standalone_proc_schema_name():
    # CREATE PROCEDURE pkg.proc → schema="pkg", no package node
    graph = {"nodes": [_node(0, "procedure", "proc", "p.sql", schema="pkg")],
             "edges": []}
    stats = fg._depgraph_closure_from_graph(graph, ["pkg.proc"], "/proj")
    assert stats["routines"] == 1


def test_bare_name_overapproximates():
    graph = {
        "nodes": [
            _node(0, "procedure", "dup", "a.sql"),
            _node(1, "procedure", "dup", "b.sql"),
            _node(2, "procedure", "uniq", "c.sql"),
        ],
        "edges": [],
    }
    stats = fg._depgraph_closure_from_graph(graph, ["dup"], "/proj")
    assert stats["routines"] == 2
    assert len(stats["files"]) == 2


def test_unmatched_seed_and_unresolved_nodes():
    graph = {
        "nodes": [
            _node(0, "procedure", "top", "a.sql"),
            {"id": 1, "type": "unresolved", "raw_expr": "missing.pkg.proc()"},
        ],
        "edges": [_edge(0, 1)],
    }
    stats = fg._depgraph_closure_from_graph(graph, ["top", "pkg.nope"], "/proj")
    assert stats["unmatched"] == ["pkg.nope"]
    assert stats["unresolved"] == ["missing.pkg.proc()"]


def test_contains_routine_does_not_pull_package():
    graph = {
        "nodes": [
            _pkg(10, "pkg_a"),
            _node(0, "procedure", "target", "a.sql"),
            _node(1, "procedure", "sibling", "a.sql"),
        ],
        "edges": [
            _edge(10, 0, "contains_routine"),
            _edge(10, 1, "contains_routine"),
        ],
    }
    stats = fg._depgraph_closure_from_graph(graph, ["pkg_a.target"], "/proj")
    assert stats["routines"] == 1


def test_missing_project_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(fg, "_resolve_codeweb_bin", lambda: "/bin/true")
    with pytest.raises(SystemExit):
        fg._resolve_dependency_graph_files(
            {"dependency_graph": {"enabled": True}})

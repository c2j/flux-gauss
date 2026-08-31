"""Regression guard for #73: version read must resolve the workspace root
`[workspace.package] version`, not fall back to a hardcoded literal."""

import converter.flux_gauss as fg


def _write_cargo(root_dir, crate_toml, root_toml):
    crate_dir = root_dir / "crates" / "fluxgauss"
    crate_dir.mkdir(parents=True)
    (crate_dir / "Cargo.toml").write_text(crate_toml, encoding="utf-8")
    (root_dir / "Cargo.toml").write_text(root_toml, encoding="utf-8")


def _base_dir(root_dir):
    converter_dir = root_dir / "converter"
    converter_dir.mkdir()
    return str(converter_dir)


def test_version_resolves_workspace_root_when_crate_uses_version_workspace(tmp_path):
    _write_cargo(
        tmp_path,
        crate_toml='[package]\nname = "fluxgauss"\nversion.workspace = true\n',
        root_toml=('[workspace]\nmembers = ["crates/fluxgauss"]\n\n[workspace.package]\nversion = "9.9.9"\n'),
    )
    assert fg._read_version_from_cargo_toml(base_dir=_base_dir(tmp_path)) == "9.9.9"


def test_version_reads_direct_version_when_present(tmp_path):
    _write_cargo(
        tmp_path,
        crate_toml='[package]\nname = "fluxgauss"\nversion = "1.2.3"\n',
        root_toml='[workspace]\nmembers = ["crates/fluxgauss"]\n',
    )
    assert fg._read_version_from_cargo_toml(base_dir=_base_dir(tmp_path)) == "1.2.3"


def test_version_returns_none_without_any_cargo_toml(tmp_path):
    assert fg._read_version_from_cargo_toml(base_dir=str(tmp_path)) is None

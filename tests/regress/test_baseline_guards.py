"""回归基线语料守卫（baseline marker）。

守护 AGENTS.md §3「回归基线双集」的 4 个配置（demo-project/fluxgauss_*）：
- ogagila（lib/ogagila 子模块，已注册）：sources 缺失即 FAIL —— 干净 clone 必须可复现。
- fastaas（lib/fastaas 为本地未跟踪目录，上游仓库 c2j/fastaas 不存在）：
  语料缺失时显式 pytest.skip（AGENTS.md §8 口径：skipped 单列、不计入失败）；
  有语料的机器（本机）则全量校验。
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# (engine, yaml_path, corpus_required)
BASELINE_CONFIGS = [
    ("py", "demo-project/fluxgauss_ogagila_py_v2.yaml", True),
    ("ru", "demo-project/fluxgauss_ogagila_ru_v2.yaml", True),
    ("py", "demo-project/fluxgauss_fastaas_py.yaml", False),
    ("ru", "demo-project/fluxgauss_fastaas_ru.yaml", False),
]


def _load_sources(yaml_path: str) -> list:
    import yaml  # pyyaml

    yaml_full = REPO_ROOT / yaml_path
    with open(yaml_full, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sources = cfg.get("sources", [])
    assert isinstance(sources, list) and len(sources) > 0, f"{yaml_path}: no sources listed"
    return sources


@pytest.mark.baseline
@pytest.mark.parametrize(
    "engine,yaml_path,corpus_required",
    [(e, y, r) for e, y, r in BASELINE_CONFIGS],
)
def test_baseline_sources_exist(engine, yaml_path, corpus_required):
    sources = _load_sources(yaml_path)
    missing = [s for s in sources if not (REPO_ROOT / s).exists()]

    if corpus_required:
        assert not missing, (
            f"{yaml_path} references {len(missing)} missing SQL files: {missing}. "
            f"ogagila 子模块已注册，干净 clone 必须可复现 —— 缺失视为门禁缺陷。"
        )
    elif missing:
        pytest.skip(
            f"fastaas corpus absent ({len(missing)} missing sources): "
            f"lib/fastaas 为本地可选依赖，此机器无语料时跳过 fastaas 基线校验"
        )


@pytest.mark.baseline
def test_baseline_configs_parse():
    import yaml  # pyyaml

    for engine, yaml_path, _ in BASELINE_CONFIGS:
        yaml_full = REPO_ROOT / yaml_path
        with open(yaml_full, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg.get("output_dir"), f"{yaml_path}: missing output_dir"
        assert cfg.get("base_package"), f"{yaml_path}: missing base_package"
        assert cfg.get("sources"), f"{yaml_path}: missing sources"
        itest = cfg.get("integration_test", {})
        assert itest.get("enabled") is False, f"{yaml_path}: baseline should disable integration_test"

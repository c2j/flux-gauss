#!/usr/bin/env bash
# 三数据集 × 双引擎全流程摸底：convert → mvn compile → mvn test → 汇总
# 用法: scripts/verify-migration.sh [dataset [engine]]
#   dataset: exam | fastaas | ogagila | all (默认 all)
#   engine:  py | ru | all (默认 all)
set -u
cd "$(dirname "$0")/.." || exit 1

RU_BIN=target/release/fluxgauss
OGSQL_BIN=${OGSQL_BIN:-$(command -v ogsql || echo ./ogsql)}

# 兼容 macOS bash 3.2（无 mapfile）
ARG1=${1:-all}
ARG2=${2:-all}
if [[ "$ARG1" == "all" ]]; then DATASETS=(exam fastaas ogagila); else DATASETS=("$ARG1"); fi
if [[ "$ARG2" == "all" ]]; then ENGINES=(py ru); else ENGINES=("$ARG2"); fi

# ogagila \set 清洗：读入 stdin，剥离 psql 元指令
clean_ogagila() {
  sed '/^\\set /d' 
}

for ds in "${DATASETS[@]}"; do
  for en in "${ENGINES[@]}"; do
    # 解析配置名：exam 有两份 (exam.yaml / exam-ru.yaml)；ogagila 有四份 (py/ru/py_v2/ru_v2)
    case "$ds-$en" in
      exam-py)    yaml=demo-project/fluxgauss_exam.yaml ;;
      exam-ru)    yaml=demo-project/fluxgauss_exam-ru.yaml ;;
      fastaas-py) yaml=demo-project/fluxgauss_fastaas_py.yaml ;;
      fastaas-ru) yaml=demo-project/fluxgauss_fastaas_ru.yaml ;;
      ogagila-py) yaml=demo-project/fluxgauss_ogagila_py.yaml ;;
      ogagila-ru) yaml=demo-project/fluxgauss_ogagila_ru.yaml ;;
      *) echo "SKIP $ds/$en (未识别配置)"; continue ;;
    esac
    dest="dest_${ds}_${en}_probe"
    echo "════════ $ds/$en ════════"
    echo "yaml: $yaml | dest: $dest"
    rm -rf "$dest"

    # 覆盖 output_dir 为探测目录（不动原配置）
    probe_cfg="$(mktemp /tmp/mig_probe.XXXXXX)"
    sed "s#^output_dir:.*#output_dir: $dest#" "$yaml" > "$probe_cfg"
    yaml="$probe_cfg"

    # ogagila: 剥离 psql \set 元指令到临时目录，配置 sources 指向清洗副本
    # （与 test_fastaas_migration.py::_prepare_ogagila 同逻辑；勿直接转换原始 submodule SQL）
    if [[ "$ds" == "ogagila" ]]; then
      tmp_cfg="$(mktemp /tmp/ogagila_cfg.XXXXXX)"
      python3 - "$yaml" "$tmp_cfg" << 'PYEOF'
import shutil, sys, tempfile
from pathlib import Path
src_cfg, dst_cfg = sys.argv[1], sys.argv[2]
src_root = Path("lib/ogagila/sqls")
clean = Path(tempfile.mkdtemp(prefix="ogagila_clean_"))
shutil.copytree(src_root, clean, dirs_exist_ok=True)
for sql in clean.rglob("*.sql"):
    stripped = b"\n".join(
        ln for ln in sql.read_bytes().split(b"\n") if not ln.lstrip().startswith(b"\\set ")
    )
    sql.write_bytes(stripped)
text = open(src_cfg, encoding="utf-8").read()
text = text.replace(str(src_root), str(clean))
open(dst_cfg, "w", encoding="utf-8").write(text)
PYEOF
      yaml="$tmp_cfg"
    fi

    # convert
    if [[ "$en" == "py" ]]; then
      if [[ ! -x "$OGSQL_BIN" ]]; then echo "  SKIP py (ogsql 缺失: $OGSQL_BIN)"; rm -rf "$dest"; continue; fi
      OGSQL_BIN="$OGSQL_BIN" python3 converter/flux_gauss.py -c "$yaml" >/dev/null 2>&1
      conv_exit=$?
    else
      if [[ ! -x "$RU_BIN" ]]; then echo "  SKIP ru (需先 cargo build --release --bin fluxgauss)"; rm -rf "$dest"; continue; fi
      "$RU_BIN" --config "$yaml" >/dev/null 2>&1
      conv_exit=$?
    fi
    echo "  convert exit: $conv_exit"

    if [[ $conv_exit -ne 0 ]]; then echo "  ✗ 转换失败，跳过编译/测试"; rm -rf "$dest"; continue; fi

    # compile + test
    (cd "$dest" && mvn -q compile >/dev/null 2>&1; echo "  compile exit: $?")
    comp_err=$(cd "$dest" && mvn -q compile 2>&1 | grep -oE "\.java:\[[0-9]+,[0-9]+\]" | sort -u | wc -l | tr -d ' ')
    echo "  compile 唯一错误位置数: $comp_err"
    (cd "$dest" && mvn test 2>&1 | grep -oE "Errors: [0-9]+|Failures: [0-9]+" | awk -F': ' '{s[$1]+=$2} END {for(k in s) print "  "k"="s[k]}')
    # 首屏错误样例
    (cd "$dest" && mvn test 2>&1 | grep -E "^\[ERROR\].*(ERROR|FAILURE)!? -- in|Tests run:" | head -3)
    echo ""
  done
done

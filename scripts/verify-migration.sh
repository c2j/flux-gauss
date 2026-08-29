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

    # ogagila: 清洗 sources → 生成临时配置
    if [[ "$ds" == "ogagila" ]]; then
      tmp_cfg="$(mktemp /tmp/ogagila_cfg.XXXXXX)"
      python3 - "$yaml" "$tmp_cfg" << 'PYEOF'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding='utf-8').read()
# sources 指向的 /tmp/ogagila_src → 清洗后写入的临时目录，逐文件 sed 等价
# 简单实现：把 /tmp/ogagila_src 路径替换为 /tmp/ogagila_src_clean（脚本调用方需先建好）
text = text.replace('/tmp/ogagila_src', '/tmp/ogagila_src_clean')
open(dst, 'w', encoding='utf-8').write(text)
PYEOF
      # 建清洗副本
      rm -rf /tmp/ogagila_src_clean && mkdir -p /tmp/ogagila_src_clean
      cp -r /tmp/ogagila_src/* /tmp/ogagila_src_clean/
      find /tmp/ogagila_src_clean -name "*.sql" -exec sed -i '' '/^\\set /d' {} \; 2>/dev/null
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

#!/usr/bin/env bash
# dual_engine_diff.sh — Python vs Rust fluxgauss engine divergence diff.
#
# For each fixture (default: tests/regress/fixtures/issue_144_*.sql), converts
# once with the Python engine and once with the Rust engine into temp dirs,
# normalizes generated sources, diffs them, and prints a per-fixture summary
# table plus divergence samples.
#
# Usage:
#   scripts/dual_engine_diff.sh [fixture.sql ...]
#   scripts/dual_engine_diff.sh --all-issue144
#
# Env overrides:
#   PYTHON_BIN   Python interpreter (default /Users/c2j/dev/miniforge3/bin/python)
#   OGSQL_BIN    ogsql binary for the Python engine (default /opt/homebrew/bin/ogsql)
#   RUST_BIN     fluxgauss binary (default: cargo run -q -p fluxgauss --)
#   KEEP=1       keep temp dirs and print their paths
#
# Exit code: 0 = no divergence on any compared file; 1 = divergence found;
#            2 = tool/usage error.
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

PYTHON_BIN="${PYTHON_BIN:-/Users/c2j/dev/miniforge3/bin/python}"
OGSQL_BIN="${OGSQL_BIN:-/opt/homebrew/bin/ogsql}"
CONVERTER="$REPO_ROOT/converter/flux_gauss.py"
FIXTURE_GLOB="tests/regress/fixtures/issue_144_*.sql"
SAMPLE_N=5

fixtures=()
for arg in "$@"; do
    case "$arg" in
        --all-issue144) ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) fixtures+=("$arg") ;;
    esac
done
if [ "${#fixtures[@]}" -eq 0 ]; then
    while IFS= read -r f; do fixtures+=("$f"); done < <(ls $FIXTURE_GLOB 2>/dev/null | sort)
fi
if [ "${#fixtures[@]}" -eq 0 ]; then
    echo "dual_engine_diff: no fixtures matched $FIXTURE_GLOB" >&2
    exit 2
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "dual_engine_diff: python not found: $PYTHON_BIN" >&2; exit 2; }
[ -x "$OGSQL_BIN" ] || { echo "dual_engine_diff: ogsql not found: $OGSQL_BIN" >&2; exit 2; }
[ -f "$CONVERTER" ] || { echo "dual_engine_diff: converter not found: $CONVERTER" >&2; exit 2; }

# Normalize generated sources for cross-engine diff:
#  - strip trailing whitespace
#  - collapse any path prefix ending in .sql to the bare filename (Source:
#    comments embed absolute paths on the Rust side, relative on Python)
#  - drop pure timestamp/report lines if any leak into src/
normalize() {
    sed -E -e 's/[[:space:]]+$//' \
           -e 's#(/[A-Za-z0-9_.-]+)*/([A-Za-z0-9_-]+\.sql)#\2#g' \
           -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.]+//g' "$1"
}

# Collect comparable generated files: everything under <out>/src.
collect_files() {
    local out="$1"
    (cd "$out" 2>/dev/null && find src -type f \( -name '*.java' -o -name '*.xml' \) | sort)
}

RUST_CMD="${RUST_BIN:-cargo run -q -p fluxgauss --}"

WORK="$(mktemp -d /tmp/dual_diff.XXXXXX)"
trap 'if [ "${KEEP:-0}" != "1" ]; then rm -rf "$WORK"; fi' EXIT

total_fixtures=0
total_diverged_files=0
total_diff_lines=0
declare -a summary_rows=()

for fixture in "${fixtures[@]}"; do
    [ -f "$fixture" ] || { echo "dual_engine_diff: fixture not found: $fixture" >&2; exit 2; }
    fixture_base="$(basename "$fixture")"
    stem="${fixture_base%.sql}"
    py_out="$WORK/$stem/py"
    ru_out="$WORK/$stem/ru"
    mkdir -p "$py_out" "$ru_out"

    # Python engine: temp yaml with the single fixture as its only source.
    cat > "$WORK/$stem/py.yaml" <<EOF
output_dir: $py_out
base_package: com.example.demo
sources:
  - $REPO_ROOT/$fixture
EOF
    OGSQL_BIN="$OGSQL_BIN" "$PYTHON_BIN" "$CONVERTER" -c "$WORK/$stem/py.yaml" --full \
        > "$WORK/$stem/py.log" 2>&1
    py_rc=$?

    # Rust engine: CLI one-shot mode.
    $RUST_CMD -o "$ru_out" -s "$REPO_ROOT/$fixture" --skip-validate \
        > "$WORK/$stem/ru.log" 2>&1
    ru_rc=$?

    total_fixtures=$((total_fixtures + 1))

    if [ $py_rc -ne 0 ] || [ $ru_rc -ne 0 ]; then
        summary_rows+=("$stem|ENGINE_FAIL|py_rc=$py_rc ru_rc=$ru_rc|-|-|-|")
        continue
    fi

    py_files="$(collect_files "$py_out")"
    ru_files="$(collect_files "$ru_out")"

    # File-set divergence: missing / extra.
    missing_py="$(comm -23 <(echo "$ru_files") <(echo "$py_files") | tr '\n' ' ')"
    missing_ru="$(comm -13 <(echo "$ru_files") <(echo "$py_files") | tr '\n' ' ')"
    set_div=0
    [ -n "$missing_py" ] && set_div=1
    [ -n "$missing_ru" ] && set_div=1

    fixture_diff_lines=0
    fixture_div_files=0
    samples=""
    for f in $(echo "$py_files" "$ru_files" | tr ' ' '\n' | sort -u); do
        [ -f "$py_out/$f" ] && [ -f "$ru_out/$f" ] || continue
        normalize "$py_out/$f" > "$WORK/py.norm"
        normalize "$ru_out/$f" > "$WORK/ru.norm"
        if ! diff -q "$WORK/py.norm" "$WORK/ru.norm" >/dev/null; then
            n=$(diff "$WORK/py.norm" "$WORK/ru.norm" | grep -c '^[<>]')
            fixture_diff_lines=$((fixture_diff_lines + n))
            fixture_div_files=$((fixture_div_files + 1))
            total_diverged_files=$((total_diverged_files + 1))
            samples="yes"
            {
                echo "  [$f]"
                diff "$WORK/py.norm" "$WORK/ru.norm" | grep '^[<>]' | head -$SAMPLE_N \
                    | cut -c1-200 | sed 's/^/    /'
            } >> "$WORK/$stem.samples"
        fi
    done
    total_diff_lines=$((total_diff_lines + fixture_diff_lines))

    # 粗分类：missing/extra 文件优先，其次内容分歧。
    if [ $set_div -eq 1 ]; then
        cat_label="file_set"
    elif [ $fixture_div_files -gt 0 ]; then
        cat_label="content_diff"
    else
        cat_label="identical"
    fi
    missing_note=""
    [ -n "$missing_py" ] && missing_note="py_missing:${missing_py}"
    if [ -n "$missing_ru" ]; then
        missing_note="${missing_note} ru_missing:${missing_ru}"
    fi
    summary_rows+=("$stem|$cat_label|files:$fixture_div_files lines:$fixture_diff_lines|$missing_note|$samples|$WORK/$stem.samples")
done

# ── 汇总表 ──
echo ""
echo "=== dual_engine_diff summary ==="
printf '%-38s %-12s %-28s %s\n' "fixture" "category" "divergence" "missing/extra"
for row in "${summary_rows[@]}"; do
    IFS='|' read -r stem cat div miss samples spath <<< "$row"
    printf '%-38s %-12s %-28s %s\n' "$stem" "$cat" "$div" "$miss"
done
echo ""
echo "totals: fixtures=$total_fixtures diverged_files=$total_diverged_files diff_lines=$total_diff_lines"

# ── 分歧样例（每 fixture 前 $SAMPLE_N 条）──
for row in "${summary_rows[@]}"; do
    IFS='|' read -r stem cat div miss samples spath <<< "$row"
    if [ "$cat" != "identical" ] && [ -n "$spath" ] && [ -f "$spath" ]; then
        echo ""
        echo "--- $stem ($cat) ---"
        head -60 "$spath"
    fi
done

if [ "${KEEP:-0}" = "1" ]; then
    echo ""
    echo "workdir kept: $WORK"
fi

[ "$total_diff_lines" -eq 0 ] && [ "$total_diverged_files" -eq 0 ]

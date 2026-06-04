#!/usr/bin/env bash
# Build fluxgauss-py standalone binary using PyInstaller
# Usage: OGSQL_BIN_PATH=/path/to/ogsql ./scripts/build_pyinstaller.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Building fluxgauss-py binary ==="

python3 --version

pip install pyinstaller pyyaml

if [ -z "${OGSQL_BIN_PATH:-}" ]; then
    echo "WARNING: OGSQL_BIN_PATH not set. Searching for ogsql..."
    OGSQL_BIN_PATH=$(which ogsql 2>/dev/null || true)
    if [ -z "$OGSQL_BIN_PATH" ]; then
        echo "ERROR: ogsql binary not found. Set OGSQL_BIN_PATH."
        exit 1
    fi
fi
echo "Using ogsql: $OGSQL_BIN_PATH"
export OGSQL_BIN_PATH

rm -rf build/ dist/

pyinstaller fluxgauss.spec --clean --noconfirm

if [ -f "dist/fluxgauss-py" ] || [ -f "dist/fluxgauss-py.exe" ]; then
    echo "=== Build successful ==="
    ls -lh dist/fluxgauss-py*
else
    echo "ERROR: Build failed - no binary found in dist/"
    exit 1
fi

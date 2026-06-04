# PyInstaller Packaging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Package the Python engine (`converter/flux_gauss.py`) as a standalone binary via PyInstaller, supporting the same 4 platforms as the Rust engine.

**Architecture:** Use PyInstaller `--onefile` mode to bundle the Python script + pyyaml into a single executable. The `ogsql` binary is bundled as a data file and extracted at runtime via `sys._MEIPASS`. Modify `_resolve_ogsql_bin()` to detect frozen state and locate the bundled ogsql.

**Tech Stack:** PyInstaller 6.x, GitHub Actions (4 platform runners), Python 3.12

---

### Task 1: Modify `_resolve_ogsql_bin()` for PyInstaller support

**Files:**
- Modify: `converter/flux_gauss.py:36-48`

**Step 1: Edit `_resolve_ogsql_bin()` to detect frozen state**

Add PyInstaller detection at the top of the candidate list. When `sys.frozen` is True, the binary is running from a PyInstaller bundle, and bundled data files are in `sys._MEIPASS`.

Current code (lines 36-48):
```python
def _resolve_ogsql_bin() -> str:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_dir = os.path.dirname(_script_dir)
    for candidate in [
        os.path.join(os.getcwd(), "ogsql"),
        os.environ.get("OGSQL_BIN", ""),
        shutil.which("ogsql") or "",
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "aarch64-apple-darwin", "release", "ogsql"),
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "release", "ogsql"),
    ]:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "ogsql"
```

Replace with:
```python
def _resolve_ogsql_bin() -> str:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_dir = os.path.dirname(_script_dir)
    candidates = []
    # PyInstaller frozen binary: bundled ogsql is in sys._MEIPASS
    if getattr(sys, 'frozen', False):
        _meipass = getattr(sys, '_MEIPASS', '')
        if _meipass:
            candidates.append(os.path.join(_meipass, 'ogsql'))
            candidates.append(os.path.join(_meipass, 'ogsql.exe'))
    candidates.extend([
        os.path.join(os.getcwd(), "ogsql"),
        os.environ.get("OGSQL_BIN", ""),
        shutil.which("ogsql") or "",
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "aarch64-apple-darwin", "release", "ogsql"),
        os.path.join(_project_dir, "lib", "ogsql-parser", "target", "release", "ogsql"),
    ])
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "ogsql"
```

**Step 2: Verify the edit doesn't break normal operation**

Run: `python3 converter/flux_gauss.py --help`
Expected: Shows help output (proves no import/syntax errors)

**Step 3: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: support PyInstaller frozen mode in _resolve_ogsql_bin()"
```

---

### Task 2: Create project packaging files

**Files:**
- Create: `requirements.txt`
- Create: `fluxgauss.spec` (PyInstaller spec file)
- Create: `scripts/build_pyinstaller.sh` (local build script)

**Step 1: Create `requirements.txt`**

```
pyyaml>=6.0
```

**Step 2: Create `fluxgauss.spec`**

PyInstaller spec file that:
- Uses `--onefile` mode
- Bundles the `ogsql` binary as data (user provides it at build time)
- Sets the binary name to `fluxgauss-py`
- Excludes unnecessary modules to reduce size
- Handles both Unix (.tar.gz ogsql) and Windows (.zip ogsql.exe) naming

```python
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import shutil

block_cipher = None

# Locate ogsql binary for bundling
ogsql_bin = os.environ.get('OGSQL_BIN_PATH', '')
if not ogsql_bin or not os.path.isfile(ogsql_bin):
    # Try common locations
    for candidate in ['ogsql', 'ogsql.exe', '../ogsql', '../ogsql.exe']:
        if os.path.isfile(candidate):
            ogsql_bin = os.path.abspath(candidate)
            break

if not ogsql_bin or not os.path.isfile(ogsql_bin):
    print("WARNING: ogsql binary not found. Set OGSQL_BIN_PATH env var.")
    print("  The packaged binary will NOT be able to parse SQL without ogsql.")
    datas = []
else:
    datas = [(ogsql_bin, '.')]

a = Analysis(
    ['converter/flux_gauss.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'test', 'tests',
        'distutils', 'setuptools', 'pip',
        'email', 'html', 'http', 'urllib',
        'xmlrpc', 'pydoc', 'doctest',
        'multiprocessing', 'concurrent',
        'asyncio', 'logging.handlers',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='fluxgauss-py',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

**Step 3: Create `scripts/build_pyinstaller.sh`**

```bash
#!/usr/bin/env bash
# Build fluxgauss-py standalone binary using PyInstaller
# Usage: OGSQL_BIN_PATH=/path/to/ogsql ./scripts/build_pyinstaller.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Building fluxgauss-py binary ==="

# Check Python version
python3 --version

# Install dependencies
pip install pyinstaller pyyaml

# Check ogsql binary
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

# Clean previous build
rm -rf build/ dist/

# Build
pyinstaller fluxgauss.spec --clean --noconfirm

# Verify output
if [ -f "dist/fluxgauss-py" ] || [ -f "dist/fluxgauss-py.exe" ]; then
    echo "=== Build successful ==="
    ls -lh dist/fluxgauss-py*
else
    echo "ERROR: Build failed - no binary found in dist/"
    exit 1
fi
```

Make executable: `chmod +x scripts/build_pyinstaller.sh`

**Step 4: Add .gitignore entries for PyInstaller artifacts**

Add to `.gitignore`:
```
# PyInstaller
build/
dist/
*.spec.bak
```

**Step 5: Commit**

```bash
git add requirements.txt fluxgauss.spec scripts/build_pyinstaller.sh .gitignore
git commit -m "feat: add PyInstaller packaging files (spec, build script, requirements)"
```

---

### Task 3: Add Python binary build jobs to CI/CD

**Files:**
- Modify: `.github/workflows/release.yml`

**Step 1: Add 4 Python binary build jobs**

Insert after the existing `test-python-engine` job (line ~367), before `test-rust-engine`:

Add these jobs, each depending on the corresponding ogsql build job:

```yaml
  # ── Python engine binary builds (PyInstaller) ──────────────
  build-fluxgauss-py-linux-x86_64:
    name: Build fluxgauss-py linux-x86_64
    runs-on: ubuntu-22.04
    needs: [build-ogsql-linux-x86_64]
    steps:
      - name: Checkout flux-gauss
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install pyinstaller pyyaml

      - name: Download ogsql binary
        uses: actions/download-artifact@v4
        with:
          name: ogsql-linux-x86_64
          path: ogsql-artifact

      - name: Extract ogsql
        run: |
          tar xzf ogsql-artifact/ogsql-linux-x86_64.tar.gz -C ogsql-artifact
          chmod +x ogsql-artifact/ogsql
          echo "OGSQL_BIN_PATH=$PWD/ogsql-artifact/ogsql" >> "$GITHUB_ENV"

      - name: Build with PyInstaller
        run: pyinstaller fluxgauss.spec --clean --noconfirm

      - name: Strip
        run: strip --strip-unneeded dist/fluxgauss-py

      - name: Package
        run: |
          cd dist
          tar czf ${{ github.workspace }}/fluxgauss-py-linux-x86_64.tar.gz fluxgauss-py

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: fluxgauss-py-linux-x86_64
          path: fluxgauss-py-linux-x86_64.tar.gz

  build-fluxgauss-py-linux-arm64:
    name: Build fluxgauss-py linux-arm64
    runs-on: ubuntu-22.04-arm
    needs: [build-ogsql-linux-arm64]
    steps:
      - name: Checkout flux-gauss
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install pyinstaller pyyaml

      - name: Download ogsql binary
        uses: actions/download-artifact@v4
        with:
          name: ogsql-linux-arm64
          path: ogsql-artifact

      - name: Extract ogsql
        run: |
          tar xzf ogsql-artifact/ogsql-linux-arm64.tar.gz -C ogsql-artifact
          chmod +x ogsql-artifact/ogsql
          echo "OGSQL_BIN_PATH=$PWD/ogsql-artifact/ogsql" >> "$GITHUB_ENV"

      - name: Build with PyInstaller
        run: pyinstaller fluxgauss.spec --clean --noconfirm

      - name: Strip
        run: strip --strip-unneeded dist/fluxgauss-py

      - name: Package
        run: |
          cd dist
          tar czf ${{ github.workspace }}/fluxgauss-py-linux-arm64.tar.gz fluxgauss-py

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: fluxgauss-py-linux-arm64
          path: fluxgauss-py-linux-arm64.tar.gz

  build-fluxgauss-py-windows-x86_64:
    name: Build fluxgauss-py windows-x86_64
    runs-on: windows-2022
    needs: [build-ogsql-windows-x86_64]
    steps:
      - name: Checkout flux-gauss
        uses: actions/checkout@v4

      - name: Enable long paths
        run: git config --global core.longpaths true

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install pyinstaller pyyaml

      - name: Download ogsql binary
        uses: actions/download-artifact@v4
        with:
          name: ogsql-windows-x86_64
          path: ogsql-artifact

      - name: Extract ogsql
        shell: pwsh
        run: |
          Expand-Archive -Path ogsql-artifact/ogsql-windows-x86_64.zip -DestinationPath ogsql-artifact
          $ogsqlPath = (Resolve-Path "ogsql-artifact/ogsql.exe").Path
          "OGSQL_BIN_PATH=$ogsqlPath" >> $env:GITHUB_ENV

      - name: Build with PyInstaller
        run: pyinstaller fluxgauss.spec --clean --noconfirm

      - name: Package
        shell: pwsh
        run: |
          Compress-Archive `
            -Path "dist/fluxgauss-py.exe" `
            -DestinationPath "fluxgauss-py-windows-x86_64.zip"

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: fluxgauss-py-windows-x86_64
          path: fluxgauss-py-windows-x86_64.zip

  build-fluxgauss-py-macos-arm64:
    name: Build fluxgauss-py macos-arm64
    runs-on: macos-14
    needs: [build-ogsql-macos-arm64]
    steps:
      - name: Checkout flux-gauss
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install pyinstaller pyyaml

      - name: Download ogsql binary
        uses: actions/download-artifact@v4
        with:
          name: ogsql-macos-arm64
          path: ogsql-artifact

      - name: Extract ogsql
        run: |
          tar xzf ogsql-artifact/ogsql-macos-arm64.tar.gz -C ogsql-artifact
          chmod +x ogsql-artifact/ogsql
          echo "OGSQL_BIN_PATH=$PWD/ogsql-artifact/ogsql" >> "$GITHUB_ENV"

      - name: Build with PyInstaller
        run: pyinstaller fluxgauss.spec --clean --noconfirm

      - name: Strip
        run: strip dist/fluxgauss-py

      - name: Package
        run: |
          cd dist
          tar czf ${{ github.workspace }}/fluxgauss-py-macos-arm64.tar.gz fluxgauss-py

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: fluxgauss-py-macos-arm64
          path: fluxgauss-py-macos-arm64.tar.gz
```

**Step 2: Update `release` job `needs` list**

Add the 4 new build jobs to the `release` job's `needs` array:

```yaml
    needs:
      - build-ogsql-linux-x86_64
      - build-ogsql-linux-arm64
      - build-ogsql-windows-x86_64
      - build-ogsql-macos-arm64
      - build-fluxgauss-linux-x86_64
      - build-fluxgauss-linux-arm64
      - build-fluxgauss-windows-x86_64
      - build-fluxgauss-macos-arm64
      - build-fluxgauss-py-linux-x86_64
      - build-fluxgauss-py-linux-arm64
      - build-fluxgauss-py-windows-x86_64
      - build-fluxgauss-py-macos-arm64
```

**Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add PyInstaller binary build for Python engine (4 platforms)"
```

---

### Task 4: Local smoke test (macOS)

**Files:** None (verification only)

**Step 1: Install PyInstaller locally**

```bash
pip3 install pyinstaller pyyaml
```

**Step 2: Build locally**

```bash
# Use existing ogsql binary (from PATH or OGSQL_BIN)
OGSQL_BIN_PATH=$(which ogsql) ./scripts/build_pyinstaller.sh
```

**Step 3: Run the binary**

```bash
./dist/fluxgauss-py --help
```
Expected: Shows help output identical to `python3 converter/flux_gauss.py --help`

**Step 4: Test full conversion**

```bash
./dist/fluxgauss-py -c demo-project/fluxgauss_py.yaml
```
Expected: Generates output in `dest_py/` identical to running with Python

**Step 5: Commit any fixes discovered during testing**

---

## Summary

| Task | Files | Complexity |
|------|-------|-----------|
| 1. PyInstaller detection | `converter/flux_gauss.py` | Low (8 lines changed) |
| 2. Packaging files | `requirements.txt`, `fluxgauss.spec`, `scripts/build_pyinstaller.sh` | Medium (new files) |
| 3. CI/CD workflow | `.github/workflows/release.yml` | Medium (4 new jobs + release update) |
| 4. Local smoke test | None | Low (verification) |

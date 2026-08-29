# fastaas submodule 落地 + 三数据集回归检查项 + 现有问题修复 — 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** ①把 `git@github.com:NO3623/fastaas.git` 加为 submodule（与 `lib/ogagila` 并列），把 `ogagila` 更新到远程 main；②将 fastaas 的 `exam/清算拆分优化考题/基线代码`（136 文件）与 `exam/all_sq/collected_sql/collected_sql`（49 文件）及 ogagila（22 文件）三数据集 × 双引擎纳入可复现的回归检查项；③先修复这三数据集当前迁移的既有问题，使检查项落地时全绿。

**Architecture:** 三数据集的 SQL 目前只存在于仓外绝对路径（`/Users/c2j/Projects/Desktop_Projects/DOTA/...`、`/tmp/fastaas/...`、`/tmp/ogagila_src/...`），导致回归不可复现。方案：fastaas 仓库内已跟踪全部两组 SQL（md5 与 DOTA 一致）→ 一个 `lib/fastaas` submodule 同时服务 exam 与 fastaas 两组配置；ogagila 配置改指既有 `lib/ogagila/sqls/` + 预处理清洗（`\set` 指令）；8 个未提交的 yaml 配置改指 submodule 相对路径并提交；新增 `test_fastaas_migration.py` 镜像 `test_demo_migration` 的 L0.0-L0.5 分层检查，三数据集 × 双引擎全绿为硬门禁。前置：先修现有问题（exam mock 默认值、ogagila test 16 errors、Rust 编码），否则检查项一上就红。

**Tech Stack:** git submodule、Python pytest（镜像 test_demo_migration.py）、Rust/Python 双引擎转换器、Maven、GitHub Actions、GB18030 编码处理。

---

## 现状事实（已验证）

| 事实 | 证据 |
|---|---|
| fastaas 远程 `git@github.com:NO3623/fastaas.git` 可访问；默认分支 main，HEAD `106046ae` | `git ls-remote` |
| **`NO3623/fastaas` 是 PRIVATE 仓库**；`c2j/ogagila` 是 PUBLIC | `gh repo view --json visibility` |
| fastaas 仓库内已跟踪两组 SQL：`exam/清算拆分优化考题/基线代码/`（136 文件）+ `exam/all_sq/collected_sql/collected_sql/`（49 文件），md5 与 DOTA 路径完全一致 | `git ls-files` + md5 全量比对 |
| fastaas 两组 SQL 均 0 psql 元指令（`\set`/`\echo`）→ 直连 submodule 无需清洗 | grep |
| ogagila submodule `lib/ogagila`：index 记录 `a0395ba4`，工作树 `2ac17ec7`（未提交 +19 commit），**远程 main = `b472aca1`** | `git submodule status` + `ls-remote` |
| ogagila `dw/ddl`+`dw/program` 16 文件含 `\set`/`\echo`，`/tmp/ogagila_src` 是 `lib/ogagila/sqls/` copy + `sed '/^\\set /d'` 清洗产物（布局 1:1 匹配） | docs/reports/ogagila-engine-validation + 目录 diff |
| 8 个 fastaas/exam/ogagila yaml **全部未提交**（untracked）；sources 全指仓外绝对路径；**均无 `encoding` 字段** | git status + 文件读 |
| Python 引擎自动探测 GB18030/GBK/Big5（`_read_sql_file`）；**Rust 引擎默认 utf-8、无自动探测**（config.rs `encoding_or_default` → "utf-8"）——fastaas collected_sql 是 GB18030，Rust 读会乱码 | 代码读 + `file` 探测 |
| CI 所有 checkout@v4 无 `submodules: recursive` → 当前 CI 不拉 submodule | ci.yml |
| 现有门禁 `test_demo_migration.py`：L0.0 配置完整性（`REPO_ROOT / s` 对绝对路径也有效）/ L0.1 转换 / L0.2 编译 0 错 / L0.3 测试 0 错 / L0.4 语义健康 / L0.5 DML 数量；阈值按 engine 键控、`DEMO_CONFIGS` 硬编码 | 文件读 |
| **摸底现状**：exam-ru compile 0 错 / test **1 error**（`_2FncGetPurchaseJsDaysServiceTest`：跨包 String mock 默认值 `"1"` 流入 `LocalDate.parse` → DateTimeParseException）；ogagila_ru compile 0 错 / test **16 errors**（待归类）；exam-py / ogagila-py / fastaas-py/ru 未跑 | 本机 mvn 实测 |

---

## 设计决策（用户已确认）

1. **submodule URL 用 ssh**（`git@github.com:NO3623/fastaas.git`）——且因 fastaas 是 PRIVATE，**CI 必须配 deploy key**（外部前置，见 §前置条件）
2. **ogagila 一并纳入检查项**（22 文件 × 双引擎），需处理 `\set` 清洗
3. **ogagila submodule 更新到远程 main `b472aca1`**
4. **先修现有问题，再加检查项**（否则检查项一上就红）

---

## 前置条件（用户需配合，非本计划代码任务）

- **fastaas deploy key**：在 flux-gauss 仓库 Settings → Secrets and variables → Actions 加一个 secret（如 `FASTAAS_DEPLOY_KEY`），其公钥加入 `NO3623/fastaas` 仓库的 Deploy keys（只读）。CI 的 checkout 才能拉 PRIVATE submodule。
- 若用户无权给 NO3623/fastaas 加 deploy key，替代方案：CI 用 PAT（个人访问令牌）以 https 形式拉取（`https://x-access-token:${PAT}@github.com/NO3623/fastaas.git`）。二选一，用户定。
- ogagila 是 PUBLIC，ssh URL 在 CI 拉取同样需要 key——建议 CI 阶段统一用一把 deploy key（或 checkout 时对 ogagila 覆盖为 https 匿名）。

---

## 验收标准（本计划的硬指标）

```bash
# 三数据集 × 双引擎全流程迁移：编译 0 错误 + 测试 0 errors/failures
python3 -m pytest tests/regress/test_fastaas_migration.py -v -m fastaas_migration
# 覆盖：exam(136) + fastaas collected_sql(49) + ogagila(22)，py+ru 各一遍
```

辅助门禁：`cargo test -p fluxgauss` 全绿、`cargo fmt --check` 干净、clippy 改动文件零新增、既有 `test_demo_migration` 不回归。

---

## 阶段 A：现有问题修复（前置）

### Task 1: 三数据集 × 双引擎全流程摸底

**Files:**
- Create: `scripts/verify-migration.sh`（可复现摸底脚本，供本计划反复使用）

**Step 1: 写摸底脚本**

```bash
#!/usr/bin/env bash
# 三数据集 × 双引擎全流程摸底：convert → mvn compile → mvn test → 汇总
set -u
DATASETS=("exam" "fastaas" "ogagila")
ENGINES=("py" "ru")
for ds in "${DATASETS[@]}"; do for en in "${ENGINES[@]}"; do
  yaml="demo-project/fluxgauss_${ds}${en:+_$en}.yaml"   # 依实际文件名微调
  dest="dest_${ds}_${en}"
  echo "== $ds/$en =="
  # convert（py 需 OGSQL_BIN；ru 用 target/release/fluxgauss）
  # mvn compile → 统计唯一 file:[line,col] 错误数
  # mvn test → 统计 Errors/Failures
done; done
```

**Step 2: 运行摸底，输出问题清单**

对每个数据集×引擎，记录：compile 错误数、test errors/failures 数、首屏错误样例。**重点确认**：
- ogagila_ru 16 errors 的具体类别（是否与 exam 同根因——mock 默认值，还是新类别）
- fastaas（从未跑过）的编译/测试状态——尤其 **Rust 引擎读 GB18030 是否乱码**（若 compile 报错或生成物乱码，确认是编码问题）
- exam-py / ogagila-py（Python 引擎自动探测编码，应无编码问题，但可能有别的）

**Step 3: 问题清单贴进报告 + Commit**
```bash
git add scripts/verify-migration.sh
git commit -m "test: migration verification script for fastaas/exam/ogagila datasets"
```

### Task 2: 修复摸底发现的问题

按摸底结果分类修复。**已知待修**（至少这些）：

**2a. 跨包 String mock 默认值**（exam 1 error，ogagila 可能同类）
- 位置：`crates/fluxgauss/src/generate/test.rs` 的 `mock_cross_service_calls`（约 L563 `scalar_mock_value(ret)`）+ `scalar_mock_value`（L794-824，String → `"1"`）
- **方案 A（推荐，调用点用法感知）**：mock 生成时扫该包的 java_logic_lines，检测该 mock 调用的返回值是否流入 `LocalDate.parse`/`Date.valueOf`/`SimpleDateFormat`/`Timestamp.valueOf`——若是，mock 值用 ISO 日期字符串 `"2024-01-01"`（注意：不是 `"20240101"`，`LocalDate.parse` 是 ISO_LOCAL_DATE 要求 `yyyy-MM-dd`）
- 实现提示：mock 值经变量中转（如 `vRepurchaseJsDate = fncComGetday(...)` → `LocalDate.parse(String.valueOf(vRepurchaseJsDate))`），需要从 mock 调用行提取 LHS 变量名，再在 java_logic_lines 里查该变量是否流入 parse——中等复杂度数据流；若实现受阻，退而求其次用**调用表达式直接出现在 parse 参数内**的简单检测 + 变量名启发（在报告中说明取舍）
- 测试：新增 fixture 锁定（一个返回日期字符串的跨包函数 + 调用方把返回值喂给 LocalDate.parse → 生成的 ServiceTest 用 `"2024-01-01"` 而非 `"1"`）
- 参考：`column_mock_value_for_key`（test.rs L432-483）已有 `ends_with("date") → java.sql.Date.valueOf("2024-01-01")` 先例

**2b. ogagila_ru 16 errors 归类修复**（Task 1 摸底后按类处理；若是 mock 默认值同类 → 2a 一并覆盖；若新类别 → 单列修复 + 测试锚定）

**2c. Rust 引擎 GB18030 编码**（fastaas collected_sql）
- 定位：确认 Rust 读文件路径（grep `read_to_string`/`fs::read` in crates/fluxgauss，尤其 `_read_sql_file` 等价物）与 `config.encoding` 的使用点
- 修复：若 Rust 已支持 `encoding` 字段解码但未自动探测 → fastaas 配置显式加 `encoding: gb18030`（需确认 Rust 解码库支持 gb18030；若只支持 gbk/gb2312，评估 gb18030 兼容性）；若 Rust 完全不支持 → 评估加 `encoding_rs` 依赖或与 Python 侧对齐的自动探测
- ⚠️ 涉及新依赖需 Ask first（AGENTS.md §1）
- 测试：fastaas collected_sql 单一文件经 Rust 引擎转换后无乱码（golden 或断言）

**2d. 其他摸底发现问题**

**验收（每修一类）：** 相关数据集 × 引擎的 compile 0 错 + test 0 错；`cargo test -p fluxgauss` 全绿；golden 变更逐条说明。

**Commit 粒度：** 每类修复一个 commit（`fix(rust): ...`）。

---

## 阶段 B：submodule 基础设施

### Task 3: fastaas submodule 新增 + ogagila 更新

**Step 1: 新增 fastaas submodule（ssh URL）**
```bash
git submodule add git@github.com:NO3623/fastaas.git lib/fastaas
```
（若本地已有 lib/fastaas 残留，先清理）

**Step 2: 更新 ogagila 到远程 main**
```bash
git -C lib/ogagila fetch origin
git -C lib/ogagila checkout b472aca11d01e284169d5b559569a4d33199cdc4   # 远程 main 确切 commit
```
（fetch 后确认 b472aca1 仍是最新；若期间远程有移动，取最新 main）

**Step 3: 验证 submodule 就位**
```bash
git submodule status
# Expected: lib/fastaas 在新 commit；lib/ogagila 在 b472aca1
ls lib/fastaas/exam/清算拆分优化考题/基线代码/ | wc -l   # 136
ls lib/fastaas/exam/all_sq/collected_sql/collected_sql/ | wc -l   # 49
```

**Step 4: Commit**
```bash
git add .gitmodules lib/fastaas lib/ogagila
git commit -m "chore: add fastaas submodule + update ogagila to main"
```

### Task 4: 8 个 yaml 配置改指 submodule 相对路径 + 提交

| 配置 | sources 改指 |
|---|---|
| `fluxgauss_fastaas_py.yaml` / `_ru.yaml` | `/tmp/fastaas/exam/all_sq/collected_sql/collected_sql/*.sql` → `lib/fastaas/exam/all_sq/collected_sql/collected_sql/*.sql` |
| `fluxgauss_exam.yaml` / `_ru.yaml` | `/Users/c2j/Projects/Desktop_Projects/DOTA/DOTA_RI/exam/...` → `lib/fastaas/exam/清算拆分优化考题/基线代码/...` |
| `fluxgauss_ogagila_py.yaml` / `_ru.yaml` / `_py_v2.yaml` / `_ru_v2.yaml` | `/tmp/ogagila_src/...` → `lib/ogagila/sqls/...`（`\set` 清洗由 Task 6 的 harness 在运行时处理，见下方**定案**） |

**定案（ogagila 路径唯一方案，Task 4/5/6 一致）**：
- ogagila 配置 sources **直接指向 submodule 内路径** `lib/ogagila/sqls/{ddl,program,dw/ddl,dw/program}/*.sql`
- `\set`/`\echo` 元指令的清洗由 **`test_fastaas_migration.py` harness 在运行时完成**：转换前把 sources 指向的 SQL 内容经 `sed '/^\\set /d'` 清洗后写入临时目录，再把配置指向临时目录喂给引擎（不修改 submodule 文件、不依赖 build 产物、可复现）
- 因此 **不创建** `scripts/prepare-ogagila-sources.sh` 独立脚本（原 Task 5 取消，清洗逻辑并入 Task 6 harness）；`scripts/verify-migration.sh`（Task 1）对 ogagila 数据集同样内联清洗逻辑
- 布局已验证 1:1 匹配（`/tmp/ogagila_src` 即 `lib/ogagila/sqls/` + sed 清洗），故清洗后引擎行为与现状完全一致

**Step 1: 逐配置替换 sources 前缀**（相对路径，基准 REPO_ROOT）
**Step 2: 依 Task 2c 结论补 `encoding` 字段**（fastaas 配置 `encoding: gb18030`，若 Rust 支持）
**Step 3: 验证**：`test_demo_migration.py` 的 L0.0 `test_sources_exist` 逻辑（`REPO_ROOT / s`）对相对路径生效——逐个配置确认 sources 存在
**Step 4: Commit**（8 个 yaml 从 untracked → tracked）
```bash
git add demo-project/fluxgauss_{fastaas,exam,ogagila}*.yaml
git commit -m "config: repoint fastaas/exam/ogagila sources to submodule-relative paths"
```

### Task 5: （已并入 Task 6——ogagila \set 清洗由 harness 运行时处理，不设独立脚本）

### Task 6: `test_fastaas_migration.py`（三数据集 × 双引擎检查项）

**Files:**
- Create: `tests/regress/test_fastaas_migration.py`（镜像 test_demo_migration.py）

**Step 1: 结构镜像**（L0.0-L0.5 同构）：
- `DATASET_CONFIGS`：三数据集 × 双引擎 = 6 个 (dataset, engine, yaml, dest) 项：
  - exam/py, exam/ru, fastaas/py, fastaas/ru, ogagila/py, ogagila/ru
- 引擎调用：py 用 `converter/flux_gauss.py`（OGSQL_BIN）+ ru 用 `target/release/fluxgauss`（复用 `_find_ogsql`/`_find_rust_binary`，可 import 自 test_demo_migration 或复制——按 AGENTS.md §11 接缝原则，优先 import 复用）
- **ogagila 数据集 harness 预处理**：转换前读取 ogagila 配置的 sources（`lib/ogagila/sqls/...`），对每个 SQL 内容做 `sed '/^\\set /d'`（Python 侧用 `re.sub(r'(?m)^\\set .*$', '', ...)` 等价），写入 pytest tmp_path，把清洗后路径临时替换配置 sources 再喂引擎。与 Task 1 摸底脚本同逻辑。
- **阈值按数据集键控**：`DML_FLOOR = {"exam": {...}, "fastaas": {...}, "ogagila": {...}}`（py/ru 各值）——摸底 Task 1 确定各数据集 DML 数量后填入
- marker：`@pytest.mark.fastaas_migration`（pyproject.toml 注册，与 demo_migration 并列）

**Step 2: 全绿验收**（本计划硬指标）
```bash
python3 -m pytest tests/regress/test_fastaas_migration.py -v -m fastaas_migration
# Expected: 三数据集 × 双引擎全部 PASSED（exam/py、exam/ru、fastaas/py、fastaas/ru、ogagila/py、ogagila/ru）
```

**Step 3: QA 逐项确认（可执行）**
```bash
# 3.1 marker 已注册
grep -n "fastaas_migration" pyproject.toml
# Expected: [tool.pytest.ini_options] 的 markers 列表含 "fastaas_migration"

# 3.2 三数据集 × 双引擎 = 6 项配置齐全
grep -cE '^\s*\("(exam|fastaas|ogagila)", "(py|ru)"' tests/regress/test_fastaas_migration.py
# Expected: 6

# 3.3 ogagila harness 清洗逻辑存在（\set 剥离）
grep -n "\\\\set" tests/regress/test_fastaas_migration.py
# Expected: 命中清洗实现处

# 3.4 运行确认全绿（上面 Step 2 命令）
```

**Step 4: Commit**

### Task 7: CI 集成

**Files:**
- Modify: `.github/workflows/ci.yml`

**Step 1: checkout 加 submodules**
所有需要 submodule 的 job（demo-migration 或新增 fastaas-migration job）的 checkout@v4 加：
```yaml
with:
  submodules: recursive
  # fastaas PRIVATE → 需 ssh deploy key；见 §前置条件
```
浅克隆：`git fetch --depth=1` 或 checkout action 的 fetch-depth 对 submodule 的深度控制（fastaas .git 309M，浅克隆避免 CI 超时/超容量）

**Step 2: ssh deploy key 配置**
CI job 加 setup 步骤：把 `${{ secrets.FASTAAS_DEPLOY_KEY }}` 写入 `~/.ssh/`（或 `ssh-add`），使 checkout submodule 能拉 PRIVATE fastaas。ogagila（PUBLIC + ssh URL）同 key 或覆盖 https——执行时定。

**Step 3: 新 job 或并入 demo-migration**
- 方案：新增 `fastaas-migration` job（needs: build-ogsql-linux，跑 `pytest tests/regress/test_fastaas_migration.py -m fastaas_migration`），与 demo-migration 并列；或并入 demo-migration job（一步到位，省 job 开销）。执行时定，报告说明。
- ⚠️ 注意：exam 数据集经 submodule 后**不再依赖仓外 DOTA 路径**——CI 可跑。这是本计划的根本价值。

**Step 4: QA 逐项确认（可执行）**
```bash
# 4.1 yaml 语法有效 + jobs 结构
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(sorted(d['jobs'].keys()))"
# Expected: 含新增 fastaas-migration（或并入的 demo-migration），原有 4 jobs 仍在

# 4.2 submodules: recursive 已加到目标 job 的 checkout
grep -n "submodules" .github/workflows/ci.yml
# Expected: 命中目标 job 的 checkout@v4 with: submodules: recursive（含显式 fetch-depth 浅克隆配置）

# 4.3 ssh deploy key 接线存在
grep -n "FASTAAS_DEPLOY_KEY\|ssh-add\|SSH_AUTH_SOCK" .github/workflows/ci.yml
# Expected: 命中 setup 步骤（写入 ~/.ssh 或 ssh-agent）

# 4.4 新 job 调用检查项
grep -n "test_fastaas_migration" .github/workflows/ci.yml
# Expected: 命中 pytest -m fastaas_migration 调用行

# 4.5 既有 CI 不回归
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); assert 'test-rust' in d['jobs'] and 'test-python' in d['jobs'] and 'demo-migration' in d['jobs'], '既有 job 缺失'"
# Expected: 无报错（既有 3+1 jobs 保留）
```

**Step 5: Commit**

---

## 阶段 D：收尾

### Task 8: AGENTS.md 更新 + 收尾自检

**Step 1: AGENTS.md 更新**
- §3 回归基线：补 fastaas/exam/ogagila 数据集说明 + `test_fastaas_migration` 门禁 + submodule 前置（deploy key）+ ogagila 清洗由 harness 处理
- §9 完成标准：加 `pytest tests/regress/test_fastaas_migration.py -m fastaas_migration` 必过项
- §10 命令：补该检查项命令

**Step 2: QA 逐项确认（可执行）**
```bash
# 2.1 AGENTS.md 新增门禁条目
grep -n "fastaas_migration" AGENTS.md
# Expected: 至少 3 处（§3 验证链、§9 完成标准、§10 命令）

# 2.2 marker 注册与 pytest 收集
python3 -m pytest tests/regress/test_fastaas_migration.py --collect-only -q -m fastaas_migration 2>&1 | tail -3
# Expected: 收集到 6（dataset×engine）或更多用例，无 collection error

# 2.3 全量门禁复跑（最终判据）
python3 -m pytest tests/regress/test_fastaas_migration.py -v -m fastaas_migration
# Expected: 三数据集 × 双引擎全部 PASSED
python3 -m pytest tests/regress/test_demo_migration.py -m demo_migration
# Expected: 既有门禁不回归（ru 全绿；py 本机因 ogsql 缺失 SKIPPED 可接受）

# 2.4 Rust 侧门禁
cargo test -p fluxgauss 2>&1 | grep -E "^test result"   # Expected: lib + regress 全绿
cargo fmt --check 2>&1 | grep -c "^Diff"                 # Expected: 0
```

**Step 3: Commit**

---

## 关键风险与缓解

| 风险 | 缓解 |
|---|---|
| **fastaas PRIVATE**：CI 无法匿名克隆 | §前置条件 deploy key（用户配合）；计划 Task 7 落实 |
| Rust 引擎不支持 gb18030 解码 | Task 2c 先验证；若需新依赖走 Ask first |
| ogagila 16 errors 是未知类别 | Task 1 摸底归类后再修（Task 2b） |
| 检查项阈值（DML_FLOOR）未知 | Task 1 摸底确定 |
| submodule 309M 克隆成本 | CI 浅克隆 |
| exam 检查项上后仍可能有隐蔽问题 | 阶段 A 摸底+修复全绿后才上检查项（用户决策 4） |

## 范围外

- Python 引擎 `_tracker` 异常修复（既有 parity 阻塞，独立问题）
- Rust CLI `-o -s` 缺陷（既有，非本计划）
- 各数据集集成测试（`mvn verify -Pintegration` 需真实 DB，不在本计划）
- `docs/plans/2026-08-28-rust-unqualified-function-resolution.md` 遗留的 follow-up（同包 OUT 不对称、嵌套 range loop）

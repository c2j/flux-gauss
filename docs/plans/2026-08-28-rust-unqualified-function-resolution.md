# Rust 引擎：裸函数调用跨包解析 + TOBEFIX 标记与报告警告 — 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Rust 引擎正确解析赋值/条件表达式中的**非包限定（裸名）用户函数调用**（如 `fnc_com_getday(...)`），删除 `func_`/`fn_` 前缀启发式；对无法解析的名称在生成代码中注入 `/* TOBEFIX ... */` 标记注释，并在迁移报告（`unresolved_calls` 段落）中给出警告。

**Architecture:** 根因在 `crates/fluxgauss/src/expr.rs` 的 `function_call_to_java` 单名分支（L2106-2112）——只查同包兄弟 `package_proc_params`，从不查 pipeline 已构建好的全局跨包映射 `all_proc_params`（pipeline.rs:560-577, 596）。修复分三条线：① 全局映射升级为 arity-aware 结构并接入单名分支（解析成功路径）；② 删除 `resolve_column_ref` 的 `func_`/`fn_` 前缀门，改为"变量优先、注册表判定"（消除前缀硬编码）；③ 兜底注释 `TODO` → `TOBEFIX`，并绕过 `coerce_for_type` 的注释吞噬（expr.rs:356-384），让标记在生成物中可见、在报告中留痕（复用 `UnresolvedCall` + 既有报告段落）。

**Tech Stack:** Rust（crates/fluxgauss/）、ogsql-parser AST、cargo test 回归（tests/regress.rs golden 机制）、Python 引擎（converter/flux_gauss.py）仅作 parity 参照。

---

## 背景与问题定位（已核实的证据）

### 触发案例

`demo-project/fluxgauss_exam-ru.yaml`（sources 含 `0_SIMPLIFIED_fnc_com_getday.sql` 与 `0_SIMPLIFIED_fnc_com_if_deal_date.sql`）生成 `dest_exam-ru/.../_2FncGetPurchaseJsDaysService.java`，SQL 第 29/32/37/41 行的 4 处裸函数调用全部折叠为类型默认值：

| SQL 行 | 原调用 | 生成行（现状，错误） |
|---|---|---|
| 29 | `fnc_com_if_deal_date(substr(...), v_repurchase_date)` | L40 `vTradedateFlag = 0L;` |
| 32 | `fnc_com_getday(substr(...), v_repurchase_date, 1)` | L42 `vRepurchaseDate = null;` |
| 37 | `fnc_com_getday(substr(...), p_i_date, 1)` | L44 `vPurchaseJsDate = null;` |
| 41 | `fnc_com_getday(substr(...), v_repurchase_date, 1)` | L45 `vRepurchaseJsDate = null;` |

对照：定义文件自身被正确转换（`_0SimplifiedFncComGetdayService.fncComGetday(String,String,long)`）；**点号限定**调用（`pkg_log.inst_log`）与**语句级**裸过程调用（`prc_com_get_seat_commision(...)`）均正确接线。规律：**表达式位置的裸用户函数调用是解析死角**。

### 根因链

```
赋值语句 statement.rs:2315 → assignment_to_java (expr.rs:66, proc 只读 &ProcedureInfo)
  → expr_to_java_impl (expr.rs:751) → function_call_to_java (expr.rs:1695)
    → 白名单 match (1726-2078)：FNC_COM_GETDAY 不在内置函数表
    → 兜底 _ => 分支 (2079-2176)：
        点号名(≥2段, 2081-2105) → 跨包 service 调用 ✅（已有）
        单名(1段, 2106-2112) → 只查 package_proc_params（同包兄弟）
                              → 从不查 all_proc_params（全局映射）❌ ← 根因
    → 2166-2175: /* TODO: implement ... */ null
  → coerce_for_type (192, 356-384): is_nullish_java_expr(ends_with("null")&&contains("/*"))
      → 注释被剥掉，String→null / Long→0L  ← 为什么用户看不到注释
```

### 为什么前缀启发式是坏设计（本计划要消除的）

`resolve_column_ref`（expr.rs:977-1003）是唯一查询 `all_proc_params` 的地方，却被 `name_lower.starts_with("func_") || starts_with("fn_")` 卡住：
- **假阴性**：`fnc_` 前缀函数（本项目实际约定）永远解析不了；
- **假阳性**：名为 `func_status` 的变量会被误判为函数；
- **把命名规范硬编码进编译器**：任何不遵循约定的真实代码都会静默出错。

正确做法：**注册表驱动**——查得到就是函数，查不到才是变量。PL/SQL 的遮蔽语义（局部变量遮蔽同名函数）由"变量查询永远先于函数查询"的解析顺序保证，不需要前缀参与。

---

## 设计决策

### D1：全局映射升级为 arity-aware（pipeline.rs:560-577）

现状 `HashMap<String, String>`（方法名 → service 变量名），`or_insert_with` 静默 first-wins（两个包定义同名函数时任意取一个，是既有隐患）。

改为：

```rust
// types.rs 新增
#[derive(Debug, Clone)]
pub struct GlobalFnEntry {
    pub svc_var: String,      // 如 "_0SimplifiedFncComGetdayService"
    pub package: String,      // 定义所在包（用于同包排除，大小写不敏感比较）
    pub params: Vec<Parameter>, // 用于实参个数匹配
}

// types.rs:219 字段类型变更
pub all_proc_params: HashMap<String, Vec<GlobalFnEntry>>,  // 方法名 → 候选列表
```

构建（pipeline.rs:560-577）：`or_insert_with` 改为 `entry().or_default().push(...)`，保留全部候选。

**影响面核验**：`all_proc_params` 消费者仅 3 处——types.rs:273（初始化）、pipeline.rs:596（赋值）、expr.rs:984（唯一读取点，随本计划一并改造）。

### D2：单名分支接入全局解析（expr.rs:2106-2112）

在 `package_proc_params` 未命中后追加：

```
同包兄弟 package_proc_params（已有，L2108，按 arity 匹配重载）
  → 全局 all_proc_params（新增）：
      按 arity 过滤（params.len() == 实参个数）
      + 排除定义包 == 当前 proc.package 的候选（同包已由上一步覆盖）
      唯一命中 → svcVar.method(args)，复用 L2154-2164 输出逻辑
      零命中   → 落 TOBEFIX 兜底（见 D4）
      多个命中 → 落 TOBEFIX 兜底 + hint 列出候选（不静默 first-wins）
```

同时把 L2154-2164 的 out 参数 `.get()` 包裹逻辑提取为 helper `emit_cross_pkg_call(svc, method, jargs, proc) -> String`，点号分支与单名分支共用（DRY）。

**注入机制无需额外改动**：`discover_cross_service_refs`（analyze.rs:301-357）用正则 `(\w+Service)\.` 事后扫描 `java_logic_lines` 反推 service 注入——新分支只要输出 `svcVar.method(...)` 格式即被自动接线（已验证：现有 `splitTradeLogService.instLog(...)` 走的就是这条路）。

### D3：删除前缀启发式（expr.rs:977-1003）

`resolve_column_ref` 裸标识符处理顺序改为（不再猜前缀）：

```
参数 → 局部变量 → 包变量（既有查询，按名匹配）
  → 同包零参函数（package_proc_params 且 arity==0）→ this.foo()
  → 全局零参函数（all_proc_params 且 arity==0，排除同包）→ svcVar.foo()
  → 兜底按变量名处理（返回 camel，现状不变）+ TOBEFIX 提示
```

注：裸标识符中"未知函数"与"未知变量"在运行时语义上同为空值路径，但按用户要求两者都要有 TOBEFIX 标记与报告警告（见 D4/D5）。

### D4：TOBEFIX 标记格式与存活机制（expr.rs）

**格式**（替换 2168-2175 与 997 两处 `/* TODO */` 兜底）：

```rust
format!(
    "/* TOBEFIX: unresolved fn {func}({args}) - pkg={pkg}, caller={file}:{proc}{ambig} */ null",
    // ambig: 多候选时追加 ", candidates=[svcA, svcB]"
)
```

**存活机制**：`coerce_for_type` 的 nullish 分支（expr.rs:356-384）当前把 `/* ... */ null` 整体剥成裸 `null`（`is_nullish_java_expr` L10-14：`ends_with("null") && contains("/*")`）——这正是今天注释消失的原因。修改该分支：返回类型默认值（String→null、Long→0L、BigDecimal→ZERO 等，保持原逻辑不变以防基础类型编译破坏）之前，若 `trimmed` 含 `/* TOBEFIX`，提取注释前缀拼回：

```rust
if is_nullish_java_expr(trimmed) {
    let default = /* 既有类型默认值逻辑（358-383，不动） */;
    if trimmed.contains("/* TOBEFIX") {
        if let Some(end) = trimmed.find("*/") {
            return format!("{} {}", &trimmed[..=end], default);
        }
    }
    return default;
}
```

产出形如 `vPurchaseJsDate = /* TOBEFIX: unresolved fn fncComGetday(...) - pkg=?, caller=... */ null;` —— 合法 Java（可编译）、标记可见、运行时值为 null。**`is_nullish_java_expr` 本身不改**（仍识别该表达式，只是 coerce 决定保留注释）。

### D5：报告警告（复用 UnresolvedCall + 既有段落）

- `UnresolvedCall` 结构已存在（types.rs:405-411：caller/callee/caller_file/args/hint）。
- 报告已存在 `## ⚠️ 未解析的跨包调用` 段落（report.rs:104-111）与 console 输出（main.rs:221-226）。
- expr 层是纯函数（`proc: &ProcedureInfo`，无 ctx），无法就地记录。采用**事后扫描**（镜像 `discover_cross_service_refs` 模式）：analyze 循环之后、`ctx.unresolved_calls` 组装之前（pipeline.rs:582-603 循环末尾），扫描每个 `proc.java_logic_lines`，凡含 `/* TOBEFIX` 的行，用正则 `TOBEFIX: unresolved fn (\w+)\((.*?)\) - pkg=` 提取 callee/args，push `UnresolvedCall { hint: "TOBEFIX: 函数/名称未解析（定义包不在 sources 或跨包同名冲突），需人工确认" }`。

这样**零报告渲染改动**，单文件/多文件、赋值/条件/参数/返回值所有表达式路径统一覆盖。

### D6：同包函数（`fnc_` 前缀、无点号）本包调用

`fnc_com_getday` 若与调用者在同一包：走既有 `package_proc_params` 分支（L2108）→ `this.fncComGetday(...)`，本计划不改变该路径（D2 只新增跨包分支）。

---

## 回归测试设施说明（先读再写测试）

- `tests/regress.rs:233 regress_golden_compare`：对 `tests/regress/fixtures/` 下每个 `.sql` **逐个单文件**跑 pipeline，与 `tests/regress/golden/ru/{pkg}/` 下 `.golden` 比对。`REGEN_RUST_GOLDEN=1` 可再生成。
- `tests/regress.rs:167 run_multi_file_conversion`：多文件合并转换，**目前硬编码只读 `BigfundService.java`**（issue_70 专用）。本计划新增一个泛化 helper，不动既有函数。
- 既有覆盖：`issue_70_fnc_a/b`（独立函数转换）、`issue_71_parity_caller`（未解析**点号**调用 → `// CALL PKG_PLOG.inst_log(...)` 约定，golden/ru/issue_71_parity_caller/Service.java.golden:23）。**裸函数表达式解析无任何覆盖**——正是本计划补的空白。

---

## 任务清单（TDD，一次循环锁一个行为）

### Task 1: 新增 fixture（callee + caller 两份文件）

**Files:**
- Create: `tests/regress/fixtures/issue_79_unqualified_fn_callee.sql`
- Create: `tests/regress/fixtures/issue_79_unqualified_fn_caller.sql`

**Step 1: 写 fixture（直接复刻 fastaas 真实形态）**

`issue_79_unqualified_fn_callee.sql`（无 schema 前缀，文件名即包名）：
```sql
CREATE OR REPLACE FUNCTION fnc_com_getday(p_i_scdm VARCHAR2, p_i_date VARCHAR2, p_i_offset NUMBER)
RETURN VARCHAR2 IS
BEGIN
  RETURN to_char(to_date(p_i_date, 'yyyymmdd') + p_i_offset, 'yyyymmdd');
END;
/
```

`issue_79_unqualified_fn_caller.sql`（无 schema 前缀，文件名即包名）：
```sql
CREATE OR REPLACE FUNCTION FNC_GET_PURCHASE_JS_DAYS (p_i_date VARCHAR2, p_i_security_id VARCHAR2)
  RETURN VARCHAR2 IS
  v_purchase_js_date VARCHAR2(8);
BEGIN
  v_purchase_js_date := fnc_com_getday(substr(p_i_security_id, 3, 3), p_i_date, 1);
  RETURN v_purchase_js_date;
END;
/
```

**Step 2: 跑当前回归，确认 caller 的 golden 体现错误行为**

Run: `REGEN_RUST_GOLDEN=1 cargo test --test regress regress_golden_compare`
Expected: 生成两个 golden 目录。人工检查 `golden/ru/issue_79_unqualified_fn_caller/Service.java.golden`，确认 `vPurchaseJsDate = null;`（错误行为被锁定，作为修复前的 Red 基线）。

**Step 3: Commit**

```bash
git add tests/regress/fixtures/
git commit -m "test: add unqualified fn cross-package fixtures (issue_79)"
```

### Task 2: 多文件解析成功测试（Red，先行失败）

**Files:**
- Modify: `crates/fluxgauss/tests/regress.rs`（新增泛化多文件 helper + 新 #[test]）

**Step 1: 新增 helper `run_multi_file_services(sql_files, out_dir) -> HashMap<String,String>`**（镜像 167-186，区别：读取目录下全部 `*Service.java`，key 为文件名；不动既有 `run_multi_file_conversion`）

**Step 2: 写失败测试**

```rust
#[test]
fn issue_79_unqualified_cross_pkg_fn_resolves() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files = vec![
        fixtures.join("issue_79_unqualified_fn_callee.sql"),
        fixtures.join("issue_79_unqualified_fn_caller.sql"),
    ];
    let tmp = tempfile::tempdir().expect("tempdir");
    let files = run_multi_file_services(&sql_files, &tmp.path().join("dest"));
    let caller = files.get("Issue79UnqualifiedFnCallerService.java").expect("caller service");
    assert!(
        caller.contains("issue79UnqualifiedFnCalleeService.fncComGetday("),
        "unqualified fn must resolve to cross-pkg service call, got:\n{}", caller
    );
    assert!(caller.contains("issue79UnqualifiedFnCalleeService"), "service must be injected");
    assert!(!caller.contains("TOBEFIX"), "resolved call must not carry TOBEFIX marker");
}
```

**Step 3: 运行确认失败**

Run: `cargo test --test regress issue_79_unqualified_cross_pkg_fn_resolves`
Expected: FAIL（当前生成 `= null;`，无 service 调用）。

**Step 4: Commit**

```bash
git commit -am "test: unqualified cross-package fn resolution (failing red)"
```

### Task 3: `all_proc_params` 升级为 arity-aware 结构

**Files:**
- Modify: `crates/fluxgauss/src/types.rs`（新增 `GlobalFnEntry`；`all_proc_params` 字段类型 L219 与初始化 L273）
- Modify: `crates/fluxgauss/src/pipeline.rs:560-577`（构建逻辑：push 全部候选 + 记录 package/params）
- Modify: `crates/fluxgauss/src/expr.rs:984`（消费者临时适配为 `Vec<GlobalFnEntry>` 的 arity==0 过滤，保持既有零参函数行为）

**Step 1: 写实现（types.rs）** —— `GlobalFnEntry` 结构 + 字段类型 `HashMap<String, Vec<GlobalFnEntry>>`

**Step 2: 改 pipeline 构建** —— `entry(method_name).or_default().push(GlobalFnEntry{ svc_var, package: svc_pkg, params: p.parameters.clone() })`

**Step 3: 编译 + 全回归（确认无行为变化——此步只是数据结构升级）**

Run: `cargo build 2>&1 | grep -E "error|warning" | head; cargo test --test regress regress_golden_compare`
Expected: 编译零 error；golden 对比全 PASS（结构升级不应改变任何生成物）。

**Step 4: Commit**

```bash
git commit -am "refactor: all_proc_params → arity-aware GlobalFnEntry index"
```

### Task 4: `function_call_to_java` 单名分支接入全局解析

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`（单名分支 2106-2112；提取 `emit_cross_pkg_call` helper 并替换 2154-2164）

**Step 1: 提取 helper**

```rust
fn emit_cross_pkg_call(cross_pkg_svc: &str, method: &str, jargs: Vec<String>, proc: &ProcedureInfo) -> String {
    let x_args: Vec<String> = jargs.iter().map(|a| {
        if is_out_param(a, proc) || proc.out_local_vars.iter().any(|(k, _)| k.to_lowercase().replace("_", "") == a.to_lowercase().replace("_", "")) {
            format!("{}.get()", a)
        } else {
            a.clone()
        }
    }).collect();
    format!("{}.{}({})", cross_pkg_svc, method, x_args.join(", "))
}
```
点号分支改用 helper（行为不变）。

**Step 2: 单名分支追加全局查询**

```rust
} else if name_parts.len() == 1 {
    let method_name = crate::naming::java_method_name(name_parts[0]);
    if proc.package_proc_params.contains_key(&method_name) {
        (method_name, true, String::new())
    } else if let Some(entries) = proc.all_proc_params.get(&method_name) {
        let pkg_lower = proc.package.to_lowercase();
        let cross: Vec<&GlobalFnEntry> = entries.iter()
            .filter(|e| e.package.to_lowercase() != pkg_lower)
            .filter(|e| e.params.len() == jargs.len())
            .collect();
        match cross.len() {
            1 => (method_name, false, cross[0].svc_var.clone()),
            _ => (String::new(), false, String::new()), // 0 或冲突 → TOBEFIX 兜底
        }
    } else {
        (String::new(), false, String::new())
    }
}
```
（冲突场景的候选列表经 D4 的 ambig 段注入 TOBEFIX 注释——需在兜底处能拿到 candidates，见 Task 5 说明。）

**Step 3: 跑 Task 2 的失败测试**

Run: `cargo test --test regress issue_79_unqualified_cross_pkg_fn_resolves`
Expected: PASS（caller 生成 `issue79UnqualifiedFnCalleeService.fncComGetday(...)` 且注入成功——注入由 `discover_cross_service_refs` 自动完成）。

**Step 4: 全回归**

Run: `cargo test`
Expected: 既有 golden 全部 PASS（点号分支行为未变；`issue_79_unqualified_fn_caller` 单文件 golden 仍是旧错误形态——该 golden 由 Task 5/6 统一再生成）。

**Step 5: Commit**

```bash
git commit -am "feat: resolve unqualified cross-package function calls in expressions"
```

### Task 5: 删除前缀启发式 + 裸标识符注册表判定

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`（resolve_column_ref 977-1003）

**Step 1: 重写分支**

```rust
// 删掉 977 的 func_/fn_ 前缀门；按解析顺序：
let method_name = crate::naming::java_method_name(name);
if proc.package_proc_params.contains_key(&method_name)
    && proc.package_proc_params[&method_name].iter().any(|ps| ps.is_empty())
{
    return format!("this.{}()", method_name);
}
if let Some(entries) = proc.all_proc_params.get(&method_name) {
    let pkg_lower = proc.package.to_lowercase();
    let cross: Vec<&GlobalFnEntry> = entries.iter()
        .filter(|e| e.package.to_lowercase() != pkg_lower && e.params.is_empty())
        .collect();
    if let Some(e) = cross.first() {
        return format!("{}.{}()", e.svc_var, method_name);
    }
}
// 兜底：按变量处理（返回 camel，现状），并标注 TOBEFIX（见 D4 格式）
```

**Step 2: 单元测试（expr.rs 内 `#[cfg(test)]` 模块，现有 16 个测试同位置）**

新增用例：
- `fnc_` 前缀裸零参函数 → 解析为 `svcVar.fncXxx()`（删除前缀门后行为）
- 同名局部变量遮蔽同名零参函数 → 按变量处理（变量优先顺序）
- 未知裸名 → 保持变量兜底 + TOBEFIX 注释

**Step 3: 运行**

Run: `cargo test expr::`
Expected: 新用例 PASS；既有 16 个 expr 测试 PASS。

**Step 4: Commit**

```bash
git commit -am "feat: registry-driven bare identifier resolution (drop fn_/func_ prefix heuristics)"
```

### Task 6: TOBEFIX 标记 + coerce 存活 + 报告警告收集

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`（兜底格式 2168-2175 与 997；coerce nullish 分支 356-384；D2 冲突场景的 candidates 注入）
- Modify: `crates/fluxgauss/src/pipeline.rs`（analyze 循环后扫描 java_logic_lines 收集 TOBEFIX → `ctx.unresolved_calls`）
- Modify: `crates/fluxgauss/src/context.rs`（若需，确认 StmtContext/AnalysisContext 无新字段——预期复用现有 unresolved_calls）

**Step 1: 兜底格式改 TOBEFIX**（两处：`function_call_to_java` L2168-2175、`resolve_column_ref` L996-1002；冲突场景追加 `candidates=[...]`）

**Step 2: coerce 存活机制**（D4 代码块：nullish 分支保留 `/* TOBEFIX */` 前缀）

**Step 3: pipeline 后扫描收集**（D5 代码块：正则提取 callee/args → push `UnresolvedCall`，hint 含 "TOBEFIX"）

**Step 4: 单元测试（report.rs `#[cfg(test)]` 模块）**

新增用例：构造含 TOBEFIX 的 ConversionReport，断言 `to_markdown()` 输出含 `TOBEFIX` 与 `⚠️` 段落（锁定报告警告行为）。

**Step 5: 运行**

Run: `cargo test`
Expected: 全部 PASS；`issue_79_unqualified_fn_caller` 单文件 golden 变更为含 TOBEFIX 注释的形态。

**Step 6: 再生成并审阅 golden**

Run: `REGEN_RUST_GOLDEN=1 cargo test --test regress regress_golden_compare`
人工审阅 `golden/ru/issue_79_unqualified_fn_caller/Service.java.golden`：确认
- `vPurchaseJsDate = /* TOBEFIX: unresolved fn fncComGetday(substr(...), pIDate, 1) - pkg=?, caller=... */ null;`
- 无裸 `= null;`（注释可见）
其余既有 golden 的 diff 应仅限"TODO→TOBEFIX"字样（若有 TODO-null 存活场景）——逐条说明后再提交。

**Step 7: Commit**

```bash
git commit -am "feat: TOBEFIX marker survives coercion + report warnings for unresolved names"
```

### Task 7: 端到端验证（仓内可复现的 mini-e2e + 完整验证链）

> **可复现性约束**：`demo-project/fluxgauss_exam-ru.yaml` 的 `sources` 指向仓外绝对路径（`/Users/c2j/Projects/Desktop_Projects/DOTA/...`），其他机器无法执行。因此本计划的端到端门禁**必须基于仓内 fixtures**（即 Task 1 新增的 `issue_79_*.sql`），经真实 CLI 转换后 `mvn compile` 验证——与回归测试互补（回归锁 golden，此处验证真实 CLI 全链路 + 编译产物）。

**Step 1: 用仓内 fixtures 走真实 CLI**

```bash
rm -rf /tmp/issue79_e2e
cargo run --bin fluxgauss -- -o /tmp/issue79_e2e -s \
  tests/regress/fixtures/issue_79_unqualified_fn_callee.sql \
  tests/regress/fixtures/issue_79_unqualified_fn_caller.sql
```

**Step 2: 断言修复点**

检查 `/tmp/issue79_e2e/src/main/java/com/example/demo/service/Issue79UnqualifiedFnCallerService.java`：
- 赋值行 → `vPurchaseJsDate = issue79UnqualifiedFnCalleeService.fncComGetday(...);`
- 构造函数注入 `issue79UnqualifiedFnCalleeService`
- 全文件无 `TOBEFIX` 与裸 `= null;`

Run: `grep -n "TOBEFIX\|= null;\|fncComGetday" /tmp/issue79_e2e/src/main/java/com/example/demo/service/Issue79UnqualifiedFnCallerService.java`

**Step 3: 编译 + 单测**

Run: `cd /tmp/issue79_e2e && mvn compile && mvn test`
Expected: BUILD SUCCESS，测试通过。

**Step 4: Rust 门禁（按 AGENTS.md「零新增 warning」策略，非全仓 `-D warnings`）**

Run:
```bash
cargo fmt --check
cargo clippy --all-targets 2>&1 | tee /tmp/clippy_after.txt
# 与改动前基线对比：改动涉及文件（src/expr.rs, src/pipeline.rs, src/types.rs, src/analyze.rs, tests/regress.rs）不得出现新增 warning
grep -E "warning.*(expr\.rs|pipeline\.rs|types\.rs|analyze\.rs|regress\.rs)" /tmp/clippy_after.txt || echo "NO NEW WARNINGS in changed files"
cargo test
```
Expected: fmt 干净；改动文件无新增 warning（存量债务不计入本计划）；`cargo test` 全绿。

**Step 5: 可选（仅本机，仓外数据集）**

若 DOTA 数据集存在，可额外跑 exam 配置验证 `_2FncGetPurchaseJsDaysService` 的 4 处调用全部解析（L40/42/44/45 → service 调用）——此步**不是门禁**，缺失数据集时跳过。

**Step 6: Commit**

```bash
git add -A
git commit -m "test: e2e — unqualified cross-package fn resolves via real CLI + mvn compile"
```

### Task 8: 收尾自检（AGENTS.md §9 完成标准）

- [ ] 新行为有失败→通过的测试（Task 2 Red→Green；Task 5/6 单元测试）
- [ ] 未删除、跳过、改写人类已有测试
- [ ] 门禁已跑：`cargo fmt --check` + `cargo clippy`（改动文件零新增 warning）+ `cargo test` + mini-e2e `mvn compile`/`mvn test`
- [ ] golden 更新均有 diff 说明（TODO→TOBEFIX 字样、issue_79 新增目录）
- [ ] 无草稿、调试输出带入
- [ ] 汇报含：改动文件清单 + 实际命令与结果
- [ ] parity 验证**不**在本计划门禁内（见"依赖与后续"#1，需先修 Python `_tracker` 阻塞）

---

## 依赖与后续（本计划范围外，需单独立项）

1. **Python 引擎 parity 阻塞（前置依赖）**：`flux_gauss.py` 在分析 `2_FNC_GET_PURCHASE_JS_DAYS.sql` 时抛 `'dict' object has no attribute '_tracker'`（整函数变 stub）。**本计划不含**该修复，因此 parity 验证**不进入本计划门禁**。修复后按 CI 既有命令跑 parity：`python3 -m pytest tests/regress/test_parity.py -v --tb=short -m parity`（需 `target/release/fluxgauss` 二进制与 ogsql；needs 标记由 CI workflow 控制）。parity 比对内容：新增的 `issue_79_*` 双引擎 golden（golden/py vs golden/ru）。
2. **增量构建依赖追踪**：Rust 侧跨包调用是否触发调用方再生成（Python 侧有"传递依赖"机制）需单独确认。
3. **fastaas 回归集**：`fnc_com_get_marketrate` 等 20+ 处同类静默折叠会被本计划点亮为真实调用，`fluxgauss_fastaas_*.yaml` 相关 dest 的回归基线需随后更新（预期行为，逐条说明）。

## 关键文件与行号索引（实施时对照）

| 位置 | 说明 |
|---|---|
| `crates/fluxgauss/src/expr.rs:66` | `assignment_to_java`（proc 只读） |
| `crates/fluxgauss/src/expr.rs:1695` | `function_call_to_java` 入口 |
| `crates/fluxgauss/src/expr.rs:2079-2176` | 兜底分支（点号 2081-2105 / 单名 2106-2112 / 跨包输出 2154-2164 / TODO-null 2166-2175） |
| `crates/fluxgauss/src/expr.rs:977-1003` | `resolve_column_ref` 前缀门 + 全局查询 |
| `crates/fluxgauss/src/expr.rs:8-14` | `is_nullish_java_expr`（**不改**） |
| `crates/fluxgauss/src/expr.rs:329-408` | `coerce_for_type`（356-384 为 nullish 分支，改存活逻辑） |
| `crates/fluxgauss/src/pipeline.rs:560-577` | `global_proc_map` 构建（升级 arity-aware） |
| `crates/fluxgauss/src/pipeline.rs:596` | `all_proc_params` 注入 |
| `crates/fluxgauss/src/pipeline.rs:582-603` | analyze 循环（TOBEFIX 后扫描收集点） |
| `crates/fluxgauss/src/types.rs:219, 273` | `all_proc_params` 字段与初始化 |
| `crates/fluxgauss/src/types.rs:405-411` | `UnresolvedCall` 结构 |
| `crates/fluxgauss/src/analyze.rs:301-357` | `discover_cross_service_refs`（注入机制，参照其扫描模式） |
| `crates/fluxgauss/src/report.rs:104-111` | 既有 `## ⚠️ 未解析的跨包调用` 段落（零改动复用） |
| `crates/fluxgauss/src/statement.rs:1802-1808` | 既有 `// CALL` 未解析记录（参照 hint 文案风格） |
| `tests/regress.rs:167, 233` | 多文件 helper / golden 对比（Task 2 新增泛化 helper） |
| `tests/regress/fixtures/issue_70_*` | 同 schema 合并转换参照（Task 2 模式） |

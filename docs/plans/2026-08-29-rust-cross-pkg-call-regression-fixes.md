# Rust 引擎跨包调用修复（#107 回归）+ 全流程迁移门禁固化 — 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 PR #107（commit `0c2dd13`）在 main 上引入的 4 类生成物编译回归，并把「demo 全流程迁移零编译错误」固化为生成逻辑改动的强制完成门禁，杜绝同类回归再次漏网。

**Architecture:** #107 让 Rust 引擎为裸函数调用生成真实跨包 service 调用（此前折叠为 `null`/`0`/`BigDecimal.ZERO`）。解析本身正确且 service 注入生效，但**新进入生成管线的真实调用暴露了四层下游缺口**：跨包路径缺实参/返回值强转（同包自调用早已具备）、callee OUT 参数未按 `AtomicReference` 语义提升、声明段调用不触发注入扫描、以及循环计数器注册改动导致同名计数器复用时声明丢失。修复策略：让跨包路径与既有同包/语句级路径**行为对称**（复用 `coerce_arg_to_type` 与 statement.rs 已验证的 promote 逻辑），并把验收标准从「手搓 mini-e2e」升级为「`test_demo_migration` 零编译错误」。

**Tech Stack:** Rust（crates/fluxgauss）、ogsql-parser AST、cargo test + tests/regress.rs golden、pytest `tests/regress/test_demo_migration.py`（Layer 0 权威门禁，双引擎）、Maven（javac 验证）。

---

## 背景：回归事实与量化

| 指标 | 基线 `682b4cf` | HEAD `0c2dd13`（含 #107） |
|---|---|---|
| demo 全流程 `mvn compile` | **0 错误（BUILD SUCCESS）** | **21 处唯一错误位置** |
| 受影响生成文件 | — | 20 个 |
| exam 全流程 `mvn compile` | 未测（仓外数据集） | 10 处错误 |

复现命令（仓内可复现，任何机器）：

```bash
# 基线
git checkout 682b4cf -- crates/fluxgauss/src
sed 's#^output_dir:.*#output_dir: /tmp/demo_base#' demo-project/fluxgauss_ru.yaml > /tmp/base.yaml
cargo run --bin fluxgauss -q -- --config /tmp/base.yaml && (cd /tmp/demo_base && mvn -q compile)   # → 0 错误
git checkout HEAD -- crates/fluxgauss/src
# HEAD
sed 's#^output_dir:.*#output_dir: /tmp/demo_head#' demo-project/fluxgauss_ru.yaml > /tmp/head.yaml
cargo run --bin fluxgauss -q -- --config /tmp/head.yaml && (cd /tmp/demo_head && mvn -q compile)   # → 21 处错误
```

**为什么 #107 的验证链没拦住**：CI 用 `-m "not demo_migration and not parity"` 主动排除；`cargo test` 不含全流程迁移；PR 用的是手搓 2 文件 mini-e2e（实参恰好类型对齐、调用在方法体而非声明段）。`tests/regress/test_demo_migration.py` 自称 "authoritative guard"，双引擎覆盖（`DEMO_CONFIGS` 含 `("ru", "demo-project/fluxgauss_ru.yaml", "dest_ru")`），但从未被执行。

---

## 四个根因（逐一带证据）

### 根因 B：跨包调用实参与返回值未强转（14 demo + 7 exam 处，最大类）

`emit_cross_pkg_call`（`crates/fluxgauss/src/expr.rs:719-738`）只对调用方 OUT 参数做 `.get()` 包裹，**零类型强转**；唯一调用点 `expr.rs:2222`。而同包自调用分支（`expr.rs:2184-2219`）逐参走 `coerce_arg_to_type(arg, &param.java_type, proc)` + OUT 透传 + 缺参补齐。

子类与实例：

| 子类 | 生成代码（demo） | 错误 |
|---|---|---|
| B1 实参字面量 → BigDecimal 参数 | `fnCalcBonus(12000, 0.10, 2)` | `int/double 无法转换为 BigDecimal`（L247,249,251,253,255,407,409,473,475,493,499） |
| B2 实参 Object（Map.get/getOrDefault）→ 具体类型 | `fnCalcYearsOfService(r.getOrDefault("hireDate", r.get("hire_date")))` | `Object 无法转换为 java.sql.Date`（L266,268）、`→ BigDecimal`（L300） |
| B3 返回值参与运算/赋值未强转 | `BigDecimal.valueOf(…fnDeptAvgSalary(vDept) * 1.2)` | `二元运算符 '*' 操作数类型错误`（L356） |
| B3 | `((Number) …fnLogSalaryChange(…))` 赋给 int | `Object 无法转换为 int`（L536） |
| B（exam） | `fncComGetMarketrate(…, vHgDays, …)`（BigDecimal → `long`）、`fncComGetSeatPara(…, rGetPurchase.getOrDefault(…), …)`（Object → String） | exam 7 处 |

`coerce_arg_to_type`（`expr.rs:635-687`）本身也有缺口：`target_is_long` 分支只处理 `arg_type == "Object" | "String"`；`infer_arg_type_from_expr`（`expr.rs:689-711`）对 BigDecimal 局部变量返回 `"BigDecimal"` → 落穿。statement.rs:1700-1715 已有完整参考链（含 `target_is_long && arg_type_inferred == "BigDecimal"` → `({}).longValue()`）。

### 根因 C：callee OUT 参数跨包传递未提升为 AtomicReference（demo 2 处）

```java
// demo FunctionCallsService.java:390,396
vRet = gaussFunctionCallsService.fnGetEmpDetails(1002, vName, vDept, vSalary);
//                                                    ↑ callee 声明 AtomicReference<String>，传入裸 String
```

`generate/service.rs:597` 确认 callee 的 OUT 参数签名为 `AtomicReference<{java_type}> {camel}`。`emit_cross_pkg_call` 的逻辑方向相反（只在**调用方**的 arg 是 OUT 时 `.get()` 拆包），从未按**被调方** OUT 参数把裸局部变量提升为 `AtomicReference` 并传引用。statement.rs:1667-1677 已有该 promote 逻辑（写入 `out_local_vars` + 目标类型）可复用。

### 根因 A：声明段默认值里的跨包调用不触发 service 注入（exam 3 处）

```
SQL   PKG_SPLIT_TRADE_STEP2.sql:513
      v_swh_zgh_flag swh_all_kind.kind_id%type := fnc_com_get_date_switch(p_i_date,'SWH_ZGH_FLAG');
                                                  ↑ DECLARE 段初始化（非方法体语句）
生成   String vSwhZghFlag = _1FncComGetDateSwitchService.fncComGetDateSwitch(pIDate, "SWH_ZGH_FLAG");
注入   ✗ 无 private final 字段、无构造器参数、无 import
javac  无法从静态上下文中引用非静态方法（把 _1Fnc…Service 当类名解析）
```

`discover_cross_service_refs`（`analyze.rs:301-357`）用正则 `(\w+Service)\.` 只扫 `proc.java_logic_lines`；声明初始化表达式由 `generate/service.rs` 从 `local_var_defaults` 单独渲染，**不在扫描范围**。且 `expr.rs` 的跨包解析路径**从不 push `ServiceCall`**（push 点只在 statement.rs:1790/1873），注入完全依赖这次事后扫描 → 扫不到即彻底丢失。

对照验证（排除命名嫌疑）：同为 `_` + 数字前缀的 `_0SimplifiedFncComGetdayService` 注入正常，因为它的调用出现在方法体语句（`v_last_date := fnc_com_getday(...)`）→ 进 `java_logic_lines` → 被扫到。

### 根因 D：多个 range loop 复用同名计数器时声明丢失（demo 1 处，#107 Task 6 引入）

```java
// 基线：两个循环各自内联声明
for (int i = 1; i <= 1000; i++) { … }
for (int i = 1; i <= 1000; i++) { … }
// HEAD：第二个丢失声明，方法级也没有 int i;  → L498 找不到符号
for (int i = 1; i <= 1000; i++) { … }
for (i = 1; i <= 1000; i++) { … }        // ← i 未声明
```

机制：`statement.rs` 的计数器注册（`if !already_declared { proc.local_vars.insert(...) }`）在第一个循环把 `i` 写入 `local_vars` → 第二个循环 `already_declared == true` → 生成不带声明的 `for (i = 1; …)`；而 `generate/service.rs` 的 `is_range_loop_iter` 检测到已存在 `for (int i = ` 就跳过方法级声明 → 第二个循环的 `i` 无任何声明。

---

## 验收门禁（本计划的硬指标）

```bash
cargo build --release --bin fluxgauss           # test_demo_migration 的 ru 引擎前置
python3 -m pytest tests/regress/test_demo_migration.py -v -m demo_migration
```

**通过标准：demo 全流程 `mvn compile` 唯一错误位置数 == 0**（基线即为 0，不接受「与 HEAD 持平」或「有所减少」）。

辅助门禁：`cargo test -p fluxgauss` 全绿、`cargo fmt --check` 干净、`cargo clippy` 改动文件零新增 warning。

可选（仅有仓外数据集的机器）：exam 配置全流程 `mvn compile` 错误数归零——**不是门禁**。

---

## Task 1: 锁定回归的失败测试（Red 先行）

**Files:**
- Create: `tests/regress/fixtures/issue_108_cross_pkg_callee.sql`
- Create: `tests/regress/fixtures/issue_108_cross_pkg_caller.sql`
- Modify: `crates/fluxgauss/tests/regress.rs`

**Step 1: 写 callee fixture**（覆盖 B1/B2/C 三类参数形态）

`issue_108_cross_pkg_callee.sql`：
```sql
CREATE OR REPLACE FUNCTION fn_calc_bonus(p_base NUMBER, p_pct NUMBER, p_years NUMBER)
RETURN NUMBER IS
BEGIN
  RETURN p_base * p_pct * p_years;
END;
/

CREATE OR REPLACE PROCEDURE prc_get_emp_name(p_i_id NUMBER, p_o_name OUT VARCHAR2)
IS
BEGIN
  p_o_name := 'emp';
END;
/
```

**Step 2: 写 caller fixture**（覆盖 A 声明段调用 + B1 字面量实参 + B2 Object 实参 + C OUT 参数 + D 双 range loop）

`issue_108_cross_pkg_caller.sql`：
```sql
CREATE OR REPLACE PROCEDURE prc_call_cross_pkg(p_i_date VARCHAR2)
IS
  v_decl_bonus NUMBER := fn_calc_bonus(12000, 0.10, 2);
  v_bonus      NUMBER;
  v_name       VARCHAR2(64);
  v_sum        NUMBER := 0;
BEGIN
  v_bonus := fn_calc_bonus(15000, 0.15, 3);
  prc_get_emp_name(1002, v_name);
  FOR i IN 1..10 LOOP
    v_sum := v_sum + i;
  END LOOP;
  FOR i IN 1..20 LOOP
    v_sum := v_sum + i;
  END LOOP;
END;
/
```

**Step 3: 新增分项行为断言测试**

⚠️ **不要在 Rust 侧调用 javac**：`crates/fluxgauss/tests/regress.rs` 全文件**没有任何 javac/`Command::new` 调用**，`issue_72_string_to_number_coercion_compiles` 名字里的 "compiles" 是**字符串断言**而非真实编译；且生成物依赖 Spring/MyBatis/slf4j classpath，裸 javac 无法编译，需完整 Maven 环境。因此：

- **Rust 侧**（本 Task）：对 A/B/C/D 四类行为做**精确字符串断言**（快、确定、可定位到根因）
- **编译判据**（Task 6）：统一由 `tests/regress/test_demo_migration.py`（Layer 0，双引擎，真实 `mvn compile`）担任——demo-project 已实证覆盖 B/C/D（21 处错误正来自它）

在 `crates/fluxgauss/tests/regress.rs` 追加：

```rust
#[test]
fn issue_108_cross_pkg_call_arg_and_out_handling() {
    // #107 回归四类：B 实参强转 / C callee OUT 提升 / A 声明段注入 / D 同名 range loop 计数器声明
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join(FIXTURES_REL);
    let sql_files = vec![
        fixtures.join("issue_108_cross_pkg_callee.sql"),
        fixtures.join("issue_108_cross_pkg_caller.sql"),
    ];
    let tmp = tempfile::tempdir().expect("tempdir");
    let files = run_multi_file_services(&sql_files, &tmp.path().join("dest"));
    let caller = files.get("Issue108CrossPkgCallerService.java").expect("caller service");

    // A: 声明段调用必须触发 service 注入
    assert!(
        caller.contains("private final Issue108CrossPkgCalleeService"),
        "A: declaration-position cross-pkg call must inject callee service:\n{}", caller
    );
    // B1: int/double 字面量实参必须强转为 callee 的 BigDecimal 参数
    assert!(
        caller.contains("fnCalcBonus(java.math.BigDecimal.valueOf(12000)"),
        "B1: numeric literal args must be coerced to BigDecimal:\n{}", caller
    );
    // C: callee OUT 参数必须收到 AtomicReference，不得是裸 String 局部变量
    assert!(
        caller.contains("prcGetEmpName(") && !caller.contains("prcGetEmpName(1002, vName)"),
        "C: callee OUT param must receive an AtomicReference, not a bare local:\n{}", caller
    );
    // D: 每个 range loop 都必须声明计数器
    assert!(
        !caller.contains("for (i ="),
        "D: every range loop must declare its counter (no bare `for (i =`):\n{}", caller
    );
}
```

（断言字面量需按实测生成结果微调——先跑一次看实际输出，再把断言收紧到能区分修复前后的最小形态；**禁止**放宽成 `is_not_none` 式永真断言。）


**Step 4: 运行确认失败**

```bash
cargo test -p fluxgauss --test regress issue_108 -- --nocapture
```
Expected: FAIL。把实际失败断言与 javac 错误贴进报告。

**Step 5: Commit**

```bash
git add tests/regress/fixtures/issue_108_*.sql crates/fluxgauss/tests/regress.rs
git commit -m "test: cross-pkg call regression fixtures + javac gate (failing red, #107)"
```

## Task 2: 根因 B —— 跨包实参强转（对齐同包分支）

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`（`emit_cross_pkg_call` L719-738；元组 L2131；点号分支 L2131-2155；单名分支 L2156-2179；调用点 L2222）

**Step 1: 把被调方 params 传到调用点**

元组由 3 项扩为 4 项 `(method, is_self_call, cross_pkg_svc, target_params: Option<Vec<Parameter>>)`：
- 单名分支：`1 =>` 臂内 `cross[0].params.clone()`（`Parameter` 已 derive Clone）
- 点号分支：用已算出的 `method` 查 `proc.all_proc_params`，按 `e.package` 匹配 `pkg_hint`（复用 L2139-2141 的 `pkg_` 前缀归一化）+ `e.params.len() == jargs.len()` 过滤，唯一命中才取；否则 `None`
- 其余臂 `None`

**Step 2: `emit_cross_pkg_call` 接收 params 并逐参强转**

```rust
fn emit_cross_pkg_call(
    cross_pkg_svc: &str, method: &str, jargs: Vec<String>,
    target_params: Option<&[Parameter]>, proc: &ProcedureInfo,
) -> String
```
逻辑对齐 `is_self_call` 分支（L2194-2208）：
- 有 `target_params` 时逐参：**非 OUT** → `coerce_arg_to_type(arg, &param.java_type, proc)`；**OUT** → 交由 Task 3 处理
- 缺参补齐同 L2209-2218（OUT → `new AtomicReference<>(null)`，否则 `null`）
- `target_params` 为 `None` 时退回现状 `.get()`-only 行为（保持向后兼容）

**Step 3: 补 `coerce_arg_to_type` 缺口（L635-687）**

按 statement.rs:1700-1715 参考链补齐：
- `target_is_long && arg_type == "BigDecimal"` → `({}).longValue()`
- `target BigDecimal && arg 为 int/double 字面量` → `java.math.BigDecimal.valueOf({})`（B1 主因；现有 L651-664 需确认是否覆盖 `0.10` 这类小数字面量）
- `target 为 java.sql.Date/Timestamp && arg 为 Object` → 现有 Date 强转 helper（若无则新增最小实现）

**Step 4: 验证**

```bash
cargo test -p fluxgauss --test regress issue_108 -- --nocapture   # B 类断言应转绿（A/C/D 仍红）
cargo test -p fluxgauss                                           # 既有全绿
REGEN_RUST_GOLDEN=1 cargo test -p fluxgauss --test regress regress_golden_compare && git status --short tests/regress/golden/
```
golden 若有 diff，逐条阅读确认是「跨包调用实参新增强转」的预期变化后再纳入提交。

**Step 5: Commit**
```bash
git commit -am "fix(rust): coerce cross-package call arguments to callee param types (#107 regression)"
```

## Task 3: 根因 C —— callee OUT 参数提升为 AtomicReference

**Files:**
- Modify: `crates/fluxgauss/src/expr.rs`（`emit_cross_pkg_call`）
- 参考（勿改）: `crates/fluxgauss/src/statement.rs:1667-1677`（既有 promote 逻辑）、`crates/fluxgauss/src/generate/service.rs:597`（callee OUT 签名）

**Step 1: 实现**

在 `emit_cross_pkg_call` 内，对 `target_params[i].is_out()` 为真的实参：
- 若实参是裸局部变量（无 `.`/`(`）→ 按 statement.rs:1667-1677 写入 `proc.out_local_vars`（目标类型取 `param.java_type`）并原样传引用，**不加 `.get()`**
- 若实参已是调用方 OUT 参数（`is_out_param`）→ 原样传递（同为 `AtomicReference`）
- 仅当**被调方参数为 IN** 而实参是 `AtomicReference` 时才 `.get()` 拆包（即现状逻辑的正确适用范围）

⚠️ 注意：`emit_cross_pkg_call` 现签名收 `&ProcedureInfo`（只读），promote 需要写 `out_local_vars` → 需改为 `&mut ProcedureInfo` 或把 promote 结果回传由调用点写入。先读 `expr.rs` 中 `expr_to_java`/`function_call_to_java` 的 proc 可变性，选侵入最小方案（若全链只读，则采用「回传待提升变量列表，由 statement 层写入」）。

**Step 2: 验证**

```bash
# C 断言转绿
cargo test -p fluxgauss --test regress issue_108 -- --nocapture
# Expected: issue_108_cross_pkg_call_arg_and_out_handling ... ok（C 断言不再 panic）

# demo 场景中 C 类错误（String→AtomicReference）消失
rm -rf /tmp/c_check && sed 's#^output_dir:.*#output_dir: /tmp/c_check#' demo-project/fluxgauss_ru.yaml > /tmp/c.yaml
cargo run --bin fluxgauss -q -- --config /tmp/c.yaml
grep -n "fnGetEmpDetails(" /tmp/c_check/src/main/java/ced/service/FunctionCallsService.java
# Expected: OUT 实参形如 AtomicReference 变量（非裸 vName/vDept/vSalary）
cd /tmp/c_check && mvn -q compile 2>&1 | grep -c "AtomicReference"
# Expected: 0（原 L390,396 的 String→AtomicReference<String> 错误消失）

# 既有测试与 golden
cargo test -p fluxgauss
# Expected: lib + regress 全部 ok
REGEN_RUST_GOLDEN=1 cargo test -p fluxgauss --test regress regress_golden_compare && git status --short tests/regress/golden/
# Expected: 仅出现「跨包 OUT 参数改为传 AtomicReference」相关 diff；逐条阅读确认后纳入提交
```


**Step 3: Commit**
```bash
git commit -am "fix(rust): promote bare locals to AtomicReference for cross-package OUT params (#107 regression)"
```

## Task 4: 根因 A —— 声明段调用触发 service 注入

**Files:**
- Modify: `crates/fluxgauss/src/analyze.rs`（`discover_cross_service_refs` L301-357）

**Step 1: 扩展扫描范围**

把 `local_var_defaults` 的值一并纳入同一正则扫描（与 `java_logic_lines` 同处理）：

```rust
for proc in &mut pkg.procedures {
    let scan_targets: Vec<String> = proc.java_logic_lines.iter().cloned()
        .chain(proc.local_var_defaults.values().cloned())   // 声明段初始化表达式（#107 回归）
        .collect();
    for line in &scan_targets { /* 既有 `//` 跳过 + 正则 + known_svc_names 逻辑不变 */ }
}
```
先读 `local_var_defaults` 的实际类型（`HashMap<String, String>` 或含结构），据此取值。

**Step 2: 验证**

```bash
# A 断言转绿
cargo test -p fluxgauss --test regress issue_108 -- --nocapture
# Expected: A 断言（private final Issue108CrossPkgCalleeService）不再 panic

# exam 场景（若有仓外数据集）：3 处静态上下文错误应消失
# 无数据集时用 issue_108 fixture 直接核对注入三要素
rm -rf /tmp/a_check && cargo run --bin fluxgauss -q -- -o /dev/null 2>/dev/null; \
  sed 's#^output_dir:.*#output_dir: /tmp/a_check#' demo-project/fluxgauss_ru.yaml > /tmp/a.yaml && \
  cargo run --bin fluxgauss -q -- --config /tmp/a.yaml
grep -rn "import ced.service.*Service;" /tmp/a_check/src/main/java/ced/service/FunctionCallsService.java | head
# Expected: 注入 import 数量不少于修复前（不得因扫描范围扩大而丢失既有注入）

# 既有测试与 golden
cargo test -p fluxgauss
# Expected: 全绿
REGEN_RUST_GOLDEN=1 cargo test -p fluxgauss --test regress regress_golden_compare && git status --short tests/regress/golden/
# Expected: diff 仅为「声明段调用所在包新增 service 注入字段/构造器参数/import」；无既有注入被删除
```


**Step 3: Commit**
```bash
git commit -am "fix(rust): scan declaration initializers for cross-service refs (#107 regression)"
```

## Task 5: 根因 D —— 同名 range loop 计数器声明

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`（计数器注册处）和/或 `crates/fluxgauss/src/generate/service.rs`（`is_range_loop_iter` 跳过逻辑）

**Step 1: 选定修法**（二选一，读代码后决定，报告说明理由）
- D1：`generate/service.rs` 的 `is_range_loop_iter` 改为「仅当**所有**该变量的 range loop 都内联声明时才跳过方法级声明」——更稳妥：只要存在不带声明的 `for (i = ` 就输出方法级 `int i = 0;`
- D2：`statement.rs` 保持每个 range loop 都内联声明（`for (int i = ...)`），即恢复基线行为，仅在 body 引用需要注册时才写 `local_vars`；需确认不破坏 #107 WARPDRIVER 的 `#{i}` 参数化收益

**Step 2: 验证**

```bash
# D 断言转绿
cargo test -p fluxgauss --test regress issue_108 -- --nocapture
# Expected: D 断言（无裸 `for (i =`）不再 panic

# demo 场景：L498「找不到符号」消失，且每个 range loop 都有声明
rm -rf /tmp/d_check && sed 's#^output_dir:.*#output_dir: /tmp/d_check#' demo-project/fluxgauss_ru.yaml > /tmp/d.yaml
cargo run --bin fluxgauss -q -- --config /tmp/d.yaml
grep -cE "for \(i = " /tmp/d_check/src/main/java/ced/service/FunctionCallsService.java
# Expected: 0
grep -cE "for \(int i = " /tmp/d_check/src/main/java/ced/service/FunctionCallsService.java
# Expected: >= 2（两个 range loop 各自有声明）
cd /tmp/d_check && mvn -q compile 2>&1 | grep -c "找不到符号"
# Expected: 0

# 必须复核 WARPDRIVER golden：#107 的 mapper 参数化收益不得回退
grep -n "insertOrderItemSnapshot" tests/regress/golden/ru/WARPDRIVER_STRESS_TEST/Mapper.xml.golden
# Expected: 仍包含 #{i, jdbcType=INTEGER, javaType=int}，不得回退为裸文本 (i)
grep -n "int i = 0;\|for (i = 1" tests/regress/golden/ru/WARPDRIVER_STRESS_TEST/Service.java.golden
# Expected: 计数器有声明（方法级 `int i = 0;` 或内联 `for (int i =`），无未声明的裸 `for (i =`

cargo test -p fluxgauss
# Expected: 全绿
REGEN_RUST_GOLDEN=1 cargo test -p fluxgauss --test regress regress_golden_compare && git status --short tests/regress/golden/
# Expected: diff 仅为循环计数器声明形态变化；逐条说明后纳入提交
```


**Step 3: Commit**
```bash
git commit -am "fix(rust): declare range-loop counter for every loop reusing the same name (#107 regression)"
```

## Task 6: 全流程迁移门禁验收（本计划的最终判据）

**Step 1: 跑权威门禁**

```bash
cargo build --release --bin fluxgauss
python3 -m pytest tests/regress/test_demo_migration.py -v -m demo_migration
```
Expected: PASS。若失败，逐条错误归入 A/B/C/D 或识别为新根因，回到对应 Task 修复后重跑（**不得放宽门禁**）。

**Step 2: 独立复核编译错误数为 0**

```bash
rm -rf /tmp/demo_gate && sed 's#^output_dir:.*#output_dir: /tmp/demo_gate#' demo-project/fluxgauss_ru.yaml > /tmp/gate.yaml
cargo run --bin fluxgauss -q -- --config /tmp/gate.yaml
cd /tmp/demo_gate && mvn -q compile 2>&1 | grep -oE "\.java:\[[0-9]+,[0-9]+\]" | sort -u | wc -l    # 必须为 0
```

**Step 3: 辅助门禁**
```bash
cargo fmt --check
cargo clippy -p fluxgauss --all-targets 2>&1 | grep -E "warning.*(expr\.rs|analyze\.rs|statement\.rs|service\.rs)" || echo NO_NEW_WARNINGS
cargo test -p fluxgauss
```

**Step 4: 可选（仅有仓外数据集时，非门禁）**：exam 配置全流程编译错误数（记录数值，期望 0）

**Step 5: Commit**（若前述任务已全部提交，此步仅为记录验收证据，无代码改动则跳过）

## Task 7: 固化门禁（AGENTS.md + CI）

**Files:**
- Modify: `AGENTS.md`（§3 验证链、§9 完成标准、§10 命令）
- Modify: `.github/workflows/ci.yml`（新增 job）

**Step 1: §3 验证链写明数据集**

```
生成逻辑改动 →
  cargo test / pytest（单元 + golden 回归）
  → pytest tests/regress/test_demo_migration.py -m demo_migration   ← 强制、仓内可复现、双引擎、mvn compile 零错误
  → [可选，有数据集时] exam / fastaas / ogagila 全量迁移
```
并补一句教训：golden 回归是**逐 fixture 单文件**转换，与 demo-project 的 41 文件全量迁移是**两套不同数据**，前者绿不代表后者可编译（实例：#107 golden 全绿而 demo 迁移从 0 错误退化到 21 错误）。

**Step 2: §9 完成标准新增硬门禁项**

```
- [ ] 生成逻辑改动：`pytest tests/regress/test_demo_migration.py -v -m demo_migration` 通过，
      且 demo 全流程 `mvn compile` 唯一错误位置数为 **0**（不接受「与基线持平」「有所减少」）
```

**Step 3: CI 新增第 4 个 job**

`.github/workflows/ci.yml` 增 `demo-migration` job：需 JDK 17 + Maven + ogsql 二进制 + `cargo build --release --bin fluxgauss`，执行 `pytest tests/regress/test_demo_migration.py -m demo_migration`。沿用现有 `Build ogsql (Linux)` job 的产物获取方式（读该 job 确认 artifact 传递机制）。

**Step 4: 验证**

```bash
# AGENTS.md 三处固化文本就位
grep -n "test_demo_migration" AGENTS.md
# Expected: 至少 3 处命中（§3 验证链、§9 完成标准、§10 命令清单）
grep -n "唯一错误位置数为 \*\*0\*\*\|不接受「与基线持平」" AGENTS.md
# Expected: §9 完成标准中命中硬门禁措辞

# CI yaml 语法有效 + 新 job 就位
python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(sorted(d['jobs'].keys()))"
# Expected: 输出的 job 列表包含新增的 demo-migration（原 3 个 job 仍在）
grep -n "demo_migration" .github/workflows/ci.yml
# Expected: 命中 pytest -m demo_migration 调用行
grep -n "cargo build --release --bin fluxgauss" .github/workflows/ci.yml
# Expected: 命中（ru 引擎前置构建步骤存在，否则 test_demo_migration 会 skip 掉 ru）
```

**Step 5: Commit**

```bash
git add AGENTS.md .github/workflows/ci.yml
git commit -m "docs+ci: 固化 demo 全流程迁移为生成逻辑改动的强制门禁"
```


## Task 8: 收尾自检

- [ ] 4 个根因各有失败→通过的测试锚定（Task 1 的 issue_108 javac 门禁 + 分项断言）
- [ ] 未删除、跳过、改写人类已有测试
- [ ] `test_demo_migration` 通过且 demo `mvn compile` 错误数为 0（贴实际命令输出）
- [ ] `cargo test` / `cargo fmt --check` / clippy 无新增 全部通过
- [ ] golden 变更逐条说明；WARPDRIVER 的 `#{i}` 参数化未回退
- [ ] AGENTS.md §3/§9/§10 与 CI job 已固化门禁
- [ ] 无草稿、调试输出、无 Cargo.lock 变更带入

---

## 范围外 / 后续

1. **Python 引擎 parity**：`flux_gauss.py` 的 `'dict' object has no attribute '_tracker'` 异常仍是 parity 前置阻塞（`pytest tests/regress/test_parity.py -m parity`），本计划不含。
2. **Rust CLI `-o -s` 缺陷**：`resolve_inputs` 未设 `config.output_dir`，导致 `-o` 模式生成物写到默认 `./dest`（既有缺陷，非 #107 引入）。本计划的所有命令均用 `--config` 模式绕开。
3. **`demo-project/*.yaml`（8 个未跟踪配置）**：`sources` 为机器本地绝对路径（`/Users/c2j/...`、`/tmp/...`），不可复现，需单独立项决定是否纳入仓库。
4. **明文口令**：`Enmo@123` 出现在已跟踪的 `fluxgauss_py.yaml`/`fluxgauss_ru.yaml` 与 2 份 docs/reports，单独清理任务。

## 关键文件与行号索引

| 位置 | 说明 |
|---|---|
| `crates/fluxgauss/src/expr.rs:719-738` | `emit_cross_pkg_call`（只 `.get()`，无强转）——根因 B/C 主战场 |
| `crates/fluxgauss/src/expr.rs:2222` | 唯一调用点 |
| `crates/fluxgauss/src/expr.rs:2131` | 3 元组（需扩为 4 元组携带 target_params） |
| `crates/fluxgauss/src/expr.rs:2131-2155` | 点号分支（无 arity 检查、无 params 查找） |
| `crates/fluxgauss/src/expr.rs:2156-2179` | 单名分支（`cross[0].params` 仅在 match 臂内可见） |
| `crates/fluxgauss/src/expr.rs:2184-2219` | 同包自调用的强转参考实现（对齐目标） |
| `crates/fluxgauss/src/expr.rs:635-687` | `coerce_arg_to_type`（BigDecimal→long 等缺口） |
| `crates/fluxgauss/src/expr.rs:689-711` | `infer_arg_type_from_expr` |
| `crates/fluxgauss/src/statement.rs:1700-1715` | 语句级强转参考链（含 `.longValue()`） |
| `crates/fluxgauss/src/statement.rs:1667-1677` | OUT 参数 promote 参考逻辑 |
| `crates/fluxgauss/src/analyze.rs:301-357` | `discover_cross_service_refs`（扫描范围缺声明段）——根因 A |
| `crates/fluxgauss/src/generate/service.rs:597` | callee OUT 参数签名 `AtomicReference<T>` |
| `crates/fluxgauss/tests/regress.rs` | `run_multi_file_services`、`issue_72_string_to_number_coercion_compiles`（javac 门禁模式） |
| `tests/regress/test_demo_migration.py` | Layer 0 权威门禁（双引擎，`DEMO_CONFIGS`） |

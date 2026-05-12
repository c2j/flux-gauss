# Rust Converter DML & Service Call Generation

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Rust converter generate complete mapper XML/Java interfaces and service classes with actual code (not TODO stubs), matching the Python converter's output quality.

**Architecture:** The root cause is that `process_statement` in `statement.rs` treats SQL and ProcedureCall statements as comments instead of extracting DML metadata and generating real Java code. We need to: (1) add a context struct for cross-package resolution, (2) handle `PlStatement::SqlStatement` using its AST to extract DML, (3) handle `PlStatement::Sql` with regex-based DML detection, (4) handle `PlStatement::ProcedureCall` to resolve cross-service calls, and (5) handle `PlStatement::Perform` for PERFORM statements.

**Tech Stack:** Rust, ogsql-parser AST types

---

## Key Files

- `crates/fluxgauss/src/statement.rs` — Main file to modify (~514 lines)
- `crates/fluxgauss/src/analyze.rs` — Update to pass context to process_statement (~115 lines)
- `crates/fluxgauss/src/types.rs` — DmlStatement, ServiceCall, ProcedureInfo types
- `crates/fluxgauss/src/naming.rs` — snake_to_camel, package_to_classname, java_method_name
- `crates/fluxgauss/src/expr.rs` — expr_to_java for expression conversion
- `crates/fluxgauss/src/context.rs` — AnalysisContext already has package_summaries

## Reference Files (Python converter)
- `converter/flux_gauss.py` lines 2902-3018 — `_process_sql_statement()` 
- `converter/flux_gauss.py` lines 3777-3889 — `_process_procedure_call()`
- `converter/flux_gauss.py` lines 3296-3421 — `_process_perform()`
- `converter/flux_gauss.py` lines 2855-2858 — `_dml_method_name()`
- `converter/flux_gauss.py` lines 6147-6158 — `_build_param_args()`
- `converter/flux_gauss.py` lines 6188-6220 — `_sql_local_var_names()`

## Expected Output Examples

### Mapper XML (currently empty, should be like dest_py):
```xml
<mapper namespace="ced.mapper.OrderMapper">
    <insert id="insertCreateOrder">
        insert into t_orders(user_id, product_id, qty, status, created_at)
        values(#{pUserId, jdbcType=BIGINT, javaType=Long}, ...)
    </insert>
    <select id="selectCancelOrder" parameterType="long" resultType="java.util.LinkedHashMap">
        select product_id, qty from t_orders where id = #{pOrderId, ...} LIMIT 1
    </select>
</mapper>
```

### Mapper Java (currently "No direct DML", should have methods):
```java
int insertCreateOrder(@Param("pUserId") Long pUserId, ...);
Map<String, Object> selectCancelOrder(@Param("pOrderId") Long pOrderId);
```

### Service (currently full of TODOs, should have real code):
```java
public void createOrder(long pUserId, long pProductId, int pQty) {
    inventoryService.reserveStock(pProductId, pQty);
    orderMapper.insertCreateOrder(pUserId, pProductId, pQty);
    commonService.logOperation("ORDER", "CREATE", 0);
}
```

---

### Task 1: Add StatementContext struct to context.rs

**Files:**
- Modify: `crates/fluxgauss/src/context.rs`

Add a `StatementContext` struct that holds cross-package resolution data and DML counter:

```rust
use std::collections::HashMap;
use crate::types::PackageSummary;

/// Context passed to process_statement for cross-package resolution and DML tracking.
pub struct StatementContext<'a> {
    pub summaries: &'a HashMap<String, PackageSummary>,
    pub dml_counter: HashMap<String, usize>,
}

impl<'a> StatementContext<'a> {
    pub fn new(summaries: &'a HashMap<String, PackageSummary>) -> Self {
        Self {
            summaries,
            dml_counter: HashMap::new(),
        }
    }
}
```

### Task 2: Add helper functions to statement.rs

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Add these helper functions at the top of the file (after imports):

```rust
use std::collections::HashMap;
use crate::context::StatementContext;
use crate::types::{DmlType, DmlStatement, ServiceCall};
use crate::naming::{snake_to_camel, snake_to_pascal, package_to_classname, java_method_name};

/// Generate a unique DML method name like "insertCreateOrder" or "selectCancelOrder"
fn dml_method_name(dml_type: &str, proc_name: &str, counter: &mut HashMap<String, usize>) -> String {
    let key = format!("{}_{}", dml_type, proc_name);
    let n = counter.entry(key.clone()).or_insert(0);
    let suffix = if *n > 0 { format!("_{}", n) } else { String::new() };
    *n += 1;
    format!("{}{}{}", dml_type, snake_to_pascal(proc_name), suffix)
}

/// Build the argument list for a mapper call, e.g., "pUserId, pProductId, pQty"
fn build_mapper_call_args(proc: &ProcedureInfo) -> String {
    let mut parts: Vec<String> = Vec::new();
    for p in &proc.parameters {
        if p.is_out() {
            continue;
        }
        let jn = snake_to_camel(&p.name);
        parts.push(jn);
    }
    parts.join(", ")
}

/// Detect DML type from raw SQL text using regex
fn detect_dml_type(sql: &str) -> Option<DmlType> {
    let upper = sql.trim_start().to_uppercase();
    if upper.starts_with("SELECT") {
        Some(DmlType::Select)
    } else if upper.starts_with("INSERT") {
        Some(DmlType::Insert)
    } else if upper.starts_with("UPDATE") {
        Some(DmlType::Update)
    } else if upper.starts_with("DELETE") {
        Some(DmlType::Delete)
    } else {
        None
    }
}

/// Extract table name from SQL text
fn extract_table_from_sql(sql: &str) -> String {
    let upper = sql.to_uppercase();
    let re = regex::Regex::new(r"(?i)(?:from|into|update)\s+(\w+)").unwrap();
    if let Some(caps) = re.captures(&upper) {
        if let Some(m) = caps.get(1) {
            return m.as_str().to_lowercase();
        }
    }
    "unknown".to_string()
}

/// Clean SQL text for use in mapper XML (remove INTO clauses)
fn clean_sql_for_mapper(sql: &str, dml_type: DmlType) -> String {
    let mut s = sql.to_string();
    if matches!(dml_type, DmlType::Select) {
        // Remove INTO clause: "select ... into vars from ..." -> "select ... from ..."
        let re = regex::Regex::new(r"(?i)\s+into\s+\w+(\s*,\s*\w+)*").unwrap();
        s = re.replace(&s, "").to_string();
    }
    s
}

/// Resolve a package name from call parts using summaries
fn resolve_package_name(pkg_hint: &str, summaries: &HashMap<String, PackageSummary>) -> Option<String> {
    // Direct match
    if summaries.contains_key(pkg_hint) {
        return Some(pkg_hint.to_string());
    }
    // Case-insensitive match
    for key in summaries.keys() {
        if key.to_lowercase() == pkg_hint.to_lowercase() {
            return Some(key.clone());
        }
    }
    None
}
```

### Task 3: Implement SqlStatement handler

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Change the `PlStatement::SqlStatement` match arm from:
```rust
PlStatement::SqlStatement { sql_text, .. } => {
    push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
    Ok(())
}
```

To:
```rust
PlStatement::SqlStatement { sql_text, statement, .. } => {
    process_sql_statement(statement, &sql_text, proc, ctx);
    Ok(())
}
```

Then add the `process_sql_statement` function:

```rust
fn process_sql_statement(
    statement: &ogsql_parser::ast::Statement,
    sql_text: &str,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    use ogsql_parser::ast::Statement;
    match statement {
        Statement::Select(select_stmt) => {
            let into_targets = &select_stmt.node.into_targets;
            let method_id = dml_method_name("select", &proc.proc_name, &mut ctx.dml_counter);
            let clean_sql = clean_sql_for_mapper(sql_text, DmlType::Select);
            let args = build_mapper_call_args(proc);

            if into_targets.is_some() && into_targets.as_ref().map_or(false, |t| !t.is_empty()) {
                // SELECT INTO variables
                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text: clean_sql,
                    result_type: Some("Map<String, Object>".to_string()),
                    parameter_types: Default::default(),
                    optional_filters: Vec::new(),
                    returns_list: false,
                    extra_params: Vec::new(),
                });
                push_logic_line(proc, format!("Map<String, Object> _row = mapper.{}({});", method_id, args));
            } else {
                // SELECT without INTO - returns list
                proc.dml_statements.push(DmlStatement {
                    sql_type: DmlType::Select,
                    method_id: method_id.clone(),
                    sql_text: sql_text.to_string(),
                    result_type: Some("Map<String, Object>".to_string()),
                    parameter_types: Default::default(),
                    optional_filters: Vec::new(),
                    returns_list: true,
                    extra_params: Vec::new(),
                });
                push_logic_line(proc, format!("List<Map<String, Object>> _result = mapper.{}({});", method_id, args));
                proc.imports.insert("import java.util.List;".to_string());
                proc.imports.insert("import java.util.Map;".to_string());
            }
            // Track table refs
            for table_ref in &select_stmt.node.from {
                extract_table_ref(table_ref, proc);
            }
        }
        Statement::Insert(insert_stmt) => {
            let method_id = dml_method_name("insert", &proc.proc_name, &mut ctx.dml_counter);
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Insert,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        Statement::Update(update_stmt) => {
            let method_id = dml_method_name("update", &proc.proc_name, &mut ctx.dml_counter);
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Update,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        Statement::Delete(delete_stmt) => {
            let method_id = dml_method_name("delete", &proc.proc_name, &mut ctx.dml_counter);
            let args = build_mapper_call_args(proc);
            proc.dml_statements.push(DmlStatement {
                sql_type: DmlType::Delete,
                method_id: method_id.clone(),
                sql_text: sql_text.to_string(),
                result_type: None,
                parameter_types: Default::default(),
                optional_filters: Vec::new(),
                returns_list: false,
                extra_params: Vec::new(),
            });
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
        _ => {
            // Other SQL types (DDL, etc.) - keep as comment
            push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
        }
    }
}

fn extract_table_ref(table_ref: &ogsql_parser::ast::TableRef, proc: &mut ProcedureInfo) {
    use ogsql_parser::ast::TableRef;
    match table_ref {
        TableRef::Table { name, .. } => {
            let table_name = name.last().map(|s| s.clone()).unwrap_or_default();
            if !table_name.is_empty() {
                proc.table_refs.insert(table_name);
            }
        }
        TableRef::Subquery { alias, .. } => {}
        _ => {}
    }
}
```

### Task 4: Implement Sql (raw text) handler

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Change the `PlStatement::Sql` match arm from:
```rust
PlStatement::Sql(sql_text) => {
    push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
    Ok(())
}
```

To:
```rust
PlStatement::Sql(sql_text) => {
    if let Some(dml_type) = detect_dml_type(sql_text) {
        let method_id = dml_method_name(
            match dml_type {
                DmlType::Select => "select",
                DmlType::Insert => "insert",
                DmlType::Update => "update",
                DmlType::Delete => "delete",
            },
            &proc.proc_name,
            &mut ctx.dml_counter,
        );
        let args = build_mapper_call_args(proc);
        let clean_sql = clean_sql_for_mapper(sql_text, dml_type);
        let is_select = matches!(dml_type, DmlType::Select);
        
        proc.dml_statements.push(DmlStatement {
            sql_type: dml_type.clone(),
            method_id: method_id.clone(),
            sql_text: clean_sql,
            result_type: if is_select { Some("Map<String, Object>".to_string()) } else { None },
            parameter_types: Default::default(),
            optional_filters: Vec::new(),
            returns_list: false,
            extra_params: Vec::new(),
        });
        
        if is_select {
            push_logic_line(proc, format!("Map<String, Object> _row = mapper.{}({});", method_id, args));
            proc.imports.insert("import java.util.Map;".to_string());
        } else {
            push_logic_line(proc, format!("mapper.{}({});", method_id, args));
        }
    } else {
        push_logic_line(proc, format!("// SQL: {}", sql_text.replace('\n', " ")));
    }
    Ok(())
}
```

### Task 5: Implement ProcedureCall handler

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Change the `PlStatement::ProcedureCall` match arm from:
```rust
PlStatement::ProcedureCall(call) => {
    let name_parts: Vec<&str> = call.node.name.iter().map(|s| s.as_str()).collect();
    let full_name = name_parts.join(".");
    let method = crate::naming::java_method_name(name_parts.last().unwrap_or(&"unknown"));
    let args: Vec<String> = call.node.arguments.iter()
        .map(|a| crate::expr::expr_to_java(a, proc))
        .collect();
    push_logic_line(proc, format!("// TODO: call {}.{}({});", full_name, method, args.join(", ")));
    Ok(())
}
```

To:
```rust
PlStatement::ProcedureCall(call) => {
    process_procedure_call(&call.node, proc, ctx);
    Ok(())
}
```

Add the function:
```rust
fn process_procedure_call(
    call: &ogsql_parser::ast::plpgsql::PlProcedureCall,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) {
    let name_parts: Vec<&str> = call.name.iter().map(|s| s.as_str()).collect();
    
    // Resolve package and function names
    let (pkg, func) = if name_parts.len() >= 3 {
        (name_parts[name_parts.len() - 2], name_parts[name_parts.len() - 1])
    } else if name_parts.len() == 2 {
        (name_parts[0], name_parts[1])
    } else if name_parts.len() == 1 {
        (&proc.package[..], name_parts[0])
    } else {
        let full_name = name_parts.join(".");
        push_logic_line(proc, format!("// CALL {}(...)", full_name));
        return;
    };

    let method = java_method_name(func);
    let args: Vec<String> = call.arguments.iter()
        .map(|a| crate::expr::expr_to_java(a, proc))
        .collect();
    let args_java = args.join(", ");

    // Try to resolve the target package
    if let Some(matched_pkg) = resolve_package_name(pkg, ctx.summaries) {
        let svc_name = format!("{}Service", {
            let cn = package_to_classname(&matched_pkg);
            let mut c = cn.chars();
            match c.next() {
                Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                None => String::new(),
            }
        });
        
        let is_self_call = matched_pkg.to_lowercase() == proc.package.to_lowercase();
        
        if is_self_call {
            push_logic_line(proc, format!("this.{}({});", method, args_java));
        } else {
            proc.service_calls.push(ServiceCall {
                service_name: svc_name.clone(),
                method_name: method.clone(),
                args: Vec::new(),
                package_name: matched_pkg,
            });
            push_logic_line(proc, format!("{}.{}({});", svc_name, method, args_java));
        }
    } else {
        // Unresolved call
        let full_name = name_parts.join(".");
        push_logic_line(proc, format!("// CALL {}({})", full_name, args_java));
    }
}
```

### Task 6: Improve PERFORM handler

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Change the `PlStatement::Perform` match arm to handle cross-service calls:

```rust
PlStatement::Perform { parsed_expr, query, .. } => {
    if let Some(expr) = parsed_expr {
        let val = crate::expr::expr_to_java(expr, proc);
        let trimmed = val.trim();

        // Check if it's a cross-service function call
        let resolved = try_resolve_perform_call(expr, proc, ctx);
        if resolved {
            // already pushed the line
        } else if trimmed.starts_with(|c: char| c.is_ascii_digit()) || trimmed == "null" {
            push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
        } else {
            push_logic_line(proc, format!("{};", val));
        }
    } else {
        push_logic_line(proc, format!("// PERFORM: {};", query.replace('\n', " ")));
    }
    Ok(())
}
```

Add `try_resolve_perform_call` function:
```rust
fn try_resolve_perform_call(
    expr: &ogsql_parser::ast::Expr,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) -> bool {
    use ogsql_parser::ast::Expr;
    match expr {
        Expr::FunctionCall(func) => {
            let name_parts: Vec<&str> = func.name.iter().map(|s| s.as_str()).collect();
            let (pkg, func_name) = if name_parts.len() >= 3 {
                (name_parts[name_parts.len() - 2], name_parts[name_parts.len() - 1])
            } else if name_parts.len() == 2 {
                (name_parts[0], name_parts[1])
            } else {
                return false;
            };
            
            let method = java_method_name(func_name);
            let args: Vec<String> = func.args.iter()
                .map(|a| crate::expr::expr_to_java(a, proc))
                .collect();
            let args_java = args.join(", ");
            
            if let Some(matched_pkg) = resolve_package_name(pkg, ctx.summaries) {
                let svc_name = format!("{}Service", {
                    let cn = package_to_classname(&matched_pkg);
                    let mut c = cn.chars();
                    match c.next() {
                        Some(f) => f.to_ascii_lowercase().to_string() + c.as_str(),
                        None => String::new(),
                    }
                });
                
                let is_self_call = matched_pkg.to_lowercase() == proc.package.to_lowercase();
                
                if is_self_call {
                    push_logic_line(proc, format!("this.{}({});", method, args_java));
                } else {
                    proc.service_calls.push(ServiceCall {
                        service_name: svc_name.clone(),
                        method_name: method.clone(),
                        args: Vec::new(),
                        package_name: matched_pkg,
                    });
                    push_logic_line(proc, format!("{}.{}({});", svc_name, method, args_java));
                }
                return true;
            }
            false
        }
        _ => false,
    }
}
```

### Task 7: Update process_statement signature

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Change the function signature:
```rust
pub fn process_statement(
    stmt: &ogsql_parser::ast::plpgsql::PlStatement,
    proc: &mut ProcedureInfo,
) -> Result<(), ConversionError> {
```

To:
```rust
pub fn process_statement(
    stmt: &ogsql_parser::ast::plpgsql::PlStatement,
    proc: &mut ProcedureInfo,
    ctx: &mut StatementContext,
) -> Result<(), ConversionError> {
```

Update ALL internal recursive calls to `process_statement(s, proc)` to `process_statement(s, proc, ctx)`.

### Task 8: Update analyze.rs to pass context

**Files:**
- Modify: `crates/fluxgauss/src/analyze.rs`

Change `analyze_procedure` to create and pass `StatementContext`:

```rust
pub fn analyze_procedure(
    proc: &mut ProcedureInfo,
    summaries: &std::collections::HashMap<String, crate::types::PackageSummary>,
    ctx: &mut AnalysisContext,
) -> Result<(), ConversionError> {
    let body = proc.body.take();
    let mut result = Ok(());
    if let Some(ref body_inner) = &body {
        proc.goto_analysis = Some(crate::statements::goto::analyze_goto_patterns(&body_inner.body));
        for decl in &body_inner.declarations {
            process_declaration(decl, proc);
        }
        let mut stmt_ctx = crate::context::StatementContext::new(summaries);
        for stmt in &body_inner.body {
            if let Err(e) = crate::statement::process_statement(stmt, proc, &mut stmt_ctx) {
                ctx.stub_procedures
                    .insert((proc.name.clone(), proc.parameters.len()));
                result = Err(e);
                break;
            }
        }
        // ... rest stays the same (goto analysis etc.)
    }
    proc.body = body;
    result
}
```

Also need to update the GOTO rewrite to pass context (or use a default empty context since it's a rewrite pass):
- For `crate::statements::goto::rewrite_with_pattern`, it also calls `process_statement` internally. Check if it needs updating.

### Task 9: Update goto.rs if needed

**Files:**
- Check: `crates/fluxgauss/src/statements/goto.rs`

The `rewrite_with_pattern` function may call `process_statement`. If so, it needs the `ctx` parameter too. However, for the GOTO rewrite pass, we can create a minimal context since it's just restructuring. Check and update as needed.

### Task 10: Fix tests in statement.rs

**Files:**
- Modify: `crates/fluxgauss/src/statement.rs`

Update all test functions to pass a StatementContext:
```rust
fn empty_stmt_ctx() -> StatementContext<'static> {
    StatementContext {
        summaries: Box::leak(Box::new(HashMap::new())),
        dml_counter: HashMap::new(),
    }
}
```

Or simpler approach — make `summaries` optional or use a helper.

### Task 11: Build and verify

**Commands:**
```bash
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java
cargo build --manifest-path crates/fluxgauss/Cargo.toml 2>&1
```

Fix any compilation errors. Then run:
```bash
cargo test --manifest-path crates/fluxgauss/Cargo.toml 2>&1
```

### Task 12: Run converter and compare output

```bash
# Run Rust converter
cd /Users/c2j/Projects/Desktop_Projects/DB/sp2java
cargo run --manifest-path crates/fluxgauss/Cargo.toml -- -c fluxgauss_rust.yaml -o ./dest_ru

# Compare mapper XML
diff <(cat dest_py/src/main/resources/mapper/OrderMapper.xml) <(cat dest_ru/src/main/resources/mapper/OrderMapper.xml)

# Compare service
diff <(cat dest_py/src/main/java/ced/service/OrderService.java) <(cat dest_ru/src/main/java/ced/service/OrderService.java)

# Compare mapper interface
diff <(cat dest_py/src/main/java/ced/mapper/OrderMapper.java) <(cat dest_ru/src/main/java/ced/mapper/OrderMapper.java)
```

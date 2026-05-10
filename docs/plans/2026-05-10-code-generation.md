# Code Generation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement full code generation so `cargo run --config demo-project/fluxgauss.yaml` produces 119 files matching Python converter output.

**Architecture:** Six generator modules (skeleton, writer, mapper, service, test, itest) produce Spring Boot + MyBatis Java project files. A `CodeWriter` utility handles indentation and formatting. Each generator takes `&PackageInfo` + config and writes files to disk.

**Tech Stack:** Rust (fluxgauss crate), std::fs for file I/O, existing types/naming/type_map modules

**Reference:** Python converter `converter/flux_gauss.py` lines 5227–7110

---

## Task 1: CodeWriter Utility

**Files:**
- Write: `crates/fluxgauss/src/generate/writer.rs`

**What:** A `CodeWriter` struct that manages indentation levels and line output. This is used by all generators.

```rust
pub struct CodeWriter {
    lines: Vec<String>,
    indent_level: usize,
}

impl CodeWriter {
    pub fn new() -> Self;
    pub fn line(&mut self, text: &str);           // adds current indent + text
    pub fn blank(&mut self);                       // empty line
    pub fn push_indent(&mut self);                 // indent_level += 1
    pub fn pop_indent(&mut self);                  // indent_level -= 1
    pub fn indented<F: FnOnce(&mut Self)>(&mut self, f: F); // push, call f, pop
    pub fn to_string(&self) -> String;             // join lines with \n
    pub fn write_to_file(&self, path: &Path) -> std::io::Result<()>; // to_string + write
}
```

**Tests:** 5 tests covering indent push/pop, blank lines, to_string output, file write.

---

## Task 2: Skeleton Files

**Files:**
- Write: `crates/fluxgauss/src/generate/skeleton.rs`

**What:** Generate project skeleton files that are written only once (if they don't exist):
1. `pom.xml` — Maven POM with Spring Boot 3.2.5, MyBatis, PostgreSQL, Lombok, Testcontainers
2. `src/main/resources/application.yml` — datasource config + mybatis mapper-locations
3. `src/main/java/.../DemoApplication.java` — Spring Boot main class with @MapperScan
4. `src/main/java/.../exception/BusinessException.java` — runtime exception class

**Key Logic from Python:**
- `_write_pom_xml()` (line 5319): hardcoded XML with logger deps appended
- `_write_application_yml()` (line 5455): YAML with db config from AppConfig
- `_write_main_application()` (line 5479): Java class with BASE_PACKAGE
- `_write_business_exception()` (line 5501): simple Java exception class

**Parameters:** `output_dir: &Path, config: &AppConfig, base_package: &str`

**Tests:** 4 tests — one per skeleton file, verifying content contains expected markers (e.g., `spring-boot-starter-parent`, `mybatis`, `@SpringBootApplication`, `BusinessException`).

---

## Task 3: Mapper Interface Generator

**Files:**
- Write: `crates/fluxgauss/src/generate/mapper.rs`

**What:** Generate `{Name}Mapper.java` — MyBatis mapper interface with method signatures for all DML statements.

**Output Example** (from `OrderMapper.java`):
```java
package com.example.demo.mapper;

import java.util.List;
import java.util.Map;
import org.apache.ibatis.annotations.*;

@Mapper
public interface OrderMapper {
    // pkg_order.sql:1 — pkg_order.create_order
    int insertCreateOrder(@Param("pUserId") Long pUserId, @Param("pProductId") Long pProductId, @Param("pQty") Integer pQty);
    // pkg_order.sql:14 — pkg_order.cancel_order
    Map<String, Object> selectCancelOrder(@Param("pOrderId") Long pOrderId);
}
```

**Key Logic from Python:**
- `_write_mapper_interface()` (line 5521): iterates `pkg.procedures[].dml_statements[]`
- `_build_mapper_method()` (line 5571): determines return type from `dml.sql_type`:
  - select → `List<Map<String, Object>>` (returns_list) or `Map<String, Object>` or specific type
  - insert/update/delete → `int`
  - else → `void`
- Parameters: IN params (skip OUT), local vars used in SQL, extra_params from dml
- Each param gets `@Param("name") Type name`

**Dependencies:** `writer.rs`, `naming.rs`, `type_map.rs`

**Tests:** 5 tests — empty package, single DML, select return types, local var params, extra params.

---

## Task 4: Mapper XML Generator

**Files:**
- Continue: `crates/fluxgauss/src/generate/mapper.rs` (same file, separate functions)

**What:** Generate `src/main/resources/mapper/{Name}Mapper.xml` — MyBatis XML mapper with SQL statements.

**Output Example** (from `OrderMapper.xml`):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.example.demo.mapper.OrderMapper">
    <!-- Source: pkg_order.sql:1-12 — pkg_order.create_order.insertCreateOrder -->
    <insert id="insertCreateOrder">
        insert into t_orders(...) values(...)
    </insert>
</mapper>
```

**Key Logic from Python:**
- `_write_mapper_xml()` (line 5633): wraps each DML in `<insert>/<select>/<update>/<delete>`
- `_build_mapper_statement()` (line 5698): complex SQL transformation pipeline:
  1. Clean SQL (remove extra whitespace, trailing semicolons)
  2. Replace cross-package function calls (`pkg.get_sys_date()` → `CURRENT_TIMESTAMP`)
  3. Replace sequence refs (`SEQ.NEXTVAL` → `nextval('seq')`)
  4. Strip double-quoted identifiers (except reserved words)
  5. Protect MyBatis placeholders from formatting
  6. Format SQL via ogsql binary (optional, best-effort)
  7. Convert params to MyBatis `#{param, jdbcType=X, javaType=Y}` syntax
  8. Add `LIMIT 1` for non-list selects
  9. XML-escape (`&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`)
- `_convert_params_to_mybatis()` (line 5813): two-pass replacement (composite fields first, then simple params)

**This is the most complex generator.** SQL transformation needs careful porting.

**Tests:** 6 tests — insert statement, select with LIMIT, param conversion, XML escaping, sequence replacement, reserved word handling.

---

## Task 5: Service Class Generator

**Files:**
- Write: `crates/fluxgauss/src/generate/service.rs`

**What:** Generate `{Name}Service.java` — Spring @Service class with procedure methods.

**Output Example** (from `OrderService.java`):
```java
@Service
// Source: pkg_order.sql
public class OrderService {
    private static final Logger log = LoggerFactory.getLogger(OrderService.class);
    private final OrderMapper orderMapper;
    private final InventoryService inventoryService;

    public OrderService(OrderMapper orderMapper, InventoryService inventoryService) {
        this.orderMapper = orderMapper;
        this.inventoryService = inventoryService;
    }

    // Source: pkg_order.create_order (PROCEDURE) — pkg_order.sql:1-12
    @Transactional
    public void createOrder(long pUserId, long pProductId, int pQty) {
        inventoryService.reserveStock(pProductId, pQty);
        orderMapper.insertCreateOrder(pUserId, pProductId, pQty);
        commonService.logOperation("ORDER", "CREATE", 0);
    }
}
```

**Key Logic from Python:**
- `_write_service_class()` (line 5873): builds full class with constructor injection
- `_build_service_method()` (line 6195): per-procedure method generation:
  - Params: IN params as primitives, OUT params as `AtomicReference<T>`
  - Return type: function → return_type, procedure → void (or `List<Map>` for REFCURSOR OUT)
  - Body: local var declarations → java_logic_lines → try/catch if exception_block
  - Annotations: `@Transactional` for DML operations
  - Stub detection: complex patterns get TODO comment + stub body
- `_collect_service_injections()` (line 5307): cross-service dependencies from service_calls

**Tests:** 5 tests — simple method, function return type, OUT params, service injection, stub body.

---

## Task 6: Service Test Generator

**Files:**
- Write: `crates/fluxgauss/src/generate/test.rs`

**What:** Generate `{Name}ServiceTest.java` — Mockito unit tests.

**Output Example** (from `OrderServiceTest.java`):
```java
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class OrderServiceTest {
    @Mock private OrderMapper orderMapper;
    @Mock private InventoryService inventoryService;
    @InjectMocks private OrderService service;

    @Test
    @Timeout(value = 5, unit = TimeUnit.SECONDS)
    void test_createOrder_success() {
        Long pUserId = 1L;
        when(orderMapper.insertCreateOrder(any(), any(), any())).thenReturn(1);
        service.createOrder(pUserId, pProductId, pQty);
    }
}
```

**Key Logic from Python:**
- `_write_service_test()` (line 6545): generates test class with @Mock/@InjectMocks
- `_build_test_methods()` (line 6657): per-procedure test methods (success + error if has RAISE)
- `_build_success_test()` (line 6756): mocks ALL mapper methods in package, then calls service method
- `_mock_all_mapper_methods()` (line 6903): collects all DMLs across all procs, generates `when(mapper.method(any()...)).thenReturn(mock_value)` for each
- `_mock_select_return()` (line 6875): determines mock return based on SQL type and result type
- `_default_test_value()` (line 6704): generates test values by type (long→1L, String→"test_...", etc.)
- `_build_error_test()` (line 6932): uses `assumeTrue(false)` for auto-generated error tests

**Tests:** 4 tests — success test generation, error test with RAISE, mock setup for select/insert, default test values.

---

## Task 7: Integration Test Generator

**Files:**
- Write: `crates/fluxgauss/src/generate/itest.rs`

**What:** Generate integration test files (optional, only when `integration_test.enabled` in config).

**Key Logic from Python:**
- `_itest_write_infrastructure()` — `AbstractIntegrationTest.java` base class
- `_itest_write_schema_sql()` — `itest-schema.sql` fixtures
- `_itest_write_class()` — per-package integration test class
- `_itest_collect_schemas()` — extracts table DDL from SQL sources

**Tests:** 3 tests — infrastructure class content, schema SQL generation, test class structure.

---

## Task 8: Pipeline Integration

**Files:**
- Modify: `crates/fluxgauss/src/pipeline.rs` (phase3_generate function)
- Modify: `crates/fluxgauss/src/main.rs` (output statistics)

**What:** Wire all generators into the pipeline's Phase 3.

**Step 1:** Replace stub `phase3_generate()` with actual generation:
1. Call `skeleton::write_skeleton_files()` (only if not existing)
2. For each package: call mapper, mapper_xml, service, test generators
3. If integration_test enabled: call itest generator
4. Track generated file paths, errors

**Step 2:** Update main.rs output to show:
- Files generated count
- Procedures processed count
- Warnings/errors

**Step 3:** Verify `cargo run --config demo-project/fluxgauss.yaml` produces files in `dest/`.

**Tests:** Integration test that runs the full pipeline with a minimal SQL file and verifies output files exist.

---

## Task 9: Golden Master Verification

**Files:**
- Write: `crates/fluxgauss/tests/golden_master_test.rs` (integration test)

**What:** Compare Rust output against Python output for `demo-project`.

**Step 1:** Run Python converter to establish baseline in `dest/`
**Step 2:** Clear `dest/`, run Rust converter
**Step 3:** Compare file lists (same files generated?)
**Step 4:** Compare file contents (byte-level or semantic comparison)
**Step 5:** Run `mvn compile` on Rust output to verify it compiles

**Acceptance criteria:**
- Same number of files generated (119)
- All Service/Mapper/Test files present
- `mvn compile` passes on generated output
- Key file content matches Python output (Service.java, Mapper.java, Mapper.xml)

---

## Implementation Order

The tasks have dependencies:
- Task 1 (writer) → all other tasks
- Task 2 (skeleton) → independent, can run after Task 1
- Task 3 (mapper interface) → after Task 1, needs DmlStatement from types
- Task 4 (mapper XML) → after Task 1, most complex, can parallel with Task 5
- Task 5 (service) → after Task 1, depends on java_logic_lines being populated
- Task 6 (test) → after Tasks 3+5 (needs mapper method names)
- Task 7 (itest) → after Task 5, optional
- Task 8 (integration) → after Tasks 2-7
- Task 9 (verification) → after Task 8

**Parallel execution groups:**
- Group A: Tasks 1+2 (writer + skeleton)
- Group B: Tasks 3+4+5 (mapper interface, mapper XML, service) — can be parallel
- Group C: Tasks 6+7 (test + itest)
- Group D: Tasks 8+9 (integration + verification)

---

## Missing Prerequisites

Before code generation can produce meaningful output, the analyzer must populate these fields in `ProcedureInfo`:
- `dml_statements: Vec<DmlStatement>` — extracted from SQL statements in the procedure body
- `java_logic_lines: Vec<String>` — converted PL/pgSQL logic
- `service_calls: Vec<ServiceCall>` — cross-package call references
- `imports: BTreeSet<String>` — required Java imports

Currently `analyze_procedure()` only handles declarations and basic statements. The DML extraction and java_logic_lines generation happen during `_process_statement()` which is partially implemented (only handles Assignment, Return, Null, Perform, Raise, Commit, Rollback).

**This means Tasks 3-6 will produce empty/minimal output until statement processing is more complete.** The generators should handle empty DML lists and empty java_logic_lines gracefully (like the Python version does with "No direct DML operations" comments).

## Critical Python Functions to Port

These functions contain non-trivial logic and must be ported carefully:

| Function | Lines | Purpose |
|---|---|---|
| `_build_mapper_method()` | 5571–5631 | Mapper interface method signatures |
| `_build_mapper_statement()` | 5698–5810 | XML mapper SQL transformation pipeline |
| `_convert_params_to_mybatis()` | 5813–5870 | SQL param → MyBatis #{param} conversion |
| `_build_service_method()` | 6195–6427 | Service method body generation |
| `_build_success_test()` | 6756–6793 | Test method generation |
| `_mock_all_mapper_methods()` | 6903–6922 | Mock setup for all DML methods |
| `_mock_select_return()` | 6875–6900 | Mock return value generation |
| `_has_compilation_issues()` | 6430–6516 | Static analysis for stub detection |

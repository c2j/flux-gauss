# Integration Testing — 集成测试自动生成

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为转换后的 Spring Boot + MyBatis 项目自动生成集成测试，连接真实数据库验证 SQL 正确性，支持 Testcontainers（自动容器）和 Remote（直连目标库）两种模式。

**Architecture:** 在现有单元测试（Mock）之外，新增集成测试层。通过 YAML 配置的 `integration_test` 块控制生成。自动从 SQL 源文件提取 DDL（建表语句）和 DML（INSERT 语义）推断测试数据。生成 `@SpringBootTest` 测试类 + Maven Profile `-Pintegration` 隔离运行。

**Tech Stack:** Python 3.9+, JUnit 5, Spring Boot Test, MyBatis Spring Boot Test, Testcontainers (PostgreSQL module), Maven Surefire/Failsafe

---

## 前置知识

### 文件结构
- **唯一需要修改的文件**: `converter/flux_gauss.py`（~6800 行）
- 所有新增逻辑在此文件中，以 `_itest_` 前缀命名新函数，与现有 `_write_service_test` 等函数区分

### 现有测试生成管线
```
generate_project() (L4319)
  └── _write_service_test() (L5557)  ← 现有单元测试
        ├── _build_test_methods() (L5673)
        ├── _build_success_test() (L5778)     ← Mock 测试
        └── _build_error_test() (L5953)       ← Mock 测试（assumeTrue false 跳过）
```

### 现有可用数据
- `parse_table_ddl()` (L176) — 已有 DDL 解析，返回 `{table_name: {col: sql_type}}`
- `ProcedureInfo.dml_statements` — 每个存储过程的所有 DML 语句（含 `sql_text`, `sql_type`, `result_type`）
- `ProcedureInfo.parameters` — 参数列表（含 `java_type`, `java_name`, `is_out`）
- `PackageInfo.table_refs` — 包引用的所有表名
- `TYPE_OVERRIDES` 全局字典 — 已收集的 `(table, column) → sql_type` 映射
- `_default_test_value()` (L5726) — 已有的测试值推断逻辑

### YAML 配置扩展

```yaml
# fluxgauss.yaml 新增块
integration_test:
  enabled: true
  mode: testcontainers          # testcontainers | remote
  # ── Testcontainers 模式 ──
  image: opengauss/opengauss:latest  # Docker 镜像，默认使用本地 OpenGauss
  # ── Remote 模式 ──
  # url: jdbc:postgresql://remote-host:5432/testdb
  # username: test_user
  # password: test_pass
  # ── 通用配置 ──
  init_sql:                     # 可选：额外的建表/初始数据脚本
    - demo-project/sql/tables.sql
```

### 生成的文件结构
```
dest/
├── src/test/java/{pkg}/
│   ├── service/
│   │   └── {Name}ServiceTest.java           ← 现有 Mock 单元测试
│   └── itest/                                ← 新增：集成测试目录
│       ├── AbstractIntegrationTest.java      ← 公共基类
│       └── {Name}ServiceIntegrationTest.java ← 每包一个集成测试
├── src/test/resources/
│   ├── application-integration.yml           ← 集成测试 Spring Profile
│   └── itest-schema.sql                      ← 汇总的建表 DDL
└── pom.xml                                   ← 新增 Maven Profile + Testcontainers 依赖
```

---

## Task 1: YAML 配置解析

**Files:**
- Modify: `converter/flux_gauss.py` (配置加载函数区域)

**Step 1: 定位 YAML 配置加载位置**

搜索 `def main` 或 `yaml.safe_load` 找到 YAML 配置加载入口。当前 `database:` 块已在 `_write_application_yml()` 中被读取（L4466-4487）。

**Step 2: 添加 `integration_test` 配置读取**

在 `generate_project()` 函数签名中添加 `itest_config` 参数透传，或在现有 `config` dict 中读取 `integration_test` 子键：

```python
def generate_project(output_dir: str, packages: list, changed_packages: set = None,
                     config: dict = None, progress_cb=None):
    # ... existing code ...
    
    # ── Integration test generation ──
    itest_cfg = (config or {}).get("integration_test", {})
    if itest_cfg.get("enabled"):
        schema_map = _collect_all_schemas(packages)  # 从 TYPE_OVERRIDES 汇总
        _write_itest_infrastructure(base_path, itest_cfg)
        for pkg in active_pkgs:
            _write_itest_class(base_path, pkg, itest_cfg, schema_map, all_packages)
```

**Step 3: 在 pipeline 主函数中传递配置**

在 `_run_pipeline()` 或 `main()` 中，确保 `integration_test` 配置被传递到 `generate_project()`。

**Step 4: 验证**

运行 `python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml`，确认不启用集成测试时不生成任何新文件。

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml && find dest/src/test -name "*Integration*" | head
# Expected: 无输出（因为 YAML 中未配置 integration_test）
```

**Step 5: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: parse integration_test config from YAML"
```

---

## Task 2: 公共基类 + Testcontainers 基础设施

**Files:**
- Modify: `converter/flux_gauss.py` (新增 `_itest_` 前缀函数)
- Generated: `dest/src/test/java/{pkg}/itest/AbstractIntegrationTest.java`
- Generated: `dest/src/test/resources/application-integration.yml`

**Step 1: 编写 `_write_itest_infrastructure()` 函数**

```python
def _write_itest_infrastructure(base_path: Path, itest_cfg: dict):
    """Generate shared integration test base class and test profile config."""
    jp = BASE_PACKAGE  # 使用基础包
    itest_dir = base_path / "src/test/java" / jp.replace(".", "/") / "itest"
    itest_dir.mkdir(parents=True, exist_ok=True)
    
    mode = itest_cfg.get("mode", "testcontainers")
    
    if mode == "testcontainers":
        _write_testcontainers_base(base_path, itest_dir, jp, itest_cfg)
    else:
        _write_remote_base(base_path, itest_dir, jp, itest_cfg)
    
    _write_itest_application_yml(base_path, itest_cfg)
```

**Step 2: 生成 Testcontainers 基类**

```python
def _write_testcontainers_base(base_path: Path, itest_dir: Path, jp: str, itest_cfg: dict):
    image = itest_cfg.get("image", "opengauss/opengauss:latest")
    
    content = textwrap.dedent(f"""\
        package {jp}.itest;

        import org.springframework.boot.test.context.SpringBootTest;
        import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
        import org.springframework.test.context.DynamicPropertySource;
        import org.testcontainers.containers.PostgreSQLContainer;
        import org.testcontainers.junit.jupiter.Container;
        import org.testcontainers.junit.jupiter.Testcontainers;

        @SpringBootTest
        @ActiveProfiles("integration")
        @Testcontainers
        public abstract class AbstractIntegrationTest {{

            @Container
            static PostgreSQLContainer<?> pg = new PostgreSQLContainer<>("{image}")
                    .withDatabaseName("itest");

            @DynamicPropertySource
            static void configureProperties(DynamicPropertyRegistry registry) {{
                registry.add("spring.datasource.url", pg::getJdbcUrl);
                registry.add("spring.datasource.username", pg::getUsername);
                registry.add("spring.datasource.password", pg::getPassword);
            }}
        }}
    """)
    (itest_dir / "AbstractIntegrationTest.java").write_text(content)
```

**Step 3: 生成 Remote 模式基类**

```python
def _write_remote_base(base_path: Path, itest_dir: Path, jp: str, itest_cfg: dict):
    content = textwrap.dedent(f"""\
        package {jp}.itest;

        import org.springframework.boot.test.context.SpringBootTest;
        import org.springframework.test.context.ActiveProfiles;

        @SpringBootTest
        @ActiveProfiles("integration")
        public abstract class AbstractIntegrationTest {{
        }}
    """)
    (itest_dir / "AbstractIntegrationTest.java").write_text(content)
```

**Step 4: 生成 `application-integration.yml`**

```python
def _write_itest_application_yml(base_path: Path, itest_cfg: dict):
    mode = itest_cfg.get("mode", "testcontainers")
    test_res = base_path / "src/test/resources"
    test_res.mkdir(parents=True, exist_ok=True)
    
    if mode == "remote":
        url = itest_cfg.get("url", "jdbc:postgresql://localhost:5432/testdb")
        username = itest_cfg.get("username", "postgres")
        password = itest_cfg.get("password", "postgres")
        content = textwrap.dedent(f"""\
            spring:
              datasource:
                url: {url}
                username: {username}
                password: {password}
                driver-class-name: org.postgresql.Driver
            mybatis:
              mapper-locations: classpath:mapper/*.xml
              configuration:
                map-underscore-to-camel-case: true
        """)
    else:
        # Testcontainers 模式：数据源由 @DynamicPropertySource 动态注入
        # 这里只放 mybatis 配置
        content = textwrap.dedent("""\
            mybatis:
              mapper-locations: classpath:mapper/*.xml
              configuration:
                map-underscore-to-camel-case: true
        """)
    (test_res / "application-integration.yml").write_text(content)
```

**Step 5: 验证编译**

在 YAML 中临时启用 `integration_test`，运行转换，检查生成的 Java 文件语法是否正确（此时 pom.xml 还没加 Testcontainers 依赖，编译会失败——这是预期的，下一步解决）。

**Step 6: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: generate integration test base class and test profile"
```

---

## Task 3: Maven POM 扩展（Testcontainers 依赖 + Integration Profile）

**Files:**
- Modify: `converter/flux_gauss.py` (L4377 `_write_pom_xml` 函数)

**Step 1: 修改 `_write_pom_xml()` 增加可选依赖**

在现有 `spring-boot-starter-test` 依赖之后，添加 Testcontainers 相关依赖（始终生成，scope=test，不影响非集成测试）：

```xml
<!-- Integration test dependencies -->
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>testcontainers</artifactId>
    <version>1.19.8</version>
    <scope>test</scope>
</dependency>
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>postgresql</artifactId>
    <version>1.19.8</version>
    <scope>test</scope>
</dependency>
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>junit-jupiter</artifactId>
    <version>1.19.8</version>
    <scope>test</scope>
</dependency>
```

**Step 2: 添加 Maven Profile**

在 `</build>` 之前添加 integration profile：

```xml
<profiles>
    <profile>
        <id>integration</id>
        <build>
            <plugins>
                <plugin>
                    <groupId>org.apache.maven.plugins</groupId>
                    <artifactId>maven-failsafe-plugin</artifactId>
                    <executions>
                        <execution>
                            <goals>
                                <goal>integration-test</goal>
                                <goal>verify</goal>
                            </goals>
                        </execution>
                    </executions>
                </plugin>
            </plugins>
        </build>
    </profile>
</profiles>
```

**Step 3: 验证**

删除现有 `dest/pom.xml`，重新运行转换，确认新 pom.xml 包含 Testcontainers 依赖。

```bash
rm dest/pom.xml
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
grep -A2 "testcontainers" dest/pom.xml
# Expected: 看到 testcontainers 依赖
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: add Testcontainers dependencies and Maven integration profile"
```

---

## Task 4: DDL Schema 汇总 + `itest-schema.sql` 生成

**Files:**
- Modify: `converter/flux_gauss.py` (新增 `_itest_` 函数)
- Generated: `dest/src/test/resources/itest-schema.sql`

**Step 1: 编写 `_collect_all_schemas()` 函数**

利用已有的 `TYPE_OVERRIDES` 全局字典和 `parse_table_ddl()` 的结果，汇总所有表的 DDL：

```python
def _collect_all_schemas(packages: list) -> dict:
    """Collect all table schemas from TYPE_OVERRIDES and package table_refs."""
    # TYPE_OVERRIDES 是 {(table, col): sql_type} 格式
    tables = {}
    for (tbl, col), sql_type in TYPE_OVERRIDES.items():
        if tbl not in tables:
            tables[tbl] = {}
        tables[tbl][col] = sql_type
    return tables
```

**Step 2: 生成 `itest-schema.sql`**

```python
def _write_itest_schema_sql(base_path: Path, packages: list, itest_cfg: dict):
    """Generate DDL script for integration test schema setup."""
    test_res = base_path / "src/test/resources"
    test_res.mkdir(parents=True, exist_ok=True)
    
    tables = _collect_all_schemas(packages)
    
    lines = ["-- Auto-generated integration test schema"]
    lines.append("-- Generated by FluxGauss integration test support")
    lines.append("")
    
    for tbl, cols in sorted(tables.items()):
        lines.append(f"CREATE TABLE IF NOT EXISTS {tbl} (")
        col_defs = []
        for col, col_type in sorted(cols.items()):
            col_defs.append(f"    {col} {col_type}")
        lines.append(",\n".join(col_defs))
        lines.append(");")
        lines.append("")
    
    # 追加用户自定义的 init_sql
    init_scripts = itest_cfg.get("init_sql", [])
    if init_scripts:
        lines.append("-- User-provided initialization scripts")
        for script_path in init_scripts:
            if os.path.exists(script_path):
                with open(script_path, 'r') as f:
                    lines.append(f"-- Source: {script_path}")
                    lines.append(f.read())
                    lines.append("")
    
    (test_res / "itest-schema.sql").write_text("\n".join(lines))
```

**Step 3: 在基类中引用 schema.sql**

修改 `AbstractIntegrationTest` 基类，添加 `@Sql` 注解在 Testcontainers 容器启动后执行建表：

```java
import org.springframework.test.context.jdbc.Sql;

@Sql(scripts = "classpath:itest-schema.sql", executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD)
```

**Step 4: 验证**

```bash
rm dest/src/test -rf
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml
cat dest/src/test/resources/itest-schema.sql | head -20
# Expected: 看到建表 DDL
```

**Step 5: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: generate itest-schema.sql from collected table DDL"
```

---

## Task 5: 测试数据自动推断

**Files:**
- Modify: `converter/flux_gauss.py` (新增 `_itest_` 函数)

这是核心逻辑。从存储过程的 DML 语句中推断需要准备哪些测试数据。

**Step 1: 编写 `_infer_test_data()` 函数**

分析每个存储过程的 DML 语句，推断需要 INSERT 的表和数据：

```python
def _infer_test_data(proc: ProcedureInfo, pkg: PackageInfo, schema_map: dict) -> list:
    """Analyze DML statements to infer required test data (INSERT statements).
    
    Returns list of {"table": str, "columns": {col: value_expr}} for each required INSERT.
    """
    inserts = []
    seen_tables = set()
    
    for dml in proc.dml_statements:
        sql = dml.sql_text.strip()
        sql_lower = sql.lower()
        
        # 从 SELECT ... FROM table 中提取被查询的表
        if dml.sql_type == "select":
            table = _extract_table_from_select(sql)
            if table and table not in seen_tables and table in schema_map:
                seen_tables.add(table)
                columns = schema_map[table]
                row = {}
                for col, col_type in columns.items():
                    row[col] = _generate_test_data_value(col, col_type)
                inserts.append({"table": table, "columns": row})
        
        # 从 INSERT INTO table 中提取被插入的表（可能不需要预插入，但需要清理）
        if dml.sql_type == "insert":
            table = _extract_table_from_insert(sql)
            if table:
                seen_tables.add(table)  # 标记为已处理，避免重复预插入
        
        # 从 UPDATE/DELETE 中提取表
        if dml.sql_type in ("update", "delete"):
            table = _extract_table_from_update_delete(sql)
            if table and table not in seen_tables and table in schema_map:
                seen_tables.add(table)
                columns = schema_map[table]
                row = {}
                for col, col_type in columns.items():
                    row[col] = _generate_test_data_value(col, col_type)
                inserts.append({"table": table, "columns": row})
    
    return inserts


def _extract_table_from_select(sql: str) -> str:
    """Extract table name from SELECT ... FROM table_name."""
    m = re.search(r'\bfrom\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _extract_table_from_insert(sql: str) -> str:
    """Extract table name from INSERT INTO table_name."""
    m = re.search(r'\binto\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _extract_table_from_update_delete(sql: str) -> str:
    """Extract table name from UPDATE/DELETE table_name."""
    m = re.search(r'\b(?:update|delete\s+from)\s+(?:\w+\.)?(\w+)', sql, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _generate_test_data_value(col_name: str, sql_type: str) -> str:
    """Generate a reasonable test value for a column based on name and SQL type."""
    name_lower = col_name.lower()
    type_lower = sql_type.lower()
    
    # 按列名语义推断
    if "id" in name_lower:
        return "1"
    if any(k in name_lower for k in ("name", "title", "label", "desc", "status")):
        return "'test_data'"
    if any(k in name_lower for k in ("email", "mail")):
        return "'test@example.com'"
    if any(k in name_lower for k in ("phone", "tel", "mobile")):
        return "'13800138000'"
    if any(k in name_lower for k in ("price", "amount", "salary", "total", "cost", "balance")):
        return "100.00"
    if any(k in name_lower for k in ("qty", "quantity", "count", "num", "stock")):
        return "10"
    if any(k in name_lower for k in ("date", "time", "created", "updated")):
        return "'2025-01-01'"
    
    # 按类型推断
    if any(t in type_lower for t in ("int", "serial", "bigint", "smallint")):
        return "1"
    if any(t in type_lower for t in ("numeric", "decimal", "real", "float", "double")):
        return "100.00"
    if any(t in type_lower for t in ("varchar", "char", "text")):
        return "'test_data'"
    if any(t in type_lower for t in ("bool",)):
        return "true"
    if any(t in type_lower for t in ("timestamp", "date", "time")):
        return "'2025-01-01'"
    
    return "'test_data'"
```

**Step 2: 生成 SQL INSERT 语句**

```python
def _generate_insert_sql(test_data: list) -> str:
    """Generate INSERT statements from inferred test data."""
    lines = []
    for item in test_data:
        table = item["table"]
        columns = item["columns"]
        col_names = ", ".join(columns.keys())
        values = ", ".join(str(v) for v in columns.values())
        lines.append(f"INSERT INTO {table} ({col_names}) VALUES ({values});")
    return "\n".join(lines)
```

**Step 3: 验证**

用 `demo-project/sql/pkg_order.sql` 测试数据推断是否正确。检查生成的 INSERT 语句是否覆盖了存储过程查询的表。

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: infer test data from DML statements for integration tests"
```

---

## Task 6: 每包集成测试类生成

**Files:**
- Modify: `converter/flux_gauss.py` (新增 `_write_itest_class()`)
- Generated: `dest/src/test/java/{pkg}/itest/{Name}ServiceIntegrationTest.java`

**Step 1: 编写 `_write_itest_class()` 函数**

```python
def _write_itest_class(base_path: Path, pkg: PackageInfo, itest_cfg: dict, 
                        schema_map: dict, all_packages: dict):
    """Generate integration test class for one package."""
    jp = _pkg_java_package(pkg)
    itest_dir = base_path / "src/test/java" / jp.replace(".", "/") / "itest"
    itest_dir.mkdir(parents=True, exist_ok=True)
    
    class_name = f"{package_to_classname(pkg.package_name)}Service"
    test_class = f"{class_name}IntegrationTest"
    mapper_name = f"{class_name[0].lower()}{class_name[1:]}Mapper"
    
    # 收集 imports
    imports = set()
    imports.add("import org.junit.jupiter.api.Test;")
    imports.add("import org.junit.jupiter.api.Timeout;")
    imports.add("import org.springframework.beans.factory.annotation.Autowired;")
    imports.add("import org.springframework.test.context.jdbc.Sql;")
    imports.add(f"import {jp}.itest.AbstractIntegrationTest;")
    imports.add(f"import {jp}.service.{class_name};")
    imports.add(f"import {jp}.mapper.{package_to_classname(pkg.package_name)}Mapper;")
    imports.add("import static org.junit.jupiter.api.Assertions.*;")
    
    # 需要的类型 imports
    for proc in pkg.procedures:
        for p in proc.parameters:
            if "Map" in p.java_type:
                imports.add("import java.util.Map;")
            if "List" in p.java_type:
                imports.add("import java.util.List;")
            if "AtomicReference" in p.java_type:
                imports.add("import java.util.concurrent.atomic.AtomicReference;")
            if "BigDecimal" in p.java_type:
                imports.add("import java.math.BigDecimal;")
        dto_class = _get_dto_classname(proc, pkg)
        if dto_class:
            imports.add(f"import {jp}.dto.{dto_class};")
    
    # 生成测试方法
    test_methods = []
    for proc in pkg.procedures:
        test_data = _infer_test_data(proc, pkg, schema_map)
        method = _build_itest_method(proc, pkg, test_data, schema_map)
        test_methods.append(method)
    
    if not test_methods:
        test_methods.append(
            "    @Test\n"
            "    void testContextLoads() {\n"
            "        assertNotNull(mapper);\n"
            "        assertNotNull(service);\n"
            "    }"
        )
    
    # 组装类
    lines = []
    lines.append(f"package {jp}.itest;")
    lines.append("")
    for imp in sorted(imports):
        lines.append(imp)
    lines.append("")
    if pkg.source_file:
        lines.append(f"// Source: {pkg.source_file}")
    lines.append(f"class {test_class} extends AbstractIntegrationTest {{")
    lines.append("")
    lines.append("    @Autowired")
    lines.append(f"    private {package_to_classname(pkg.package_name)}Mapper {mapper_name};")
    lines.append("")
    lines.append("    @Autowired")
    lines.append(f"    private {class_name} service;")
    
    for tm in test_methods:
        lines.append("")
        lines.append(tm)
    
    lines.append("}")
    lines.append("")
    (itest_dir / f"{test_class}.java").write_text("\n".join(lines))
```

**Step 2: 编写 `_build_itest_method()` 函数**

```python
def _build_itest_method(proc: ProcedureInfo, pkg: PackageInfo, 
                         test_data: list, schema_map: dict) -> str:
    """Generate a single integration test method."""
    method_name = java_method_name(proc.proc_name)
    lines = []
    
    # 生成 @Sql 注解准备测试数据
    if test_data:
        insert_sql = _generate_insert_sql(test_data)
        # 将 INSERT 语句写入单独的 SQL 文件并用 @Sql 引用
        # 或者直接用 @Sql(statements = {...}) 内联
        sql_stmts = []
        for item in test_data:
            table = item["table"]
            columns = item["columns"]
            col_names = ", ".join(columns.keys())
            values = ", ".join(str(v) for v in columns.values())
            sql_stmts.append(f'"{table}"')  # 简化表示，实际生成完整 INSERT
        lines.append("    @Test")
    else:
        lines.append("    @Test")
    
    lines.append(f"    @Timeout(value = 10, unit = java.util.concurrent.TimeUnit.SECONDS)")
    lines.append(f"    void test_{method_name}_integration() {{")
    
    # 参数准备（复用 _default_test_value 逻辑）
    in_params = [p for p in proc.parameters if not p.is_out]
    out_params = [p for p in proc.parameters if p.is_out]
    
    for p in in_params:
        val = _default_test_value(p.java_type, p.java_name, pkg=pkg)
        lines.append(f"        {p.java_type} {p.java_name} = {val};")
    
    for p in out_params:
        if p.is_refcursor:
            continue
        lines.append(f"        AtomicReference<{p.java_type}> {p.java_name} = new AtomicReference<>(null);")
    
    # 调用 service 方法
    args = [p.java_name for p in in_params]
    args += [p.java_name for p in out_params if not p.is_refcursor]
    args_str = ", ".join(args)
    
    if proc.is_function:
        lines.append(f"        var result = service.{method_name}({args_str});")
        lines.append(f"        // TODO: Add domain-specific assertions")
        lines.append(f"        // assertNotNull(result);")
    else:
        lines.append(f"        service.{method_name}({args_str});")
        lines.append(f"        // TODO: Add domain-specific assertions")
    
    lines.append("    }")
    return "\n".join(lines)
```

**Step 3: 验证**

在 YAML 中启用 `integration_test`，运行转换：

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full
find dest/src/test -name "*Integration*" -type f | head
# Expected: 看到 AbstractIntegrationTest.java 和多个 *IntegrationTest.java
```

**Step 4: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: generate per-package integration test classes"
```

---

## Task 7: @Sql 测试数据注入

**Files:**
- Modify: `converter/flux_gauss.py` (`_build_itest_method` 和相关函数)

**Step 1: 改进测试数据生成方式**

将每个方法的推断测试数据写入独立的 SQL 文件，用 `@Sql` 注解引用：

```python
def _write_itest_fixtures(base_path: Path, proc: ProcedureInfo, pkg: PackageInfo,
                           test_data: list) -> str:
    """Write test fixture SQL file and return @Sql annotation reference."""
    jp = _pkg_java_package(pkg)
    fixture_dir = base_path / "src/test/resources" / "itest-fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    
    method_name = java_method_name(proc.proc_name)
    fixture_file = f"{pkg.package_name}_{method_name}.sql"
    
    lines = ["-- Auto-generated test fixture"]
    lines.append(f"-- Package: {pkg.package_name}, Procedure: {proc.proc_name}")
    lines.append("")
    
    for item in test_data:
        table = item["table"]
        columns = item["columns"]
        col_names = ", ".join(columns.keys())
        values = ", ".join(str(v) for v in columns.values())
        lines.append(f"INSERT INTO {table} ({col_names}) VALUES ({values})")
        lines.append("ON CONFLICT DO NOTHING;")  # 幂等性：避免重复插入
        lines.append("")
    
    (fixture_dir / fixture_file).write_text("\n".join(lines))
    return f"classpath:itest-fixtures/{fixture_file}"
```

**Step 2: 在测试方法中注入 @Sql 注解**

修改 `_build_itest_method()` 使用生成的 fixture：

```java
@Test
@Sql(scripts = "classpath:itest-fixtures/pkg_order_createOrder.sql", 
     executionPhase = Sql.ExecutionPhase.BEFORE_TEST_METHOD)
@Timeout(value = 10, unit = java.util.concurrent.TimeUnit.SECONDS)
void test_createOrder_integration() {
    // ... test code ...
}
```

**Step 3: 添加清理逻辑**

在基类中添加 `@DirtiesContext` 或 `@Sql` 清理脚本，确保测试之间数据隔离：

```java
// 在基类中添加
@Sql(scripts = "classpath:itest-cleanup.sql", executionPhase = Sql.ExecutionPhase.AFTER_TEST_METHOD)
```

或者使用 `@Transactional` + `@Rollback` 自动回滚。

**Step 4: 验证**

```bash
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full
ls dest/src/test/resources/itest-fixtures/
# Expected: 看到多个 fixture SQL 文件
```

**Step 5: Commit**

```bash
git add converter/flux_gauss.py
git commit -m "feat: generate test fixture SQL files with @Sql injection"
```

---

## Task 8: 使用指南更新

**Files:**
- Modify: `使用指南.md`

**Step 1: 在使用指南中添加集成测试章节**

在"配置"章节后添加：

```markdown
## 集成测试

### 启用集成测试

在 `fluxgauss.yaml` 中添加 `integration_test` 配置块：

\`\`\`yaml
integration_test:
  enabled: true
  mode: testcontainers    # testcontainers | remote
  
  # Testcontainers 模式（推荐）
  image: opengauss/opengauss:latest
  
  # Remote 模式（直连真实数据库）
  # mode: remote
  # url: jdbc:postgresql://your-server:5432/testdb
  # username: test_user
  # password: test_pass
  
  # 额外的建表/初始化脚本
  init_sql:
    - sql/tables.sql
\`\`\`

### 运行集成测试

\`\`\`bash
# Testcontainers 模式（需要 Docker）
cd dest && mvn verify -Pintegration

# Remote 模式（确保目标数据库可达）
cd dest && mvn verify -Pintegration -Dspring.profiles.active=integration
\`\`\`

### 生成的文件

| 文件 | 说明 |
|---|---|
| `itest/AbstractIntegrationTest.java` | 基类（Testcontainers 启动/数据库连接） |
| `itest/{Name}ServiceIntegrationTest.java` | 每包的集成测试 |
| `itest-schema.sql` | 自动从 SQL 源提取的建表语句 |
| `itest-fixtures/*.sql` | 每个测试方法的测试数据 |

### 自定义测试数据

自动推断的测试数据是骨架，你可能需要调整：

1. 编辑 `src/test/resources/itest-fixtures/` 下的 SQL 文件
2. 在 YAML 中用 `init_sql` 引用你的初始数据脚本
3. 在生成的 `*IntegrationTest.java` 中添加 `// TODO` 标记的断言
```

**Step 2: Commit**

```bash
git add 使用指南.md
git commit -m "docs: add integration testing guide"
```

---

## Task 9: 端到端验证

**Step 1: 配置并运行**

```bash
# 在 demo-project/fluxgauss.yaml 中添加 integration_test 配置
# 运行转换
python3 converter/flux_gauss.py -c demo-project/fluxgauss.yaml --full

# 编译检查
cd dest && mvn compile
# Expected: BUILD SUCCESS

# 运行单元测试（不应受影响）
mvn test
# Expected: 现有单元测试全部通过

# 运行集成测试（需要 Docker）
mvn verify -Pintegration
# Expected: Testcontainers 启动 PostgreSQL → 建表 → 执行测试 → 清理
```

**Step 2: 检查关键点**

- [ ] 集成测试文件生成在 `src/test/java/{pkg}/itest/` 目录
- [ ] `AbstractIntegrationTest.java` 正确启动 Testcontainers
- [ ] `itest-schema.sql` 包含所有必要的建表语句
- [ ] 每个 fixture SQL 文件包含合理的测试数据
- [ ] `mvn test` 仍只跑单元测试
- [ ] `mvn verify -Pintegration` 跑集成测试
- [ ] 生成的代码无编译错误

**Step 3: 最终 Commit**

```bash
git add -A
git commit -m "feat: complete integration testing support with Testcontainers and remote modes"
```

---

## 风险和注意事项

1. **OpenGauss 兼容性**：如果目标数据库是 OpenGauss 而非 PostgreSQL，Testcontainers 默认用 PostgreSQL 镜像。需要用户配置正确的 Docker 镜像（如 `openGauss/opengauss:5.0`）。但 OpenGauss 的 Testcontainers 支持不如 PostgreSQL 成熟，可能需要自定义容器类。

2. **测试数据质量**：自动推断的测试数据是"骨架"，对于复杂业务逻辑（外键约束、唯一约束、枚举值）可能不完整。生成的文件中标注 `// TODO` 让用户补充。

3. **DDL 完整性**：`parse_table_ddl()` 只解析简单的 `CREATE TABLE`，不支持复杂的 `CREATE TABLE AS`、分区表、继承表等。用户可能需要在 `init_sql` 中补充。

4. **性能**：每个测试类共享一个 Testcontainers 实例（`static` 字段），不会为每个类启动新容器。但 `@DirtiesContext` 会重启 Spring 上下文，慎用。

5. **增量构建**：集成测试文件应在 `changed_packages` 为 None 时（全量）或对应包变更时重新生成。

# FluxGauss (sp2java) 设计文档

> 本文档详细描述 FluxGauss 转换器的完整架构、数据模型、算法逻辑和代码生成规则，详细程度足以指导使用任意编程语言实现同等功能。

---

## 目录

1. [概述与架构](#1-概述与架构)
2. [外部依赖](#2-外部依赖)
3. [常量与配置](#3-常量与配置)
4. [类型系统](#4-类型系统)
5. [数据模型](#5-数据模型)
6. [AST 提取管线](#6-ast-提取管线)
7. [语句处理引擎](#7-语句处理引擎)
8. [表达式转换系统](#8-表达式转换系统)
9. [SQL 重构](#9-sql-重构)
10. [项目生成](#10-项目生成)
11. [集成测试生成](#11-集成测试生成)
12. [CLI 与报告](#12-cli-与报告)
13. [增量构建与缓存](#13-增量构建与缓存)

---

## 1. 概述与架构

### 1.1 功能定位

FluxGauss 将 OpenGauss/PostgreSQL 的 PL/pgSQL 存储过程转换为 Spring Boot + MyBatis 的 Java 项目。转换器解析 SQL 文件，提取存储过程的控制流和数据操作，然后生成对应的 Java Service 类、MyBatis Mapper 接口、MyBatis XML 映射文件和单元测试。

**双引擎实现**：项目提供两套转换引擎，共享相同的配置格式和生成产物：

| 引擎 | 入口 | 代码量 | 适用场景 |
|------|------|--------|----------|
| Python | `converter/flux_gauss.py` | ~13900 行 | 通用（100-1000 个 SP） |
| Rust | `crates/fluxgauss/` | ~14187 行 | 大批量（1000-30000 个 SP），支持 Rayon 并行 |

本文档以 Python 引擎为主要参考描述，并在各章节标注 Rust 引擎的关键差异。**Python 引擎是功能最完整的参考实现**；Rust 引擎持续对齐中。

### 1.2 整体架构

```
SQL 源文件
    ↓
ogsql-parser (Rust 解析器，Python 通过子进程调用，Rust 通过 crate 直接集成)
    ↓
JSON AST
    ↓
┌─────────────────────────────────────────────────────┐
│            FluxGauss 转换引擎 (Python / Rust)         │
│                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ SQL 解析  │→│ AST 提取     │→│ 过程分析     │  │
│  │          │  │ (extract_*)  │  │ (analyze_*)  │  │
│  └──────────┘  └──────────────┘  └──────────────┘  │
│                                        ↓            │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ 代码生成  │←│ 表达式转换   │←│ 语句处理引擎 │  │
│  │(generate)│  │(_expr_*)     │  │(_process_*)  │  │
│  └──────────┘  └──────────────┘  └──────────────┘  │
│       ↓                                             │
│  Spring Boot 项目:                                  │
│  Service.java + Mapper.java + Mapper.xml + Test.java│
└─────────────────────────────────────────────────────┘
```

### 1.3 数据流

```
1. SQL 文件 → _read_sql_file() → (文本, 编码)
2. 文本 → _split_sql_statements() → [(sql_text, start_line), ...]
3. 语句 → parse_sql_file() → AST {statements, errors, comments}
4. AST → extract_procedures() → (ProcedureInfo[], package_vars, custom_types)
5. SQL 文件 → _recover_constant_declarations() → 更新 package_vars
6. AST → extract_comments() → CommentInfo[]
7. Comments → _map_comments_to_procedures() → 过程获得 leading_comments / inline_comments
8. ProcedureInfo → analyze_procedure() → 填充 dml_statements, java_logic_lines, service_calls, imports 等
9. ProcedureInfo[] → generate_project() → 写出 Java/XML 文件
```

### 1.4 核心设计决策

| 决策 | 原因 |
|------|------|
| 双引擎（Python + Rust） | Python 快速迭代功能完整；Rust 高性能大批量并行 |
| 外部 Rust 解析器 | SQL 语法复杂，专用解析器更可靠。Python 通过子进程调用，Rust 通过 crate 直接集成 |
| 每个 SQL 包 → 4 个 Java 文件 | 清晰的 Service/Mapper 分层 |
| 骨架文件仅写入一次 | 避免覆盖用户自定义修改 |
| REFCURSOR → `List<Map<String, Object>>` | JDBC 无原生游标映射 |
| OUT 参数 → `AtomicReference<T>` | Java 无引用传递，需容器包装 |
| GOTO → 模式匹配重写 | Java 无 GOTO，需转换为结构化控制流 |

---

## 2. 外部依赖

### 2.1 ogsql-parser

**功能**：将 SQL 文本解析为 JSON 格式的 AST。

**调用方式**：
- **Python 引擎**：通过子进程调用 ogsql 二进制
- **Rust 引擎**：通过 `ogsql-parser` crate 直接集成（无需外部进程）

```bash
# 解析 SQL 文件，输出 JSON AST（含注释提取）
ogsql --comments -f <input.sql> parse -j

# 将 AST JSON 还原为 SQL 文本
ogsql json2sql -f <input.json>
```

**二进制路径解析顺序**（`_resolve_ogsql_bin()`）：
1. 当前工作目录下的 `ogsql`
2. 环境变量 `OGSQL_BIN`
3. `shutil.which("ogsql")`（系统 PATH）
4. `{project_dir}/lib/ogsql-parser/target/aarch64-apple-darwin/release/ogsql`
5. `{project_dir}/lib/ogsql-parser/target/release/ogsql`
6. 回退：`"ogsql"`（依赖 PATH）

### 2.2 运行时依赖

| 依赖 | 用途 |
|------|------|
| Python 3.9+ | 运行时 |
| pyyaml | YAML 配置文件解析（可选） |
| Java 17+ | 编译验证生成的项目 |
| Maven | 构建生成的 Spring Boot 项目 |

---

## 3. 常量与配置

### 3.1 全局常量

| 常量 | 值 | 用途 |
|------|------|------|
| `OGSQL_BIN` | `_resolve_ogsql_bin()` 的返回值 | ogsql 二进制路径 |
| `BASE_PACKAGE` | `"com.example.demo"` | 默认 Java 基础包名 |
| `BASE_DIR` | `"src/main/java/com/example/demo"` | 默认 Java 源码目录 |
| `RESOURCES_DIR` | `"src/main/resources"` | 资源文件目录 |

### 3.2 日志配置预设

支持 4 种日志框架预设，每个预设包含 `imports`（导入语句列表）、`declaration`（声明模板，使用 `{class_name}` 占位符）和 `pom`（Maven 依赖列表）：

| 预设名 | 导入 | 声明 |
|--------|------|------|
| `slf4j` | `org.slf4j.Logger`, `org.slf4j.LoggerFactory` | `private static final Logger log = LoggerFactory.getLogger({class_name}.class);` |
| `log4j2` | `org.apache.logging.log4j.LogManager`, `org.apache.logging.log4j.Logger` | `private static final Logger log = LogManager.getLogger({class_name}.class);` |
| `commons-logging` | `org.apache.commons.logging.Log`, `org.apache.commons.logging.LogFactory` | `private static final Log log = LogFactory.getLog({class_name}.class);` |
| `jul` | `java.util.logging.Logger` | `private static final Logger log = Logger.getLogger({class_name}.class.getName());` |

日志配置可通过 YAML 中的 `logger` 字段指定预设名称，或提供自定义字典：

```yaml
# 预设模式
logger: log4j2

# 自定义模式
logger:
  imports:
    - "import com.mycompany.Logger;"
  declaration: "private static final Logger log = LoggerFactory.create({class_name}.class);"
  pom:
    - '<dependency>...</dependency>'
```

### 3.3 YAML 配置格式

```yaml
output_dir: ./dest                    # 输出目录
base_package: com.example.demo        # Java 基础包名

logger: slf4j                         # 日志框架预设或自定义配置

database:                             # 数据库连接（可选，生成 application.yml）
  url: jdbc:postgresql://localhost:5432/demo
  username: postgres
  password: postgres
  driver: org.postgresql.Driver

sources:                              # SQL 源文件列表
  - sql/pkg_order.sql
  - sql/pkg_product.sql

java_packages:                        # 可选：SQL 文件到 Java 包名的映射
  - package: com.example.order
    sources:
      - sql/pkg_order.sql

type_aliases:                         # 可选：自定义类型别名（仅 Python 引擎支持）
  VARCHAR_ARRAY: "String[]"

integration_test:                     # 可选：集成测试配置
  enabled: true
  mode: remote                        # remote 或 testcontainers
  url: jdbc:postgresql://localhost:5432/postgres
  username: gaussdb
  password: secret
  init_sql:
    - sql/init_data.sql
```

### 3.4 包级别配置辅助函数

| 函数 | 输入 | 输出 | 逻辑 |
|------|------|------|------|
| `_pkg_java_package(pkg)` | PackageInfo 对象 | `str` | 返回 `pkg.java_package`，未设置则返回 `BASE_PACKAGE` |
| `_pkg_base_dir(pkg)` | PackageInfo 对象 | `str` | 返回 `"src/main/java/" + _pkg_java_package(pkg).replace(".", "/")` |

---

## 4. 类型系统

### 4.1 SQL 到 Java 类型映射（`SQL_TO_JAVA`）

| SQL 类型 | Java 类型 |
|----------|-----------|
| `bigint`, `biginteger`, `int8` | `Long` |
| `integer`, `int`, `int4`, `smallint`, `serial`, `number` | `Integer` |
| `bigserial` | `Long` |
| `numeric`, `decimal` | `java.math.BigDecimal` |
| `real`, `float4` | `Float` |
| `float8`, `double precision`, `double` | `Double` |
| `varchar`, `varchar2`, `character varying`, `char`, `text`, `string`, `clob`, `json`, `jsonb`, `uuid`, `exception` | `String` |
| `boolean`, `bool` | `Boolean` |
| `timestamp`, `timestamp without time zone`, `timestamp with time zone` | `java.sql.Timestamp` |
| `date` | `java.sql.Date` |
| `time` | `java.sql.Time` |
| `bytea`, `blob` | `byte[]` |
| `record` | `Map<String, Object>` |

### 4.2 自定义类型预设（`_CUSTOM_TYPE_PRESETS`）

用于将用户定义的数组/表类型名映射到 Java 泛型类型。匹配策略：精确匹配 → 后缀匹配 → 关键字匹配。

**字符串数组**：`arrytype`, `arrtype`, `array_type`, `arraytype`, `str_array`, `string_array`, `varchar2_array`, `varchar_array`, `text_array`, `char_array`, `split_tbl`, `split_array`, `id_list`, `string_list`, `string_tbl`, `tab_varchar2`, `tab_varchar`, `tab_text`, `tab_char`, `tab_string` → `List<String>`

**数值数组**：`num_array`, `number_array`, `number_tbl`, `decimal_array`, `dec_array`, `tab_number`, `tab_numeric` → `List<java.math.BigDecimal>`；`int_array`, `integer_array`, `int_list`, `integer_list`, `integer_tbl`, `tab_integer` → `List<Integer>`；`long_array`, `long_list`, `bigint_array`, `tab_bigint` → `List<Long>`

**日期数组**：`date_array`, `date_list`, `date_tbl`, `tab_date` → `List<java.sql.Date>`；`timestamp_array`, `timestamp_list`, `tab_timestamp` → `List<java.sql.Timestamp>`

**布尔数组**：`bool_array`, `boolean_array` → `List<Boolean>`

**二进制数组**：`raw_array`, `blob_array`, `byte_array` → `List<byte[]>`

**记录/对象类型**：`rec_type`, `record_type`, `obj_type`, `row_type` → `Map<String, Object>`

**游标类型**：`sys_refcursor`, `ref_cursor`, `refcursor` → `List<Map<String, Object>>`

**范围类型**：`int4range`, `int8range`, `numrange`, `tsrange`, `tstzrange`, `daterange` → `Object`

**JSON/UUID 数组**：`jsonb_array`, `json_array`, `uuid_array` → `List<String>`

### 4.3 SQL 到 JDBC 类型映射（`SQL_TO_JDBC_TYPE`）

| SQL 类型 | JDBC 类型 |
|----------|-----------|
| `bigint`, `biginteger`, `int8`, `bigserial` | `BIGINT` |
| `integer`, `int`, `int4`, `smallint`, `serial` | `INTEGER` |
| `number`, `numeric` | `NUMERIC` |
| `decimal` | `DECIMAL` |
| `real`, `float4` | `REAL` |
| `float8`, `double precision`, `double` | `DOUBLE` |
| `varchar`, `varchar2`, `character varying`, `string` | `VARCHAR` |
| `char` | `CHAR` |
| `text` | `LONGVARCHAR` |
| `boolean`, `bool` | `BOOLEAN` |
| `timestamp`（含时区变体） | `TIMESTAMP` |
| `date` | `DATE` |
| `time` | `TIME` |
| `bytea` | `BINARY` |
| `blob` | `BLOB` |
| `clob` | `CLOB` |
| `json`, `jsonb` | `VARCHAR` |
| `uuid` | `OTHER` |
| `record` | `None`（复合类型） |
| `exception` | `VARCHAR` |

### 4.4 Java 到 JDBC 反向映射（`_JAVA_TO_JDBC`）

| Java 类型 | JDBC 类型 |
|-----------|-----------|
| `String` | `VARCHAR` |
| `Long` | `BIGINT` |
| `Integer` | `INTEGER` |
| `Boolean` | `BOOLEAN` |
| `Double` | `DOUBLE` |
| `Float` | `REAL` |
| `java.math.BigDecimal` | `NUMERIC` |
| `java.sql.Timestamp` | `TIMESTAMP` |
| `java.sql.Date` | `DATE` |
| `java.sql.Time` | `TIME` |
| `byte[]` | `BINARY` |
| `Object` | `None` |
| `Map<String, Object>` | `None` |

### 4.5 类型转换核心函数

#### `sql_type_to_java(sql_type) → str`

**算法**：
1. 空值 → `"Object"`
2. **字典类型**（PercentType、RefCursor 等）：
   - `TypeName` 键 → 递归处理值
   - `PercentType` 键 → 从 `TYPE_OVERRIDES` 查找；未找到则通过 `_infer_type_from_column_name()` 推断
   - `PercentRowType` 或 `Record` → `"Map<String, Object>"`
   - `RefCursor` 或 `Cursor` → `"List<Map<String, Object>>"`
3. **字符串 `%TYPE` 锚定类型**（如 `table.column%TYPE`）：正则匹配 → `TYPE_OVERRIDES` → 推断
4. **标准化字符串类型**：小写、去括号、去修饰词（deterministic 等）；以 "table" 开头 → `List<Map<String, Object>>`
5. **SQL 数组类型**（如 `FLOAT8[]`）：去括号获取基础类型 → `java.util.List<{base}>`
6. **查表 `SQL_TO_JAVA`**
7. **查自定义类型预设**：短名精确 → 全名精确 → 关键字匹配
8. **默认**：`"Map<String, Object>"`

#### `sql_type_to_jdbc(sql_type) → Optional[str]`

与 `sql_type_to_java` 相同的解析链，但查 `SQL_TO_JDBC_TYPE` 表。

#### `java_type_to_jdbc(java_type) → Optional[str]`

直接查 `_JAVA_TO_JDBC` 表。`List<...>` 类型返回 `None`。

### 4.6 命名工具函数

| 函数 | 输入 → 输出 | 规则 |
|------|-------------|------|
| `snake_to_camel(s)` | `get_product_info` → `getProductInfo` | 按下划线分割，首段小写，后续段首字母大写 |
| `snake_to_pascal(s)` | `order_service` → `OrderService` | 按下划线分割，每段首字母大写 |
| `package_to_classname(name)` | `pkg_order` → `Order` | 去掉 `pkg_`/`PKG_`/`pack_` 前缀，转 PascalCase |
| `java_method_name(name)` | `create_order` → `createOrder` | 等同 `snake_to_camel` |
| `_custom_type_classname(name)` | `t_coord_rec` → `CoordRec` | 去掉 `t_`/`type_` 前缀，转 PascalCase |
| `_java_safe_identifier(s)` | 任意字符串 → 合法 Java 标识符 | 去除特殊字符、数字前缀加下划线、Java 关键字前加下划线 |

**Java 关键字列表**（52个 + 3个 PL/pgSQL 特殊词）：
`abstract`, `assert`, `boolean`, `break`, `byte`, `case`, `catch`, `char`, `class`, `const`, `continue`, `default`, `do`, `double`, `else`, `enum`, `extends`, `final`, `finally`, `float`, `for`, `goto`, `if`, `implements`, `import`, `instanceof`, `int`, `interface`, `long`, `native`, `new`, `package`, `private`, `protected`, `public`, `return`, `short`, `static`, `strictfp`, `super`, `switch`, `synchronized`, `this`, `throw`, `throws`, `transient`, `try`, `void`, `volatile`, `while`, `true`, `false`, `null`, `old`, `new`, `raise`

### 4.7 列名类型推断（`_infer_type_from_column_name`）

当 SQL 类型和 `TYPE_OVERRIDES` 均无法确定类型时，通过列名模式推断：

| 列名模式 | 推断 SQL 类型 |
|----------|---------------|
| 含 `name`, `txt`, `text`, `info`, `desc`, `msg`, `remark`, `comment` | `varchar` |
| 含 `id`, `no`, `seq`（且不含 `varchar`） | `bigint` |
| 含 `num`（且不含 `varchar`） | `integer` |
| 含 `amount`, `balance`, `price`, `qty`, `quantity`, `total`, `salary` | `numeric` |
| 含 `date`, `time`, `stamp` | `timestamp` |
| 含 `flag`, `status`, `level`, `type`, `code` | `varchar` |
| 其他 | `varchar` |

### 4.8 导入解析（`_resolve_import`）

| Java 类型 | 导入语句 |
|-----------|----------|
| `AtomicReference` | `java.util.concurrent.atomic.AtomicReference` |
| `BigDecimal` | `java.math.BigDecimal` |
| `BigInteger` | `java.math.BigInteger` |
| `List` | `java.util.List` |
| `ArrayList` | `java.util.ArrayList` |
| `Map` | `java.util.Map` |
| `HashMap` | `java.util.HashMap` |
| `Arrays` | `java.util.Arrays` |
| `Objects` | `java.util.Objects` |

简单类型（`String`, `Long`, `Integer`, `Boolean`, `Double`, `Float`, `Object`, `byte[]`, `void`）不需要导入。包含泛型的类型先剥离泛型部分再查表。

---

## 5. 数据模型

### 5.1 Parameter

表示存储过程/函数的一个参数。

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `name` | `str` | 必填 | SQL 参数名（snake_case） |
| `java_type` | `str` | 必填 | Java 类型名 |
| `sql_type` | `str` | 必填 | SQL 数据类型 |
| `mode` | `Optional[str]` | `None` | `"IN"` / `"OUT"` / `"INOUT"` |

**计算属性**：
- `java_name`: `snake_to_camel(self.name)`
- `is_out`: `mode in ("OUT", "INOUT")`
- `is_refcursor`: `sql_type` 为 `"refcursor"`, `"ref cursor"`, `"refcur"`, 或 `"cursor"`

> **Rust 差异**：`mode` 使用 `ParamMode` 枚举（`ParamMode::In` / `Out` / `InOut`）而非字符串，类型安全性更强。

### 5.2 CommentInfo

表示一条 SQL 注释。

| 字段 | 类型 | 用途 |
|------|------|------|
| `text` | `str` | 注释原文（保留 `--` 或 `/* */` 定界符） |
| `line` | `int` | 起始行号（1-based） |
| `end_line` | `int` | 结束行号 |
| `column` | `int` | 列位置 |
| `comment_type` | `str` | `"line"` 或 `"block"` |

> **Rust 差异**：Rust 中对应的类型名为 `CommentBlock`，字段名不同：`line` → `start_line`、`column` 字段缺失、`comment_type` → `is_block: bool`。

### 5.3 DmlStatement

表示提取的一条 DML 语句（SELECT/INSERT/UPDATE/DELETE/MERGE），用于生成 MyBatis Mapper。

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `sql_type` | `str` | 必填 | DML 类型：`"Select"` / `"Insert"` / `"Update"` / `"Delete"` / `"Merge"` 等 |
| `method_id` | `str` | 必填 | Mapper 方法标识符（camelCase） |
| `sql_text` | `str` | 必填 | SQL 语句文本 |
| `result_type` | `Optional[str]` | `None` | SELECT 返回类型 |
| `parameter_types` | `dict` | `{}` | 参数名 → Java 类型 |
| `optional_filters` | `list` | `[]` | 可选 WHERE 过滤条件 |
| `returns_list` | `bool` | `False` | 是否返回多行 |
| `extra_params` | `list` | `[]` | 额外参数（不在原始 SQL 中） |
| `is_dynamic` | `bool` | `False` | EXECUTE IMMEDIATE 语句标记 |
| `returning_cols` | `list` | `[]` | RETURNING 子句列名 |
| `returning_into_vars` | `list` | `[]` | RETURNING INTO 目标变量名 |
| `is_forall_batch` | `bool` | `False` | FORALL 批量操作标记 |
| `forall_batch_list_var` | `str` | `""` | `<foreach>` 迭代变量名 |
| `forall_batch_arrays` | `dict` | `{}` | `{java_array_name: element_type}` 批量映射 |

> **Rust 差异**：`sql_type` 使用 `DmlType` 枚举（`Select`/`Insert`/`Update`/`Delete`）而非字符串。相比 Python 缺少以下 6 个字段：`is_dynamic`、`returning_cols`、`returning_into_vars`、`is_forall_batch`、`forall_batch_list_var`、`forall_batch_arrays`。`extra_params` 在 Rust 中为 `Vec<(String, String)>` 元组类型。

### 5.4 ServiceCall

表示一个跨服务方法调用（`pkg.proc()` 形式）。

| 字段 | 类型 | 用途 |
|------|------|------|
| `service_name` | `str` | 生成的 Service Bean 名称（如 `"inventoryService"`） |
| `method_name` | `str` | Java 方法名 |
| `args` | `list` | 参数表达式列表 |
| `package_name` | `str` | 原始包名（用于追踪） |

### 5.5 ProcedureInfo

核心数据结构，表示一个存储过程/函数的完整信息。

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| **核心字段** | | | |
| `name` | `str` | 必填 | 全限定名：`"pkg_order.create_order"` |
| `package` | `str` | 必填 | 包名 |
| `proc_name` | `str` | 必填 | 过程名 |
| `is_function` | `bool` | 必填 | 是否为函数 |
| `return_type` | `Optional[str]` | `None` | SQL 返回类型（仅函数） |
| `parameters` | `list[Parameter]` | 必填 | 参数列表 |
| `body` | `dict` | 必填 | PL/pgSQL 块 AST |
| `sql_text` | `str` | 必填 | 原始 SQL 源文本 |
| **生成产物（analyze_procedure 填充）** | | | |
| `dml_statements` | `list[DmlStatement]` | `[]` | 提取的 DML 操作 |
| `service_calls` | `list[ServiceCall]` | `[]` | 跨服务调用 |
| `java_logic_lines` | `list[str]` | `[]` | 生成的 Java 逻辑行 |
| `imports` | `set[str]` | `set()` | 所需 Java 导入 |
| `local_vars` | `dict` | `{}` | 局部变量声明：`var_name → java_type` |
| `local_var_defaults` | `dict` | `{}` | 默认值：`var_name → default_expr` |
| `table_refs` | `set[str]` | `set()` | 引用的表名 |
| `var_assignments` | `dict` | `{}` | 变量赋值追踪 |
| `dynamic_sql_templates` | `dict` | `{}` | 动态 SQL：`var_name → (sql_template, param_list)` |
| `sql_expr_vars` | `dict` | `{}` | SQL 表达式变量 |
| `inlined_sql_vars` | `set[str]` | `set()` | 内联到动态 SQL 的变量名 |
| `is_autonomous` | `bool` | `False` | 是否有 `PRAGMA AUTONOMOUS_TRANSACTION` |
| `scheduler_tasks` | `list` | `[]` | 调度任务定义 |
| `_needs_futures_list` | `bool` | `False` | 是否需要 Futures 列表 |
| **游标追踪** | | | |
| `open_cursors` | `dict` | `{}` | 游标状态：`cursor_name → {"result_var": str, "index_var": str}` |
| `refcursor_out_params` | `set[str]` | `set()` | REFCURSOR OUT 参数的 Java 名 |
| `cursor_decls` | `dict` | `{}` | 游标声明：`cursor_name → parsed_query` |
| `cursor_params` | `dict` | `{}` | 游标参数：`cursor_name → [param_names]` |
| `custom_types` | `dict` | `{}` | 自定义类型：`type_name → {"kind": "record"/"varray"/"table", ...}` |
| **源追踪** | | | |
| `source_file` | `str` | `""` | 原始 SQL 文件名 |
| `_source_path` | `str` | `""` | 完整文件系统路径 |
| `source_start_line` | `int` | `0` | 过程起始行 |
| `source_end_line` | `int` | `0` | 过程结束行 |
| `leading_comments` | `list[CommentInfo]` | `[]` | 过程声明前的注释 |
| `inline_comments` | `list[CommentInfo]` | `[]` | 过程体内的注释 |

> **Rust 差异**：
> - `body` 类型为 `Option<PlBlock>`（强类型 AST），Python 中为 `dict`
> - `imports` 为 `BTreeSet<String>`，`table_refs` 为 `HashSet<String>`
> - `open_cursors` 为 `HashMap<String, CursorInfo>`（强类型结构体）
> - `custom_types` 为 `HashMap<String, CustomTypeInfo>`
> - Python 特有字段 Rust 缺失：`sql_expr_vars`、`inlined_sql_vars`、`_needs_futures_list`
> - Rust 特有字段 Python 缺失：`package_vars`（VarInfo 结构体）、`has_array_vars`、`out_local_vars`、`goto_analysis`（Option<GotoAnalysis>）、`package_proc_params`、`select_counter`、`for_loop_counter`
> - `source_path` 在 Rust 中为 `source_path`（无下划线前缀）

### 5.6 PackageInfo

聚合一个 SQL 文件（包）中的所有过程和包级元数据。

| 字段 | 类型 | 默认值 | 用途 |
|------|------|--------|------|
| `package_name` | `str` | 必填 | 包名 |
| `procedures` | `list[ProcedureInfo]` | `[]` | 所有过程 |
| `table_refs` | `set[str]` | `set()` | 引用的表名 |
| `package_vars` | `dict` | `{}` | 包级变量 |
| `source_file` | `str` | `""` | 源文件名 |
| `comments` | `list[CommentInfo]` | `[]` | 包级注释 |
| `java_package` | `str` | `""` | 自定义 Java 包名（空=用 BASE_PACKAGE） |
| `custom_types` | `dict` | `{}` | 包级自定义类型 |
| `_extra_mapper_methods` | `list` | `[]` | 额外 Mapper 方法 |

### 5.7 SkippedItem

表示转换过程中跳过的非过程语句。

| 字段 | 类型 | 用途 |
|------|------|------|
| `sql_file` | `str` | 源 SQL 文件路径 |
| `statement_type` | `str` | 高层类型：`"DDL"` / `"DML"` / `"OTHER"` |
| `category` | `str` | 具体类别（如 `"CREATE TABLE"`, `"SELECT"`） |
| `name` | `str` | 对象名 |
| `detail` | `str` | 跳过原因说明 |
| `line_start` / `line_end` | `int` | 行号范围 |

> **Rust 差异**：字段名完全不同：`sql_file` → `source_file`、`statement_type` → `item_type`、`detail` → `reason`、`line_start`/`line_end` → `line_number`（单字段）。缺少 `category` 字段。

### 5.8 ProcedureMapping

记录 SQL 过程到 Java 产物的映射关系（用于转换报告）。

| 字段 | 类型 | 用途 |
|------|------|------|
| `sql_file` | `str` | 源文件路径 |
| `procedure_name` | `str` | 全限定过程名 |
| `procedure_type` | `str` | `"PROCEDURE"` 或 `"FUNCTION"` |
| `java_service` | `str` | Java Service 类名 |
| `java_method` | `str` | Java 方法名 |
| `mapper_methods` | `list[str]` | Mapper 方法名列表 |
| `generated_files` | `list[str]` | 生成的文件路径 |
| `is_stub` | `bool` | 是否为存根 |
| `has_parse_error` | `bool` | 是否有解析错误 |
| `notes` | `str` | 附加说明 |
| `stub_reasons` | `list[str]` | 存根原因列表 |
| `table_refs` | `set[str]` | 引用的表名 |

> **Rust 差异**：字段名不同（`sql_file` → `sql_package`，`procedure_name` → `sql_procedure`）。缺少 `procedure_type`、`mapper_methods`、`generated_files`、`has_parse_error`、`stub_reasons`、`table_refs` 字段。`notes` 类型为 `Vec<String>` 而非 `str`。

### 5.9 ConversionReport

转换操作的完整报告。

| 字段 | 类型 | 用途 |
|------|------|------|
| `generated_at` | `str` | ISO 时间戳 |
| `config_path` | `str` | 配置文件路径 |
| `output_dir` | `str` | 输出目录 |
| `sql_files` | `list[str]` | 处理的 SQL 文件 |
| `procedure_mappings` | `list[ProcedureMapping]` | 过程映射列表 |
| `skipped_items` | `list[SkippedItem]` | 跳过的语句 |
| `parse_errors` | `list` | 解析错误 |
| `parse_warnings` | `list` | 解析警告 |
| `unresolved_calls` | `list[str]` | 未解析的调用 |
| `total_packages` | `int` | 包总数 |
| `total_procedures` | `int` | 过程总数 |
| `total_dml` | `int` | DML 语句总数 |
| `total_cross_calls` | `int` | 跨包调用总数 |

> **Rust 差异**：Rust 引擎没有独立的 `ConversionReport` 类型，而是在 `PipelineResult` 中内联报告数据，通过 `report::build_report()` 构建 Markdown 报告。

### 5.10 GotoInfo / LabelInfo / GotoAnalysis

GOTO 模式分析的数据结构。

**GotoInfo**：`label`, `source_idx`, `source_depth`, `is_forward`, `is_backward`, `source_path`

**LabelInfo**：`name`, `target_idx`, `target_depth`

**GotoAnalysis**：
- `labels`: `label_name → LabelInfo`
- `gotos`: `GotoInfo[]`
- `pattern`: 检测到的模式（`"A"` 无 GOTO / `"B"` 仅 break / `"C"` 仅 continue / `"D"` 单前向循环 / `"E"` 复杂 / `"unknown"`）
- `label_stmt_map`: `label_name → statement dict`

> **Rust 差异**：
> - `GotoPattern` 使用描述性枚举名：`CleanupExit` / `LoopSimulation` / `LogicSkip` / `DeepNestedBreak` / `StateMachine`，而非字母 A-E
> - `GotoInfo` 字段名不同：`source_idx` → `stmt_index`、`source_depth` → `nesting_depth`，额外增加 `inside_loop: bool`
> - `GotoAnalysis` 中 `labels` 为 `HashMap<String, usize>`（直接映射），不使用 `LabelInfo` 结构体
> - Rust 额外增加 `has_backward`、`has_forward`、`cross_block` 标志位
> - Rust 缺少 `label_stmt_map` 字段

---

## 6. AST 提取管线

### 6.1 _read_sql_file(path) → (text, encoding)

**算法**：
1. 以二进制模式读取文件
2. 尝试 UTF-8 解码（错误替换为 `\ufffd`）
3. 如无替换字符 → 返回 UTF-8
4. 尝试 GB18030 → GBK → Big5 回退编码
5. 全部失败 → 返回 `'utf-8-damaged'`

### 6.2 _split_sql_statements(sql_text) → [(sql_text, start_line)]

**算法**：
1. 逐行扫描，跟踪 dollar-quote 状态（`$$...$$` 或 `$tag$...$tag$`）
2. 遇到 `$$ LANGUAGE PLPGSQL` 结尾模式 → 切分语句
3. 跳过空行，记录实际内容起始行
4. 返回 `(sql文本, 起始行号)` 元组列表

### 6.3 parse_sql_file(sql_path) → AST dict

**算法**：

**单语句路径**（快速路径）：
1. 调用 `_read_sql_file()` 获取文本
2. 调用 ogsql 二进制：`[OGSQL_BIN, "--comments", "-f", input_file, "parse", "-j"]`
3. 如 ogsql 返回非零或无 JSON → 去掉 `--comments` 重试
4. 解析 JSON 输出
5. 从源文本提取注释：`_extract_comments_from_text(sql_text)`

**多语句路径**：
1. 调用 `_split_sql_statements()` 获取语句列表
2. 对每条语句单独调用 ogsql
3. 合并 AST：偏移每条语句的行号（`_offset_lines_in_ast()`）
4. 从完整文本提取注释

**返回格式**：
```json
{
  "statements": [
    {"CreateFunction": {...}},
    {"CreateProcedure": {...}},
    {"CreatePackage": {...}},
    {"CreatePackageBody": {...}}
  ],
  "errors": [...],
  "comments": [...]
}
```

### 6.4 extract_parameters(params_list) → list[Parameter]

**算法**：
1. 遍历参数字典列表
2. **解析器缺陷修正**：如果 `name` 为 `"out"/"in"/"inout"` 且 `data_type` 含多个词，则重新拆分为正确的 `name + type + mode`
3. 处理字典类型的 `data_type`（RefCursor、PercentType、TypeName）
4. 调用 `sql_type_to_java()` 转换类型
5. 过滤掉 `"self"` 参数

### 6.5 extract_procedures(ast, source_file) → (procedures, package_vars, custom_types)

**算法**：

**阶段 1：处理独立过程/函数**（AST 中的 `CreateFunction`/`CreateProcedure`）
- 提取名称部分（可能含包名前缀）
- 调用 `extract_parameters()`
- 检测 REFCURSOR OUT 参数
- 创建 `ProcedureInfo`

**阶段 2：处理包规范**（`CreatePackage`）
- 提取包级 `Variable` → 转换类型和默认值 → 存入 `package_vars`
- 提取包级 `Type`（Record / VarrayOf）→ 存入 `custom_types`

**阶段 3：处理包体**（`CreatePackageBody`）
- 提取第一个函数/过程之前的变量（包级变量）
- 提取过程/函数：
  - 解析返回类型（自定义记录/变长数组类型）
  - 解析参数中的自定义类型
  - 提取过程局部的 Type 声明（TableOf / VarrayOf）
  - 创建 `ProcedureInfo`

### 6.6 _recover_constant_declarations(sql_path, package_vars)

**算法**：
1. 正则扫描 `name CONSTANT type := value;` 模式
2. 转换类型和默认值
3. 更新 `package_vars` 和全局 `_PACKAGE_CONSTANTS`

**存在原因**：ogsql-parser 将 CONSTANT 声明解析为 `data_type={'TypeName': 'constant'}`，丢失了实际类型信息。此函数通过正则从源文件恢复。

### 6.7 注释处理

**extract_comments(ast)**：将 AST 注释节点转为 `CommentInfo` 对象列表。

**_extract_comments_from_text(sql_text)**：用正则 `(--[^\n]*|/\*[\s\S]*?\*/)` 直接从源文本提取注释（绕过 ogsql 行号偏差）。

**_map_comments_to_procedures(comments, procedures)**：
- 注释在过程体内 → `inline_comments`
- 注释在前一过程结束到当前过程开始之间 → `leading_comments`
- 不在任何过程内 → 包级注释

**_inject_inline_comments(proc, stmt_checkpoints)**：将 `inline_comments` 按行号比例插入到 `java_logic_lines` 中。

---

## 7. 语句处理引擎

### 7.1 分发逻辑

`_process_statement(stmt, proc, all_packages, dml_counter)` 根据 AST 节点类型分发到对应处理器：

| AST 节点类型 | 处理器 | PL/pgSQL 构造 | Rust 状态 |
|-------------|--------|---------------|-----------|
| `SqlStatement` | `_process_sql_statement()` | SELECT / INSERT / UPDATE / DELETE / MERGE | ✅ 已实现 |
| `If` | `_process_if()` | IF / ELSIF / ELSE | ✅ 已实现 |
| `Return` | `_process_return()` | RETURN | ✅ 已实现 |
| `Assignment` | `_process_assignment()` | `var := expr` | ✅ 已实现 |
| `Raise` | `_process_raise()` | RAISE EXCEPTION / NOTICE | ✅ 已实现 |
| `Perform` | `_process_perform()` | PERFORM function_call | ✅ 已实现 |
| `For` | `_process_for()` | FOR 循环（范围/查询/游标） | ✅ 已实现 |
| `While` | `_process_while()` | WHILE 循环 | ✅ 已实现 |
| `Loop` | `_process_loop()` | LOOP（无限循环） | ✅ 已实现 |
| `Open` | `_process_cursor_open()` | OPEN cursor | ✅ 已实现 |
| `Fetch` | `_process_cursor_fetch()` | FETCH cursor INTO | ⚠️ 存根（注释占位） |
| `Close` | `_process_cursor_close()` | CLOSE cursor | ⚠️ 存根（注释占位） |
| `Exit` | `_process_exit()` | EXIT [WHEN] | ✅ 已实现 |
| `Execute` | `_process_execute()` | EXECUTE IMMEDIATE | ✅ 已实现 |
| `Block` | 内联处理 | DECLARE ... BEGIN ... END | ✅ 已实现 |
| `Commit` | 内联处理 | COMMIT | ⚠️ 存根（注释占位） |
| `Rollback` | 内联处理 | ROLLBACK | ⚠️ 部分实现 |
| `ProcedureCall` | `_process_procedure_call()` | CALL / 过程调用 | ✅ 已实现 |
| `Continue` | 内联处理 | CONTINUE [WHEN] | ✅ 已实现 |
| `Goto` | 内联处理 + 后期重写 | GOTO label | ✅ 已实现（goto.rs） |
| `Case` | `_process_case_stmt()` | CASE WHEN THEN | ✅ 已实现 |
| `Savepoint` | 内联处理 | SAVEPOINT | ⚠️ 存根（注释占位） |
| `ReturnQuery` | `_process_return_query()` | RETURN QUERY | ⚠️ 存根（注释占位） |
| `GetDiagnostics` | `_process_get_diagnostics()` | GET DIAGNOSTICS | ⚠️ 存根（注释占位） |
| `ForAll` | `_process_forall()` | FORALL 批量操作 | ⚠️ 存根（注释占位） |
| `Null` | 内联处理 | NULL（无操作） | ✅ 已实现 |

**Rust 特有语句类型**（Python 引擎无独立处理）：

| AST 节点类型 | PL/pgSQL 构造 | Rust 状态 |
|-------------|---------------|-----------|
| `ForEach` | FOR EACH 数组迭代 | ✅ 已实现 |
| `ReturnNext` | RETURN NEXT（流水线函数） | ⚠️ 存根 |
| `Move` | MOVE 游标 | ⚠️ 存根 |
| `ReleaseSavepoint` | RELEASE SAVEPOINT | ⚠️ 存根 |
| `SetTransaction` | SET TRANSACTION | ⚠️ 存根 |
| `VariableSet` | SET 变量 | ⚠️ 存根 |
| `VariableReset` | RESET 变量 | ⚠️ 存根 |
| `PipeRow` | PIPE ROW（流水线函数） | ⚠️ 存根 |
| `Sql`（纯文本） | 内联 SQL 文本处理 | ✅ 已实现 |

> **架构差异**：Python 引擎每种语句有独立的 `_process_*()` 函数；Rust 引擎所有语句处理在 `process_statement()` 函数中内联实现（~700 行 match 语句），仅有 GOTO 独立为 `goto.rs` 模块。Rust 的 `statements/` 子模块（`sql.rs`、`control_flow.rs` 等）当前为空存根，模块化架构处于规划阶段。

### 7.2 analyze_procedure() 分析管线

对每个过程的完整分析流程：

**Pass 1：提取类型声明**
- 扫描 `block.declarations` 中的 `Type` 节点
- TableOf → `java.util.List<elemType>`
- VarrayOf → `java.util.List<elemType>`

**Pass 2：处理变量/记录/游标/Pragma 声明**
- `Variable`：转换类型、解析默认值 → 存入 `proc.local_vars`
- `Record`：`Map<String, Object>`
- `Pragma`：检测 `AUTONOMOUS_TRANSACTION` → `proc.is_autonomous = True`
- `Cursor`：存储 `parsed_query` 和参数到 `proc.cursor_decls` / `proc.cursor_params`

**Pass 3：处理体语句**
- 遍历 `body.stmts`，对每条语句调用 `_process_statement()`
- 记录语句检查点（用于注释注入）

**Pass 4：注入行内注释**
- 调用 `_inject_inline_comments(proc, stmt_checkpoints)`

**Pass 5：后处理 GOTO 模式**
- 如果存在 GOTO，调用 `_analyze_and_rewrite_goto()`

### 7.3 主要处理器详解

#### SQL 语句处理器 (`_process_sql_statement`)

**处理逻辑**：
1. 从 AST 重构 SQL 文本（通过 ogsql json2sql）
2. 将参数转换为 MyBatis `#{param}` 占位符
3. 生成唯一 Mapper 方法名
4. 处理特殊子句：
   - `SELECT INTO`：提取目标变量，生成行声明和字段赋值
   - `BULK COLLECT INTO`：遍历结果列表，提取字段
   - `RETURNING INTO`：存储 `returning_cols` 和 `returning_into_vars`
5. 添加 `DmlStatement` 到 `proc.dml_statements`
6. 生成 Java 赋值代码

**生成的 Java 模式**：
```java
// 简单 SELECT
mapper.selectMethodName(param1, param2);

// SELECT INTO 单变量
_row = mapper.selectMethodName(param1, param2);
if (_row == null) _row = java.util.Collections.emptyMap();
varName = (Type) _row.get("columnName");

// SELECT INTO 多变量
_row = mapper.selectMethodName(param1, param2);
var1 = (Type) _row.get("col1");
var2 = (Type) _row.get("col2");

// BULK COLLECT INTO
List<Map<String, Object>> _bulkResult = mapper.selectMethodName(param1);
for (Map<String, Object> _bulkRow : _bulkResult) {
    var1.add((Type) _bulkRow.get("col1"));
}

// DML 带行计数
_sqlRowCount = mapper.updateMethodName(param1, param2);
```

#### IF 处理器 (`_process_if`)

```java
if (condition) {
    // then 语句
} else if (condition2) {
    // elsif 语句
} else {
    // else 语句
}
```

条件通过 `_expr_to_java()` 转换后经 `_coerce_condition()` 确保为布尔表达式。`null` 条件替换为 `false`。

#### 赋值处理器 (`_process_assignment`)

```java
// 简单赋值
varName = expression;

// OUT 参数赋值
outParam.set(expression);

// 记录字段赋值
recordVar.put("fieldName", expression);

// BigDecimal 强制转换
decimalVar = java.math.BigDecimal.valueOf(numericExpression);
```

类型推断和强制转换规则：
- BigDecimal 目标 → 包装数值表达式
- String 目标 → 数值表达式包装 `String.valueOf()`
- OUT 参数 → `.set()` vs `.get()` 区分
- `Map.get()` 读取 → 安全类型转换

#### FOR 循环处理器 (`_process_for`)

**范围变体**：
```java
for (int i = 1; i <= 10; i++) { /* 正向 */ }
for (int i = 10; i >= 1; i--) { /* REVERSE */ }
```

**查询变体**：
```java
List<Map<String, Object>> recList = mapper.selectMethodName(params);
for (Map<String, Object> rec : recList) {
    // 循环体
}
```

**游标变体**：
```java
for (int recIdx = 0; recIdx < recResult.size(); recIdx++) {
    found = recIdx < recResult.size();
    Map<String, Object> rec = recResult.get(recIdx);
    // 循环体
}
```

#### RAISE 处理器 (`_process_raise`)

```java
// Exception
throw new BusinessException("Error message");
throw new BusinessException(String.format("Error: %s, code: %d", msg, code));

// Notice/Info/Log/Debug
log.info("Notice message");
log.debug("Debug message: {}", param);
```

#### 游标操作

**OPEN**：
```java
cursorResult = mapper.selectMethodName(params);
cursorIdx = 0;
if (cursorResult == null) cursorResult = new java.util.ArrayList<>();
```

**FETCH**：
```java
found = cursorIdx < cursorResult.size();
if (found) {
    _row = cursorResult.get(cursorIdx);
    cursorIdx++;
    var1 = (Type) _row.get("var1");
    var2 = (Type) _row.get("var2");
}
```

**CLOSE**：仅生成注释（Java 中 List 自动管理）。

#### EXECUTE IMMEDIATE 处理器 (`_process_execute`)

处理优先级：
1. `parsed_query`（首选）：从 AST 重构 SQL
2. `BinaryOp ||` 拼接：通过 `_reconstruct_sql_from_concat()` 重构
3. 变量引用：从 `proc.var_assignments` 或 `proc.dynamic_sql_templates` 查找

```java
// EXECUTE INTO
_row = mapper.method(params);
varName = (Type) _row.get("columnName");

// 动态 SQL 模板
mapper.method(templateParam1, templateParam2, usingArg1);
```

#### FORALL 批量处理器

**批量模式**（当所有数组引用为简单类型时）：
```java
List<Map<String, Object>> _batchMethod = new java.util.ArrayList<>();
for (int _bi = 0; _bi < arr.size(); _bi++) {
    Map<String, Object> _brow = new java.util.LinkedHashMap<>();
    _brow.put("arr", arr.get(_bi));
    _batchMethod.add(_brow);
}
_sqlRowCount += mapper.method(_batchMethod);
```

**循环回退模式**：
```java
for (int i = 1; i <= arr.size(); i++) {
    _sqlRowCount += mapper.method(arr.get(i - 1));
}
```

#### CASE 处理器

```java
// 简单 CASE（基本类型）
if (operand == value1) { /* ... */ }
else if (operand == value2) { /* ... */ }
else { /* ... */ }

// 简单 CASE（对象类型）
if (java.util.Objects.equals(operand, value1)) { /* ... */ }

// 搜索 CASE
if (condition1) { /* ... */ }
else if (condition2) { /* ... */ }
```

#### GOTO 后处理

5 种检测模式及对应转换策略：

| 模式 | 描述 | Java 转换 |
|------|------|-----------|
| A | 无 GOTO | 无需处理 |
| B | 仅 break（退出循环） | `break;` |
| C | 仅 continue（跳到循环头） | `continue;` |
| D | 单前向循环 | `do-while` 循环 |
| E | 复杂（状态机） | `enum + while-switch` |

### 7.4 DML 分析

#### _extract_dml_target(stmt) → str

从 DML AST 提取目标表名：
- INSERT → `table[-1]`
- UPDATE → 搜索 `Table` 节点
- DELETE → `_extract_dml_target_simple()` 搜索
- SELECT → 搜索 FROM 子句中的 `Table` 节点

#### OUT 参数提升 (`_promote_out_local_vars`)

当局部变量被用作 OUT 参数的传参容器时，将其类型从 `T` 提升为 `AtomicReference<T>`。

**算法**：
1. 遍历所有 `ProcedureCall` / `Perform` / `Assignment(FunctionCall)` 节点
2. 解析目标过程的参数列表
3. 检查每个实参位置是否对应 OUT 参数
4. 如果实参是局部变量 → 提升为 `AtomicReference<BaseType>`
5. 修补已有 Java 代码行：
   - 传给方法/mapper 调用时去掉 `.get()`
   - 独立读取时加上 `.get()`（赋值、`.set()`、方法参数除外）

---

## 8. 表达式转换系统

> **引擎覆盖度**：Python 引擎支持 110+ SQL 函数映射；Rust 引擎当前支持约 60 个（~40% 覆盖度）。Rust 缺少的类别主要包括：三角函数（sin/cos/tan 等）、高级日期函数（to_date/date_trunc/months_between）、JSON/数组函数（jsonb_*）、编码/哈希函数（md5/digest/crc32）、Oracle 兼容函数（decode/nextval）和 PostGIS 函数。Rust 特有函数包括：`LPAD`/`RPAD`（String.format 实现）、`STRING_TO_ARRAY`、`FORMAT`、`REGEXP_SPLIT_TO_ARRAY` 等。

### 8.1 _expr_to_java(expr, proc, as_read, all_packages) → str

核心表达式转换函数，将 PL/pgSQL AST 表达式转换为 Java 表达式字符串。

**参数**：
- `expr`：AST 表达式节点
- `proc`：当前 `ProcedureInfo`（用于变量查找）
- `as_read`：`True` 时对 OUT 参数添加 `.get()`（读取模式）
- `all_packages`：所有包的字典（用于跨包调用解析）

### 8.2 支持的表达式类型

#### 字面量

| AST 类型 | Java 输出 |
|----------|-----------|
| Null | `"null"` |
| String | `"...\"escaped\"..."` |
| Integer | `"123"` |
| Float | `"3.14d"` |
| Boolean | `"true"` / `"false"` |
| BitString | `Long.parseUnsignedLong("1010", 2)` |

#### 变量引用（ColumnRef / PlVariable）

| 情况 | Java 输出 |
|------|-----------|
| 特殊变量 `FOUND` | `found` |
| `SQLERRM` | `"Database error"` |
| `CURRENT_TIMESTAMP` / `NOW` / `SYSDATE` | `new java.sql.Timestamp(System.currentTimeMillis())` |
| `CURRENT_DATE` | `new java.sql.Date(System.currentTimeMillis())` |
| 多段引用 `v_emp.status` | `vEmp.get("status")` 或自定义字段访问 |
| OUT 参数读取 | `paramName.get()` |
| 包级变量 | `this.varName` |
| 序列 `.NEXTVAL` | `/* NEXTVAL: seq */ null` |
| 普通变量 | `camelCaseName` |

#### 二元运算符（BinaryOp）

**算术运算**：
- 基本类型：`left + right`, `left - right` 等
- BigDecimal：`left.add(right)`, `left.subtract(right)` 等
- 时间戳算术：`new Timestamp(ts.getTime() ± millis)`

**比较运算**：
- BigDecimal：`left.compareTo(right) == 0`（等于）, `!= 0`（不等）, `> 0`（大于）等
- String 等于：`left.equals(right)` / `!left.equals(right)`
- Long：`left.compareTo(right) == 0` 或直接比较
- 字符串与整数字面量比较：`"123".equals(var)` 模式

**字符串连接** (`||`)：
- 简单：`left.toString().concat(String.valueOf(right))`
- 动态 SQL 上下文中：存储为模板

**逻辑运算**：`AND` → `&&`, `OR` → `||`, `NOT` → `!`

**PostgreSQL 特殊运算符**：`->`, `->>`, `<@`, `@>`, `&&`, `<<`, `>>` → 注释存根

#### 函数调用（FunctionCall）

转换优先级：
1. 自定义类型构造器（TABLE OF, VARRAY）→ `Arrays.asList()`
2. 数组索引 `name[idx]` → `name.get(idx - 1)`（1-based → 0-based）
3. `SQL_FUNCTION_MAP` 查表
4. 特殊函数处理器（`abs`, `ceil`, `floor`, `round`, `to_char` 等）
5. 同包方法调用 → `this.method()`
6. 跨包服务调用 → `pkgService.method()`
7. 不支持 → TODO 注释

#### 特殊函数（SpecialFunction）

通过 `SPECIAL_FUNCTION_MAP` 分发到专用处理器：

| 函数 | 处理器 | Java 输出 |
|------|--------|-----------|
| `substr` / `substring` | `_sf_substr` | `str.substring(Math.max(0, start-1))` |
| `overlay` | `_sf_overlay` | 字符串拼接：`left + repl + right` |
| `position` | `_sf_position` | `str.indexOf(substr) + 1` |
| `extract` | `_sf_extract` | `ts.toLocalDateTime().getYear()` 等 |
| `trim` | `_sf_trim` | `.trim()` 或 `replaceAll(...)` |
| `convert` | `_sf_convert` | `new String(expr.getBytes(), encoding)` |
| `current_timestamp` | `_sf_current_timestamp` | `new java.sql.Timestamp(System.currentTimeMillis())` |
| `group_concat` | `_sf_group_concat` | TODO 注释 |
| `interval` | `_sf_interval` | 毫秒值计算 |

> **Rust 差异**：Rust 的 `special_function_to_java()` 仅支持 2 个特殊函数（`substring`/`substr` 和 `extract`），缺少 Python 的 overlay、position、trim、convert、current_timestamp、current_time、group_concat、interval 处理器。

#### 其他表达式类型

| 类型 | Java 输出 |
|------|-----------|
| `InList` | `Arrays.asList(list).contains(expr)` |
| `IsNull` / `IsNotNull` | `expr == null` / `expr != null` |
| `IsBoolean` | `Boolean.TRUE.equals(expr)` |
| `Parenthesized` | `(expr)` |
| `Case` | 嵌套三元运算符 `(cond1 ? res1 : (cond2 ? res2 : else))` |
| `Like` | `.contains()` / `.startsWith()` / `.endsWith()` / `.matches()` |
| `Between` | `expr >= low && expr <= high` |
| `CursorAttribute` | `!found` / `found` / `result != null` / `__ROWCOUNT__` |
| `Subscript` | `array.get(idx - 1)` |
| `Subquery` | `null` + TODO 注释 |

### 8.3 SQL_FUNCTION_MAP（完整列表）

映射值的三种格式：
- 直接函数名：如 `"Math.max"`
- `__EXPR__` 前缀：直接表达式模板，替换 `{args}`, `{args0}` 等
- `__HANDLER__`：调用 `_handle_function()` 特殊处理

**字符串函数**：
`upper` → `String.valueOf({args}).toUpperCase()`
`lower` → `String.valueOf({args}).toLowerCase()`
`length` → `String.valueOf({args0}).length()`
`substr` → `String.valueOf({args0}).substring({args1})`
`replace` → `String.valueOf({args0}).replace({args1}, {args2})`
`rtrim` → `.replaceAll("\\s+$", "")`
`ltrim` → `.replaceAll("^\\s+", "")`
`chr` → `String.valueOf((char)({args0}))`
`ascii` → `(int) String.valueOf({args0}).charAt(0)`
`reverse` → `new StringBuilder(String.valueOf({args0})).reverse().toString()`
`repeat` → `String.valueOf({args0}).repeat(...)`
`split_part` → 数组索引拆分
`initcap` → Stream 拆分首字母大写
`regexp_replace` → `.replaceAll({args1}, {args2})`
`regexp_like` → `.matches({args1})`
`left` → `.substring(0, Math.min(len, s.length()))`
`right` → `.substring(Math.max(0, s.length() - len))`
`concat` → 特殊处理
`instr` → `.indexOf({args1}) + 1`
`strpos` → `.indexOf(String.valueOf({args1})) + 1`

**数值函数**：
`abs` → 特殊处理（BigDecimal 或 Math）
`ceil` → 特殊处理
`floor` → `Math.floor`
`round` → 特殊处理
`trunc` → `(int) Math.floor((double)({args0}))`
`mod` → `({args0}) % ({args1})`
`power` → `Math.pow`
`sign` → `Integer.signum((int)({args0}))`
`sqrt` → `Math.sqrt`
`log` → `Math.log`
`exp` → `Math.exp`
`sin/cos/tan/asin/acos/atan/atan2` → `Math.*`
`radians` → `Math.toRadians`
`degrees` → `Math.toDegrees`
`random` → `Math.random()`
`pi` → `Math.PI`

**空值处理**：
`coalesce` → `Objects.requireNonNullElse`
`nullif` → 三元表达式
`nvl` → `({args0} != null ? {args0} : {args1})`
`nvl2` → `({args0} != null ? {args1} : {args2})`

**日期函数**：
`current_timestamp` / `now` / `clock_timestamp` / `statement_timestamp` → `new java.sql.Timestamp(System.currentTimeMillis())`
`current_date` → `new java.sql.Date(System.currentTimeMillis())`
`to_date` → 特殊处理
`to_timestamp` → `java.sql.Timestamp.valueOf`
`to_char` → 特殊处理（`SimpleDateFormat`）
`date_trunc` → 特殊处理（`ChronoUnit`）
`add_months` → `LocalDate.now().plusMonths()`

**条件函数**：
`decode` → 特殊处理（嵌套三元）
`greatest` → `Math.max`
`least` → `Math.min`

**聚合存根**：
`count` → `0L`, `sum` → `0L`, `avg` → `0.0d`, `max` → `Math.max`, `min` → `Math.min`

**其他**：
`cast` → `({type}) {expr}`
`gen_random_uuid` → `java.util.UUID.randomUUID().toString()`
`pg_backend_pid` → `Thread.currentThread().getId()`
`inet_client_addr` → `"127.0.0.1"`

### 8.4 类型推断规则

| 表达式 | 推断类型 |
|--------|----------|
| 字符串字面量 | `String` |
| 整数字面量 | `Integer` |
| 浮点字面量 | `Double` |
| 布尔字面量 | `Boolean` |
| Null | `Object` |
| 局部变量 | 查 `proc.local_vars` |
| 参数 | 查 `proc.parameters` |
| 包常量 | 查 `_PACKAGE_CONSTANTS` |
| `abs(BigDecimal arg)` | `java.math.BigDecimal` |
| 字符串函数 | `String` |
| `to_date` | `java.sql.Date` |
| 数值函数（整数类） | `Integer` |
| 数值函数（浮点类） | `Double` |
| 算术运算（任一操作数为 BigDecimal） | `java.math.BigDecimal` |
| 算术运算（任一操作数为 Double） | `Double` |
| 算术运算（任一操作数为 Long） | `Long` |
| 算术运算（默认） | `Integer` |
| CASE 表达式 | 传播最宽类型 |

### 8.5 强制转换规则（`_coerce_java_arg`）

| 源 | 目标 | 转换 |
|----|------|------|
| `""` | 数值类型 | `0L` / `0` / `BigDecimal.ZERO` / `0.0d` / `false` |
| `'123'` | Long / Integer | `123L` / `123` |
| 数值字面量 | BigDecimal | `BigDecimal.valueOf(literal)` |
| `Map.get()` 结果 | Long | `Long.parseLong(String.valueOf())` |
| `Map.get()` 结果 | Integer | `Integer.parseInt(String.valueOf())` |
| `Map.get()` 结果 | BigDecimal | `new BigDecimal(String.valueOf())` |
| BigDecimal | String | `expr.toString()` |
| 字符串日期字面量 | Date | `java.sql.Date.valueOf()` |

### 8.6 辅助函数

**_cleanup_java_expr(expr)**：
- 去除嵌套 `String.valueOf(String.valueOf(x))` → `String.valueOf(x)`
- 常量折叠 `Integer.parseInt(String.valueOf(200))` → `200`
- 去除 `String.valueOf("literal")` → `"literal"`

**_java_op(sql_op)**：SQL 运算符到 Java 运算符的映射（`=` → `==`, `<>` → `!=`, `AND` → `&&`, `OR` → `||`, `||` → `+`）。

---

## 9. SQL 重构

### 9.1 参数占位符转换

#### _convert_placeholders_to_mybatis(sql, proc) → str

**算法**：
1. 构建类型映射：参数名 → `(java_name, jdbc_type, java_type)`
2. 转换 `:param` → `#{javaName, jdbcType=JDBC, javaType=Java}`（有 proc 时）或 `#{javaName}`（无 proc 时）
3. 转换 `$N` → `#{paramN}`

#### _convert_params_to_mybatis(sql, proc) → str

**两阶段转换**：
1. **第一阶段**：复合字段访问 `var.field` → `#{var.field}`（排除已转换的 `#{` 前缀）
2. **第二阶段**：简单参数 → `#{javaName, jdbcType=JDBC, javaType=Java}`（负向前瞻防止重复转换）
3. **清理**：移除 PostgreSQL `::TYPE` 类型转换，转换为标准 CAST

### 9.2 动态 SQL 模板重构

#### _reconstruct_sql_from_concat(ast_node) → (sql_template, param_list)

**算法**：
1. 递归展平 `BinaryOp(||)` 节点
2. 字面量字符串 → 直接拼接文本
3. 变量引用 → `#{var}`（值上下文）或 `${var}`（标识符上下文）
4. SQL 表达式变量 → 递归内联
5. 验证首词是否为 SQL 动词

**上下文判断**（`_is_identifier_context`）：
- 尾随文本以 `'` 结束 → 值上下文
- 尾随文本以 `=` 或 `= '` 结束 → 值上下文
- 其他 → 标识符上下文

#### _flatten_concat(ast_node) → [(text, params)]

递归展平算法：
1. `BinaryOp(||)` → 递归处理左右子树
2. `Literal`：字符串去引号、整数/浮点转字符串、Null 转 `NULL`
3. `PlVariable`：检查是否为 SQL 表达式变量 → 内联或生成占位符
4. `ColumnRef`：多段引用 `${var.field}` 或单段 `#{var}`
5. `FunctionCall`：`to_char`/`sysdate` 等转为 SQL 片段

### 9.3 AST 到 SQL 还原

#### _reconstruct_sql_from_ast(ast) → str

**算法**：
1. 将 AST 写入临时 JSON 文件
2. 调用 `ogsql json2sql -f temp.json`
3. 修复关键字间距（`_fix_reconstructed_sql`）
4. 消歧 GROUP/ORDER（`_qualify_ambiguous_group_order`）
5. 补充缺失子句（`_append_missing_select_clauses`）

#### _fix_reconstructed_sql(sql) → str

修复 ogsql json2sql 输出中的关键字间距问题：在标识符和关键字之间插入空格，修复连接的关键字对（如 `GROUPBY` → `GROUP BY`）。处理约 80 个 SQL 关键字。

### 9.4 Mapper XML SQL 处理管线

`_build_mapper_statement()` 的完整处理步骤：

1. 清理 SQL：移除注释、规范化空白
2. 去除尾部分号
3. 移除 `BULK COLLECT INTO` 子句
4. 修复 `DELETE FROM x x FROM` 语法
5. 保护 MyBatis 占位符文本（暂存）
6. 通过 ogsql 格式化 SQL
7. 修复关键字间距和特殊情况
8. 补充缺失的 SELECT 子句（LIMIT、锁子句）
9. 添加缺失的 LATERAL 关键字
10. Oracle 语法转 PostgreSQL
11. 去引号标识符（保留保留字引号）
12. 恢复占位符
13. 转换参数为 MyBatis `#{}` 语法
14. 动态 SQL 中整个体为 `#{var}` 时使用 `${}`
15. 去除 `RETURNING INTO` 子句
16. 单行 SELECT 无 LIMIT 时追加 `LIMIT 1`

---

## 10. 项目生成

### 10.1 generate_project() 编排流程

**Python 引擎**（13 步完整管线）：

```
1. 骨架文件（仅当文件不存在时写入）
   ├── pom.xml
   ├── application.yml
   ├── DemoApplication.java
   └── BusinessException.java

2. 每个活跃包（changed_packages 或全部）
   ├── 收集服务注入（_collect_service_injections）
   ├── {Name}Service.java
   ├── {Name}Mapper.java
   ├── {Name}Mapper.xml
   ├── {Name}ServiceTest.java
   └── 保存生成检查点

3. 集成测试（可选）
   ├── AbstractIntegrationTest.java
   ├── itest-schema.sql
   └── 每个包的集成测试类
```

**Rust 引擎**（4 阶段管线）：

```
Phase 0: validate  → SQL 语法校验（含包一致性检查）
Phase 1: parse     → 增量解析 SQL 文件（缓存 AST JSON）
Phase 2: analyze   → 过程分析 + DDL schema 提取 + OUT 参数提升
Phase 3: generate  → 代码生成（骨架 + 包 + 集成测试）
```

> **关键差异**：Rust 缺少 Python 引擎的 DDL 预扫描阶段（Phase 0）、基于 manifest.json 的增量追踪、过期包清理、检查点/续做（`--resume`）功能。DDL 解析在 Rust 的 Phase 2 中进行而非 Python 的 Phase 0。

### 10.2 骨架文件生成

#### pom.xml

标准 Spring Boot 3.2.5 + MyBatis 项目结构：
- `spring-boot-starter-web`
- `mybatis-spring-boot-starter:3.0.3`
- `postgresql` JDBC 驱动
- `lombok`（可选）
- `spring-boot-starter-test`
- `testcontainers`（测试范围）
- 日志框架依赖（从配置注入）
- Surefire 插件排除 `**/itest/**`
- `integration` profile 包含 itest 测试

#### application.yml

```yaml
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/demo
    username: postgres
    password: postgres
    driver-class-name: org.postgresql.Driver
mybatis:
  mapper-locations: classpath:mapper/*.xml
  configuration:
    map-underscore-to-camel-case: true
```

#### DemoApplication.java

```java
@SpringBootApplication
@MapperScan("{BASE_PACKAGE}.mapper")
public class DemoApplication { ... }
```

#### BusinessException.java

```java
public class BusinessException extends RuntimeException {
    public BusinessException(String message) { super(message); }
    public BusinessException(String message, Throwable cause) { super(message, cause); }
}
```

### 10.3 Mapper 接口生成 (`_write_mapper_interface`)

**文件路径**：`{base}/{java_pkg_path}/mapper/{ClassName}Mapper.java`

```java
package {java_pkg}.mapper;
import org.apache.ibatis.annotations.*;

@Mapper
public interface {ClassName}Mapper {
    // Source: file.sql:line - proc.name
    {ReturnType} {methodName}(@Param("p1") Type1 p1, @Param("p2") Type2 p2);
    // ...更多方法
}
```

**方法签名生成规则**（`_build_mapper_method`）：

| 情况 | 返回类型 |
|------|----------|
| 有 `returning_cols` | `Map<String, Object>` |
| SELECT + `returns_list` | `List<Map<String, Object>>` |
| SELECT + 简单类型 | `Integer` / `String`（连接时） |
| INSERT/UPDATE/DELETE | `int` |
| 默认 | `void` 或 `Map<String, Object>` |

**参数列表**：
- 跳过 OUT 参数
- INOUT 参数使用 `.get()`
- 包含 SQL 中使用的局部变量
- 包含 `extra_params`
- FORALL 批量：`@Param("list") List<Map<String, Object>> list`

### 10.4 Mapper XML 生成 (`_write_mapper_xml`)

**文件路径**：`{base}/src/main/resources/mapper/{ClassName}Mapper.xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
        "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="{java_pkg}.mapper.{ClassName}Mapper">

    <select id="methodName" resultType="java.util.LinkedHashMap">
        SELECT ... FROM ... WHERE col = #{param, jdbcType=VARCHAR, javaType=String}
    </select>

    <insert id="methodName">
        INSERT INTO table (col1, col2) VALUES (#{p1}, #{p2})
    </insert>

    <!-- FORALL 批量 -->
    <insert id="batchMethod">
        <foreach collection="list" item="item" separator=";">
            INSERT INTO table (col) VALUES (#{item.col})
        </foreach>
    </insert>

</mapper>
```

**namespace**：`{java_pkg}.mapper.{ClassName}Mapper`

**标签选择**：SELECT → `<select>`, INSERT → `<insert>`, UPDATE → `<update>`, DELETE → `<delete>`

**resultType 属性**：
- Map 结果：`java.util.LinkedHashMap`
- 简单类型：小写类型名
- 自定义类型：完整类型名

**SQL 处理**：经过 `_build_mapper_statement()` 完整管线（见 9.4 节）。

### 10.5 Service 类生成 (`_write_service_class`)

**文件路径**：`{base}/{java_pkg_path}/service/{ClassName}Service.java`

```java
package {java_pkg}.service;
// imports...

@Service
public class {ClassName}Service {

    private static final Logger log = LoggerFactory.getLogger({ClassName}Service.class);
    private final {ClassName}Mapper {mapperVar};
    private final {OtherService} {otherServiceVar};
    // ...

    public {ClassName}Service({ClassName}Mapper {mapperVar}, {OtherService} {otherServiceVar}) {
        this.{mapperVar} = {mapperVar};
        this.{otherServiceVar} = {otherServiceVar};
    }

    // 包级常量
    private static final Long MAX_RETRY = 3L;

    // 包级可变变量
    private String globalStatus = "";

    // 自定义记录类型
    public static class CoordRec {
        public Integer x;
        public Integer y;
    }

    // 辅助方法（按需生成）
    private int _crc32(String input) { /* CRC32 实现 */ }
    private String _md5(String input) { /* MD5 实现 */ }
    private <T> List<T> _appendList(List<T> list, T element) { ... }

    // 业务方法（从过程生成）
    @Transactional
    public void createOrder(Long orderId, String productName) {
        // 转换后的 Java 逻辑
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void autonomousOperation() {
        // 自治事务方法
    }
}
```

**方法签名生成**（`_build_service_method`）：
- IN 参数：基本类型
- OUT 参数：`AtomicReference<Type>`
- 返回类型：函数返回类型，或 REFCURSOR OUT 的 `List<Map<String, Object>>`
- 自治事务：`@Transactional(propagation = REQUIRES_NEW)`
- 含 DML 操作：`@Transactional`

**局部变量提升**：所有局部变量在方法开头声明并初始化默认值。

**编译问题检测**（`_has_compilation_issues`）：
- 未解析的游标结果变量
- AtomicReference 比较运算符
- OUT 参数访问模式
- 子查询/范围/位串占位符
- GOTO 死循环
- 未解析函数调用
- BigDecimal 字面量问题
- 服务自注入循环
- 基本类型 `.equals()` 调用

### 10.6 单元测试生成 (`_write_service_test`)

**文件路径**：`{base}/src/test/java/{java_pkg_path}/service/{ClassName}ServiceTest.java`

```java
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class {ClassName}ServiceTest {

    @Mock private {ClassName}Mapper {mapperVar};
    @Mock private {OtherService} {otherServiceVar};
    @InjectMocks private {ClassName}Service service;

    @Test
    void test_createOrder_success() {
        // Arrange
        when({mapperVar}.selectMethodName(any())).thenReturn(Map.of("col", val));
        // Act
        service.createOrder(1L, "product");
        // Assert
        verify({mapperVar}).insertMethodName(any(), any());
    }

    @Test
    void test_createOrder_throwsBusinessException() {
        // Act & Assert
        assertThrows(BusinessException.class, () -> service.createOrder(null, null));
    }
}
```

**测试方法生成**：
- 每个过程一个成功测试
- 含 RAISE 的过程额外生成异常测试
- 不安全 while 循环或递归调用 → `@Disabled`
- 测试数据由 `_default_test_value()` 根据类型和名称推断

**Mock 返回值**（`_mock_select_return`）：
- SELECT 列表：`List.of(Map.of("col", inferredVal))`
- SELECT 单行：对应类型的默认值
- INSERT/UPDATE/DELETE：`1`
- RETURNING：Map 带列推断值

### 10.7 命名约定汇总

| SQL 实体 | Java 产物 | 命名规则 |
|----------|-----------|----------|
| `pkg_order` | `OrderService` | 去掉 `pkg_` 前缀 + PascalCase + `Service` |
| `pkg_order` | `OrderMapper` | 去掉 `pkg_` 前缀 + PascalCase + `Mapper` |
| `create_order` | `createOrder()` | snake_to_camel |
| 参数 `p_order_id` | `pOrderId` | snake_to_camel |
| Mapper 变量 | `orderMapper` | PascalCase 首字母小写 + `Mapper` |
| Service 变量 | `orderService` | PascalCase 首字母小写 |
| 测试类 | `OrderServiceTest` | Service 类名 + `Test` |

---

## 11. 集成测试生成

### 11.1 基础设施

#### AbstractIntegrationTest

**Testcontainers 模式**：
```java
@SpringBootTest
@ActiveProfiles("integration")
@Testcontainers
@Sql(scripts = "classpath:itest-schema.sql")
public abstract class AbstractIntegrationTest {
    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16-alpine")
            .withDatabaseName("test").withUsername("test").withPassword("test");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        // ...
    }
}
```

**Remote 模式**：
```java
@SpringBootTest
@ActiveProfiles("integration")
@SqlMergeMode(MERGE)
@Sql(scripts = "classpath:itest-schema.sql")
public abstract class AbstractIntegrationTest { }
```

### 11.2 Schema 提取与 DDL 生成

**提取管线**：
1. 从 DML 语句提取序列（`NEXTVAL` 模式）
2. 从 DML 语句提取表和列：
   - INSERT：解析列列表
   - SELECT：提取 SELECT 子句和 WHERE 条件中的列
   - UPDATE：提取 SET 和 WHERE 列
   - JOIN：提取连接表
3. 从 `TYPE_OVERRIDES` 推断列类型
4. 处理自增表

**DDL 生成顺序**：
1. DROP/CREATE 序列
2. DROP/DELETE 表（Testcontainers → DROP，Remote → DELETE）
3. CREATE TABLE IF NOT EXISTS
4. 提取 INSERT 测试数据
5. ID 偏移 8000 避免冲突

### 11.3 测试数据推断

**值生成规则**（`_itest_generate_test_value`）：

| 类型 | 生成值 |
|------|--------|
| Integer（ID/no 结尾） | `1` |
| Integer（其他） | `10` |
| Numeric | `99.99`（尊重精度） |
| Timestamp | `'2024-01-01 00:00:00'` |
| Date | `'2024-01-01'` |
| Boolean | `true` |
| VARCHAR | `'test_{col}'`（尊重长度） |
| 单字符 | `'Y'` |

### 11.4 集成测试类

```java
class {ClassName}ServiceIntegrationTest extends AbstractIntegrationTest {
    @Autowired private {ClassName}Mapper {mapperVar};
    @Autowired private {ClassName}Service service;

    @Test
    @Timeout(value = 10, unit = TimeUnit.SECONDS)
    @Sql(scripts = "classpath:itest-fixtures/{pkg}_{proc}.sql")
    void test_{method}_integration() {
        // 参数设置
        // OUT 参数容器
        // 服务调用
        // 断言
    }
}
```

**超时计算**：10-30 秒，基于过程复杂度评分。

**禁用条件**：存根、不安全 while、递归调用、动态 SQL。

**传递表解析**：BFS 遍历过程调用链，深度限制 2 层，收集所有需要的表。

---

## 12. CLI 与报告

### 12.1 命令行参数

| 参数 | 类型 | 用途 |
|------|------|------|
| `-c, --config FILE` | 路径 | YAML 配置文件路径 |
| `-o, --output DIR` | 路径 | 输出目录（CLI 模式） |
| `-s, --sources SQL...` | 路径列表 | SQL 源文件（CLI 模式） |
| `--full` | 标志 | 强制完整重新生成（忽略缓存） |
| `--resume` | 标志 | 从检查点恢复（跳过已完成的包） |
| `--skip-validate` | 标志 | 跳过 SQL 语法验证 |
| `--report FILE` | 路径 | 报告输出路径 |

### 12.2 main() 执行管线

**Python 引擎**（13 步）：

```
1. 解析参数 → 确定 BASE_PACKAGE、日志配置、Java 包映射
2. 增量构建检测 → SHA-256 哈希比较 → 确定 changed_files
3. SQL 验证（Phase -1）→ 批量验证变更文件
4. DDL 预扫描（Phase 0）→ 解析 CREATE TABLE → 填充 TYPE_OVERRIDES
5. 解析 SQL 文件（Phase 1）→ 加载缓存 AST / 批量解析变更文件
6. 分析过程（Phase 2）→ 构建依赖图 → 生成 Java 逻辑行
7. OUT 局部变量提升（Phase 2.5）
8. 依赖解析 → BFS 查找依赖包
9. 生成项目（Phase 3）→ 调用 generate_project()
10. 清理过期包 → 删除已移除包的文件
11. 保存清单 → 存储新哈希和元数据
12. 生成报告 → 时间戳报告 + 最新报告
13. 输出摘要 → 统计信息 + 报告路径
```

**Rust 引擎**（4 阶段）：

```
Phase 0: validate  → SQL 语法校验（含交互式错误确认，非交互模式自动中止）
Phase 1: parse     → 增量解析（检查 .fluxgauss/ast/ 缓存，按需重新解析）
Phase 2: analyze   → 过程分析 + parse_table_ddl() + promote_out_local_vars()
Phase 3: generate  → 项目生成 + 集成测试 + 报告
```

> **关键差异**：
> - Rust 无 manifest.json 清单文件（无 SHA-256 文件级哈希跟踪）
> - Rust 无独立的 DDL 预扫描阶段（DDL 在 analyze 阶段读取）
> - Rust 无显式的依赖解析阶段（BFS 传递依赖触发）
> - Rust 无过期包清理
> - Rust 有 `--resume` CLI 参数但未实现检查点功能
> - Rust 有 Phase 0 交互式错误确认（Python 无）
> - Rust Phase 0 验证包含包一致性检查和未定义变量检查

### 12.3 转换报告

**输出格式**：Markdown

**报告章节**：
1. 头部：时间戳、配置路径、输出目录
2. 概览表：文件数、包数、过程数、DML 数、调用数、转换数、存根数、跳过数、错误数、警告数、未解析数、TODO 数
3. SQL → Java 映射表（按文件分组）
4. 跳过的语句（DDL/DML/OTHER 分类）
5. 错误和警告
6. 未解析的跨包调用
7. 存根过程及原因
8. 数据库对象依赖
9. 未映射函数调用
10. TODO 摘要（按类别和过程分布）

### 12.4 全局追踪变量

| 变量 | 用途 |
|------|------|
| `UNRESOLVED_CALLS` | 未解析的跨服务调用 |
| `STUB_PROCEDURES` | 存根过程列表 |
| `STUB_REASONS` | 存根原因映射 |
| `UNSUPPORTED_FUNCTIONS` | 不支持的 SQL 函数 |
| `TODO_SUMMARY` | TODO 项汇总 |
| `_MISSING_OVERLOADS` | 缺失的方法重载 |
| `_PACKAGE_CONSTANTS` | 恢复的包常量 |
| `_PACKAGE_VARIABLES` | 包变量信息 |
| `_UDF_RETURN_TYPES` | 用户定义函数返回类型 |

---

## 13. 增量构建与缓存

### 13.1 缓存目录结构

```
.fluxgauss/
├── manifest.json                     # 文件哈希和元数据
├── ast/                              # 缓存的 AST JSON
│   ├── pkg_order.json
│   └── pkg_product.json
├── generation-checkpoint.json        # 恢复检查点
├── logs/
│   ├── conversion-20260526_123456.log
│   └── conversion-latest.log         # 符号链接到最新
└── reports/
    ├── conversion-report-20260526_123456.md
    └── conversion-report-latest.md
```

### 13.2 清单格式

```json
{
  "files": {
    "sql/pkg_order.sql": {
      "hash": "sha256hex...",
      "package": "pkg_order",
      "java_package": "com.example.order"
    }
  }
}
```

### 13.3 增量构建逻辑

1. 加载缓存的 `manifest.json`
2. 计算每个 SQL 文件的 SHA-256 哈希
3. 比较缓存哈希 → 确定 `changed_files`
4. 未变更文件从 `.fluxgauss/ast/` 加载缓存 AST
5. 变更文件重新解析
6. **传递依赖**：通过 BFS 遍历 `service_calls` 查找依赖包 → 也标记为需重新生成

### 13.4 恢复检查点

```json
{
  "completed": ["pkg_order", "pkg_product"],
  "updated_at": "2026-05-26T12:34:56"
}
```

**`--resume` 模式**：加载检查点，跳过已完成的包。成功完成后清除检查点。

### 13.5 清理过期包

生成完成后，检查输出目录中的 Service/Mapper 文件，如果对应的 SQL 包已不在源列表中，则删除这些文件。

---

## 附录 A：完整数据流示例

以 `pkg_order.create_order(p_product_id INTEGER, p_qty INTEGER)` 为例：

```
1. 解析
   SQL 文件 → ogsql → AST: {CreatePackageBody: {items: [{Function: {...}}]}}

2. 提取
   extract_procedures() → ProcedureInfo(
       name="pkg_order.create_order",
       parameters=[Parameter(name="p_product_id", java_type="Integer"),
                   Parameter(name="p_qty", java_type="Integer")],
       body={stmts: [{Assignment: ...}, {SqlStatement: ...}, {Return: ...}]}
   )

3. 分析
   analyze_procedure() → 遍历 body.stmts:
   - Assignment "v_status := 'PENDING'" → local_vars["v_status"] = "String"
   - SqlStatement INSERT INTO orders → DmlStatement(sql_type="Insert", ...)
   - SqlStatement SELECT price → DmlStatement(sql_type="Select", ...)
   - Return v_status → "return vStatus"

4. 生成
   _write_service_class() → OrderService.java:
       public String createOrder(Integer pProductId, Integer pQty) {
           String vStatus = "PENDING";
           _sqlRowCount = orderMapper.insertOrder(pProductId, pQty, vStatus);
           _row = orderMapper.selectPrice(pProductId);
           ...
           return vStatus;
       }

   _write_mapper_xml() → OrderMapper.xml:
       <insert id="insertOrder">
           INSERT INTO orders (product_id, qty, status)
           VALUES (#{pProductId, jdbcType=INTEGER, javaType=Integer},
                   #{pQty, jdbcType=INTEGER, javaType=Integer},
                   #{vStatus, jdbcType=VARCHAR, javaType=String})
       </insert>

   _write_service_test() → OrderServiceTest.java:
       @Test void test_createOrder_success() { ... }
```

---

## 附录 B：PL/pgSQL 到 Java 构造映射表

| PL/pgSQL | Java |
|----------|------|
| `PROCEDURE` | `void method()` |
| `FUNCTION` | `Type method()` |
| `OUT` 参数 | `AtomicReference<Type>` |
| `INOUT` 参数 | `AtomicReference<Type>` |
| `REFCURSOR` | `List<Map<String, Object>>` |
| `SELECT INTO` | `_row = mapper.select(); var = (Type)_row.get("col")` |
| `BULK COLLECT INTO` | `List<Map> list = mapper.select(); for循环提取` |
| `INSERT/UPDATE/DELETE` | `_sqlRowCount = mapper.dml()` |
| `RETURNING INTO` | `_row = mapper.dml(); var = _row.get("col")` |
| `IF/ELSIF/ELSE` | `if/else if/else` |
| `FOR i IN 1..N` | `for (int i=1; i<=N; i++)` |
| `FOR rec IN SELECT` | `for (Map rec : mapper.select())` |
| `FOR rec IN cursor` | 索引迭代 `cursorResult.get(idx)` |
| `WHILE cond LOOP` | `while (cond)` |
| `LOOP ... END LOOP` | `while (true)` |
| `EXIT WHEN cond` | `if (cond) break` |
| `CONTINUE WHEN cond` | `if (cond) continue` |
| `CASE WHEN THEN` | `if/else if/else` 或三元运算符 |
| `RAISE EXCEPTION` | `throw new BusinessException(msg)` |
| `RAISE NOTICE` | `log.info(msg)` |
| `EXECUTE IMMEDIATE` | 动态 Mapper 方法 |
| `FORALL` | 批量 `<foreach>` 或循环 |
| `PERFORM func()` | `this.method()` / `service.method()` |
| `COMMIT` | Spring `@Transactional` 自动提交 |
| `ROLLBACK` | `TransactionAspectSupport.setRollbackOnly()` |
| `SAVEPOINT` | `connection.setSavepoint()` |
| `GOTO` | 模式检测 → break/continue/do-while/状态机 |
| `||`（字符串连接） | `.concat()` 或 `+` |
| `:=`（赋值） | `=` 或 `.set()`（OUT 参数） |
| `IS NULL` | `== null` |
| `LIKE '%x%'` | `.contains("x")` |
| `BETWEEN a AND b` | `>= a && <= b` |
| `IN (list)` | `Arrays.asList(list).contains(x)` |
| `COALESCE(a, b)` | `Objects.requireNonNullElse(a, b)` |
| `NVL(a, b)` | `a != null ? a : b` |
| `SYSDATE` | `new Timestamp(System.currentTimeMillis())` |
| `%TYPE` | TYPE_OVERRIDES 查找或列名推断 |
| `%ROWTYPE` | `Map<String, Object>` |

---

## 附录 C：双引擎功能矩阵

本文档以 Python 引擎为主要参考（功能最完整）。下表汇总 Rust 引擎与 Python 引擎的关键差异。

### C.1 数据模型

| 数据类型 | Python | Rust | 差异 |
|----------|--------|------|------|
| Parameter.mode | `Optional[str]` (`"IN"/"OUT"/"INOUT"`) | `Option<ParamMode>` 枚举 | Rust 类型安全 |
| CommentInfo | `CommentInfo`（5 字段，含 `column`） | `CommentBlock`（4 字段，无 `column`） | 字段名和类型不同 |
| DmlStatement | 14 字段 | 8 字段 | Rust 缺少 `is_dynamic`、`returning_cols`、`returning_into_vars`、FORALL 相关字段 |
| SkippedItem | 7 字段（含 `category`, `line_start`/`line_end`） | 5 字段（`line_number` 替代） | 字段名和结构不同 |
| ProcedureMapping | 12 字段 | 6 字段 | Rust 缺少 `mapper_methods`、`generated_files`、`has_parse_error`、`stub_reasons` 等 |
| ConversionReport | 独立类（14 字段） | 集成在 `PipelineResult` 中 | 无独立类型 |
| GotoPattern | 字符串 A/B/C/D/E | `GotoPattern` 枚举（CleanupExit 等） | Rust 描述性命名 |
| ProcedureInfo.body | `dict` | `Option<PlBlock>`（强类型 AST） | Rust 类型安全 |

### C.2 语句处理

| 特性 | Python | Rust |
|------|--------|------|
| 处理架构 | 独立 `_process_*()` 函数 | 内联 `match` 语句（~700 行） |
| 模块化 | 无（单文件） | `statements/` 子模块（当前为空存根） |
| 额外语句类型 | 无 | ForEach, ReturnNext, Move, PipeRow, VariableSet/Reset 等 9 种 |
| 存根语句 | 0 | 11 种（Fetch/Close/Move/Commit/Savepoint/ReturnQuery/GetDiagnostics/ForAll 等） |

### C.3 SQL 函数覆盖

| 类别 | Python | Rust | 覆盖率 |
|------|--------|------|--------|
| 字符串函数 | 25 | 16 | 64% |
| 数值函数 | 20 | 13 | 65% |
| 日期函数 | 15 | 7 | 47% |
| 空值处理 | 4 | 4 | 100% |
| JSON/数组 | 11 | 4 | 36% |
| 编码/哈希 | 6 | 0 | 0% |
| Oracle 兼容 | 7 | 0 | 0% |
| PostGIS | 5 | 0 | 0% |
| 聚合存根 | 5 | 8 | 部分 |
| **总计** | **~110** | **~60** | **~40%** |

### C.4 管线功能

| 功能 | Python | Rust |
|------|--------|------|
| SQL 语法校验 | ✅ Phase -1 | ✅ Phase 0（含交互式确认） |
| DDL 预扫描 | ✅ Phase 0（独立阶段） | ⚠️ 在 Phase 2 中进行 |
| 增量解析 | ✅ SHA-256 + manifest.json | ✅ 文件级缓存 |
| 传递依赖追踪 | ✅ BFS 自动触发 | ❌ |
| 过期包清理 | ✅ | ❌ |
| 检查点/续做 | ✅ `--resume` | ❌（CLI 参数存在但未实现） |
| 并行处理 | ❌ 单线程 | ✅ Rayon 多线程 |
| 包一致性检查 | ❌ | ✅ Phase 0 |
| 转换报告 | ✅ Markdown | ✅ Markdown |

### C.5 配置支持

| 配置项 | Python | Rust |
|--------|--------|------|
| `logger` 预设/自定义 | ✅ | ✅ |
| `database` | ✅ | ✅ |
| `java_packages` | ✅ | ✅ |
| `integration_test` | ✅ | ✅ |
| `type_aliases` | ✅ | ❌ |
| `init_sql`（集成测试） | ✅ | ❌ |

### C.6 已知 Rust 缺陷

1. **NULLIF 映射错误**：Rust 在参数相等时返回 `1` 而非 `null`
2. **to_timestamp / add_months / date_trunc**：存根返回 `null`，非功能实现
3. **静态类型映射**：`sql_type_to_java()` 仅查静态表，无 Python 的自定义类型预设（`_CUSTOM_TYPE_PRESETS`）匹配
4. **FORALL 不支持**：存根注释，无批量操作生成
5. **动态 SQL 模板**：不支持 Python 的 `dynamic_sql_templates` 和 `inlined_sql_vars`

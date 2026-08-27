# FluxGauss

将 OpenGauss / PostgreSQL 存储过程（PL/pgSQL）自动转换为 Spring Boot + MyBatis Java 项目。

[![Version](https://img.shields.io/badge/version-0.6.27-blue)](crates/fluxgauss/Cargo.toml)

## 功能亮点

- **AST 驱动的语义转换** — 基于 Rust SQL 解析器（ogsql-parser）生成完整抽象语法树，逐节点转换为 Java 代码
- **双引擎** — Python 引擎（~17300 行，评级 A-）+ Rust 引擎（~18800 行，高性能），共享配置格式
- **增量构建** — SHA-256 内容缓存 + 传递依赖追踪，只重新生成变更部分
- **跨包依赖自动解析** — 自动识别包间调用，生成正确的 Service 注入和 import
- **自动化测试生成** — Mockito 单元测试 + Testcontainers 集成测试
- **110+ 内置 SQL 函数映射** — 字符串、数学、日期、空值处理、类型转换、三角/编码/JSON 函数
- **动态 SQL → MyBatis Dynamic XML** — 自动检测条件 SQL 拼接，生成 `<if>`、`<where>`、`<set>` 等动态标签
- **源码溯源注释** — 生成代码中标注原始 SQL 文件名和行号范围
- **调试模式** — `--debug` 在生成的 Java/XML 中注入精确的 SQL 源码行号注释，方便逐行比对
- **转换报告 + 处理日志** — 自动输出 Markdown 格式的 SQL-to-Java 映射报告及详细处理日志
- **多编码支持** — 通过 `--encoding` 或 YAML 配置指定源码编码（如 GBK），自动处理非 UTF-8 SQL 文件
- **独立二进制分发** — 通过 PyInstaller 打包为单文件可执行程序（Linux / macOS / Windows）
- **MCP 服务器模式** — `--mcp` 标志启动 stdio MCP 服务器，AI 客户端可直接调用 validate_sql 和 convert_sql 工具
- **MCP 服务器 + REST API**（ogsql-parser 内建） — 支持 MCP 协议 和 REST API 模式的 SQL 解析服务

## 快速开始

### 前置条件

- Python 3.10+（Python 引擎）
- Rust 1.80+（Rust 引擎，可选）
- Java 17+（用于编译验证生成结果）
- Docker（可选，用于集成测试的 Testcontainers 模式）

### 安装

```bash
# 方式一：源码运行（推荐）
git clone https://github.com/c2j/flux-gauss.git
cd flux-gauss

# 方式二：独立二进制（无需 Python/Rust 环境）
# 从 Releases 页面下载对应平台的 fluxgauss-py 二进制
# chmod +x fluxgauss-py && ./fluxgauss-py -c fluxgauss.yaml
```

### 编写配置文件

创建 `fluxgauss.yaml`：

```yaml
output_dir: ./dest
base_package: com.example.demo

sources:
  - sql/pkg_order.sql
  - sql/pkg_product.sql
```

### 执行转换

```bash
# Python 引擎（推荐）
python3 converter/flux_gauss.py -c fluxgauss.yaml

# Rust 引擎（大批量场景）
cargo run --bin fluxgauss -- --config fluxgauss.yaml

# 独立二进制
./fluxgauss-py -c fluxgauss.yaml
```

### 编译验证

```bash
cd dest
mvn compile        # 编译检查
mvn test           # 运行自动生成的单元测试
```

> **Demo 配置文件说明**: `demo-project/` 下提供三份示例配置 — `fluxgauss_py.yaml`（Python 引擎）、`fluxgauss_ru.yaml`（Rust 引擎）、`fluxgauss_tu.yaml`（小规模测试集，`tu` = Test Unit）。输出目录分别为 `dest_py/`、`dest_ru/`、`dest_tu/`。

## 架构

```
SQL 文件
  |
  v
ogsql-parser (Rust 二进制/crate)  --  JSON AST
  |
  v
FluxGauss 转换引擎 (Python / Rust)
  |
  v
Spring Boot + MyBatis 项目
  ├── {Name}Service.java        -- 业务逻辑
  ├── {Name}Mapper.java         -- Mapper 接口
  ├── {Name}Mapper.xml          -- MyBatis SQL 映射
  └── {Name}ServiceTest.java    -- 单元测试
```

ogsql-parser 同时提供 **MCP 服务器**（`ogsql-mcp`）和 **REST API 服务器**（`ogsql serve`），可用于构建 SQL 解析微服务和 AI 工具链集成。

## 双引擎

| 维度 | Python 引擎 | Rust 引擎 |
|------|------------|-----------|
| 入口 | `converter/flux_gauss.py` | `crates/fluxgauss/` |
| 代码量 | ~17300 行 | ~18800 行 |
| 功能完整度 | 完整（评级 A-） | 持续对齐中（评级 B+） |
| 适用场景 | 通用（100-1000 个 SP） | 大批量（1000-30000 个 SP） |
| 增量构建 | ✅ | ✅ |
| 集成测试生成 | ✅ | ✅ |
| 动态 SQL → MyBatis XML | ✅ | ✅ |
| 多编码支持 | ✅ | ✅ |
| 调试模式（--debug） | ✅ | ✅ |
| 并行处理 | 单线程 | Rayon 多线程 |
| SQL 校验 | ✅ | ✅ |
| 配置格式 | YAML | YAML（相同） |

## 支持的 PL/pgSQL 特性

| PL/pgSQL 特性 | Java 转换方式 |
|---------------|--------------|
| PROCEDURE | Service 方法 |
| FUNCTION | 带返回值的 Service 方法 |
| IN / OUT / IN OUT 参数 | 方法参数 / `AtomicReference<T>` |
| SELECT / INSERT / UPDATE / DELETE | MyBatis Mapper 方法 + XML |
| IF / ELSIF / ELSE | if / else if / else |
| FOR ... IN SELECT LOOP | for 循环 + Mapper 查询 |
| WHILE LOOP | while 循环 |
| 游标 OPEN / FETCH / CLOSE | Mapper 查询 + 变量赋值 |
| EXCEPTION（WHEN OTHERS） | try-catch 块 |
| 跨包调用（pkg.proc） | Service 注入 + 方法调用 |
| PRAGMA AUTONOMOUS_TRANSACTION | `@Transactional(propagation = REQUIRES_NEW)` |
| %TYPE 锚定类型 | 从 DDL 解析实际 Java 类型 |
| 动态 SQL（EXECUTE IMMEDIATE） | Mapper 调用（参数化）+ 动态 XML 标签 |
| REFCURSOR OUT 参数 | `List<Map<String, Object>>` |
| GOTO | 状态机 / do-while + continue |
| MERGE INTO | MyBatis `<update>` 标签（保留原生 MERGE 语法） |
| TRUNCATE | Mapper `<update>` 调用 |
| TABLE OF 类型 | `java.util.List<ElemType>` |
| FORALL 批量操作 | MyBatis `<foreach>` 批量或逐条循环 |
| RETURNING INTO | Mapper 调用 + 结果提取 |
| SELECT INTO | Mapper 查询 + 变量提取 |
| RAISE EXCEPTION | BusinessException 抛出 |
| GET DIAGNOSTICS row_count | 影响行数变量赋值 |
| SAVEPOINT / ROLLBACK TO | 注释存根（标记需人工处理） |
| 包常量（PACKAGE CONSTANTS） | static final 字段 |
| CASE WHEN | if-else 或嵌套三元表达式 |
| 自定义类型（RECORD / VARRAY / TABLE OF） | 内部类 / `List<T>` |
| CONTINUE / EXIT WHEN | continue / break |
| COMMIT / ROLLBACK | 注释存根 |

## 配置选项

### 完整配置示例（`fluxgauss.yaml`）

```yaml
output_dir: ./dest
base_package: com.example.demo

# 日志框架（可选，默认 slf4j）
logger: slf4j          # slf4j | log4j2 | commons-logging | jul | 自定义

# 源码编码（可选，默认 utf-8）
encoding: gbk           # utf-8 | gbk | gb2312 | big5

# 数据库连接（可选，用于生成 application.yml）
database:
  url: jdbc:postgresql://localhost:5432/mydb
  username: app_user
  password: secret
  driver: org.postgresql.Driver

# SQL 源文件列表（必填）
sources:
  - sql/pkg_order.sql
  - sql/pkg_product.sql

# Java 包映射（可选）— 将指定文件生成到不同的 Java 包下
java_packages:
  - package: com.example.order
    sources:
      - sql/pkg_order.sql

# 自定义类型别名（可选）— 将自定义 SQL 类型映射到 Java 类型
type_aliases:
  VARCHAR_ARRAY: "List<String>"
  MY_CUSTOM_TYPE: "com.example.MyType"

# 集成测试（可选）
integration_test:
  enabled: true
  mode: testcontainers    # testcontainers | remote
  # mode: remote
  # url: jdbc:postgresql://localhost:5432/testdb
  # username: test_user
  # password: test_pass
  init_sql:
    - sql/tables.sql
```

## 命令行选项

```
fluxgauss -c <config.yaml> | -o <dir> -s <sql> [...]

选项:
  -c, --config FILE         YAML 配置文件路径
  -o, --output DIR          输出目录（与 -s 配合使用）
  -s, --sources SQL...      SQL 源文件列表（与 -o 配合使用）
  --report FILE             指定转换报告输出路径（Markdown 格式）
  --full                    强制全量重新生成（忽略缓存）
  --resume                  从断点续做（跳过已成功生成的包）
  --skip-validate           跳过 SQL 语法校验阶段
  --encoding ENC            指定源码编码格式（默认 UTF-8）
  --debug                   调试模式：在生成的 Java/XML 中注入 SQL 源码行号注释
  --mcp                     以 MCP 服务器模式启动（stdio 协议）
  -v, --version             显示版本信息
  -h, --help                显示帮助信息
```

### 调试模式（`--debug`）

在生成的 Java 代码和 MyBatis XML 中注入精确的 SQL 源码行号注释，便于逐行比对转换结果：

```java
// SQL: pkg_order.sql:42  →  Java 转换后的代码行，标注了原始 SQL 位置
if (pStatus != null) {
```

## 生成项目结构

```
dest/
  ├── pom.xml                                          # Maven 项目配置
  ├── src/
  │   ├── main/
  │   │   ├── java/com/example/demo/
  │   │   │   ├── DemoApplication.java                 # Spring Boot 启动类
  │   │   │   ├── exception/
  │   │   │   │   └── BusinessException.java           # 业务异常
  │   │   │   ├── mapper/
  │   │   │   │   ├── OrderMapper.java                 # Mapper 接口
  │   │   │   │   └── ...
  │   │   │   └── service/
  │   │   │       ├── OrderService.java                # Service 类
  │   │   │       └── ...
  │   │   └── resources/
  │   │       ├── application.yml                      # Spring Boot 配置
  │   │       └── mapper/
  │   │           ├── OrderMapper.xml                  # MyBatis XML 映射
  │   │           └── ...
  │   └── test/
  │       └── java/com/example/demo/
  │           ├── service/
  │           │   ├── OrderServiceTest.java            # 单元测试
  │           │   └── ...
  │           └── itest/                               # 集成测试（可选）
  │               ├── AbstractIntegrationTest.java
  │               └── OrderServiceIntegrationTest.java
  └── .fluxgauss/                                      # 增量缓存 + 报告 + 日志
      ├── manifest.json                                # 内容哈希缓存
      ├── ast/                                         # 缓存 AST JSON
      ├── reports/                                     # Markdown 转换报告
      ├── logs/                                        # 处理日志
      └── generation-checkpoint.json                  # 断点续做检查点
```

## MCP 服务器模式（`--mcp`）

FluxGauss 双引擎均支持通过 `--mcp` 标志以 MCP（Model Context Protocol）服务器模式启动，允许 AI 客户端（Claude Desktop、Cursor 等）直接调用存储过程转换功能。

### 启动方式

```bash
# Python 引擎
pip install mcp
python3 converter/flux_gauss.py --mcp

# Rust 引擎
cargo build -p fluxgauss-mcp
fluxgauss --mcp              # 自动调用 fluxgauss-mcp 二进制
```

### MCP 工具

| 工具 | 功能 | 关键行为 |
|------|------|----------|
| `validate_sql` | 验证 SQL 语法、包一致性、未定义变量 | 返回结构化错误/警告列表，含 `valid` 布尔值 |
| `convert_sql` | 完整转换：验证 → 解析 → 分析 → 生成 Java 项目 | **先自动执行 validate，有错误则中止**；可用 `skip_validation: true` 强制转换 |

### 推荐工作流

```
1. validate_sql → 检查 SQL 语法
2. 修复错误（如有）
3. convert_sql  → 生成 Spring Boot 项目
```

### MCP 客户端配置

```json
{
  "mcpServers": {
    "fluxgauss": {
      "command": "python3",
      "args": ["converter/flux_gauss.py", "--mcp"],
      "env": {
        "OGSQL_BIN": "/path/to/ogsql"
      }
    }
  }
}
```

Rust 引擎配置：

```json
{
  "mcpServers": {
    "fluxgauss": {
      "command": "fluxgauss-mcp"
    }
  }
}
```

### convert_sql 参数

`convert_sql` 接受两种输入方式：

**方式一：直接参数**
```json
{
  "files": ["sql/pkg_order.sql"],
  "output_dir": "./dest",
  "base_package": "com.example.demo"
}
```

**方式二：配置对象**（与 fluxgauss.yaml 相同的 schema）
```json
{
  "config": {
    "output_dir": "./dest",
    "base_package": "com.example.demo",
    "sources": ["sql/pkg_order.sql"],
    "logger": "slf4j"
  }
}
```

**可选参数**：`full` (bool)、`debug` (bool)、`skip_validation` (bool)

---

## MCP 服务器 & REST API（ogsql-parser）

ogsql-parser 提供两种服务模式，可用于构建 SQL 解析微服务：

### MCP 服务（Model Context Protocol）

```bash
# 构建并启动 MCP 服务器
cargo build --release --features mcp -p ogsql-parser --bin ogsql-mcp
./target/release/ogsql-mcp

# MCP 客户端配置示例（如 Claude Desktop）
{
  "mcpServers": {
    "ogsql": {
      "command": "/path/to/ogsql-mcp"
    }
  }
}
```

MCP 服务支持的操作：
- `parse_sql` — 解析 SQL 文本为 JSON AST
- `json_to_sql` — 将 JSON AST 还原为 SQL 文本
- `format_sql` — SQL 格式化
- `validate_sql` — SQL 语法校验

### REST API 服务

```bash
# 构建并启动 REST API 服务器（默认端口 9527）
cargo build --release --features serve -p ogsql-parser --bin ogsql
./target/release/ogsql serve --port 9527

# API 端点
POST /api/parse     — 解析 SQL 为 JSON AST
POST /api/json2sql  — JSON AST 还原为 SQL
POST /api/format    — SQL 格式化
GET  /api/health    — 健康检查
```

## 项目数据

| 指标 | 数值 |
|------|------|
| 版本 | 0.6.27 |
| SQL 演示文件 | 48 个（36 个含存储过程） |
| 自动生成单元测试 | 357+ 个（全部通过） |
| Python 测试套件 | 22 个测试文件 / ~6200 行 |
| 支持的 PL/pgSQL 特性 | 28+ 种语句类型 |
| 支持的内置函数映射 | 110+ 个（Python） / 60+ 个（Rust） |
| 等价性对比轮次 | 6 轮（V1-V6） |
| CI/CD 构建平台 | Linux x86_64 / ARM64, macOS ARM64, Windows x86_64 |

## 文档

- [使用指南](使用指南.md) — 完整的配置说明、CLI 选项、PL/pgSQL 特性支持列表
- [贡献指南](CONTRIBUTING.md) — 如何参与开发、提交代码和测试
- [更新日志](CHANGELOG.md) — 版本变更记录
- [迁移方案对比](docs/migration-comparison.md) — 存储过程迁移到 Java 的各种方案比较
- [设计文档](docs/design-document.md) — 完整的数据模型、算法逻辑和代码生成规则
- [AGENTS.md](AGENTS.md) — AI 辅助开发指南（项目架构和开发规范）

## License

MIT — see [LICENSE](LICENSE)

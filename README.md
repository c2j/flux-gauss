# FluxGauss

将 OpenGauss / PostgreSQL 存储过程（PL/pgSQL）自动转换为 Spring Boot + MyBatis Java 项目。

## 功能亮点

- **AST 驱动的语义转换** — 基于 Rust SQL 解析器（ogsql-parser）生成完整抽象语法树，逐节点转换为 Java 代码
- **双引擎** — Python 引擎（~14800 行，评级 A-）+ Rust 引擎（~15800 行，高性能），共享配置格式
- **增量构建** — SHA-256 内容缓存 + 传递依赖追踪，只重新生成变更部分
- **跨包依赖自动解析** — 自动识别包间调用，生成正确的 Service 注入和 import
- **自动化测试生成** — Mockito 单元测试 + Testcontainers 集成测试
- **50+ 内置 SQL 函数映射** — 字符串、数学、日期、空值处理、类型转换
- **动态 SQL → MyBatis Dynamic XML** — 自动检测条件 SQL 拼接，生成 `<if>`、`<where>`、`<set>` 等动态标签
- **源码溯源注释** — 生成代码中标注原始 SQL 文件名和行号范围
- **转换报告** — 自动输出 Markdown 格式的 SQL-to-Java 映射报告
- **多编码支持** — 通过 `--encoding` 或 YAML 配置指定源码编码（如 GBK），自动处理非 UTF-8 SQL 文件

## 快速开始

### 前置条件

- Python 3.9+（Python 引擎）
- Rust 1.80+（Rust 引擎，可选）
- Java 17+（用于编译验证生成结果）

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
```

### 编译验证

```bash
cd dest
mvn compile        # 编译检查
mvn test           # 运行自动生成的单元测试
```

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

## 双引擎

| 维度 | Python 引擎 | Rust 引擎 |
|------|------------|-----------|
| 入口 | `converter/flux_gauss.py` | `crates/fluxgauss/` |
| 代码量 | ~14800 行 | ~15800 行 |
| 功能完整度 | 完整 | 持续对齐中 |
| 综合评级 | A- | B+ |
| 适用场景 | 通用（100-1000 个 SP） | 大批量（1000-30000 个 SP） |
| 增量构建 | ✅ | ✅ |
| 集成测试生成 | ✅ | ✅ |
| 动态 SQL → MyBatis XML | ✅ | ✅ |
| 多编码支持 | ✅ | ✅ |
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
| 包常量（PACKAGE CONSTANTS） | static final 字段 |

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
  │       └── java/com/example/demo/service/
  │           ├── OrderServiceTest.java                # 单元测试
  │           └── ...
  └── .fluxgauss/                                      # 增量缓存
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
  -v, --version             显示版本信息
  -h, --help                显示帮助信息
```

## 项目数据

| 指标 | 数值 |
|------|------|
| SQL 演示文件 | 51 个（37 个含存储过程） |
| 自动生成单元测试 | 357+ 个（全部通过） |
| 支持的 PL/pgSQL 特性 | 26+ 种语句类型 |
| 支持的内置函数映射 | 50+ 个 |
| 等价性对比轮次 | 6 轮（V1-V6） |

## 文档

- [使用指南](使用指南.md) — 完整的配置说明、CLI 选项、PL/pgSQL 特性支持列表
- [迁移方案对比](docs/migration-comparison.md) — 存储过程迁移到 Java 的各种方案比较
- [AGENTS.md](AGENTS.md) — 项目架构和开发指南

## License

Private / Internal Use

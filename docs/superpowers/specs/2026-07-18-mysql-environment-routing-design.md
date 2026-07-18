# MySQL 环境路由设计

## 目标

后端不再使用 SQLite。开发、生产和测试环境均连接 MySQL，但必须使用各自明确的连接目标，避免测试或本地运行因配置回退而误连生产数据库。

## 环境与连接映射

应用新增 `APP_ENV` 配置，允许值为 `development`、`production` 和 `test`，默认值为 `development`。

| `APP_ENV` | 数据库环境变量 | 用途 |
| --- | --- | --- |
| `development` | `DB_PROD_OUT` | 本地启动，通过外网访问 MySQL |
| `production` | `DATABASE_URL_PRODUCTION` | 生产部署，通过生产连接访问 MySQL |
| `test` | `DATABASE_URL_TEST` | pytest 专用 MySQL，数据库名必须为 `connection_dev` |

每种环境只读取其对应变量。目标变量缺失或为空时，应用立即以清晰的配置错误停止启动，不回退到其他环境的地址。

## URL 与驱动

项目使用 SQLAlchemy 同步引擎和 PyMySQL 驱动。配置层接受现有的 `mysql://` URL，并在创建引擎前规范化为 `mysql+pymysql://`。已经明确指定 SQLAlchemy 驱动的 MySQL URL 保持不变。

`requirements.txt` 增加固定版本的 `PyMySQL`。数据库 URL 中的账号、密码和主机信息继续只保存在未跟踪的 `.env` 或部署平台环境变量中，不写入代码、文档或测试输出。

## 测试隔离

pytest 在导入应用模块之前将 `APP_ENV` 设为 `test`，确保全局 SQLAlchemy 引擎从一开始就绑定 `DATABASE_URL_TEST`。

测试配置采用 fail-closed 策略：

- `DATABASE_URL_TEST` 必须存在。
- URL 必须是 MySQL URL。
- URL 的数据库名必须严格等于 `connection_dev`。
- 任一条件不满足时，测试在连接数据库之前失败。

现有测试会写入持久数据但不会完整清理。本次变更只负责将其与生产库隔离，不扩大范围重写测试数据生命周期。

## 数据库初始化

保留 `Base.metadata.create_all()`，用于创建 MySQL 中尚不存在的表。移除 SQLite 的 `check_same_thread` 参数、SQLite 默认 URL，以及仅为 SQLite 执行的 `ALTER TABLE` 兼容逻辑。

`create_all()` 不负责修改已存在的表结构。本设计假设用户已完成目标 MySQL 数据库的结构调整；后续结构演进应另行引入迁移机制，不在本次范围内。

数据库引擎启用连接存活检查，以降低长连接被 MySQL 服务端关闭后首次请求失败的概率。

## 配置接口

配置模块对外提供解析后的数据库 URL，数据库模块不自行判断运行环境。这样，环境选择、必填校验、测试库名保护和 URL 规范化集中在配置层，SQLAlchemy 初始化只消费已验证的结果。

为避免在错误信息中泄露凭据，配置错误只报告缺失的变量名、非法 URL 类型或数据库名不匹配，不回显完整连接字符串。

## 文档与验证

README 将说明三种环境的启动方式和变量映射，并明确：

- 本地启动默认使用 `DB_PROD_OUT`。
- 生产启动必须设置 `APP_ENV=production`。
- 测试使用 `DATABASE_URL_TEST`，且目标库为 `connection_dev`。
- 项目不再提供 SQLite 回退。

自动化测试覆盖环境到变量的映射、缺失变量失败、MySQL URL 规范化、测试库名保护，以及非 MySQL URL 拒绝。实现完成后运行聚焦配置测试，再运行完整后端测试；完整测试只会在测试库安全校验通过后连接 `connection_dev`。

## 非目标

- 不迁移 SQLite 中的历史数据。
- 不创建或管理 MySQL 用户、权限或数据库实例。
- 不自动修改未跟踪的 `.env` 中的秘密值。
- 不引入 Alembic 或其他数据库迁移框架。
- 不清理测试此前或本轮生成的业务数据。

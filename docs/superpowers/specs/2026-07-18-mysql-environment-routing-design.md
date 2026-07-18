# MySQL 环境路由设计

## 目标

后端不再使用 SQLite。开发和生产环境均连接 MySQL 数据库 `connection`，本地与 pytest 通过外网地址连接，生产部署通过生产地址连接。

## 环境与连接映射

应用使用 `APP_ENV` 配置，允许值为 `development` 和 `production`，默认值为 `development`。项目根目录未跟踪的 `.env` 显式设置 `APP_ENV=development`。

| `APP_ENV` | 数据库环境变量 | 用途 |
| --- | --- | --- |
| `development` | `DB_PROD_OUT` | 本地启动和 pytest，通过外网访问 `connection` |
| `production` | `DATABASE_URL_PRODUCTION` | 生产部署，通过生产连接访问 MySQL |

每种环境只读取其对应变量。目标变量缺失或为空时，应用立即以清晰的配置错误停止启动，不回退到其他环境的地址。

## URL 与驱动

项目使用 SQLAlchemy 同步引擎和 PyMySQL 驱动。配置层接受现有的 `mysql://` URL，并在创建引擎前规范化为 `mysql+pymysql://`。已经明确指定 SQLAlchemy 驱动的 MySQL URL 保持不变。

`requirements.txt` 增加固定版本的 `PyMySQL`。数据库 URL 中的账号、密码和主机信息继续只保存在未跟踪的 `.env` 或部署平台环境变量中，不写入代码、文档或测试输出。

## pytest 数据库行为

pytest 不再覆盖 `APP_ENV`，与本地启动使用同一套 `development` 配置，通过 `DB_PROD_OUT` 直接连接数据库 `connection`。

用户已明确接受以下影响：

- pytest 会向 `connection` 永久写入用户、家庭、学生、任务、提交和批改等测试记录。
- 现有测试不完整清理写入的数据，重复运行会持续保留测试记录。
- 不再使用 `connection_dev`，也不再读取 `DATABASE_URL_TEST`。

本次变更不新增数据清理机制，也不复制或迁移旧 SQLite 数据。

## 数据库初始化

保留 `Base.metadata.create_all()`，用于创建 MySQL 中尚不存在的表。移除 SQLite 的 `check_same_thread` 参数、SQLite 默认 URL，以及仅为 SQLite 执行的 `ALTER TABLE` 兼容逻辑。

`create_all()` 不负责修改已存在的表结构。本设计假设用户已完成目标 MySQL 数据库的结构调整；后续结构演进应另行引入迁移机制，不在本次范围内。

数据库引擎启用连接存活检查，以降低长连接被 MySQL 服务端关闭后首次请求失败的概率。

## 配置接口

配置模块对外提供解析后的数据库 URL，数据库模块不自行判断运行环境。环境选择、必填校验和 URL 规范化集中在配置层，SQLAlchemy 初始化只消费已验证的结果。

为避免在错误信息中泄露凭据，配置错误只报告缺失的变量名或非法 URL 类型，不回显完整连接字符串。

## 文档与验证

README 将说明两种环境以及 pytest 的连接行为，并明确：

- 本地启动默认使用 `DB_PROD_OUT`。
- 生产启动必须设置 `APP_ENV=production`。
- pytest 使用本地的 `development` 配置和 `DB_PROD_OUT`，直接连接 `connection`。
- 项目不再提供 SQLite 回退。

自动化测试覆盖环境到变量的映射、缺失变量失败、MySQL URL 规范化，以及非 MySQL URL 拒绝。实现阶段只运行不打开数据库连接的聚焦配置测试，避免仅为验证配置而额外向 `connection` 写入一轮业务测试数据。

## 非目标

- 不迁移 SQLite 中的历史数据。
- 不创建或管理 MySQL 用户、权限或数据库实例。
- 除新增非秘密配置 `APP_ENV=development` 外，不自动修改未跟踪的 `.env` 中的秘密值。
- 不引入 Alembic 或其他数据库迁移框架。
- 不清理测试此前或本轮生成的业务数据。

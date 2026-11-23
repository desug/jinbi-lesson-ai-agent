# Jinbi Lesson AI Agent

> Jinbi Lesson 是面向课时与教务场景的后端及 AI Agent 演示项目。仓库展示 FastAPI、SQLAlchemy、受限工具调用、会话状态和 BM25 规则检索如何组合成可审查的业务流程。离线 DEMO 使用隔离 SQLite 和虚构记录；它的确定性规划器不是大语言模型。

## 背景

项目源自已上线小程序所覆盖的课时查询、课时流水和教务规则业务。本仓库整理其后端工程与参赛演示能力。上线业务经验不代表此仓库包含小程序前端或生产部署配置；公开前请补充可公开的小程序介绍与截图，并确认相关素材授权。

## 技术栈

Python 3.11+、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、SQLite（隔离 DEMO）/ MySQL（业务后端配置）、OpenAI Python SDK（DeepSeek OpenAI-compatible API）、rank-bm25、pytest/unittest。

## 结构

```text
app/                 FastAPI、模型、路由、服务、规则语料与 DEMO
  core/              配置、数据库、JWT
  models/            SQLAlchemy ORM
  routers/           HTTP 接口
  services/          Agent、查询工具、审批、BM25 RAG
  knowledge/          DEMO 请假/补课规则文本
alembic/             数据库迁移
tests/               自动化测试
docs/                架构、工作流、演示与发布说明
```

## Agent 与安全边界

业务 API 入口为 `app.main:app`。传统管理员 AI 查询由 `app/services/deepseek_planner.py` 解析为受约束的结构化意图，再由 SQLAlchemy 只读查询；模型不接收数据库结果，也不生成 SQL。Agent DEMO 在 `app/services/ai_conversation_chat.py` 管理会话状态、候选身份确认与多轮补充。离线版本使用 `OfflineDemoPlanner` 按固定协议生成预设工具调用，仅用于可重复演示，不产生真实模型推理。

工具定义位于 `app/services/ai_tools.py` 等服务模块。工具调用受服务端白名单、参数校验、管理员鉴权和只读查询限制；离线 DEMO 的写入意图拒绝，不向课时数据执行修改。规则审批仅允许隔离 SQLite DEMO。

## BM25 RAG

DEMO 规则存于隔离数据库的规则模型；检索先按审批状态、生效日期和适用范围筛选，再使用 `rank_bm25.BM25Okapi` 对规则标题、类别、班型和正文评分，并附带规则来源引用。低证据或多个适用范围时返回未命中/人工复核状态。此为词项检索，不是向量数据库或生成式答案保证。详见 [RAG 流程](docs/rag-workflow.md)。

## 离线 DEMO

Python 3.11+ 环境安装依赖并启动：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python app\offline_demo_launcher.py --rules
```

在另一个终端运行 `python app/prepare_offline_demo_rules.py` 初始化虚构规则。浏览器访问 `http://127.0.0.1:18765/`。DEMO 只绑定本机回环地址，启动器清理子进程环境并使用合成 SQLite 数据。试问“查询虚构小明剩余课时”，选择候选后补充“数学”；可问“请假规则是什么”“补课时间如何安排”。写入请求应被拒绝。完整演示见 [docs/demo.md](docs/demo.md)。

## 在线 DeepSeek DEMO（可选）

在线模式不会从 `.env` 读取密钥，而是在本地终端通过隐藏输入提示读取。仅在你明确希望发起真实模型请求时运行：

```powershell
python app\demo_online_launcher.py --rules
```

输入 API Key 后按提示运行；服务仍只监听 `127.0.0.1`。密钥不应写入命令行参数、源码、截图或日志。在线请求会产生外部服务调用，预算/网络失败策略见 [docs/demo.md](docs/demo.md)。本整理过程没有调用 DeepSeek。

## 业务后端

FastAPI 应用为 `app.main:app`，开发启动：

```bash
uvicorn app.main:app --reload
```

此入口读取数据库配置，默认面向 MySQL 业务环境；本地离线演示请使用独立启动器，不要把业务应用接入生产数据库做演示。`.env.example` 仅提供占位值。原业务 seed 保留在本地且不公开；公开的 `app/scripts/seed_example.py` 仅向新建 SQLite 文件写入虚构数据，并生成随机管理员密码。

## 测试

```powershell
$env:APP_ENV='test'
$env:PYTHON_DOTENV_DISABLED='1'
$env:DEEPSEEK_API_KEY=''
$env:JWT_SECRET_KEY='isolated-d2-r1-test-only'
$env:PYTHONPATH='tests'
$env:MYSQL_HOST='127.0.0.1'
$env:MYSQL_USER='offline_unused'
$env:MYSQL_PASSWORD='offline_unused'
$env:MYSQL_DB='offline_unused'
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_seed_example.py tests/test_d2_r1_demo.py tests/test_d3_r2_r2_rules.py tests/test_d3_r2_r3_retrieval.py tests/test_demo_rules_http.py tests/test_ai_tools.py tests/test_rule_boundary.py
python tests/run_release_http_smoke.py
```

测试也包含 `unittest`。本地发布回归于 2026-11-23 运行：**70 passed，19 subtests passed，15 条 Pydantic 弃用警告**。独立进程 HTTP 烟雾检查也已通过，覆盖本机健康检查、登录、多轮课时查询、虚构规则引用、普通账号写入拒绝和会话隔离。GitHub Actions 的 Linux runner 尚未实际运行。测试不访问真实 DeepSeek、生产 MySQL 或真实学员资料。

## 演示素材

- 已上线小程序截图：`docs/media/mini-program/`（待补充）
- AI 助手首页：`docs/media/agent-home/`（待补充）
- 多轮课时查询：`docs/media/multiturn-hours/`（待补充）
- RAG 规则问答：`docs/media/rag-answer/`（待补充）
- 工具调用流程图、技术架构图：见 [架构文档](docs/architecture.md) 与 [Agent 流程](docs/agent-workflow.md)
- 项目演示视频：待补充链接

## 已知限制与规划

- 离线规划器为确定性测试/演示替身，不能代表模型效果。
- BM25 依赖词项切分与规则质量，复杂语义和冲突规则需人工复核。
- 在线模式需要用户自行提供有效 DeepSeek Key；本仓库不提供在线服务或生产密钥。
- 生产业务端的 CORS、密钥轮换、审计与部署策略需按实际环境配置。
- 非测试环境启动时要求配置至少 32 字符的独立随机 `JWT_SECRET_KEY`，并拒绝已知占位值。公开发布前应确认线上使用独立安全密钥；若曾使用开发默认值，应由部署负责人轮换并使旧 Token 失效。
- 后续可完善脱敏的演示数据、端到端发布流程、评测集说明和截图/视频素材。

## 许可证

本项目采用 [MIT License](LICENSE)，版权归 desug 所有。

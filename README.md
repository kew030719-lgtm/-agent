# 企业内部员工助手 Agent

FastAPI + Vue 3 网页版员工助手，同时保留命令行入口：登录后按角色使用工具；DeepSeek 负责问题路由和基于资料的回答；LangGraph 组织流程；SQLite 保存账号、文档、业务记录和人工待办。知识向量由本地中文模型 `BAAI/bge-small-zh-v1.5` 生成，存入 Docker 中的 Milvus Standalone。

## 网页版启动

本机已安装依赖、构建前端并启动依赖服务后，在项目根目录执行：

```bash
.venv/bin/python server.py
```

打开 **http://127.0.0.1:8000**，使用原来 CLI 的员工账号和密码登录。接口文档：**http://127.0.0.1:8000/docs**。网页与 CLI 共用原有 SQLite 账号、知识库、工单等数据。首位管理员在空数据库中通过 `main.py init-admin` 创建；随后管理员可在网页「用户管理」创建员工或其他管理员账号，也可继续使用 `main.py add-user`。不开放员工自助注册。

### 前后端分别开发

终端一（项目根目录）：

```bash
.venv/bin/python server.py
```

终端二：

```bash
cd frontend
npm ci
npm run dev
```

访问 **http://127.0.0.1:5173**。Vite 将 `/api` 请求代理到后端。正式构建用 `npm run build`，随后重启 `server.py`，即可由 FastAPI 同时提供前端页面与 API。新机器先安装 Python 依赖 `pip install -r requirements.txt`；前端使用 Node.js 20.19+ 或 22.12+。

### 网页功能

- 登录、退出、当前账号和权限展示。凭证由 HttpOnly Cookie 保存；后端每次从数据库核对账号启用状态与角色，前端不能传入角色提升权限。
- 多会话聊天、历史记录、IT 报修确认、知识追问、知识回答来源和有用/无用反馈。网页历史会保存问题和回答正文；对话只能由所属账号读取。
- 可见知识文件列表。管理员上传文件后由 Celery 后台解析、切片和写入 Milvus；「导入任务」显示阶段、切片数和失败原因，可重试失败任务。文档全部建库完成后才进入检索，权限规则继续生效。
- 有权限的管理员查看并处理人工待办、查看知识问答统计和员工反馈。
- 管理员在「用户管理」查看账号列表，创建账号、选择角色并设置至少 8 位的初始密码；角色选项由后端返回，普通员工不能访问这些接口。

### Web 目录

```text
server.py                      # 读取 .env 并启动 Uvicorn
app/
  agent/service.py             # Web 调用的 Agent 服务，复用 graph 和 tools
  api/
    app.py                     # FastAPI 工厂、生命周期、异常处理、静态页面
    dependencies.py            # 请求数据库连接、登录身份、会话归属检查
    database.py                # 登录会话、聊天历史的表结构
    schemas.py                 # Pydantic 请求与响应模型
    auth_routes.py             # 登录、退出、当前账号
    chat_routes.py             # 对话、历史、反馈、请求编号去重
    knowledge_routes.py        # 资料列表、上传、补建索引、删除
    ingestion_routes.py        # 后台导入任务、进度、失败重试
    admin_routes.py            # 人工待办、反馈、统计
    user_routes.py             # 管理员账号列表、角色选项、创建用户
frontend/
  src/
    App.vue                    # 应用布局、登录状态与页面切换
    api.ts                     # fetch 封装和错误处理
    chat.ts                    # 会话状态、发送与恢复
    types.ts                   # 前后端数据类型
    components/                # 登录、聊天、知识库、待办、统计页面
    style.css                  # 桌面和窄屏样式
  vite.config.ts               # 开发服务器与 API 代理
```

### API 路径

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST / GET / POST | `/api/auth/login` / `/api/auth/me` / `/api/auth/logout` | 登录、当前账号、退出 |
| GET / POST | `/api/users` | 管理员查看用户列表、创建账号 |
| GET | `/api/users/roles` | 管理员获取可分配角色与权限 |
| GET / POST | `/api/conversations` | 查看自己的会话、创建会话 |
| GET / POST | `/api/conversations/{id}/messages` | 历史、发送问题 |
| POST | `/api/messages/{id}/feedback` | 评价知识回答 |
| GET / POST | `/api/knowledge` | 可见文件列表、管理员上传 |
| POST / DELETE | `/api/knowledge/{id}/reindex` / `/api/knowledge/{id}` | 补建缺失索引、删除 |
| GET | `/api/knowledge-jobs` / `/api/knowledge-jobs/{id}` | 本人创建的导入任务与进度 |
| POST | `/api/knowledge-jobs/{id}/retry` | 重试失败或失去执行租约的任务 |
| GET / POST | `/api/handoffs` / `/api/handoffs/{id}/resolve` | 待办、标记处理完成 |
| GET | `/api/knowledge-feedback` / `/api/knowledge-stats` | 管理员反馈与统计 |

写请求需带 `X-Requested-With: employee-assistant`；有 Origin 时还必须在 `API_ALLOWED_ORIGINS` 中。登录示例请求体为 `{"username":"员工账号","password":"账号密码"}`。发送问题需提供 `question` 与 UUID 格式的 `request_id`；重复提交同一已完成请求会返回原答案，避免再次执行工具。前端会自动携带 Cookie 和上述请求头。在 Swagger 文档点击 Authorize，在 RequestMarker 中填写 `employee-assistant`，随后执行登录接口即可继续调试。

### 管理员日常操作

**创建账号**：登录后打开「用户管理」→「创建账号」→ 填写账号名、选择角色、输入并确认初始密码 → 提交。成功后用户即可用该账号登录。重复账号会被拒绝，页面不会显示已存密码。支持普通员工、人事、IT、财务、管理员五种角色。

**处理人工待办**：管理员打开「人工协助 → 处理队列」，先完成必要的实际处理，再点击「填写回复」，填写员工可见的结果，确认「回复并结束事项」。例如余额缺失应先用 `main.py set-leave` 录入，再回复核对结果。员工通过「人工协助 → 我的事项」查看状态、回复、处理人和时间，并可标记已读。已处理事项可通过状态筛选查看；旧记录没有回复内容时会明确提示。

### Web 配置与边界

根目录 `.env` 新增 `API_HOST`、`API_PORT`、`API_SESSION_TTL_HOURS`、`API_COOKIE_SECURE`、`API_UPLOAD_MAX_MB`、`API_ALLOWED_ORIGINS`、`FRONTEND_HOST`、`FRONTEND_PORT`、`FRONTEND_API_TARGET`。所有默认值和说明见 `.env.example`。前端只使用服务器地址配置，DeepSeek Key 和 Milvus 凭证不会注入前端构建。`API_DATABASE_PATH` 指定业务 SQLite，其所在目录用于保存知识源文件和图片；`INGESTION_MODEL_ROOT` 可指定共享的本地模型目录，留空时使用业务数据库所在目录。

当前默认仅监听本机，并用一个 Uvicorn 进程运行。网页对话上下文已写入 SQLite，重启后历史和正常完成的报修确认状态仍可继续；CLI 原本的内存检查点机制保持不变。若服务在执行操作期间中断，该请求保留为 processing，不会自动重放；请先核对业务记录，再新建对话继续。这里没有实现跨 SQLite 与 Milvus 的分布式事务，也没有承诺进程崩溃场景下所有业务操作精确执行一次。

网页问答会等待完整答案返回，当前没有流式输出。文件上传请求只保存原文件和创建任务，解析、OCR 和向量入库在独立 Celery Worker 中执行。当前使用 SQLite，Worker 默认并发为 1，适合本地学习与小规模使用。公开部署前应通过 HTTPS 并设置 `API_COOKIE_SECURE=true`，按实际站点配置允许来源。

### 后台知识建库（Redis + Celery）

后台建库需要可连接的 Redis 和 Milvus 服务。先在项目根目录安装应用依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Redis 的安装与运行由使用环境管理，通过下方 `CELERY_BROKER_URL` 配置连接地址。

在 `.env` 中配置以下项目。API、CLI 和 Worker 必须使用同一个数据库与对应的 Milvus 集合；不同业务数据库应使用不同集合，并分配不同的 `CELERY_QUEUE`，避免 Worker 消费其他环境的任务。

```dotenv
API_DATABASE_PATH=data/employee_assistant.db
MILVUS_URI=http://127.0.0.1:19530
MILVUS_COLLECTION=employee_knowledge_bge_small_zh_v15
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_QUEUE=knowledge
INGESTION_MODEL_ROOT=data
OCR_TESSDATA_PATH=data/knowledge/tessdata
INGESTION_TIME_LIMIT_SECONDS=3600
```

上述是默认数据库示例。已有模拟库时应保留 `.env` 中的模拟库路径和集合名。Redis 地址或端口改变时同步修改 `CELERY_BROKER_URL`。模型目录需已准备 `BAAI/bge-small-zh-v1.5`，OCR 目录需包含中文和英文语言包。修改配置后重启 API 和 Worker。

另开终端，在项目根目录启动 Worker，并保持终端运行：

```bash
.venv/bin/celery -A app.ingestion.celery_app:celery_app worker --pool=prefork --concurrency=1 --loglevel=INFO
```

网页上传后会返回任务编号，无需等待建库完成。「导入任务」依次展示排队、解析/OCR、切片、向量化、入库和完成；解析阶段无法提前知道总切片数，向量化后显示实际计数。支持 MD、DOCX、Excel、原生及扫描 PDF。

- 文件先持久化，再投递任务。队列暂时不可用时保留文档和失败任务，修复 Redis 后可重试。
- 任务成功前，文档不会出现在知识检索、产品目录和正文/图片预览中；已有文档补建索引期间也暂时隐藏。
- 重试复用已完成的解析切片与向量主键；重复上传请求使用同一个 UUID `request_key` 返回原任务，参数不同则拒绝。上传以表单传该字段，补建索引以 JSON 传该字段。
- 任务只对文档创建者开放，后端会重新核对账号启用状态和管理权限；执行中定期续租，旧 Worker 不能覆盖新尝试的完成状态。
- 活跃任务期间不能删除该文档或并发补建索引。处理失败后可检查原因，再重试或删除。
- Redis 队列允许重复投递；业务状态以 SQLite 为准。当前没有跨 SQLite/Milvus 的分布式事务。普通处理失败由管理员手动重试；Worker 意外终止由 Celery 重新投递，旧租约过期后才能接管。长时间排队也可手动重试投递。

核心代码位于 `app/ingestion/`：`jobs.py` 管理持久化状态与租约，`worker.py` 执行建库，`celery_app.py` 配置队列，`tasks.py` 接收任务编号。CLI 的知识导入仍为同步方式；`main.py reindex-knowledge` 会为当前账号负责的文档提交后台补建任务，进度在网页查看。

## 已实现

- “你是谁”“现在有什么权限”等常用问法由本地节点直接回答；权限来自登录账号，不能通过对话修改。
- 模型/API 或检索服务异常提示重试，不创建人工待办；知识缺少依据或路由不明确时先说明或澄清，用户明确要求转人工后创建待办；请假余额未录入也先提示，员工确认后才转人工。
- 账号登录和角色权限：`employee`、`hr`、`it`、`finance`、`admin`。
- IT 工单、本人请假余额、报销申请、会议室查询；低置信度先澄清，知识不足先说明，支持确认转人工。
- 管理员导入 PDF、Markdown、DOCX、XLSX、XLSM、XLS。解析时保留页码、标题、行号、工作表和表格行等位置；再按结构切片、生成向量。
- 扫描 PDF 通过 OCR 提取文字。PDF 图片另存于 `data/knowledge/images/`，其相对地址、页码和区域写入 `knowledge_assets`；读取图片路径需通过文档可见范围检查。
- 原问题与一条经过校验的改写分别走 Milvus 向量检索和 SQLite FTS5/BM25 关键词检索，用 RRF 融合各路排名，默认保留36条重排候选。明确点名文档时按原问题限定目标；每一路按角色过滤并由 SQLite 复核权限。网页追问由模型结合上一主题与当前有权阅读的资料标题补全后路由。DeepSeek 回答必须提供资料编号及有效原文，最终显示来源位置。
- 可选的本地 Cross-Encoder 对候选切片重排；知识 Prompt 限制为最多 4 条资料、总长 4000 字符。模型必须引用资料中的连续原文，回答中的数字还要出现在引文中。
- 员工可主动评价上一条知识回答；管理员可查看已提交的评价和知识问答运行统计。

## 项目结构

```text
employee_assistant/
├── app/
│   ├── auth.py                 # 登录、用户和角色权限
│   ├── config.py               # 项目 .env 加载和模型配置
│   ├── errors.py               # 服务故障类型
│   ├── agent/
│   │   ├── graph.py            # LangGraph 对话流程
│   │   ├── profile.py          # 助手身份和登录账号权限
│   │   └── router.py           # DeepSeek 意图路由
│   ├── knowledge/
│   │   ├── blocks.py           # 解析块与来源位置
│   │   ├── chunks.py           # 结构切片
│   │   ├── documents.py        # 文档/切片/图片记录和权限查询
│   │   ├── embeddings.py       # 本地向量模型和索引迁移
│   │   ├── lifecycle.py        # 知识文件替换与删除
│   │   ├── keyword.py          # SQLite FTS5 中文关键词检索
│   │   ├── reranker.py         # 本地 Cross-Encoder 重排
│   │   ├── feedback.py         # 知识回答评价
│   │   ├── usage.py            # 知识问答结果和耗时统计
│   │   ├── query.py            # 短追问的会话内扩展
│   │   ├── vector_store.py     # Milvus 连接、写入和向量搜索
│   │   ├── retrieval.py        # Milvus 检索及 SQLite 权限复核
│   │   ├── answer.py           # 带来源的知识回答
│   │   ├── images.py           # PDF 图片保存
│   │   └── parsers/            # PDF、Markdown、Word、Excel 解析
│   └── tools/                  # IT、请假、报销、会议室、人工待办
├── scripts/prepare_ocr.py      # 下载并校验 OCR 语言包
├── scripts/eval_rag.py         # 临时合成数据的离线检索评估
├── deploy/milvus/compose.yaml  # 本机 Docker Standalone 部署
├── data/                       # 本地数据库、模型、知识源文件和图片（忽略提交）
├── main.py                     # 命令行入口
└── requirements.txt
```

## 安装

在项目根目录运行（需要 Python 3.12）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/prepare_ocr.py
.venv/bin/python main.py prepare-model
.venv/bin/python main.py prepare-reranker
docker compose -f deploy/milvus/compose.yaml up -d
docker compose -f deploy/milvus/compose.yaml ps
```

Ubuntu 若提示缺少 `ensurepip`，先安装系统包 `python3.12-venv`，再重新创建虚拟环境。`prepare_ocr.py` 从 Tesseract 官方语言仓库下载中英文数据并校验 SHA-256；`prepare-model` 下载本地向量模型；`prepare-reranker` 下载约 0.57 GB 的 `BAAI/bge-reranker-v2-m3-int8`。未准备重排模型时，问答保留 RRF 融合顺序。Docker 首次启动需拉取 Milvus、etcd、MinIO 镜像。日常向量生成在本机运行，Milvus 服务监听 `127.0.0.1:19530`；路由、查询改写和知识回答使用 DeepSeek API。

若执行 `docker compose` 时提示无法访问 `/var/run/docker.sock`，请在确认 Compose 文件内容后用 `sudo docker compose -f deploy/milvus/compose.yaml up -d` 启动；此命令会要求你输入本机 sudo 密码。启动后可用 `sudo docker compose -f deploy/milvus/compose.yaml ps` 检查容器状态。

Compose 数据放在 `data/milvus/`，执行 `docker compose -f deploy/milvus/compose.yaml down` 只停止容器，不删除数据。项目默认通过 `MILVUS_URI=http://127.0.0.1:19530` 连接；如需连接另一台 Milvus，可设置 `MILVUS_URI`、`MILVUS_TOKEN` 和可选的 `MILVUS_COLLECTION`。本机 Compose 只开放回环地址，默认未启用 Milvus 身份认证；不要把端口直接暴露到公网。

## 首次使用

```bash
.venv/bin/python main.py init-admin
.venv/bin/python main.py add-user
.venv/bin/python main.py add-knowledge
.venv/bin/python main.py run
```

`add-knowledge` 要求管理员登录，然后输入文件路径和可见范围 `general`、`hr`、`it` 或 `finance`。文件和元数据导入成功后会立即建立 Milvus 索引。`update-knowledge` 输入旧文档编号和新版文件路径：新版沿用旧版权限范围，先导入并索引成功，再移除旧版；新版会得到新的文档编号。`delete-knowledge` 按文档编号删除 SQLite 记录、Milvus 向量及保存的源文件和图片。旧版本已存于 SQLite 的向量可通过 `.venv/bin/python main.py reindex-knowledge` 迁入 Milvus；此命令可重复执行，只补建缺失或范围不一致的向量。若提示切片过长，请整理源文件并重新导入。`run` 会要求登录；设置 `DEEPSEEK_API_KEY` 环境变量，或按提示在终端输入。程序自动读取项目根目录 `.env`，终端已有的环境变量优先；修改配置后需重启程序。

| 命令 | 作用 |
| --- | --- |
| `init-admin` | 创建首位管理员 |
| `add-user` | 管理员创建账号 |
| `add-knowledge` | 管理员导入知识文件并建立索引 |
| `update-knowledge` | 管理员替换已有知识文件并同步索引 |
| `delete-knowledge` | 管理员删除知识文件及其索引 |
| `prepare-model` | 下载本地中文向量模型 |
| `reindex-knowledge` | 管理员迁移旧向量并补建 Milvus 索引 |
| `prepare-reranker` | 下载并验证本地重排模型 |
| `knowledge-feedback` | 管理员查看员工主动提交的知识回答评价 |
| `knowledge-stats` | 管理员查看知识问答成功/转人工次数及耗时 |
| `set-leave` | HR 或管理员维护请假余额 |
| `add-room`、`add-booking` | 管理员录入会议室及预订 |
| `run` | 启动员工对话 |
| `handoffs`、`resolve-handoff` | 管理员查看或处理人工待办 |

## 环境配置

配置项见 `.env.example`；已有 `.env` 时只补缺失项，保留 API 密钥。

| 配置项 | 默认值 / 说明 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥，未配置时 CLI 提示输入 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash`，路由和知识回答共用 |
| `DEEPSEEK_TIMEOUT_SECONDS` | `30`，单次模型请求超时秒数 |
| `DEEPSEEK_MAX_RETRIES` | `1`，SDK 自动重试次数 |
| `MILVUS_URI` | `http://127.0.0.1:19530`，包含主机和端口 |
| `MILVUS_COLLECTION` | `employee_knowledge_bge_small_zh_v15` |
| `MILVUS_TOKEN` | 默认空；启用认证时填写 `用户名:密码` |
| `MILVUS_TIMEOUT_SECONDS` | `10`，Milvus 请求超时秒数 |
| `APP_TIMEZONE` | `Asia/Shanghai`，自然语言时间与当前会议室查询的时区 |

本机 Python 连接 Docker Milvus 时使用宿主机映射端口。若 Python 也在 Docker 中，应把地址改为同一 Docker 网络中的 Milvus 服务名。`.env` 只配置客户端连接，不会更改 Docker 的端口映射；`9091` 是健康检查端口，客户端使用 `19530`。

## 对话示例

```text
你是谁？
现在有什么权限？
公司的年假制度是什么？
我的请假余额
报修：电脑无法联网
报销：125.50；事由：客户拜访交通费
查会议室
帮我看看现在哪间会议室有空
八个人
用一个小时
那改成明天下午三点
反馈：有用
```

会议室操作由 `app/agent/meeting_dialogue.py` 将本轮自然语言转为条件变化，后端合并已有条件并调用本地预订工具。人数、时长可在同一会话补充；只给开始时间时会追问时长或结束时间。“现在空闲”只表示当前时刻的预订状态，不保证后续整个时段可用，也不代表现场一定无人。原有固定格式继续兼容。

所有员工角色均有 `meeting:book`，可在对话中为当前登录账号预订；只有管理员有 `meeting:write`，用于维护房间及人工录入预订。可说“帮我订竹影会议室，明天下午三点到四点，8个人”。系统先展示房间、日期、时间和预订人，确认后才写库；修改条件后必须重新确认。提交时检查过去时段、房间状态、容量及预订冲突，并复用同一员工的相同预订，防止重复写入。取消或改签已成功创建的预订暂需管理员处理。

网页把会议室条件存入当前会话的 `meeting_context`，启动时为旧数据库自动补充此字段。切换话题或账号角色变化后不继承条件，不同会话独立；CLI 会话条件由 LangGraph 内存检查点保存。报销等其他工具目前仍保留原有交互，尚未全部改成自然语言参数提取。

`反馈：有用` 或 `反馈：无用` 只评价紧接着的上一条成功知识回答，不会进入 Agent 路由。提交时才把该次问题、回答和评价写入本地 SQLite；管理员可通过 `knowledge-feedback` 查看。知识问答的运行统计会记录登录账号、成功/转人工结果及耗时，不记录问题和回答正文；管理员用 `knowledge-stats` 查看汇总。离线检索评估可运行 `.venv/bin/python -m scripts.eval_rag`，它在临时目录生成 Markdown、DOCX、XLSX、PDF 和 Milvus Lite 数据，不调用 DeepSeek，也不修改现有知识库。

## 验证命令

在项目根目录执行：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.eval_rag
.venv/bin/python -m scripts.eval_live
```

`eval_rag` 使用真实本地 Embedding、Reranker 和临时 Milvus Lite，检查检索排名与权限过滤，不调用 DeepSeek。保留旧0.55分数线仅作诊断，不作为当前问答的硬门槛。

`eval_live` 读取 `.env` 中的密钥，调用真实 DeepSeek，会产生 API 用量。它模拟 CLI 的登录身份和键盘输入，使用真实 LangGraph、检索和生成链路；用合成年假、VPN、会议室和报销文件建立临时知识库，不写入现有 SQLite 或 Docker Milvus。覆盖知识引用、多轮追问、角色隔离、无依据说明及身份权限查询；登录密码验证不在这个脚本的覆盖范围内。服务故障通过单元测试注入验证。两个评估脚本均需要已下载的本地模型及 Milvus Lite 运行环境。

服务异常不计入知识回答成功/转人工统计。少量合成案例通过不代表已完成企业真实语料的阈值校准。

## 当前50人公司模拟环境

旧星河百人模拟库、文件及独立向量集合已按要求删除。当前为维科科技（完全虚构）：管理员2人、人事3人、IT支持5人、财务4人、普通员工36人，共50人。账号格式为 `wk_角色_两位序号`，统一初始密码 `123456789`。

当前目录：`data/company_simulation/wk50_20261012_170752`，包含 `ACCOUNTS.md` 完整账号及年假余额清单、`accounts.json`、`company.db`、`manifest.json`、`sources/` 原始文档和导入后的知识文件。知识库为公司制度、年假制度、报销制度、请假制度，以及10份不同用途的产品资料；全部为 `general`，每位员工均可查看。另有4间会议室，工单、报销、待办和历史对话从空数据开始。

```bash
# 启动当前模拟环境（网页与API同一端口）。
.venv/bin/python -m scripts.company_simulation serve --root data/company_simulation/wk50_20261012_170752 --port 8002

# 如需重新生成另一套50人数据；不会自动删除现有数据。
.venv/bin/python -m scripts.seed_company50
```

打开 `http://127.0.0.1:8002`，管理员 `wk_admin_01`，普通员工 `wk_employee_09`。模型缓存复用现有文件，业务数据库、上传文档及Milvus集合独立。默认8000端口服务仍使用原业务库。模拟资料是供查询和流程演示的虚构制度，不代表系统已集成资料中描述的全部产品操作。

`scripts/company_simulation.py seed/test` 保留旧百人压测脚本，不能用其中硬编码的百人测试案例验收当前50人数据。历史对话接口继续支持 `limit`（1–100）与 `offset`。

## 组织架构与账号管理

账号档案分为姓名、部门、职级、直属上级和权限角色。`role` 继续表示系统权限配置，`job_level` 表示组织职级；两者独立。研发部经理可以使用基础权限，董事长也不会因职级自动获得系统管理权。

- 默认部门：研发、销售、产品、运营、人事、IT支持、财务、管理部；管理员可在前端新增部门。
- 职级：普通员工、组长、部门经理、总经理、董事长。允许跨级汇报；组长和部门经理的下属必须在同部门，总经理和董事长可以跨部门管理。可暂不分配上级。
- 管理员在「用户管理」创建或编辑档案，列表支持部门筛选、账号/姓名/职级搜索。编辑不修改用户名或密码。
- 后端校验自我汇报、上级职级、无效或停用上级；调整部门/职级时也检查现有下属，不合法的修改整体回滚。
- 历史数据库启动时自动补充组织字段，旧账号默认未分配部门/上级，普通员工职级；原密码及权限角色保留。当前50人模拟公司已补齐组织档案，详情见模拟目录内的 `ORGANIZATION.md`。
- 模拟公司共34名普通员工、7名组长、7名部门经理、1名总经理、1名董事长。管理部放置两位高管，其他7个部门各设经理和组长。`wk_admin_01` 为董事长、`wk_admin_02` 为总经理；两者保留原有管理员权限。
- 组织档案驱动报销和年假逐级审批；知识可见性由文档访问策略控制。可以问助手“我的部门”“我的职级”“我的上级是谁”。

接口：`GET /api/users/organization` 获取部门和职级；`POST /api/users/departments` 新增部门；`POST /api/users` 创建账号；`POST /api/users/{username}/profile` 完整更新姓名、部门、职级、上级及权限角色。上述接口均要求用户管理权限。`/api/auth/me` 返回本人组织档案。

已有且尚未分配组织档案的 wk50 模拟库可运行下列一次性迁移（先自动备份数据库，已有组织信息时拒绝覆盖）：

```bash
.venv/bin/python -m scripts.organize_company50 --root data/company_simulation/wk50_20261012_170752
```

## 文档级知识权限

进入「知识库」导入文件，或进入「我负责的资料」点击文件的「权限」，即可设置：

- **全体员工**：所有启用账号。
- **自定义授权**：每个授权组可选部门、最低职级、指定员工。同组条件为 AND，多组为 OR；同一个部门/员工选择框内的多个选项为 OR。
- **沿用原权限角色范围**：继续按 `general/hr/it/finance` 和原角色权限判断，供旧资料兼容。原文档不会自动变为新部门规则。

例如：只允许总经理和董事长阅读，添加一个授权组，仅选择最低职级“总经理”；仅研发部阅读，只选择研发部；仅某位员工阅读，只选择该员工。部门与最低职级同时选择时，必须同时符合。高管和系统管理员均不会自动绕过自定义规则。

新前端导入默认自定义授权、没有授权组，正文无人可读。空授权组不允许保存；显式零组表示全部拒绝，文件负责人仍可管理。旧 CLI 和兼容调用若只传 `scope`，继续使用显式选择的原角色范围。

当前文档负责人是上传者（`created_by`），维护文档、重建索引和修改权限均需要本人且具有 `kb:write`。其他管理员不能给自己修改该文档的授权。“我可阅读的资料”只显示可读文件；“我负责的资料”用于管理本人上传的文件，允许显示不可读文件的基本信息。负责人身份不隐含正文阅读权。本版暂未提供负责人交接功能。

权限规则位于 SQLite `knowledge_documents.access_policy`，修改记录写入 `knowledge_access_audit`。文档的文本切片、OCR、图片和原文件统一继承文档权限。向量和 BM25 检索预先按可读文档 ID 过滤，取回正文后再次校验；规则改变不需要重新生成向量。无权访问的预览、原文件或图片均返回 404。

每次权限判断读取当前账号的部门、职级、角色和启用状态，不使用模型或前端提供的身份字段。新知识回答记录参与生成的文档 ID；历史展示、重复请求和反馈会重新校验。撤权时清除会话追问上下文；旧回答/反馈未记录可验证来源时，页面隐藏正文并提示重新提问，原数据库记录保留。已传到浏览器、模型服务或下载到本地的内容无法追回。

接口：`GET /api/knowledge/access-options` 获取授权选项；`GET/POST /api/knowledge/{id}/access` 读取/更新规则；`GET /api/knowledge/{id}/access-audit` 查看授权记录（负责人）；`GET /api/knowledge/{id}/preview`、`download` 和 `/api/knowledge/assets/{asset_id}` 为鉴权后的资料访问入口。上传的 multipart `access_policy` 字段为规则 JSON。

应用权限的边界是应用访问；拥有服务器/数据库权限的运维人员仍可读取存储。账号管理员目前可以调整组织档案，因此组织任免也属于可信管理操作，尚未实现双人审批。

## 查询改写和召回评估

问答链路：原问题 →（追问补全）→ 原问题加一条改写 → 各自向量/BM25检索 → RRF去重融合 → 默认36候选重排 → 必要时补一个同章节或同页的相邻片段 → 最多4段、4000字符证据 → 回答与引用校验。

- 原问题始终参与检索，生成回答也始终使用原问题。改写不作为新的权限或文档范围来源。
- 改写用一个额外模型请求，8秒超时且不自动重试。编号、版本、数字、日期或检测到的否定/范围词发生变化时丢弃改写；非法输出、超时也回退原文。这些规则无法形式化保证所有语义完全不变。
- RRF使用各路排名的倒数之和，常数为60；保留向量分数仅供诊断，不用0.55在重排之前淘汰关键词命中。模型判断证据不足时说明缺失，用户确认才转人工；引文和数字校验继续执行。
- 短片段或冒号结尾的片段可补同章节/同页的前后邻居，最多一条。邻居再次经过权限检查，独立标注来源，共用证据长度预算。

配置已放入 `.env` / `.env.example`：`RAG_QUERY_REWRITE_ENABLED=true`、`RAG_CANDIDATE_LIMIT=36`（4–100）、`RAG_NEIGHBOR_CONTEXT_ENABLED=true`。

本地重排使用 `RAG_RERANK_BATCH_SIZE=1`（1–64）。批次大小只控制每次模型推理处理多少片段，全部候选仍参与重排；按长度分组后会恢复分数与原片段的对应关系。不同 CPU 上最佳批次可能不同，量化模型的分数也可能随批次略变。性能比较需要在同一份语料、问题与候选集上运行，并分别记录检索、重排和回答生成的耗时。

旧星河语料的专项评估脚本已退出版本管理。重新评估必须使用与当前文档对应的问题和金标，不能沿用旧报告声称新语料效果。

## 当前边界

- 旧版 `.doc` 需先转换成 `.docx`；DOCX 内嵌图片、Excel 图表或内嵌图片尚未提取。扫描 PDF 的图片会保存，只有 OCR 得到的文字会进入向量检索；纯图片仍可按权限取得图片文件路径，但不会自动生成图像语义回答。
- 单个表格行保持完整；若长度超过模型上限，索引会报错并保留已导入文档，供整理后重新导入。关键词索引使用 SQLite FTS5 三字切分，两字中文查询主要依赖向量检索或改写。引文和数字校验不能证明回答的每句话都被资料支持；召回变多也不等于答案正确。
- 网页保存最近知识主题、原始问题、来源文档 ID 和待转人工问题，由模型补全隐含追问；不保存无限历史摘要，多个产品的模糊指代会要求澄清。CLI 仍使用原有短追问规则。旧 SQLite `embedding` 字段暂时保留供迁移，新向量只写入 Milvus。
- IT 工单、报销和会议室数据仅在本地 SQLite；人工兜底支持本地待办、处理回复和未读角标，没有企业系统对接或邮件/即时通信推送。CLI 的 LangGraph 检查点在内存中，重启后 CLI 对话状态不恢复；网页版通过 SQLite 保存会话上下文。

### 网页产品目录与知识追问

- `app/agent/dialogue.py`：一次 DeepSeek 调用完成主题补全、路由和查询类型判断；工具参数仍由后端单独校验。资料撤权后清除关联上下文。
- `app/knowledge/catalog.py`：以“公司 + 产品编号”统计可见产品，多个文档或版本只算一个产品。目录继承文档 ACL；不把 Top-K 切片数量当作产品总数，也不承诺可见数量等于公司总数。
- 上传产品资料时勾选“产品资料”并填写公司、编号、产品名；现有资料可由上传者在“我负责的资料”点击“产品登记”。目录维护接口为 `GET/POST /api/knowledge/{id}/product`；普通文档仍可单独做 RAG。替换文档保留登记，删除文档同步移除登记。
- wk50 现有演示数据可用 `.venv/bin/python -m scripts.register_company50_products --root data/company_simulation/wk50_20261012_170752` 补登记；脚本先备份 SQLite，仅识别这批模拟产品，重复执行不覆盖已登记信息。
- 资料只覆盖部分问题时回答已知内容并指出缺失；“负责部门”不能推断为“交付人”。无依据、低置信度与模糊指代不自动建人工待办。可点击或输入“转人工”，也可输入“取消转人工”。直接输入 `转人工：具体问题` 可明确发起待办。

## scripts 目录与发布范围

| 脚本 | 用途 |
| --- | --- |
| `prepare_ocr.py` | 准备并校验 Tesseract 中英文语言包 |
| `eval_rag.py` | 用临时合成文档评估解析、检索和重排 |
| `eval_live.py` | 用真实模型评估 CLI 问答链路，会消耗 API 用量 |
| `company_simulation.py` | 模拟环境的公共工具与启动入口；其中 seed/test 面向旧百人语料 |
| `seed_company50.py` | 生成当前 50 人模拟公司、制度与产品资料 |
| `organize_company50.py` | 为旧版 50 人模拟库补组织档案，也被生成脚本复用 |
| `register_company50_products.py` | 为旧版 50 人模拟库补产品目录登记 |

`eval_company_routing.py`、`eval_recall.py`、`eval_recall_live.py`、`eval_rerank_latency.py` 依赖旧星河数据、过时的自动转人工断言或历史快照；本地历史文件保留，但已从当前 Git 跟踪范围移除并加入忽略规则。

仓库包含代码、测试、依赖锁文件、部署配置和空密钥的 `.env.example`。`.env`、`data/`（数据库、账号清单、模型、文档和评估报告）、虚拟环境、依赖目录、前端构建产物和日志均不上传。

新克隆项目不包含本机模拟数据。准备好 `.env`、Milvus 和本地模型后，运行 `.venv/bin/python -m scripts.seed_company50`，再将它输出的目录传给 `scripts.company_simulation serve --root`。README 中带时间戳的目录仅是本机示例。模拟账号的统一密码仅用于本地演示。


### 自然语言报销与报修

网页和 CLI 共用草稿流程。可以说“帮我报销客户拜访打车费125.5元”或“电脑无法联网，帮我报修”。
信息不足时会追问；也可以接着说“改成150元”“事由改成客户拜访交通费”。
系统展示申请人和最新内容后，回复“确认报销”/“确认报修”才创建业务记录，回复“取消”则清除未提交草稿。
旧的 `报销：125.50；事由：客户拜访交通费` 和 `报修：电脑无法联网` 格式也需要确认。

- 网页草稿按账号、会话保存在 SQLite，刷新/重新登录可继续；切换业务话题会清除旧草稿。
- 模型只能提取字段，不能授予权限或确认操作。金额由 Decimal 校验，目前只支持人民币、本人、单笔报销。
- 每份确认摘要带服务端生成的请求编号；重复提交同一草稿返回原业务记录，不重复创建。
- 报销提交后进入组织审批，不会付款；已提交申请的撤销/修改仍未实现。


### 报销组织审批

左侧「报销与审批」包含「我的报销」「待我审批」「我已审批」；有 `expense:read_all` 权限的财务或管理员还可查看「全部报销」。

- 普通员工按实际汇报链逐级审批，到部门经理通过后结束。例如：员工 → 组长 → 部门经理。组长本人的申请由部门经理审批。
- 部门经理及以上只需直属上级审批：部门经理 → 总经理、总经理 → 董事长。如果低职级员工直接汇报给总经理或董事长，则由该上级审批后结束。
- 董事长或未配置上级的账号不会自动审批成功。申请保留为待审批，页面提示完善组织关系；没有合适上级时需线下处理，系统不强行指派管理员。
- 模型不会执行审批。只有当前轮次、当前级别的指定审批人才能在页面确认通过或驳回；不能审批自己的申请，不能跳级。系统管理员/财务的查看权限不赋予审批权。
- 驳回必须写原因；通过、驳回均记录审批人、意见和时间。员工在「我的报销」查看结果。审批完成后不可修改结果；这不代表已付款。
- 待审批期间，系统会重新核对汇报链和账号启用状态。关系变化后，旧审批人不能继续审批；申请人或管理员可在详情中点击「按当前组织关系重新匹配」，从第一级重新审核，保留旧轮次记录。老申请也可通过此入口分配审批链。
- 已经处理过申请的审批人仍可查看该申请的审批记录；未来审批人要等流转到自己后才能查看。暂无消息推送或附件/发票核验功能。

接口：`GET /api/expenses?view=mine|inbox|reviewed|all`、`GET /api/expenses/{id}`、`POST /api/expenses/{id}/review`、`POST /api/expenses/{id}/reassign`。
审批提交必须携带服务端返回的 `step_id`，旧页面、重复点击或并发处理会被拒绝，避免覆盖别人刚做的决定。


### 人工协助回复闭环

- 员工明确转人工后建立待处理事项；员工只能查看自己的问题和回复，管理员凭 `handoff:manage` 查看处理队列。
- 回复必填（最多4000字），确认后将事项更新为已处理，同时保存处理人、时间和完整回复。重复或并发处理不能覆盖已经提交的回复。
- 员工的导航显示未读回复数，在登录、导航、窗口重新获得焦点及页面可见时每30秒刷新。事项内容在打开详情或点击“刷新事项”时读取；不自动写入模型上下文。
- 员工查看回复后可点击“标记已读”，管理员不能替员工标记。回复保存于 SQLite，重新登录仍可查看；不发送邮件、短信或企业 IM 消息。
- CLI 的 `resolve-handoff` 同样要求填写回复；网页与 CLI 共用记录。处理回复不自动执行余额修改或其他业务操作。
- `GET /api/handoffs` 为管理员队列，`GET /api/handoffs/mine` 为本人列表，均支持 `status`、`limit`、`offset`；`GET /api/handoffs/unread-count` 仅返回本人的未读数。
- `POST /api/handoffs/{id}/resolve` 必须携带 `{"reply":"具体处理结果"}`；`POST /api/handoffs/{id}/read` 仅允许本人将已回复事项标记已读。

### IT 工单处理闭环

- 在工作助手中描述故障，核对摘要后确认报修；左侧「IT 工单 → 我的报修」查看自己的记录。
- IT 角色和系统管理员具有 `ticket:manage`，可在「待认领」认领、在「我处理的」填写处理结果。处理本人报修时必须由其他 IT 人员认领。
- 状态为：待处理 → 处理中 → 待员工确认 → 已关闭。只有报修员工本人能确认解决；若仍未解决，填写原因后退回原处理人，再次处理和确认。
- 每次认领、提交结果、退回、确认都记录操作人、内容和时间；旧记录迁移时保留原工单，不补造历史操作。
- 写操作携带详情中的 `version`，服务端在事务内重新核对账号、权限和状态；重复点击、并发认领和过期页面会被拒绝，不能覆盖新结果。
- 可以问“我的报修处理到哪了？”或“查询我的工单#1”。模型只识别查询意图与编号，回答来自本人真实工单；即使管理员也不能通过这条“我的工单”查询读取他人记录。未指定编号时最多展示最近20条。
- 模型不执行认领、处理或关闭；这些动作在工单页面确认。工单流转会生成站内通知；当前没有自动派单、处理时限提醒、转派或外部消息推送。处理人离职/失去权限时需管理员线下协调，不能借旧角色继续处理。

接口：`GET /api/tickets?view=mine|queue|assigned`、`GET /api/tickets/{id}`、`POST /api/tickets/{id}/claim|resolve|confirm|reopen`。
写操作请求示例：`{"version":1,"comment":"已修复网卡驱动并验证网络"}`。提交处理结果和退回时 `comment` 必填。

### 年假申请与组织审批

左侧「请假与审批」包含本人余额、申请表、「我的请假」「待我审批」「我已审批」；HR/管理员凭 `leave:read_all` 可查看全部申请。所有现有员工角色增加独立的 `leave:create` 申请权限，审批人仍由组织关系决定。

- 第一版只支持年假，以半天为单位。起止日期和上午/下午均计入范围；只能新建今天（Asia/Shanghai）及以后的申请，单次区间最多366天。天数由后端工作日历计算，模型不计算或修改余额。
- 可直接说“下周一下午请半天年假，事由是家庭事务”。助手追问缺失信息、展示日期/扣减天数/事由/审批人/日历依据，明确回复“确认请假”后提交。也可以在页面填写，先核算预览再确认。
- 日历默认周一至周五，不自动包含法定节假日和调休。HR/管理员凭 `leave:write_all` 在本页维护单日工作/休息安排；表单和对话都会说明此计算依据。不要把默认日历当作完整的法定节假日日历。
- 余额分为“剩余年假、待审批预占、可申请”。提交后预占，逐级全部审批通过后在同一事务扣减总余额；驳回或本人撤回待审批申请只释放预占。HR 设置余额不能低于当前预占量；网页余额维护带固定请求编号，重试原请求不会恢复期间已经审批扣掉的额度。
- 普通员工沿实际汇报链逐级审批至部门经理；部门经理及以上由直属上级审批。未配置上级或董事长没有更高审批人时，不自动通过；保留预占并提示配置，员工可撤回。
- 审批与报销一致：仅当前审批人可处理，不可审批本人申请；下一审批人未到本级前不能查看正文；历史审批人可查看已经处理过的记录。HR/管理员的全部读取权限不赋予审批权。
- 组织关系变化时阻止旧审批人操作，由本人或管理员重新匹配，从首级重新审批，保留原记录。申请期间的日历变化若影响工作半天，则阻止继续审批，必须由本人撤回重提，不能通过重新分配绕过。
- 待审批与已通过申请的工作半天不能重叠。创建请求编号、审批步骤编号和撤回版本号防止重复提交、重复扣减或旧页面覆盖新状态；日期/额度/权限在写事务中再次验证。
- 对话可问“我的请假批了吗？”“我的年假余额”。查询绑定登录人，只读取本人的真实记录；不会由模型执行审批或撤回已提交申请。
- 当前不含病假/事假、附件、自动年初发放或结转、已批准假期的撤销退款和外部考勤同步；余额为现有账号的额度池，不按年度分账。

主要接口：`GET /api/leave/balance`、`POST /api/leave/preview`、`POST /api/leave`、`GET /api/leave?view=mine|inbox|reviewed|all`、`GET /api/leave/{id}`、`POST /api/leave/{id}/review|cancel|reassign`。
日历查询 `GET /api/leave/calendar?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` 返回范围内每日最终安排；HR 通过 `POST /api/leave/calendar` 维护单日安排、`POST /api/leave/balance` 维护账号额度。

### 统一待办与站内通知

左侧「待办与通知」区分业务待办和未读提醒，两个数量分别显示。

- 我的待办汇总：轮到本人审批的年假/报销、IT 待认领或本人处理中的工单、本人需确认结果的工单、管理员待回复的人工事项。本人申请遇到组织关系或工作日历变化，也会提示处理。
- 点击待办或通知直接打开对应业务详情；每次查询详情和执行操作仍重新验证权限。事项处理后从待办移出，读取通知不会代替审批、确认或回复。
- 通知包含审批交接和结果、工单认领/处理/退回/确认、人工回复。通知和业务写操作在同一数据库事务提交，同一事件重试不会重复通知。
- 查看通知列表或打开业务详情不会自动标记已读；显式「标记已读」只改变阅读状态。人工协助原有的已读按钮与通知已读同步。
- 可问助手“我还有哪些待办？”“哪些申请有新结果？”“我的未读通知”。回答读取登录账号的真实记录，不自动审批、回复或标记已读。
- 聊天中的待办/通知摘要为查询快照；再次读取历史或重放请求时，若当前结果已变化，则隐藏旧摘要并提示重新查询，避免保留已失效权限的正文。
- 登录、切换页面、窗口重新激活及页面可见时每 30 秒刷新角标；列表进入时或手动刷新时更新，避免轮询打断业务表单。
- 首次升级只按既有业务的当前状态补发通知并保留原时间，保留人工回复的原已读状态；后续启动不重复补发。历史通知描述当时的事件，最新状态以业务详情为准。
- 当前为站内通知，没有邮件、短信、企业微信或钉钉推送。统一待办按当前权限过滤后分页/计数，适合当前规模；尚未引入消息队列或后台任务服务。

接口：`GET /api/work-center/todos`、`GET /api/work-center/summary`、`GET /api/notifications?unread_only=true`、`POST /api/notifications/{id}/read`。列表支持 `limit` 和 `offset`；身份来自登录会话，不接受指定他人账号查询。

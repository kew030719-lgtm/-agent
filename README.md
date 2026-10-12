# 企业内部员工助手 Agent

FastAPI + Vue 3 网页版员工助手，同时保留命令行入口：登录后按角色使用工具；DeepSeek 负责问题路由和基于资料的回答；LangGraph 组织流程；SQLite 保存账号、文档、业务记录和人工待办。知识向量由本地中文模型 `BAAI/bge-small-zh-v1.5` 生成，存入 Docker 中的 Milvus Standalone。

## 网页版启动

依赖已经安装，前端已经构建。现在只需在项目根目录执行：

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
- 可见知识文件列表。管理员可上传文件并选择范围、补建缺失索引、删除文档与索引。支持的格式与原 CLI 一致；导入失败时会说明是否已保存文档，便于重新建立索引。
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
| GET / POST | `/api/handoffs` / `/api/handoffs/{id}/resolve` | 待办、标记处理完成 |
| GET | `/api/knowledge-feedback` / `/api/knowledge-stats` | 管理员反馈与统计 |

写请求需带 `X-Requested-With: employee-assistant`；有 Origin 时还必须在 `API_ALLOWED_ORIGINS` 中。登录示例请求体为 `{"username":"员工账号","password":"账号密码"}`。发送问题需提供 `question` 与 UUID 格式的 `request_id`；重复提交同一已完成请求会返回原答案，避免再次执行工具。前端会自动携带 Cookie 和上述请求头。在 Swagger 文档点击 Authorize，在 RequestMarker 中填写 `employee-assistant`，随后执行登录接口即可继续调试。

### 管理员日常操作

**创建账号**：登录后打开「用户管理」→「创建账号」→ 填写账号名、选择角色、输入并确认初始密码 → 提交。成功后用户即可用该账号登录。重复账号会被拒绝，页面不会显示已存密码。支持普通员工、人事、IT、财务、管理员五种角色。

**处理人工待办**：打开「人工待办」→ 查看问题、发起人和转交原因 → 先完成实际处理 → 点击「标记已处理」。例如“请假余额未录入”应先用 `main.py set-leave` 录入余额，再关闭待办。该按钮仅将待办从 pending 更新为 resolved，同时保存处理人和处理时间；不会自动办理业务，也不会向员工发送通知。当前页面只展示待处理事项，已处理事项保留在数据库。

### Web 配置与边界

根目录 `.env` 新增 `API_HOST`、`API_PORT`、`API_SESSION_TTL_HOURS`、`API_COOKIE_SECURE`、`API_UPLOAD_MAX_MB`、`API_ALLOWED_ORIGINS`、`FRONTEND_HOST`、`FRONTEND_PORT`、`FRONTEND_API_TARGET`。所有默认值和说明见 `.env.example`。前端只使用服务器地址配置，DeepSeek Key 和 Milvus 凭证不会注入前端构建。测试可用 `API_DATABASE_PATH` 指向隔离 SQLite；其所在目录同时作为该 API 的知识文件和模型目录。

当前默认仅监听本机，并用一个 Uvicorn 进程运行。网页对话上下文已写入 SQLite，重启后历史和正常完成的报修确认状态仍可继续；CLI 原本的内存检查点机制保持不变。若服务在执行操作期间中断，该请求保留为 processing，不会自动重放；请先核对业务记录，再新建对话继续。这里没有实现跨 SQLite 与 Milvus 的分布式事务，也没有承诺进程崩溃场景下所有业务操作精确执行一次。

网页请求会等待完整答案返回，当前没有流式输出。上传和本地模型推理在工作线程处理；同一进程的知识检索/入库通过锁协调，适合当前本地学习与小规模使用。公开部署前应通过 HTTPS 并设置 `API_COOKIE_SECURE=true`，按实际站点配置允许来源。

## 已实现

- “你是谁”“现在有什么权限”等常用问法由本地节点直接回答；权限来自登录账号，不能通过对话修改。
- 模型/API 或检索服务异常提示重试，不创建人工待办；只有意图需人工处理、依据不足等情况进入待办。
- 账号登录和角色权限：`employee`、`hr`、`it`、`finance`、`admin`。
- IT 工单、本人请假余额、报销申请、会议室查询；低置信度及知识不足的问题转人工待办。
- 管理员导入 PDF、Markdown、DOCX、XLSX、XLSM、XLS。解析时保留页码、标题、行号、工作表和表格行等位置；再按结构切片、生成向量。
- 扫描 PDF 通过 OCR 提取文字。PDF 图片另存于 `data/knowledge/images/`，其相对地址、页码和区域写入 `knowledge_assets`；读取图片路径需通过文档可见范围检查。
- 原问题与一条经过校验的改写分别走 Milvus 向量检索和 SQLite FTS5/BM25 关键词检索，用 RRF 融合各路排名，默认保留36条重排候选。明确点名文档时按原问题限定目标；每一路按角色过滤并由 SQLite 复核权限。短追问先带上上一条成功知识问题。DeepSeek 回答必须提供资料编号及有效原文，最终显示来源位置。
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

`eval_live` 读取 `.env` 中的密钥，调用真实 DeepSeek，会产生 API 用量。它模拟 CLI 的登录身份和键盘输入，使用真实 LangGraph、检索和生成链路；用合成年假、VPN、会议室和报销文件建立临时知识库，不写入现有 SQLite 或 Docker Milvus。覆盖知识引用、多轮追问、角色隔离、无依据转人工及身份权限查询；登录密码验证不在这个脚本的覆盖范围内。服务故障通过单元测试注入验证。两个评估脚本均需要已下载的本地模型及 Milvus Lite 运行环境。

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

打开 `http://127.0.0.1:8002`，管理员 `wk_admin_01`，普通员工 `wk_employee_01`。模型缓存复用现有文件，业务数据库、上传文档及Milvus集合独立。默认8000端口服务仍使用原业务库。模拟资料是供查询和流程演示的虚构制度，不代表系统已集成资料中描述的全部产品操作。

`scripts/company_simulation.py seed/test` 保留旧百人压测脚本，不能用其中硬编码的百人测试案例验收当前50人数据。历史对话接口继续支持 `limit`（1–100）与 `offset`。

## 查询改写和召回评估

问答链路：原问题 →（追问补全）→ 原问题加一条改写 → 各自向量/BM25检索 → RRF去重融合 → 默认36候选重排 → 必要时补一个同章节或同页的相邻片段 → 最多4段、4000字符证据 → 回答与引用校验。

- 原问题始终参与检索，生成回答也始终使用原问题。改写不作为新的权限或文档范围来源。
- 改写用一个额外模型请求，8秒超时且不自动重试。编号、版本、数字、日期或检测到的否定/范围词发生变化时丢弃改写；非法输出、超时也回退原文。这些规则无法形式化保证所有语义完全不变。
- RRF使用各路排名的倒数之和，常数为60；保留向量分数仅供诊断，不用0.55在重排之前淘汰关键词命中。模型判断证据不足时仍转人工；引文和数字校验继续执行。
- 短片段或冒号结尾的片段可补同章节/同页的前后邻居，最多一条。邻居再次经过权限检查，独立标注来源，共用证据长度预算。

配置已放入 `.env` / `.env.example`：`RAG_QUERY_REWRITE_ENABLED=true`、`RAG_CANDIDATE_LIMIT=36`（4–100）、`RAG_NEIGHBOR_CONTEXT_ENABLED=true`。

本地重排使用 `RAG_RERANK_BATCH_SIZE=1`（1–64）。批次大小只控制每次模型推理处理多少片段，全部候选仍参与重排；按长度分组后会恢复分数与原片段的对应关系。不同 CPU 上最佳批次可能不同，量化模型的分数也可能随批次略变。`scripts/eval_rerank_latency.py --root ...` 复用已保存的20题查询改写，对同一组候选交替测量原64条批次和当前配置，报告重排耗时及最终证据命中；该耗时不含改写、检索和回答生成。

`scripts/eval_recall.py`、`scripts/eval_rerank_latency.py` 是旧星河语料的固定金标评估脚本，需要对应语料和基准快照。旧数据已删除，它们不适用于当前维科50人语料。重新评估必须使用与新文档对应的问题和金标，不能沿用旧报告声称新语料效果。

## 当前边界

- 旧版 `.doc` 需先转换成 `.docx`；DOCX 内嵌图片、Excel 图表或内嵌图片尚未提取。扫描 PDF 的图片会保存，只有 OCR 得到的文字会进入向量检索；纯图片仍可按权限取得图片文件路径，但不会自动生成图像语义回答。
- 单个表格行保持完整；若长度超过模型上限，索引会报错并保留已导入文档，供整理后重新导入。关键词索引使用 SQLite FTS5 三字切分，两字中文查询主要依赖向量检索或改写。引文和数字校验不能证明回答的每句话都被资料支持；召回变多也不等于答案正确。
- 会话内追问仍靠“那”“这个”“它”“刚才”等短问题触发上一条知识问题补全，再交给改写模型；没有完整历史摘要、基于上一条答案的指代消解或任意复杂问题拆解。旧 SQLite `embedding` 字段暂时保留供迁移，新向量只写入 Milvus。
- IT 工单、报销和会议室数据仅在本地 SQLite；人工兜底是本地待办队列，没有企业系统对接或通知。CLI 的 LangGraph 检查点在内存中，重启后 CLI 对话状态不恢复；网页版通过 SQLite 保存会话上下文。

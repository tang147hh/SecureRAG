# SecureRAG

SecureRAG 是一个面向企业知识库问答的 RAG 应用。项目保留 Kotaemon 的核心 RAG、索引、存储和推理能力，但应用前端只使用 `frontend/` 下的 React/Vite 界面；原 Kotaemon/Gradio 应用前端已经从启动链路和源码中移除。

## 核心能力

- React 单页前端：聊天、文件管理、引用面板、Trace 查看、RAG 评测工作台。
- FastAPI 后端：统一暴露 `/api/react/*` REST 和 SSE 流式聊天接口。
- 文档入库：支持 PDF、Office、图片、HTML、Markdown、CSV、ZIP 等文件类型，按用户权限写入索引。
- 检索增强：支持向量检索、全文检索、Hybrid/RRF、rerank、MMR、查询改写、HyDE、RAG-Fusion。
- GraphRAG：可通过 LightRAG / NanoGraphRAG 等可选依赖启用图检索增强。
- 权限控制：文件级 ACL、私有索引、可见文件过滤，评测时可检测权限泄露。
- 可观测性：保存每轮 RAG Trace，包括检索参数、有效身份、引用证据、耗时和错误。
- 评测工作台：管理数据集、样例和多策略对比运行，并可选接入 Ragas 指标。

## 技术栈

- 后端：Python 3.10+、FastAPI、SQLModel/SQLAlchemy、SQLite、Chroma、LanceDB、theflow。
- RAG 核心：`libs/kotaemon` 提供 LLM、Embedding、loader、splitter、retriever、reranker 和 pipeline 基础能力。
- 应用后端：`libs/ktem` 提供 SecureRAG 的索引、权限、Trace、评测、React API 和 headless runtime。
- 前端：React 19、TypeScript、Vite、lucide-react。
- 包管理：Python 使用 `uv` workspace；前端使用 `pnpm`。

## 目录结构

```text
.
├── app.py                         # 非 SSO FastAPI 入口，挂载 React dist
├── sso_app.py                     # SSO FastAPI 入口，支持 Google / Keycloak
├── launch.sh                      # 容器/部署启动脚本
├── flowsettings.py                # 模型、索引、存储、权限和功能开关配置
├── frontend/                      # 唯一保留的应用前端，React + Vite
├── libs/ktem/ktem/react_runtime.py # 无 Gradio 的 headless 应用运行态
├── libs/ktem/ktem/react_api.py     # React 前端使用的 FastAPI 路由
├── libs/ktem/ktem/react_defaults.py
├── libs/ktem/ktem/index/          # 文件索引、检索、GraphRAG
├── libs/ktem/ktem/permissions/    # 文件 ACL 与权限过滤
├── libs/ktem/ktem/trace/          # RAG Trace 持久化
├── libs/ktem/ktem/evaluation/     # RAG 评测数据集与运行记录
└── libs/kotaemon/                 # 底层 RAG 组件库
```

## 本地启动

### 1. 准备环境变量

复制 `.env.example` 为 `.env`，至少配置一个聊天模型和一个 Embedding 模型。当前默认配置偏向：

- 聊天模型：`DEEPSEEK_API_KEY`、`DEEPSEEK_API_BASE`、`DEEPSEEK_CHAT_MODEL`
- Embedding：本地 Ollama OpenAI-compatible endpoint，默认 `KH_OLLAMA_URL=http://localhost:11434/v1/` 和 `LOCAL_MODEL_EMBEDDINGS=nomic-embed-text`

### 2. 安装后端依赖

```bash
uv sync
```

如需额外文档解析、GraphRAG 或 OCR，可按 Dockerfile 中的 full/paddle 目标安装相应 extras。

### 3. 安装并构建前端

```bash
cd frontend
pnpm install
pnpm build
cd ..
```

构建产物位于 `frontend/dist`。后端会把它挂载到 `/app/`。

### 4. 启动后端

```bash
HOST=0.0.0.0 PORT=7860 .venv/bin/uvicorn app:app --host "$HOST" --port "$PORT"
```

浏览器访问：

```text
http://localhost:7860/app/
```

开发前端时也可以单独运行：

```bash
cd frontend
VITE_API_BASE_URL=http://localhost:7860/api/react pnpm dev
```

## SSO 启动

`sso_app.py` 支持 Google OpenID Connect 和 Keycloak。

```bash
KH_SSO_ENABLED=true \
AUTHENTICATION_METHOD=KEYCLOAK \
KEYCLOAK_SERVER_URL=https://keycloak.example.com \
KEYCLOAK_REALM=your-realm \
KEYCLOAK_CLIENT_ID=your-client \
KEYCLOAK_CLIENT_SECRET=your-secret \
SECRET_KEY=change-me \
.venv/bin/uvicorn sso_app:app --host 0.0.0.0 --port 7860
```

如果 `AUTHENTICATION_METHOD` 不是 `KEYCLOAK`，默认使用 Google，并读取 `GOOGLE_CLIENT_ID` 与 `GOOGLE_CLIENT_SECRET`。

## 主要 API

所有 React 前端接口都在 `/api/react` 下：

- `GET /health`：后端健康检查。
- `GET/POST /conversations`：列出或创建会话。
- `GET /conversations/{id}/messages`：读取会话消息。
- `POST /chat/send`：普通聊天。
- `POST /chat/stream`：SSE 流式聊天。
- `GET /messages/{message_id}/references`：恢复某条回答的引用。
- `GET /traces/{trace_id}`：查看 RAG Trace。
- `POST /files/upload`：上传并入库文件。
- `GET /files/workspace`：文件与目录工作区。
- `PATCH /files/{file_id}/permissions`：更新文件 ACL。
- `GET/POST /eval/datasets`：管理评测数据集。
- `POST /eval/examples/{example_id}/run`：启动单样例评测。
- `POST /eval/datasets/{dataset_id}/run`：启动数据集评测。

## 数据与存储

运行数据默认写入 `ktem_app_data/`：

- `user_data/sql.db`：会话、用户、索引元数据、权限、Trace、评测记录。
- `user_data/files/`：上传文件。
- `user_data/vectorstore/`：Chroma 向量库。
- `user_data/docstore/`：LanceDB 文档片段库。
- `user_data/prompt_templates.json`：用户提示词模板。
- `user_data/chat_runtime_settings.json`：用户聊天参数。

## 重要配置

`flowsettings.py` 是主配置文件。常用项包括：

- `KH_FEATURE_USER_MANAGEMENT`：是否启用用户身份隔离。
- `KH_DATABASE`：SQLite 数据库路径。
- `KH_FILESTORAGE_PATH`：上传文件目录。
- `KH_DOCSTORE` / `KH_VECTORSTORE`：文档库和向量库配置。
- `KH_ALLOWED_LLM_NAMES` / `KH_ALLOWED_EMBEDDING_NAMES`：允许展示的模型名称。
- `KH_REASONINGS`：启用的推理 pipeline。
- `KH_INDICES`：默认文件索引配置。
- `USE_LIGHTRAG`、`USE_NANO_GRAPHRAG`、`USE_MS_GRAPHRAG`：GraphRAG 可选能力。
- `KH_RAGAS_EVAL_ENABLED`：是否开启 Ragas 评测指标。

## 前端说明

React 前端源码在 `frontend/src`：

- `App.tsx`：应用状态编排、启动加载、会话/文件/评测视图切换。
- `api/client.ts`：后端 API client 和 SSE 流式消息解析。
- `api/types.ts`：前后端共享的数据类型。
- `components/ChatWorkspace.tsx`：聊天工作区。
- `components/FileWorkspace.tsx`：文件、目录和权限管理。
- `components/EvaluationWorkspace.tsx`：RAG 评测工作台。
- `components/ReferencePanel.tsx`：引用与证据面板。
- `styles/global.css`：全局样式。

## 测试与检查

```bash
# Python 语法检查
.venv/bin/python -m compileall -q app.py sso_app.py libs/ktem/ktem

# 后端运行态初始化检查
.venv/bin/python - <<'PY'
from ktem.react_runtime import ReactRuntime
from ktem.react_api import ReactApiService
runtime = ReactRuntime()
service = ReactApiService()
service.configure(runtime)
print(len(runtime.index_manager.indices), service.get_chat_settings("default").reasoningMethod)
PY

# 前端构建
cd frontend && pnpm build
```

## 当前前端边界

本项目应用层只保留 React/Vite 前端。`libs/ktem` 中原 Kotaemon/Gradio 页面、主题、页面资源和启动入口已经移除；`libs/kotaemon` 中独立的 promptui/Gradio 工具也已移除。`app.py`、`sso_app.py` 和 `launch.sh` 都只启动 FastAPI + React 静态资源。

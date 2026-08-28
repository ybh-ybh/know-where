# 知归（KnowWhere）

让散落的信息各归其位。

知归是一个仅供个人使用的 AI 信息归档工具。当前 MVP 支持把公开微信公众号文章发送给飞书机器人，自动提取完整正文、调用 OpenAI 兼容模型生成分类与摘要，并归档到系统创建的飞书多维表格。

## 当前能力

- 飞书官方 SDK 长连接接收个人私聊，不需要公网回调地址。
- 系统幂等创建“知归”多维表格，并只补齐缺失字段。
- 从“一级分类”单选字段读取默认及用户自定义分类。
- 微信公众号文章 UTF-8 正文、标题、作者和发布时间提取。
- 提示词主约束 + few-shot + `json_object` + Pydantic Schema 校验。
- 首轮非法 JSON 后进行一次受限修复；仍失败时明确降级，不写非法分类。
- PostgreSQL 保存任务状态、完整正文、AI 结果和飞书外部引用。
- 同一规范 URL/内容 ID 幂等归档。
- 端口/适配器边界已为 LLM、ASR、对象存储和归档供应商替换预留。

腾讯云 COS 与 ASR 的配置和端口已经定义，但视频下载、分段、转录编排不在本次文章 MVP 代码范围内。

## 核心数据流

```text
飞书私聊 / CLI
  → 微信文章提取器
  → 飞书分类目录
  → OpenAI 兼容 LLM
  → 飞书多维表格
  → PostgreSQL 完成快照
```

业务流水线只依赖 `ContentExtractorPort`、`LlmProviderPort`、`CategoryCatalogPort`、`RecordArchivePort` 和 `TaskRepositoryPort`。供应商选择集中在 `composition.py`，切换实现时不修改状态机。

## 准备配置

1. 安装 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Docker Desktop。
2. 复制 `.env.example` 为 `.env`。
3. 填入飞书和一个 OpenAI 兼容 LLM 的必填配置；视频链路需要时再填写腾讯云配置。
4. 确认飞书应用已经发布、启用机器人能力，并订阅 `im.message.receive_v1` 事件。

`.env` 已被 Git 和 Docker 构建上下文忽略。不要把真实密钥写进镜像、日志、提交、Issue 或问题截图。Docker Compose 通过 `env_file` 在运行时注入配置，不把密钥复制进镜像。

PostgreSQL 数据库名、用户名和密码必须在本地 `.env` 中设置；Compose 只做运行时引用，并只把开发数据库映射到 `127.0.0.1:5432`。公网或服务器部署时应使用独立强密码，并避免暴露数据库端口。

LLM 统一使用 `KW_LLM_API_KEY`、`KW_LLM_BASE_URL` 和 `KW_LLM_MODEL`，不在配置结构中绑定具体厂商。`KW_LLM_THINKING_MODE` 是可选的非标准兼容扩展；供应商不支持时保持为空。

## 本地运行

```powershell
Copy-Item .env.example .env
uv sync --all-groups
docker compose up -d postgres
uv run knowwhere migrate-and-health
uv run knowwhere init-feishu
uv run knowwhere process "https://mp.weixin.qq.com/s/文章ID"
```

默认读取根目录 `.env`；需要使用其他文件时，可为命令传入 `--env-file 路径`。进程环境变量的优先级高于文件值，便于容器或密钥管理服务覆盖。

离线验证不访问云服务：

```powershell
uv run knowwhere fake-smoke
uv run ruff check .
uv run mypy src/knowwhere
uv run pytest
```

## Docker Compose 运行

构建并启动 PostgreSQL 与飞书长连接 Gateway：

```powershell
docker compose build gateway
docker compose --profile gateway up -d postgres gateway
docker compose --profile gateway ps
```

此时把一个公开微信公众号链接私聊发送给机器人即可。Gateway 会先回复“已收到”，完成后回复飞书记录链接。

容器内手工处理一篇文章：

```powershell
docker compose run --rm app process `
  "https://mp.weixin.qq.com/s/文章ID"
```

停止服务：

```powershell
docker compose --profile gateway down
```

## LLM JSON 可靠性策略

知归不把“供应商宣称支持 JSON”当作可靠性保证。当前顺序是：

1. 系统提示词明确唯一 JSON 对象、固定字段、分类枚举和事实忠实约束。
2. 提供一组合法的 user/assistant few-shot。
3. 请求兼容接口的 `response_format={"type":"json_object"}`。
4. 依次尝试直接 JSON、Markdown JSON 代码块和首个平衡对象。
5. 使用严格 Pydantic Schema 校验字段、数量、长度、置信度及分类集合。
6. 失败时只携带失败输出做一次受限修复，不重复发送全文。
7. 两次都失败时生成显式 `LLM_JSON_DEGRADED` 结果，分类回退为“其他”，避免非法数据污染知识库。

## 主要目录

```text
src/knowwhere/domain/          领域对象与状态机
src/knowwhere/application/     端口和处理流水线
src/knowwhere/adapters/        微信、LLM、飞书与 Fake 适配器
src/knowwhere/infrastructure/  PostgreSQL 映射与仓储
alembic/                       数据库迁移
tests/                         离线回归测试
docs/                          PRD、架构与部署说明
```

项目的完整产品范围和后续视频链路分别见 `docs/PRD.md` 与 `docs/TECHNICAL_ARCHITECTURE.md`。

## 开源提交边界

应提交源码、测试、Alembic 迁移、`.env.example`、`uv.lock`、Docker 文件和通用文档。以下内容只保留在本机：

- `.env` 和任何包含真实密钥的本地配置文件。
- `agent_memory/`、`work/`、一次性环境验收记录和个人调试笔记。
- `.venv/`、缓存、覆盖率报告、日志、IDE 配置、数据库备份与 Compose 本地覆盖文件。

如果密钥曾经进入 Git 历史或远端仓库，仅删除文件不够，必须立即在对应平台轮换密钥并清理历史。

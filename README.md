# 知归（KnowWhere）

让散落的信息各归其位。

知归是一个仅供个人使用的 AI 信息归档工具。当前支持把公开微信公众号、稀土掘金文章、GitHub 仓库和抖音图文/视频发送给飞书机器人，自动提取正文、README、逐图视觉正文或视频转录，调用 OpenAI 兼容模型生成分类与摘要，并归档到系统创建的飞书多维表格。

## 当前能力

- 飞书官方 SDK 长连接接收个人私聊，不需要公网回调地址。
- 系统幂等创建“知归”多维表格，并只补齐缺失字段。
- 从“一级分类”单选字段读取默认及用户自定义分类。
- 微信公众号文章 UTF-8 正文、标题、作者和发布时间提取。
- 稀土掘金文章 SSR Markdown/DOM 双路径正文、标题、作者和发布时间提取。
- GitHub 公开仓库根目录 README Markdown 原文提取。
- 抖音分享短链还原、A-Bogus 作品详情解析，以及图文/视频事实字段分流。
- 抖音图文全部图片经私有 COS 短时 URL 交给 GLM-4.6V 做有序 OCR 和视觉理解。
- 抖音视频经 FFmpeg 标准化为单声道 16k MP3，并按 4 小时切成腾讯单任务安全分段，再由腾讯云录音文件识别顺序转录。
- COS 临时对象在成功和失败路径都会清理；任务保存无密钥阶段检查点。
- AI 同步生成简短标题、分类、标签与摘要，飞书同时保留未经改写的原始标题。
- 飞书提供“阅读状态”和默认留空的“阅读时间”字段。
- 提示词主约束 + few-shot + `json_object` + Pydantic Schema 校验。
- 首轮非法 JSON 后进行一次受限修复；仍失败时明确降级，不写非法分类。
- PostgreSQL 保存任务状态、完整正文、AI 结果和飞书外部引用。
- 同一规范 URL/内容 ID 幂等归档。
- 端口/适配器边界允许替换 LLM、视觉模型、ASR、对象存储和归档供应商。

## 核心数据流

```text
飞书私聊 / CLI
  → 内容平台分派器（微信 / 掘金 / GitHub / 抖音）
  → 抖音图文：全部图片 → 私有 COS → GLM 视觉正文 → 清理
  → 抖音视频：视频 → FFmpeg 音频 → 私有 COS → 腾讯 ASR → 清理
  → 飞书分类目录
  → OpenAI 兼容 LLM
  → 飞书多维表格
  → PostgreSQL 完成快照
```

业务流水线只依赖稳定端口。抖音提取器通过 `ArtifactStorePort`、`VisionProviderPort` 和 `AsrProviderPort` 使用外部能力；供应商选择集中在 `composition.py`，切换实现时不修改状态机。

## 准备配置

1. 安装 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Docker Desktop。
2. 复制 `.env.example` 为 `.env`。
3. 填入用户自建 PostgreSQL、飞书和一个 OpenAI 兼容 LLM 的必填配置。
4. 抖音视频填写腾讯云 COS/ASR 配置；抖音图文还需填写独立的 `KW_VISION_*` 配置。
5. 确认飞书应用已经发布、启用机器人能力，并订阅 `im.message.receive_v1` 事件。

`.env` 已被 Git 和 Docker 构建上下文忽略。不要把真实密钥写进镜像、日志、提交、Issue 或问题截图。Docker Compose 通过 `env_file` 在运行时注入配置，不把密钥复制进镜像。

PostgreSQL 由用户自行部署和备份，Compose 不再创建数据库容器或数据卷。请在 `.env` 的 `KW_DATABASE_URL` 中填写完整连接地址，并确保数据库已创建、应用主机可访问；密码含特殊字符时先做 URL 编码。数据库端口不要向无关公网来源开放。

LLM 统一使用 `KW_LLM_API_KEY`、`KW_LLM_BASE_URL` 和 `KW_LLM_MODEL`，不在配置结构中绑定具体厂商。`KW_LLM_THINKING_MODE` 是非标准兼容扩展，默认 `disabled`；需要时可设为 `enabled`，供应商不支持该字段时保持为空。

视觉模型使用独立的 `KW_VISION_API_KEY`、`KW_VISION_BASE_URL` 和 `KW_VISION_MODEL`。智谱示例地址为 `https://open.bigmodel.cn/api/paas/v4`，官方模型 ID 为 `glm-4.6v`。腾讯云视频链路要求 COS Bucket 为私有读写，并为当前凭据授予限定前缀的上传、读取、删除权限及录音文件识别权限；账号还必须有可用 ASR 额度。

## 本地运行

```powershell
Copy-Item .env.example .env
uv sync --all-groups
uv run knowwhere migrate-and-health
uv run knowwhere init-feishu
uv run knowwhere process "https://mp.weixin.qq.com/s/文章ID"
uv run knowwhere process "https://juejin.cn/post/文章ID"
uv run knowwhere process "https://github.com/Tencent/WeKnora"
uv run knowwhere process "https://v.douyin.com/抖音分享码/"
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

先对用户自建 PostgreSQL 执行迁移和健康检查，再构建并启动飞书长连接 Gateway：

```powershell
docker compose build gateway
docker compose run --rm app
docker compose --profile gateway up -d gateway
docker compose --profile gateway ps
```

此时把公开微信公众号、稀土掘金文章、GitHub 仓库或抖音图文/视频链接私聊发送给机器人即可。Gateway 会先回复“已收到”，完成后回复飞书记录链接。

容器内手工处理一篇文章：

```powershell
docker compose run --rm app process `
  "https://mp.weixin.qq.com/s/文章ID"
```

停止服务：

```powershell
docker compose --profile gateway down
```

该命令只停止应用容器，不会停止或删除用户自建 PostgreSQL。

## 主要目录

```text
src/knowwhere/domain/          领域对象与状态机
src/knowwhere/application/     端口和处理流水线
src/knowwhere/adapters/        微信、掘金、GitHub、抖音、COS、视觉、ASR、LLM 与飞书适配器
src/knowwhere/infrastructure/  PostgreSQL 映射与仓储
alembic/                       数据库迁移
tests/                         离线回归测试
docs/                          PRD、架构与部署说明
```

项目的完整产品范围和多媒体链路设计分别见 `docs/PRD.md` 与 `docs/TECHNICAL_ARCHITECTURE.md`。A-Bogus 独立算法的来源与许可证见 `THIRD_PARTY_NOTICES.md`。

## 开源提交边界

应提交源码、测试、Alembic 迁移、`.env.example`、`uv.lock`、Docker 文件和通用文档。以下内容只保留在本机：

- `.env` 和任何包含真实密钥的本地配置文件。
- `agent_memory/`、`work/`、一次性环境验收记录和个人调试笔记。
- `.venv/`、缓存、覆盖率报告、日志、IDE 配置、数据库备份与 Compose 本地覆盖文件。

如果密钥曾经进入 Git 历史或远端仓库，仅删除文件不够，必须立即在对应平台轮换密钥并清理历史。

![知归 KnowWhere Banner](docs/assets/banner.png)

# 知归（KnowWhere）

把文章、GitHub 仓库或图文视频链接发给飞书机器人，自动提取内容、生成分类和摘要，并归档到飞书多维表格，方便之后回顾、阅读和补充笔记。

KnowWhere is a self-hosted Feishu bot that extracts, summarizes, and organizes links into a personal knowledge base in Feishu Bitable.

[项目网站](https://ybh-ybh.github.io/know-where/) · [完整部署教程](https://ybh-ybh.github.io/know-where/#quickstart) · [支持平台](#支持的平台) · [开发与贡献](#开发与贡献)

## 使用效果

发送一个受支持的链接后，机器人会先回复“知归已收到，正在提取、总结并归档。”，处理完成后返回飞书记录链接。

打开记录可以查看原始链接、正文或转录，以及 AI 生成的标题、分类、标签、摘要和关键观点。新内容默认标记为“未读”，可以继续填写阅读时间和阅读笔记。

![知归飞书多维表格实际归档效果，展示平台、内容类型、分类、摘要与阅读状态](docs/assets/feishu-bitable-preview.png)

飞书多维表格中的真实归档记录：文章、视频和图文统一整理，可按分类浏览并管理阅读状态。截图中的部分内容已做模糊处理。

<!-- 待补充同一条归档内容的已脱敏飞书私聊回执截图。 -->

## 为什么使用知归

- **随手发个链接，剩下自动完成**：在飞书私聊里发送内容，由知归完成提取、分析和归档，无需逐项复制整理。
- **散落各个平台，统一收进知识库**：文章、仓库 README、图文和视频语音集中管理，保留原始链接和来源信息。
- **长文和视频，先看懂重点**：通过摘要和关键观点判断是否值得细读，再回到正文或原链接深入阅读。
- **按自己的习惯，整理和消化**：在飞书中扩展“一级分类”选项，使用未读、分类等视图回顾，并维护自己的阅读笔记。

处理流程：发送链接 → 提取内容 → AI 分类与总结 → 飞书归档；PostgreSQL 保存任务状态与处理结果。

## 支持的平台

| 来源 | 支持内容 | 处理方式与要求 |
| --- | --- | --- |
| 微信公众号 | 公开文章 | 提取标题、作者、发布时间和正文；需要文本模型 |
| 稀土掘金 | 公开文章 | 提取标题、作者、发布时间和正文；需要文本模型 |
| GitHub | 公开仓库主页 | 提取默认分支根目录 README；需要文本模型 |
| 小红书 | 公开图文、视频及分享短链 | 图文另需视觉模型；视频转录后总结；部分访问需要网页会话 |
| 抖音 | 公开图文、视频及分享短链 | 图文另需视觉模型；视频转录后总结 |
| B站 | 公开视频、分享短链及分 P | 提取简介、下载音轨并转录后总结，不分析视频画面 |

所有内容最终都使用文本模型分类和总结。视频默认使用本地 `faster-whisper` 转录，无需云端 ASR 凭据。平台登录、验证、限流或页面变化可能影响提取。

## 开始使用

推荐使用 Docker Compose，完整操作见[页面内部署教程](https://ybh-ybh.github.io/know-where/#quickstart)，包含 Windows、macOS、Linux 和两种数据库路径。

项目网站由 GitHub Pages 托管，提供介绍与教程；机器人服务需要在你自己的电脑或服务器上持续运行。飞书通过长连接接收事件，无需公网域名或 Webhook 回调地址。

### 部署前准备

| 项目 | 要求 |
| --- | --- |
| 运行设备 | 可持续运行 Docker 的电脑或服务器；视频转录会增加 CPU、内存和磁盘开销 |
| 工具 | Git；Windows/macOS 使用 Docker Desktop，Linux 使用 Docker Engine 和 Compose 插件 |
| 飞书应用 | 可创建企业自建应用并启用机器人；个人使用请将应用可用范围设为自己 |
| 文本模型 | 提供 OpenAI 兼容 Chat Completions 接口的 API Key、Base URL 和模型 ID |
| PostgreSQL | 使用仓库提供的可选数据库容器，或准备一个容器可访问的已有数据库 |

Docker 镜像已包含 Python、uv 和 FFmpeg，本机无需另外安装。文本模型、视觉模型和可选腾讯云服务的费用由你选择的供应商收取；本地转录使用运行设备的计算资源。

### 部署导览

先拉取项目，后续操作都在项目目录执行：

```sh
git clone https://github.com/ybh-ybh/know-where.git
cd know-where
```

接下来按[完整教程](https://ybh-ybh.github.io/know-where/#quickstart)依次完成：

1. 复制 `.env.example` 为 `.env`；Windows PowerShell 使用 `Copy-Item .env.example .env`，macOS/Linux 使用 `cp .env.example .env`。
2. 创建飞书应用、启用机器人并申请权限，获取 App ID 和 App Secret；事件订阅和发布留到 Gateway 启动后完成。
3. 填写六项主线配置：`KW_DATABASE_URL`、`KW_FEISHU_APP_ID`、`KW_FEISHU_APP_SECRET`、`KW_LLM_API_KEY`、`KW_LLM_BASE_URL`、`KW_LLM_MODEL`。Base URL 填接口根地址，不追加 `/chat/completions`。
4. 准备 PostgreSQL：默认 `compose.yml` 使用已有数据库；新建数据库时显式叠加 `compose.postgres.yml`，并填写 `KW_POSTGRES_PASSWORD` 和对应连接地址。
5. 构建镜像、执行 `migrate-and-health`，再启动 Gateway。健康检查成功输出为 `OK: migrations + config + postgresql`。
6. 保持 Gateway 运行，配置飞书长连接、订阅 `im.message.receive_v1` 并发布应用；权限生效后执行 `init-feishu`。
7. 在机器人私聊发送第一条链接，确认能打开归档记录。

新建数据库配置使用 PostgreSQL 17、命名卷持久化和健康检查，不发布主机端口。已有数据库的地址必须从容器可达，容器中的 `localhost` 指向容器自身。密码包含特殊字符时，需要对连接地址中的密码部分进行 URL 编码。

### 首次归档验收

建议先发送本项目的仓库链接，验证文字内容链路：

```text
https://github.com/ybh-ybh/know-where
```

收到“归档完成”后，打开返回的记录，确认标题、原始链接、分类、摘要和关键观点均可查看。容器正在运行或数据库检查通过，只验证了部署的一部分；第一条真实内容成功归档才完成主线验收。

`init-feishu` 输出的 `workspace_url` 是归档空间链接，用户访问授权由首次包含受支持链接的有效私聊触发。应用没有锁定首位用户的白名单；其他可用范围内用户发送有效链接，也可能获得同一归档空间的完整权限。详见[飞书配置指南](docs/FEISHU_SETUP.md)。

## 可选能力

| 能力 | 何时需要 | 配置入口 |
| --- | --- | --- |
| 图文视觉理解 | 归档小红书或抖音图文 | [配置视觉模型](docs/ADVANCED_USAGE.md#图文视觉理解) |
| 视频语音转录 | 默认本地转录；也可切换腾讯 ASR 与私有 COS | [配置转录](docs/ADVANCED_USAGE.md#视频语音转录) |
| 小红书网页会话 | 公开笔记被引导至登录页时 | [配置 Cookie](docs/ADVANCED_USAGE.md#小红书网页会话) |
| 媒体和配置文件调整 | 修改大小、超时、临时目录或使用替代配置文件 | [进阶使用说明](docs/ADVANCED_USAGE.md) |

全部配置字段和默认值见 [`.env.example`](.env.example)。

## 常见问题与使用边界

**修改 `.env` 后怎样生效？**

Docker 部署需要重新创建 Gateway 容器，单纯 `restart` 不会重新加载环境变量。切换 ASR 供应商还需重新构建镜像，详见[配置生效方式](docs/ADVANCED_USAGE.md#让配置生效)。

**怎样更新和备份？**

等待正在处理的任务结束，停止 Gateway，备份数据库，再执行 `git pull --ff-only`、重新构建、迁移检查及重新创建 Gateway。备份应保存在容器外；停止容器时不要使用 `down -v` 删除数据卷。命令见[完整部署与维护教程](https://ybh-ybh.github.io/know-where/#quickstart)。

**重复发送链接会怎样？**

已成功归档的同一规范链接，在对应飞书记录仍存在时会复用已有记录。当前仍会先执行内容提取，不保证跳过下载或转录。

**“部分成功”是什么意思？**

模型输出格式不合格时会尝试一次修复；再次不合格时使用启发式结果，并标记“部分成功”和“待人工复核”。提取证据不完整也可能标为部分成功。鉴权、网络或接口异常仍可能导致任务失败。

**长内容是否全部参与总结？**

当前文本模型输入取提取正文的前 40,000 个字符。归档正文与模型输入范围不同，长文章或长视频的摘要可能未覆盖后半段内容。

其他边界：

- 面向公开内容，不支持私密、付费或已删除内容；部分平台需要有效网页会话，仍受验证与限流影响。
- 飞书入口只处理私聊，一条消息只取第一个受支持链接。
- GitHub 只接受仓库主页，要求公开仓库有可读取的默认分支根目录 README。
- 视频基于配文、简介与语音转录总结，不理解视频画面；速度受网络、媒体大小和模型性能影响。
- 当前通过飞书表格回顾和管理内容，未提供机器人知识问答或独立搜索页面。

更多检查步骤见[网页部署教程](https://ybh-ybh.github.io/know-where/#quickstart)。

## 数据与隐私

- 提取正文或转录、任务状态、AI 结果和归档引用保存在你配置的 PostgreSQL；归档内容写入飞书。
- 文章、README、图文理解文本和视频转录会按上述输入范围发送给配置的文本模型；小红书和抖音图文图片会发送给配置的视觉模型。
- 本地 `faster-whisper` 不把音频提交给云端 ASR，但转录文本仍会用于模型总结。腾讯 ASR 模式通过你自己的私有 COS Bucket 中转音频，再交给腾讯 ASR 识别。
- 本地临时媒体和 COS 中转对象默认在任务结束后清理，模型缓存保留以便复用。数据库和飞书中的归档内容不随临时文件清理而删除。
- 不要提交 `.env`、API Key 或 Cookie。原始分享链接可能含访问令牌，会作为来源信息归档；分享记录或截图前请检查并脱敏。

## 开发与贡献

本地运行需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和已配置的 PostgreSQL；处理视频还需 FFmpeg。从项目根目录运行：

```sh
uv sync --all-groups --extra local-asr
uv run knowwhere migrate-and-health
uv run knowwhere gateway
```

运行前按部署教程填写 `.env`。首次配置飞书时，保持 Gateway 运行，在控制台完成事件订阅与发布，再在另一个终端执行 `uv run knowwhere init-feishu`。已有应用权限生效后可直接运行。仅使用腾讯 ASR 时，安装命令改为 `uv sync --all-groups`。

| 命令 | 用途 |
| --- | --- |
| `uv run knowwhere health` | 校验配置和数据库连接，不验证飞书或模型服务 |
| `uv run knowwhere migrate-and-health` | 执行数据库迁移并检查连接 |
| `uv run knowwhere init-feishu` | 幂等创建或升级飞书归档空间 |
| `uv run knowwhere process "<URL>"` | 通过 CLI 处理单条链接，仍会调用模型和飞书 |
| `uv run knowwhere gateway` | 启动飞书长连接机器人 |
| `uv run knowwhere fake-smoke` | 不访问网络和数据库的接线冒烟测试 |

提交改动前运行与改动相关的检查；项目检查命令为：

```sh
uv run ruff check .
uv run mypy src/knowwhere
uv run pytest
git diff --check
```

报告问题时请附上平台、部署方式、复现步骤和脱敏错误信息；贡献代码时说明改动目的与验证结果。

```text
src/knowwhere/domain/          领域对象与任务状态机
src/knowwhere/application/     处理流水线与端口
src/knowwhere/adapters/        平台、模型、转录与飞书适配器
src/knowwhere/infrastructure/  数据库模型与仓储
alembic/                       数据库迁移
tests/                         离线回归测试
docs/                          网页教程与项目文档
```

## 更多文档

- [完整部署与维护教程](https://ybh-ybh.github.io/know-where/#quickstart)
- [飞书应用配置指南](docs/FEISHU_SETUP.md)
- [进阶使用说明](docs/ADVANCED_USAGE.md)
- [技术架构文档](docs/TECHNICAL_ARCHITECTURE.md)（含设计目标，实现情况以当前代码为准）
- [交互式数据流图](https://ybh-ybh.github.io/know-where/diagrams/knowwhere-dataflow.html)
- [第三方软件与许可证说明](THIRD_PARTY_NOTICES.md)

## License

本项目基于 [Apache License 2.0](LICENSE) 开源。使用第三方组件时，还需遵守其各自的许可证与使用条款。

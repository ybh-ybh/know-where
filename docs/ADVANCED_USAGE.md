# 知归进阶使用说明

[返回 README](../README.md) · [首次部署教程](https://ybh-ybh.github.io/know-where/#quickstart) · [完整配置模板](../.env.example)

本文用于完成首次部署后的能力扩展。所有命令从项目根目录执行；示例只含占位值，真实凭据保存在本机 `.env`。

## 让配置生效

修改 `.env` 后，本地运行需要重新启动 Gateway 进程；Docker 部署需要重新创建容器。根据首次部署时的数据库路径，始终使用对应的 Compose 文件组合：

| 数据库路径 | Compose 命令前缀 |
| --- | --- |
| 已有数据库 | `docker compose` |
| 新建数据库 | `docker compose -f compose.yml -f compose.postgres.yml` |

### 已有数据库

```sh
docker compose --profile gateway up -d --force-recreate gateway
docker compose logs -f gateway
```

### 新建数据库

```sh
docker compose -f compose.yml -f compose.postgres.yml --profile gateway up -d --force-recreate gateway
docker compose -f compose.yml -f compose.postgres.yml logs -f gateway
```

单纯 `restart` 不会重新加载 `.env`。调整视觉模型、Cookie 等运行配置无需重建镜像；切换 `KW_ASR_PROVIDER` 会改变镜像依赖，必须先使用对应前缀执行 `build app`，再重新创建 Gateway。

重新创建容器前等待当前任务处理结束。不要使用 `down -v`，该选项会删除当前 Compose 项目声明的命名卷，包括模型缓存和临时数据；新建数据库路径下也会删除 PostgreSQL 数据卷。完整停止、备份和更新流程见[部署与维护教程](https://ybh-ybh.github.io/know-where/#quickstart)。

## 图文视觉理解

归档小红书或抖音图文时，需同时配置三项：

```dotenv
# 视觉模型 API Key。
KW_VISION_API_KEY=REPLACE_ME
# OpenAI 兼容接口根地址，不追加 /chat/completions。
KW_VISION_BASE_URL=https://视觉模型服务地址/v1
# 供应商提供的视觉模型 ID。
KW_VISION_MODEL=REPLACE_ME
```

视觉接口使用 OpenAI 兼容消息格式，将有序图片以 Base64 形式发送给视觉模型；生成的图文理解文本随后发送给文本模型分类与总结。配置供应商不支持的思考模式时，可将模板中的 `KW_VISION_THINKING_MODE` 留空。

按前文重新创建 Gateway，再发送一条可访问的图文链接。验证机器人返回的记录包含图文理解结果和摘要；只确认页面能打开不足以证明图片下载和视觉链路成功。

## 视频语音转录

### 默认本地转录

默认使用 CPU 运行 `faster-whisper`，无需腾讯云凭据：

```dotenv
# 本地语音识别供应商。
KW_ASR_PROVIDER=faster_whisper
# 本地 ASR 使用本地临时文件。
KW_TEMP_STORAGE_PROVIDER=local
# 首次使用时下载的模型名称，也可以填写本地模型路径。
KW_FASTER_WHISPER_MODEL=small
# 默认推理设备与计算精度。
KW_FASTER_WHISPER_DEVICE=cpu
KW_FASTER_WHISPER_COMPUTE_TYPE=int8
```

首次处理视频时会下载模型，需要可用网络和磁盘空间。Docker 默认将模型缓存到 `asr-models` 命名卷，媒体工作目录使用 `media-temp` 卷；普通容器重建会复用缓存。模型缓存不参与任务结束后的临时文件清理。

本地运行可用 `KW_FASTER_WHISPER_MODEL_DIR` 指定缓存目录。离线部署可先准备兼容的模型文件，将 `KW_FASTER_WHISPER_MODEL` 指向运行环境可见的模型目录，并设置 `KW_FASTER_WHISPER_ALLOW_MODEL_DOWNLOAD=false`。宿主机目录在容器内不可自动访问，需要另外配置挂载；不要把 Windows 主机路径直接作为容器路径。

CPU 视频转录可能耗时较长；先用短视频验证转录和归档。语音转录不包含画面理解，本地识别得到的文本仍会发送给文本模型总结。

### GPU 使用前提

本地运行在具备可用 NVIDIA CUDA 环境及兼容推理依赖时，可选择 `KW_FASTER_WHISPER_DEVICE=cuda`、`KW_FASTER_WHISPER_COMPUTE_TYPE=float16`。

默认 Docker 镜像和 Compose 未配置 GPU 运行库及设备透传。容器使用 GPU 还需另外准备兼容 CUDA/CTranslate2 的运行环境和设备访问配置；只修改上述两个变量不能完成 GPU 部署。本项目默认教学使用 CPU，不提供已验证的 GPU 版本组合。

### 腾讯云 ASR 与私有 COS

切换腾讯云录音文件识别时，同时填写以下配置：

```dotenv
# 切换为腾讯 ASR，必须配合 COS 中转。
KW_ASR_PROVIDER=tencent
KW_TEMP_STORAGE_PROVIDER=tencent_cos
# 腾讯账号与访问凭据。
KW_TENCENTCLOUD_APP_ID=REPLACE_ME
KW_TENCENTCLOUD_SECRET_ID=REPLACE_ME
KW_TENCENTCLOUD_SECRET_KEY=REPLACE_ME
# 私有 COS Bucket 的地域和名称。
KW_COS_REGION=ap-shanghai
KW_COS_BUCKET=knowwhere-temp-1250000000
```

准备私有 COS Bucket，并给凭据提供所需的 ASR 和 COS 权限。知归上传临时音频、生成短时访问地址供腾讯 ASR 拉取，在任务结束后按清理策略删除中转对象。服务按供应商规则计费，密钥不要提交到仓库。

选择对应数据库路径重建镜像并重新创建 Gateway：

```sh
# 已有数据库。
docker compose build app
docker compose --profile gateway up -d --force-recreate gateway
```

```sh
# 新建数据库。
docker compose -f compose.yml -f compose.postgres.yml build app
docker compose -f compose.yml -f compose.postgres.yml --profile gateway up -d --force-recreate gateway
```

构建过程读取 `KW_ASR_PROVIDER`：腾讯模式不安装 `local-asr` 及其本地推理依赖，切回 `faster_whisper` 时也必须重新构建。本地开发切换到本地转录时执行 `uv sync --all-groups --extra local-asr`；仅用腾讯模式时可执行 `uv sync --all-groups`。

完成后发送短视频，确认转录、摘要及飞书记录可读。若开启默认清理，还应确认 COS 中转对象已清理；只看到 ASR 任务创建成功不代表归档完成。

## 小红书网页会话

默认匿名请求小红书页面。若提示“访问被引导至登录页”，可以配置自己浏览器的有效会话：

1. 在自己的浏览器打开原始分享链接，确认目标笔记正常显示。
2. 打开开发者工具 Network（网络），刷新页面，选择发往 `www.xiaohongshu.com` 或 `xiaohongshu.com` 的页面请求。
3. 复制请求头中 `Cookie` 的值，不含 `Cookie:` 前缀。可以先尝试未登录会话；如果浏览器要求登录，完成登录后重新获取。
4. 在本机 `.env` 填写单行配置，保留分号、等号和空格：

```dotenv
# 仅作字段格式示例，替换成自己浏览器的实际 Cookie。
KW_XIAOHONGSHU_COOKIE='a1=你的实际值; web_session=你的实际值'
```

Cookie 只用于上述两个页面主机，不发送到 `xhslink.cn`、`xhslink.com` 或媒体 CDN。不要把 Cookie 发给机器人或提交仓库。程序会隐藏配置显示中的 Cookie，页面请求错误也会对完整 URL 和访问令牌做脱敏。

原始分享链接中的 `xsec_token`、`xsec_source` 会保留用于访问，并作为原始链接归档；去重使用的规范链接不含这些参数。分享记录或截图时请检查原始链接中的访问令牌。

如果首次升级包含新的 Cookie 支持代码，先重新构建镜像。之后只更新 Cookie 无需重建镜像，但仍需按[配置生效方式](#让配置生效)重新创建 Gateway；本地运行则重启进程。更新后把原始分享链接作为一条新消息发送。

Cookie 不保证访问成功。配置后仍失败时，先在浏览器确认同一链接可访问，再检查会话、部署网络和目标链接的访问条件。登录跳转不能单独证明 Cookie 过期；限流时稍后再试，验证或安全拦截需先在浏览器处理。程序不会自动登录、执行浏览器验证或循环重试这些访问错误。

HTTP 200 也不代表内容已提取成功。验证应覆盖目标笔记、媒体下载、视觉理解或转录，以及最终飞书归档。

## 临时文件与媒体限制

| 配置 | 作用 |
| --- | --- |
| `KW_TEMP_LOCAL_ROOT` | 媒体下载、FFmpeg 与本地 ASR 的工作目录 |
| `KW_TEMP_DELETE_AFTER_PROCESS` | 成功或失败后是否清理临时产物，默认 `true` |
| `KW_FFMPEG_PATH` | FFmpeg 可执行文件名或路径；默认容器内已安装 |
| `KW_DOUYIN_REQUEST_TIMEOUT_SECONDS`、`KW_XIAOHONGSHU_REQUEST_TIMEOUT_SECONDS`、`KW_BILIBILI_REQUEST_TIMEOUT_SECONDS` | 平台请求超时 |
| `KW_DOUYIN_MAX_IMAGE_BYTES`、`KW_XIAOHONGSHU_MAX_IMAGE_BYTES` | 单张图片大小上限 |
| `KW_DOUYIN_MAX_IMAGES`、`KW_XIAOHONGSHU_MAX_IMAGES` | 单作品图片数量上限 |
| `KW_DOUYIN_MAX_VIDEO_BYTES`、`KW_XIAOHONGSHU_MAX_VIDEO_BYTES` | 单个视频大小上限 |
| `KW_BILIBILI_MAX_AUDIO_BYTES` | B站单条音频大小上限 |

`KW_TEMP_DELETE_AFTER_PROCESS=false` 可保留临时产物排查问题，排查后应自行清理。清理临时文件不会清除模型缓存、PostgreSQL 或飞书归档。参数范围、默认值和其他模型设置见[配置模板](../.env.example)。

## 使用替代配置文件

应用默认读取当前工作目录的 `.env`。本地 CLI 可显式选择一个替代文件：

```powershell
uv run knowwhere health --env-file D:\config\knowwhere.env
```

文件选择顺序为：显式 `--env-file` 参数 → `KW_ENV_FILE` 环境变量 → 当前工作目录 `.env`。这是选择一个文件的顺序；不会再合并其他文件补齐缺项。进程环境变量始终覆盖所选文件中的同名配置。

相对文件路径按当前工作目录解析。从项目根目录运行也能让迁移命令找到仓库的 `alembic/` 目录。

Docker Compose 的两种配置读取需分开理解：

- Compose 使用 `.env` 或自身的 `--env-file` 参数展开构建参数和数据库密码等变量。
- 本仓库 `compose.yml` 中的服务 `env_file: .env` 决定向容器注入哪个文件；只改变 Compose 的 `--env-file` 不会自动改写这项设置。

应用的 `--env-file` 是容器内路径，主机上的配置文件需先挂载才能读取。需要自定义部署时，请同时核对 Compose 插值来源、服务 `env_file` 与容器实际环境，避免构建和运行读取不同配置。

## 不启动机器人处理链接

在飞书权限、模型与数据库都已配置后，可通过 CLI 直接归档：

```sh
# 已有数据库。
docker compose run --rm app process "https://github.com/ybh-ybh/know-where"
```

```sh
# 新建数据库。
docker compose -f compose.yml -f compose.postgres.yml run --rm app process "https://github.com/ybh-ybh/know-where"
```

CLI 会执行真实提取、模型调用与飞书写入，并非离线检查；也不会触发机器人私聊中的用户授权步骤。首次个人使用仍应通过机器人完成链接归档，确认自己能访问记录。

更多排错见[网页部署教程](https://ybh-ybh.github.io/know-where/#quickstart)。

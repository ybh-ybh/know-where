# 知归飞书应用配置指南

> 目标：使用飞书企业自建应用，通过长连接接收个人私聊链接，自动创建并维护多维表格、字段和视图。

[返回 README](../README.md) · [完整部署教程](https://ybh-ybh.github.io/know-where/#quickstart)

本指南说明飞书侧配置。请先按完整教程准备数据库和模型配置；初次部署的顺序为：创建应用并申请权限 → 启动 Gateway → 配置长连接并发布 → 初始化归档空间 → 私聊发送链接。

## 1. 需要提供的凭据

只需要以下两个飞书应用凭据：

| 配置项 | 从哪里获取 | 用途 |
| --- | --- | --- |
| `KW_FEISHU_APP_ID` | 飞书开放平台应用详情的“凭证与基础信息” | 标识知归应用 |
| `KW_FEISHU_APP_SECRET` | 同上 | 换取应用身份访问凭证 |

企业自建应用的 App ID 通常以 `cli_` 开头。知归会通过应用凭据获取 `tenant_access_token`，无需手工调用接口或填写访问令牌。

请把它们填写在项目根目录 `.env`，不要粘贴到聊天、Issue、提交记录或日志中：

```dotenv
# 飞书企业自建应用的 App ID。
KW_FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
# 飞书企业自建应用的 App Secret。
KW_FEISHU_APP_SECRET=请填写真实值
```

以下信息不需要人工提供：

- `tenant_access_token`：服务通过 App ID 和 App Secret 自动获取。
- `Verification Token`、`Encrypt Key`、公网回调 URL：知归首版使用长连接接收事件，不使用 Webhook 回调。
- 用户 `open_id`：从私聊消息事件中取得，有效内容链接触发归档空间授权。
- 多维表格 `app_token`、`table_id`、字段 ID、视图 ID：由系统创建后持久化。
- 飞书文件夹 Token：无需预先创建文件夹；应用创建归档资源，用户首次发送受支持链接时再按第 2.5 节授予访问权限。

## 2. 飞书侧操作步骤

### 2.1 创建企业自建应用

1. 打开[飞书开放平台开发者后台](https://open.feishu.cn/app)。
2. 创建“企业自建应用”，应用名称建议填写“知归”。
3. 在“添加应用能力”中启用“机器人”。
4. 在“凭证与基础信息”复制 App ID 和 App Secret，按第 1 节写入本机 `.env`。

### 2.2 申请最小权限

在“权限管理”中搜索并申请以下权限：

| 权限名称 | 权限标识 | 为什么需要 |
| --- | --- | --- |
| 读取用户发给机器人的单聊消息 | `im:message.p2p_msg:readonly` | 接收个人发送的链接；不申请群消息权限 |
| 以应用的身份发消息 | `im:message:send_as_bot` | 回复接收、归档完成和失败状态 |
| 查看、评论、编辑和管理多维表格 | `bitable:app` | 创建多维表格、表、字段、视图、记录，并为个人用户授权 |
| 查看云空间中文件元数据 | `drive:drive.metadata:readonly` | 读取归档空间的真正所有者，只在 owner 仍是应用时执行转移 |
| 查看、评论、编辑和管理文档 | `docs:doc` | 管理归档所需的飞书文档能力与访问权限 |

如果飞书控制台把文档权限拆分为更细粒度权限，则至少选择“创建/编辑新版文档”和“添加云文档协作者”；后者的权限标识为 `docs:permission.member:create`。不需要申请通讯录、群消息、日历或用户手机号权限。

### 2.3 配置事件订阅

1. 完成数据库和模型配置后，按[部署教程](https://ybh-ybh.github.io/know-where/#quickstart)构建镜像、执行迁移检查并启动 Gateway。
2. 保持 Gateway 运行，打开飞书控制台“事件与回调”或“事件订阅”。
3. 选择“使用长连接接收事件”，保存并确认连接在线。若提示未建立连接，先检查 Gateway 日志和应用凭据。
4. 添加事件“接收消息”，事件标识为 `im.message.receive_v1`。

无需配置公网请求地址，长连接由 Gateway 使用官方 SDK 建立。入口在进程内按 `message_id` 过滤重复投递；内容归档另按规范链接与内部内容 ID 复用已有记录。

### 2.4 限制为个人使用

1. 创建应用版本。
2. 把应用“可用范围”只设置为你本人，不要设置为全员。
3. 提交并发布版本；如果所在企业要求管理员审核，需要由管理员批准和安装。

应用可用范围是个人使用的访问边界。当前代码没有锁定首位用户的白名单：可用范围内其他用户发送受支持链接时，也可能获得同一归档空间的完整权限。群聊消息不会创建任务，因此个人部署请务必只开放给自己。

### 2.5 初始化归档空间与首次授权

应用版本已发布、权限生效后，执行初始化命令。根据你的数据库路径，选择其中一条：

```sh
# 使用已有数据库。
docker compose run --rm app init-feishu
```

```sh
# 使用可选 PostgreSQL 容器。
docker compose -f compose.yml -f compose.postgres.yml run --rm app init-feishu
```

初始化会幂等创建或升级多维表格、字段和默认视图，输出 `workspace_url` 和 `schema_version`。输出链接只表示资源已初始化，用户访问授权还需下面的有效私聊触发：

1. 在飞书中打开机器人私聊，发送 `https://github.com/ybh-ybh/know-where`。
2. 收到“知归已收到，正在提取、总结并归档。”后，系统读取发送者 `open_id` 并授予归档空间访问权限。
3. 若归档空间仍属于应用，系统将所有权转给该用户，同时保留应用的管理权；若已归其他用户所有，则为当前用户添加或更新完整访问权限。
4. 等待“归档完成”回复，打开记录，核对原始链接、标题、分类和摘要，确认自己可以访问归档内容。

单独发送“绑定知归”不会触发授权或归档。初始化可以重复执行，已有资源会被复用；Gateway 启动仅执行数据库迁移并建立长连接，不等同于完成飞书资源初始化或首条归档验收。

首次请使用文字内容验证。图文需要视觉模型，视频默认本地转录；腾讯 ASR 与私有 COS 等扩展配置见[进阶使用说明](ADVANCED_USAGE.md)。

## 3. 凭据安全检查

- `.env` 必须被 `.gitignore` 和 `.dockerignore` 忽略；仓库只提交 `.env.example`。
- 如果真实密钥曾进入 Git 提交、GitHub、聊天、截图或日志，应立即在对应控制台轮换，不要只删除文件。
- Docker Compose 通过 `env_file` 在运行时注入变量；生产环境可改用 Docker Secret 或外部密钥服务。应用日志不得输出整个配置对象。
- 飞书 App Secret 和腾讯云 SecretKey 应分别轮换，不能复用。

## 4. 官方依据

- [飞书接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)
- [飞书发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create)
- [飞书创建多维表格](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app/create)
- [飞书新增字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/create)
- [飞书增加协作者权限](https://open.feishu.cn/document/server-docs/docs/permission/permission-member/create)

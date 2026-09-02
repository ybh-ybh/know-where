# 知归飞书应用配置指南

> 目标：使用飞书企业自建应用，通过长连接接收个人私聊消息，由系统自动创建多维表格、字段、视图和全文文档。

## 1. 需要提供的凭据

只需要以下两个飞书应用凭据：

| 配置项 | 从哪里获取 | 用途 |
| --- | --- | --- |
| `KW_FEISHU_APP_ID` | 飞书开放平台应用详情的“凭证与基础信息” | 标识知归应用 |
| `KW_FEISHU_APP_SECRET` | 同上 | 换取应用身份访问凭证 |

企业自建应用的 App ID 通常以 `cli_` 开头。保存后应先调用“自建应用获取 tenant_access_token”接口验证；只有返回 `code=0` 才表示 App ID/App Secret 配对有效。

请把它们填写在本机根目录 `.env`，不要粘贴到聊天、Issue、提交记录或日志中：

```dotenv
# 飞书企业自建应用的 App ID。
KW_FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
# 飞书企业自建应用的 App Secret。
KW_FEISHU_APP_SECRET=请填写真实值
```

以下信息不需要人工提供：

- `tenant_access_token`：服务通过 App ID 和 App Secret 自动获取并刷新。
- `Verification Token`、`Encrypt Key`、公网回调 URL：知归首版使用长连接接收事件，不使用 Webhook 回调。
- 用户 `open_id`：从第一条合法私聊消息事件取得。
- 多维表格 `app_token`、`table_id`、字段 ID、视图 ID：由系统创建后持久化。
- 飞书文件夹 Token：首版由应用在其云空间根目录创建归档资源，再把绑定用户设为可管理协作者。

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
| 以应用的身份发消息 | `im:message:send_as_bot` | 回复接收、成功、重复和失败状态 |
| 查看、评论、编辑和管理多维表格 | `bitable:app` | 创建多维表格、表、字段、视图、记录，并为个人用户授权 |
| 查看、评论、编辑和管理文档 | `docs:doc` | 创建或更新超长正文文档，并管理其访问权限 |

如果飞书控制台把文档权限拆分为更细粒度权限，则至少选择“创建/编辑新版文档”和“添加云文档协作者”；后者的权限标识为 `docs:permission.member:create`。不需要申请通讯录、群消息、日历或用户手机号权限。

### 2.3 配置事件订阅

1. 打开“事件与回调”或“事件订阅”。
2. 订阅方式选择“使用长连接接收事件”。
3. 添加事件“接收消息”，事件标识为 `im.message.receive_v1`。
4. 不配置公网请求地址；长连接由知归 `gateway` 进程使用官方 SDK 建立。

飞书官方说明同一消息在特殊情况下可能被重复推送，因此知归使用事件体中的 `message_id` 去重，`event_id` 只用于链路追踪。

### 2.4 限制为个人使用

1. 创建应用版本。
2. 把应用“可用范围”只设置为你本人，不要设置为全员。
3. 提交并发布版本；如果所在企业要求管理员审核，需要由管理员批准和安装。

可用范围是首次绑定的安全边界：系统收到范围内第一条私聊消息后保存该用户 `open_id`，此后只接受同一用户的私聊消息，群聊消息不会创建任务。

### 2.5 首次绑定与自动建表

运行时代码完成并启动后：

1. 在飞书中打开“知归”机器人私聊。
2. 发送一条“绑定知归”消息或第一条内容链接。
3. 系统取得发送者 `open_id`，创建“知归”多维表格、内容库字段和默认视图。
4. 系统把该用户添加为多维表格的可管理协作者，并回复表格链接。
5. 后续容器重启只校验和迁移既有资源，不会重复创建表格。

当前实现已支持文章、GitHub README、抖音图文和抖音视频，并已接入飞书归档初始化与长连接 Gateway；启动后可以直接执行 2.5。视频需要腾讯云 COS/ASR 可用额度，图文需要额外配置视觉模型。

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

"""飞书长连接消息入口。"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx

from knowwhere.composition import Runtime

if TYPE_CHECKING:
    from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import (
        P2ImMessageReceiveV1,
    )

# 内容链接匹配只接受当前已接入的平台和路径形式。
SUPPORTED_ARTICLE_URL_PATTERN = re.compile(
    r"https://(?:"
    r"mp\.weixin\.qq\.com/[^\s<>\"']+|"
    r"(?:www\.)?juejin\.cn/post/\d+(?:\?[^\s<>\"']*)?|"
    r"v\.douyin\.com/[A-Za-z0-9_-]+/?(?:\?[^\s<>\"']*)?|"
    r"www\.douyin\.com/(?:video|note)/\d+(?:\?[^\s<>\"']*)?|"
    r"www\.iesdouyin\.com/share/(?:video|note)/\d+/?(?:\?[^\s<>\"']*)?|"
    r"(?:www\.)?github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/"
    r"[A-Za-z0-9._-]{1,100}/?(?:[?#][^\s<>\"']*)?"
    r"(?![A-Za-z0-9._/-])"
    r")"
)


# 递归读取飞书 text/post/card JSON 中的字符串值。
def _string_values(value: object) -> list[str]:
    """把任意消息 JSON 展平成字符串集合。"""

    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        # 映射的所有嵌套值。
        nested_values = tuple(value.values())
        return [text for nested_value in nested_values for text in _string_values(nested_value)]
    if isinstance(value, list):
        return [text for item in value for text in _string_values(item)]
    return []


# 从飞书 text/post/card 消息 JSON 中提取第一个受支持内容链接。
def extract_supported_url(message_content: str) -> str | None:
    """解析飞书消息正文。"""

    try:
        # 飞书文本消息载荷。
        payload = json.loads(message_content)
    except json.JSONDecodeError:
        # 非标准消息也允许直接在原文中寻找链接。
        payload = message_content
    for text in _string_values(payload):
        # 当前字符串中的第一个受支持链接。
        match = SUPPORTED_ARTICLE_URL_PATTERN.search(text)
        if match is not None:
            return match.group(0).rstrip('。；，,;.:)]}"')
    return None


# 飞书回复客户端只负责消息 API，不参与内容归档。
class FeishuReplyClient:
    """回复原飞书消息。"""

    # 保存应用凭据和可注入客户端。
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化回复客户端。"""

        self._app_id = app_id
        self._app_secret = app_secret
        self._client = client or httpx.Client(timeout=httpx.Timeout(30.0))

    # 回复一条纯文本消息。
    def reply_text(self, message_id: str, text: str) -> None:
        """向原消息发送可见回执。"""

        # 租户令牌响应。
        token_response = self._client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        token_response.raise_for_status()
        # 鉴权响应载荷。
        token_payload = token_response.json()
        if int(token_payload.get("code", 0)) != 0:
            raise RuntimeError(f"飞书回复鉴权失败: {token_payload.get('msg', 'unknown')}")
        # 租户访问令牌。
        token = str(token_payload.get("tenant_access_token", ""))
        if not token:
            raise RuntimeError("飞书回复鉴权响应缺少 tenant_access_token")
        # 回复响应。
        response = self._client.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )
        response.raise_for_status()
        # 回复业务载荷。
        payload = response.json()
        if int(payload.get("code", 0)) != 0:
            raise RuntimeError(f"飞书消息回复失败: {payload.get('msg', 'unknown')}")


# 可调用处理器把状态与分支拆出工厂函数，保持 WebSocket 回调轻量。
class _MessageHandler:
    """校验飞书私聊事件并调度后台工作。"""

    # 保存运行时、回复客户端、线程工厂和进程内去重状态。
    def __init__(
        self,
        runtime: Runtime,
        reply_client: FeishuReplyClient,
        thread_factory: Callable[..., threading.Thread],
    ) -> None:
        """初始化消息处理器。"""

        # 业务运行时依赖。
        self._runtime = runtime
        # 飞书消息回复客户端。
        self._reply_client = reply_client
        # 可注入线程工厂。
        self._thread_factory = thread_factory
        # 单进程内已接收 message_id，抵御 WebSocket 短时重复投递。
        self._seen_message_ids: set[str] = set()
        # 并发访问去重集合的锁。
        self._seen_lock = threading.Lock()

    # 原子登记新的飞书消息 ID。
    def _mark_seen(self, message_id: str) -> bool:
        """返回消息是否为本进程首次接收。"""

        with self._seen_lock:
            if message_id in self._seen_message_ids:
                return False
            self._seen_message_ids.add(message_id)
        return True

    # 在后台线程处理一个已经校验的链接。
    def _process_message(self, message_id: str, open_id: str, url: str) -> None:
        """执行归档并回复终态。"""

        try:
            self._reply_client.reply_text(message_id, "知归已收到，正在提取、总结并归档。")
            # 首次私聊用户获得系统创建工作区的完整权限。
            self._runtime.feishu.grant_full_access(open_id)
            # 真实处理结果。
            result = self._runtime.pipeline.process(url)
            self._reply_client.reply_text(message_id, f"归档完成：{result.record_url}")
        except Exception as error:
            # 回执只包含异常类型和受限消息，不返回堆栈、请求头或密钥。
            safe_message = f"{type(error).__name__}: {str(error)[:300]}"
            try:
                self._reply_client.reply_text(message_id, f"处理失败：{safe_message}")
            except Exception:
                # 回复失败时 SDK 日志仍能保留事件处理状态，不能让线程再次抛出。
                return

    # 对不含受支持链接的私聊给出明确提示，避免静默无响应。
    def _reply_unsupported(self, message_id: str) -> None:
        """回复当前 MVP 支持边界。"""

        try:
            self._reply_client.reply_text(
                message_id,
                "知归已收到消息，但没有识别到微信公众号、稀土掘金、GitHub 或抖音链接。"
                "请直接粘贴受支持的文章、仓库或抖音图文/视频链接。",
            )
        except Exception:
            return

    # 启动守护线程，避免阻塞飞书 WebSocket 回调。
    def _start_worker(
        self,
        target: Callable[..., None],
        args: tuple[str, ...],
        name: str,
    ) -> None:
        """创建并启动后台工作线程。"""

        # 当前后台工作线程。
        worker = self._thread_factory(target=target, args=args, daemon=True, name=name)
        worker.start()

    # WebSocket 收到消息后的快速回调。
    def __call__(self, event: P2ImMessageReceiveV1) -> None:
        """校验私聊 text 事件并启动后台线程。"""

        # 飞书事件主体。
        event_data = event.event
        if event_data is None or event_data.message is None or event_data.sender is None:
            return
        # 原始消息对象。
        message = event_data.message
        if message.chat_type != "p2p":
            return
        # 飞书 message_id 是官方要求的消息去重键。
        message_id = message.message_id or ""
        # 发送者 ID 对象。
        sender_id = event_data.sender.sender_id
        # 个人用户 open_id。
        open_id = sender_id.open_id if sender_id is not None else ""
        if not message_id or not open_id or not self._mark_seen(message_id):
            return
        # 支持的内容链接。
        url = extract_supported_url(message.content or "")
        # 只输出安全元数据，绝不记录消息正文、用户 open_id 或完整 message_id。
        print(
            "gateway_event "
            f"message_suffix={message_id[-8:]} "
            f"chat_type={message.chat_type} "
            f"message_type={message.message_type} "
            f"url_found={url is not None}",
            flush=True,
        )
        if url is None:
            self._start_worker(
                self._reply_unsupported,
                (message_id,),
                f"knowwhere-unsupported-{message_id[-8:]}",
            )
            return
        self._start_worker(
            self._process_message,
            (message_id, open_id, url),
            f"knowwhere-{message_id[-8:]}",
        )


# 创建事件处理回调并把耗时任务移出 WebSocket 应答线程。
def build_message_handler(
    runtime: Runtime,
    reply_client: FeishuReplyClient,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
) -> Callable[[P2ImMessageReceiveV1], None]:
    """构造私聊文本事件处理器。"""

    return _MessageHandler(runtime, reply_client, thread_factory)


# 启动飞书官方 SDK 长连接客户端。
def run_gateway(runtime: Runtime) -> None:
    """持续接收飞书私聊消息。"""

    # 官方 SDK 很大，仅在真正启动 Gateway 时延迟导入，避免拖慢 CLI 和测试。
    import lark_oapi as lark

    # 飞书回复 API 客户端。
    reply_client = FeishuReplyClient(
        runtime.settings.feishu.app_id,
        runtime.settings.feishu.app_secret.get_secret_value(),
    )
    # 消息事件分发器；长连接模式不需要 Verification Token 和 Encrypt Key。
    event_handler = (
        lark.EventDispatcherHandler.builder("", "", lark.LogLevel.WARNING)
        .register_p2_im_message_receive_v1(build_message_handler(runtime, reply_client))
        .build()
    )
    # 飞书 WebSocket 客户端。
    ws_client = lark.ws.Client(
        runtime.settings.feishu.app_id,
        runtime.settings.feishu.app_secret.get_secret_value(),
        log_level=lark.LogLevel.WARNING,
        event_handler=event_handler,
    )
    ws_client.start()

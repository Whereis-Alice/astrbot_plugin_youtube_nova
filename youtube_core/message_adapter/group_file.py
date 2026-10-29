"""OneBot QQ 文件投递：群文件与私聊文件共用回执等待和错误处理。"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from astrbot.api.event import AstrMessageEvent

from ..logger import logger

_INVALID_FILE_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class GroupFileUploadError(RuntimeError):
    """文件投递不可用或执行失败（保留历史类名）。"""


class GroupFileUploadUnconfirmed(GroupFileUploadError):
    """未收到完成回执，协议端可能仍在上传，不能按失败重试。"""


def _scoped_upload_api(bot: Any, timeout_seconds: float) -> Any:
    """复用 aiocqhttp 连接，仅给本次调用设置超时，不修改共享客户端。"""
    try:
        from aiocqhttp import CQHttp
        from aiocqhttp.api_impl import HttpApi, UnifiedApi, WebSocketReverseApi
    except ImportError:
        return None
    if not isinstance(bot, CQHttp):
        return None
    if getattr(bot.call_action, "__func__", None) is not CQHttp.call_action:
        return None
    api = vars(bot).get("_api")
    if type(api) is not UnifiedApi:
        return None
    transports = {}
    for attr, expected_type in (
        ("_wsr_api", WebSocketReverseApi),
        ("_http_api", HttpApi),
    ):
        transport = vars(api).get(attr)
        if transport is None:
            continue
        # 未识别的扩展实现保留原调用路径，避免绕过第三方包装。
        if type(transport) is not expected_type:
            return None
        state = vars(transport)
        try:
            if expected_type is WebSocketReverseApi:
                transports[attr] = WebSocketReverseApi(
                    state["_api_clients"], state["_event_clients"], timeout_seconds
                )
            else:
                transports[attr] = HttpApi(
                    state["_api_root"], state["_access_token"], timeout_seconds
                )
        except KeyError:
            return None
    return UnifiedApi(
        http_api=transports.get("_http_api"),
        wsr_api=transports.get("_wsr_api"),
    )


def _receipt_is_uncertain(exc: Exception) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    try:
        from aiocqhttp.exceptions import NetworkError
    except ImportError:
        pass
    else:
        if isinstance(exc, NetworkError):
            return True
    text = str(exc).lower()
    return any(
        word in text
        for word in (
            "websocket api call timeout",
            "timed out",
            "timeout",
            "超时",
            "connection closed",
            "connection reset",
        )
    )


def build_group_file_name(
    metadata: dict,
    file_path: str,
    *,
    video_index: int = 0,
    video_count: int = 1,
) -> str:
    """生成可读且能被 QQ 接受的文件名，不改变磁盘上的缓存文件。"""
    title = str(metadata.get("title") or "YouTube 视频").strip()
    title = _INVALID_FILE_NAME.sub("_", title).strip(" ._") or "YouTube 视频"
    if video_count > 1:
        title += f" [{video_index + 1}]"
    suffix = Path(file_path).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = ".mp4"
    max_stem_length = max(1, 180 - len(suffix))
    return title[:max_stem_length].rstrip(" .") + suffix


class GroupFileUploader:
    """根据当前 QQ 会话选择文件上传 action；保留历史内部名称。"""

    def __init__(
        self,
        timeout_seconds: int = 600,
        *,
        source_mode: str = "auto",
        callback_api_base: str = "",
    ):
        self.timeout_seconds = max(30, min(3600, int(timeout_seconds or 600)))
        self.source_mode = (
            source_mode if source_mode in {"auto", "local", "http"} else "auto"
        )
        self.callback_api_base = str(callback_api_base or "").strip().rstrip("/")

    async def _upload_source(self, path: str) -> str:
        """使用宿主短期文件服务跨容器传递文件，避免把整个视频编码进 API。"""
        if self.source_mode == "local":
            return path
        try:
            from astrbot.core import astrbot_config, file_token_service
        except ImportError as exc:
            if self.source_mode == "auto":
                return path
            raise GroupFileUploadError(
                "当前 AstrBot 版本不支持文件 Token 服务"
            ) from exc
        callback_base = self.callback_api_base or str(
            astrbot_config.get("callback_api_base") or ""
        ).strip().rstrip("/")
        if not callback_base:
            if self.source_mode == "auto":
                return path
            raise GroupFileUploadError(
                "HTTP 文件上传需要配置协议端可访问的 callback_api_base"
                "（AstrBot 全局配置或插件文件服务回调地址）"
            )
        try:
            parsed = urlsplit(callback_base)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            # 同时验证端口，避免畸形 URL 注册无用令牌。
            _port = parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise GroupFileUploadError("文件服务回调地址必须是有效的 HTTP(S) 基础地址")
        # File.register_to_file_service() 使用同一服务但只有默认 300 秒。
        # 群文件可能等待更久；上传超时后令牌仍与缓存保留期保持一致。
        token = await file_token_service.register_file(
            path,
            timeout=self.timeout_seconds + max(600, self.timeout_seconds),
        )
        return f"{callback_base}/api/file/{token}"

    @staticmethod
    def _safe_error_detail(error: Any, source: str) -> str:
        """协议端的下载错误可能回显含凭据的文件服务 URL。"""
        text = str(error or "接口拒绝")
        if source.startswith(("http://", "https://")):
            text = text.replace(source, "<文件服务地址>")
        return text

    @staticmethod
    def _target(event: AstrMessageEvent) -> tuple[str, dict, str] | None:
        """只从当前事件取收件人，群消息不会因用户 ID 存在而转入私聊。"""
        try:
            group_id = str(event.get_group_id() or "").strip()
            if group_id:
                if not group_id.isdigit():
                    return None
                return "upload_group_file", {"group_id": int(group_id)}, "群文件"
            if not event.is_private_chat():
                return None
            user_id = str(event.get_sender_id() or "").strip()
            if user_id.isdigit():
                return "upload_private_file", {"user_id": user_id}, "私聊文件"
        except Exception:
            pass
        return None

    @classmethod
    def can_upload(cls, event: AstrMessageEvent) -> bool:
        target = cls._target(event)
        if target is None:
            return False

        get_platform_name = getattr(event, "get_platform_name", None)
        if callable(get_platform_name):
            try:
                platform_name = str(get_platform_name() or "").strip().lower()
            except Exception:
                return False
            if platform_name and platform_name != "aiocqhttp":
                return False

        bot = getattr(event, "bot", None)
        if bot is None:
            return False
        api = getattr(bot, "api", None)
        return bool(
            callable(getattr(bot, "call_action", None))
            or callable(getattr(api, "call_action", None))
            or callable(getattr(bot, target[0], None))
        )

    async def _call_action(
        self, event: AstrMessageEvent, action: str, payload: dict
    ) -> Any:
        bot = getattr(event, "bot", None)
        api = _scoped_upload_api(bot, self.timeout_seconds)
        if api is not None:
            get_self_id = getattr(event, "get_self_id", None)
            self_id = str(get_self_id() or "") if callable(get_self_id) else ""
            if self_id:
                payload = {**payload, "self_id": self_id}
            return await api.call_action(action, **payload)
        call_action = getattr(bot, "call_action", None)
        if callable(call_action):
            return await call_action(action, **payload)

        api = getattr(bot, "api", None)
        api_call_action = getattr(api, "call_action", None)
        if callable(api_call_action):
            return await api_call_action(action, **payload)

        upload = getattr(bot, action, None)
        if callable(upload):
            return await upload(**payload)
        raise GroupFileUploadError(f"当前 OneBot 客户端未暴露 {action}")

    async def upload(
        self,
        event: AstrMessageEvent,
        file_path: str,
        file_name: str,
    ) -> Any:
        if not self.can_upload(event):
            raise GroupFileUploadError("当前会话不支持 QQ 文件上传")
        path = os.path.abspath(str(file_path or ""))
        if not os.path.isfile(path):
            raise GroupFileUploadError("待上传的视频文件不存在")

        target = self._target(event)
        if target is None:
            raise GroupFileUploadError("无法确定文件接收会话")
        action, recipient, label = target
        source = path
        try:
            source = await self._upload_source(path)
            payload = {
                **recipient,
                "file": source,
                "name": str(file_name or Path(path).name),
            }
            source_label = "HTTP 文件服务" if source != path else "共享本地路径"
            logger.info(
                f"开始上传{label}: {payload['name']}，来源 {source_label}，"
                f"回执等待上限 {self.timeout_seconds} 秒"
            )
            result = await asyncio.wait_for(
                self._call_action(event, action, payload),
                timeout=self.timeout_seconds,
            )
            # 某些适配器直接返回 OneBot 响应包装，不会抛 ActionFailed。
            if isinstance(result, dict):
                status = str(result.get("status") or "").lower()
                retcode = str(result.get("retcode", "0"))
                if status == "failed" or retcode not in {"0", "1"}:
                    detail = (
                        result.get("wording") or result.get("message") or "接口拒绝"
                    )
                    detail = self._safe_error_detail(detail, source)
                    raise GroupFileUploadError(f"{label}上传失败：{detail}")
                if status == "async" or retcode == "1":
                    raise GroupFileUploadUnconfirmed(
                        f"协议端已受理，尚未确认{label}上传完成"
                    )
            # aiocqhttp 返回 data 并丢弃包装中的 status；异步受理空回执
            # 因而会变成 None。不能把这种回复当作文件已上传。
            if result is None or result == {}:
                raise GroupFileUploadUnconfirmed(
                    f"未收到{label}完成回执，请稍后查看当前会话，暂勿重复解析"
                )
            return result
        except asyncio.CancelledError:
            raise
        except GroupFileUploadError:
            raise
        except Exception as exc:
            if _receipt_is_uncertain(exc):
                raise GroupFileUploadUnconfirmed(
                    f"{label}上传回执未确认，文件可能仍在上传或已发送，请稍后查看当前会话"
                ) from exc
            detail = self._safe_error_detail(exc, source)
            raise GroupFileUploadError(f"{label}上传失败：{detail}") from exc

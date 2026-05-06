import os
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from codec import ProtoCodec


PLUGIN_NAME = "astrbot_plugin_bubble_transform"
GROUP_MESSAGE_CMD = "trpc.msg.register_proxy.RegisterProxy.SsoGetGroupMsg"
SEND_MESSAGE_CMD = "MessageSvc.PbSendMsg"
MESSAGE_ELEM_PATH = "3.6.3.1.2"
VIDEO_TYPE_PATH = "53.3"
VIDEO_MSG_TYPE = 21
BUBBLE_MSG_TYPE = 14
SUPPORTED_COMMANDS = {"#转泡泡", "/转泡泡", "转泡泡"}


class BubbleTransformError(RuntimeError):
    """Expected protocol/packet parsing errors for user-facing failure messages."""


@register(PLUGIN_NAME, "Taylor", "NapCat OneBot QQ 视频转泡泡", "0.2.0")
class BubbleTransformPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def transform(self, event: AstrMessageEvent):
        """回复视频消息，发送 #转泡泡，将 QQ 视频 pb 元素改为泡泡元素后重发。"""
        if event.message_str.strip() not in SUPPORTED_COMMANDS:
            return

        event.stop_event()

        reply_id = self._extract_reply_id(event)
        if not reply_id:
            yield event.plain_result("请回复一条视频消息后再发送 #转泡泡")
            return

        client = getattr(event, "bot", None)
        if not client or not getattr(client, "api", None):
            yield event.plain_result("没有拿到 OneBot API client，无法调用 NapCat 接口。")
            return

        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result("目前只支持群聊视频转泡泡。")
            return

        try:
            raw_message = await self._get_group_raw_message(client, int(group_id), int(reply_id))
            elem_array = self._get_by_path(raw_message, MESSAGE_ELEM_PATH)
            if not isinstance(elem_array, list):
                yield event.plain_result("没有在被回复消息里找到 QQ 原始视频 elem。")
                return

            if not self._convert_video_to_bubble(elem_array):
                yield event.plain_result("未找到可转换的视频元素，可能不是视频消息或已经是泡泡。")
                return

            await self._send_elem(client, int(group_id), elem_array)
            logger.info("[转泡泡] 转换并发送成功")
        except BubbleTransformError as exc:
            logger.warning(f"[转泡泡] 协议处理失败: {exc}")
            yield event.plain_result(str(exc))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            logger.exception(f"[转泡泡] 数据解析失败: {exc}")
            yield event.plain_result("转换失败，收到的协议数据结构和预期不一致。")
        except Exception as exc:
            logger.exception(f"[转泡泡] 处理失败: {exc}")
            yield event.plain_result("转换失败，请确认 NapCat 支持 /send_packet，且回复的是群视频消息。")

    async def _get_group_raw_message(self, client: Any, group_id: int, message_id: int) -> dict[str, Any]:
        msg = await client.api.call_action("get_msg", message_id=message_id)
        msg = self._unwrap_onebot_response(msg)
        if not isinstance(msg, dict):
            raise BubbleTransformError("转换失败，get_msg 返回格式异常。")
        seq = int(msg.get("real_seq") or 0)
        if not seq:
            raise BubbleTransformError("转换失败，未拿到 real_seq，请更新 NapCat 后重试。")

        packet = {
            "1": {
                "1": group_id,
                "2": seq,
                "3": seq,
            },
            "2": True,
        }
        return await self._send_packet(
            client,
            GROUP_MESSAGE_CMD,
            packet,
        )

    async def _send_elem(self, client: Any, group_id: int, elem_array: list[dict[str, Any]]) -> dict[str, Any]:
        packet = {
            "1": {
                "2": {
                    "1": group_id,
                },
            },
            "2": {
                "1": 1,
                "2": 0,
                "3": 0,
            },
            "3": {
                "1": {
                    "2": elem_array,
                },
            },
            "4": self._random_uint32(),
            "5": self._random_uint32(),
        }
        return await self._send_packet(client, SEND_MESSAGE_CMD, packet)

    async def _send_packet(self, client: Any, cmd: str, packet: dict[str, Any]) -> dict[str, Any]:
        encoded = ProtoCodec.encode(packet)
        response = await client.api.call_action(
            "send_packet",
            cmd=cmd,
            data=encoded.hex(),
            rsp=True,
        )
        response = self._unwrap_onebot_response(response)
        data = response.get("data") if isinstance(response, dict) else response
        if not data:
            raise BubbleTransformError("转换失败，send_packet 没有返回可解析的数据。")
        return ProtoCodec.decode(data)

    def _extract_reply_id(self, event: AstrMessageEvent) -> str | None:
        for segment in event.get_messages() or []:
            segment_type = getattr(segment, "type", None)
            if segment_type not in {"reply", "Reply"}:
                continue
            reply_id = getattr(segment, "id", None)
            if reply_id:
                return str(reply_id)

        raw = event.message_obj.raw_message or {}
        raw_segments = raw.get("message", []) if isinstance(raw, dict) else []
        for segment in raw_segments:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data") or {}
            reply_id = data.get("id")
            if reply_id:
                return str(reply_id)
        return None

    def _convert_video_to_bubble(self, elem_array: list[dict[str, Any]]) -> bool:
        for elem in elem_array:
            type_value = self._get_by_path(elem, VIDEO_TYPE_PATH)
            if type_value == VIDEO_MSG_TYPE:
                self._set_by_path(elem, VIDEO_TYPE_PATH, BUBBLE_MSG_TYPE)
                logger.info("[转泡泡] 已将视频消息类型从 21 改为 14")
                return True
        return False

    def _unwrap_onebot_response(self, response: Any) -> Any:
        if (
            isinstance(response, dict)
            and response.get("status") == "ok"
            and "data" in response
        ):
            return response["data"]
        return response

    def _get_by_path(self, data: Any, path: str) -> Any:
        # QQ packet data is decoded into numeric-string dict keys, so a segment like
        # "3" may represent either a dict key or, when the current node is a list, an
        # array index. This helper preserves that protocol-specific traversal rule.
        current = data
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                current = current[idx] if idx < len(current) else None
            else:
                return None
        return current

    def _set_by_path(self, data: dict[str, Any], path: str, value: Any) -> None:
        parts = path.split(".")
        current: Any = data
        for part in parts[:-1]:
            if isinstance(current, dict):
                current = current[part]
                continue
            if isinstance(current, list) and part.isdigit():
                idx = int(part)
                if idx >= len(current):
                    raise IndexError(f"path index out of range: {part}")
                current = current[idx]
                continue
            raise TypeError(f"unsupported path segment for set: {part}")

        last = parts[-1]
        if isinstance(current, dict):
            current[last] = value
            return
        if isinstance(current, list) and last.isdigit():
            idx = int(last)
            if idx >= len(current):
                raise IndexError(f"path index out of range: {last}")
            current[idx] = value
            return
        raise TypeError(f"unsupported final path segment for set: {last}")

    @staticmethod
    def _random_uint32() -> int:
        return int.from_bytes(os.urandom(4), "big")

    async def terminate(self):
        pass

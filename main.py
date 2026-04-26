import base64
import os
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


PLUGIN_NAME = "astrbot_plugin_bubble_transform"


class ProtoCodec:
    """Small protobuf codec for Packet-plugin style numeric JSON objects."""

    @classmethod
    def encode(cls, value: dict[str | int, Any]) -> bytes:
        out = bytearray()
        for key, item in value.items():
            field = int(key)
            items = item if isinstance(item, list) else [item]
            for one in items:
                out.extend(cls._encode_field(field, one))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes | str) -> dict[str, Any]:
        raw = cls._to_bytes(data)
        value, _ = cls._decode_message(raw, 0, len(raw))
        return value

    @classmethod
    def _encode_field(cls, field: int, value: Any) -> bytes:
        if isinstance(value, bool):
            return cls._key(field, 0) + cls._varint(1 if value else 0)
        if isinstance(value, int):
            return cls._key(field, 0) + cls._varint(value)
        if isinstance(value, bytes):
            return cls._key(field, 2) + cls._varint(len(value)) + value
        if isinstance(value, str):
            raw = value.encode()
            return cls._key(field, 2) + cls._varint(len(raw)) + raw
        if isinstance(value, dict):
            raw = cls.encode(value)
            return cls._key(field, 2) + cls._varint(len(raw)) + raw
        raise TypeError(f"unsupported protobuf value type: {type(value)!r}")

    @classmethod
    def _decode_message(cls, data: bytes, offset: int, end: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while offset < end:
            key, offset = cls._read_varint(data, offset)
            field = str(key >> 3)
            wire_type = key & 0x7

            if wire_type == 0:
                value, offset = cls._read_varint(data, offset)
            elif wire_type == 1:
                value = data[offset : offset + 8]
                offset += 8
            elif wire_type == 2:
                size, offset = cls._read_varint(data, offset)
                raw = data[offset : offset + size]
                offset += size
                value = cls._decode_length_delimited(raw)
            elif wire_type == 5:
                value = data[offset : offset + 4]
                offset += 4
            else:
                raise ValueError(f"unsupported protobuf wire type: {wire_type}")

            cls._append_value(result, field, value)

        return result, offset

    @classmethod
    def _decode_length_delimited(cls, raw: bytes) -> Any:
        if not raw:
            return b""
        try:
            nested, pos = cls._decode_message(raw, 0, len(raw))
            if pos == len(raw) and nested:
                return nested
        except Exception:
            pass
        try:
            text = raw.decode()
            if text.isprintable():
                return text
        except UnicodeDecodeError:
            pass
        return raw

    @staticmethod
    def _append_value(target: dict[str, Any], field: str, value: Any) -> None:
        if field not in target:
            target[field] = value
            return
        if not isinstance(target[field], list):
            target[field] = [target[field]]
        target[field].append(value)

    @classmethod
    def _key(cls, field: int, wire_type: int) -> bytes:
        return cls._varint((field << 3) | wire_type)

    @staticmethod
    def _varint(value: int) -> bytes:
        out = bytearray()
        while value > 0x7F:
            out.append((value & 0x7F) | 0x80)
            value >>= 7
        out.append(value)
        return bytes(out)

    @staticmethod
    def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
        shift = 0
        value = 0
        while True:
            byte = data[offset]
            offset += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, offset
            shift += 7

    @staticmethod
    def _to_bytes(data: bytes | str) -> bytes:
        if isinstance(data, bytes):
            return data
        try:
            return bytes.fromhex(data)
        except ValueError:
            return base64.b64decode(data)


@register(PLUGIN_NAME, "Taylor", "NapCat OneBot QQ 视频转泡泡", "0.2.0")
class BubbleTransformPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def transform(self, event: AstrMessageEvent):
        """回复视频消息，发送 #转泡泡，将 QQ 视频 pb 元素改为泡泡元素后重发。"""
        if event.message_str.strip() not in {"#转泡泡", "/转泡泡", "转泡泡"}:
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

            elem_array = self._get_by_path(raw_message, "3.6.3.1.2")
            if not isinstance(elem_array, list):
                yield event.plain_result("没有在被回复消息里找到 QQ 原始视频 elem。")
                return

            if not self._convert_video_to_bubble(elem_array):
                yield event.plain_result("未找到可转换的视频元素，可能不是视频消息或已经是泡泡。")
                return

            await self._send_elem(client, int(group_id), elem_array)
            logger.info("[转泡泡] 转换并发送成功")
        except Exception as exc:
            logger.exception(f"[转泡泡] 处理失败: {exc}")
            yield event.plain_result("转换失败，请确认 NapCat 支持 /send_packet，且回复的是群视频消息。")

    async def _get_group_raw_message(self, client: Any, group_id: int, message_id: int) -> dict[str, Any]:
        msg = await client.api.call_action("get_msg", message_id=message_id)
        msg = self._unwrap_onebot_response(msg)
        seq = int(msg.get("real_seq") or 0)
        if not seq:
            raise RuntimeError("获取 real_seq 失败，请更新 NapCat 或确认 get_msg 返回 real_seq")

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
            "trpc.msg.register_proxy.RegisterProxy.SsoGetGroupMsg",
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
        return await self._send_packet(client, "MessageSvc.PbSendMsg", packet)

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
            return {}
        return ProtoCodec.decode(data)

    def _extract_reply_id(self, event: AstrMessageEvent) -> str | None:
        for segment in event.get_messages() or []:
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
            type_value = self._get_by_path(elem, "53.3")
            if type_value == 21:
                self._set_by_path(elem, "53.3", 14)
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
            current = current[part]
        current[parts[-1]] = value

    @staticmethod
    def _random_uint32() -> int:
        return int.from_bytes(os.urandom(4), "big")

    async def terminate(self):
        pass

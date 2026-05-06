import base64
from typing import Any


class ProtoCodec:
    """Heuristic protobuf codec for Packet-plugin style numeric JSON objects."""

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

        # NapCat does not ship the protobuf schema here, so we decode length-delimited
        # values heuristically: first try nested protobuf, then printable UTF-8 text,
        # and finally fall back to the original bytes.
        try:
            nested, pos = cls._decode_message(raw, 0, len(raw))
            if pos == len(raw) and nested:
                return nested
        except (IndexError, ValueError):
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
            if offset >= len(data):
                raise ValueError("truncated varint")
            byte = data[offset]
            offset += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, offset
            shift += 7
            if shift > 63:
                raise ValueError("varint is too large")

    @staticmethod
    def _to_bytes(data: bytes | str) -> bytes:
        if isinstance(data, bytes):
            return data
        try:
            return bytes.fromhex(data)
        except ValueError:
            return base64.b64decode(data)

# astrbot_plugin_bubble_transform

QQ / NapCat、LLBot OneBot v11 视频转泡泡插件。

## 用法

在 QQ 中回复一条视频消息，然后发送以下任意指令：

- `#转泡泡`
- `/转泡泡`
- `转泡泡`

## 效果

插件会把被回复的视频消息转换成泡泡样式重新发送出来。

## 说明

- 目前支持群聊使用。
- 需要 AstrBot 使用 OneBot v11 / `aiocqhttp` 适配器，并由 NapCat 或 LLBot 连接 QQ。
- NapCat 需要支持 `send_packet`；LLBot 需要支持 `send_pb`。
- 仅支持作为“视频消息”发送的视频；作为群文件发送的 MP4 暂不支持直接转换。

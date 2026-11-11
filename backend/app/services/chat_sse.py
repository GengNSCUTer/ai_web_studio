"""Chat对外流的唯一编码：JSON事件放在标准SSE的data字段。"""

import json


def encode_chat_sse(event: dict, *, event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: chat\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

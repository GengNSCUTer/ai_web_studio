"""在线验收共用客户端：POST创建Run，GET读取标准SSE，不兼容旧Chat流格式。"""

from contextlib import contextmanager
import json

CREATE_PATHS = {"/api/chat/runs", "/api/chat/runs/regenerate", "/api/chat/runs/edit"}


def iter_sse_events(lines):
    """按空行分帧，多行data拼接；忽略心跳，不分发未完整结束的半帧。"""
    data = []
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if line == "":
            if data:
                yield json.loads("\n".join(data))
            data = []
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)


def sse_events(text):
    return list(iter_sse_events(text.splitlines()))


def _create(client, path, payload):
    if path not in CREATE_PATHS:
        raise ValueError("仅允许ChatRun创建资源")
    created = client.post(path, json=payload)
    created.raise_for_status()
    return created.json()


@contextmanager
def chat_stream(client, path="/api/chat/runs", *, json, **kwargs):
    run = _create(client, path, json)
    headers = {**kwargs.pop("headers", {}), "Accept": "text/event-stream"}
    with client.stream("GET", f"/api/chat/runs/{run['id']}/events", headers=headers, **kwargs) as response:
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            raise ValueError("不是SSE响应")
        yield response


def chat_response(client, path="/api/chat/runs", *, json, **kwargs):
    run = _create(client, path, json)
    response = client.get(f"/api/chat/runs/{run['id']}/events", **kwargs)
    response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("text/event-stream"):
        raise ValueError("不是SSE响应")
    return response


def browser_chat_response(request, base_url, *, data, kind="new", timeout=240000):
    suffix = {"new": "", "regenerate": "/regenerate", "edit": "/edit"}[kind]
    created = request.post(base_url + "/api/backend/chat/runs" + suffix, data=data, timeout=timeout)
    if not created.ok:
        raise RuntimeError(f"创建任务失败，HTTP {created.status}")
    run = created.json()
    result = request.get(base_url + f"/api/backend/chat/runs/{run['id']}/events", timeout=timeout,
        headers={"accept": "text/event-stream"})
    if not result.ok or not result.headers.get("content-type", "").startswith("text/event-stream"):
        raise RuntimeError("SSE订阅失败")
    return result

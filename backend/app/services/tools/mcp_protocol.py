"""MCP两代HTTP协议的元数据与参数头；不包含鉴权或业务权限决策。"""

import base64
import re


MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
CLIENT_INFO = {"name": "ai_web_studio", "version": "0.1.0"}


def header_value(value: str) -> str:
    safe = value == value.strip() and all(32 <= ord(char) <= 126 or char == "\t" for char in value)
    sentinel = value.startswith("=?base64?") and value.endswith("?=")
    return value if safe and not sentinel else "=?base64?" + base64.b64encode(value.encode()).decode() + "?="


def parameter_headers(schema: dict, arguments: dict) -> dict[str, str]:
    """仅接受沿properties可达的原始值，非法标注不能被静默忽略。"""
    headers, names = {}, set()

    def visit(node, path=(), reachable=True):
        if not isinstance(node, dict):
            return
        if "x-mcp-header" in node:
            name = node["x-mcp-header"]
            if (not reachable or not path or not isinstance(node.get("type"), str)
                    or node.get("type") not in {"string", "integer", "boolean"}
                    or not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                    or name.lower() in names):
                raise RuntimeError("MCP工具的参数请求头声明无效。")
            names.add(name.lower())
            value = arguments
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if value is not None:
                kind = node["type"]
                valid = ((kind == "string" and isinstance(value, str))
                         or (kind == "boolean" and isinstance(value, bool))
                         or (kind == "integer" and type(value) is int and abs(value) <= 2**53 - 1))
                if not valid:
                    raise RuntimeError("MCP参数请求头的值类型无效。")
                headers[f"Mcp-Param-{name}"] = header_value(str(value).lower() if isinstance(value, bool) else str(value))
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for name, child in value.items():
                    visit(child, (*path, name), reachable)
            elif isinstance(value, dict):
                visit(value, path, False)
            elif isinstance(value, list):
                for child in value:
                    visit(child, path, False)
    visit(schema)
    return headers

"""对外工具动作与数据检查。只从用户原任务获得动作范围，不从资料获得授权。"""

from dataclasses import dataclass
import base64
import re
from urllib.parse import unquote


INTENT_PATTERNS = {
    "web_search": r"联网|上网|网页|网上|互联网|外部网站|官网|搜索|最新|新闻|公开资料|\b(?:web|internet|online|search|latest|official)\b",
    "web_fetch": r"https?://|网页|网站|官网|联网|\b(?:web|website|fetch|url|online)\b",
    "documentation": r"官方文档|技术文档|context7|API|SQLAlchemy|FastAPI|React|httpx|pgvector|\b(?:docs|documentation|framework)\b",
    "weather": r"天气|温度|气温|多少度|下雨|降雨|预报|\b(?:weather|temperature|rain|forecast)\b",
    "map_route": r"路线|导航|驾车|开车|步行|地铁|公交|耗时|多久到|怎么去|路上|沿途|\b(?:route|drive|driving|walking|transit|directions)\b",
    "map_distance": r"距离|多远|相距|几公里|哪个近|\b(?:distance|nearest|closer)\b",
    "map_poi": r"附近|周边|地点|地址|地图|位置|服务区|景点|酒店|餐厅|医院|车站|\b(?:nearby|place|location|restaurant|hotel|map)\b",
    "map_geo": r"经纬度|坐标|地理编码|地址|路线|驾车|开车|步行|距离|\b(?:geocode|coordinate|address|route|distance)\b",
}
NO_WEB = re.compile(r"(?:不要|不用|无需|禁止|别)(?:使用|调用|进行)?(?:联网|上网|网页搜索|外部网站)|\b(?:do not|don't|never|no)\s+(?:(?:use|search|browse|access)\s+)?(?:the\s+)?(?:web|internet|online)\b", re.I)
LOCAL_ONLY = re.compile(r"仅(?:根据|使用)|只(?:根据|依据|使用|总结|整理)|\b(?:only|solely)\b", re.I)
SECRET = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?token|password|passwd|secret|authorization)\b[\"']?\s*[:=]\s*[\"']?[^\s,;\"']{4,}"
    r"|\b(?:sk-[A-Za-z0-9_-]{16,}|tvly-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,})\b"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|https?://[^/@\s]+:[^/@\s]+@", re.I)
PERSONAL = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


@dataclass(frozen=True)
class ActionScope:
    original_task: str
    confirmed_tool_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str
    field_paths: tuple[str, ...] = ()


def strings(value, path=""):
    if isinstance(value, str):
        yield path or "/", value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield path or "/", str(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from strings(item, path + "/" + str(index))


def decoded_variants(text):
    """有界检查URL/JSON转义和常见base64，不能据此宣称覆盖所有编码外传。"""
    value = text[:32000]
    for _ in range(3):
        yield value
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    if re.fullmatch(r"[A-Za-z0-9_+/=-]{24,32000}", value):
        try:
            yield base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_").decode("utf-8")
        except (ValueError, UnicodeError):
            pass


class ToolActionGuard:
    def __init__(self, scope: ActionScope | None = None):
        self.scope = scope
        self._protected: list[str] = []

    def remember_sources(self, sources):
        """当前已读工作区/产物正文只作为保护材料，绝不作为动作授权输入。"""
        for source in sources:
            kind = source.get("source_type") if isinstance(source, dict) else getattr(source, "source_type", "")
            if kind not in {"workspace_file_read", "artifact_read", "agent_artifact_read"}:
                continue
            metadata = source.get("metadata", {}) if isinstance(source, dict) else getattr(source, "metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            raw = metadata.get("raw") or {}
            content = raw.get("content") if isinstance(raw, dict) else None
            if not isinstance(content, str):
                content = source.get("display_text", "") if isinstance(source, dict) else getattr(source, "display_text", "")
            if content and len(self._protected) < 16:
                self._protected.append(re.sub(r"\s+", "", content)[:8000])

    def check(self, definition, arguments):
        network = definition.adapter_type not in {"workspace_file", "agent_artifact"}
        if not network:
            return GuardDecision(True, "local_action")
        scope = self.scope
        if scope is None:
            return GuardDecision(False, "original_task_missing")
        task = scope.original_task
        explicit = definition.tool_key in scope.confirmed_tool_keys
        category = definition.category
        if not explicit:
            pattern = INTENT_PATTERNS.get(category)
            if not pattern or not re.search(pattern, task, re.I):
                return GuardDecision(False, "action_outside_original_task")
            if category in {"web_search", "web_fetch", "documentation"} and NO_WEB.search(task):
                return GuardDecision(False, "original_task_forbids_web")
            if LOCAL_ONLY.search(task) and re.search(r"玩家反馈|本地|工作区|上传|附件|所给|提供的资料|反馈数据|player feedback|uploaded|workspace", task, re.I) and not re.search(r"联网|网页|官网|公开资料|\bweb\b", task, re.I):
                return GuardDecision(False, "local_evidence_only")
        for path, text in strings(arguments):
            if re.search(r"/(?:api[_-]?key|access[_-]?token|password|passwd|secret|authorization)$", path, re.I) and text:
                return GuardDecision(False, "outbound_secret_field", (path,))
            if len(text) > 32000:
                return GuardDecision(False, "outbound_argument_too_large", (path,))
            for value in decoded_variants(text):
                if SECRET.search(value):
                    return GuardDecision(False, "outbound_secret", (path,))
                if PERSONAL.search(value):
                    return GuardDecision(False, "outbound_personal_identifier", (path,))
                compact = re.sub(r"\s+", "", value)
                # 精确连续片段保护不依赖模型判断；短公开关键词不按整份私有资料外传处理。
                if len(compact) >= 24 and any(compact[index:index + 24] in material
                    for material in self._protected for index in range(0, len(compact) - 23)):
                    return GuardDecision(False, "outbound_workspace_content", (path,))
        return GuardDecision(True, "action_and_data_allowed")

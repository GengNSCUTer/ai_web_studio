"""低成本触发分类；只决定何时排队，不决定事实成立或记忆生效。"""

from dataclasses import dataclass
import json
import re

from app.services.memory_extraction_policy import explicit_remember, redact_source, REDACTED


@dataclass(frozen=True)
class MemorySignal:
    action: str
    reason: str
    priority: int = 0


_FORGET = re.compile(r"^(?:请|帮我)?(?:忘记|忘掉|删除长期记忆|撤销长期记忆)[：:\s]*(.+)$|^(?:please\s+)?forget\s*[:：]?\s+(.+)$", re.I | re.S)
_EXTERNAL = re.compile(r"^(?:>|```|[“\"「]|以下(?:是|为)|(?:网页|文档|论文|别人|他|她|系统提示|工具返回|引用).{0,12}(?:说|写|内容|要求|提到)|(?:the (?:document|website)|he|she) says)", re.I)
_TEMPORARY = re.compile(r"仅(?:限|这)|只(?:在|限|这)|今天|这次|本次|本轮|临时|today|this (?:time|turn)|for now", re.I)
_TENTATIVE = re.compile(r"如果|假设|可能|也许|不确定|考虑|暂定|还没决定|待定|maybe|perhaps|consider|not sure|\bif\b", re.I)
_CORRECTION = re.compile(r"^(?:更正|纠正|更新(?:一下)?(?:记忆)?|之前.{0,35}(?:不对|说错)|(?:我的项目|本项目|项目|我们|我).{0,25}(?:改用|不再|已改|改为))|^(?:correction|update my memory)\b", re.I)
_STABLE = re.compile(r"^(?:以后|今后|长期|默认).{0,30}(?:回答|使用|用|希望|请)|^(?:我的项目|本项目|项目|我们(?:的项目)?|我).{0,45}(?:最终决定|确定(?:了)?|已决定|采用|使用|偏好|喜欢|负责|习惯)|^I (?:prefer|always|work as)|^my project (?:uses|will use)", re.I)
_QUESTION = re.compile(r"[?？]$|^(?:什么是|怎么|如何|为什么|解释|介绍一下|请解释|请介绍|帮我(?:查|搜索|总结)|what\b|how\b|why\b|explain\b)", re.I)
_ACK = re.compile(r"^(?:好(?:的)?|谢谢|收到|明白(?:了)?|继续|ok|thanks|thank you)[。！!\s]*$", re.I)
_NO_SAVE = re.compile(r"^(?:请)?(?:别|不要|无需)(?:再)?(?:记住|保存|记录)|^(?:please )?(?:do not|don't) (?:remember|save)", re.I)


def forget_target(text: str) -> str | None:
    match = _FORGET.match(text.strip())
    if not match:
        return None
    return next(value for value in match.groups() if value is not None).strip().strip('“”"「」')


def classify_memory_signal(text: str) -> MemorySignal:
    text = (text or "").strip()
    if not text or _EXTERNAL.search(text) or "```" in text:
        return MemorySignal("skip", "external_or_empty")
    sanitized = redact_source(text)
    if REDACTED in sanitized:
        text = sanitized.replace(REDACTED, "").strip("。！？，,;； \n")
        if len(text) < 4:
            return MemorySignal("skip", "sensitive_source")
    if forget_target(text) is not None:
        return MemorySignal("revoke", "explicit_forget", 100)
    if _NO_SAVE.search(text):
        return MemorySignal("skip", "no_persistence_requested")
    if _TEMPORARY.search(text):
        return MemorySignal("skip", "temporary_request")
    if _TENTATIVE.search(text):
        return MemorySignal("buffer", "tentative_statement")
    if explicit_remember(text):
        return MemorySignal("extract_now", "explicit_remember", 90)
    if _CORRECTION.search(text):
        return MemorySignal("revise", "explicit_correction", 80)
    if _QUESTION.search(text) or _ACK.fullmatch(text):
        return MemorySignal("skip", "ordinary_question")
    if _STABLE.search(text):
        return MemorySignal("extract_now", "stable_statement", 70)
    return MemorySignal("buffer", "batch_fallback")


def read_trigger_state(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        if isinstance(value, dict) and isinstance(value.get("pending", []), list):
            return value
    except (TypeError, ValueError):
        pass
    return {}

"""记忆的事实标识、时效与凭证边界；不依赖模型自报的风险等级。"""

import re
from dataclasses import dataclass


def normalize(value: str | None) -> str:
    return " ".join((value or "").split()).strip()


@dataclass(frozen=True)
class MemoryIdentity:
    key: str
    value: str
    structured: bool = False


# 密码即使很短也不能进入普通记忆；示例变量名、未填写的标签不等于凭证值。
_CREDENTIAL = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|\btoken\b|password|passwd|密码|密钥|令牌)"
    r"\s*(?:[:=：]|(?:就是|是|为))\s*\S+|"
    r"\bBearer\s+[a-z0-9._~+/=-]+|\b(?:sk-|tvly-)[a-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_TURN_ONLY = re.compile(r"这次|本次|这一轮|这轮|this\s+(?:time|turn|answer)|for\s+this\s+response", re.I)
_TEMPORARY = re.compile(
    r"今天|明天|后天|本周|这周|下周|这个月|下个月|当前临时|暂时|最近|"
    r"\btoday\b|\btomorrow\b|this week|next week|\bcurrently\b|\btemporary\b", re.I
)
_NEGATION = re.compile(r"不|没|禁止|取消|不要|\b(?:not|never|without|no)\b", re.I)


def contains_credential(title: str, content: str) -> bool:
    label = re.fullmatch(r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|passwd|密码|密钥|令牌)\s*[:：]?", normalize(title), re.I)
    return bool(_CREDENTIAL.search(f"{title}\n{content}") or (label and normalize(content)))


def lifetime(title: str, content: str) -> str:
    text = f"{title}\n{content}"
    if _TURN_ONLY.search(text):
        return "turn"
    return "temporary" if _TEMPORARY.search(text) else "durable"


def requested_response_language(query: str | None) -> str | None:
    """本轮明确语言要求在代码层覆盖默认值，不让两个相反要求同时进入 Prompt。"""
    text = normalize(query).lower()
    match = re.search(r"(?:这次|本次|这一轮|这轮|请|please).{0,40}?(?:用|使用|in)\s*(中文|汉语|英文|英语|chinese|english)", text)
    if match:
        return "zh" if match.group(1) in {"中文", "汉语", "chinese"} else "en"
    return None


def memory_identity(memory_type: str, title: str, content: str) -> MemoryIdentity:
    """常见单值偏好用固定标识；其余按类型和标题保守归类，不声称理解任意事实。"""
    title, content = normalize(title).lower(), normalize(content).lower()
    language_topic = bool(re.search(r"回答语言|回复语言|默认语言|response.language|answer.language", title))
    language_topic |= bool(re.search(r"(?:回答|回复|answer|respond).*(?:中文|英文|chinese|english)|(?:中文|英文|chinese|english).*(?:回答|回复|answer|respond)", content))
    if language_topic:
        values = {code for code, pattern in (("zh", r"中文|汉语|chinese"), ("en", r"英文|英语|english")) if re.search(pattern, content)}
        # 多个值或否定陈述不自动归一，防止“不要英文”被当作“英文”。
        residual = content
        for term in ("从现在开始", "从现在起", "长期语言偏好", "用户", "默认", "喜欢", "希望", "以后", "今后", "回答", "回复", "使用", "中文", "汉语", "英文", "英语", "请", "长期", "偏好", "语言", "已经", "更改", "改为", "进行", "倾向", "一直", "用", "以", "为", "是", "的", "都", "今天", "本周", "暂时"):
            residual = residual.replace(term, "")
        residual = re.sub(r"\b(?:user|prefers?|default|answers?|respond|response|in|with|english|chinese|always|please|use|from|now|on|by)\b", "", residual)
        residual = re.sub(r"[\s，。,.！!；;：:]", "", residual)
        if len(values) == 1 and not _NEGATION.search(content) and not residual:
            return MemoryIdentity("response_language", values.pop(), True)
        return MemoryIdentity("response_language", content)
    generic_title = re.sub(r"补充$", "", title)
    return MemoryIdentity(f"{memory_type}:{generic_title}", content)


def equivalent_content(left: str, right: str, *, allow_reordering: bool = False) -> bool:
    """仅忽略排版和安全的词序变动；数字、标识符、否定发生变化时不能去重。"""
    left, right = normalize(left).lower(), normalize(right).lower()
    def compact(text: str) -> str:
        return re.sub(r"[\s，。,.！!；;：:]", "", text)
    if compact(left) == compact(right):
        return True
    if not allow_reordering:
        return False
    if bool(_NEGATION.search(left)) != bool(_NEGATION.search(right)):
        return False
    # 重排检测要求英文实体和数字完全一致；中文内容去掉少量连接词后顺序也相同。
    def terms(text: str) -> tuple[list[str], str]:
        identifiers = sorted(re.findall(r"[a-z0-9_]+", text))
        chinese = re.sub(r"[a-z0-9_]+", "", text)
        for stop in ("技术基础", "项目", "使用", "采用", "是", "的", "和", "与"):
            chinese = chinese.replace(stop, "")
        return identifiers, compact(chinese)
    left_terms, right_terms = terms(left), terms(right)
    return bool(left_terms[0]) and bool(left_terms[1]) and left_terms == right_terms

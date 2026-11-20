"""判断本轮是否读取记忆，以及候选与当前任务是否相关。"""

import re
from dataclasses import dataclass

from app.services.memory_policy import memory_identity, normalize


SEMANTIC_MIN = .55
QUERY_LIMIT = 3
RECALL_LIMIT = 8
PREFERENCE_TYPES = ("profile", "instruction")

_SOCIAL = re.compile(r"你好|您好|嗨|哈喽|早上好|晚上好|谢谢(?:你|了)?|多谢|好的|收到|明白了|再见|"
                     r"hi|hello|hey|thanks|thank you|ok(?:ay)?|got it|bye", re.I)
_OPT_OUT = re.compile(
    r"(?:^|[，。；！？,;!?\n])\s*(?:请\s*)?(?:(?:本轮|这轮|这次|此次|本次|这一轮)\s*)?"
    r"(?:请\s*)?(?:不要|不用|无需|别|禁止)(?:使用|参考|调用|读取|检索|注入|用)?(?:我的|已有的|已保存的)?"
    r"(?:长期记忆|记忆)(?=$|[，。；！？,;!?.\s]|(?:来|辅助)?(?:回答|作答|生成))|"
    r"(?:^|[.,;!?\n])\s*(?:please\s+)?(?:do not|don't|never)\s+(?:use|retrieve|read)\s+"
    r"(?:(?:my|saved|long-term)\s+)*memor(?:y|ies)\b", re.I)
_RECALL_PREFERENCES = re.compile(
    r"(?:你)?(?:还)?(?:记得|记住了|保存了)(?:我|我的)(?:有)?(?:哪些|什么|多少)(?:个)?(?:偏好|习惯|长期指令|回答要求)(?:吗)?|"
    r"(?:列出|查看|告诉我)(?:你)?(?:已)?(?:记录|记得|保存)(?:的)?(?:我|我的)?(?:哪些)?(?:偏好|习惯|长期指令)|"
    r"what (?:preferences|habits) do you remember about me|what do you remember about my preferences", re.I)
_FOLLOW_UP = re.compile(r"上次|之前|前几次|上一(?:个|轮)?会话|另一个会话|原来的|之前说的|"
                        r"last time|previous chat|earlier conversation|other chat", re.I)
_LANGUAGE_TOPIC = re.compile(r"(?:回答|回复|输出|默认)(?:用什么|用哪种|的)?语言|什么语言(?:回答|回复)|"
                             r"response language|answer language", re.I)
_GENERIC_TERMS = frozenset("这个 那个 我的 我们 你的 你们 之前 现在 什么 怎么 如何 一下 是否 项目 使用 采用 回答 回复 介绍 解释 请问 about this that your with from which what please explain the you".split())


@dataclass(frozen=True)
class MemoryReadDecision:
    mode: str
    reason: str
    query: str


def decide_memory_read(query: str, *, enabled: bool = True, prior_user_text: str = "") -> MemoryReadDecision:
    if not enabled:
        return MemoryReadDecision("disabled", "memory_disabled", "")
    text = normalize(query)
    if not text:
        return MemoryReadDecision("skipped", "empty_query", "")
    if _OPT_OUT.search(query):
        return MemoryReadDecision("skipped", "user_opt_out", "")
    compact = text.strip(" \t。！!?？,.，~～")
    if _SOCIAL.fullmatch(compact):
        return MemoryReadDecision("skipped", "social_only", "")
    if _RECALL_PREFERENCES.fullmatch(compact):
        return MemoryReadDecision("explicit_recall", "user_requested_preferences", text)
    prior = normalize(prior_user_text)
    if len(text) <= 80 and prior and prior != text and _FOLLOW_UP.search(text):
        return MemoryReadDecision("relevant", "contextual_follow_up", f"{text}\n{prior[:400]}")
    return MemoryReadDecision("relevant", "query_relevance", text)


def lexical_evidence(memory, query: str, terms_for, query_terms=None) -> float:
    """过滤泛词，只接受明确主题或足够的词法证据。"""
    title = normalize(memory.title).lower()
    text = query.lower()
    if (memory.memory_type in PREFERENCE_TYPES
            and memory_identity(memory.memory_type, memory.title, memory.content).key == "response_language"
            and _LANGUAGE_TOPIC.search(text)):
        return 1.0
    terms = query_terms if query_terms is not None else terms_for(text) - _GENERIC_TERMS
    overlap = terms & terms_for(f"{title}\n{memory.content}")
    if not overlap:
        return 0.
    score = len(overlap) / len(terms)
    if title and title not in _GENERIC_TERMS and title in text:
        return max(score, .5)
    technical_term = any(re.fullmatch(r"[a-z0-9_]{3,}", term) for term in overlap)
    if score >= .12 and (len(overlap) >= 2 or len(terms) <= 2 or technical_term):
        return score
    return 0.


def memory_query_terms(query, terms_for):
    return terms_for(query) - _GENERIC_TERMS

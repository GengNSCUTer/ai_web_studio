"""区分绝对召回门槛、词法覆盖特征和相对排序。"""

import re

from app.services.knowledge_search_text import normalize_search_text


# 只有通过独立留出验证的模型才登记额外下限。
SEMANTIC_PROFILES: dict[tuple[str, str, int], float] = {}
LEXICAL_MIN_COVERAGE: dict[bool, float] = {}


def query_anchors(query: str) -> tuple[str, ...]:
    parts = re.split(r"(?:对比|比较|区别|分别|差异|相同点|不同点|和|与|以及|、|[,，；;]|\b(?:vs\.?|versus|and)\b)",
                     normalize_search_text(query), flags=re.IGNORECASE)
    anchors = []
    for part in parts:
        terms = re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", part.lower())
        terms = [re.sub(r"^(?:请问|请|说明|介绍|分析)|(?:有哪些|是什么|如何|怎么)$", "", term) for term in terms]
        anchors.extend(term for term in terms if len(term) >= 2 and term not in {"the", "what", "how", "which"})
    return tuple(dict.fromkeys(anchors))[:8]

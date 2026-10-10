from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.tools.catalog import ToolCatalog
from app.services.tools.schemas import ToolDefinition


@dataclass(frozen=True)
class ToolCandidate:
    definition: ToolDefinition
    score: float
    reasons: list[str] = field(default_factory=list)


class ToolCandidateSelector:
    """根据当前问题和有限追问线索筛选候选；不构造参数，也不授予权限。"""

    max_candidates = 6
    # 文件名只是召回线索，不能直接作为已授权路径或文件 ID。
    FILE_NAME_PATTERN = re.compile(
        r"[^\s/\\<>\"'`，。；！？:：]{1,100}\.(?:md|txt|pdf|docx?|xlsx?|csv|jsonl?|ya?ml|"
        r"toml|xml|html?|py|java|js|jsx|ts|tsx|log)(?=$|[\s，。；！？,;!?：:）)\]】\"'`])",
        re.I,
    )
    FILE_ORAL_PATTERN = re.compile(r"(?:上传|附件|读一下|读一读|翻一翻)")
    FILE_LIST_PATTERN = re.compile(r"(?:列出|列一下|有哪些|都有什么).{0,16}(?:文件|文档|附件)")
    FILE_EDIT_PATTERN = re.compile(r"(?:修改|编辑|替换|重构|diff|补丁|写入|改成|更改)", re.I)
    FOLLOWUP_PATTERN = re.compile(r"(?:它|这份|那份|这个|那个|里面|其中|继续|上面|刚才|上述|这篇|那篇)")
    FILE_EXCLUSION_PATTERN = re.compile(
        r"(?:不要|不用|别|无需|不)(?:使用|用|读取|读|查看|搜索|访问|检查)?(?:本地|工作区|项目)?(?:文件|文档)"
        r"|(?:只|仅).{0,5}(?:联网|网上|网页|官网)"
    )
    WEB_EXCLUSION_PATTERN = re.compile(r"(?:不要|不用|别|无需|不)(?:使用|用|进行)?(?:联网|上网|网页搜索|网络搜索)")
    NO_TOOL_PATTERN = re.compile(r"(?:不要|不用|别|无需)(?:再|去)?(?:调用|使用)(?:任何|所有|外部)?工具")

    CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
        "web_search": re.compile(r"(最新|今天|现在|新闻|官网|搜索|查询|资料|总统|版本|政策|价格|实时)"),
        "weather": re.compile(r"(天气|气温|温度|下雨|降雨|台风|空气质量|冷不冷|热不热|明天|后天)"),
        "map_route": re.compile(r"(路线|怎么去|怎么走|导航|驾车|开车|步行|地铁|公交|多久到|开车多久|要多久|耗时|预计耗时|路上|沿途|途中)"),
        "map_distance": re.compile(r"(多远|相距|距离|几公里|多少公里|哪个近|更近|分别离|离.+远)"),
        "map_poi": re.compile(r"(附近|周边|位置|地址|在哪|哪里|地图|服务区|景点|酒店|餐厅|医院|学校|车站)"),
        "map_geo": re.compile(r"(经纬度|坐标|地理编码|行政区|区划|地址解析)"),
        "workspace_file": re.compile(r"(工作区|项目文件|文件|文档|资料|附件|报告|代码|读取|查找|修改|编辑|替换|重构|diff)"),
    }

    def __init__(self, catalog: ToolCatalog | None = None, *, max_candidates: int | None = None) -> None:
        self.catalog = catalog or ToolCatalog()
        if max_candidates is not None:
            if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
                raise ValueError("候选工具数量必须是正整数。")
            self.max_candidates = max_candidates

    def select(
        self,
        *,
        query: str,
        enabled: bool,
        allowed_tool_keys: set[str] | None = None,
        recent_messages: list[object] | None = None,
    ) -> tuple[list[ToolDefinition], dict]:
        definitions = [
            tool
            for tool in self.catalog.list_definitions()
            if tool.enabled_by_default
            and (allowed_tool_keys is None or tool.tool_key in allowed_tool_keys)
            and not (tool.category == "web_search" and self.WEB_EXCLUSION_PATTERN.search(query))
        ]
        if not enabled:
            return [], self._trace(
                query=query,
                candidates=[],
                reason="external_tools_disabled",
                allowed_tool_keys=allowed_tool_keys,
            )
        if self.NO_TOOL_PATTERN.search(query):
            return [], self._trace(query=query, candidates=[], reason="user_requested_no_tools",
                                   allowed_tool_keys=allowed_tool_keys)
        if not definitions:
            return [], self._trace(
                query=query,
                candidates=[],
                reason="empty_allowed_catalog" if allowed_tool_keys is not None else "empty_catalog",
                allowed_tool_keys=allowed_tool_keys,
            )

        file_hint = self._file_hint(query=query, recent_messages=recent_messages or [])
        scored = [self._score_tool(tool=tool, query=query, file_hint=file_hint) for tool in definitions]
        scored.sort(key=lambda candidate: (-candidate.score, candidate.definition.tool_key))

        # 先固定专业工具容量，再保留联网兜底；满额后不得先添加再截断。
        web_fallback = self.catalog.get_or_none("web.tavily.search")
        reserve_web_slot = bool(
            web_fallback
            and any(tool.tool_key == web_fallback.tool_key for tool in definitions)
        )
        specialized_budget = max(0, self.max_candidates - int(reserve_web_slot))
        selected: list[ToolCandidate] = []
        seen: set[str] = set()
        seen_categories: set[str] = set()
        for candidate in scored:
            if len(selected) >= specialized_budget:
                break
            if candidate.score <= 0 or candidate.definition.tool_key == "web.tavily.search":
                continue
            if candidate.definition.category in seen_categories:
                continue
            selected.append(candidate)
            seen.add(candidate.definition.tool_key)
            seen_categories.add(candidate.definition.category)
        for candidate in scored:
            if len(selected) >= specialized_budget:
                break
            if (
                candidate.score <= 0
                or candidate.definition.tool_key in seen
                or candidate.definition.tool_key == "web.tavily.search"
            ):
                continue
            selected.append(candidate)
            seen.add(candidate.definition.tool_key)

        # 兜底只进入候选，不代表一定调用；Planner 可以返回无需工具。
        if reserve_web_slot and web_fallback:
            web_score = next(
                (candidate.score for candidate in scored if candidate.definition.tool_key == web_fallback.tool_key),
                0.35,
            )
            selected.append(
                ToolCandidate(
                    definition=web_fallback,
                    score=max(web_score, 0.35),
                    reasons=["web_search_fallback"],
                )
            )
            seen.add(web_fallback.tool_key)

        # 无联网工具的目录只暴露一个低风险只读兜底。
        if not selected:
            fallback = next(
                (tool for tool in definitions if tool.read_only and tool.risk_level != "high"),
                None,
            )
            selected = (
                [ToolCandidate(definition=fallback, score=0.2, reasons=["generic_enabled_tool"])]
                if fallback
                else []
            )

        return [candidate.definition for candidate in selected], self._trace(
            query=query,
            candidates=selected,
            reason=(
                "ranked_within_explicit_skill_allowlist"
                if allowed_tool_keys is not None
                else "ranked_by_query_and_tool_metadata"
            ),
            allowed_tool_keys=allowed_tool_keys,
        )

    @classmethod
    def _file_hint(cls, *, query: str, recent_messages: list[object]) -> str | None:
        """仅用同会话最近用户消息补充明确追问，不扫描工具/助手输出。"""
        text = query[:2400]
        if cls.FILE_EXCLUSION_PATTERN.search(text):
            return None
        if cls.FILE_NAME_PATTERN.search(text):
            return "current_file_name"
        if cls.CATEGORY_PATTERNS["workspace_file"].search(text) or cls.FILE_ORAL_PATTERN.search(text):
            return "current_file_intent"
        if len(text) > 240 or not cls.FOLLOWUP_PATTERN.search(text):
            return None
        # 当前问题有明确的新领域时，不让旧文件话题污染召回。
        # “别联网”是禁止项，不能被当成切换到联网话题而丢失文件追问线索。
        topic_text = cls.WEB_EXCLUSION_PATTERN.sub("", text)
        if any(cls.CATEGORY_PATTERNS[name].search(topic_text) for name in (
            "weather", "map_route", "map_distance", "map_poi", "map_geo"
        )) or re.search(r"(?:联网|网上|网页|官网|新闻)", topic_text):
            return None
        for message in reversed(recent_messages[-8:]):
            role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
            if role != "user":
                continue
            previous = str(content or "")[:600].strip()
            if previous == query.strip():
                continue
            # 只查看最近一个不同的用户问题，不越过已经切换的话题。
            if not cls.FILE_EXCLUSION_PATTERN.search(previous) and (
                cls.FILE_NAME_PATTERN.search(previous)
                or cls.CATEGORY_PATTERNS["workspace_file"].search(previous)
                or cls.FILE_ORAL_PATTERN.search(previous)
            ):
                return "recent_user_file_followup"
            break
        return None

    def _score_tool(self, *, tool: ToolDefinition, query: str, file_hint: str | None = None) -> ToolCandidate:
        score = 0.0
        reasons: list[str] = []
        # 编辑预览虽不落正文、标为 read_only，也不应被普通文件追问一并召回。
        # 使用已有结果合同类型判断，避免只对某个工具名称写特例。
        edit_query = re.sub(r"(?:不要|不用|别|无需|不)(?:修改|编辑|替换|写入)", "", query)
        if tool.category == "workspace_file" and tool.quality_contract.get("semantic_profile") in {"approval_draft", "file_revision"} \
                and not self.FILE_EDIT_PATTERN.search(edit_query):
            return ToolCandidate(definition=tool, score=0.0, reasons=["file_edit_intent_absent"])

        category_pattern = self.CATEGORY_PATTERNS.get(tool.category)
        if category_pattern and category_pattern.search(query) and not (
            tool.category == "workspace_file" and self.FILE_EXCLUSION_PATTERN.search(query)
        ):
            score += 1.4
            reasons.append(f"category_match:{tool.category}")
        if file_hint and tool.category == "workspace_file" and tool.read_only:
            score += 1.8
            reasons.append(f"file_hint:{file_hint}")
            # 明确文件名时优先搜索/读取，列表问题优先列表；其他文件工具仍按元数据匹配。
            if tool.tool_key in {"workspace.files.search", "workspace.files.read"}:
                score += 0.2
            if tool.tool_key == "workspace.files.list" and self.FILE_LIST_PATTERN.search(query):
                score += 0.4

        haystack = " ".join(
            [
                tool.tool_key,
                tool.provider,
                tool.category,
                tool.display_name,
                tool.description,
                " ".join(tool.when_to_use),
                " ".join(tool.when_not_to_use),
            ]
        ).lower()
        query_terms = [term for term in re.split(r"\s+|，|,|。|；|;|\?|？", query.lower()) if len(term) >= 2]
        metadata_hits = sum(1 for term in query_terms if term in haystack)
        if metadata_hits:
            score += min(metadata_hits * 0.25, 1.0)
            reasons.append(f"metadata_hits:{metadata_hits}")

        if tool.source_type == "mcp_server":
            score += 0.15
            reasons.append("user_enabled_mcp_tool")
        if tool.tool_key == "amap.maps.text_search" and re.search(r"(服务区|地点|地址|哪里|在哪|路上)", query):
            score += 0.25
            reasons.append("text_search_preferred_for_keyword_poi")
        if tool.tool_key == "amap.maps.around_search" and not re.search(r"(附近|周边|周围|半径)", query):
            score -= 0.2
            reasons.append("around_search_needs_center_location")
        # 只读和风险等级是权限属性，不能单独证明语义相关性。
        if tool.read_only:
            reasons.append("read_only")
        if tool.risk_level == "high":
            score -= 0.35
            reasons.append("high_risk_penalty")

        return ToolCandidate(definition=tool, score=round(score, 3), reasons=reasons)

    @staticmethod
    def _trace(
        *,
        query: str,
        candidates: list[ToolCandidate],
        reason: str,
        allowed_tool_keys: set[str] | None = None,
    ) -> dict:
        return {
            "type": "tool_candidate_selection",
            "selector": "tool_candidate_selector_v2",
            "reason": reason,
            "query_preview": query[:240],
            "selected_count": len(candidates),
            "scope": "explicit_skill" if allowed_tool_keys is not None else "catalog",
            "allowed_tool_keys": sorted(allowed_tool_keys) if allowed_tool_keys is not None else None,
            "candidates": [
                {
                    "tool_key": candidate.definition.tool_key,
                    "display_name": candidate.definition.display_name,
                    "category": candidate.definition.category,
                    "provider": candidate.definition.provider,
                    "source_type": candidate.definition.source_type,
                    "risk_level": candidate.definition.risk_level,
                    "read_only": candidate.definition.read_only,
                    "score": candidate.score,
                    "reasons": candidate.reasons,
                }
                for candidate in candidates
            ],
        }

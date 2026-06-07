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
    """Selects a compact candidate set for the LLM planner.

    This layer should not extract final tool arguments. Its job is only to keep
    the planner prompt small and focused when the catalog grows.
    """

    max_candidates = 8

    CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
        "web_search": re.compile(r"(最新|今天|现在|新闻|官网|搜索|查询|资料|总统|版本|政策|价格|实时)"),
        "weather": re.compile(r"(天气|气温|温度|下雨|降雨|台风|空气质量|冷不冷|热不热|明天|后天)"),
        "map_route": re.compile(r"(路线|怎么去|怎么走|导航|驾车|开车|步行|地铁|公交|多久到|开车多久|要多久|耗时|预计耗时)"),
        "map_distance": re.compile(r"(多远|相距|距离|几公里|多少公里|哪个近|更近|分别离|离.+远)"),
        "map_poi": re.compile(r"(附近|周边|位置|地址|在哪|哪里|地图|服务区|景点|酒店|餐厅|医院|学校|车站)"),
        "map_geo": re.compile(r"(经纬度|坐标|地理编码|行政区|区划|地址解析)"),
    }

    def __init__(self, catalog: ToolCatalog | None = None, *, max_candidates: int | None = None) -> None:
        self.catalog = catalog or ToolCatalog()
        if max_candidates is not None:
            self.max_candidates = max_candidates

    def select(self, *, query: str, enabled: bool) -> tuple[list[ToolDefinition], dict]:
        definitions = [tool for tool in self.catalog.list_definitions() if tool.enabled_by_default]
        if not enabled:
            return [], self._trace(query=query, candidates=[], reason="external_tools_disabled")
        if not definitions:
            return [], self._trace(query=query, candidates=[], reason="empty_catalog")

        scored = [self._score_tool(tool=tool, query=query) for tool in definitions]
        scored.sort(key=lambda candidate: (-candidate.score, candidate.definition.tool_key))

        selected: list[ToolCandidate] = []
        seen: set[str] = set()
        seen_categories: set[str] = set()
        for candidate in scored:
            if candidate.score <= 0:
                continue
            if candidate.definition.category in seen_categories:
                continue
            selected.append(candidate)
            seen.add(candidate.definition.tool_key)
            seen_categories.add(candidate.definition.category)
            if len(selected) >= self.max_candidates:
                break
        for candidate in scored:
            if candidate.score <= 0 or candidate.definition.tool_key in seen:
                continue
            selected.append(candidate)
            seen.add(candidate.definition.tool_key)
            if len(selected) >= self.max_candidates:
                break

        # Always keep a web-search fallback if it exists and fits the budget.
        if len(selected) < self.max_candidates and "web.tavily.search" not in seen:
            fallback = self.catalog.get_or_none("web.tavily.search")
            if fallback and fallback.enabled_by_default:
                selected.append(ToolCandidate(definition=fallback, score=0.35, reasons=["web_search_fallback"]))

        # If the query did not match anything, keep the strongest generic read-only tools.
        if not selected:
            selected = [
                ToolCandidate(definition=tool, score=0.2, reasons=["generic_enabled_tool"])
                for tool in definitions[: self.max_candidates]
            ]

        selected = selected[: self.max_candidates]
        return [candidate.definition for candidate in selected], self._trace(
            query=query,
            candidates=selected,
            reason="ranked_by_query_and_tool_metadata",
        )

    def _score_tool(self, *, tool: ToolDefinition, query: str) -> ToolCandidate:
        score = 0.0
        reasons: list[str] = []

        category_pattern = self.CATEGORY_PATTERNS.get(tool.category)
        if category_pattern and category_pattern.search(query):
            score += 1.4
            reasons.append(f"category_match:{tool.category}")

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
        if tool.read_only:
            score += 0.1
            reasons.append("read_only")
        if tool.risk_level == "high":
            score -= 0.35
            reasons.append("high_risk_penalty")

        return ToolCandidate(definition=tool, score=round(score, 3), reasons=reasons)

    @staticmethod
    def _trace(*, query: str, candidates: list[ToolCandidate], reason: str) -> dict:
        return {
            "type": "tool_candidate_selection",
            "selector": "tool_candidate_selector_v1",
            "reason": reason,
            "query_preview": query[:240],
            "selected_count": len(candidates),
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

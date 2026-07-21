from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class QueryRewriteResult:
    original_query: str
    rewritten_query: str
    did_rewrite: bool
    reason: str = ""
    extracted_places: list[str] | None = None


class QueryRewriteService:
    """Lightweight rule-based rewrite for tool routing.

    This is intentionally narrower than a full LLM router. It only resolves
    common map-distance coreferences so the map provider can receive a
    standalone query.
    """

    COREFERENCE_PATTERN = re.compile(r"(他们|它们|这些|那些|这几个|那几个|上面|前面|刚才)")
    MAP_DISTANCE_PATTERN = re.compile(r"(多远|相距|距离|几公里|多少公里|开车多久|步行多久|要多久|多久到)")
    DESTINATION_PATTERN = re.compile(r"(?:离|距离|到)(.+?)(?:有)?(?:多远|多少公里|几公里|要多久|多久|开车多久|步行多久|$)")
    PLACE_HINT_PATTERN = re.compile(
        r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,30}?"
        r"(?:村|镇|乡|街道|区|县|市|省|路|街|大道|学校|大学|医院|酒店|公司|园区|机场|车站|站|广场|中心|大厦|公园))"
    )
    NOISE_PATTERN = re.compile(
        r"(用户|助手|回答|工具|来源|问题|请问|帮我|查询|查一下|一下|分别|距离|路线|导航|地图|天气|"
        r"怎么走|怎么去|多远|相距|开车|步行|公交|地铁|多久|哪里|在哪|位置|地址)"
    )

    def rewrite(self, *, query: str, recent_messages: list[object] | None = None) -> QueryRewriteResult:
        original_query = (query or "").strip()
        if not original_query:
            return QueryRewriteResult(original_query=query, rewritten_query=query, did_rewrite=False)

        if not self._should_rewrite(original_query):
            return QueryRewriteResult(original_query=original_query, rewritten_query=original_query, did_rewrite=False)

        destination = self._extract_destination(original_query)
        if not destination:
            return QueryRewriteResult(
                original_query=original_query,
                rewritten_query=original_query,
                did_rewrite=False,
                reason="未识别到目标地点。",
            )

        places = self._extract_recent_places(recent_messages or [], exclude=destination)
        if not places:
            return QueryRewriteResult(
                original_query=original_query,
                rewritten_query=original_query,
                did_rewrite=False,
                reason="最近历史中没有可用地点候选。",
            )

        rewritten = f"{'、'.join(places[:4])}分别到{destination}有多远"
        return QueryRewriteResult(
            original_query=original_query,
            rewritten_query=rewritten,
            did_rewrite=True,
            reason="检测到地图距离类指代问题，已用最近历史地点改写为独立查询。",
            extracted_places=places[:4],
        )

    def _should_rewrite(self, query: str) -> bool:
        return bool(self.COREFERENCE_PATTERN.search(query) and self.MAP_DISTANCE_PATTERN.search(query))

    def _extract_destination(self, query: str) -> str | None:
        compact = re.sub(r"\s+", "", query)
        match = self.DESTINATION_PATTERN.search(compact)
        if not match:
            return None
        destination = self._clean_place(match.group(1))
        return destination or None

    def _extract_recent_places(self, recent_messages: list[object], *, exclude: str) -> list[str]:
        places: list[str] = []
        recent = recent_messages[-8:]
        # Prefer places explicitly written by the user. Assistant history can be
        # hallucinated or contain explanatory locations unrelated to the reference.
        for preferred_role in ("user", "assistant"):
            for message in reversed(recent):
                content = getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")
                role = getattr(message, "role", None) if not isinstance(message, dict) else message.get("role")
                if role != preferred_role or not content:
                    continue
                for split_place in self._extract_split_places(str(content)):
                    place = self._clean_place(split_place)
                    if not place or place in places or place == exclude or place in exclude or exclude in place:
                        continue
                    places.append(place)
                    if len(places) >= 2:
                        return places
                for match in self.PLACE_HINT_PATTERN.finditer(str(content)):
                    place = self._clean_place(match.group(1))
                    if not place or place in places or place == exclude or place in exclude or exclude in place:
                        continue
                    if self.NOISE_PATTERN.fullmatch(place):
                        continue
                    places.append(place)
                    if len(places) >= 4:
                        return places
        return places

    @staticmethod
    def _extract_split_places(content: str) -> list[str]:
        text = re.sub(r"。.*$", "", content)
        text = re.sub(r"(这两个地点|这些地点|两个地点|地点|位于[\u4e00-\u9fa5A-Za-z0-9·（）()]+)", "", text)
        if not re.search(r"(和|与|跟|、|,|，)", text):
            return []
        candidates = re.split(r"(?:和|与|跟|、|,|，)", text)
        return [candidate for candidate in candidates if 2 <= len(candidate.strip()) <= 20]

    @staticmethod
    def _clean_place(value: str) -> str:
        cleaned = re.sub(r"[，,。！？?；;：:、\[\]【】\"'“”‘’]", "", value or "")
        cleaned = re.sub(r"^(他们|它们|这些|那些|这几个|那几个|上面|前面|刚才|分别)", "", cleaned)
        cleaned = re.sub(r"(有)?(多远|多少公里|几公里|要多久|多久|开车多久|步行多久)$", "", cleaned)
        return cleaned.strip()

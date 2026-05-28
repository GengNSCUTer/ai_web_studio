from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.services.tools.schemas import ExternalSource


class TavilySearchProvider:
    async def query(self, query: str) -> list[ExternalSource]:
        api_key = settings.tavily_api_key.strip()
        if not api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY")

        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "include_raw_content": False,
            "max_results": 5,
        }
        async with httpx.AsyncClient(timeout=settings.external_tool_timeout_seconds) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            data = response.json()

        sources: list[ExternalSource] = []
        answer = (data.get("answer") or "").strip()
        if answer:
            sources.append(
                ExternalSource(
                    source_type="web",
                    provider="tavily",
                    title="Tavily 综合摘要",
                    display_text=answer,
                    rank=0,
                    citation_label="[S]",
                )
            )

        for index, item in enumerate(data.get("results", []) or [], start=1):
            url = (item.get("url") or "").strip() or None
            title = (item.get("title") or "").strip() or (urlparse(url or "").netloc or "搜索结果")
            content = (item.get("content") or item.get("snippet") or "").strip()
            if not content and not url:
                continue
            sources.append(
                ExternalSource(
                    source_type="web",
                    provider="tavily",
                    title=title,
                    url=url,
                    display_text=content[:1600],
                    rank=index,
                    score=item.get("score"),
                    citation_label=f"[{index}]",
                    metadata={"domain": urlparse(url or "").netloc},
                )
            )
        return sources

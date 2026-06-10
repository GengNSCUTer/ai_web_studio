from __future__ import annotations

from app.services.chat_provider_service import ChatProviderService


class KnowledgeModelCatalogService:
    EMBEDDING_KEYWORDS = ("embedding", "embed", "bge", "gte", "e5", "bce")
    RERANK_KEYWORDS = ("rerank", "reranker", "ranker", "bge-reranker", "bce-reranker")

    async def list_options(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str | None,
        model_kind: str,
        strict: bool = False,
    ) -> tuple[list[str], str]:
        normalized_provider = (provider or "siliconflow").strip()
        normalized_kind = "rerank" if model_kind == "rerank" else "embedding"

        if normalized_provider in {"siliconflow", "openai-compatible", "ollama"}:
            provider_type = "ollama" if normalized_provider == "ollama" else "openai-compatible"
            try:
                remote_models = await ChatProviderService().list_models(
                    provider_type=provider_type,
                    base_url=base_url,
                    api_key=api_key,
                )
            except Exception:
                if strict:
                    raise
                return [], "remote-unavailable"
            filtered_remote = self._filter_models(remote_models, normalized_kind)
            return self._dedupe(filtered_remote or remote_models), "remote"

        return [], "unsupported-provider"

    def _filter_models(self, models: list[str], model_kind: str) -> list[str]:
        keywords = self.RERANK_KEYWORDS if model_kind == "rerank" else self.EMBEDDING_KEYWORDS
        return [model for model in models if any(keyword in model.lower() for keyword in keywords)]

    @staticmethod
    def _dedupe(models: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for model in models:
            normalized = model.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result

import json
from datetime import datetime, timezone

from app.models.tool_config import UserToolCredential
from app.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeJob
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeJobRepository,
    KnowledgeRetrievalLogRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    KnowledgeConnectionTestResponse,
    KnowledgeCredentialResponse,
    KnowledgeCredentialUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentIndexResponse,
    KnowledgeDocumentParseResponse,
    KnowledgeDocumentResponse,
    KnowledgeJobResponse,
    KnowledgeMarkdownPreviewResponse,
    KnowledgeMarkdownChunkResponse,
    KnowledgeRetrievalChunkResponse,
    KnowledgeRetrievalTestResponse,
)
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeIndexService
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalFilter, KnowledgeRetrievalPipeline
from app.services.knowledge_parser_service import KnowledgeParserService
from app.services.knowledge_model_metadata import infer_embedding_dimensions
from app.services.secret_service import SecretService
from app.services.setting_service import SettingService
from app.services.tools.credentials import ToolCredentialResolver


class KnowledgeBaseService:
    ALLOWED_PARSERS = {"local_basic", "mineru"}
    ALLOWED_CHUNK_MODES = {"general", "parent_child"}
    ALLOWED_EMBEDDING_PROVIDERS = {"siliconflow", "openai-compatible", "ollama"}
    ALLOWED_RERANK_PROVIDERS = {"siliconflow", "openai-compatible", "ollama"}
    ALLOWED_RETRIEVAL_MODES = {"vector"}

    def __init__(self, repo: KnowledgeBaseRepository, project_repo: ProjectRepository):
        self.repo = repo
        self.project_repo = project_repo

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _validate_project(self, project_id: str | None, user_id: str) -> bool:
        return project_id is None or self.project_repo.get_by_user(project_id, user_id) is not None

    def _to_response(self, item: KnowledgeBase) -> KnowledgeBaseResponse:
        response = KnowledgeBaseResponse.model_validate(item)
        response.document_count = self.repo.document_count(item.id, item.user_id)
        return response

    def list_knowledge_bases(self, user_id: str) -> list[KnowledgeBaseResponse]:
        return [self._to_response(item) for item in self.repo.list_by_user(user_id)]

    def get_knowledge_base(self, knowledge_base_id: str, user_id: str) -> KnowledgeBaseResponse | None:
        item = self.repo.get_by_user(knowledge_base_id, user_id)
        if not item:
            return None
        return self._to_response(item)

    def create_knowledge_base(
        self,
        user_id: str,
        payload: KnowledgeBaseCreate,
    ) -> KnowledgeBaseResponse | None:
        if not self._validate_project(payload.project_id, user_id):
            return None
        if payload.parser_provider not in self.ALLOWED_PARSERS:
            raise ValueError("Unsupported parser provider")
        if payload.chunk_mode not in self.ALLOWED_CHUNK_MODES:
            raise ValueError("Unsupported chunk mode")
        if payload.embedding_provider not in self.ALLOWED_EMBEDDING_PROVIDERS:
            raise ValueError("Unsupported embedding provider")
        if payload.rerank_provider not in self.ALLOWED_RERANK_PROVIDERS:
            raise ValueError("Unsupported rerank provider")
        if payload.retrieval_mode not in self.ALLOWED_RETRIEVAL_MODES:
            raise ValueError("Unsupported retrieval mode")
        if payload.chunk_overlap >= payload.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if payload.rerank_top_n > payload.retrieval_top_k:
            raise ValueError("Rerank top N cannot exceed retrieval top K")

        item = KnowledgeBase(
            user_id=user_id,
            project_id=payload.project_id,
            name=payload.name.strip(),
            description=self._normalize_optional_text(payload.description),
            visibility="private",
            parser_provider=payload.parser_provider,
            chunk_mode=payload.chunk_mode,
            chunk_size=payload.chunk_size,
            chunk_overlap=payload.chunk_overlap,
            chunk_delimiter=payload.chunk_delimiter,
            embedding_provider=payload.embedding_provider,
            embedding_model=payload.embedding_model.strip(),
            embedding_dimensions=infer_embedding_dimensions(payload.embedding_model, payload.embedding_dimensions),
            rerank_enabled=payload.rerank_enabled,
            rerank_provider=payload.rerank_provider,
            rerank_model=payload.rerank_model.strip(),
            retrieval_mode=payload.retrieval_mode,
            retrieval_top_k=payload.retrieval_top_k,
            rerank_top_n=payload.rerank_top_n,
            score_threshold=payload.score_threshold,
            max_context_chunks=payload.max_context_chunks,
            max_context_chars=payload.max_context_chars,
            strict_knowledge_answer=payload.strict_knowledge_answer,
        )
        return self._to_response(self.repo.save(item))

    def update_knowledge_base(
        self,
        knowledge_base_id: str,
        user_id: str,
        payload: KnowledgeBaseUpdate,
    ) -> KnowledgeBaseResponse | None:
        item = self.repo.get_by_user(knowledge_base_id, user_id)
        if not item:
            return None
        data = payload.model_dump(exclude_unset=True)
        if "project_id" in data and not self._validate_project(data["project_id"], user_id):
            return None
        if "name" in data and data["name"] is not None:
            item.name = data["name"].strip()
        if "description" in data:
            item.description = self._normalize_optional_text(data["description"])
        if "project_id" in data:
            item.project_id = data["project_id"]
        if "retrieval_top_k" in data and data["retrieval_top_k"] is not None:
            item.retrieval_top_k = data["retrieval_top_k"]
        if "rerank_top_n" in data and data["rerank_top_n"] is not None:
            if data["rerank_top_n"] > item.retrieval_top_k:
                raise ValueError("Rerank top N cannot exceed retrieval top K")
            item.rerank_top_n = data["rerank_top_n"]
        if "score_threshold" in data and data["score_threshold"] is not None:
            item.score_threshold = data["score_threshold"]
        if "max_context_chunks" in data and data["max_context_chunks"] is not None:
            item.max_context_chunks = data["max_context_chunks"]
        if "max_context_chars" in data and data["max_context_chars"] is not None:
            item.max_context_chars = data["max_context_chars"]
        if "strict_knowledge_answer" in data and data["strict_knowledge_answer"] is not None:
            item.strict_knowledge_answer = data["strict_knowledge_answer"]
        return self._to_response(self.repo.save(item))

    def delete_knowledge_base(self, knowledge_base_id: str, user_id: str) -> bool:
        item = self.repo.get_by_user(knowledge_base_id, user_id)
        if not item:
            return False
        KnowledgeRetrievalLogRepository(self.repo.db).delete_by_knowledge_base(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
        )
        self.repo.delete(item)
        return True


class KnowledgeDocumentService:
    def __init__(
        self,
        document_repo: KnowledgeDocumentRepository,
        base_repo: KnowledgeBaseRepository,
        job_repo: KnowledgeJobRepository,
        chunk_repo: KnowledgeChunkRepository | None = None,
        setting_repo: UserSettingRepository | None = None,
    ):
        self.document_repo = document_repo
        self.base_repo = base_repo
        self.job_repo = job_repo
        self.chunk_repo = chunk_repo or KnowledgeChunkRepository(document_repo.db)
        self.setting_repo = setting_repo or UserSettingRepository(document_repo.db)

    def list_documents(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeDocumentResponse] | None:
        if not self.base_repo.get_by_user(knowledge_base_id, user_id):
            return None
        return [
            KnowledgeDocumentResponse.model_validate(item)
            for item in self.document_repo.list_by_knowledge_base(knowledge_base_id, user_id)
        ]

    def add_document(
        self,
        knowledge_base_id: str,
        user_id: str,
        payload: KnowledgeDocumentCreate,
    ) -> KnowledgeDocumentResponse | None:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, user_id)
        if not knowledge_base:
            return None
        if not payload.storage_key.startswith(f"{user_id}/"):
            raise ValueError("Invalid storage key")

        document = KnowledgeDocument(
            knowledge_base_id=knowledge_base.id,
            user_id=user_id,
            project_id=knowledge_base.project_id,
            file_name=payload.file_name,
            mime_type=payload.mime_type,
            file_size=payload.file_size,
            storage_key=payload.storage_key,
            parser_provider=knowledge_base.parser_provider,
            parse_status="pending",
            index_status="pending",
        )
        saved = self.document_repo.save(document)
        job = KnowledgeJob(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            document_id=saved.id,
            job_type="parse_document",
            status="pending",
            payload_json=json.dumps({"storage_key": saved.storage_key}, ensure_ascii=False),
        )
        self.job_repo.save(job)
        return KnowledgeDocumentResponse.model_validate(saved)

    def delete_document(self, knowledge_base_id: str, document_id: str, user_id: str) -> bool:
        document = self.document_repo.get_by_user(document_id, user_id)
        if not document or document.knowledge_base_id != knowledge_base_id:
            return False
        self.document_repo.delete(document)
        return True

    def parse_document(
        self,
        knowledge_base_id: str,
        document_id: str,
        user_id: str,
    ) -> KnowledgeDocumentParseResponse | None:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, user_id)
        document = self.document_repo.get_by_user(document_id, user_id)
        if not knowledge_base or not document or document.knowledge_base_id != knowledge_base.id:
            return None

        job = self.job_repo.latest_by_document_type(
            document_id=document.id,
            user_id=user_id,
            job_type="parse_document",
        )
        if not job or job.status not in {"pending", "failed"}:
            job = KnowledgeJob(
                user_id=user_id,
                knowledge_base_id=knowledge_base.id,
                document_id=document.id,
                job_type="parse_document",
                status="pending",
                payload_json=json.dumps({"storage_key": document.storage_key}, ensure_ascii=False),
            )
            job = self.job_repo.save(job)

        now = datetime.now(timezone.utc)
        job.status = "running"
        job.started_at = now
        job.finished_at = None
        job.error_message = None
        document.parse_status = "parsing"
        document.error_message = None
        self.job_repo.save(job)
        self.document_repo.save(document)

        parser = KnowledgeParserService(credential_resolver=ToolCredentialResolver(self.document_repo.db))
        try:
            result = parser.parse(document=document, user_id=user_id)
            document.parse_status = "parsed"
            document.index_status = "pending"
            document.parsed_markdown_path = result.markdown_path
            document.parsed_assets_json = result.assets_json
            document.error_message = None
            saved_document = self.document_repo.save(document)
            job.status = "succeeded"
            job.finished_at = datetime.now(timezone.utc)
            saved_job = self.job_repo.save(job)
            return KnowledgeDocumentParseResponse(
                document=KnowledgeDocumentResponse.model_validate(saved_document),
                job=KnowledgeJobResponse.model_validate(saved_job),
                markdown_preview=result.markdown_preview,
            )
        except Exception as exc:
            document.parse_status = "failed"
            document.error_message = str(exc)
            saved_document = self.document_repo.save(document)
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            saved_job = self.job_repo.save(job)
            return KnowledgeDocumentParseResponse(
                document=KnowledgeDocumentResponse.model_validate(saved_document),
                job=KnowledgeJobResponse.model_validate(saved_job),
                markdown_preview=None,
            )

    def preview_markdown(
        self,
        knowledge_base_id: str,
        document_id: str,
        user_id: str,
    ) -> KnowledgeMarkdownPreviewResponse | None:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, user_id)
        document = self.document_repo.get_by_user(document_id, user_id)
        if not knowledge_base or not document or document.knowledge_base_id != knowledge_base.id:
            return None
        if not document.parsed_markdown_path:
            raise ValueError("文档尚未解析，暂无 Markdown 预览。")
        markdown = KnowledgeParserService().preview_markdown(
            markdown_path=document.parsed_markdown_path,
            user_id=user_id,
        )
        chunks = self.chunk_repo.list_by_document(document.id, user_id)
        return KnowledgeMarkdownPreviewResponse(
            document_id=document.id,
            file_name=document.file_name,
            markdown=markdown,
            chunks=[
                KnowledgeMarkdownChunkResponse(
                    chunk_id=chunk.id,
                    chunk_index=chunk.chunk_index,
                    source_start=chunk.source_start,
                    source_end=chunk.source_end,
                    content=chunk.content,
                )
                for chunk in chunks
            ],
        )

    def index_document(
        self,
        knowledge_base_id: str,
        document_id: str,
        user_id: str,
    ) -> KnowledgeDocumentIndexResponse | None:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, user_id)
        document = self.document_repo.get_by_user(document_id, user_id)
        if not knowledge_base or not document or document.knowledge_base_id != knowledge_base.id:
            return None

        job = KnowledgeJob(
            user_id=user_id,
            knowledge_base_id=knowledge_base.id,
            document_id=document.id,
            job_type="index_document",
            status="running",
            payload_json=json.dumps(
                {
                    "embedding_provider": knowledge_base.embedding_provider,
                    "embedding_model": knowledge_base.embedding_model,
                    "embedding_dimensions": knowledge_base.embedding_dimensions,
                },
                ensure_ascii=False,
            ),
            started_at=datetime.now(timezone.utc),
        )
        job = self.job_repo.save(job)
        document.index_status = "indexing"
        document.error_message = None
        self.document_repo.save(document)

        index_service = KnowledgeIndexService(
            chunk_repo=self.chunk_repo,
            document_repo=self.document_repo,
            setting_service=SettingService(self.setting_repo),
        )
        try:
            result = index_service.index_document(user_id=user_id, knowledge_base=knowledge_base, document=document)
            saved_document = self.document_repo.get_by_user(document.id, user_id) or document
            job.status = "succeeded"
            job.finished_at = datetime.now(timezone.utc)
            saved_job = self.job_repo.save(job)
            return KnowledgeDocumentIndexResponse(
                document=KnowledgeDocumentResponse.model_validate(saved_document),
                job=KnowledgeJobResponse.model_validate(saved_job),
                chunk_count=result.chunk_count,
                index_path=result.index_path,
            )
        except Exception as exc:
            document.index_status = "failed"
            document.error_message = str(exc)
            saved_document = self.document_repo.save(document)
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            saved_job = self.job_repo.save(job)
            return KnowledgeDocumentIndexResponse(
                document=KnowledgeDocumentResponse.model_validate(saved_document),
                job=KnowledgeJobResponse.model_validate(saved_job),
                chunk_count=0,
                index_path=None,
            )

    def test_retrieval(
        self,
        knowledge_base_id: str,
        user_id: str,
        query: str,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        file_types: list[str] | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        section_query: str | None = None,
    ) -> KnowledgeRetrievalTestResponse | None:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, user_id)
        if not knowledge_base:
            return None
        if page_start is not None and page_end is not None and page_start > page_end:
            raise ValueError("页码范围不合法：起始页不能大于结束页。")
        resolved_top_k = top_k or knowledge_base.retrieval_top_k
        filters = KnowledgeRetrievalFilter(
            document_ids=self._valid_document_filter_ids(
                knowledge_base_id=knowledge_base_id,
                user_id=user_id,
                document_ids=document_ids or [],
            ),
            file_types=self._normalize_file_types(file_types or []),
            page_start=page_start,
            page_end=page_end,
            section_query=self._normalize_optional_text(section_query),
        )
        retrieval_pipeline = KnowledgeRetrievalPipeline(
            chunk_repo=self.chunk_repo,
            setting_service=SettingService(self.setting_repo),
        )
        results = retrieval_pipeline.retrieve(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            top_k=resolved_top_k,
            filters=filters,
        )
        documents = {
            document.id: document
            for document in self.document_repo.list_by_knowledge_base(knowledge_base_id, user_id)
        }
        return KnowledgeRetrievalTestResponse(
            query=query,
            top_k=resolved_top_k,
            total_chunks=self.chunk_repo.count_by_knowledge_base(knowledge_base_id, user_id),
            rerank_enabled=knowledge_base.rerank_enabled,
            rerank_model=knowledge_base.rerank_model if knowledge_base.rerank_enabled else None,
            filters=filters.to_public_dict(),
            results=[
                KnowledgeRetrievalChunkResponse(
                    chunk_id=result.chunk.id,
                    document_id=result.chunk.document_id,
                    file_name=documents.get(result.chunk.document_id).file_name
                    if documents.get(result.chunk.document_id)
                    else result.metadata.get("file_name", "unknown"),
                    chunk_index=result.chunk.chunk_index,
                    score=result.rerank_score if result.rerank_score is not None else result.score,
                    vector_score=result.score,
                    rerank_score=result.rerank_score,
                    rank_source=result.rank_source,
                    content=result.chunk.content,
                    metadata=result.metadata,
                )
                for result in results
            ],
        )

    @staticmethod
    def _normalize_file_types(file_types: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in file_types:
            value = item.strip().lower()
            if not value:
                continue
            if value in {"application/pdf", "pdf"}:
                value = "pdf"
            elif value in {"text/markdown", "markdown", "md"}:
                value = "markdown"
            elif value in {"text/plain", "plain", "txt"}:
                value = "text"
            elif value in {"text/html", "html"}:
                value = "html"
            if value not in normalized:
                normalized.append(value)
        return normalized

    def _valid_document_filter_ids(
        self,
        *,
        knowledge_base_id: str,
        user_id: str,
        document_ids: list[str],
    ) -> list[str]:
        if not document_ids:
            return []
        valid_ids = {
            document.id
            for document in self.document_repo.list_by_knowledge_base(knowledge_base_id, user_id)
        }
        unique_ids = list(dict.fromkeys(document_ids))
        invalid_ids = [document_id for document_id in unique_ids if document_id not in valid_ids]
        if invalid_ids:
            raise ValueError("检索过滤文档不存在或不属于当前知识库。")
        return unique_ids

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class KnowledgeJobService:
    def __init__(self, job_repo: KnowledgeJobRepository, base_repo: KnowledgeBaseRepository):
        self.job_repo = job_repo
        self.base_repo = base_repo

    def list_jobs(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeJobResponse] | None:
        if not self.base_repo.get_by_user(knowledge_base_id, user_id):
            return None
        return [
            KnowledgeJobResponse.model_validate(item)
            for item in self.job_repo.list_by_knowledge_base(knowledge_base_id, user_id)
        ]


class KnowledgeCredentialService:
    MINERU_PROVIDER_KEY = "mineru"

    def __init__(self, repo: ToolConfigRepository):
        self.repo = repo
        self.secret_service = SecretService()

    def get_mineru_credential(self, user_id: str) -> KnowledgeCredentialResponse:
        credential = self.repo.get_credential(user_id, self.MINERU_PROVIDER_KEY)
        resolved = ToolCredentialResolver(self.repo.db).resolve(user_id=user_id, provider_key=self.MINERU_PROVIDER_KEY)
        if credential:
            saved_key = self.secret_service.decrypt(credential.api_key)
            return KnowledgeCredentialResponse(
                provider_key=self.MINERU_PROVIDER_KEY,
                credential_name=credential.credential_name,
                is_enabled=credential.is_enabled,
                has_api_key=bool(saved_key or resolved.api_key),
                api_key_masked=self.secret_service.mask(saved_key or resolved.api_key),
                source="user" if saved_key else resolved.source,
            )
        return KnowledgeCredentialResponse(
            provider_key=self.MINERU_PROVIDER_KEY,
            credential_name="MinerU 默认凭据",
            is_enabled=resolved.is_enabled,
            has_api_key=bool(resolved.api_key),
            api_key_masked=self.secret_service.mask(resolved.api_key),
            source=resolved.source,
        )

    def update_mineru_credential(
        self,
        user_id: str,
        payload: KnowledgeCredentialUpdate,
    ) -> KnowledgeCredentialResponse:
        credential = self.repo.get_credential(user_id, self.MINERU_PROVIDER_KEY)
        if not credential:
            credential = UserToolCredential(
                user_id=user_id,
                provider_key=self.MINERU_PROVIDER_KEY,
                credential_name="MinerU 默认凭据",
                api_key=None,
                is_enabled=True,
            )

        data = payload.model_dump(exclude_unset=True)
        if "credential_name" in data and data["credential_name"] is not None:
            credential.credential_name = data["credential_name"].strip() or "MinerU 默认凭据"
        if data.get("clear_api_key"):
            credential.api_key = None
        elif "api_key" in data and data["api_key"] is not None:
            credential.api_key = self.secret_service.encrypt(data["api_key"])
        if "is_enabled" in data and data["is_enabled"] is not None:
            credential.is_enabled = bool(data["is_enabled"])
        self.repo.save_credential(credential)
        return self.get_mineru_credential(user_id)

    def test_mineru_credential(self, user_id: str) -> KnowledgeConnectionTestResponse:
        ok, message = KnowledgeParserService(
            credential_resolver=ToolCredentialResolver(self.repo.db),
        ).test_mineru_connection(user_id=user_id)
        return KnowledgeConnectionTestResponse(
            ok=ok,
            provider_key=self.MINERU_PROVIDER_KEY,
            message=message,
        )

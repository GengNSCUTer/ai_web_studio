import json

from app.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeJob
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeDocumentRepository,
    KnowledgeJobRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentResponse,
    KnowledgeJobResponse,
)


class KnowledgeBaseService:
    ALLOWED_PARSERS = {"local_basic", "mineru"}
    ALLOWED_CHUNK_MODES = {"general", "parent_child"}
    ALLOWED_EMBEDDING_PROVIDERS = {"siliconflow"}
    ALLOWED_RERANK_PROVIDERS = {"siliconflow"}
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
            embedding_dimensions=payload.embedding_dimensions,
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
        self.repo.delete(item)
        return True


class KnowledgeDocumentService:
    def __init__(
        self,
        document_repo: KnowledgeDocumentRepository,
        base_repo: KnowledgeBaseRepository,
        job_repo: KnowledgeJobRepository,
    ):
        self.document_repo = document_repo
        self.base_repo = base_repo
        self.job_repo = job_repo

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

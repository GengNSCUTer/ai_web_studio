"""Stable, non-sensitive error classification for knowledge workflows."""

from __future__ import annotations

from typing import Any


class KnowledgeRetrievalUnavailableError(RuntimeError):
    """Both channels of a hybrid retrieval request failed before ranking."""

    error_code = "hybrid_both_unavailable"


def classify_knowledge_error(exc: BaseException) -> str:
    """Map an internal exception to a small public error-code vocabulary.

    The original exception remains useful in server logs, but it must never be
    persisted in a user-facing notice or an evaluation record.  Keep this
    classifier deliberately conservative: unknown failures are grouped under a
    generic code instead of trying to expose provider-specific details.
    """

    if isinstance(exc, KnowledgeRetrievalUnavailableError):
        return exc.error_code
    if isinstance(exc, (TimeoutError,)):
        return "provider_timeout"
    if isinstance(exc, (ConnectionError,)):
        return "provider_unavailable"
    if isinstance(exc, (PermissionError, FileNotFoundError, ValueError)):
        return "invalid_request"
    message = str(exc).lower()
    if any(marker in message for marker in ("timeout", "timed out", "rate limit", "429", "502", "503")):
        return "provider_unavailable"
    return "knowledge_retrieval_failed"


def public_knowledge_error_message(error_code: str, *, knowledge_base_name: str | None = None) -> str:
    """Return a safe message suitable for API responses and persisted records."""

    prefix = f"知识库「{knowledge_base_name}」" if knowledge_base_name else "知识库"
    messages = {
        "provider_timeout": f"{prefix}检索超时，已跳过本轮知识库上下文，请稍后重试。",
        "provider_unavailable": f"{prefix}依赖的检索服务暂时不可用，已跳过本轮知识库上下文，请稍后重试。",
        "hybrid_both_unavailable": f"{prefix}的向量与词法检索暂时都不可用，已跳过本轮知识库上下文，请稍后重试。",
        "invalid_request": f"{prefix}检索参数或资源状态不合法，已跳过本轮知识库上下文。",
        "knowledge_retrieval_failed": f"{prefix}检索失败，已跳过本轮知识库上下文，请稍后重试。",
        "evaluation_failed": "评测运行失败，请稍后重试或查看服务日志。",
        "evaluation_case_failed": "该评测 Case 执行失败，已记录为失败样本。",
    }
    return messages.get(error_code, "知识库操作失败，请稍后重试。")


def public_knowledge_error_payload(exc: BaseException, *, scope: str) -> dict[str, Any]:
    """Build a stable error payload without copying exception text."""

    error_code = classify_knowledge_error(exc)
    return {"scope": scope, "error_code": error_code}

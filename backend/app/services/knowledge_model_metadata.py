DEFAULT_EMBEDDING_DIMENSIONS = 1024


def infer_embedding_dimensions(model: str | None, fallback: int = DEFAULT_EMBEDDING_DIMENSIONS) -> int:
    normalized = (model or "").lower()

    if "qwen3-embedding-8b" in normalized:
        return 4096
    if "qwen3-embedding-4b" in normalized:
        return 2560
    if "qwen3-embedding-0.6b" in normalized:
        return 1024
    if "bge-m3" in normalized:
        return 1024
    if "bge-large" in normalized:
        return 1024
    if "bce-embedding-base" in normalized:
        return 768

    return fallback

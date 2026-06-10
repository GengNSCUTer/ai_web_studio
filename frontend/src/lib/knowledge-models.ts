export function inferEmbeddingDimensions(model: string, fallback = 1024): number {
  const normalized = model.toLowerCase();

  if (normalized.includes("qwen3-embedding-8b")) {
    return 4096;
  }
  if (normalized.includes("qwen3-embedding-4b")) {
    return 2560;
  }
  if (normalized.includes("qwen3-embedding-0.6b")) {
    return 1024;
  }
  if (normalized.includes("bge-m3")) {
    return 1024;
  }
  if (normalized.includes("bge-large")) {
    return 1024;
  }
  if (normalized.includes("bce-embedding-base")) {
    return 768;
  }

  return fallback;
}

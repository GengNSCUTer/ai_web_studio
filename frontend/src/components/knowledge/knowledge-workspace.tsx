"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { MessageMarkdown } from "@/components/message-markdown";
import { inferEmbeddingDimensions } from "@/lib/knowledge-models";
import type {
  KnowledgeBase,
  KnowledgeConnectionTestResult,
  KnowledgeCredential,
  KnowledgeDocument,
  KnowledgeDocumentIndexResult,
  KnowledgeDocumentParseResult,
  KnowledgeEvalCase,
  KnowledgeEvalOutcome,
  KnowledgeEvalRun,
  KnowledgeEvalSet,
  KnowledgeJob,
  KnowledgeMarkdownPreview,
  KnowledgeRetrievalLog,
  KnowledgeRetrievalTestRequest,
  KnowledgeRetrievalTestResult,
  Project,
  UploadItem,
  User,
  UserSettings,
} from "@/lib/types";

type KnowledgeWorkspaceProps = {
  currentUser: User;
  initialKnowledgeBases: KnowledgeBase[];
  initialProjects: Project[];
  activeKnowledgeBase?: KnowledgeBase | null;
  initialDocuments?: KnowledgeDocument[];
  initialJobs?: KnowledgeJob[];
  initialMineruCredential?: KnowledgeCredential | null;
  initialSettings?: UserSettings | null;
};

type CreateFormState = {
  name: string;
  description: string;
  project_id: string;
  parser_provider: "local_basic" | "mineru";
  embedding_provider: string;
  chunk_size: number;
  chunk_overlap: number;
  embedding_model: string;
  embedding_dimensions: number;
  rerank_enabled: boolean;
  rerank_provider: string;
  rerank_model: string;
  retrieval_top_k: number;
  rerank_top_n: number;
  score_threshold: number;
  max_context_chunks: number;
  max_context_chars: number;
};

function buildDefaultCreateForm(settings?: UserSettings | null): CreateFormState {
  const embeddingModel = settings?.knowledge_embedding_model || "BAAI/bge-m3";
  const embeddingDimensions = inferEmbeddingDimensions(
    embeddingModel,
    settings?.knowledge_embedding_dimensions || 1024
  );

  return {
    name: "",
    description: "",
    project_id: "",
    parser_provider:
      settings?.knowledge_parser_provider === "mineru" ? "mineru" : "local_basic",
    embedding_provider: settings?.knowledge_embedding_provider || "siliconflow",
    chunk_size: 1000,
    chunk_overlap: 150,
    embedding_model: embeddingModel,
    embedding_dimensions: embeddingDimensions,
    rerank_enabled: settings?.knowledge_rerank_enabled ?? true,
    rerank_provider: settings?.knowledge_rerank_provider || "siliconflow",
    rerank_model: settings?.knowledge_rerank_model || "BAAI/bge-reranker-v2-m3",
    retrieval_top_k: 20,
    rerank_top_n: 6,
    score_threshold: 0.2,
    max_context_chunks: 6,
    max_context_chars: 12000,
  };
}

const PROVIDER_OPTIONS = ["siliconflow", "openai-compatible", "ollama"];
const EMBEDDING_MODELS = [
  "BAAI/bge-m3",
  "Qwen/Qwen3-Embedding-0.6B",
  "Qwen/Qwen3-Embedding-4B",
  "Qwen/Qwen3-Embedding-8B",
];
const RERANK_MODELS = ["BAAI/bge-reranker-v2-m3"];

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatBytes(value: number | null) {
  if (!value) {
    return "0 B";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function projectName(projects: Project[], projectId: string | null) {
  if (!projectId) {
    return "未绑定工作区";
  }
  return projects.find((project) => project.id === projectId)?.name ?? "未知工作区";
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    pending: "等待中",
    running: "执行中",
    succeeded: "成功",
    parsing: "解析中",
    parsed: "已解析",
    indexing: "索引中",
    indexed: "已索引",
    failed: "失败",
    deleted: "已删除",
  };
  return labels[value] ?? value;
}

function retrievalRankLabel(value: string) {
  const labels: Record<string, string> = {
    rerank: "Rerank",
    vector_fallback: "向量回退",
    vector: "向量召回",
  };
  return labels[value] ?? value;
}

function formatMetric(value: unknown) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function parseOptionalPositiveInt(value: string) {
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function formatRetrievalFilters(filters: Record<string, unknown> | null | undefined) {
  if (!filters?.enabled) {
    return "未启用过滤";
  }
  const parts: string[] = [];
  const documentIds = Array.isArray(filters.document_ids) ? filters.document_ids : [];
  const fileTypes = Array.isArray(filters.file_types) ? filters.file_types : [];
  if (documentIds.length > 0) {
    parts.push(`文档 ${documentIds.length} 个`);
  }
  if (fileTypes.length > 0) {
    parts.push(`类型 ${fileTypes.map(String).join("/")}`);
  }
  if (typeof filters.page_start === "number" || typeof filters.page_end === "number") {
    parts.push(`页码 ${filters.page_start ?? "?"}-${filters.page_end ?? "?"}`);
  }
  if (typeof filters.section_query === "string" && filters.section_query.trim()) {
    parts.push(`章节 ${filters.section_query.trim()}`);
  }
  return parts.length > 0 ? `过滤条件：${parts.join(" · ")}` : "已启用过滤";
}

export function KnowledgeWorkspace({
  currentUser,
  initialKnowledgeBases,
  initialProjects,
  activeKnowledgeBase = null,
  initialDocuments = [],
  initialJobs = [],
  initialMineruCredential = null,
  initialSettings = null,
}: KnowledgeWorkspaceProps) {
  const [knowledgeBases, setKnowledgeBases] = useState(initialKnowledgeBases);
  const [documents, setDocuments] = useState(initialDocuments);
  const [jobs, setJobs] = useState(initialJobs);
  const [mineruCredential, setMineruCredential] = useState(initialMineruCredential);
  const [mineruTokenDraft, setMineruTokenDraft] = useState("");
  const [mineruMessage, setMineruMessage] = useState<string | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(() =>
    buildDefaultCreateForm(initialSettings)
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isSavingMineru, setIsSavingMineru] = useState(false);
  const [isTestingMineru, setIsTestingMineru] = useState(false);
  const [parsingDocumentId, setParsingDocumentId] = useState<string | null>(null);
  const [indexingDocumentId, setIndexingDocumentId] = useState<string | null>(null);
  const [previewDocument, setPreviewDocument] = useState<KnowledgeMarkdownPreview | null>(null);
  const [retrievalQuery, setRetrievalQuery] = useState("");
  const [retrievalResult, setRetrievalResult] = useState<KnowledgeRetrievalTestResult | null>(null);
  const [isTestingRetrieval, setIsTestingRetrieval] = useState(false);
  const [retrievalDocumentIds, setRetrievalDocumentIds] = useState<string[]>([]);
  const [retrievalFileTypes, setRetrievalFileTypes] = useState<string[]>([]);
  const [retrievalPageStart, setRetrievalPageStart] = useState("");
  const [retrievalPageEnd, setRetrievalPageEnd] = useState("");
  const [retrievalSectionQuery, setRetrievalSectionQuery] = useState("");
  const [retrievalLogs, setRetrievalLogs] = useState<KnowledgeRetrievalLog[]>([]);
  const [isLoadingRetrievalLogs, setIsLoadingRetrievalLogs] = useState(false);
  const [evalSets, setEvalSets] = useState<KnowledgeEvalSet[]>([]);
  const [evalCases, setEvalCases] = useState<KnowledgeEvalCase[]>([]);
  const [evalRuns, setEvalRuns] = useState<KnowledgeEvalRun[]>([]);
  const [evalOutcome, setEvalOutcome] = useState<KnowledgeEvalOutcome | null>(null);
  const [selectedEvalSetId, setSelectedEvalSetId] = useState("");
  const [evalSetName, setEvalSetName] = useState("");
  const [evalCaseQuery, setEvalCaseQuery] = useState("");
  const [evalExpectedChunkId, setEvalExpectedChunkId] = useState("");
  const [isLoadingEval, setIsLoadingEval] = useState(false);
  const [isCreatingEvalSet, setIsCreatingEvalSet] = useState(false);
  const [isAddingEvalCase, setIsAddingEvalCase] = useState(false);
  const [isRunningEval, setIsRunningEval] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const sortedKnowledgeBases = useMemo(
    () =>
      [...knowledgeBases].sort(
        (left, right) =>
          new Date(right.updated_at ?? right.created_at).getTime() -
          new Date(left.updated_at ?? left.created_at).getTime()
      ),
    [knowledgeBases]
  );

  const activeId = activeKnowledgeBase?.id ?? null;
  const visibleActiveKnowledgeBase =
    activeId ? knowledgeBases.find((item) => item.id === activeId) ?? activeKnowledgeBase : null;

  function updateCreateForm<K extends keyof CreateFormState>(key: K, value: CreateFormState[K]) {
    setCreateForm((current) => ({ ...current, [key]: value }));
  }

  async function refreshDocuments(knowledgeBaseId: string) {
    const [nextDocuments, nextJobs] = await Promise.all([
      requestJson<KnowledgeDocument[]>(`/api/backend/knowledge-bases/${knowledgeBaseId}/documents`),
      requestJson<KnowledgeJob[]>(`/api/backend/knowledge-bases/${knowledgeBaseId}/jobs`),
    ]);
    setDocuments(nextDocuments);
    setJobs(nextJobs);
  }

  async function refreshRetrievalLogs(knowledgeBaseId: string) {
    setIsLoadingRetrievalLogs(true);
    setErrorMessage(null);
    try {
      const logs = await requestJson<KnowledgeRetrievalLog[]>(
        `/api/backend/knowledge-bases/${knowledgeBaseId}/retrieval-logs?limit=20`
      );
      setRetrievalLogs(logs);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "加载检索日志失败");
    } finally {
      setIsLoadingRetrievalLogs(false);
    }
  }

  async function refreshEvalData(knowledgeBaseId: string, nextEvalSetId = selectedEvalSetId) {
    setIsLoadingEval(true);
    setErrorMessage(null);
    try {
      const sets = await requestJson<KnowledgeEvalSet[]>(`/api/backend/knowledge-bases/${knowledgeBaseId}/eval-sets`);
      setEvalSets(sets);
      const resolvedEvalSetId = nextEvalSetId || sets[0]?.id || "";
      setSelectedEvalSetId(resolvedEvalSetId);
      if (!resolvedEvalSetId) {
        setEvalCases([]);
        setEvalRuns([]);
        return;
      }
      const [cases, runs] = await Promise.all([
        requestJson<KnowledgeEvalCase[]>(
          `/api/backend/knowledge-bases/${knowledgeBaseId}/eval-sets/${resolvedEvalSetId}/cases`
        ),
        requestJson<KnowledgeEvalRun[]>(
          `/api/backend/knowledge-bases/${knowledgeBaseId}/eval-sets/${resolvedEvalSetId}/runs`
        ),
      ]);
      setEvalCases(cases);
      setEvalRuns(runs);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "加载评测集失败");
    } finally {
      setIsLoadingEval(false);
    }
  }

  async function handleCreateKnowledgeBase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const payload = {
        name: createForm.name.trim(),
        description: createForm.description.trim() || null,
        project_id: createForm.project_id || null,
        parser_provider: createForm.parser_provider,
        chunk_mode: "general",
        chunk_size: createForm.chunk_size,
        chunk_overlap: createForm.chunk_overlap,
        chunk_delimiter: "\n\n",
        embedding_provider: createForm.embedding_provider,
        embedding_model: createForm.embedding_model,
        embedding_dimensions: createForm.embedding_dimensions,
        rerank_enabled: createForm.rerank_enabled,
        rerank_provider: createForm.rerank_provider,
        rerank_model: createForm.rerank_model,
        retrieval_mode: "vector",
        retrieval_top_k: createForm.retrieval_top_k,
        rerank_top_n: createForm.rerank_top_n,
        score_threshold: createForm.score_threshold,
        max_context_chunks: createForm.max_context_chunks,
        max_context_chars: createForm.max_context_chars,
        strict_knowledge_answer: false,
      };
      const created = await requestJson<KnowledgeBase>("/api/backend/knowledge-bases", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      setKnowledgeBases((current) => [created, ...current]);
      setCreateForm(buildDefaultCreateForm(initialSettings));
      setIsCreateOpen(false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "创建知识库失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files || files.length === 0) {
      return [];
    }
    const formData = new FormData();
    Array.from(files).forEach((file) => formData.append("files", file));
    const response = await fetch("/api/backend/uploads", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Upload failed: ${response.status}`);
    }
    return (await response.json()) as UploadItem[];
  }

  async function handleUploadDocuments(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!visibleActiveKnowledgeBase || !files || files.length === 0) {
      return;
    }
    setIsUploading(true);
    setErrorMessage(null);
    try {
      const uploadedItems = await uploadFiles(files);
      for (const item of uploadedItems) {
        await requestJson<KnowledgeDocument>(
          `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/documents`,
          {
            method: "POST",
            headers: {
              "content-type": "application/json",
            },
            body: JSON.stringify({
              file_name: item.file_name,
              mime_type: item.mime_type,
              file_size: item.file_size,
              storage_key: item.storage_key,
            }),
          }
        );
      }
      await refreshDocuments(visibleActiveKnowledgeBase.id);
      setKnowledgeBases((current) =>
        current.map((item) =>
          item.id === visibleActiveKnowledgeBase.id
            ? { ...item, document_count: item.document_count + uploadedItems.length }
            : item
        )
      );
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "上传文档失败。");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  async function handleSaveMineruCredential(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSavingMineru(true);
    setMineruMessage(null);
    setErrorMessage(null);
    try {
      const updated = await requestJson<KnowledgeCredential>("/api/backend/knowledge/credentials/mineru", {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          api_key: mineruTokenDraft.trim() || undefined,
          is_enabled: true,
        }),
      });
      setMineruCredential(updated);
      setMineruTokenDraft("");
      setMineruMessage("MinerU token 已保存，页面不会回显明文。");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "保存 MinerU token 失败。");
    } finally {
      setIsSavingMineru(false);
    }
  }

  async function handleTestMineruCredential() {
    setIsTestingMineru(true);
    setMineruMessage(null);
    setErrorMessage(null);
    try {
      const result = await requestJson<KnowledgeConnectionTestResult>("/api/backend/knowledge/credentials/mineru/test", {
        method: "POST",
      });
      setMineruMessage(result.message);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "测试 MinerU 连接失败。");
    } finally {
      setIsTestingMineru(false);
    }
  }

  async function handleParseDocument(document: KnowledgeDocument) {
    if (!visibleActiveKnowledgeBase) {
      return;
    }
    setParsingDocumentId(document.id);
    setErrorMessage(null);
    try {
      const result = await requestJson<KnowledgeDocumentParseResult>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/documents/${document.id}/parse`,
        { method: "POST" }
      );
      setDocuments((current) =>
        current.map((item) => (item.id === result.document.id ? result.document : item))
      );
      setJobs((current) => [result.job, ...current.filter((item) => item.id !== result.job.id)]);
      if (result.markdown_preview) {
        try {
          const fullPreview = await requestJson<KnowledgeMarkdownPreview>(
            `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/documents/${result.document.id}/markdown-preview`
          );
          setPreviewDocument(fullPreview);
        } catch {
          setPreviewDocument({
            document_id: result.document.id,
            file_name: result.document.file_name,
            markdown: result.markdown_preview,
            chunks: [],
          });
        }
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "解析文档失败。");
    } finally {
      setParsingDocumentId(null);
    }
  }

  async function handlePreviewDocument(document: KnowledgeDocument) {
    if (!visibleActiveKnowledgeBase) {
      return;
    }
    setErrorMessage(null);
    try {
      const result = await requestJson<KnowledgeMarkdownPreview>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/documents/${document.id}/markdown-preview`
      );
      setPreviewDocument(result);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "加载 Markdown 预览失败。");
    }
  }

  async function handleIndexDocument(document: KnowledgeDocument) {
    if (!visibleActiveKnowledgeBase) {
      return;
    }
    setIndexingDocumentId(document.id);
    setErrorMessage(null);
    try {
      const result = await requestJson<KnowledgeDocumentIndexResult>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/documents/${document.id}/index`,
        { method: "POST" }
      );
      setDocuments((current) =>
        current.map((item) => (item.id === result.document.id ? result.document : item))
      );
      setJobs((current) => [result.job, ...current.filter((item) => item.id !== result.job.id)]);
      if (result.chunk_count === 0 && result.job.error_message) {
        setErrorMessage(`索引失败：${result.job.error_message}`);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "生成索引失败。");
    } finally {
      setIndexingDocumentId(null);
    }
  }

  async function handleTestRetrieval(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!visibleActiveKnowledgeBase || !retrievalQuery.trim()) {
      return;
    }
    setIsTestingRetrieval(true);
    setErrorMessage(null);
    try {
      const requestBody: KnowledgeRetrievalTestRequest = {
        query: retrievalQuery.trim(),
        top_k: visibleActiveKnowledgeBase.retrieval_top_k,
        document_ids: retrievalDocumentIds,
        file_types: retrievalFileTypes,
        page_start: parseOptionalPositiveInt(retrievalPageStart),
        page_end: parseOptionalPositiveInt(retrievalPageEnd),
        section_query: retrievalSectionQuery.trim() || null,
      };
      const result = await requestJson<KnowledgeRetrievalTestResult>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/retrieval-test`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify(requestBody),
        }
      );
      setRetrievalResult(result);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "检索测试失败。");
    } finally {
      setIsTestingRetrieval(false);
    }
  }

  function toggleRetrievalDocumentId(documentId: string) {
    setRetrievalDocumentIds((current) =>
      current.includes(documentId) ? current.filter((item) => item !== documentId) : [...current, documentId]
    );
  }

  function toggleRetrievalFileType(fileType: string) {
    setRetrievalFileTypes((current) =>
      current.includes(fileType) ? current.filter((item) => item !== fileType) : [...current, fileType]
    );
  }

  async function handleCreateEvalSet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!visibleActiveKnowledgeBase || !evalSetName.trim()) {
      return;
    }
    setIsCreatingEvalSet(true);
    setErrorMessage(null);
    try {
      const created = await requestJson<KnowledgeEvalSet>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/eval-sets`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: evalSetName.trim() }),
        }
      );
      setEvalSetName("");
      await refreshEvalData(visibleActiveKnowledgeBase.id, created.id);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "创建评测集失败。");
    } finally {
      setIsCreatingEvalSet(false);
    }
  }

  async function handleSelectEvalSet(knowledgeBaseId: string, evalSetId: string) {
    setSelectedEvalSetId(evalSetId);
    setEvalOutcome(null);
    if (!evalSetId) {
      setEvalCases([]);
      setEvalRuns([]);
      return;
    }
    await refreshEvalData(knowledgeBaseId, evalSetId);
  }

  async function handleAddEvalCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!visibleActiveKnowledgeBase || !selectedEvalSetId || !evalCaseQuery.trim()) {
      return;
    }
    setIsAddingEvalCase(true);
    setErrorMessage(null);
    try {
      await requestJson<KnowledgeEvalCase>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/eval-sets/${selectedEvalSetId}/cases`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            query: evalCaseQuery.trim(),
            expected_chunk_id: evalExpectedChunkId.trim() || null,
          }),
        }
      );
      setEvalCaseQuery("");
      setEvalExpectedChunkId("");
      await refreshEvalData(visibleActiveKnowledgeBase.id, selectedEvalSetId);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "添加评测用例失败。");
    } finally {
      setIsAddingEvalCase(false);
    }
  }

  async function handleRunEvalSet() {
    if (!visibleActiveKnowledgeBase || !selectedEvalSetId) {
      return;
    }
    setIsRunningEval(true);
    setErrorMessage(null);
    try {
      const outcome = await requestJson<KnowledgeEvalOutcome>(
        `/api/backend/knowledge-bases/${visibleActiveKnowledgeBase.id}/eval-sets/${selectedEvalSetId}/runs`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ top_k: visibleActiveKnowledgeBase.retrieval_top_k }),
        }
      );
      setEvalOutcome(outcome);
      await refreshEvalData(visibleActiveKnowledgeBase.id, selectedEvalSetId);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "运行评测失败。");
    } finally {
      setIsRunningEval(false);
    }
  }

  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-4 py-5 text-[var(--ink-strong)] sm:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-2.5rem)] max-w-7xl flex-col gap-4">
        <header className="rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-5 py-4 shadow-[var(--panel-shadow)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-xs uppercase tracking-[0.35em] text-[var(--ink-muted)]">Knowledge Base</p>
              <h1 className="mt-2 text-3xl font-semibold">个人知识库</h1>
              <p className="mt-2 max-w-2xl text-sm leading-7 text-[var(--ink-soft)]">
                当前阶段已进入 RAG-3：支持文档解析、Markdown 预览、Chunk 分块、Embedding、FAISS 索引与检索测试。
                本阶段先打通文本 RAG，PDF 内图片和复杂表格先保留解析资产扩展点，后续再做 OCR / caption / 多模态索引。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href="/"
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
              >
                工作台
              </Link>
              <Link
                href="/chat"
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
              >
                智能问答
              </Link>
              <button
                type="button"
                onClick={() => setIsCreateOpen(true)}
                className="primary-action rounded-full px-5 py-2 text-sm font-medium transition hover:brightness-105"
              >
                新建知识库
              </button>
            </div>
          </div>
        </header>

        {errorMessage ? (
          <div className="rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-4 py-3 text-sm text-[var(--danger-text)]">
            {errorMessage}
          </div>
        ) : null}

        <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[320px_1fr]">
          <aside className="min-h-0 rounded-[26px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4 shadow-[var(--panel-shadow)]">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">知识库列表</h2>
                <p className="text-xs text-[var(--ink-muted)]">共 {knowledgeBases.length} 个</p>
              </div>
              <span className="rounded-full bg-[var(--accent-soft)] px-3 py-1 text-xs text-[var(--accent-strong)]">
                {currentUser.username}
              </span>
            </div>

            <div className="max-h-[calc(100vh-260px)] space-y-2 overflow-y-auto pr-1">
              {sortedKnowledgeBases.length === 0 ? (
                <div className="rounded-3xl border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] p-5 text-sm leading-7 text-[var(--ink-soft)]">
                  还没有知识库。先新建一个知识库，后续再上传文档。
                </div>
              ) : (
                sortedKnowledgeBases.map((item) => (
                  <Link
                    key={item.id}
                    href={`/knowledge/${item.id}`}
                    className={`block rounded-3xl border px-4 py-3 transition ${
                      item.id === activeId
                        ? "border-[var(--accent-strong)] bg-[var(--accent-soft)]"
                        : "border-[var(--control-border)] bg-[var(--control-bg)] hover:border-[var(--accent-strong)]"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold">{item.name}</p>
                        <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--ink-soft)]">
                          {item.description || "暂无描述"}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-full border border-[var(--control-border)] px-2 py-0.5 text-[10px] text-[var(--ink-muted)]">
                        {item.document_count} 文档
                      </span>
                    </div>
                    <div className="mt-3 flex items-center justify-between text-[11px] text-[var(--ink-muted)]">
                      <span>{projectName(initialProjects, item.project_id)}</span>
                      <span>{formatDateTime(item.updated_at ?? item.created_at)}</span>
                    </div>
                  </Link>
                ))
              )}
            </div>
          </aside>

          <section className="min-h-0 rounded-[26px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4 shadow-[var(--panel-shadow)]">
            {visibleActiveKnowledgeBase ? (
              <div className="flex h-full min-h-0 flex-col">
                <div className="flex flex-col gap-4 border-b border-[var(--hairline)] pb-4 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <p className="text-xs uppercase tracking-[0.26em] text-[var(--ink-muted)]">
                      {projectName(initialProjects, visibleActiveKnowledgeBase.project_id)}
                    </p>
                    <h2 className="mt-2 text-3xl font-semibold">{visibleActiveKnowledgeBase.name}</h2>
                    <p className="mt-2 max-w-3xl text-sm leading-7 text-[var(--ink-soft)]">
                      {visibleActiveKnowledgeBase.description || "暂无描述。"}
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                    <Metric label="文档" value={`${visibleActiveKnowledgeBase.document_count}`} />
                    <Metric label="Chunk" value={`${visibleActiveKnowledgeBase.chunk_size}/${visibleActiveKnowledgeBase.chunk_overlap}`} />
                    <Metric label="召回" value={`Top ${visibleActiveKnowledgeBase.retrieval_top_k}`} />
                    <Metric label="注入" value={`${visibleActiveKnowledgeBase.max_context_chunks} 段`} />
                  </div>
                </div>

                <div className="grid gap-4 py-4 xl:grid-cols-[1.05fr_0.85fr_0.85fr]">
                  <Panel title="配置概览" subtitle="RAG-3 已接入分块与向量索引；更换 Embedding 模型后需要重建索引。">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Info label="解析器" value={visibleActiveKnowledgeBase.parser_provider} />
                      <Info label="分块模式" value={visibleActiveKnowledgeBase.chunk_mode} />
                      <Info label="Embedding" value={visibleActiveKnowledgeBase.embedding_model} />
                      <Info label="Rerank" value={visibleActiveKnowledgeBase.rerank_enabled ? visibleActiveKnowledgeBase.rerank_model : "未启用"} />
                      <Info label="检索模式" value={visibleActiveKnowledgeBase.retrieval_mode} />
                      <Info label="分数阈值" value={`${visibleActiveKnowledgeBase.score_threshold}`} />
                    </div>
                  </Panel>

                  <Panel title="阶段状态" subtitle="当前阶段聚焦文本索引和检索可观测；聊天引用会在 RAG-5 接入。">
                    <div className="space-y-2 text-sm text-[var(--ink-soft)]">
                      <StatusLine done label="知识库配置已持久化" />
                      <StatusLine done label="文档上传记录已接入" />
                      <StatusLine done label="本地基础解析已接入" />
                      <StatusLine done label="Markdown 预览已接入" />
                      <StatusLine done label="MinerU 凭据与远程解析已接入" />
                      <StatusLine done label="Chunk / Embedding / FAISS 已接入" />
                      <StatusLine done label="检索测试已接入" />
                      <StatusLine label="聊天引用待接入" />
                    </div>
                  </Panel>

                  <Panel title="MinerU 凭据" subtitle="Token 按用户加密保存，不会在页面回显明文。">
                    <form onSubmit={handleSaveMineruCredential} className="space-y-3">
                      <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3">
                        <p className="text-xs text-[var(--ink-muted)]">当前状态</p>
                        <p className="mt-1 text-sm font-semibold">
                          {mineruCredential?.has_api_key
                            ? `已配置：${mineruCredential.api_key_masked ?? "****"}`
                            : "未配置"}
                        </p>
                        <p className="mt-1 text-xs text-[var(--ink-muted)]">
                          来源：{mineruCredential?.source ?? "missing"}
                        </p>
                      </div>
                      <input
                        type="password"
                        value={mineruTokenDraft}
                        onChange={(event) => setMineruTokenDraft(event.target.value)}
                        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
                        placeholder="输入新的 MinerU token"
                      />
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="submit"
                          disabled={isSavingMineru || !mineruTokenDraft.trim()}
                          className="primary-action rounded-full px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {isSavingMineru ? "保存中..." : "保存 Token"}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleTestMineruCredential()}
                          disabled={isTestingMineru}
                          className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {isTestingMineru ? "测试中..." : "测试连接"}
                        </button>
                      </div>
                      {mineruMessage ? (
                        <p className="rounded-2xl border border-[var(--success-border)] bg-[var(--success-bg)] px-3 py-2 text-xs text-[var(--success-text)]">
                          {mineruMessage}
                        </p>
                      ) : null}
                    </form>
                  </Panel>
                </div>

                <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                  <Panel title="文档" subtitle="上传后依次触发解析与索引；索引成功后可在右侧做检索测试。">
                    <div className="mb-4 flex flex-wrap items-center gap-2">
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        onChange={handleUploadDocuments}
                        className="hidden"
                      />
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={isUploading}
                        className="primary-action rounded-full px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {isUploading ? "上传中..." : "上传文档"}
                      </button>
                      <span className="text-xs text-[var(--ink-muted)]">
                        当前复用聊天附件上传链路，支持 txt/md/pdf/docx 等现有格式。
                      </span>
                    </div>

                    <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
                      {documents.length === 0 ? (
                        <EmptyBox text="还没有文档。上传文件后，这里会出现文档记录和解析状态。" />
                      ) : (
                        documents.map((document) => (
                          <div
                            key={document.id}
                            className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <p className="truncate text-sm font-semibold">{document.file_name}</p>
                                <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                  {document.mime_type || "unknown"} · {formatBytes(document.file_size)}
                                </p>
                              </div>
                              <span className="rounded-full bg-[var(--soft-bg)] px-3 py-1 text-xs text-[var(--ink-soft)]">
                                v{document.document_version}
                              </span>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2 text-xs">
                              <Badge label={`解析：${statusLabel(document.parse_status)}`} />
                              <Badge label={`索引：${statusLabel(document.index_status)}`} />
                              <Badge label={formatDateTime(document.created_at)} />
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() => void handleParseDocument(document)}
                                disabled={parsingDocumentId === document.id}
                                className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)] disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                {parsingDocumentId === document.id
                                  ? "解析中..."
                                  : document.parse_status === "parsed"
                                    ? "重新解析"
                                    : "解析"}
                              </button>
                              <button
                                type="button"
                                onClick={() => void handlePreviewDocument(document)}
                                disabled={!document.parsed_markdown_path}
                                className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)] disabled:cursor-not-allowed disabled:opacity-45"
                              >
                                预览 Markdown
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleIndexDocument(document)}
                                disabled={indexingDocumentId === document.id || document.parse_status !== "parsed"}
                                className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)] disabled:cursor-not-allowed disabled:opacity-45"
                              >
                                {indexingDocumentId === document.id
                                  ? "索引中..."
                                  : document.index_status === "indexed"
                                    ? "重建索引"
                                    : "生成索引"}
                              </button>
                            </div>
                            {document.error_message ? (
                              <p className="mt-3 rounded-2xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-xs leading-5 text-[var(--danger-text)]">
                                {document.error_message}
                              </p>
                            ) : null}
                          </div>
                        ))
                      )}
                    </div>
                  </Panel>

                  <div className="space-y-4">
                    <Panel title="检索测试" subtitle="用于定位解析、分块、Embedding 和召回质量问题；聊天接入前先在这里验证。">
                      <form onSubmit={handleTestRetrieval} className="space-y-3">
                        <textarea
                          value={retrievalQuery}
                          onChange={(event) => setRetrievalQuery(event.target.value)}
                          className="min-h-24 w-full resize-none rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
                          placeholder="输入要检索的问题，例如：这篇论文提出了什么方法？"
                        />
                        <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] p-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-xs font-medium text-[var(--ink-soft)]">过滤条件</p>
                            <button
                              type="button"
                              onClick={() => {
                                setRetrievalDocumentIds([]);
                                setRetrievalFileTypes([]);
                                setRetrievalPageStart("");
                                setRetrievalPageEnd("");
                                setRetrievalSectionQuery("");
                              }}
                              className="text-xs text-[var(--ink-muted)] underline-offset-2 hover:underline"
                            >
                              清空
                            </button>
                          </div>
                          <div className="mt-3 space-y-3">
                            <div>
                              <p className="mb-2 text-[11px] uppercase tracking-[0.2em] text-[var(--ink-muted)]">
                                文档
                              </p>
                              <div className="max-h-28 space-y-2 overflow-y-auto pr-1">
                                {documents.length === 0 ? (
                                  <p className="text-xs text-[var(--ink-muted)]">暂无文档可选。</p>
                                ) : (
                                  documents.map((document) => (
                                    <label
                                      key={document.id}
                                      className="flex cursor-pointer items-center gap-2 rounded-xl border border-transparent px-2 py-1.5 text-xs text-[var(--ink-soft)] hover:border-[var(--control-border)]"
                                    >
                                      <input
                                        type="checkbox"
                                        checked={retrievalDocumentIds.includes(document.id)}
                                        onChange={() => toggleRetrievalDocumentId(document.id)}
                                      />
                                      <span className="truncate">
                                        {document.file_name} · {document.mime_type || "unknown"}
                                      </span>
                                    </label>
                                  ))
                                )}
                              </div>
                            </div>
                            <div>
                              <p className="mb-2 text-[11px] uppercase tracking-[0.2em] text-[var(--ink-muted)]">
                                文件类型
                              </p>
                              <div className="flex flex-wrap gap-2">
                                {["pdf", "markdown", "text", "html"].map((fileType) => (
                                  <button
                                    key={fileType}
                                    type="button"
                                    onClick={() => toggleRetrievalFileType(fileType)}
                                    className={`rounded-full border px-3 py-1.5 text-xs transition ${
                                      retrievalFileTypes.includes(fileType)
                                        ? "border-[var(--accent-strong)] bg-[var(--accent-soft)] text-[var(--ink-strong)]"
                                        : "border-[var(--control-border)] bg-[var(--control-bg)] text-[var(--ink-soft)]"
                                    }`}
                                  >
                                    {fileType}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <div className="grid gap-2 sm:grid-cols-3">
                              <input
                                value={retrievalPageStart}
                                onChange={(event) => setRetrievalPageStart(event.target.value)}
                                inputMode="numeric"
                                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
                                placeholder="起始页"
                              />
                              <input
                                value={retrievalPageEnd}
                                onChange={(event) => setRetrievalPageEnd(event.target.value)}
                                inputMode="numeric"
                                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
                                placeholder="结束页"
                              />
                              <input
                                value={retrievalSectionQuery}
                                onChange={(event) => setRetrievalSectionQuery(event.target.value)}
                                className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
                                placeholder="章节关键词"
                              />
                            </div>
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-xs text-[var(--ink-muted)]">
                            Top K：{visibleActiveKnowledgeBase.retrieval_top_k}
                          </span>
                          <button
                            type="submit"
                            disabled={isTestingRetrieval || !retrievalQuery.trim()}
                            className="primary-action rounded-full px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {isTestingRetrieval ? "检索中..." : "测试检索"}
                          </button>
                        </div>
                      </form>

                      {retrievalResult ? (
                        <div className="mt-4 space-y-2">
                          <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-xs leading-6 text-[var(--ink-muted)]">
                            <span>
                              共 {retrievalResult.total_chunks} 个已索引片段，返回 {retrievalResult.results.length} 条结果。
                            </span>
                            <span className="ml-2">
                              Rerank：
                              {retrievalResult.rerank_enabled
                                ? retrievalResult.rerank_model || "已启用"
                                : "未启用"}
                            </span>
                            <span className="ml-2">{formatRetrievalFilters(retrievalResult.filters)}</span>
                          </div>
                          {retrievalResult.results.length === 0 ? (
                            <EmptyBox text="没有召回结果。请确认至少一个文档已索引，或调整查询和阈值。" />
                          ) : (
                            <div className="max-h-[320px] space-y-2 overflow-y-auto pr-1">
                              {retrievalResult.results.map((result) => (
                                <div
                                  key={result.chunk_id}
                                  className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3"
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                      <p className="truncate text-sm font-semibold">{result.file_name}</p>
                                      <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                        Chunk #{result.chunk_index} · 最终 {result.score.toFixed(4)} · 向量{" "}
                                        {result.vector_score.toFixed(4)}
                                        {result.rerank_score !== null
                                          ? ` · Rerank ${result.rerank_score.toFixed(4)}`
                                          : ""}
                                      </p>
                                      <p className="mt-1 break-all text-[11px] text-[var(--ink-muted)]">
                                        chunk_id：{result.chunk_id}
                                      </p>
                                    </div>
                                    <Badge label={retrievalRankLabel(result.rank_source)} />
                                  </div>
                                  {typeof result.metadata?.rerank_error === "string" ? (
                                    <p className="mt-2 rounded-xl border border-[var(--warning-border)] bg-[var(--warning-bg)] px-3 py-2 text-xs leading-5 text-[var(--warning-text)]">
                                      Rerank 失败，已回退到向量召回：{result.metadata.rerank_error}
                                    </p>
                                  ) : null}
                                  <p className="mt-3 line-clamp-6 whitespace-pre-wrap text-xs leading-6 text-[var(--ink-soft)]">
                                    {result.content}
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ) : null}
                    </Panel>

                    <Panel
                      title="RAG-6.0 检索观测"
                      subtitle="先把检索日志和小型评测集打通，为后续 Hybrid、Parent-Child、Contextual Retrieval 提供基线。"
                    >
                      <div className="space-y-5">
                        <div>
                          <div className="mb-3 flex items-center justify-between gap-2">
                            <div>
                              <h4 className="text-sm font-semibold">最近检索日志</h4>
                              <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                聊天使用知识库后会记录候选片段、注入片段和诊断信息。
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                visibleActiveKnowledgeBase
                                  ? void refreshRetrievalLogs(visibleActiveKnowledgeBase.id)
                                  : undefined
                              }
                              disabled={isLoadingRetrievalLogs}
                              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] disabled:opacity-60"
                            >
                              {isLoadingRetrievalLogs ? "刷新中..." : "刷新日志"}
                            </button>
                          </div>
                          {retrievalLogs.length === 0 ? (
                            <EmptyBox text="暂无日志。先在聊天里启用知识库问答，或点击刷新加载已有日志。" />
                          ) : (
                            <div className="max-h-[220px] space-y-2 overflow-y-auto pr-1">
                              {retrievalLogs.map((log) => (
                                <div
                                  key={log.id}
                                  className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-xs"
                                >
                                  <div className="flex items-start justify-between gap-3">
                                    <p className="line-clamp-2 text-sm font-semibold">{log.query}</p>
                                    <Badge label={log.status === "success" ? "成功" : log.status} />
                                  </div>
                                  <p className="mt-2 text-[var(--ink-muted)]">
                                    {formatDateTime(log.created_at)} · 候选 {log.candidates.length} · 注入{" "}
                                    {log.selected.length} · {log.elapsed_ms ?? 0}ms
                                  </p>
                                  {log.selected.length > 0 ? (
                                    <div className="mt-2 rounded-xl bg-[var(--soft-bg)] px-3 py-2 text-[var(--ink-soft)]">
                                      <p className="line-clamp-3">
                                        {String(log.selected[0]?.preview || log.selected[0]?.content || "无预览")}
                                      </p>
                                    </div>
                                  ) : null}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>

                        <div className="border-t border-[var(--hairline)] pt-4">
                          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                            <div>
                              <h4 className="text-sm font-semibold">小型评测集</h4>
                              <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                手动维护关键问题与期望 chunk，用 Hit@K / MRR 观察检索质量。
                              </p>
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                visibleActiveKnowledgeBase
                                  ? void refreshEvalData(visibleActiveKnowledgeBase.id)
                                  : undefined
                              }
                              disabled={isLoadingEval}
                              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] disabled:opacity-60"
                            >
                              {isLoadingEval ? "加载中..." : "刷新评测"}
                            </button>
                          </div>

                          <form onSubmit={handleCreateEvalSet} className="flex gap-2">
                            <input
                              value={evalSetName}
                              onChange={(event) => setEvalSetName(event.target.value)}
                              className="min-w-0 flex-1 rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
                              placeholder="新建评测集，例如：论文核心问题"
                            />
                            <button
                              type="submit"
                              disabled={isCreatingEvalSet || !evalSetName.trim()}
                              className="primary-action rounded-full px-4 py-2 text-sm font-medium disabled:opacity-60"
                            >
                              {isCreatingEvalSet ? "创建中..." : "创建"}
                            </button>
                          </form>

                          {evalSets.length > 0 ? (
                            <div className="mt-3 space-y-3">
                              <select
                                value={selectedEvalSetId}
                                onChange={(event) =>
                                  visibleActiveKnowledgeBase
                                    ? void handleSelectEvalSet(visibleActiveKnowledgeBase.id, event.target.value)
                                    : undefined
                                }
                                className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
                              >
                                {evalSets.map((item) => (
                                  <option key={item.id} value={item.id}>
                                    {item.name}
                                  </option>
                                ))}
                              </select>

                              <form onSubmit={handleAddEvalCase} className="space-y-2">
                                <textarea
                                  value={evalCaseQuery}
                                  onChange={(event) => setEvalCaseQuery(event.target.value)}
                                  className="min-h-20 w-full resize-none rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-sm outline-none focus:border-[var(--accent-strong)]"
                                  placeholder="评测问题，例如：这篇论文提出了什么 routing 方法？"
                                />
                                <input
                                  value={evalExpectedChunkId}
                                  onChange={(event) => setEvalExpectedChunkId(event.target.value)}
                                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-xs outline-none focus:border-[var(--accent-strong)]"
                                  placeholder="期望命中的 chunk_id，可先从检索测试结果复制"
                                />
                                <div className="flex items-center justify-between gap-2">
                                  <span className="text-xs text-[var(--ink-muted)]">
                                    当前 {evalCases.length} 条用例，{evalRuns.length} 次运行。
                                  </span>
                                  <div className="flex gap-2">
                                    <button
                                      type="submit"
                                      disabled={isAddingEvalCase || !evalCaseQuery.trim() || !selectedEvalSetId}
                                      className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)] disabled:opacity-60"
                                    >
                                      {isAddingEvalCase ? "添加中..." : "添加用例"}
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => void handleRunEvalSet()}
                                      disabled={isRunningEval || evalCases.length === 0}
                                      className="primary-action rounded-full px-3 py-1.5 text-xs font-medium disabled:opacity-60"
                                    >
                                      {isRunningEval ? "运行中..." : "运行评测"}
                                    </button>
                                  </div>
                                </div>
                              </form>

                              {evalOutcome ? (
                                <div className="grid gap-2 sm:grid-cols-4">
                                  <Metric label="Hit@K" value={formatMetric(evalOutcome.run.metrics.hit_at_k)} />
                                  <Metric label="MRR" value={formatMetric(evalOutcome.run.metrics.mrr)} />
                                  <Metric
                                    label="Precision"
                                    value={formatMetric(evalOutcome.run.metrics.context_precision)}
                                  />
                                  <Metric
                                    label="Recall"
                                    value={formatMetric(evalOutcome.run.metrics.context_recall)}
                                  />
                                </div>
                              ) : null}

                              {evalCases.length > 0 ? (
                                <div className="max-h-[180px] space-y-2 overflow-y-auto pr-1">
                                  {evalCases.map((item) => (
                                    <div
                                      key={item.id}
                                      className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-2 text-xs"
                                    >
                                      <p className="line-clamp-2 text-sm font-medium">{item.query}</p>
                                      <p className="mt-1 break-all text-[var(--ink-muted)]">
                                        expected chunk：{item.expected_chunk_id || "未设置"}
                                      </p>
                                    </div>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          ) : (
                            <p className="mt-3 text-xs leading-6 text-[var(--ink-muted)]">
                              还没有评测集。创建一个评测集后，可以添加关键问题并运行检索质量基线。
                            </p>
                          )}
                        </div>
                      </div>
                    </Panel>

                    <Panel title="任务" subtitle="解析和索引当前同步触发；后续会迁移为后台 worker。">
                      <div className="max-h-[260px] space-y-2 overflow-y-auto pr-1">
                        {jobs.length === 0 ? (
                          <EmptyBox text="暂无任务。上传文档后会生成 parse_document 任务。" />
                        ) : (
                          jobs.map((job) => (
                            <div
                              key={job.id}
                              className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3"
                            >
                              <div className="flex items-center justify-between gap-3">
                                <p className="text-sm font-semibold">{job.job_type}</p>
                                <Badge label={statusLabel(job.status)} />
                              </div>
                              <p className="mt-2 text-xs text-[var(--ink-muted)]">
                                {formatDateTime(job.created_at)}
                              </p>
                              {job.error_message ? (
                                <p className="mt-2 text-xs text-[var(--danger-text)]">{job.error_message}</p>
                              ) : null}
                            </div>
                          ))
                        )}
                      </div>
                    </Panel>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex h-full min-h-[520px] items-center justify-center rounded-[24px] border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] p-6 text-center">
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-muted)]">RAG-3</p>
                  <h2 className="mt-3 text-3xl font-semibold">先创建一个知识库</h2>
                  <p className="mx-auto mt-3 max-w-xl text-sm leading-7 text-[var(--ink-soft)]">
                    知识库创建时会保存解析、分块、Embedding、Rerank 和检索配置。当前已经可以上传文档、解析、索引并测试检索。
                  </p>
                  <button
                    type="button"
                    onClick={() => setIsCreateOpen(true)}
                    className="primary-action mt-6 rounded-full px-5 py-2 text-sm font-medium transition hover:brightness-105"
                  >
                    新建知识库
                  </button>
                </div>
              </div>
            )}
          </section>
        </section>
      </div>

      {previewDocument ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
          <section className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-[30px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
            <div className="flex items-center justify-between gap-3 border-b border-[var(--hairline)] px-5 py-4">
              <div className="min-w-0">
                <p className="text-xs uppercase tracking-[0.24em] text-[var(--ink-muted)]">Markdown Preview</p>
                <h3 className="mt-1 truncate text-2xl font-semibold">{previewDocument.file_name}</h3>
              </div>
              <button
                type="button"
                onClick={() => setPreviewDocument(null)}
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)]"
              >
                关闭
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <MessageMarkdown content={previewDocument.markdown} />
            </div>
          </section>
        </div>
      ) : null}

      {isCreateOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
          <form
            onSubmit={handleCreateKnowledgeBase}
            className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-[30px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]"
          >
            <div className="border-b border-[var(--hairline)] px-6 py-5">
              <p className="text-xs uppercase tracking-[0.28em] text-[var(--ink-muted)]">Create Knowledge Base</p>
              <h2 className="mt-2 text-3xl font-semibold">新建知识库</h2>
              <p className="mt-2 text-sm leading-7 text-[var(--ink-soft)]">
                创建时保存解析、分块、Embedding、Rerank 和检索配置。创建后进入详情页上传文档，并可先使用本地基础解析生成 Markdown 预览。
              </p>
            </div>

            <div className="space-y-5 px-6 py-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block sm:col-span-2">
                  <span className="mb-2 block text-sm text-[var(--ink-soft)]">名称</span>
                  <input
                    value={createForm.name}
                    onChange={(event) => updateCreateForm("name", event.target.value)}
                    className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
                    placeholder="例如：课程论文知识库"
                    required
                  />
                </label>

                <label className="block sm:col-span-2">
                  <span className="mb-2 block text-sm text-[var(--ink-soft)]">描述</span>
                  <textarea
                    value={createForm.description}
                    onChange={(event) => updateCreateForm("description", event.target.value)}
                    className="min-h-20 w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
                    placeholder="这个知识库主要保存什么资料？"
                  />
                </label>

                <SelectField
                  label="所属工作区"
                  value={createForm.project_id}
                  onChange={(value) => updateCreateForm("project_id", value)}
                  options={[
                    { value: "", label: "不绑定工作区" },
                    ...initialProjects.map((project) => ({ value: project.id, label: project.name })),
                  ]}
                />

                <SelectField
                  label="解析器"
                  value={createForm.parser_provider}
                  onChange={(value) => updateCreateForm("parser_provider", value as "local_basic" | "mineru")}
                  options={[
                    { value: "local_basic", label: "本地基础解析" },
                    { value: "mineru", label: "MinerU（需先配置 token）" },
                  ]}
                />

                <NumberField
                  label="Chunk Size"
                  value={createForm.chunk_size}
                  min={100}
                  max={8000}
                  onChange={(value) => updateCreateForm("chunk_size", value)}
                />
                <NumberField
                  label="Chunk Overlap"
                  value={createForm.chunk_overlap}
                  min={0}
                  max={2000}
                  onChange={(value) => updateCreateForm("chunk_overlap", value)}
                />

                <SelectField
                  label="Embedding Provider"
                  value={createForm.embedding_provider}
                  onChange={(value) => updateCreateForm("embedding_provider", value)}
                  options={PROVIDER_OPTIONS.map((provider) => ({ value: provider, label: provider }))}
                />

                <SelectField
                  label="Embedding 模型"
                  value={createForm.embedding_model}
                  onChange={(value) => {
                    setCreateForm((current) => ({
                      ...current,
                      embedding_model: value,
                      embedding_dimensions: inferEmbeddingDimensions(value, current.embedding_dimensions),
                    }));
                  }}
                  options={EMBEDDING_MODELS.map((model) => ({ value: model, label: model }))}
                />

                <ReadOnlyField
                  label="Embedding 维度"
                  value={`${createForm.embedding_dimensions}`}
                  hint="由当前 Embedding 模型自动确定，保存为索引元数据。创建索引后如更换模型，需要重建索引。"
                />

                <label className="flex items-center gap-3 rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm text-[var(--ink-soft)]">
                  <input
                    type="checkbox"
                    checked={createForm.rerank_enabled}
                    onChange={(event) => updateCreateForm("rerank_enabled", event.target.checked)}
                  />
                  启用 Rerank
                </label>

                <SelectField
                  label="Rerank Provider"
                  value={createForm.rerank_provider}
                  onChange={(value) => updateCreateForm("rerank_provider", value)}
                  options={PROVIDER_OPTIONS.map((provider) => ({ value: provider, label: provider }))}
                />

                <SelectField
                  label="Rerank 模型"
                  value={createForm.rerank_model}
                  onChange={(value) => updateCreateForm("rerank_model", value)}
                  options={RERANK_MODELS.map((model) => ({ value: model, label: model }))}
                />

                <NumberField
                  label="Vector Top K"
                  value={createForm.retrieval_top_k}
                  min={1}
                  max={100}
                  onChange={(value) => updateCreateForm("retrieval_top_k", value)}
                />
                <NumberField
                  label="Rerank Top N"
                  value={createForm.rerank_top_n}
                  min={1}
                  max={50}
                  onChange={(value) => updateCreateForm("rerank_top_n", value)}
                />
                <NumberField
                  label="Score Threshold"
                  value={createForm.score_threshold}
                  min={0}
                  max={1}
                  step={0.05}
                  onChange={(value) => updateCreateForm("score_threshold", value)}
                />
                <NumberField
                  label="最大注入片段"
                  value={createForm.max_context_chunks}
                  min={1}
                  max={50}
                  onChange={(value) => updateCreateForm("max_context_chunks", value)}
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-[var(--hairline)] px-6 py-5">
              <button
                type="button"
                onClick={() => setIsCreateOpen(false)}
                disabled={isSubmitting}
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] disabled:opacity-60"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={isSubmitting || !createForm.name.trim()}
                className="primary-action rounded-full px-5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSubmitting ? "创建中..." : "创建知识库"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--soft-bg)] px-3 py-2">
      <p className="text-[11px] text-[var(--ink-muted)]">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold">{value}</p>
    </div>
  );
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <section className="rounded-[24px] border border-[var(--control-border)] bg-[var(--soft-bg)] p-4">
      <div className="mb-4">
        <h3 className="text-lg font-semibold">{title}</h3>
        <p className="mt-1 text-xs leading-5 text-[var(--ink-muted)]">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3">
      <p className="text-[11px] text-[var(--ink-muted)]">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold">{value}</p>
    </div>
  );
}

function StatusLine({ done = false, label }: { done?: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`inline-flex h-5 w-5 items-center justify-center rounded-full border text-[10px] ${
          done
            ? "border-[var(--success-border)] bg-[var(--success-bg)] text-[var(--success-text)]"
            : "border-[var(--control-border)] bg-[var(--control-bg)] text-[var(--ink-muted)]"
        }`}
      >
        {done ? "✓" : "·"}
      </span>
      <span>{label}</span>
    </div>
  );
}

function Badge({ label }: { label: string }) {
  return (
    <span className="rounded-full border border-[var(--control-border)] bg-[var(--soft-bg)] px-2.5 py-1 text-[var(--ink-soft)]">
      {label}
    </span>
  );
}

function EmptyBox({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-8 text-center text-sm leading-7 text-[var(--ink-soft)]">
      {text}
    </div>
  );
}

function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm text-[var(--ink-soft)]">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function ReadOnlyField({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="block">
      <span className="mb-2 block text-sm text-[var(--ink-soft)]">{label}</span>
      <div className="rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm">
        <p className="font-semibold text-[var(--ink-strong)]">{value}</p>
        {hint ? <p className="mt-1 text-xs leading-5 text-[var(--ink-muted)]">{hint}</p> : null}
      </div>
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm text-[var(--ink-soft)]">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-sm outline-none focus:border-[var(--accent-strong)]"
      />
    </label>
  );
}

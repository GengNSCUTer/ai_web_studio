"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { KnowledgeBase, KnowledgeDocument, KnowledgeJob, Project, UploadItem, User } from "@/lib/types";

type KnowledgeWorkspaceProps = {
  currentUser: User;
  initialKnowledgeBases: KnowledgeBase[];
  initialProjects: Project[];
  activeKnowledgeBase?: KnowledgeBase | null;
  initialDocuments?: KnowledgeDocument[];
  initialJobs?: KnowledgeJob[];
};

type CreateFormState = {
  name: string;
  description: string;
  project_id: string;
  parser_provider: "local_basic" | "mineru";
  chunk_size: number;
  chunk_overlap: number;
  embedding_model: string;
  rerank_enabled: boolean;
  rerank_model: string;
  retrieval_top_k: number;
  rerank_top_n: number;
  score_threshold: number;
  max_context_chunks: number;
  max_context_chars: number;
};

const DEFAULT_CREATE_FORM: CreateFormState = {
  name: "",
  description: "",
  project_id: "",
  parser_provider: "local_basic",
  chunk_size: 1000,
  chunk_overlap: 150,
  embedding_model: "BAAI/bge-m3",
  rerank_enabled: true,
  rerank_model: "BAAI/bge-reranker-v2-m3",
  retrieval_top_k: 20,
  rerank_top_n: 6,
  score_threshold: 0.2,
  max_context_chunks: 6,
  max_context_chars: 12000,
};

const EMBEDDING_MODELS = ["BAAI/bge-m3", "Qwen/Qwen3-Embedding-0.6B"];
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
    parsing: "解析中",
    parsed: "已解析",
    indexing: "索引中",
    indexed: "已索引",
    failed: "失败",
    deleted: "已删除",
  };
  return labels[value] ?? value;
}

export function KnowledgeWorkspace({
  currentUser,
  initialKnowledgeBases,
  initialProjects,
  activeKnowledgeBase = null,
  initialDocuments = [],
  initialJobs = [],
}: KnowledgeWorkspaceProps) {
  const [knowledgeBases, setKnowledgeBases] = useState(initialKnowledgeBases);
  const [documents, setDocuments] = useState(initialDocuments);
  const [jobs, setJobs] = useState(initialJobs);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(DEFAULT_CREATE_FORM);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
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
        embedding_provider: "siliconflow",
        embedding_model: createForm.embedding_model,
        embedding_dimensions: createForm.embedding_model === "BAAI/bge-m3" ? 1024 : 1024,
        rerank_enabled: createForm.rerank_enabled,
        rerank_provider: "siliconflow",
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
      setCreateForm(DEFAULT_CREATE_FORM);
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

  return (
    <main className="min-h-screen bg-[var(--app-bg)] px-4 py-5 text-[var(--ink-strong)] sm:px-6">
      <div className="mx-auto flex min-h-[calc(100vh-2.5rem)] max-w-7xl flex-col gap-4">
        <header className="rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-5 py-4 shadow-[var(--panel-shadow)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-xs uppercase tracking-[0.35em] text-[var(--ink-muted)]">Knowledge Base</p>
              <h1 className="mt-2 text-3xl font-semibold">个人知识库</h1>
              <p className="mt-2 max-w-2xl text-sm leading-7 text-[var(--ink-soft)]">
                当前阶段先搭建知识库骨架：创建配置、上传文档记录、任务状态。后续会接入 MinerU 解析、Embedding、FAISS 和聊天引用。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href="/"
                className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)] hover:text-[var(--ink-strong)]"
              >
                返回聊天
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

                <div className="grid gap-4 py-4 xl:grid-cols-[1.2fr_0.8fr]">
                  <Panel title="配置概览" subtitle="RAG-1 只展示配置，后续会增加可编辑和重建索引提示。">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Info label="解析器" value={visibleActiveKnowledgeBase.parser_provider} />
                      <Info label="分块模式" value={visibleActiveKnowledgeBase.chunk_mode} />
                      <Info label="Embedding" value={visibleActiveKnowledgeBase.embedding_model} />
                      <Info label="Rerank" value={visibleActiveKnowledgeBase.rerank_enabled ? visibleActiveKnowledgeBase.rerank_model : "未启用"} />
                      <Info label="检索模式" value={visibleActiveKnowledgeBase.retrieval_mode} />
                      <Info label="分数阈值" value={`${visibleActiveKnowledgeBase.score_threshold}`} />
                    </div>
                  </Panel>

                  <Panel title="阶段状态" subtitle="当前只完成知识库骨架，解析索引会在后续阶段补齐。">
                    <div className="space-y-2 text-sm text-[var(--ink-soft)]">
                      <StatusLine done label="知识库配置已持久化" />
                      <StatusLine done label="文档上传记录已接入" />
                      <StatusLine label="MinerU 解析待接入" />
                      <StatusLine label="Embedding / FAISS 待接入" />
                      <StatusLine label="聊天引用待接入" />
                    </div>
                  </Panel>
                </div>

                <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[1.2fr_0.8fr]">
                  <Panel title="文档" subtitle="上传后会创建文档记录和 pending 解析任务。">
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
                          </div>
                        ))
                      )}
                    </div>
                  </Panel>

                  <Panel title="任务" subtitle="RAG-1 只创建 pending job，后续 worker 会消费这些任务。">
                    <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
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
            ) : (
              <div className="flex h-full min-h-[520px] items-center justify-center rounded-[24px] border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] p-6 text-center">
                <div>
                  <p className="text-xs uppercase tracking-[0.3em] text-[var(--ink-muted)]">RAG-1</p>
                  <h2 className="mt-3 text-3xl font-semibold">先创建一个知识库</h2>
                  <p className="mx-auto mt-3 max-w-xl text-sm leading-7 text-[var(--ink-soft)]">
                    知识库创建时会保存解析、分块、embedding、rerank 和检索配置。下一阶段再把文档解析和向量索引接上。
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
                当前先保存知识库配置。创建后进入详情页上传文档，后续阶段会补解析、索引和检索测试。
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
                    { value: "mineru", label: "MinerU（后续接凭据）" },
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
                  label="Embedding 模型"
                  value={createForm.embedding_model}
                  onChange={(value) => updateCreateForm("embedding_model", value)}
                  options={EMBEDDING_MODELS.map((model) => ({ value: model, label: model }))}
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

"use client";

import { useRef, useState } from "react";

import type { Conversation, FileRevision, Project, ProjectFile, ProjectStats } from "@/lib/types";

type WorkspaceModalMode = "create" | "edit" | "move" | null;

type WorkspaceText = {
  workspace: string;
  moveWorkspace: string;
  workspaceSettings: string;
  newWorkspace: string;
  close: string;
  currentConversation: string;
  workspaceTarget: string;
  workspaceName: string;
  workspaceDefaultModel: string;
  workspaceNoDefaultModel: string;
  workspaceSystemPrompt: string;
  workspaceStats: string;
  workspaceFiles: string;
  saving: string;
  workspaceAddFiles: string;
  workspaceConversationCount: string;
  workspaceMessageCount: string;
  workspaceFileCount: string;
  workspaceTemplateCount: string;
  workspaceTotalFileSize: string;
  delete: string;
  workspaceFileEmpty: string;
  workspaceFileVersions: string;
  workspaceNoRevisions: string;
  deleteWorkspace: string;
  cancel: string;
  saveWorkspace: string;
};

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, { cache: "no-store", ...init });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

type WorkspaceDraft = {
  id: string;
  name: string;
  default_model: string;
  system_prompt: string;
  target_project_id: string;
};

type WorkspaceModalProps = {
  mode: WorkspaceModalMode;
  text: WorkspaceText;
  projects: Project[];
  workspaceDraft: WorkspaceDraft;
  workspaceMoveConversation: Conversation | null;
  activeProject: Project | null;
  workspaceModelOptions: string[];
  projectFiles: ProjectFile[];
  projectStats: ProjectStats | null;
  isAddingProjectFile: boolean;
  onClose: () => void;
  onDraftChange: (updater: (current: WorkspaceDraft) => WorkspaceDraft) => void;
  onDeleteProject: () => void;
  onSubmit: () => void | Promise<void>;
  onAddProjectFiles: (files: FileList | null) => void | Promise<void>;
  onDeleteProjectFile: (fileId: string) => void | Promise<void>;
};

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

export function WorkspaceModal({
  mode,
  text,
  projects,
  workspaceDraft,
  workspaceMoveConversation,
  activeProject,
  workspaceModelOptions,
  projectFiles,
  projectStats,
  isAddingProjectFile,
  onClose,
  onDraftChange,
  onDeleteProject,
  onSubmit,
  onAddProjectFiles,
  onDeleteProjectFile,
}: WorkspaceModalProps) {
  const [revisionFileId, setRevisionFileId] = useState<string | null>(null);
  const [revisions, setRevisions] = useState<FileRevision[]>([]);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  const [isLoadingRevisions, setIsLoadingRevisions] = useState(false);
  const revisionRequestId = useRef(0);

  async function showRevisionHistory(fileId: string) {
    if (revisionFileId === fileId) {
      revisionRequestId.current += 1;
      setRevisionFileId(null);
      setRevisions([]);
      return;
    }
    setRevisionFileId(fileId);
    setIsLoadingRevisions(true);
    setRevisionError(null);
    const requestId = revisionRequestId.current + 1;
    revisionRequestId.current = requestId;
    try {
      const loaded = await requestJson<FileRevision[]>(
        `/api/backend/agent-runtime/files/${fileId}/revisions`
      );
      if (revisionRequestId.current === requestId) {
        setRevisions(loaded);
      }
    } catch (error) {
      if (revisionRequestId.current === requestId) {
        setRevisions([]);
        setRevisionError(error instanceof Error ? error.message : "Failed to load revisions");
      }
    } finally {
      if (revisionRequestId.current === requestId) {
        setIsLoadingRevisions(false);
      }
    }
  }

  if (!mode) {
    return null;
  }

  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
      <div className="flex max-h-[calc(100vh-4rem)] w-full max-w-2xl flex-col overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--hairline)] px-5 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              {text.workspace}
            </p>
            <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">
              {mode === "move"
                ? text.moveWorkspace
                : mode === "edit"
                  ? text.workspaceSettings
                  : text.newWorkspace}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
          >
            {text.close}
          </button>
        </div>

        <div className="min-h-0 overflow-y-auto px-5 py-5">
          {mode === "move" ? (
            <div className="space-y-4">
              <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3">
                <p className="text-sm font-medium text-[var(--ink-strong)]">
                  {workspaceMoveConversation?.title ?? text.currentConversation}
                </p>
                <p className="mt-1 text-xs text-[var(--ink-muted)]">
                  {workspaceMoveConversation?.model_name ?? "--"}
                </p>
              </div>
              <label className="block text-sm">
                <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceTarget}</span>
                <select
                  value={workspaceDraft.target_project_id}
                  onChange={(event) =>
                    onDraftChange((current) => ({
                      ...current,
                      target_project_id: event.target.value,
                    }))
                  }
                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                >
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : (
            <div className="space-y-4">
              <label className="block text-sm">
                <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceName}</span>
                <input
                  value={workspaceDraft.name}
                  onChange={(event) =>
                    onDraftChange((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                />
              </label>

              <label className="block text-sm">
                <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceDefaultModel}</span>
                <select
                  value={workspaceDraft.default_model}
                  onChange={(event) =>
                    onDraftChange((current) => ({
                      ...current,
                      default_model: event.target.value,
                    }))
                  }
                  className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                >
                  <option value="">{text.workspaceNoDefaultModel}</option>
                  {workspaceModelOptions.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block text-sm">
                <span className="mb-2 block text-[var(--ink-soft)]">{text.workspaceSystemPrompt}</span>
                <textarea
                  value={workspaceDraft.system_prompt}
                  onChange={(event) =>
                    onDraftChange((current) => ({
                      ...current,
                      system_prompt: event.target.value,
                    }))
                  }
                  className="min-h-[180px] w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
                />
              </label>

              {mode === "edit" && activeProject ? (
                <div className="space-y-4 rounded-[24px] border border-[var(--hairline)] bg-[var(--soft-bg)] p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-[var(--ink-strong)]">
                        {text.workspaceStats}
                      </p>
                      <p className="mt-1 text-xs text-[var(--ink-muted)]">{text.workspaceFiles}</p>
                    </div>
                    <label className="cursor-pointer rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-xs text-[var(--ink-soft)] transition hover:border-[var(--accent-strong)]">
                      {isAddingProjectFile ? text.saving : text.workspaceAddFiles}
                      <input
                        type="file"
                        multiple
                        accept="image/*,.txt,.md,.markdown,.pdf,.docx"
                        className="hidden"
                        disabled={isAddingProjectFile}
                        onChange={(event) => {
                          void onAddProjectFiles(event.target.files);
                          event.target.value = "";
                        }}
                      />
                    </label>
                  </div>
                  {projectStats ? (
                    <div className="grid gap-2 sm:grid-cols-5">
                      {[
                        [text.workspaceConversationCount, projectStats.conversation_count],
                        [text.workspaceMessageCount, projectStats.message_count],
                        [text.workspaceFileCount, projectStats.file_count],
                        [text.workspaceTemplateCount, projectStats.prompt_template_count],
                        [text.workspaceTotalFileSize, formatBytes(projectStats.total_file_size)],
                      ].map(([label, value]) => (
                        <div
                          key={label}
                          className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2"
                        >
                          <p className="text-[10px] text-[var(--ink-muted)]">{label}</p>
                          <p className="mt-1 break-all text-sm font-semibold text-[var(--ink-strong)]">
                            {value}
                          </p>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="space-y-2">
                    {projectFiles.length > 0 ? (
                      projectFiles.map((file) => (
                        <div key={file.id} className="rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-3 py-2">
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium text-[var(--ink-strong)]">
                                {file.file_name}
                              </p>
                              <p className="mt-1 text-xs text-[var(--ink-muted)]">
                                {file.kind} · {formatBytes(file.file_size ?? 0)}
                              </p>
                            </div>
                            <div className="flex shrink-0 gap-2">
                              <button
                                type="button"
                                onClick={() => void showRevisionHistory(file.id)}
                                className="rounded-full border border-[var(--control-border)] px-3 py-1 text-xs text-[var(--ink-soft)]"
                              >
                                {revisionFileId === file.id
                                  ? (text.cancel)
                                  : text.workspaceFileVersions}
                              </button>
                              <button
                                type="button"
                                onClick={() => void onDeleteProjectFile(file.id)}
                                className="rounded-full border border-[rgba(174,65,45,0.22)] px-3 py-1 text-xs text-[#9f3a2b]"
                              >
                                {text.delete}
                              </button>
                            </div>
                          </div>
                          {revisionFileId === file.id ? (
                            <div className="mt-3 border-t border-[var(--hairline)] pt-3">
                              {isLoadingRevisions ? (
                                <p className="text-xs text-[var(--ink-soft)]">{text.saving}</p>
                              ) : revisionError ? (
                                <p className="break-all text-xs text-[var(--danger-text)]">{revisionError}</p>
                              ) : revisions.length === 0 ? (
                                <p className="text-xs text-[var(--ink-soft)]">
                                  {text.workspaceNoRevisions}
                                </p>
                              ) : (
                                <ol className="space-y-2">
                                  {revisions.map((revision) => (
                                    <li key={revision.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                                      <span className="font-medium text-[var(--ink-strong)]">
                                        v{revision.revision_number} · {revision.created_by}
                                      </span>
                                      <span className="text-[var(--ink-muted)]">
                                        {new Date(revision.created_at).toLocaleString()}
                                      </span>
                                    </li>
                                  ))}
                                </ol>
                              )}
                            </div>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <p className="rounded-2xl border border-dashed border-[var(--control-border)] px-3 py-3 text-xs text-[var(--ink-soft)]">
                        {text.workspaceFileEmpty}
                      </p>
                    )}
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-[var(--hairline)] px-5 py-4">
          <div>
            {mode === "edit" ? (
              <button
                type="button"
                onClick={onDeleteProject}
                className="rounded-full border border-[rgba(174,65,45,0.22)] px-4 py-2 text-sm text-[#9f3a2b]"
              >
                {text.deleteWorkspace}
              </button>
            ) : null}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)]"
            >
              {text.cancel}
            </button>
            <button
              type="button"
              onClick={() => void onSubmit()}
              disabled={mode === "move" ? !workspaceDraft.target_project_id : !workspaceDraft.name.trim()}
              className="primary-action rounded-full px-5 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-55"
            >
              {mode === "move" ? text.moveWorkspace : text.saveWorkspace}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import type { Conversation, Project, ProviderInfo, User } from "@/lib/types";

type SidebarText = {
  appTag: string;
  appTitle: string;
  newChat: string;
  unnamedUser: string;
  settings: string;
  logout: string;
  currentProvider: string;
  providerLoading: string;
  providerBaseUrlLoading: string;
  historyChats: string;
  historyCountSuffix: string;
  workspace: string;
  allWorkspaces: string;
  unassignedWorkspace: string;
  workspaceManage: string;
  newWorkspace: string;
  conversationSearchPlaceholder: string;
  noConversations: string;
  noConversationMatches: string;
  activeChats: string;
  archivedChats: string;
  showArchived: string;
  hideArchived: string;
  menuLabel: string;
  rename: string;
  pin: string;
  unpin: string;
  archive: string;
  unarchive: string;
  moveToWorkspace: string;
  removeFromWorkspace: string;
  shareConversation: string;
  exportOptions: string;
  delete: string;
};

type ChatSidebarProps = {
  text: SidebarText;
  currentUser: User | null;
  providerInfo: ProviderInfo | null;
  conversations: Conversation[];
  filteredConversations: Conversation[];
  activeConversations: Conversation[];
  archivedConversations: Conversation[];
  projects: Project[];
  activeProject: Project | null;
  selectedProjectScope: string;
  selectedConversationId: string | null;
  conversationQuery: string;
  showArchived: boolean;
  shouldShowArchivedSection: boolean;
  openConversationMenuId: string | null;
  uiLanguage: "zh-CN" | "en-US";
  conversationMenuRef: React.RefObject<HTMLDivElement | null>;
  onNewConversation: () => void;
  onOpenSettings: () => void;
  onLogout: () => void | Promise<void>;
  onProjectScopeChange: (value: string) => void;
  onConfigureProject: () => void | Promise<void>;
  onCreateProject: () => void;
  onConversationQueryChange: (value: string) => void;
  onToggleArchived: () => void;
  onSelectConversation: (conversationId: string) => void | Promise<void>;
  onToggleConversationMenu: (conversationId: string) => void;
  onRenameConversation: (conversationId: string) => void;
  onTogglePinned: (conversation: Conversation) => void | Promise<void>;
  onToggleConversationArchived: (conversation: Conversation) => void | Promise<void>;
  onMoveConversation: (conversation: Conversation) => void;
  onRemoveConversationFromWorkspace: (
    conversation: Conversation,
    projectId: string | null
  ) => void | Promise<void>;
  onShareConversation: (conversation: Conversation) => void | Promise<void>;
  onExportConversation: (conversation: Conversation) => void;
  onDeleteConversation: (conversationId: string) => void;
};

function formatTime(value: string | null, uiLanguage: "zh-CN" | "en-US") {
  if (!value) {
    return "--";
  }

  return new Intl.DateTimeFormat(uiLanguage, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function ChatSidebar({
  text,
  currentUser,
  providerInfo,
  conversations,
  filteredConversations,
  activeConversations,
  archivedConversations,
  projects,
  activeProject,
  selectedProjectScope,
  selectedConversationId,
  conversationQuery,
  showArchived,
  shouldShowArchivedSection,
  openConversationMenuId,
  uiLanguage,
  conversationMenuRef,
  onNewConversation,
  onOpenSettings,
  onLogout,
  onProjectScopeChange,
  onConfigureProject,
  onCreateProject,
  onConversationQueryChange,
  onToggleArchived,
  onSelectConversation,
  onToggleConversationMenu,
  onRenameConversation,
  onTogglePinned,
  onToggleConversationArchived,
  onMoveConversation,
  onRemoveConversationFromWorkspace,
  onShareConversation,
  onExportConversation,
  onDeleteConversation,
}: ChatSidebarProps) {
  function renderConversationItem(conversation: Conversation) {
    const isActive = conversation.id === selectedConversationId;
    const isMenuOpen = openConversationMenuId === conversation.id;

    return (
      <div key={conversation.id} className="relative">
        <button
          type="button"
          onClick={() => void onSelectConversation(conversation.id)}
          className={`conversation-card w-full rounded-2xl border px-4 py-3 pr-12 text-left transition ${
            isActive ? "is-active" : ""
          }`}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="line-clamp-2 text-sm font-medium">{conversation.title}</div>
            {conversation.is_pinned ? (
              <span className="conversation-pin shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.18em]">
                PIN
              </span>
            ) : null}
          </div>
          <div className="conversation-meta mt-2 flex items-center justify-between text-[11px]">
            <span>{conversation.model_name}</span>
            <span>{formatTime(conversation.updated_at, uiLanguage)}</span>
          </div>
        </button>

        <div className="absolute right-2 top-2">
          <button
            type="button"
            onClick={() => onToggleConversationMenu(conversation.id)}
            className="sidebar-icon-button inline-flex h-8 w-8 items-center justify-center rounded-full border backdrop-blur transition"
            aria-label={text.menuLabel}
            title={text.menuLabel}
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4 fill-none stroke-current stroke-2">
              <circle cx="12" cy="5" r="1.5" />
              <circle cx="12" cy="12" r="1.5" />
              <circle cx="12" cy="19" r="1.5" />
            </svg>
          </button>

          {isMenuOpen ? (
            <div className="sidebar-menu absolute right-0 z-20 mt-2 w-40 overflow-hidden rounded-2xl border py-1">
              <button
                type="button"
                onClick={() => onRenameConversation(conversation.id)}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.rename}
              </button>
              <button
                type="button"
                onClick={() => void onTogglePinned(conversation)}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {conversation.is_pinned ? text.unpin : text.pin}
              </button>
              <button
                type="button"
                onClick={() => void onToggleConversationArchived(conversation)}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {conversation.is_archived ? text.unarchive : text.archive}
              </button>
              {projects.length > 0 ? (
                <button
                  type="button"
                  onClick={() => onMoveConversation(conversation)}
                  className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
                >
                  {text.moveToWorkspace}
                </button>
              ) : null}
              {conversation.project_id ? (
                <button
                  type="button"
                  onClick={() => void onRemoveConversationFromWorkspace(conversation, null)}
                  className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
                >
                  {text.removeFromWorkspace}
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void onShareConversation(conversation)}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.shareConversation}
              </button>
              <button
                type="button"
                onClick={() => onExportConversation(conversation)}
                className="sidebar-menu-item block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.exportOptions}
              </button>
              <button
                type="button"
                onClick={() => onDeleteConversation(conversation.id)}
                className="sidebar-menu-item is-danger block w-full px-3 py-2 text-left text-sm transition"
              >
                {text.delete}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <aside className="app-sidebar flex w-full min-h-0 max-h-[42vh] flex-col overflow-hidden rounded-[22px] border p-3 lg:h-full lg:max-h-none lg:w-[276px] lg:shrink-0">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.35em] text-white/55">{text.appTag}</p>
          <h1 className="mt-2 text-2xl font-semibold">{text.appTitle}</h1>
        </div>
        <button
          type="button"
          onClick={onNewConversation}
          className="primary-action rounded-full border px-4 py-2 text-sm transition hover:brightness-105"
        >
          {text.newChat}
        </button>
      </div>

      <div className="sidebar-user-card rounded-2xl border p-3 text-sm">
        <p className="font-medium">{currentUser?.username ?? text.unnamedUser}</p>
        <p className="mt-1 break-all text-xs text-white/45">{currentUser?.email ?? "--"}</p>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={onOpenSettings}
            className="rounded-full border border-white/12 bg-white/8 px-3 py-1.5 text-xs transition hover:bg-white/14"
          >
            {text.settings}
          </button>
          <button
            type="button"
            onClick={() => void onLogout()}
            className="rounded-full border border-white/12 bg-white/8 px-3 py-1.5 text-xs transition hover:bg-white/14"
          >
            {text.logout}
          </button>
        </div>
      </div>

      <div className="sidebar-provider-card mt-3 rounded-2xl border p-3 text-sm">
        <p>
          {text.currentProvider}：{providerInfo?.provider ?? text.providerLoading}
        </p>
        <p className="mt-1 break-all text-xs text-white/45">
          {providerInfo?.base_url ?? text.providerBaseUrlLoading}
        </p>
      </div>

      <div ref={conversationMenuRef} className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="mb-3 flex shrink-0 items-center justify-between">
          <p className="text-sm font-medium text-white/75">{text.historyChats}</p>
          <p className="text-xs text-white/45">
            {conversations.length}
            {text.historyCountSuffix ? ` ${text.historyCountSuffix}` : ""}
          </p>
        </div>

        <div className="sidebar-section-card mb-3 shrink-0 rounded-2xl border p-2">
          <div className="mb-2 px-1 text-xs uppercase tracking-[0.18em] text-white/45">
            {text.workspace}
          </div>
          <div className="flex items-center gap-1.5">
            <select
              value={selectedProjectScope}
              onChange={(event) => onProjectScopeChange(event.target.value)}
              className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/8 px-3 py-2 text-xs text-white outline-none"
            >
              <option value="all">{text.allWorkspaces}</option>
              <option value="unassigned">{text.unassignedWorkspace}</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
            {activeProject ? (
              <button
                type="button"
                onClick={() => void onConfigureProject()}
                className="shrink-0 rounded-xl border border-white/12 bg-white/8 px-2.5 py-2 text-[11px] text-white/70 transition hover:bg-white/14"
              >
                {text.workspaceManage}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onCreateProject}
              className="shrink-0 rounded-xl border border-white/12 bg-white/8 px-2.5 py-2 text-xs text-white/75 transition hover:bg-white/14"
              aria-label={text.newWorkspace}
            >
              +
            </button>
          </div>
        </div>

        <input
          value={conversationQuery}
          onChange={(event) => onConversationQueryChange(event.target.value)}
          placeholder={text.conversationSearchPlaceholder}
          className="mb-3 w-full shrink-0 rounded-2xl border border-white/10 bg-white/8 px-4 py-2.5 text-sm text-white placeholder:text-white/35 outline-none transition focus:border-[#f0c419]/45"
        />

        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain pr-1">
          {conversations.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-white/15 px-4 py-6 text-sm text-white/45">
              {text.noConversations}
            </div>
          ) : null}

          {conversations.length > 0 && filteredConversations.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-white/15 px-4 py-6 text-sm text-white/45">
              {text.noConversationMatches}
            </div>
          ) : null}

          {activeConversations.length > 0 ? (
            <>
              <div className="px-1 pt-1 text-[11px] uppercase tracking-[0.2em] text-white/38">
                {text.activeChats}
              </div>
              {activeConversations.map(renderConversationItem)}
            </>
          ) : null}

          {archivedConversations.length > 0 ? (
            <>
              <button
                type="button"
                onClick={onToggleArchived}
                className="mt-1 flex items-center justify-between rounded-2xl border border-white/10 bg-white/6 px-3 py-2 text-left text-xs uppercase tracking-[0.18em] text-white/55 transition hover:bg-white/10"
              >
                <span>{text.archivedChats}</span>
                <span>{showArchived ? text.hideArchived : text.showArchived}</span>
              </button>
              {shouldShowArchivedSection ? archivedConversations.map(renderConversationItem) : null}
            </>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

"use client";

type AppDialogState =
  | { type: "rename-conversation"; conversationId: string; title: string }
  | { type: "delete-conversation"; conversationId: string; title: string }
  | { type: "delete-project"; projectId: string; title: string }
  | null;

type AppDialogText = {
  rename: string;
  delete: string;
  currentConversation: string;
  dialogCancel: string;
  confirm: string;
};

type AppConfirmDialogProps = {
  dialog: AppDialogState;
  text: AppDialogText;
  title: string;
  description: string;
  renameConversationDraft: string;
  isDialogSubmitting: boolean;
  onClose: () => void;
  onRenameConversationDraftChange: (value: string) => void;
  onSubmit: () => void | Promise<void>;
};

export function AppConfirmDialog({
  dialog,
  text,
  title,
  description,
  renameConversationDraft,
  isDialogSubmitting,
  onClose,
  onRenameConversationDraftChange,
  onSubmit,
}: AppConfirmDialogProps) {
  if (!dialog) {
    return null;
  }

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void onSubmit();
        }}
        className="w-full max-w-lg overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]"
      >
        <div className="border-b border-[var(--hairline)] px-5 py-4">
          <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
            {dialog.type === "rename-conversation" ? text.rename : text.delete}
          </p>
          <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">{title}</h3>
          <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">{description}</p>
        </div>

        <div className="px-5 py-5">
          {dialog.type === "rename-conversation" ? (
            <label className="block text-sm">
              <span className="mb-2 block text-[var(--ink-soft)]">{text.currentConversation}</span>
              <input
                autoFocus
                value={renameConversationDraft}
                onChange={(event) => onRenameConversationDraftChange(event.target.value)}
                className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 text-[var(--ink-strong)] outline-none focus:border-[var(--accent-strong)]"
              />
            </label>
          ) : (
            <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3">
              <p className="break-words text-sm font-medium text-[var(--ink-strong)]">{dialog.title}</p>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[var(--hairline)] px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            disabled={isDialogSubmitting}
            className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-2 text-sm text-[var(--ink-soft)] disabled:cursor-not-allowed disabled:opacity-55"
          >
            {text.dialogCancel}
          </button>
          <button
            type="submit"
            disabled={isDialogSubmitting || (dialog.type === "rename-conversation" && !renameConversationDraft.trim())}
            className={`rounded-full px-5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55 ${
              dialog.type === "rename-conversation"
                ? "primary-action hover:brightness-105"
                : "border border-[rgba(174,65,45,0.22)] bg-[var(--danger-bg)] text-[var(--danger-text)] hover:brightness-95"
            }`}
          >
            {dialog.type === "rename-conversation" ? text.confirm : text.delete}
          </button>
        </div>
      </form>
    </div>
  );
}

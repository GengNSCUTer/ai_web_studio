"use client";

import type { Conversation, ConversationShare } from "@/lib/types";

type ShareText = {
  shareConversation: string;
  shareTitle: string;
  shareDescription: string;
  close: string;
  shareExpiresDays: string;
  shareEnable: string;
  shareDisable: string;
  shareCopy: string;
  shareCopied: string;
  openShare: string;
  shareRevoke: string;
  shareNoLink: string;
  shareCreate: string;
};

type ConversationShareModalProps = {
  conversation: Conversation | null;
  conversationShare: ConversationShare | null;
  shareExpiresDays: string;
  shareUrl: string;
  shareCopied: boolean;
  isShareBusy: boolean;
  text: ShareText;
  onClose: () => void;
  onShareExpiresDaysChange: (value: string) => void;
  onCreateOrEnableShare: () => void | Promise<void>;
  onCopyShareUrl: () => void | Promise<void>;
  onToggleShare: (enabled: boolean) => void | Promise<void>;
  onRevokeShare: () => void | Promise<void>;
};

export function ConversationShareModal({
  conversation,
  conversationShare,
  shareExpiresDays,
  shareUrl,
  shareCopied,
  isShareBusy,
  text,
  onClose,
  onShareExpiresDaysChange,
  onCreateOrEnableShare,
  onCopyShareUrl,
  onToggleShare,
  onRevokeShare,
}: ConversationShareModalProps) {
  if (!conversation) {
    return null;
  }

  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center rounded-[32px] bg-[var(--overlay-bg)] p-4 backdrop-blur-sm">
      <div className="w-full max-w-2xl overflow-hidden rounded-[28px] border border-[var(--panel-border)] bg-[var(--modal-bg)] shadow-[var(--panel-shadow)]">
        <div className="flex items-start justify-between gap-4 border-b border-[var(--hairline)] px-5 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-[var(--ink-muted)]">
              {text.shareConversation}
            </p>
            <h3 className="mt-1 text-2xl font-semibold text-[var(--ink-strong)]">{text.shareTitle}</h3>
            <p className="mt-2 text-sm leading-6 text-[var(--ink-soft)]">{text.shareDescription}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-[var(--control-border)] bg-[var(--control-bg)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
          >
            {text.close}
          </button>
        </div>
        <div className="space-y-4 px-5 py-5">
          <div className="rounded-2xl border border-[var(--hairline)] bg-[var(--soft-bg)] px-4 py-3">
            <p className="text-sm font-medium text-[var(--ink-strong)]">{conversation.title}</p>
            <p className="mt-1 text-xs text-[var(--ink-muted)]">{conversation.model_name}</p>
          </div>
          <label className="block text-sm">
            <span className="mb-2 block text-[var(--ink-soft)]">{text.shareExpiresDays}</span>
            <input
              type="number"
              min="1"
              max="365"
              value={shareExpiresDays}
              onChange={(event) => onShareExpiresDaysChange(event.target.value)}
              className="w-full rounded-2xl border border-[var(--control-border)] bg-[var(--control-bg)] px-4 py-3 outline-none focus:border-[var(--accent-strong)]"
            />
          </label>
          {conversationShare ? (
            <div className="space-y-3 rounded-2xl border border-[var(--hairline)] bg-[var(--control-bg)] px-4 py-3">
              <p className="break-all text-sm text-[var(--ink-soft)]">{shareUrl}</p>
              <p className="text-xs text-[var(--ink-muted)]">
                {conversationShare.is_enabled ? text.shareEnable : text.shareDisable}
                {conversationShare.expires_at ? ` · ${conversationShare.expires_at}` : ""}
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void onCopyShareUrl()}
                  className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                >
                  {shareCopied ? text.shareCopied : text.shareCopy}
                </button>
                <a
                  href={shareUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)]"
                >
                  {text.openShare}
                </a>
                <button
                  type="button"
                  onClick={() => void onToggleShare(!conversationShare.is_enabled)}
                  disabled={isShareBusy}
                  className="rounded-full border border-[var(--control-border)] px-3 py-1.5 text-xs text-[var(--ink-soft)] disabled:opacity-55"
                >
                  {conversationShare.is_enabled ? text.shareDisable : text.shareEnable}
                </button>
                <button
                  type="button"
                  onClick={() => void onRevokeShare()}
                  disabled={isShareBusy}
                  className="rounded-full border border-[rgba(174,65,45,0.22)] px-3 py-1.5 text-xs text-[#9f3a2b] disabled:opacity-55"
                >
                  {text.shareRevoke}
                </button>
              </div>
            </div>
          ) : (
            <p className="rounded-2xl border border-dashed border-[var(--control-border)] bg-[var(--soft-bg)] px-4 py-3 text-sm text-[var(--ink-soft)]">
              {text.shareNoLink}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-[var(--hairline)] px-5 py-4">
          <button
            type="button"
            onClick={() => void onCreateOrEnableShare()}
            disabled={isShareBusy}
            className="primary-action rounded-full px-5 py-2 text-sm font-medium disabled:opacity-55"
          >
            {text.shareCreate}
          </button>
        </div>
      </div>
    </div>
  );
}

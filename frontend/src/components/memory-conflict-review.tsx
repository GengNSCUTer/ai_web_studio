"use client";

import { useState } from "react";
import type { UserMemory } from "@/lib/types";

export type MemoryApprovalOptions = {
  supersedes_memory_id: string | null;
  expected_target_version: number | null;
  acknowledge_conflicts: boolean;
};

export function MemoryConflictReview({ memory, disabled, chinese, onApprove, onRecheck }: {
  memory: UserMemory; disabled: boolean; chinese: boolean;
  onApprove: (options: MemoryApprovalOptions) => void; onRecheck: () => void;
}) {
  const review = memory.conflict_review;
  const pairs = review?.pairs ?? [];
  const conflicts = pairs.filter((pair) => pair.relation === "contradiction" && pair.confidence >= .85);
  const choices = pairs.filter((pair) => pair.relation === "contradiction" || pair.relation === "uncertain" || pair.confidence < .85);
  const [target, setTarget] = useState(memory.supersedes_memory_id ?? conflicts[0]?.memory_id ?? "");
  const [acknowledged, setAcknowledged] = useState(false);
  const labels = chinese ? {
    duplicate: "重复", contradiction: "矛盾", complement: "补充", different_scope: "不同条件/范围", uncertain: "待确认",
  } : { duplicate: "Duplicate", contradiction: "Contradiction", complement: "Complement", different_scope: "Different scope", uncertain: "Uncertain" };
  const duplicate = pairs.some((pair) => pair.relation === "duplicate" && pair.confidence >= .85);
  const ready = review && review.status !== "stale";
  const selected = choices.find((pair) => pair.memory_id === target);

  return <div className="mt-3 w-full min-w-0 space-y-3" data-testid="memory-conflict-review">
    {pairs.map((pair) => <div key={pair.memory_id} className="rounded-xl border border-[var(--hairline)] p-3 text-xs">
      <p className="font-medium">{labels[pair.relation]} · {pair.decision_source === "rules"
        ? (chinese ? "规则判定" : "Rule judgment")
        : pair.decision_source === "unavailable" ? (chinese ? "判断不可用" : "Judgment unavailable")
        : `${chinese ? "模型自评 " : "Model confidence "}${Math.round(pair.confidence * 100)}%`}
        {pair.impact === "high" ? (chinese ? " · 高影响" : " · High impact") : ""}</p>
      <p className="mt-1">{chinese ? "已有记忆：" : "Existing memory: "}{pair.existing_title} · v{pair.version}</p>
      <p className="mt-1 whitespace-pre-wrap break-words text-[var(--ink-soft)]">{pair.existing_content}</p>
      <p className="mt-1 text-[var(--ink-muted)]">{pair.reason}</p>
    </div>)}
    {review && !review.coverage_complete ? <p className="text-xs text-[var(--ink-muted)]">
      {chinese ? "分析覆盖有限或服务不可用，请人工核对；旧记忆尚未修改。" : "Analysis is incomplete; review manually. Existing memories are unchanged."}
    </p> : null}
    {choices.length ? <label className="block text-xs">
      {chinese ? "确认后替换哪条记忆" : "Memory to replace after approval"}
      <select aria-label={chinese ? "替换目标记忆" : "Replacement memory"} value={target} disabled={disabled}
        onChange={(event) => setTarget(event.target.value)} className="mt-1 block w-full rounded-lg border border-[var(--hairline)] bg-[var(--control-bg)] p-2">
        <option value="">{chinese ? "新增/补充，不替换旧记忆" : "Add without replacing"}</option>
        {choices.map((pair) => <option key={pair.memory_id} value={pair.memory_id}>{pair.existing_title} · v{pair.version}</option>)}
      </select>
    </label> : null}
    {review?.requires_review ? <label className="flex items-start gap-2 text-xs">
      <input type="checkbox" checked={acknowledged} disabled={disabled} onChange={(event) => setAcknowledged(event.target.checked)} />
      <span>{chinese ? "我已核对新旧记忆及其范围、影响，确认以上选择" : "I reviewed both memories, their scope and impact, and confirm this choice"}</span>
    </label> : null}
    {duplicate || conflicts.length > 1 ? <p className="text-xs text-[var(--ink-muted)]">
      {duplicate ? (chinese ? "请使用已有记忆，或修改候选后重新检查。" : "Use the existing memory, or edit and recheck.")
        : (chinese ? "存在多个冲突目标，请先逐条处理后重新检查。" : "Resolve multiple conflicting memories before approval.")}
    </p> : null}
    <div className="flex flex-wrap gap-2">
      <button type="button" disabled={disabled} onClick={onRecheck}
        className="rounded-full border border-[var(--hairline)] px-3 py-1 text-xs disabled:opacity-50">
        {chinese ? "重新检查冲突" : "Recheck conflicts"}
      </button>
      <button type="button" disabled={disabled || !ready || duplicate || conflicts.length > 1
          || Boolean(review?.requires_review && !acknowledged) || Boolean(conflicts.length && !selected)}
        onClick={() => onApprove({ supersedes_memory_id: selected?.memory_id ?? null,
          expected_target_version: selected?.version ?? null, acknowledge_conflicts: acknowledged })}
        className="rounded-full bg-[var(--accent-strong)] px-3 py-1 text-xs text-white disabled:opacity-50">
        {chinese ? (selected ? "确认替换并启用" : "确认启用") : (selected ? "Replace and enable" : "Approve")}
      </button>
    </div>
  </div>;
}

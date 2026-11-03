import type { CitationAudit } from "@/lib/types";

export function CitationAuditNotice({ audit, english = false }: { audit?: CitationAudit | null; english?: boolean }) {
  if (!audit || audit.status === "not_applicable") return null;
  if (audit.status === "passed") return <p className="mt-3 text-xs text-[var(--ink-muted)]">
    {english ? "Citation labels checked · Claim support has not been verified" : "引用标签有效 · 未核验证据是否支持结论"}
  </p>;
  if (audit.status === "incomplete") return <p className="mt-3 text-xs text-[var(--ink-muted)]">
    {english ? "Answer incomplete · Citation review is incomplete" : "回答未完成 · 引用检查未完成"}
  </p>;
  return <div role="note" aria-label={english ? "Citation review" : "引用检查提示"}
    className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950">
    <p>{english ? "Some citations need review. The original answer is preserved." : "引用需要复核，原回答已保留。"}</p>
    {audit.unknown_labels.length ? <p className="mt-1">{english ? "Unknown labels: " : "不存在的来源编号："}{audit.unknown_labels.slice(0, 12).join("、")}</p> : null}
    {audit.excluded_labels.length ? <p className="mt-1">{english ? "Sources outside the final context: " : "未进入最终上下文的来源："}{audit.excluded_labels.slice(0, 12).join("、")}</p> : null}
    {audit.missing_citation ? <p className="mt-1">{english ? "Evidence was provided, but no citation label was found." : "本轮提供了证据，但回答未标注来源编号。"}</p> : null}
    {audit.reasons.includes("no_injected_evidence") ? <p className="mt-1">{english ? "No source body remained in the final context." : "参考资料正文未进入最终上下文，无法核对本轮引用。"}</p> : null}
    <p className="mt-1">{english ? "This checks labels only, not whether evidence supports each claim." : "这里只检查标签和来源范围，不判断证据是否支持每条结论。"}</p>
  </div>;
}

import type { ExternalSource } from "@/lib/types";

export function citationAnchorId(messageId: string, label: string) {
  return `evidence-${encodeURIComponent(messageId)}-${label.replace(/[\[\]]/g, "")}`;
}

export function revealCitationTarget(href: string) {
  const target = document.getElementById(href.slice(1));
  if (!target) return false;
  const details = target.closest("details");
  if (details) details.open = true;
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.focus({ preventScroll: true });
  return true;
}

export function visibleEvidenceSources(sources: ExternalSource[] = []) {
  return sources.filter((source) => source.used_in_prompt !== false
    && (source.metadata?.citation_scope !== "final_prompt"
      || Number(source.metadata?.citation_body_chars) > 0));
}

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
};

export function remarkEvidenceCitations(options: { messageId: string; labels: string[] }) {
  const allowed = new Set(options.labels);
  return (tree: MarkdownNode, file: { value: unknown }) => {
    const markdown = String(file.value);
    function walk(node: MarkdownNode) {
      if (["code", "inlineCode", "link", "linkReference", "html"].includes(node.type)) return;
      if (!node.children) return;
      node.children = node.children.flatMap((child) => {
        if (["link", "linkReference"].includes(child.type)) {
          const labelText = (child.children || []).map((item) => item.type === "text" ? item.value || "" : "").join("");
          const citation = labelText.match(/^\[?(KB|T)\s*(\d{1,9})\]?$/i);
          if (citation) {
            const label = `[${citation[1].toUpperCase()}${Number(citation[2])}]`;
            // 模型即使自己为编号附URL，引用仍只允许跳到最终映射的本地来源。
            return allowed.has(label)
              ? [{ type: "link", url: `#${citationAnchorId(options.messageId, label)}`, children: [{ type: "text", value: label }] }]
              : [{ type: "text", value: label }];
          }
          return [child];
        }
        if (child.type !== "text" || !child.value) { walk(child); return [child]; }
        // 逐个核对原文转义，避免同段出现字面示例时连真实引用也被跳过。
        const raw = markdown.slice(child.position?.start.offset, child.position?.end.offset);
        const rawLabels = [...raw.matchAll(/(\\*)\[(KB|T)\s*(\d{1,9})\]/gi)];
        const parts: MarkdownNode[] = [];
        let cursor = 0;
        for (const match of child.value.matchAll(/\[(KB|T)\s*(\d{1,9})\]/gi)) {
          const label = `[${match[1].toUpperCase()}${Number(match[2])}]`;
          const rawIndex = rawLabels.findIndex((item) => `[${item[2].toUpperCase()}${Number(item[3])}]` === label);
          if (rawIndex < 0) continue;
          const rawMatch = rawLabels.splice(rawIndex, 1)[0];
          if (rawMatch[1].length % 2) continue;
          if (!allowed.has(label)) continue;
          const index = match.index!;
          if (index > cursor) parts.push({ type: "text", value: child.value.slice(cursor, index) });
          parts.push({ type: "link", url: `#${citationAnchorId(options.messageId, label)}`,
            children: [{ type: "text", value: match[0] }] });
          cursor = index + match[0].length;
        }
        if (!cursor) return [child];
        if (cursor < child.value.length) parts.push({ type: "text", value: child.value.slice(cursor) });
        return parts;
      });
    }
    walk(tree);
  };
}

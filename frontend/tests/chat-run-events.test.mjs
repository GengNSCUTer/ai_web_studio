import assert from "node:assert/strict";
import { test } from "node:test";
import { subscribeChatRun, runMessageStatus } from "../src/lib/chat-run-events.ts";

const frame = (id, body) => `id: r:${id}\nevent: chat\ndata: ${JSON.stringify({ ...body, event_id: id })}\n\n`;

test("取消/失败与消息状态明确映射", () => {
  assert.equal(runMessageStatus("cancel_requested"), "streaming");
  assert.equal(runMessageStatus("cancelled"), "cancelled");
  assert.equal(runMessageStatus("interrupted"), "failed");
  assert.equal(runMessageStatus("succeeded"), "done");
});

test("SSE跨包解析、断线游标重连和重复事件不重复追加", async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  globalThis.window = globalThis;
  const urls = [];
  try {
    globalThis.fetch = async (url) => {
      urls.push(url);
      if (urls.length === 1) {
        const data = frame(1, { type: "answer_delta", text: "前" });
        return new Response(new ReadableStream({ start(controller) {
          controller.enqueue(new TextEncoder().encode(data.slice(0, 18)));
          controller.enqueue(new TextEncoder().encode(data.slice(18)));
          controller.close();
        } }));
      }
      return new Response(frame(1, { type: "answer_delta", text: "前" }) +
        frame(2, { type: "answer_delta", text: "后" }) + frame(3, { type: "done" }));
    };
    let answer = "";
    await subscribeChatRun("r", new AbortController().signal, (event) => {
      if (event.type === "answer_delta") answer += event.text;
    });
    assert.equal(answer, "前后");
    assert.equal(urls.length, 2);
    assert.match(urls[1], /after=1/);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
  }
});

test("末尾同序号快照也能结束订阅，主动断开不调用取消接口", async () => {
  const previous = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url) => {
      calls.push(url);
      return new Response(frame(0, { type: "run_snapshot", run: { status: "succeeded" } }));
    };
    await subscribeChatRun("r", new AbortController().signal, () => {});
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(subscribeChatRun("r", controller.signal, () => {}), { name: "AbortError" });
    assert.equal(calls.length, 1);
    assert.ok(calls.every((url) => url.includes("/events?")));
  } finally {
    globalThis.fetch = previous;
  }
});

test("生成重启快照替换旧片段，后续事件只追加到新回答", async () => {
  const previous = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(
      frame(1, { type: "answer_delta", text: "旧回答半句" }) +
      frame(2, { type: "run_snapshot", reason: "generation_restarted", run: { status: "running", content: "" } }) +
      frame(3, { type: "answer_delta", text: "新回答" }) + frame(4, { type: "done" }),
    );
    let answer = "";
    await subscribeChatRun("r", new AbortController().signal, (event) => {
      if (event.type === "run_snapshot") answer = event.run.content;
      else if (event.type === "answer_delta") answer += event.text;
    });
    assert.equal(answer, "新回答");
  } finally {
    globalThis.fetch = previous;
  }
});

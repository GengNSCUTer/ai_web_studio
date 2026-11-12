import assert from "node:assert/strict";
import { test } from "node:test";
import { subscribeChatRun, runMessageStatus } from "../src/lib/chat-run-events.ts";

class FakeEventSource extends EventTarget {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances = [];
  readyState = 0;
  closeCount = 0;
  constructor(url) {
    super();
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() { this.readyState = 2; this.closeCount += 1; }
  emit(sequence, event, id = `r:${sequence}`) {
    this.readyState = 1;
    this.dispatchEvent(new MessageEvent("chat", { data: JSON.stringify({ ...event, event_id: sequence }), lastEventId: id }));
  }
  fail(state = 0) { this.readyState = state; this.dispatchEvent(new Event("error")); }
}

function nativeSource(t) {
  const original = globalThis.EventSource;
  globalThis.EventSource = FakeEventSource;
  FakeEventSource.instances = [];
  t.after(() => { globalThis.EventSource = original; });
  return () => FakeEventSource.instances.at(-1);
}

test("取消/失败与消息状态明确映射", () => {
  assert.equal(runMessageStatus("cancel_requested"), "streaming");
  assert.equal(runMessageStatus("cancelled"), "cancelled");
  assert.equal(runMessageStatus("interrupted"), "failed");
  assert.equal(runMessageStatus("succeeded"), "done");
});

test("只创建原生同源EventSource，不使用fetch/after或手写重连", async (t) => {
  const current = nativeSource(t);
  const original = globalThis.fetch;
  globalThis.fetch = () => { throw new Error("订阅不应调用fetch"); };
  t.after(() => { globalThis.fetch = original; });
  let answer = "";
  const result = subscribeChatRun("r", new AbortController().signal, (event) => {
    if (event.type === "answer_delta") answer += event.text;
  });
  const source = current();
  assert.equal(source.url, "/api/backend/chat/runs/r/events");
  source.emit(1, { type: "answer_delta", text: "前" });
  source.fail();
  assert.equal(source.closeCount, 0);
  source.emit(1, { type: "answer_delta", text: "前" });
  source.emit(2, { type: "answer_delta", text: "后" });
  source.emit(3, { type: "done" });
  await result;
  assert.equal(answer, "前后");
  assert.equal(FakeEventSource.instances.length, 1);
  assert.equal(source.closeCount, 1);
});

test("已终态的同序号快照结束订阅，迟到事件不再更新UI", async (t) => {
  const current = nativeSource(t);
  const seen = [];
  const result = subscribeChatRun("r", new AbortController().signal, (event) => seen.push(event.type));
  const source = current();
  source.emit(0, { type: "run_snapshot", run: { status: "succeeded" } });
  await result;
  source.emit(1, { type: "answer_delta", text: "迟到" });
  assert.deepEqual(seen, ["run_snapshot"]);
  assert.equal(source.closeCount, 1);
});

test("工具步骤的终态不结束Chat订阅，后续正文与done仍可收到", async (t) => {
  const current = nativeSource(t);
  const seen = [];
  const result = subscribeChatRun("r", new AbortController().signal, (event) => seen.push(event.type));
  const source = current();
  const outcomes = [
    { type: "tool_workflow_end", status: "succeeded" },
    { type: "tool_planner_end", status: "failed" },
    ...["cancelled", "expired", "interrupted"].map((status) => ({ type: "tool_workflow_step_outcome", status })),
  ];
  for (const [index, event] of outcomes.entries()) {
    source.emit(index + 1, event);
    assert.equal(source.closeCount, 0, `工具${event.status}不能终止整个Chat`);
  }
  source.emit(6, { type: "answer_delta", text: "基于成功分支继续回答" });
  source.emit(7, { type: "done" });
  await result;
  assert.equal(seen.length, 7);
  assert.equal(seen.at(-2), "answer_delta");
  assert.equal(source.closeCount, 1);
});

test("Chat自身的run_status终态仍关闭订阅", async (t) => {
  const current = nativeSource(t);
  for (const status of ["succeeded", "failed", "cancelled", "expired", "interrupted"]) {
    const result = subscribeChatRun("r", new AbortController().signal, () => {});
    const source = current();
    source.emit(1, { type: "run_status", status: "running" });
    assert.equal(source.closeCount, 0);
    source.emit(2, { type: "run_status", status });
    await result;
    assert.equal(source.closeCount, 1);
  }
});

test("生成重启快照替换旧片段，后续仅追加新正文", async (t) => {
  const current = nativeSource(t);
  let answer = "";
  const result = subscribeChatRun("r", new AbortController().signal, (event) => {
    if (event.type === "run_snapshot") answer = event.run.content;
    else if (event.type === "answer_delta") answer += event.text;
  });
  current().emit(1, { type: "answer_delta", text: "旧回答半句" });
  current().emit(2, { type: "run_snapshot", run: { status: "running", content: "" } });
  current().emit(3, { type: "answer_delta", text: "新回答" });
  current().emit(4, { type: "done" });
  await result;
  assert.equal(answer, "新回答");
});

test("提前abort不建连接，活跃abort仅关闭订阅且移除监听", async (t) => {
  const current = nativeSource(t);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(subscribeChatRun("r", controller.signal, () => {}), { name: "AbortError" });
  assert.equal(FakeEventSource.instances.length, 0);
  const active = new AbortController();
  let count = 0;
  const result = subscribeChatRun("r", active.signal, () => { count += 1; });
  const source = current();
  active.abort();
  await assert.rejects(result, { name: "AbortError" });
  source.emit(1, { type: "answer_delta", text: "迟到" });
  assert.equal(count, 0);
  assert.equal(source.closeCount, 1);
});

test("永久关闭立即失败，不创建替代连接", async (t) => {
  const current = nativeSource(t);
  const result = subscribeChatRun("r", new AbortController().signal, () => {});
  current().fail(FakeEventSource.CLOSED);
  await assert.rejects(result, /订阅已中断/);
  assert.equal(FakeEventSource.instances.length, 1);
});

test("无业务进展的连续五次错误结束原生订阅，进展会重置计数", async (t) => {
  const current = nativeSource(t);
  const result = subscribeChatRun("r", new AbortController().signal, () => {});
  const source = current();
  for (let i = 0; i < 4; i += 1) source.fail();
  source.emit(1, { type: "answer_delta", text: "进展" });
  for (let i = 0; i < 4; i += 1) source.fail();
  assert.equal(source.closeCount, 0);
  source.fail();
  await assert.rejects(result, /订阅已中断/);
  assert.equal(source.closeCount, 1);
  assert.equal(FakeEventSource.instances.length, 1);
});

test("非法JSON/跨Run/负数或字符串游标显式失败并关闭", async (t) => {
  const current = nativeSource(t);
  for (const [sequence, id] of [[1, "other:1"], [-1, "r:-1"], ["1", "r:1"]]) {
    const result = subscribeChatRun("r", new AbortController().signal, () => {});
    const source = current();
    source.emit(sequence, { type: "answer_delta", text: "不可信" }, id);
    await assert.rejects(result, /游标无效/);
    assert.equal(source.closeCount, 1);
  }
  const result = subscribeChatRun("r", new AbortController().signal, () => {});
  const source = current();
  source.dispatchEvent(new MessageEvent("chat", { data: "{broken", lastEventId: "r:1" }));
  await assert.rejects(result, SyntaxError);
  assert.equal(source.closeCount, 1);
});

test("模型失败事件完成传递后关闭，UI回调异常也不跳过继续", async (t) => {
  const current = nativeSource(t);
  const seen = [];
  const result = subscribeChatRun("r", new AbortController().signal, (event) => seen.push(event.type));
  current().emit(1, { type: "model_error", error: "受控失败" });
  await result;
  assert.deepEqual(seen, ["model_error"]);
  const failed = subscribeChatRun("r", new AbortController().signal, () => { throw new Error("UI失败"); });
  current().emit(1, { type: "answer_delta", text: "不会丢弃后继续" });
  await assert.rejects(failed, /UI失败/);
  assert.equal(current().closeCount, 1);
});

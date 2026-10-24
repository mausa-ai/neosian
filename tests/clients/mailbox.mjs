// Keyless lifecycle and wake contracts: node tests/clients/mailbox.mjs.
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = await mkdtemp(join(tmpdir(), "neosian-mailbox-"));
const realInterval = globalThis.setInterval;
const realClear = globalThis.clearInterval;
const timers = new Set();
globalThis.setInterval = (callback) => {
  const timer = { callback, unref() {} };
  timers.add(timer);
  return timer;
};
globalThis.clearInterval = (timer) => timers.delete(timer);
const moduleFrom = async (source) => await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const until = async (check) => {
  for (let n = 0; n < 200; n++) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error("adapter did not settle");
};
const item = { id: "message", occurrence: 1, scope: "user:unit", token: "lease",
  text: "[neosian delivery — message message]\nCheck upstream" };
try {
  // Pi's real subprocess boundary with a controlled, durable recorder.
  const log = join(root, "events.jsonl");
  const reply = join(root, "reply.json");
  await writeFile(reply, JSON.stringify({ messages: [item] }));
  const recorder = (log, reply) => `
    const fs = require('node:fs');
    let body = '';
    process.stdin.on('data', chunk => body += chunk);
    process.stdin.on('end', () => {
      const payload = JSON.parse(body);
      fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify(payload) + '\\n');
      if (payload.hook_event_name === 'MailboxPoll') {
        const reply = fs.readFileSync(${JSON.stringify(reply)}, 'utf8');
        setTimeout(() => process.stdout.write(reply), 70);
      } else if (payload.hook_event_name === 'MailboxContext') {
        process.stdout.write('[neosian session identity: ' + payload.session_id + ']');
      } else if (payload.hook_event_name === 'SessionStart') {
        process.stdout.write('index\\n[neosian session identity: ' + payload.session_id + ']');
      }
    });
  `;
  const argvFor = (log, reply) => JSON.stringify([process.execPath, "-e", recorder(log, reply), "--"]);
  const source = await readFile(new URL("../../assets/clients/pi-record.ts", import.meta.url), "utf8");
  const { default: piRecord } = await moduleFrom(stripTypeScriptTypes(source.replace(
    "__NEOSIAN_RECORD_ARGV__", argvFor(log, reply)
  )));
  const callbacks = new Map(), sent = [];
  piRecord({ on: (event, callback) => callbacks.set(event, callback),
    sendMessage: (message, options) => sent.push({ message, options }) });
  let session = "old";
  const ctx = { cwd: root, sessionManager: { getSessionId: () => session } };
  const emit = async (event, value = {}) => await callbacks.get(event)?.(value, ctx);
  const events = async (file = log) =>
    (await readFile(file, "utf8").catch(() => "")).trim().split("\n").filter(Boolean).map(JSON.parse);
  await emit("session_start", { reason: "startup" });
  let [timer] = timers;
  timer.callback();
  await until(async () => (await events()).some((e) => e.hook_event_name === "MailboxDelivery"));
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].options, { triggerTurn: true, deliverAs: "followUp" });
  await emit("message_end", { message: { role: "custom", customType: "neosian-message", content: item.text } });
  await emit("message_end", { message: { role: "assistant", content: "checked" } });
  await emit("agent_settled");
  timer.callback(); // A lost recorder response never repeats the accepted turn.
  await until(async () => (await events()).filter((e) => e.hook_event_name === "MailboxDelivery").length === 2);
  assert.equal(sent.length, 1);
  await writeFile(reply, JSON.stringify({ messages: [{ ...item, id: "old-session-only" }] }));
  await until(async () => {
    if ((await events()).filter((e) => e.hook_event_name === "MailboxPoll").length === 3) return true;
    // The prior delivery may be logged before its subprocess exits. Keep
    // ticking like the real interval until that in-flight poll has drained.
    timer.callback();
    return false;
  });
  session = "new"; // Session manager is mutable while subprocess I/O is pending.
  await emit("session_start", { reason: "switch" });
  await until(async () => (await events()).some((e) => e.message_id === "old-session-only"));
  const stale = (await events()).find((e) => e.message_id === "old-session-only");
  assert.equal(stale.session_id, "old");
  assert.equal(stale.accepted, false);
  assert.equal(sent.length, 1);
  const options = { sections: {} };
  await emit("before_agent_start", { systemPromptOptions: options });
  assert.ok(options.sections.neosian.includes("identity: new"));
  assert.equal(options.sections.neosian.includes("identity: old"), false);
  await emit("session_shutdown");
  assert.equal(timers.size, 0);
  assert.ok((await events()).some((e) => e.hook_event_name === "MailboxReceived"));
  assert.ok((await events()).some((e) => e.hook_event_name === "Stop" && e.last_assistant_message === "checked"));

  // OpenCode 2: one definition whose setup registers the hooks; the wake is
  // a synthetic input, the stop an execution event; the same recorder.
  const ocLog = join(root, "opencode.jsonl");
  const ocReply = join(root, "opencode-reply.json");
  await writeFile(ocReply, JSON.stringify({ messages: [{ ...item, id: "opencode-message" }] }));
  const js = await readFile(new URL("../../assets/clients/opencode-record.js", import.meta.url), "utf8");
  const { default: opencode } = await moduleFrom(js.replace("__NEOSIAN_RECORD_ARGV__", argvFor(ocLog, ocReply)));
  assert.equal(opencode.id, "neosian-record");
  const hooks = new Map(), wakes = [], stream = [];
  let refuse = true, waiting;
  const feed = (type, data) => { stream.push({ type, data }); waiting?.(); };
  const subscribe = () => ({ async *[Symbol.asyncIterator]() {
    for (;;) {
      while (stream.length) yield stream.shift();
      await new Promise((resolve) => { waiting = resolve; });
    }
  } });
  const cleanup = await opencode.setup({
    location: { directory: root },
    session: { hook: async (name, callback) => hooks.set(name, callback),
      synthetic: async (input) => {
        if (refuse) throw new Error("temporarily unavailable");
        wakes.push(input);
        return {};
      } },
    tool: { hook: async (name, callback) => hooks.set(name, callback) },
    event: { subscribe },
  });
  [timer] = timers;
  const recorded = async (name) => (await events(ocLog)).filter((e) => e.hook_event_name === name);
  feed("session.execution.succeeded", { sessionID: "historical" });
  await timer.callback();
  assert.equal((await events(ocLog)).length, 0); // An unseen session is never polled or stopped.
  await hooks.get("prompt")({ sessionID: "live", messageID: "msg_1", prompt: { text: "continue" }, delivery: "queue" });
  assert.equal((await recorded("UserPromptSubmit"))[0].prompt, "continue");
  const request = { sessionID: "live", system: [] };
  hooks.get("context")(request);
  assert.ok(request.system[0].text.includes("identity: live"));
  await timer.callback(); // Busy: no wake attempt.
  assert.equal((await recorded("MailboxPoll")).length, 0);
  feed("session.execution.succeeded", { sessionID: "live" });
  await until(async () => (await recorded("Stop")).length === 1);
  await timer.callback();
  await until(async () => (await recorded("MailboxDelivery")).some((e) => e.accepted === false));
  refuse = false;
  await timer.callback(); // Rejected wake returns to idle and can retry.
  await until(async () => (await recorded("MailboxDelivery")).some((e) => e.accepted === true));
  assert.equal(wakes.length, 1);
  assert.equal(wakes[0].sessionID, "live");
  assert.equal(wakes[0].text, item.text);
  feed("session.synthetic", { sessionID: "live", text: item.text });
  feed("session.execution.started", { sessionID: "live" });
  feed("session.step.started", { sessionID: "live", assistantMessageID: "msg_a" });
  feed("session.text.ended", { sessionID: "live", assistantMessageID: "msg_a", ordinal: 0, text: "checked" });
  feed("session.execution.succeeded", { sessionID: "live" });
  await until(async () => (await recorded("Stop")).length === 2);
  assert.equal((await recorded("Stop"))[1].last_assistant_message, "checked");
  assert.equal((await recorded("MailboxReceived"))[0].text, item.text);
  await timer.callback(); // A delivered item never repeats the accepted turn.
  await until(async () => (await recorded("MailboxDelivery")).length === 3);
  assert.equal(wakes.length, 1);
  await hooks.get("execute.after")({ sessionID: "live", tool: "test", input: {}, id: "call",
    status: "completed", result: { content: [{ type: "text", text: "tool result" }] } });
  assert.equal((await recorded("PostToolUse"))[0].tool_response, "tool result");
  const next = { sessionID: "live", system: [] };
  hooks.get("context")(next);
  assert.ok(next.system[0].text.includes("identity: live"));
  feed("session.deleted", { sessionID: "live" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  await writeFile(ocReply, JSON.stringify({ messages: [{ ...item, id: "after-delete" }] }));
  await timer.callback();
  assert.equal((await recorded("MailboxPoll")).length, 3); // Deleted: no poll, no wake.
  assert.equal(wakes.length, 1);
  await cleanup();
  assert.equal(timers.size, 0);
  console.log("Mailbox adapters: wake acceptance/retry/dedup, busy sessions, switching, deletion and context passed");
} finally {
  globalThis.setInterval = realInterval;
  globalThis.clearInterval = realClear;
  await rm(root, { recursive: true, force: true });
}

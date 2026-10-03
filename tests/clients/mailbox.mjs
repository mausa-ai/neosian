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
  const recorder = `
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
  const source = await readFile(new URL("../../neosian/assets/clients/pi-record.ts", import.meta.url), "utf8");
  const { default: piRecord } = await moduleFrom(stripTypeScriptTypes(source.replace(
    "__NEOSIAN_RECORD_ARGV__", JSON.stringify([process.execPath, "-e", recorder, "--"])
  )));
  const callbacks = new Map(), sent = [];
  piRecord({ on: (event, callback) => callbacks.set(event, callback),
    sendMessage: (message, options) => sent.push({ message, options }) });
  let session = "old";
  const ctx = { cwd: root, sessionManager: { getSessionId: () => session } };
  const emit = async (event, value = {}) => await callbacks.get(event)?.(value, ctx);
  const events = async () => (await readFile(log, "utf8")).trim().split("\n").map(JSON.parse);
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

  // OpenCode uses its SDK's async prompt and tracks only observed live sessions.
  const js = await readFile(new URL("../../neosian/assets/clients/opencode-record.js", import.meta.url), "utf8");
  const { NeosianRecord } = await moduleFrom(js.replace("__NEOSIAN_RECORD_ARGV__", '["neosian", "record"]'));
  const calls = [], wakes = [];
  let refuse = true, pending = { ...item, id: "opencode-message" };
  const $ = (_strings, _argv, body) => ({ quiet() { return this; }, async nothrow() {
    const payload = JSON.parse(await body.text());
    calls.push(payload);
    const data = payload.hook_event_name === "MailboxPoll" ? JSON.stringify({ messages: [pending] })
      : payload.hook_event_name === "MailboxContext" ? `context for ${payload.session_id}` : "";
    return { exitCode: 0, stdout: Buffer.from(data) };
  } });
  const hooks = await NeosianRecord({ directory: root, $, client: { session: {
    messages: async () => ({ data: [] }),
    promptAsync: async (request) => {
      if (refuse) return { error: "temporarily unavailable" };
      wakes.push(request);
      return {};
    },
  } } });
  [timer] = timers;
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "historical" } } });
  await timer.callback();
  assert.equal(calls.length, 0);
  const output = { parts: [{ type: "text", id: "original", text: "continue" }] };
  await hooks["chat.message"]({ sessionID: "live" }, output);
  assert.equal(output.parts[1].synthetic, true);
  await timer.callback(); // Busy: no wake attempt.
  assert.equal(calls.some((e) => e.hook_event_name === "MailboxPoll"), false);
  const idle = () => hooks.event({ event: { type: "session.idle", properties: { sessionID: "live" } } });
  await idle();
  await timer.callback();
  assert.equal(calls.at(-1).accepted, false);
  refuse = false;
  await timer.callback(); // Rejected wake returns to idle and can retry.
  assert.equal(wakes.length, 1);
  assert.equal(wakes[0].path.id, "live");
  assert.equal(wakes[0].body.parts[0].synthetic, true);
  assert.equal(calls.at(-1).accepted, true);
  await idle();
  await timer.callback();
  assert.equal(wakes.length, 1);
  const tool = { output: "tool result" };
  await hooks["tool.execute.after"]({ sessionID: "live", tool: "test", args: {}, callID: "call" }, tool);
  assert.equal(tool.output, "tool result\n\ncontext for live");
  await hooks.event({ event: { type: "session.deleted", properties: { info: { id: "live" } } } });
  pending = { ...item, id: "after-delete" };
  await timer.callback();
  assert.equal(wakes.length, 1);
  await hooks.event({ event: { type: "server.instance.disposed", properties: { directory: root } } });
  assert.equal(timers.size, 0);
  console.log("Mailbox adapters: wake acceptance/retry/dedup, busy sessions, switching, deletion and context passed");
} finally {
  globalThis.setInterval = realInterval;
  globalThis.clearInterval = realClear;
  await rm(root, { recursive: true, force: true });
}

// Keyless adapter check: node tests/clients/pi_record.mjs (Node >=22.19).
// This optional client-runtime check does not add Node to the Python gate.
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = await mkdtemp(join(tmpdir(), "neosian-pi-adapter-"));
try {
  const log = join(root, "events.jsonl");
  const recorder = `
    const fs = require('node:fs');
    let body = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => body += chunk);
    process.stdin.on('end', () => {
      const payload = JSON.parse(body);
      fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify(payload) + '\\n');
      if (payload.hook_event_name === 'SessionStart')
        process.stdout.write(payload.source + ':' + payload.session_id);
    });
  `;
  const template = await readFile(new URL("../../neosian/assets/clients/pi-record.ts", import.meta.url), "utf8");
  const load = async (argv) => {
    const source = stripTypeScriptTypes(template.replace("__NEOSIAN_RECORD_ARGV__", JSON.stringify(argv)));
    const { default: extension } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
    const handlers = new Map();
    extension({ on: (event, callback) => handlers.set(event, callback) });
    return async (event, data = {}) => await handlers.get(event)?.(data, ctx);
  };
  let session = "one";
  const ctx = { cwd: root, sessionManager: { getSessionId: () => session } };
  const emit = await load([process.execPath, "-e", recorder, "--"]);
  const message = (role, value) => ({ message: { role, content: [{ type: "text", text: value }] } });
  await emit("session_start", { reason: "startup" });
  const options = { sections: { other: "preserved" } };
  await emit("before_agent_start", { systemPromptOptions: options });
  assert.deepEqual(options.sections, { other: "preserved", neosian: "startup:one" });
  await emit("message_end", message("user", "hello"));
  const long = "tail-" + "é".repeat(50_000);
  await Promise.all([1, 2, 3].map((n) => emit("tool_result", {
    toolName: n === 2 ? "mcp__neosian_memory__continue_session" : "bash",
    toolCallId: `outer/${n}`, parentToolCallId: "outer", input: { n },
    content: [{ type: "text", text: n === 2 ? "[continuing conversation prior]" : long }],
    structuredContent: n === 3 ? { detail: "structured" } : undefined,
    isError: n === 1,
  })));
  await emit("message_end", message("assistant", "retry me"));
  await emit("agent_end"); // This MUST NOT commit the span.
  let events = (await readFile(log, "utf8")).trim().split("\n").map(JSON.parse);
  assert.equal(events.filter((e) => e.hook_event_name === "Stop").length, 0);
  await emit("session_compact");
  const original = [
    { role: "system", sections: { preamble: "pi", neosian: "stale" } },
    { role: "user", content: "hello" },
    { role: "system", sections: { tools: "kept", neosian: "also stale" } },
  ];
  const updated = await emit("context_with_system", { messages: original });
  assert.deepEqual(updated.messages, [
    { role: "system", sections: { preamble: "pi", neosian: "compact:one" } },
    original[1], { role: "system", sections: { tools: "kept" } },
  ]);
  assert.equal(original[0].sections.neosian, "stale");
  await emit("message_end", message("assistant", "final"));
  await emit("agent_settled");
  await emit("agent_settled"); // No duplicate turn.
  await emit("message_end", message("user", "next prompt"));
  await emit("message_end", message("assistant", "second answer"));
  await emit("agent_settled");
  await emit("session_shutdown");
  session = "two";
  await emit("session_start", { reason: "resume" });
  await emit("before_agent_start", { systemPromptOptions: options });
  assert.equal(options.sections.neosian, "resume:two");
  assert.equal(await emit("context_with_system", { messages: original }), undefined);
  await emit("message_end", message("user", "interrupted"));
  await emit("session_shutdown"); // Drain an open span once on orderly exit.
  events = (await readFile(log, "utf8")).trim().split("\n").map(JSON.parse);
  const stops = events.filter((e) => e.hook_event_name === "Stop");
  assert.deepEqual(stops.map((e) => [e.session_id, e.last_assistant_message]), [
    ["one", "final"], ["one", "second answer"], ["two", ""],
  ]);
  const calls = events.filter((e) => e.hook_event_name === "PostToolUse");
  assert.deepEqual(calls.map((e) => e.tool_use_id), ["outer/1", "outer/2", "outer/3"]);
  assert.ok(calls[0].tool_response.content[0].text === long, "long Unicode result preserved");
  assert.equal(calls[0].tool_response.isError, true);
  assert.deepEqual(calls[2].tool_response.structuredContent, { detail: "structured" });
  const missing = await load([join(root, "missing-recorder")]);
  await missing("session_start", { reason: "startup" });
  await missing("message_end", message("user", "still runs"));
  await missing("agent_settled");
  await missing("session_shutdown");
  console.log("Pi adapter: concurrent/nested results, settlement, compaction, resume, shutdown and failure passed");
} finally {
  await rm(root, { recursive: true, force: true });
}

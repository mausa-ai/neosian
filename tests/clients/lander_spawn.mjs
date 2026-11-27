// Keyless spawn check for the detached lander (the T1e1d slice of the
// T1e1 plan): node tests/clients/lander_spawn.mjs CLIENT SESSION ARGV PROJECT
// (Node >=22.19). CLIENT is `opencode` or `pi`; ARGV is the recorder's
// command line as JSON, as the installer writes it into the adapter. The
// generated OpenCode plugin or Pi extension runs its own spawn code through
// a prompt and a Stop of the real recorder; once the Stop's child has
// closed (its exit and its standard streams), the harness prints `stopped`
// and Node exits at once, as a client that quits right after a turn does.
// `tests/contract/landing/test_lander.py` runs it and checks that the turn
// lands afterwards. This optional client-runtime check adds Node to no gate.
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";

const [client, session, argv, project] = process.argv.slice(2);
const moduleFrom = async (source) =>
  await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const adapter = async (name) =>
  (await readFile(new URL(`../../assets/clients/${name}`, import.meta.url), "utf8"))
    .replace("__NEOSIAN_RECORD_ARGV__", argv);

if (client === "pi") {
  const source = stripTypeScriptTypes(await adapter("pi-record.ts"));
  const { default: extension } = await moduleFrom(source);
  const handlers = new Map();
  extension({ on: (event, callback) => handlers.set(event, callback) });
  const ctx = { cwd: project, sessionManager: { getSessionId: () => session } };
  const emit = async (event, data = {}) => await handlers.get(event)?.(data, ctx);
  await emit("message_end", { message: { role: "user", content: "survive the client" } });
  await emit("message_end", { message: { role: "assistant", content: "done" } });
  // The Stop: resolves once its child has closed.
  await emit("agent_settled");
} else if (client === "opencode") {
  const { default: plugin } = await moduleFrom(await adapter("opencode-record.js"));
  const hooks = new Map();
  const events = [];
  let wake = () => {};
  let observed = () => {};
  // The plugin awaits each event's handling before it asks for the next,
  // so a pull on an empty queue means every event so far was handled.
  async function* subscribe({ signal }) {
    while (!signal.aborted) {
      if (events.length) {
        yield events.shift();
        continue;
      }
      observed();
      await new Promise((resolve) => { wake = resolve; });
    }
  }
  const push = async (...items) => {
    const handled = new Promise((resolve) => { observed = resolve; });
    events.push(...items);
    wake();
    await handled;
  };
  await plugin.setup({
    location: { directory: project },
    session: {
      hook: async (name, callback) => hooks.set(name, callback),
      synthetic: async () => {},
    },
    tool: { hook: async (name, callback) => hooks.set(name, callback) },
    event: { subscribe },
  });
  await hooks.get("prompt")({
    prompt: { text: "survive the client" }, sessionID: session, messageID: "message-1",
  });
  const data = { sessionID: session, assistantMessageID: "assistant-1" };
  await push(
    { type: "session.execution.started", data: { sessionID: session } },
    { type: "session.step.started", data },
    { type: "session.text.ended", data: { ...data, text: "done" } },
    // The Stop: handled once its child has closed.
    { type: "session.execution.succeeded", data: { sessionID: session } },
  );
} else {
  throw new Error(`unknown client ${client}`);
}
process.stdout.write("stopped\n");
process.exit(0);

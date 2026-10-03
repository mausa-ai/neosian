// neosian record: Pi 1.0.1+ lifecycle events, never transcript files.
// Regenerate with `neosian record install --client pi`.
import { spawn } from "node:child_process";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const ARGV = __NEOSIAN_RECORD_ARGV__;

export default function neosianRecord(pi: ExtensionAPI) {
  let queue: Promise<string> = Promise.resolve("");
  let context = "";
  let compacted = false;
  let pending = false;
  let answer = "";
  let generation = 0;
  let timer: ReturnType<typeof setInterval> | undefined;
  let polling = false;
  const delivered = new Set<string>();

  // Capture identity before queuing: a switch must not move an old event
  // into a new session. The pipe keeps payloads out of argv and shell syntax.
  const record = (ctx: ExtensionContext, payload: Record<string, unknown>, json = false,
    session = ctx.sessionManager.getSessionId()) => {
    const body = JSON.stringify({
      ...payload,
      session_id: session,
    });
    const cwd = ctx.cwd;
    queue = queue.then(() => new Promise<string>((resolve) => {
      const child = spawn(ARGV[0], [...ARGV.slice(1), "--project", cwd, ...(json ? ["--json"] : [])], {
        cwd, stdio: ["pipe", "pipe", "ignore"], timeout: 30_000,
      });
      let output = "";
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (chunk: string) => { output += chunk; });
      child.stdin.on("error", () => {}); // EPIPE when the recorder refuses
      child.on("error", () => resolve(""));
      child.on("close", (code) => {
        if (code !== 0) console.error("neosian: recorder failed; check neosian status");
        resolve(code === 0 ? output.trimEnd() : "");
      });
      child.stdin.end(body);
    })).catch(() => ""); // A broken recorder never blocks the agent.
    return queue;
  };

  const refresh = async (ctx: ExtensionContext, source: string) => {
    context = await record(ctx, { hook_event_name: "SessionStart", source });
  };
  const text = (content: string | { type: string; text?: string }[]) =>
    typeof content === "string" ? content : content
      .filter((part) => part.type === "text")
      .map((part) => part.text ?? "").join("\n");
  const finish = async (ctx: ExtensionContext) => {
    if (!pending) return;
    pending = false;
    const last = answer;
    answer = "";
    await record(ctx, { hook_event_name: "Stop", last_assistant_message: last });
  };

  const poll = async (ctx: ExtensionContext, epoch: number) => {
    if (polling || epoch !== generation) return;
    polling = true;
    const session = ctx.sessionManager.getSessionId();
    try {
      const output = await record(ctx, { hook_event_name: "MailboxPoll" }, true);
      const items = output ? JSON.parse(output).messages ?? [] : [];
      for (const item of items) {
        const key = `${item.id}:${item.occurrence}`;
        const valid = epoch === generation;
        if (valid && !delivered.has(key)) {
          pi.sendMessage({ customType: "neosian-message", content: item.text,
            display: true, details: { id: item.id, occurrence: item.occurrence } },
            { triggerTurn: true, deliverAs: "followUp" });
          delivered.add(key);
        }
        await record(ctx, { hook_event_name: "MailboxDelivery", scope: item.scope,
          message_id: item.id, occurrence: item.occurrence, token: item.token,
          accepted: valid }, true, session);
      }
    } catch { /* Pending messages survive receiver/transport failures. */ }
    finally { polling = false; }
  };

  pi.on("session_start", async (event, ctx) => {
    generation++;
    if (timer) clearInterval(timer);
    const epoch = generation;
    pending = false;
    answer = "";
    compacted = false;
    await refresh(ctx, event.reason);
    if (epoch !== generation) return;
    timer = setInterval(() => { void poll(ctx, epoch); }, 5000);
    timer.unref();
  });
  pi.on("session_compact", async (_event, ctx) => {
    await refresh(ctx, "compact");
    compacted = true;
  });
  pi.on("before_agent_start", async (event, ctx) => {
    const inbox = await record(ctx, { hook_event_name: "MailboxContext" });
    const base = context.split("[neosian session identity:")[0].trimEnd();
    event.systemPromptOptions.sections.neosian = [base, inbox].filter(Boolean).join("\n\n");
    compacted = false;
  });
  // Automatic compaction can retry without before_agent_start. Replace
  // only our section in that request, preserving Pi's other prompt state.
  pi.on("context_with_system", (event) => {
    if (!compacted) return;
    let placed = false;
    return { messages: event.messages.map((message) => {
      if (message.role !== "system" || !message.sections) return message;
      const sections = { ...message.sections };
      delete sections.neosian;
      if (!placed) {
        sections.neosian = context;
        placed = true;
      }
      return { ...message, sections };
    }) };
  });
  pi.on("message_end", async (event, ctx) => {
    const message = event.message;
    if (message.role === "user") {
      pending = true;
      answer = "";
      await record(ctx, { hook_event_name: "UserPromptSubmit", prompt: text(message.content) });
    } else if (message.role === "custom" && message.customType === "neosian-message") {
      pending = true;
      answer = "";
      await record(ctx, { hook_event_name: "MailboxReceived", text: text(message.content) });
    } else if (message.role === "assistant") {
      answer = text(message.content);
    }
  });
  pi.on("tool_result", async (event, ctx) => {
    pending = true;
    await record(ctx, {
      hook_event_name: "PostToolUse",
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      tool_input: event.input,
      tool_response: {
        content: event.content,
        isError: event.isError,
        ...(event.structuredContent === undefined ? {} : { structuredContent: event.structuredContent }),
      },
    });
  });
  // agent_end also fires before automatic retry/recovery. Settlement is
  // the boundary that commits one span, including nested MCP results.
  pi.on("agent_settled", async (_event, ctx) => { await finish(ctx); });
  pi.on("session_shutdown", async (_event, ctx) => {
    generation++;
    if (timer) clearInterval(timer);
    timer = undefined;
    await finish(ctx);
    await queue;
  });
}

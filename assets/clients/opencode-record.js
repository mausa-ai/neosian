// neosian record — the OpenCode plugin.
//
// Written by `neosian record install --client opencode`; regenerate with
// the installer rather than editing — ARGV below is the whole
// configuration. OpenCode has no shell hooks; its plugin API (OpenCode 2:
// one default definition whose `setup` registers hooks) is mapped here
// onto the three payloads `neosian record` reads (a prompt, a tool round,
// a stop), so the verb and the record are identical across clients
// (neosian docs agents). One plugin serves every project: the verb
// derives the layout from the directory OpenCode opened.
import { spawn } from "node:child_process";

const ARGV = __NEOSIAN_RECORD_ARGV__;
const DELIVERY = "[neosian delivery —";

export default {
  id: "neosian-record",
  setup: async (ctx) => {
    const directory = ctx.location.directory;
    // Quiet and never throwing: a broken store never blocks the agent.
    const record = (payload, json = false) => new Promise((resolve) => {
      const command = [...ARGV, "--project", directory];
      if (json) command.push("--json");
      const child = spawn(command[0], command.slice(1), { stdio: ["pipe", "pipe", "ignore"] });
      const chunks = [];
      child.stdout.on("data", (chunk) => chunks.push(chunk));
      child.on("error", () => resolve(""));
      child.on("close", (code) => resolve(code === 0 ? Buffer.concat(chunks).toString().trimEnd() : ""));
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
    });
    // A tool's result as the model reads it: its content (a string or text
    // blocks, an MCP tool's included), else its typed output.
    const text = (result) =>
      typeof result.content === "string" ? result.content
        : result.content?.length
          ? result.content.filter((part) => part.type === "text").map((part) => part.text).join("\n")
          : result.output === undefined ? "" : JSON.stringify(result.output);
    const active = new Map(); // a session seen at a prompt: "busy" or "idle"
    const frames = new Map(); // the inbox frame the turn's requests carry
    const replies = new Map(); // the assistant's text of the current step
    // The prompt callback can be queued while the preceding execution is
    // still streaming. Only an observed start binds a prompt to an execution.
    const pending = new Map();
    const executing = new Map();
    const delivered = new Set();
    const inbox = async (id) => {
      const frame = await record({ session_id: id, hook_event_name: "MailboxContext" });
      if (frame) frames.set(id, frame);
      else frames.delete(id);
    };
    let polling = false;
    const timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        for (const [id, state] of active) {
          if (state !== "idle") continue;
          const output = await record({ session_id: id, hook_event_name: "MailboxPoll" }, true);
          const items = output ? JSON.parse(output).messages ?? [] : [];
          for (const item of items) {
            const key = `${item.id}:${item.occurrence}`;
            const report = (accepted) => record({ session_id: id, hook_event_name: "MailboxDelivery",
              message_id: item.id, scope: item.scope, occurrence: item.occurrence,
              token: item.token, accepted }, true);
            if (active.get(id) !== "idle") { await report(false); continue; }
            if (!delivered.has(key)) {
              active.set(id, "busy");
              try {
                await ctx.session.synthetic({ sessionID: id, text: item.text, description: "neosian delivery" });
              } catch {
                // Release the reservation so a later poll may retry.
                if (active.has(id)) active.set(id, "idle");
                await report(false);
                continue;
              }
              delivered.add(key);
            }
            await report(true);
          }
        }
      } catch { /* The reservation expires; normal activity also picks up unread items. */ }
      finally { polling = false; }
    }, 5000);
    timer.unref();
    await ctx.session.hook("prompt", async (event) => {
      const prompt = event.prompt.text;
      if (!prompt) return;
      const id = event.sessionID;
      const queue = pending.get(id) ?? [];
      queue.push(event.messageID);
      pending.set(id, queue);
      active.set(id, "busy");
      await inbox(id);
      await record({ session_id: id, hook_event_name: "UserPromptSubmit", generation_id: event.messageID, prompt });
    });
    // The frame rides every model request of the turn as a system part,
    // refreshed after each tool round; the user's own text is never edited.
    await ctx.session.hook("context", (event) => {
      const frame = frames.get(event.sessionID);
      if (frame) event.system.push({ type: "text", text: frame });
    });
    await ctx.tool.hook("execute.after", async (event) => {
      const id = event.sessionID;
      await record({
        session_id: id,
        hook_event_name: "PostToolUse",
        // A late tool callback can also arrive in a different execution.
        // Only attribute it when the host names the current assistant message.
        generation_id: event.assistantMessageID && event.assistantMessageID === replies.get(id)?.message
          ? executing.get(id) : undefined,
        tool_name: event.tool,
        tool_input: event.input,
        tool_use_id: event.id,
        tool_response: event.status === "completed" ? text(event.result) : event.error.message,
      });
      await inbox(id);
    });
    const observe = async (event) => {
      const id = event.data?.sessionID;
      if (!id) return;
      if (event.type === "session.synthetic") {
        if (event.data.text.startsWith(DELIVERY))
          await record({ session_id: id, hook_event_name: "MailboxReceived", text: event.data.text });
      } else if (event.type === "session.step.started") {
        replies.set(id, { message: event.data.assistantMessageID, parts: [] });
      } else if (event.type === "session.text.ended") {
        const reply = replies.get(id);
        if (reply && reply.message === event.data.assistantMessageID) reply.parts.push(event.data.text);
      } else if (event.type === "session.deleted") {
        active.delete(id);
        frames.delete(id);
        replies.delete(id);
        pending.delete(id);
        executing.delete(id);
      } else if (event.type === "session.execution.started") {
        const queue = pending.get(id) ?? [];
        executing.set(id, queue.length === 1 ? queue[0] : undefined);
        pending.delete(id);
        replies.delete(id);
        if (active.has(id)) active.set(id, "busy");
      } else if (event.type.startsWith("session.execution.") && active.has(id)) {
        // Succeeded, failed or interrupted: the turn is over.
        const generation = executing.get(id);
        const answer = (replies.get(id)?.parts ?? []).join("\n");
        // Only the next unambiguous execution start can claim another generation.
        if (!executing.has(id) && pending.get(id)?.length === 1) pending.delete(id);
        active.set(id, pending.has(id) ? "busy" : "idle");
        if (!pending.has(id)) frames.delete(id);
        replies.delete(id);
        executing.delete(id);
        await record({ session_id: id, hook_event_name: "Stop", generation_id: generation,
          last_assistant_message: answer });
      }
    };
    const controller = new AbortController();
    const watch = async () => {
      while (!controller.signal.aborted) {
        try {
          for await (const event of ctx.event.subscribe({ signal: controller.signal })) await observe(event);
        } catch {
          executing.clear();
          pending.clear();
          replies.clear();
          // The stream is volatile by contract; a failed one is resubscribed.
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
    };
    watch();
    return () => { clearInterval(timer); controller.abort(); };
  },
};

// neosian record — the OpenCode plugin.
//
// Written by `neosian record install --client opencode`; regenerate with
// the installer rather than editing — ARGV below is the whole
// configuration. OpenCode has no shell hooks; its plugin hooks are mapped
// here onto the three payloads `neosian record` reads (a prompt, a tool
// round, a stop), so the verb and the record are identical across
// clients (neosian docs agents). One plugin serves every project: the
// verb derives the layout from the directory OpenCode opened.
const ARGV = __NEOSIAN_RECORD_ARGV__;

export const NeosianRecord = async ({ client, $, directory }) => {
  const active = new Map();
  const delivered = new Set();
  let polling = false;
  const record = async (payload, json = false) => {
    const body = new Response(JSON.stringify(payload));
    // Quiet and never throwing: a broken store never blocks the agent.
    const command = [...ARGV, "--project", directory];
    if (json) command.push("--json");
    const result = await $`${command} < ${body}`.quiet().nothrow();
    return result.exitCode === 0 ? result.stdout.toString().trimEnd() : "";
  };
  const text = (parts) =>
    parts
      .filter((part) => part.type === "text" && !part.synthetic)
      .map((part) => part.text)
      .join("\n");
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
          if (active.get(id) !== "idle") {
            await record({ session_id: id, hook_event_name: "MailboxDelivery",
              message_id: item.id, scope: item.scope, occurrence: item.occurrence,
              token: item.token, accepted: false }, true);
            continue;
          }
          if (!delivered.has(key)) {
            active.set(id, "busy");
            let accepted = false;
            try {
              const response = await client.session.promptAsync({ path: { id }, body: {
                parts: [{ type: "text", text: item.text, synthetic: true }],
              } });
              accepted = !response.error;
            } catch { /* Release the reservation so a later poll may retry. */ }
            if (!accepted) {
              if (active.has(id)) active.set(id, "idle");
              await record({ session_id: id, hook_event_name: "MailboxDelivery",
                message_id: item.id, scope: item.scope, occurrence: item.occurrence,
                token: item.token, accepted: false }, true);
              continue;
            }
            delivered.add(key);
          }
          await record({ session_id: id, hook_event_name: "MailboxDelivery",
            message_id: item.id, scope: item.scope, occurrence: item.occurrence,
            token: item.token, accepted: true }, true);
        }
      }
    } catch { /* The reservation expires; normal activity also picks up unread items. */ }
    finally { polling = false; }
  }, 5000);
  timer.unref();
  return {
    "chat.message": async (input, output) => {
      const prompt = text(output.parts);
      if (!prompt) {
        const wake = output.parts.find((part) => part.synthetic &&
          part.type === "text" && part.text.startsWith("[neosian delivery —"));
        if (wake) await record({ session_id: input.sessionID,
          hook_event_name: "MailboxReceived", text: wake.text });
        return;
      }
      active.set(input.sessionID, "busy");
      const inbox = await record({ session_id: input.sessionID, hook_event_name: "MailboxContext" });
      const first = output.parts.find((part) => part.type === "text");
      if (inbox && first) output.parts.push({ ...first, id: `prt_${crypto.randomUUID().replaceAll("-", "")}`,
        text: inbox, synthetic: true });
      await record({
        session_id: input.sessionID,
        hook_event_name: "UserPromptSubmit",
        prompt,
      });
    },
    "tool.execute.after": async (input, output) => {
      await record({
        session_id: input.sessionID,
        hook_event_name: "PostToolUse",
        tool_name: input.tool,
        tool_input: input.args,
        tool_use_id: input.callID,
        // An MCP tool hands back its raw result: the text is its content.
        tool_response: output.output ?? text(output.content ?? []),
      });
      const inbox = await record({ session_id: input.sessionID, hook_event_name: "MailboxContext" });
      if (inbox) output.output = `${output.output ?? text(output.content ?? [])}\n\n${inbox}`;
    },
    event: async ({ event }) => {
      if (event.type === "server.instance.disposed") {
        if (event.properties.directory === directory) {
          clearInterval(timer);
          active.clear();
        }
        return;
      }
      const id = event.type === "session.deleted"
        ? event.properties.info.id : event.properties.sessionID;
      if (event.type === "session.deleted") { active.delete(id); return; }
      if (event.type === "session.status" && active.has(id)) active.set(id, event.properties.status.type);
      if (event.type !== "session.idle") return;
      if (!active.has(id)) return;
      active.set(id, "idle");
      const listed = await client.session.messages({ path: { id } });
      const messages = listed.data ?? [];
      const last = [...messages].reverse().find((m) => m.info.role === "assistant");
      await record({
        session_id: id,
        hook_event_name: "Stop",
        last_assistant_message: last ? text(last.parts) : "",
      });
    },
  };
};

// neosian record — the OpenCode plugin.
//
// Written by `neosian record install --client opencode`; regenerate with
// the installer rather than editing — ARGV below is the whole
// configuration. OpenCode has no shell hooks; its plugin hooks are mapped
// here onto the three payloads `neosian record` reads (a prompt, a tool
// round, a stop), so the verb and the record are identical across
// clients (neosian docs agents).
const ARGV = __NEOSIAN_RECORD_ARGV__;

export const NeosianRecord = async ({ client, $ }) => {
  const record = async (payload) => {
    const body = new Response(JSON.stringify(payload));
    // Quiet and never throwing: a broken store never blocks the agent.
    await $`${ARGV} < ${body}`.quiet().nothrow();
  };
  const text = (parts) =>
    parts
      .filter((part) => part.type === "text" && !part.synthetic)
      .map((part) => part.text)
      .join("\n");
  return {
    "chat.message": async (input, output) => {
      const prompt = text(output.parts);
      if (!prompt) return;
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
        tool_response: output.output,
      });
    },
    event: async ({ event }) => {
      if (event.type !== "session.idle") return;
      const id = event.properties.sessionID;
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

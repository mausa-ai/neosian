//! Per-adapter stream fixtures, trimmed from the real runs in
//! record/x2b-captures-2026-11-21 (Claude Code 2.1.330 `claude-c4`, codex-cli
//! 0.169.0 `codex-x3`, OpenCode 2.3.2 `opencode-o4`): the keys the record
//! reads are kept as the harness printed them, with some of the rest.

use serde_json::json;

use super::*;

fn all(agent: Agent, lines: &[&str]) -> Vec<Said> {
    lines.iter().flat_map(|line| read(agent, line)).collect()
}

const CLAUDE: &[&str] = &[
    r#"{"type":"system","subtype":"hook_started","hook_name":"SessionStart:resume","session_id":"5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe"}"#,
    r#"{"type":"system","subtype":"init","cwd":"/private/tmp/p","session_id":"5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe","tools":["Bash","Write"]}"#,
    r#"{"type":"assistant","message":{"model":"claude-fable-5-2","role":"assistant","content":[{"type":"tool_use","id":"toolu_01Ue","name":"Write","input":{"file_path":"/private/tmp/p/probe.txt","content":"ok\n"},"caller":{"type":"direct"}}]},"parent_tool_use_id":null,"session_id":"5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe"}"#,
    r#"{"type":"user","message":{"role":"user","content":[{"tool_use_id":"toolu_01Ue","type":"tool_result","content":"File created successfully at: /private/tmp/p/probe.txt"}]},"parent_tool_use_id":null,"session_id":"5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe","tool_use_result":{"type":"create","filePath":"/private/tmp/p/probe.txt","content":"ok\n"}}"#,
    r#"{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_01Tp","name":"Bash","input":{"command":"touch probe2.txt","description":"Create an empty file"}}]},"parent_tool_use_id":null}"#,
    r#"{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"},"session_id":"5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe"}"#,
    r#"{"type":"user","message":{"role":"user","content":[{"tool_use_id":"toolu_01Tp","type":"tool_result","content":"(Bash completed with no output)","is_error":false}]},"parent_tool_use_id":null,"tool_use_result":{"stdout":"","stderr":"","interrupted":false,"isImage":false,"noOutputExpected":true}}"#,
    r#"{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"done"}]},"parent_tool_use_id":null}"#,
    r#"{"type":"result","subtype":"success","is_error":false,"duration_ms":15504,"num_turns":3,"result":"done","stop_reason":"end_turn","session_id":"5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe","permission_denials":[]}"#,
];

#[test]
fn claude_code_calls_pair_with_their_structured_results() {
    let said = all(Agent::ClaudeCode, CLAUDE);
    let session = "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe".to_string();
    assert_eq!(
        said,
        vec![
            Said::Session(session),
            Said::Call {
                id: "toolu_01Ue".into(),
                name: "Write".into(),
                input: json!({"file_path": "/private/tmp/p/probe.txt", "content": "ok\n"}),
            },
            Said::Result {
                id: "toolu_01Ue".into(),
                response: json!({"type": "create", "filePath": "/private/tmp/p/probe.txt", "content": "ok\n"}),
                ok: true,
            },
            Said::Call {
                id: "toolu_01Tp".into(),
                name: "Bash".into(),
                input: json!({"command": "touch probe2.txt", "description": "Create an empty file"}),
            },
            Said::Result {
                id: "toolu_01Tp".into(),
                response: json!({"stdout": "", "stderr": "", "interrupted": false, "isImage": false, "noOutputExpected": true}),
                ok: true,
            },
            Said::Text("done".into()),
            Said::Answer("done".into()),
            Said::Completed,
        ]
    );
}

#[test]
fn claude_code_subagent_rounds_errors_and_failures() {
    let subagent = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_sub","name":"Read","input":{}}]},"parent_tool_use_id":"toolu_task"}"#;
    assert_eq!(read(Agent::ClaudeCode, subagent), vec![]);
    let denied = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_x","content":"Permission for this tool use was denied.","is_error":true}]},"parent_tool_use_id":null}"#;
    assert_eq!(
        read(Agent::ClaudeCode, denied),
        vec![Said::Result {
            id: "toolu_x".into(),
            response: json!("Permission for this tool use was denied."),
            ok: false,
        }]
    );
    let failed = r#"{"type":"result","subtype":"error_during_execution","is_error":true}"#;
    assert_eq!(read(Agent::ClaudeCode, failed), vec![Said::Failed]);
    let refused =
        r#"{"type":"result","subtype":"success","is_error":true,"result":"Not logged in"}"#;
    assert_eq!(read(Agent::ClaudeCode, refused), vec![Said::Failed]);
    // Two results on one line: the structured result belongs to neither.
    let two = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"a","content":"one"},{"type":"tool_result","tool_use_id":"b","content":"two"}]},"tool_use_result":{"stdout":"?"}}"#;
    let results = read(Agent::ClaudeCode, two);
    assert_eq!(
        results[1],
        Said::Result {
            id: "b".into(),
            response: json!("two"),
            ok: true
        }
    );
}

const CODEX: &[&str] = &[
    r#"{"type":"thread.started","thread_id":"01a1fb84-1ba9-7f52-90b8-d807e8cc7651"}"#,
    r#"{"type":"turn.started"}"#,
    r#"{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"I’ll write `ok` to `probe3.txt` and verify its contents."}}"#,
    r#"{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"printf '%s' ok > probe3.txt\"","aggregated_output":"","exit_code":null,"status":"in_progress"}}"#,
    r#"{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"printf '%s' ok > probe3.txt\"","aggregated_output":"","exit_code":0,"status":"completed"}}"#,
    r#"{"type":"item.completed","item":{"id":"item_2","type":"command_execution","command":"/bin/zsh -lc 'od -An -tx1 probe3.txt'","aggregated_output":"           6f  6b\n\n","exit_code":0,"status":"completed"}}"#,
    r#"{"type":"item.completed","item":{"id":"item_r","type":"reasoning","text":"thinking"}}"#,
    r#"{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"done"}}"#,
    r#"{"type":"turn.completed","usage":{"input_tokens":55896,"cached_input_tokens":50688,"output_tokens":175}}"#,
];

#[test]
fn codex_items_are_tool_calls_in_its_own_vocabulary() {
    let said = all(Agent::Codex, CODEX);
    assert_eq!(
        said[0],
        Said::Session("01a1fb84-1ba9-7f52-90b8-d807e8cc7651".into())
    );
    assert_eq!(
        said[2],
        Said::Tool {
            id: Some("item_1".into()),
            name: "command_execution".into(),
            input: json!({"command": "/bin/zsh -lc \"printf '%s' ok > probe3.txt\""}),
            response: json!({"aggregated_output": "", "exit_code": 0, "status": "completed"}),
        }
    );
    assert!(matches!(&said[3], Said::Tool { id: Some(id), .. } if id == "item_2"));
    assert_eq!(said[4..], [Said::Text("done".into()), Said::Completed]);
    assert_eq!(
        said.len(),
        6,
        "a started command and reasoning are not calls"
    );
    let failed = r#"{"type":"turn.failed","error":{"message":"quota exceeded"}}"#;
    assert_eq!(read(Agent::Codex, failed), vec![Said::Failed]);
    let mcp = r#"{"type":"item.completed","item":{"id":"item_5","type":"mcp_tool_call","server":"neosian","tool":"memory","arguments":{"command":"view"},"result":{"content":[{"type":"text","text":"- /user"}]},"error":null,"status":"completed"}}"#;
    assert!(
        matches!(&read(Agent::Codex, mcp)[0], Said::Tool { name, input, .. } if name == "mcp__neosian__memory" && input == &json!({"command": "view"}))
    );
}

const OPENCODE: &[&str] = &[
    r#"{"type":"step_start","timestamp":1795221493011,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"id":"prt_1","sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","type":"step-start"}}"#,
    r#"{"type":"tool_use","timestamp":1795221494418,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"partID":"prt_2","type":"tool","id":"call-85f7","tool":"write","state":{"status":"completed","input":{"path":"probe8.txt","content":"ok"},"output":"Created file successfully: probe8.txt","title":"write"}}}"#,
    r#"{"type":"tool_use","timestamp":1795221494429,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"partID":"prt_3","type":"tool","id":"call-4c26","tool":"write","state":{"status":"error","input":{"path":"../outside-opencode.txt","content":"ok"},"error":"Permission denied: external_directory"}}}"#,
    r#"{"type":"step_finish","timestamp":1795221494430,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"step-finish","reason":"tool-calls"}}"#,
    r#"{"type":"step_start","timestamp":1795221497398,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"step-start"}}"#,
    r#"{"type":"text","timestamp":1795221497687,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"text","text":"Succeeded: created `probe8.txt`."}}"#,
    r#"{"type":"step_finish","timestamp":1795221497688,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"step-finish","reason":"stop","cost":0}}"#,
];

#[test]
fn opencode_tool_parts_steps_and_the_final_stop() {
    let said: Vec<Said> = all(Agent::OpenCode, OPENCODE)
        .into_iter()
        .filter(|said| !matches!(said, Said::Session(_)))
        .collect();
    assert_eq!(
        said,
        vec![
            Said::Step,
            Said::Tool {
                id: Some("call-85f7".into()),
                name: "write".into(),
                input: json!({"path": "probe8.txt", "content": "ok"}),
                response: json!("Created file successfully: probe8.txt"),
            },
            Said::Tool {
                id: Some("call-4c26".into()),
                name: "write".into(),
                input: json!({"path": "../outside-opencode.txt", "content": "ok"}),
                response: json!("Permission denied: external_directory"),
            },
            Said::Step,
            Said::Text("Succeeded: created `probe8.txt`.".into()),
            Said::Completed,
        ]
    );
    let running = r#"{"type":"tool_use","sessionID":"ses_a","part":{"tool":"read","state":{"status":"running","input":{}}}}"#;
    assert_eq!(
        read(Agent::OpenCode, running),
        vec![Said::Session("ses_a".into())]
    );
    let error = r#"{"type":"error","sessionID":"ses_a","error":{"type":"provider.quota","message":"Insufficient balance."}}"#;
    assert_eq!(read(Agent::OpenCode, error)[1], Said::Failed);
    assert_eq!(read(Agent::OpenCode, "not json"), vec![]);
}

pub(in crate::harness) fn claude() -> &'static [&'static str] {
    CLAUDE
}

pub(in crate::harness) fn codex() -> &'static [&'static str] {
    CODEX
}

pub(in crate::harness) fn opencode() -> &'static [&'static str] {
    OPENCODE
}

//! Plans and readings against the real harnesses' observed forms
//! (record/x2b-captures-2026-11-21): the lines here are copied from them.

use std::path::Path;

use serde_json::json;

use super::super::events::Event;
use super::*;

fn server() -> McpServer {
    McpServer {
        command: "/opt/neosian".to_string(),
        args: ["mcp", "--root", "/r", "--actor", "mcp:claude-code"]
            .map(String::from)
            .to_vec(),
        mounted: true,
    }
}

fn entry(value: serde_json::Value) -> Event {
    Event::Observed(value)
}

fn request<'a>(
    agent: Agent,
    mode: Mode,
    resume: Option<&'a str>,
    keys: &'a [CodexKey],
) -> Request<'a> {
    Request {
        agent,
        mode,
        model: None,
        directory: Path::new("/work/dir"),
        resume,
        new_session: "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe",
        private: Path::new("/tmp/neosian-harness-x"),
        codex_keys: keys,
        opencode_config: None,
        parent: None,
        server: None,
        codex_off: &[],
    }
}

fn words(plan: &Plan) -> Vec<&str> {
    plan.argv.iter().map(String::as_str).collect()
}

#[test]
fn claude_code_read_only_loads_only_user_settings_and_this_server() {
    let own = server();
    let mut start = request(Agent::ClaudeCode, Mode::ReadOnly, None, &[]);
    start.server = Some(&own);
    start.parent = Some("parent-1");
    let planned = plan(&start).unwrap();
    let config = json!({"mcpServers": {"neosian-memory": {
        "command": "/opt/neosian",
        "args": ["mcp", "--root", "/r", "--actor", "mcp:claude-code"],
    }}})
    .to_string();
    assert_eq!(
        words(&planned),
        [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--session-id",
            "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe",
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--mcp-config",
            config.as_str(),
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Grep,Glob",
            "--allowedTools",
            "mcp__neosian-memory",
            "--disallowedTools",
            "mcp__neosian-memory__harness",
            "mcp__neosian__harness",
        ]
    );
    let env = |key: &str| {
        planned
            .env
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value.as_str())
    };
    assert_eq!(env("NEOSIAN_HARNESS_CHILD"), Some("1"));
    assert_eq!(env("PWD"), Some("/work/dir"));
    assert_eq!(env("NEOSIAN_HARNESS_PARENT"), Some("parent-1"));
    assert_eq!(
        env("NEOSIAN_HARNESS_SESSION"),
        Some("5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe")
    );
    let mut write = request(Agent::ClaudeCode, Mode::Write, Some("s-1"), &[]);
    write.model = Some("opus");
    write.server = Some(&own);
    let resumed = plan(&write).unwrap();
    assert_eq!(
        words(&resumed)[5..13],
        [
            "--resume",
            "s-1",
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
            "--model",
            "opus"
        ]
    );
    for switch in [
        "--setting-sources",
        "--strict-mcp-config",
        "--mcp-config",
        "--allowedTools",
    ] {
        assert!(!resumed.argv.iter().any(|word| word == switch), "{switch}");
    }
    assert_eq!(resumed.argv.last().unwrap(), "mcp__neosian__harness");
    assert!(
        resumed
            .env
            .contains(&("NEOSIAN_HARNESS_SESSION".to_string(), "s-1".to_string()))
    );
}

#[test]
fn codex_always_passes_approval_never_and_denies_per_found_key() {
    let none = plan(&request(Agent::Codex, Mode::ReadOnly, None, &[])).unwrap();
    assert_eq!(
        words(&none),
        [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "-c",
            "approval_policy=\"never\"",
            "-C",
            "/work/dir",
            "-",
        ]
    );
    assert!(
        none.env
            .contains(&("TMPDIR".to_string(), "/tmp/neosian-harness-x".to_string()))
    );
    let keys = [CodexKey {
        key: "neosian-memory",
        disabled: vec!["upkeep_apply".to_string(), "say \"hi\"".to_string()],
    }];
    let resumed = plan(&request(Agent::Codex, Mode::Write, Some("01a1fb84"), &keys)).unwrap();
    let argv = words(&resumed);
    assert!(!argv.contains(&"--skip-git-repo-check"));
    assert_eq!(
        argv[3..11],
        [
            "-s",
            "workspace-write",
            "-c",
            "approval_policy=\"never\"",
            "-c",
            "sandbox_workspace_write.exclude_slash_tmp=true",
            "-c",
            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        ]
    );
    assert_eq!(
        argv[13..],
        [
            "-c",
            "mcp_servers.neosian-memory.env.NEOSIAN_HARNESS_CHILD=\"1\"",
            "-c",
            "mcp_servers.neosian-memory.disabled_tools=[\"upkeep_apply\",\"say \\\"hi\\\"\",\"harness\"]",
            "resume",
            "01a1fb84",
            "-",
        ]
    );
    let parsed: toml::Table = "x = [\"upkeep_apply\",\"say \\\"hi\\\"\",\"harness\"]"
        .parse()
        .unwrap();
    assert_eq!(
        parsed["x"].as_array().unwrap()[1].as_str(),
        Some("say \"hi\"")
    );
}

#[test]
fn codex_keys_come_from_mcp_get_answers() {
    let found = codex::keys(&[
        ("neosian-memory", r#"{"name":"neosian-memory","enabled":true,"disabled_tools":["harness"],"transport":{"type":"stdio"}}"#.to_string()),
        ("neosian", "not json".to_string()),
    ]);
    assert_eq!(
        found,
        [CodexKey {
            key: "neosian-memory",
            disabled: vec!["harness".to_string()]
        }]
    );
    let again = plan(&request(Agent::Codex, Mode::ReadOnly, None, &found)).unwrap();
    assert!(
        again
            .argv
            .contains(&"mcp_servers.neosian-memory.disabled_tools=[\"harness\"]".to_string())
    );
    assert_eq!(
        codex::disabled_tools(r#"{"disabled_tools":null}"#),
        Some(Vec::new())
    );
}

#[test]
fn opencode_runs_standalone_with_its_preset_agents_merged_in() {
    let mut start = request(Agent::OpenCode, Mode::Write, Some("ses_e0476f"), &[]);
    start.model = Some("opencode/deepseek-v4.1-flash-free");
    start.opencode_config = Some(r#"{"model":"x/y","agent":{"mine":{"mode":"primary"}}}"#);
    let planned = plan(&start).unwrap();
    assert_eq!(
        words(&planned),
        [
            "opencode",
            "run",
            "--standalone",
            "--format",
            "json",
            "--agent",
            "neosian-harness-write",
            "--model",
            "opencode/deepseek-v4.1-flash-free",
            "--session",
            "ses_e0476f",
        ]
    );
    let config: serde_json::Value = serde_json::from_str(&planned.env[0].1).unwrap();
    assert_eq!(config["model"], "x/y");
    assert_eq!(config["agent"]["mine"]["mode"], "primary");
    let write = &config["agent"]["neosian-harness-write"]["permission"];
    let order: Vec<&String> = write.as_object().unwrap().keys().collect();
    assert_eq!(
        order,
        [
            "*",
            "read",
            "glob",
            "grep",
            "list",
            "edit",
            "external_directory",
            "neosian-memory_harness",
            "neosian_harness"
        ]
    );
    assert_eq!(write["*"], "deny");
    assert_eq!(write["read"]["*.env"], "deny");
    assert!(planned.env.contains(&(
        "OPENCODE_DISABLE_PROJECT_CONFIG".to_string(),
        "1".to_string()
    )));
    let edit = write["edit"].as_object().unwrap();
    assert_eq!(edit["*"], "allow");
    for path in [
        ".opencode/*",
        "*/.opencode/*",
        "opencode.json",
        "opencode.jsonc",
        "*/opencode.json",
        "*/opencode.jsonc",
    ] {
        assert_eq!(edit[path], "deny", "{path}");
    }
    let mut ro = request(Agent::OpenCode, Mode::ReadOnly, None, &[]);
    ro.opencode_config = None;
    let ro_plan = plan(&ro).unwrap();
    assert!(ro_plan.env.contains(&(
        "OPENCODE_DISABLE_PROJECT_CONFIG".to_string(),
        "1".to_string()
    )));
    assert!(
        !ro_plan
            .env
            .iter()
            .any(|(key, _)| key == "NEOSIAN_HARNESS_SESSION")
    );
    let read_only = &config["agent"]["neosian-harness-read-only"]["permission"];
    assert!(read_only.get("edit").is_none() && read_only["neosian_harness"] == "deny");
    for bad in ["[1]", "not json", r#"{"agent":[]}"#] {
        start.opencode_config = Some(bad);
        assert_eq!(plan(&start), Err(Unplanned::OpenCodeConfig), "{bad}");
    }
    start.opencode_config = Some("  ");
    assert!(plan(&start).is_ok());
}

#[test]
fn claude_code_output_reads_as_observed() {
    let read = |line: &str| Agent::ClaudeCode.read(line);
    assert_eq!(
        read(
            r#"{"type":"system","subtype":"init","cwd":"/w","session_id":"5eab6b3f","tools":["Glob"]}"#
        ),
        [Event::Session("5eab6b3f".into())]
    );
    assert_eq!(
        read(r#"{"type":"system","subtype":"hook_started","session_id":"5eab6b3f"}"#),
        []
    );
    assert_eq!(
        read(
            r#"{"type":"assistant","message":{"content":[{"type":"thinking","thinking":""},{"type":"text","text":"ok\nnone"}]}}"#
        ),
        [Event::Text("ok\nnone".into())]
    );
    assert_eq!(
        read(
            r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t","name":"Write","input":{}}]}}"#
        ),
        [entry(json!({"kind": "tool", "name": "Write"}))]
    );
    assert_eq!(
        read(
            r#"{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"Permission for this tool use was denied.","is_error":true,"tool_use_id":"t"}]}}"#
        ),
        [entry(
            json!({"kind": "tool_error", "text": "Permission for this tool use was denied."})
        )]
    );
    assert_eq!(
        read(
            r#"{"type":"result","subtype":"success","is_error":false,"result":"done","permission_denials":[{"tool_name":"Bash","tool_use_id":"t","tool_input":{}}]}"#
        ),
        [
            entry(json!({"kind": "denied", "name": "Bash"})),
            Event::Answer("done".into())
        ]
    );
    assert_eq!(
        read(r#"{"type":"result","subtype":"error_during_execution","is_error":true}"#),
        [Event::Failure("error_during_execution".into())]
    );
    assert_eq!(read("not json"), []);
}

#[test]
fn codex_output_reads_as_observed() {
    let read = |line: &str| Agent::Codex.read(line);
    assert_eq!(
        read(r#"{"type":"thread.started","thread_id":"01a1fb82-bd86-7433-a9cd-a9e5ed3e5e67"}"#),
        [Event::Session(
            "01a1fb82-bd86-7433-a9cd-a9e5ed3e5e67".into()
        )]
    );
    assert_eq!(
        read(
            r#"{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"ok\nmcp__envprobe2__harness"}}"#
        ),
        [Event::Text("ok\nmcp__envprobe2__harness".into())]
    );
    assert_eq!(
        read(
            r#"{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc 'printf ok > probe6.txt'","aggregated_output":"","exit_code":0,"status":"completed"}}"#
        ),
        [entry(
            json!({"kind": "command", "id": "item_1", "command": "/bin/zsh -lc 'printf ok > probe6.txt'", "exit": 0})
        )]
    );
    // A running command is seen at once, named by the same id (x2c r1).
    assert_eq!(
        read(
            r#"{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc 'sleep 120'","aggregated_output":"","exit_code":null,"status":"in_progress"}}"#
        ),
        [entry(
            json!({"kind": "command_started", "id": "item_1", "command": "/bin/zsh -lc 'sleep 120'"})
        )]
    );
    assert_eq!(
        read(r#"{"type":"item.started","item":{"id":"item_0","type":"agent_message"}}"#),
        []
    );
    assert_eq!(
        read(r#"{"type":"turn.completed","usage":{"input_tokens":1}}"#),
        []
    );
    assert_eq!(
        read(r#"{"type":"turn.failed","error":{"message":"quota"}}"#),
        [Event::Failure("quota".into())]
    );
    assert_eq!(
        read(r#"{"type":"error","message":"Reconnecting... 1/5"}"#),
        [entry(
            json!({"kind": "error", "text": "Reconnecting... 1/5"})
        )]
    );
}

#[test]
fn opencode_output_reads_as_observed() {
    let read = |line: &str| Agent::OpenCode.read(line);
    let session = Event::Session("ses_e0476f6c7ffeNt1Y5fzK9bFTke".into());
    assert_eq!(
        read(
            r#"{"type":"step_start","timestamp":1,"sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"step-start"}}"#
        ),
        std::slice::from_ref(&session)
    );
    assert_eq!(
        read(
            r#"{"type":"text","sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"text","text":"ok\nenvprobe2_harness"}}"#
        ),
        [session.clone(), Event::Text("ok\nenvprobe2_harness".into())]
    );
    assert_eq!(
        read(
            r#"{"type":"tool_use","sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","part":{"type":"tool","tool":"write","state":{"status":"error","error":"Permission denied: external_directory"}}}"#
        ),
        [
            session.clone(),
            entry(
                json!({"kind": "tool", "name": "write", "status": "error", "error": "Permission denied: external_directory"})
            )
        ]
    );
    assert_eq!(
        read(
            r#"{"type":"error","sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","error":{"type":"provider.quota","message":"Insufficient balance.","status":402}}"#
        ),
        [session, Event::Failure("Insufficient balance.".into())]
    );
}

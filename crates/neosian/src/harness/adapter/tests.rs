//! Plans and readings against the real harnesses' observed forms
//! (record/x2b-captures-2026-11-21): the lines here are copied from them.

use std::path::Path;

use super::super::events::Event;
use super::*;

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
    }
}

fn words(plan: &Plan) -> Vec<&str> {
    plan.argv.iter().map(String::as_str).collect()
}

#[test]
fn claude_code_names_the_session_and_denies_harness_under_both_keys() {
    let start = plan(&request(Agent::ClaudeCode, Mode::ReadOnly, None, &[])).unwrap();
    assert_eq!(
        words(&start),
        [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--session-id",
            "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Grep,Glob",
            "--disallowedTools",
            "mcp__neosian-memory__harness",
            "mcp__neosian__harness",
        ]
    );
    assert_eq!(
        start.env,
        [("NEOSIAN_HARNESS_CHILD".to_string(), "1".to_string())]
    );
    let mut write = request(Agent::ClaudeCode, Mode::Write, Some("s-1"), &[]);
    write.model = Some("opus");
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
    assert_eq!(resumed.argv.last().unwrap(), "mcp__neosian__harness");
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
        [Event::Note("[tool Write]".into())]
    );
    assert_eq!(
        read(
            r#"{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"Permission for this tool use was denied.","is_error":true,"tool_use_id":"t"}]}}"#
        ),
        [Event::Note(
            "[tool error] Permission for this tool use was denied.".into()
        )]
    );
    assert_eq!(
        read(
            r#"{"type":"result","subtype":"success","is_error":false,"result":"done","permission_denials":[{"tool_name":"Bash","tool_use_id":"t","tool_input":{}}]}"#
        ),
        [
            Event::Note("[denied Bash]".into()),
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
        [Event::Note(
            "[$ /bin/zsh -lc 'printf ok > probe6.txt'] exit 0".into()
        )]
    );
    assert_eq!(
        read(r#"{"type":"item.started","item":{"id":"item_1","type":"command_execution"}}"#),
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
        [Event::Note("[error] Reconnecting... 1/5".into())]
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
            Event::Note("[tool write] error: Permission denied: external_directory".into())
        ]
    );
    assert_eq!(
        read(
            r#"{"type":"error","sessionID":"ses_e0476f6c7ffeNt1Y5fzK9bFTke","error":{"type":"provider.quota","message":"Insufficient balance.","status":402}}"#
        ),
        [session, Event::Failure("Insufficient balance.".into())]
    );
}

#[test]
fn inputs_are_held_to_their_grammar() {
    for good in [
        "opus",
        "opencode/deepseek-v4.1-flash-free",
        "gpt-5.5@high",
        "a#b+c:d",
    ] {
        assert!(model_ok(good), "{good}");
    }
    for bad in [
        "",
        "a b",
        "x;rm",
        "$(x)",
        "é",
        "-x",
        "--dangerously-skip-permissions",
        &"m".repeat(129),
    ] {
        assert!(!model_ok(bad), "{bad}");
    }
    assert!(session_ok("ses_e0476f6c7ffeNt1Y5fzK9bFTke") && session_ok("01a1fb84-1ba9"));
    for bad in ["", "-x", "--resume", "a b", "a/b", ".x"] {
        assert!(!session_ok(bad), "{bad}");
    }
    assert!(prompt_ok("x") && !prompt_ok("") && !prompt_ok(&"x".repeat(PROMPT_BYTES + 1)));
    let here = std::env::temp_dir();
    assert_eq!(directory(None, &here), here.canonicalize().ok());
    assert_eq!(directory(Some("no-such-directory-x2b"), &here), None);
    assert_eq!(Agent::parse("claude-code"), Some(Agent::ClaudeCode));
    assert_eq!(Agent::parse("claude"), None);
    assert_eq!(Agent::NAMES, Agent::ALL.map(Agent::name));
    assert_eq!(Mode::NAMES, [Mode::ReadOnly, Mode::Write].map(Mode::name));
}

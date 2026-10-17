//! `neosian mcp` through the literal binary: one JSON-RPC session on its
//! stdio over a planted home, the SDK's shapes answered.

mod support;

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

use serde_json::{Value, json};
use support::Scratch;

const DIR: &str = "user%3Ame/documents";

fn envelope(content: &str) -> String {
    format!(
        "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-10-12T22:27:56.053875Z'\nupdated_at: '2026-10-12T22:27:56.053875Z'\nactor: seed\n---\n{content}"
    )
}

/// Every request's answer, in order, over one session.
fn session(home: &Scratch, actor: &str, messages: &[Value]) -> Vec<Value> {
    let mut server = Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args(["mcp", "--scope", "user:me", "--actor", actor, "--root"])
        .arg(home.path())
        .env_remove("NEOSIAN_SCOPE")
        .env_remove("NEOSIAN_POSTGRES_DSN")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary runs");
    let mut stdin = server.stdin.take().unwrap();
    let mut stdout = BufReader::new(server.stdout.take().unwrap());
    let mut answers = Vec::new();
    for message in messages {
        writeln!(stdin, "{message}").unwrap();
        stdin.flush().unwrap();
        if message.get("id").is_some() {
            let mut line = String::new();
            stdout.read_line(&mut line).unwrap();
            answers.push(serde_json::from_str(&line).unwrap());
        }
    }
    drop(stdin);
    let output = server.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        output.stderr.is_empty(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    answers
}

fn call(id: u64, name: &str, arguments: Value) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
}

fn text(answer: &Value) -> &str {
    answer["result"]["content"][0]["text"].as_str().unwrap()
}

#[test]
fn the_state_set_is_served_over_stdio() {
    let home = Scratch::new("mcp-session");
    home.plant(
        &format!("{DIR}/skills/deploy.md"),
        &envelope("---\ndescription: Deploy it.\n---\n\nRun the script.\n"),
    );
    let initialize = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}});
    let answers = session(
        &home,
        "mcp:test",
        &[
            json!({"jsonrpc": "2.0", "id": 0, "method": "tools/list"}),
            initialize,
            json!({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json!({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"}),
            call(
                4,
                "memory",
                json!({"command": "create", "path": "/memories/notes", "content": "é first"}),
            ),
            call(
                5,
                "memory",
                json!({"command": "view", "path": "/memories/notes"}),
            ),
            call(6, "load_skill", json!({"name": "deploy"})),
            call(7, "recall_turn", json!({"turn": "x"})),
            call(
                8,
                "messages",
                json!({"command": "send", "scope": "/memories", "body": "later", "actionable": true}),
            ),
            call(9, "messages", json!({"command": "list"})),
            json!({"jsonrpc": "2.0", "id": 10, "method": "prompts/get", "params": {"name": "nope"}}),
            json!({"jsonrpc": "2.0", "id": 11, "method": "logging/setLevel", "params": {"level": "info"}}),
        ],
    );
    assert_eq!(answers[0]["error"]["code"], -32602);
    let opened = &answers[1]["result"];
    assert_eq!(opened["serverInfo"]["name"], "neosian-memory");
    assert_eq!(opened["protocolVersion"], "2025-06-18");
    assert!(
        opened["instructions"]
            .as_str()
            .unwrap()
            .starts_with("## Memory\n")
    );
    assert!(
        opened["instructions"]
            .as_str()
            .unwrap()
            .contains("- /memories/skills/deploy")
    );
    let names: Vec<&str> = answers[2]["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        names,
        [
            "memory",
            "list_skills",
            "load_skill",
            "messages",
            "recall_turn",
            "search_history",
            "continue_session",
            "handoff"
        ]
    );
    assert_eq!(
        answers[3]["result"]["prompts"],
        json!([{"description": "Deploy it.", "name": "deploy"}, {"description": "Hand this session off, so that the next session in any agent continues it; write the note, then stop", "name": "handoff"}])
    );
    assert_eq!(text(&answers[4]), "Created /memories/notes (v1)");
    assert_eq!(text(&answers[5]), "/memories/notes (v1):\n1: é first");
    assert_eq!(text(&answers[6]), "Run the script.");
    assert_eq!(
        answers[6]["result"]["content"][1]["text"],
        "/memories/skills/deploy (version 1)"
    );
    assert_eq!(answers[7]["result"]["isError"], true);
    assert_eq!(
        text(&answers[7]),
        "Invalid arguments for tool 'recall_turn': turn: Input should be a valid integer, unable to parse string as an integer; conversation: Field required"
    );
    let sent: Value = serde_json::from_str(text(&answers[8])).unwrap();
    assert_eq!(
        (
            sent["version"].as_u64(),
            sent["actor"].as_str(),
            sent["actionable"].as_bool()
        ),
        (Some(1), Some("mcp:test"), Some(true))
    );
    let listed: Value = serde_json::from_str(text(&answers[9])).unwrap();
    assert_eq!(listed["items"][0]["id"], sent["id"]);
    assert_eq!(
        answers[10]["error"],
        json!({"code": -32602, "message": "Skill 'nope' not found"})
    );
    assert_eq!(answers[11]["error"]["code"], -32601);
    let document = std::fs::read_to_string(home.path().join(DIR).join("notes.md")).unwrap();
    assert!(document.ends_with("actor: mcp:test\n---\né first"));
}

#[test]
fn a_hooked_client_gets_the_discipline_alone_and_install_names_python() {
    let home = Scratch::new("mcp-hooked");
    let initialize = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}});
    let answers = session(&home, "mcp:claude-code", &[initialize]);
    let instructions = answers[0]["result"]["instructions"].as_str().unwrap();
    assert!(instructions.starts_with("Your persistent memory is the `memory` tool"));
    assert!(!instructions.contains("## /memories"));
    assert_eq!(answers[0]["result"]["protocolVersion"], "2024-11-05");
    let refused = Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args(["mcp", "install", "--client", "claude-code"])
        .output()
        .unwrap();
    assert_eq!(refused.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&refused.stderr).contains("uv tool install neosian"));
    let usage = Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args(["mcp", "--scope", "nope"])
        .output()
        .unwrap();
    assert_eq!(usage.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&usage.stderr).contains("[memory_scope_invalid]"));
}

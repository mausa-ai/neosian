//! `neosian continue` through the literal binary, over a home whose
//! sessions, baton and turn logs are planted as the Python side writes
//! them.

mod support;

use std::process::Command;

use support::{Scratch, text};

const SCOPE: &str = "user:me/proj:demo";
const DIR: &str = "user%3Ame/proj%3Ademo/documents";
const TURNS: &str = concat!(
    r#"{"neosian_format":1,"turn":1,"created_at":"2026-10-12T22:27:56.052833Z","messages":[{"role":"user","content":"turn 1: release the train","reasoning":null,"tool_calls":[],"tool_call_id":null},{"role":"assistant","content":null,"reasoning":null,"tool_calls":[{"id":"t1","name":"Read","arguments":{"file_path":"/work/demo/file0.py"}}],"tool_call_id":null},{"role":"tool","content":"the whole file","reasoning":null,"tool_calls":[],"tool_call_id":"t1"},{"role":"assistant","content":"done with turn 1","reasoning":null,"tool_calls":[],"tool_call_id":null}],"actor":"claude-code:s1"}"#,
    "\n",
    r#"{"neosian_format":1,"turn":2,"created_at":"2026-10-12T22:30:00Z","messages":[{"role":"user","content":"turn 2","reasoning":null,"tool_calls":[],"tool_call_id":null},{"role":"assistant","content":"done with turn 2","reasoning":null,"tool_calls":[],"tool_call_id":null}],"actor":"claude-code:s1"}"#,
    "\n",
);

fn envelope(version: u64, updated: &str, content: &str) -> String {
    format!(
        "---\nneosian_format: 1\nversion: {version}\ncreated_at: '2026-10-12T22:27:56.053875Z'\nupdated_at: '{updated}'\nactor: claude-code:s1\n---\n{content}"
    )
}

fn session(id: &str, updated: &str) -> String {
    envelope(
        3,
        updated,
        &format!(
            "# claude-code session {id}\n\n- agent: claude-code\n- conversation: {id}\n- started: 2026-10-12T22:27:56.052833Z\n- last prompt: turn 2\n- turns: 2\n- continues: s0\n"
        ),
    )
}

fn baton(conversation: Option<&str>, status: &str) -> String {
    let link = conversation.map_or(String::new(), |id| format!("- conversation: {id}\n"));
    envelope(
        1,
        "2026-10-12T22:31:00Z",
        &format!(
            "# handoff from claude-code:s1\n\n- from: claude-code:s1\n- written: 2026-10-12T22:31:00.5Z\n- status: {status}\n{link}\nPick up the release.\n"
        ),
    )
}

fn planted(name: &str) -> Scratch {
    let home = Scratch::new(name);
    home.plant("conversations/s1/turns.jsonl", TURNS);
    home.plant(
        "conversations/s2/turns.jsonl",
        &TURNS.replace("done with", "finished"),
    );
    home.plant(
        &format!("{DIR}/sessions/s1.md"),
        &session("s1", "2026-10-12T22:30:00Z"),
    );
    home.plant(
        &format!("{DIR}/sessions/s2.md"),
        &session("s2", "2026-10-12T22:32:00Z"),
    );
    home
}

fn run(home: &Scratch, args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_neosian"))
        .arg("continue")
        .args(args)
        .args(["--root"])
        .arg(home.path())
        .args(["--scope", SCOPE])
        .env_remove("NEOSIAN_SCOPE")
        .env_remove("NEOSIAN_POSTGRES_DSN")
        .output()
        .expect("the binary runs")
}

#[test]
fn a_named_conversation_is_delivered_whole() {
    let home = planted("bin-continue");
    let delivered = run(&home, &["s1"]);
    assert_eq!(delivered.status.code(), Some(0));
    assert_eq!(
        text(delivered.stdout),
        "[continuing conversation s1 — written by claude-code:s1, 2 turns, 2026-10-12T22:27Z to \
         2026-10-12T22:30Z, itself continuing s0; every user prompt and final answer verbatim, \
         tool rounds one line each; recall_turn(n, conversation=\"s1\") re-reads any turn whole]\n\
         [1] USER: turn 1: release the train\n\
         TOOL Read({\"file_path\":\"/work/demo/file0.py\"}) → the whole file\n\
         AGENT: done with turn 1\n\
         [2] USER: turn 2\n\
         AGENT: done with turn 2\n\
         [end — you are continuing this conversation: carry on from its last answer as the same \
         work, in the same voice; the user's next message is its next turn]\n"
    );
}

#[test]
fn a_bare_call_means_the_newest_session_or_the_linked_note() {
    let home = planted("bin-continue-bare");
    let newest = run(&home, &["--json"]);
    let answer: serde_json::Value = serde_json::from_slice(&newest.stdout).unwrap();
    assert_eq!(
        (answer["conversation"].as_str(), answer["note"].as_bool()),
        (Some("s2"), Some(false))
    );
    assert_eq!(
        (answer["scope"].as_str(), answer["client"].is_null()),
        (Some(SCOPE), true)
    );
    assert!(
        answer["text"]
            .as_str()
            .unwrap()
            .contains("[1] USER: turn 1")
    );
    home.plant(&format!("{DIR}/handoff.md"), &baton(Some("s1"), "pending"));
    let linked = run(&home, &[]);
    let delivered = text(linked.stdout);
    assert!(
        delivered.starts_with("[continuing conversation s1 —"),
        "{delivered}"
    );
    assert!(delivered.contains(
        "[its handoff note, written 2026-10-12T22:31Z by claude-code:s1]\nPick up the release.\n[end of note]\n[1] USER:"
    ));
    // The read leaves the baton as it was.
    assert_eq!(
        std::fs::read_to_string(home.path().join(format!("{DIR}/handoff.md"))).unwrap(),
        baton(Some("s1"), "pending")
    );
    // Named, the note rides only with its own session.
    let other = run(&home, &["s2"]);
    assert!(!text(other.stdout).contains("handoff note"));
    // Picked up, it rides with nothing.
    home.plant(
        &format!("{DIR}/handoff.md"),
        &baton(Some("s1"), "picked up 2026-10-12T22:40:00Z by mcp:codex"),
    );
    assert!(
        text(run(&home, &["s1"]).stdout)
            .lines()
            .nth(1)
            .unwrap()
            .starts_with("[1] USER:")
    );
}

#[test]
fn an_unlinked_note_means_the_session_after_it() {
    let home = planted("bin-continue-unlinked");
    home.plant(&format!("{DIR}/handoff.md"), &baton(None, "pending"));
    let after = run(&home, &[]);
    assert!(text(after.stdout).starts_with("[continuing conversation s2 —"));
    home.plant(
        &format!("{DIR}/sessions/s2.md"),
        &session("s2", "2026-10-12T22:30:30Z"),
    );
    let early = run(&home, &["--json"]);
    assert_eq!(early.status.code(), Some(1));
    let answer: serde_json::Value = serde_json::from_slice(&early.stdout).unwrap();
    assert_eq!(
        answer["error"],
        "The handoff note's session has not landed yet"
    );
    assert_eq!(
        answer["hint"],
        "Name the conversation, or retry in a moment."
    );
    assert_eq!(
        text(early.stderr),
        "error: The handoff note's session has not landed yet\nhint: Name the conversation, or retry in a moment.\n"
    );
}

#[test]
fn nothing_to_continue_and_no_turns_are_tier_one() {
    let home = Scratch::new("bin-continue-empty");
    let nothing = run(&home, &[]);
    assert_eq!(nothing.status.code(), Some(1));
    assert_eq!(
        text(nothing.stderr),
        "error: Nothing to continue: no session is listed in this scope\n\
         hint: Name a conversation id to continue one anyway.\n"
    );
    let unknown = run(&home, &["nope"]);
    assert_eq!(unknown.status.code(), Some(1));
    assert_eq!(
        text(unknown.stderr),
        "error: Conversation 'nope' has no turns\n"
    );
    let refused = run(&home, &["a/b", "--json"]);
    assert_eq!(refused.status.code(), Some(2));
    let answer: serde_json::Value = serde_json::from_slice(&refused.stdout).unwrap();
    assert_eq!(answer["error"], "usage");
    assert_eq!(
        answer["hint"],
        "conversation 'a/b': must match [A-Za-z0-9_.-]{1,128} — one flat segment"
    );
    let scope = Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args(["continue", "--scope", "bad", "--root"])
        .arg(home.path().join("unbuilt"))
        .output()
        .unwrap();
    assert_eq!(scope.status.code(), Some(2));
    assert_eq!(
        text(scope.stderr),
        "error: --scope: does not match <type>:<id>[/...]\n"
    );
    assert!(!home.path().join("unbuilt").exists());
}

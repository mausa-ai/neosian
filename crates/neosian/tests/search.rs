//! `neosian search` through the literal binary, over a home whose turn
//! logs are planted as the Python side writes them.

mod support;

use std::process::Command;

use support::{Scratch, text};

const CONVERSATIONS: &str = "conversations";
const EARLY: &str = concat!(
    r#"{"neosian_format":1,"turn":1,"created_at":"2026-10-12T22:27:56.052833Z","messages":[{"role":"user","content":"the cache TTL is eight hours","reasoning":null,"tool_calls":[],"tool_call_id":null},{"role":"assistant","content":"noted","reasoning":null,"tool_calls":[],"tool_call_id":null}],"actor":"claude-code:early"}"#,
    "\n",
    r#"{"neosian_format":1,"turn":2,"created_at":"2026-10-12T22:27:56.057178Z","messages":[{"role":"user","content":"and the \"deploy\" target?","reasoning":null,"tool_calls":[],"tool_call_id":null},{"role":"assistant","content":null,"reasoning":null,"tool_calls":[{"id":"t1","name":"Read","arguments":{"file_path":"/work/demo/deploy.md"}}],"tool_call_id":null},{"role":"tool","content":"eu-west-1","reasoning":null,"tool_calls":[],"tool_call_id":"t1"},{"role":"assistant","content":"eu-west-1","reasoning":null,"tool_calls":[],"tool_call_id":null}],"actor":"claude-code:early"}"#,
    "\n",
);
const LATE: &str = concat!(
    r#"{"neosian_format":1,"turn":1,"created_at":"2026-10-12T22:28:00Z","messages":[{"role":"user","content":"ttl again: caf"#,
    "\\",
    r#"u00e9","reasoning":null,"tool_calls":[],"tool_call_id":null}],"actor":null}"#,
    "\n",
    r#"{"neosian_format":1,"turn":2,"created_at":"2026-10-12T22:28:01Z","messages":[],"actor":"cli:local","redacted":true}"#,
    "\n",
);

fn search(home: &Scratch, args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_neosian"))
        .arg("search")
        .args(args)
        .arg("--root")
        .arg(home.path())
        .env_remove("NEOSIAN_POSTGRES_DSN")
        .output()
        .expect("the binary runs")
}

fn planted(name: &str) -> Scratch {
    let home = Scratch::new(name);
    home.plant(&format!("{CONVERSATIONS}/early/turns.jsonl"), EARLY);
    home.plant(&format!("{CONVERSATIONS}/late/turns.jsonl"), LATE);
    home.plant(&format!("{CONVERSATIONS}/not an id/turns.jsonl"), EARLY);
    home
}

#[test]
fn hits_come_newest_first_one_line_each() {
    let home = planted("bin-search");
    let found = search(&home, &["TTL"]);
    assert_eq!(found.status.code(), Some(0));
    assert_eq!(
        text(found.stdout),
        "[late #1] 2026-10-12T22:28:00Z -  ttl again: café\n\
         [early #1] 2026-10-12T22:27:56.052833Z claude-code:early  the cache TTL is eight hours\n"
    );
    let narrowed = search(&home, &["ttl", "--conversation", "early", "--limit", "1"]);
    assert!(text(narrowed.stdout).starts_with("[early #1]"));
    let argument = search(&home, &["deploy.md", "eu-west-1"]);
    assert_eq!(
        text(argument.stdout),
        "[early #2] 2026-10-12T22:27:56.057178Z claude-code:early  Read {\"file_path\":\"/work/demo/deploy.md\"}\n"
    );
    let none = search(&home, &["deploy", "zzz"]);
    assert_eq!(
        (none.status.code(), text(none.stdout).as_str()),
        (Some(0), "no turn matches every term of 'deploy zzz'\n")
    );
}

#[test]
fn json_is_one_envelope() {
    let home = planted("bin-search-json");
    let found = search(&home, &["café", "--json"]);
    let answer: serde_json::Value = serde_json::from_slice(&found.stdout).unwrap();
    assert_eq!(answer["query"], "café");
    assert_eq!(answer["conversations"], serde_json::Value::Null);
    assert_eq!(
        (answer["limit"].as_i64(), answer["client"].is_null()),
        (Some(20), true)
    );
    assert_eq!(answer["hits"].as_array().unwrap().len(), 1);
    assert_eq!(answer["hits"][0]["snippet"], "ttl again: café");
    assert_eq!(answer["hits"][0]["actor"], serde_json::Value::Null);
}

#[test]
fn a_malformed_candidate_row_is_refused_at_tier_one() {
    let home = planted("bin-search-malformed");
    home.plant(
        &format!("{CONVERSATIONS}/broken/turns.jsonl"),
        "{\"turn\": ttl}\n",
    );
    let refused = search(&home, &["ttl", "--json"]);
    assert_eq!(refused.status.code(), Some(1));
    let answer: serde_json::Value = serde_json::from_slice(&refused.stdout).unwrap();
    assert_eq!(
        answer["error"],
        "[agent_conversation_format_unsupported] Unsupported turn format in conversation 'broken': malformed turn-log line 1"
    );
    assert!(text(refused.stderr).starts_with("error: [agent_conversation_format_unsupported]"));
    // A line that cannot hold a prefilterable term is never decoded; a
    // term the file could not hold verbatim makes every line a candidate.
    let unseen = search(&home, &["eight"]);
    assert_eq!(unseen.status.code(), Some(0));
    assert!(text(unseen.stdout).starts_with("[early #1]"));
    assert_eq!(search(&home, &["café"]).status.code(), Some(1));
}

#[test]
fn a_wrong_invocation_exits_two_and_builds_nothing() {
    let home = Scratch::new("bin-search-usage");
    let root = home.path().join("unbuilt");
    let unbuilt = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_neosian"))
            .arg("search")
            .args(args)
            .arg("--root")
            .arg(&root)
            .output()
            .unwrap()
    };
    for (args, message) in [
        (vec!["x", "--limit", "0"], "--limit must be 1 to 500, got 0"),
        (
            vec!["x", "--limit", "501"],
            "--limit must be 1 to 500, got 501",
        ),
        (vec!["  "], "query must contain at least one term"),
        (
            vec!["x", "--conversation", "a/b"],
            "--conversation 'a/b': must match [A-Za-z0-9_.-]{1,128} — one flat segment",
        ),
    ] {
        let refused = unbuilt(&args);
        assert_eq!(refused.status.code(), Some(2), "{args:?}");
        assert_eq!(text(refused.stderr), format!("error: {message}\n"));
    }
    let json = unbuilt(&["x", "--limit", "-1", "--json"]);
    let answer: serde_json::Value = serde_json::from_slice(&json.stdout).unwrap();
    assert_eq!(
        answer,
        serde_json::json!({"error": "usage", "hint": "--limit must be 1 to 500, got -1"})
    );
    assert!(!root.exists());
}

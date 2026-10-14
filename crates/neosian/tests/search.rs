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

const MESSAGE_ID: &str = "6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f";
/// One message about `early` turn 1, as the mailbox writes it: version 1
/// open, version 2 done with an outcome; the document is the second.
fn message(status: &str, outcome: &str) -> String {
    format!(
        "{{\n  \"format\": 1,\n  \"id\": \"{MESSAGE_ID}\",\n  \"scope\": \"user:me\",\n  \"conversation\": null,\n  \"turn\": null,\n  \"source\": null,\n  \"about\": \"early\",\n  \"about_turn\": 1,\n  \"actor\": \"cli:local\",\n  \"body\": \"the cache ttl question\",\n  \"created_at\": \"2026-10-12T22:29:00Z\",\n  \"due_at\": \"2026-10-12T22:29:00Z\",\n  \"delivery\": \"next_activity\",\n  \"actionable\": false,\n  \"occurrence\": 1,\n  \"status\": \"{status}\",\n  \"read_by\": null,\n  \"read_at\": null,\n  \"owner\": null,\n  \"claim_token\": null,\n  \"claim_until\": null,\n  \"outcome\": {outcome},\n  \"wake_owner\": null,\n  \"wake_token\": null,\n  \"wake_until\": null,\n  \"wake_accepted\": false\n}}\n"
    )
}
const ANNOTATION: &str = "[6f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f occurrence=1 scope=user:me from=cli:local at=2026-10-12T22:29:00+00:00 status=done actionable=False]\nthe cache ttl question\nLatest outcome: fixed in deploy";

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

/// The turns, and the message about `early` under the `user:me` scope.
fn with_message(name: &str) -> Scratch {
    let home = planted(name);
    let (open, done) = (
        message("open", "null"),
        message("done", "\"fixed in deploy\""),
    );
    home.plant(
        &format!("user%3Ame/documents/messages/{MESSAGE_ID}.md"),
        &format!(
            "---\nneosian_format: 1\nversion: 2\ncreated_at: '2026-10-12T22:29:00Z'\nupdated_at: '2026-10-12T22:30:00Z'\nactor: cli:local\n---\n{done}"
        ),
    );
    let row = |version: u64, action: &str, content: &str, at: &str| {
        serde_json::json!({
            "neosian_format": 1, "path": format!("messages/{MESSAGE_ID}"), "version": version,
            "action": action, "content": content, "actor": "cli:local", "created_at": at,
            "redacted": false,
        })
        .to_string()
    };
    home.plant(
        &format!("user%3Ame/versions/messages/{MESSAGE_ID}.jsonl"),
        &format!(
            "{}\n{}\n",
            row(1, "created", &open, "2026-10-12T22:29:00Z"),
            row(2, "modified", &done, "2026-10-12T22:30:00Z")
        ),
    );
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

#[test]
fn a_message_is_a_hit_of_its_own_and_annotates_the_turn_it_is_about() {
    let home = with_message("bin-search-message");
    let found = search(&home, &["ttl"]);
    assert_eq!(found.status.code(), Some(0));
    assert_eq!(
        text(found.stdout),
        format!(
            "{ANNOTATION}\n  the cache ttl question\n\
             [late #1] 2026-10-12T22:28:00Z -  ttl again: café\n\
             [early #1] 2026-10-12T22:27:56.052833Z claude-code:early  the cache TTL is eight hours\n\
             {ANNOTATION}\n"
        )
    );
    let outcome = search(&home, &["deploy", "--limit", "1"]);
    assert_eq!(
        text(outcome.stdout),
        format!("{ANNOTATION}\n  fixed in deploy\n")
    );
    let narrowed = search(&home, &["ttl", "--conversation", "late"]);
    assert_eq!(
        text(narrowed.stdout),
        "[late #1] 2026-10-12T22:28:00Z -  ttl again: café\n"
    );
    let envelope: serde_json::Value =
        serde_json::from_slice(&search(&home, &["ttl", "--json"]).stdout).unwrap();
    let hits = envelope["hits"].as_array().unwrap();
    assert_eq!(hits.len(), 3);
    assert_eq!(hits[0]["type"], "message");
    assert_eq!(hits[0]["conversation_id"], "early");
    assert_eq!(hits[0]["turn"], 1);
    assert_eq!(hits[0]["created_at"], "2026-10-12T22:29:00Z");
    assert_eq!(hits[0]["annotation"], ANNOTATION);
    assert_eq!(hits[1]["conversation_id"], "late");
    assert!(hits[1].get("annotations").is_none());
    assert_eq!(hits[2]["annotations"], serde_json::json!([ANNOTATION]));
}

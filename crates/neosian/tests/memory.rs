//! `neosian memory` through the literal binary, over a home planted as the
//! Python side writes it.

mod support;

use std::process::Command;

use support::{Scratch, memory, text};

const DIR: &str = "user%3Ame";
const FACTS: &str = "---\nneosian_format: 1\nversion: 2\ncreated_at: '2026-10-10T18:20:18.273843Z'\nupdated_at: '2026-10-10T18:20:18.276022Z'\nactor: seed\n---\n# Facts\n\n- deploy target: eu-west-1 (v1)\n";
const ROWS: &str = concat!(
    r##"{"neosian_format":1,"path":"facts","version":1,"action":"created","content":"# Facts\n","actor":"seed","created_at":"2026-10-10T18:20:18.273843Z","redacted":false}"##,
    "\n",
    r##"{"neosian_format":1,"path":"facts","version":2,"action":"modified","content":"# Facts\n\n- deploy target: eu-west-1 (v1)\n","actor":null,"created_at":"2026-10-10T18:20:18.276022Z","redacted":false}"##,
    "\n",
);

fn planted(name: &str) -> Scratch {
    let home = Scratch::new(name);
    home.plant(&format!("{DIR}/documents/facts.md"), FACTS);
    home.plant(&format!("{DIR}/documents/notes/reading.md"), FACTS);
    home.plant(&format!("{DIR}/versions/facts.jsonl"), ROWS);
    home
}

#[test]
fn view_prints_the_index_a_listing_and_a_numbered_document() {
    let home = planted("bin-view");
    let index = memory(home.path(), &["view"]);
    assert_eq!(index.status.code(), Some(0));
    assert_eq!(
        text(index.stdout),
        "## /memories\n- /memories/facts\n- /memories/notes/reading\n"
    );
    let listing = memory(home.path(), &["view", "/memories/notes"]);
    assert_eq!(text(listing.stdout), "- /memories/notes/reading\n");
    let document = memory(
        home.path(),
        &["view", "/memories/facts", "--view-range", "3", "-1"],
    );
    assert_eq!(
        text(document.stdout),
        "/memories/facts (v2):\n3: - deploy target: eu-west-1 (v1)\n4: \n"
    );
    assert!(document.stderr.is_empty());
}

#[test]
fn a_failed_command_exits_one_with_its_guidance_on_stderr() {
    let home = planted("bin-fail");
    let missing = memory(home.path(), &["view", "/memories/nope"]);
    assert_eq!(missing.status.code(), Some(1));
    assert!(missing.stdout.is_empty());
    assert_eq!(
        text(missing.stderr),
        "error: No document or directory at /memories/nope\n\
         hint: Run the memory tool with command 'view' and path '/' first.\n"
    );
    let envelope = memory(home.path(), &["view", "/elsewhere/x", "--json"]);
    assert_eq!(envelope.status.code(), Some(1));
    let answer: serde_json::Value = serde_json::from_slice(&envelope.stdout).unwrap();
    assert_eq!(answer["success"], false);
    assert_eq!(
        answer["error"],
        "[memory_path_invalid] Invalid memory document path '/elsewhere/x': no mount named \
         'elsewhere' (available mounts: /memories)"
    );
    assert!(
        answer["system_reminder"]
            .as_str()
            .unwrap()
            .starts_with("Paths look like")
    );
}

#[test]
fn versions_is_the_audit_trail_and_under_json_the_content_too() {
    let home = planted("bin-versions");
    let trail = memory(home.path(), &["versions", "/memories/facts"]);
    assert_eq!(
        text(trail.stdout),
        "v2  modified  -  2026-10-10T18:20:18.276022+00:00\n\
         v1  created  seed  2026-10-10T18:20:18.273843+00:00\n"
    );
    let rows = memory(
        home.path(),
        &["versions", "/memories/facts", "--limit", "1", "--json"],
    );
    let answer: serde_json::Value = serde_json::from_slice(&rows.stdout).unwrap();
    assert_eq!(answer["path"], "/memories/facts");
    assert_eq!(answer["versions"].as_array().unwrap().len(), 1);
    assert_eq!(
        answer["versions"][0]["content"],
        "# Facts\n\n- deploy target: eu-west-1 (v1)\n"
    );
    let none = memory(home.path(), &["versions", "/memories/never"]);
    assert_eq!(
        (none.status.code(), text(none.stdout).as_str()),
        (Some(0), "no history for /memories/never\n")
    );
}

#[test]
fn a_wrong_invocation_exits_two_and_builds_nothing() {
    let home = Scratch::new("bin-usage");
    let root = home.path().join("unbuilt");
    let refused = memory(
        &root,
        &["versions", "/memories/facts", "--limit", "0", "--json"],
    );
    assert_eq!(refused.status.code(), Some(2));
    let answer: serde_json::Value = serde_json::from_slice(&refused.stdout).unwrap();
    assert_eq!(
        answer,
        serde_json::json!({"error": "usage", "hint": "--limit must be at least 1"})
    );
    assert_eq!(text(refused.stderr), "error: --limit must be at least 1\n");
    let grammar = memory(&root, &["view", "--bogus", "--json"]);
    assert_eq!(grammar.status.code(), Some(2));
    let answer: serde_json::Value = serde_json::from_slice(&grammar.stdout).unwrap();
    assert_eq!(answer["error"], "usage");
    let scope = Command::new(env!("CARGO_BIN_EXE_neosian"))
        .args([
            "memory",
            "view",
            "/",
            "--mount",
            "scope=bad,path=user",
            "--root",
        ])
        .arg(&root)
        .output()
        .unwrap();
    assert_eq!(scope.status.code(), Some(2));
    assert_eq!(
        text(scope.stderr),
        "error: [memory_scope_invalid] Invalid memory scope 'bad': does not match \
         <type>:<id>[/...]\n"
    );
    assert!(!root.exists());
}

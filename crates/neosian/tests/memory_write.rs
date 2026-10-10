//! The writing half of `neosian memory` through the literal binary: the
//! six commands, a payload from stdin, the operator verbs.

mod support;

use std::io::Write;
use std::process::Stdio;

use support::{Scratch, command, memory, text};

#[test]
fn a_history_the_python_side_began_is_continued() {
    let home = Scratch::new("bin-continue");
    let stamp = "2026-10-10T18:20:18.273843Z";
    home.plant(
        "user%3Ame/documents/facts.md",
        &format!(
            "---\nneosian_format: 1\nversion: 2\ncreated_at: '{stamp}'\nupdated_at: '2026-10-10T18:20:18.276022Z'\nactor: seed\ncustom: kept\n---\n# Facts\n\n- eu-west-1\n"
        ),
    );
    home.plant(
        "user%3Ame/versions/facts.jsonl",
        &format!(
            "{{\"neosian_format\":1,\"path\":\"facts\",\"version\":1,\"action\":\"created\",\"content\":\"# Facts\\n\",\"actor\":\"seed\",\"created_at\":\"{stamp}\",\"redacted\":false}}\n\
             {{\"neosian_format\":1,\"path\":\"facts\",\"version\":2,\"action\":\"modified\",\"content\":\"# Facts\\n\\n- eu-west-1\\n\",\"actor\":\"seed\",\"created_at\":\"2026-10-10T18:20:18.276022Z\",\"redacted\":false}}\n"
        ),
    );
    let edit = [
        "str_replace",
        "/memories/facts",
        "--old-str",
        "west-1",
        "--new-str",
        "west-2",
    ];
    assert_eq!(
        text(memory(home.path(), &edit).stdout),
        "Edited /memories/facts (v3)\n"
    );
    let written =
        std::fs::read_to_string(home.path().join("user%3Ame/documents/facts.md")).unwrap();
    assert!(written.starts_with(&format!(
        "---\nneosian_format: 1\nversion: 3\ncreated_at: '{stamp}'\n"
    )));
    assert!(written.ends_with("\nactor: cli:local\ncustom: kept\n---\n# Facts\n\n- eu-west-2\n"));
    let trail = text(memory(home.path(), &["versions", "/memories/facts"]).stdout);
    let rows: Vec<&str> = trail.lines().collect();
    assert_eq!(rows.len(), 3);
    assert!(rows[0].starts_with("v3  modified  cli:local  "));
    assert_eq!(
        rows[2],
        "v1  created  seed  2026-10-10T18:20:18.273843+00:00"
    );
}

#[test]
fn the_six_commands_write_through_the_binary() {
    let home = Scratch::new("bin-write");
    let run = |args: &[&str]| {
        let output = memory(home.path(), args);
        (
            output.status.code(),
            text(output.stdout),
            text(output.stderr),
        )
    };
    let said = |args: &[&str]| run(args).1;
    assert_eq!(
        run(&["create", "/memories/todo", "--content", "- one\n- two\n"]),
        (
            Some(0),
            "Created /memories/todo (v1)\n".to_string(),
            String::new()
        )
    );
    assert_eq!(
        run(&["create", "/memories/todo", "--content", "- one\n- two\n"]),
        (
            Some(0),
            "Created /memories/todo (v2)\n".to_string(),
            "hint: Overwrote an existing document (was v1); prefer str_replace for edits.\n"
                .to_string()
        )
    );
    let edit = [
        "str_replace",
        "/memories/todo",
        "--old-str",
        "two",
        "--new-str",
        "three",
    ];
    assert_eq!(said(&edit), "Edited /memories/todo (v3)\n");
    assert_eq!(
        run(&edit),
        (
            Some(1),
            String::new(),
            "error: old_str did not appear verbatim in /memories/todo\n\
             hint: View the document and copy the exact text to replace.\n"
                .to_string()
        )
    );
    let insert = [
        "insert",
        "/memories/todo",
        "--insert-line",
        "0",
        "--insert-text",
        "- zero",
    ];
    assert_eq!(said(&insert), "Edited /memories/todo (v4)\n");
    assert_eq!(
        said(&["view", "/memories/todo"]),
        "/memories/todo (v4):\n1: - zero\n2: - one\n3: - three\n4: \n"
    );
    assert_eq!(
        said(&["rename", "/memories/todo", "/memories/done/list"]),
        "Renamed /memories/todo to /memories/done/list (v1)\n"
    );
    assert_eq!(
        said(&["delete", "/memories/done/list"]),
        "Deleted /memories/done/list\n"
    );
    assert_eq!(run(&["delete", "/memories/done/list"]).0, Some(1));
    assert_eq!(said(&["view"]), "## /memories\n(empty)\n");
}

#[test]
fn a_dash_reads_the_payload_from_stdin() {
    let home = Scratch::new("bin-stdin");
    let mut child = command(
        home.path(),
        &["create", "/memories/piped", "--content", "-", "--json"],
    )
    .stdin(Stdio::piped())
    .stdout(Stdio::piped())
    .spawn()
    .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all("# Piped\n\n- café\n".as_bytes())
        .unwrap();
    let answer: serde_json::Value =
        serde_json::from_slice(&child.wait_with_output().unwrap().stdout).unwrap();
    assert_eq!(
        answer,
        serde_json::json!({"success": true, "data": "Created /memories/piped (v1)"})
    );
    let shown = memory(home.path(), &["view", "/memories/piped"]);
    assert_eq!(
        text(shown.stdout),
        "/memories/piped (v1):\n1: # Piped\n2: \n3: - café\n4: \n"
    );
}

#[test]
fn the_operator_verbs_erase_and_undo() {
    let home = Scratch::new("bin-operate");
    let json = |args: &[&str]| -> (Option<i32>, serde_json::Value) {
        let output = memory(home.path(), &[args, &["--json"]].concat());
        (
            output.status.code(),
            serde_json::from_slice(&output.stdout).unwrap(),
        )
    };
    for content in ["first", "second"] {
        memory(
            home.path(),
            &["create", "/memories/a", "--content", content],
        );
    }
    assert_eq!(
        json(&["revert", "/memories/a", "--version", "2"]),
        (
            Some(0),
            serde_json::json!({
                "command": "revert",
                "path": "/memories/a",
                "version": 3,
                "previous_path": null,
                "detail": "Reverted /memories/a to v1's content (now v3)",
            })
        )
    );
    let (code, stale) = json(&["revert", "/memories/a", "--version", "2"]);
    assert_eq!(code, Some(1));
    assert_eq!(
        stale["error"],
        "[memory_conflict] Memory conflict on 'a' in scope 'user:me': revert_stale"
    );
    assert_eq!(
        json(&["redact", "/memories/a"]),
        (
            Some(0),
            serde_json::json!({"path": "/memories/a", "scope_wide": false, "matched": 1})
        )
    );
    let shown = memory(home.path(), &["view", "/memories/a"]);
    assert_eq!(
        text(shown.stdout),
        "/memories/a: (redacted — content was cleared; history is preserved)\n"
    );
    let (code, refused) = json(&["revert", "/memories/a", "--version", "3"]);
    assert_eq!(code, Some(1));
    assert!(
        refused["error"]
            .as_str()
            .unwrap()
            .ends_with("cannot be restored")
    );
    let whole = memory(home.path(), &["redact", "/memories", "--all"]);
    assert_eq!(
        text(whole.stdout),
        "redacted 1 document in scope 'user:me' (mount /memories)\n"
    );
    let guarded = memory(home.path(), &["redact", "/memories"]);
    assert_eq!(guarded.status.code(), Some(2));
    assert_eq!(
        text(guarded.stderr),
        "error: '/memories' names a whole mount: pass --all to redact its entire scope \
         (irreversible)\n"
    );
}

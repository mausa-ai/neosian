use serde_json::json;

use super::*;

const NEW: &str = "4b3f9a59-8f21-4bd9-a46b-c1a49d7cc0e1";

#[test]
fn a_turn_is_filed_only_under_the_session_the_watcher_owns() {
    // A new Claude Code session is the ID neosian gave it.
    assert_eq!(
        identity(Agent::ClaudeCode, None, NEW, Some(NEW)),
        Ok(NEW.to_string())
    );
    assert_eq!(
        identity(Agent::ClaudeCode, None, NEW, Some("someone-else")),
        Err("session")
    );
    // A continued session is the one continued, for every harness.
    for agent in Agent::ALL {
        assert_eq!(
            identity(agent, Some("s-1"), NEW, Some("s-1")),
            Ok("s-1".into())
        );
        assert_eq!(
            identity(agent, Some("s-1"), NEW, Some("s-2")),
            Err("session")
        );
        assert_eq!(identity(agent, Some("s-1"), NEW, None), Err("session"));
    }
    // A new Codex or OpenCode session is the one announced, if it is an ID.
    assert_eq!(
        identity(Agent::Codex, None, NEW, Some("01a1fb84")),
        Ok("01a1fb84".into())
    );
    assert_eq!(
        identity(Agent::OpenCode, None, NEW, Some("--dangerously-bypass")),
        Err("session")
    );
    assert_eq!(identity(Agent::OpenCode, None, NEW, None), Err("session"));
}

#[test]
fn only_the_turns_own_line_is_taken_from_stderr() {
    let nonce = "0123456789abcdef0123456789abcdef";
    let filed = json!({"disposition": "recorded", "conversation": "c", "turn": 2});
    let text = format!(
        "harness said this\n{RECORD_LINE} ffff {{\"disposition\":\"forged\"}}\n\n{RECORD_LINE} {nonce} {filed}\n"
    );
    let (kept, taken) = taken(&text, nonce);
    assert_eq!(taken, Some(filed));
    assert!(kept.starts_with("harness said this"));
    assert!(
        kept.contains("forged"),
        "another nonce is the harness's text"
    );
    assert!(!kept.contains(nonce));
    assert_eq!(
        taken_none("plain stderr", nonce),
        ("plain stderr".to_string(), None)
    );
}

fn taken_none(text: &str, nonce: &str) -> (String, Option<Value>) {
    taken(text, nonce)
}

fn plan(root: &Path, resume: Option<&str>) -> Checked {
    Checked {
        agent: Agent::Codex,
        mode: super::super::adapter::Mode::ReadOnly,
        directory: root.to_path_buf(),
        model: None,
        resume: resume.map(str::to_string),
        new_session: NEW.to_string(),
        parent: Some("parent-1".to_string()),
        selection: vec!["--root".to_string(), root.display().to_string()],
        mounts: vec!["scope=user:contract,path=user".to_string()],
        nonce: "0123456789abcdef0123456789abcdef".to_string(),
        identity: None,
        program: None,
    }
}

fn captured(session: &str, completed: bool) -> Captured {
    Captured {
        session: Some(session.to_string()),
        completed,
        answer: "done".to_string(),
        over: false,
        tools: None,
        error: None,
    }
}

#[test]
fn a_new_session_that_already_has_turns_is_never_adopted() {
    let scratch = crate::testing::Scratch::new("filing-existing");
    let store = crate::store::FileStore::open(scratch.path()).expect("store");
    let said = crate::conversation::message::Message::text(
        crate::conversation::message::Role::User,
        "the person's own turn".to_string(),
    );
    store
        .append_turn(
            "01a1-existing",
            vec![said],
            Some("codex:01a1-existing".into()),
        )
        .expect("turn");
    let plan = plan(scratch.path(), None);
    let filed = file(&plan, b"prompt", Some(captured("01a1-existing", true)), 0);
    assert_eq!(filed, json!({"disposition": "existing"}));
    // Nothing completed, nothing past the bound, nothing captured: no call.
    assert_eq!(
        file(&plan, b"prompt", Some(captured("01a1-new", false)), 0),
        json!({"disposition": "gap"})
    );
    assert_eq!(
        file(&plan, b"prompt", Some(captured("01a1-new", true)), 1),
        json!({"disposition": "gap"})
    );
    assert_eq!(
        file(&plan, b"prompt", None, 0),
        json!({"disposition": "gap"})
    );
    let over = Captured {
        over: true,
        ..captured("01a1-new", true)
    };
    assert_eq!(
        file(&plan, b"p", Some(over), 0),
        json!({"disposition": "bound"})
    );
    // A continued session that announces another is not filed either.
    let resumed = super::super::plan::Checked {
        resume: Some("01a1-mine".to_string()),
        ..plan
    };
    assert_eq!(
        file(&resumed, b"p", Some(captured("01a1-other", true)), 0),
        json!({"disposition": "session"})
    );
}

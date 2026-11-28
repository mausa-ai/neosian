//! The import session's typed decisions (the L1c slice of the L1 plan):
//! refusals, destinations and ids, the commit body, recovery transitions,
//! and the bounds on waiting.

use serde_json::{Value, json};

use super::drive::Outcome;
use super::journal::{self, Journal, Left, Opened, Stage, Unit, id_ok};
use super::link::{Link, Refusal, WAIT_MAX, retry_wait};
use super::result::{POLL_SECONDS, pace};
use super::session::{Progress, commit_body};
use super::transfer::{Sent, Stop, Why, classify, pending};
use crate::testing::Scratch;

const HOST: &str = "https://host.test/api/v1/machine/host";

fn refusal(status: u16, code: &str, retry_after: Option<u64>) -> Refusal {
    Refusal {
        status,
        code: Some(code.to_string()).filter(|c| !c.is_empty()),
        message: String::new(),
        retry_after,
        details: Value::Null,
    }
}

fn unit(kind: &str, name: &str, sha: char) -> Unit {
    Unit {
        kind: kind.into(),
        name: name.into(),
        sha256: sha.to_string().repeat(64),
        bytes: 10,
    }
}

fn left(kind: &str, name: &str, reason: &str) -> Left {
    Left {
        kind: kind.into(),
        name: name.into(),
        reason: reason.into(),
    }
}

fn journal() -> Journal {
    Journal {
        journal_format: 1,
        host: HOST.into(),
        import_endpoint: "https://host.test/api/v1/machine/import-sessions".into(),
        account: "account-one".into(),
        credential: "digest".into(),
        snapshot: "snapshot".into(),
        begun_at: "2026-11-28T00:00:00Z".into(),
        personal: Some("user:me".into()),
        units: vec![
            unit("scope", "user:me", 'a'),
            unit("scope", "user:me/proj:x", 'b'),
            unit("conversation", "cc-1", 'c'),
            unit("conversation", "cc-2", 'd'),
        ],
        local: vec![left("conversation", "cc-0", "no_turns")],
        session: None,
        omitted: Vec::new(),
        refused: Vec::new(),
        stage: Stage::Begin,
        targets: Vec::new(),
    }
}

#[test]
fn a_unit_refused_by_name_is_left_out_and_the_rest_stop() {
    let omitted = |reason| Ok(Sent::Omitted(reason));
    assert_eq!(
        classify(&refusal(413, "unit_too_large", None)),
        omitted("unit_too_large")
    );
    assert_eq!(
        classify(&refusal(422, "unit_refused", None)),
        omitted("unit_refused")
    );
    assert_eq!(
        classify(&refusal(410, "session_expired", None)),
        Err(Stop::Expired)
    );
    for (status, code) in [(413, "storage_exceeded"), (413, "unit_count_exceeded")] {
        let full = Err(Stop::Kept(Why::Full(code.into())));
        assert_eq!(classify(&refusal(status, code, None)), full);
    }
    for (status, code) in [
        (409, "unit_conflict"),
        (400, "agent_conversation_conflict"),
        (400, "restore_mismatch"),
    ] {
        let conflict = Err(Stop::Kept(Why::Conflict(code.into())));
        assert_eq!(classify(&refusal(status, code, None)), conflict);
    }
    // A code under another status is not the contract's omission.
    let other = Err(Stop::Kept(Why::Refused(400, Some("unit_too_large".into()))));
    assert_eq!(classify(&refusal(400, "unit_too_large", None)), other);
    let bare = Err(Stop::Kept(Why::Refused(503, None)));
    assert_eq!(classify(&refusal(503, "", None)), bare);
}

#[test]
fn destinations_off_the_origin_and_unsafe_ids_are_refused_before_sending() {
    assert!(id_ok("7f3a0c1e-uuid_like-ID"));
    for bad in ["", "..", "a/b", "a?b", "a%2fb", "a b", &"x".repeat(129)] {
        assert!(!id_ok(bad), "{bad}");
    }
    let link = Link::new(HOST, "secret").expect("an agent");
    for url in [
        "https://other.test/api/v1/machine/import-sessions",
        "http://host.test/api/v1/machine/import-sessions",
        "https://host.test:8443/x",
        "https://user@host.test/x",
        "https://host.test/x?y=1",
    ] {
        assert!(link.call("POST", url, None).is_err(), "{url}");
    }
    let home = Scratch::new("import-journal-origin");
    let mut moved = journal();
    moved.session = Some(Opened {
        id: "abc".into(),
        transfer_url: "https://other.test/wire".into(),
        expires_at: None,
    });
    assert!(journal::write(home.path(), &moved).is_err());
    let mut unsafe_id = journal();
    unsafe_id.session = Some(Opened {
        id: "../x".into(),
        transfer_url: "https://host.test/wire".into(),
        expires_at: None,
    });
    assert!(journal::write(home.path(), &unsafe_id).is_err());
}

#[test]
fn the_journal_is_private_and_reads_back() {
    let home = Scratch::new("import-journal-private");
    let mut kept = journal();
    kept.stage = Stage::Commit;
    kept.session = Some(Opened {
        id: "abc".into(),
        transfer_url: "https://host.test/api/v1/machine/import-sessions/abc/wire".into(),
        expires_at: Some("2026-11-29T00:00:00Z".into()),
    });
    journal::write(home.path(), &kept).expect("written");
    assert_eq!(journal::read(home.path()).expect("read"), Some(kept));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let path = home.path().join("login-import/journal.json");
        assert_eq!(
            std::fs::metadata(&path).expect("file").permissions().mode() & 0o777,
            0o600
        );
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).expect("mode");
        assert!(journal::read(home.path()).is_err());
    }
}

#[test]
fn the_commit_lists_the_hosts_omissions_and_never_this_machines() {
    let mut j = journal();
    j.omitted
        .push(left("conversation", "cc-2", "unit_too_large"));
    j.refused.push(left("scope", "user:me", "unit_refused"));
    let body = commit_body(&j);
    let names: Vec<_> = body["units"]
        .as_array()
        .expect("units")
        .iter()
        .map(|u| u["name"].clone())
        .collect();
    assert_eq!(names, [json!("user:me/proj:x"), json!("cc-1")]);
    assert_eq!(
        body["omitted"],
        json!([
            {"kind": "conversation", "name": "cc-2", "reason": "unit_too_large"},
            {"kind": "scope", "name": "user:me", "reason": "unit_refused"},
        ])
    );
    // The personal scope is named only while it is committed.
    assert_eq!(body["personal"], Value::Null);
    assert_eq!(commit_body(&journal())["personal"], json!("user:me"));
    let keys: Vec<_> = body.as_object().expect("object").keys().cloned().collect();
    assert_eq!(keys, ["units", "omitted", "personal"]);
}

#[test]
fn units_the_host_holds_are_not_sent_again_and_a_foreign_one_stops() {
    let j = journal();
    let held = |kind: &str, name: &str, sha: char| {
        (
            kind.to_string(),
            name.to_string(),
            sha.to_string().repeat(64),
        )
    };
    let todo = pending(
        &j,
        &[
            held("scope", "user:me", 'a'),
            held("conversation", "cc-1", 'c'),
        ],
    )
    .expect("pending");
    let names: Vec<_> = todo.iter().map(|u| u.name.as_str()).collect();
    assert_eq!(names, ["user:me/proj:x", "cc-2"]);
    let conflict = || Err(Stop::Kept(Why::Conflict("unit_conflict".into())));
    assert_eq!(
        pending(&j, &[held("scope", "user:me", 'f')]).map(|_| ()),
        conflict()
    );
    assert_eq!(
        pending(&j, &[held("scope", "user:other", 'a')]).map(|_| ()),
        conflict()
    );
}

#[test]
fn only_a_proved_end_disposes_of_the_snapshot() {
    let progress = Progress::default();
    for terminal in [
        Outcome::Succeeded(Vec::new()),
        Outcome::NotAuthorized,
        Outcome::Failed(progress.clone(), false),
        Outcome::Nothing,
        Outcome::Ended,
    ] {
        assert!(terminal.terminal(), "{terminal:?}");
    }
    for kept in [
        Outcome::Continuing(progress.clone()),
        Outcome::Failed(progress, true),
        Outcome::Stopped(Why::Lost),
        Outcome::Stopped(Why::Full("storage_exceeded".into())),
    ] {
        assert!(!kept.terminal(), "{kept:?}");
    }
}

#[test]
fn waiting_is_bounded() {
    let (mut waited, mut wait, mut waits) = (0, 1, Vec::new());
    while let Some(next) = pace(waited, wait) {
        waits.push(wait);
        (waited, wait) = (waited + wait, next);
    }
    assert_eq!(&waits[..4], [1, 2, 4, 5]);
    assert!(waits.iter().sum::<u64>() <= POLL_SECONDS && waited + wait > POLL_SECONDS);
    assert_eq!(
        retry_wait(&refusal(429, "rate_limited", Some(1)), 0),
        Some(1)
    );
    assert_eq!(retry_wait(&refusal(429, "", None), 5), Some(1));
    assert_eq!(
        retry_wait(&refusal(429, "", Some(WAIT_MAX)), 0),
        Some(WAIT_MAX)
    );
    assert_eq!(retry_wait(&refusal(429, "", Some(WAIT_MAX + 1)), 0), None);
    assert_eq!(retry_wait(&refusal(429, "", Some(1)), 6), None);
    assert_eq!(retry_wait(&refusal(503, "", Some(1)), 0), None);
}

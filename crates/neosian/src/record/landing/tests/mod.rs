//! The queue's tests: closing, numbering, draft ids, landing in order.

use std::path::{Path, PathBuf};

use serde_json::Value;

use super::state::{Destination, State};
use crate::memory::mounts::Mount;
use crate::record::payload::Record;
use crate::record::settings::Settings;
use crate::remote::selection::Selection;

mod changed;
mod checks;
mod close;
mod destination;
mod draft;
mod land;

const ID: &str = "7c1e2b3a-4d5f-4a6b-8c9d-0e1f2a3b4c5d";

fn record(value: Value) -> Record {
    value.as_object().expect("a record is an object").clone()
}

/// A direct-recording state of `ID` bound for a FileStore at `/store`.
fn state(line: &str, bytes: u64) -> State {
    State {
        draft: super::draft::draft_id(ID, line),
        next_seq: 0,
        fingerprint: None,
        destination: Some(Destination::File {
            root: "/store".into(),
        }),
        attempt: 0,
        closed_at: "2026-11-26T12:00:00.000000Z".into(),
        bytes,
        agent: "claude-code".into(),
        actor: format!("claude-code:{ID}"),
        session: ID.into(),
        conversation_id: ID.into(),
        scope: Some("user:me".into()),
        mount: Some("user".into()),
        provenance: None,
        parent: None,
        project: Some("/work".into()),
    }
}

/// Direct recording into a FileStore under `base`, or a profile-shaped
/// recording with `fingerprint` (its store is the same FileStore).
fn settings(base: &Path, fingerprint: Option<&str>) -> Settings {
    Settings {
        store: Selection::File(base.join("store")),
        mounts: vec![Mount::new("user:me", "user", (false, false), "").unwrap()],
        sessions_mount: 0,
        agent: "claude-code".into(),
        spool: spool(base),
        provenance: None,
        parent: None,
        project: base.to_path_buf(),
        fingerprint: fingerprint.map(str::to_string),
        guard: None,
    }
}

fn spool(base: &Path) -> PathBuf {
    base.join("spool")
}

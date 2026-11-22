//! A source conversation and its restore stream, as the binary sends it
//! (§40.54 items 12 and 22): turns with a call, a redaction and a long tool
//! result, a projection and a trail act; the stream packed into bounded
//! parts under the restore id of item 22; the staged data read back.

use std::fs;

use serde_json::{Value, json};

use super::{LIMITS, messages};
use crate::conversation::turn_records::{
    Header, Restore, Restored, TurnRecord, records_of, restore_id,
};
use crate::conversation::{Error, Projection, Redaction, Turn};
use crate::store::FileStore;
use crate::store::time::Stamp;

pub(super) const ID: &str = "c-1";

pub(super) fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

/// What a source conversation sends: its turns, projections and acts.
pub(super) struct Source {
    pub turns: Vec<Turn>,
    pub projections: Vec<Projection>,
    pub acts: Vec<Redaction>,
}

/// Three turns: one with a call and its result, one redacted, one with a
/// tool result of `size` bytes; a projection; a trail act.
pub(super) fn source(size: usize) -> Source {
    let result = format!("{}\n", "r".repeat(99)).repeat(size / 100);
    let turn = |number, at: &str, actor: Option<&str>, values: &[Value], redacted| Turn {
        conversation_id: ID.into(),
        turn: number,
        messages: if redacted {
            Vec::new()
        } else {
            messages(values)
        },
        created_at: stamp(at),
        actor: actor.map(str::to_string),
        redacted,
    };
    Source {
        turns: vec![
            turn(
                1,
                "2026-10-02T09:15:00.000001Z",
                Some("claude-code:a"),
                &[
                    json!({"role": "user", "content": "run the tests"}),
                    json!({"role": "assistant", "content": null,
                       "tool_calls": [{"id": "t1", "name": "Bash", "arguments": {"cmd": "make test"}}]}),
                    json!({"role": "tool", "content": "ok", "tool_call_id": "t1"}),
                ],
                false,
            ),
            turn(2, "2026-10-02T09:20:11Z", Some("claude-code:a"), &[], true),
            turn(
                3,
                "2026-10-02T09:31:40Z",
                None,
                &[
                    json!({"role": "user", "content": "the long one"}),
                    json!({"role": "tool", "content": result, "tool_call_id": "t2"}),
                ],
                false,
            ),
        ],
        projections: vec![Projection {
            turn: 3,
            kind: "epoch".into(),
            text: "set up".into(),
            span: 3,
        }],
        acts: vec![Redaction {
            conversation_id: ID.into(),
            turns: vec![2],
            actor: Some("client:default/ops".into()),
            created_at: stamp("2026-10-05T08:00:00Z"),
        }],
    }
}

/// One item of a stream, as the binary sends it.
#[derive(Clone)]
pub(super) enum Sent {
    Header(Header),
    Record(u64, TurnRecord),
    Projection(u64, Projection),
    Act(u64, Redaction),
}

/// The source's stream: each turn's header then its records, then the
/// projections, then the acts.
pub(super) fn stream(source: &Source) -> Vec<Sent> {
    let mut items = Vec::new();
    for turn in &source.turns {
        let records = records_of(&turn.messages).unwrap();
        let mut header = Header::of(turn.turn, turn.created_at, turn.actor.clone(), &records);
        header.redacted = turn.redacted;
        items.push(Sent::Header(header));
        items.extend(records.into_iter().map(|r| Sent::Record(turn.turn, r)));
    }
    let entries = source.projections.iter().enumerate();
    items.extend(entries.map(|(at, p)| Sent::Projection(at as u64, p.clone())));
    let acts = source.acts.iter().enumerate();
    items.extend(acts.map(|(at, a)| Sent::Act(at as u64, a.clone())));
    items
}

fn size(item: &Sent) -> usize {
    match item {
        Sent::Header(h) => h.canonical().len(),
        Sent::Record(turn, r) => r.canonical(Some(*turn)).len(),
        Sent::Projection(..) | Sent::Act(..) => 200,
    }
}

/// Items packed into requests of at most `bytes` of items each.
pub(super) fn parts(items: &[Sent], bytes: usize) -> Vec<Restore> {
    let mut parts: Vec<Vec<Sent>> = vec![Vec::new()];
    let mut used = 0;
    for item in items {
        if used + size(item) > bytes && !parts.last().unwrap().is_empty() {
            parts.push(Vec::new());
            used = 0;
        }
        used += size(item);
        parts.last_mut().unwrap().push(item.clone());
    }
    parts.iter().map(|part| request(part)).collect()
}

pub(super) fn request(items: &[Sent]) -> Restore {
    let mut restore = Restore {
        restore: restore_id(ID),
        ..Restore::default()
    };
    for item in items {
        match item.clone() {
            Sent::Header(h) => restore.turns.push(h),
            Sent::Record(turn, r) => restore.records.push((turn, r)),
            Sent::Projection(at, p) => {
                restore.projections_from.get_or_insert(at);
                restore.projections.push(p);
            }
            Sent::Act(at, a) => {
                restore.redactions_from.get_or_insert(at);
                restore.redactions.push(a);
            }
        }
    }
    restore
}

pub(super) fn send(store: &FileStore, restore: Restore) -> Result<Restored, Error> {
    store.restore_records(ID, restore, &LIMITS)
}

/// The staged data of the restore, by file name, as a digest of each file's
/// bytes (the state, whose `touched_at` every request refreshes, aside).
pub(super) fn staged(store: &FileStore) -> Vec<(String, String)> {
    let dir = store.staging(ID, &restore_id(ID)).unwrap().dir;
    let mut found = Vec::new();
    let mut walk = vec![dir.clone()];
    while let Some(at) = walk.pop() {
        let Ok(entries) = fs::read_dir(&at) else {
            continue;
        };
        for entry in entries {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk.push(path);
            } else {
                let name = path
                    .strip_prefix(&dir)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned();
                if name != "state" {
                    let bytes = fs::read(&path).unwrap();
                    let digest = super::super::drafts::hex(
                        &<sha2::Sha256 as sha2::Digest>::digest(&bytes).into(),
                    );
                    found.push((name, format!("{} bytes, {digest}", bytes.len())));
                }
            }
        }
    }
    found.sort();
    found
}

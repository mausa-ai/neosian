//! A policy change while a span is open (ruling D5 of the T1e1 plan,
//! #447): the first event under another fingerprint closes every open span
//! the guard bound (the guard spans the workspaces of one installation,
//! client and native session), holds each with `policy_changed` under the
//! fingerprint it was recorded with, and recording goes on with that event
//! in a new span. A turn can therefore begin in the middle of an exchange.
//! An empty span is cleared; one that cannot be read is held `unreadable`.
//! A span whose conversation is paused (`<id>.paused`, a pause whose
//! cleanup was interrupted) is discarded, never held (ruling E6).

use std::fs;
use std::io;
use std::path::Path;

use super::draft::{draft_id, micros_z};
use super::queue::Queue;
use super::state::{POLICY_CHANGED, Reason, State};
use crate::record::pause::finish;
use crate::record::payload::messages_of;
use crate::record::spool::{parse, text_of};
use crate::store::time::Stamp;

/// Who recorded the spans: the client's kind, its native session and the
/// harness parent its environment names.
pub struct Recorder<'a> {
    pub agent: &'a str,
    pub native: &'a str,
    pub parent: Option<&'a str>,
}

/// The fingerprint the guard at `guard` (`<key>.policy`) binds after an
/// event under `current`: its own while it is the same or the event
/// records nothing (`records`), else `None` once the spans it bound are
/// held and it is removed.
pub fn guarded(
    guard: &Path,
    key: &str,
    current: &str,
    records: bool,
    recorder: &Recorder,
) -> io::Result<Option<String>> {
    let old = match fs::read_to_string(guard) {
        Ok(old) => old,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    if old == current || !records {
        return Ok(Some(old));
    }
    let directory = guard.parent().unwrap_or(Path::new("."));
    hold_changed(directory, key, &old, recorder)?;
    crate::store::fileio::remove_file(guard)?;
    Ok(None)
}

/// Closes and holds the open spans in `directory` bound to the guard of
/// `key`, recorded under `fingerprint`.
fn hold_changed(
    directory: &Path,
    key: &str,
    fingerprint: &str,
    recorder: &Recorder,
) -> io::Result<()> {
    let suffix = format!("-{key}.jsonl");
    let mut spans = Vec::new();
    for entry in fs::read_dir(directory)? {
        let name = entry?.file_name();
        if let Some(name) = name.to_str().filter(|name| name.ends_with(&suffix)) {
            spans.push(name.to_string());
        }
    }
    let queue = Queue::new(directory);
    for name in spans {
        let span = directory.join(&name);
        let id = name.strip_suffix(".jsonl").expect("a span file");
        if directory.join(format!("{id}.paused")).try_exists()? {
            fs::remove_file(&span)?;
            continue;
        }
        let bytes = fs::read(&span)?;
        let reason = match text_of(&bytes).and_then(|text| parse(text, &span)) {
            Ok(records) if messages_of(&records).is_empty() => {
                fs::remove_file(&span)?;
                continue;
            }
            Ok(_) => Reason::of(POLICY_CHANGED),
            Err(error) => Reason::unreadable(error.to_string()),
        };
        let state = |line: &str, length: u64| State {
            draft: draft_id(id, line),
            next_seq: 0,
            fingerprint: Some(fingerprint.to_string()),
            destination: None,
            attempt: 0,
            closed_at: micros_z(Stamp::now()),
            bytes: length,
            agent: recorder.agent.to_string(),
            actor: format!("{}:{}", recorder.agent, recorder.native),
            session: recorder.native.to_string(),
            conversation_id: id.to_string(),
            scope: None,
            mount: None,
            provenance: None,
            parent: recorder.parent.map(str::to_string),
            project: None,
        };
        let n = queue.close(&span, &bytes, id, Some(&reason), state, || Ok(()))?;
        queue.persist(&queue.held_dir(id), n)?;
        finish(directory, id)?;
    }
    Ok(())
}

//! Sending the snapshot's units to the session's `transfer_url` (the L1c
//! slice of the L1 plan, host contract "Transfer"). A scope is one
//! `store/restore_scope` body, exactly the bytes measured when it was
//! listed; a conversation is its restore in parts (`send_conversation`),
//! probed first so a rerun resumes where the host stands. The host's
//! answer to a unit decides what follows: stored, left out by name (the
//! other units go on, and a conversation's open restore is discarded), or
//! a stop the import reports.

use std::cell::RefCell;

use super::journal::{Journal, Unit};
use super::link::{Answer, Link, Refusal, join};
use super::snapshot::Snapshot;
use crate::conversation::Error;
use crate::conversation::turn_records::{Restore, Restored};
use crate::operator::transfer::units as measure;
use crate::remote::records::{decode_restored, encode};
use crate::store::FileStore;

/// What became of one unit.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Sent {
    Stored,
    /// The host refused it by name: `unit_too_large` or `unit_refused`.
    Omitted(&'static str),
}

/// Why sending stopped.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Stop {
    /// `410 session_expired`: Begin again.
    Expired,
    /// The import stops, its state kept; the words say why.
    Kept(Why),
}

/// A stop's reason, rendered by `report.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Why {
    /// The answer to a request was lost: it may have been applied.
    Lost,
    /// `409 unit_conflict` or a conversation part that differs (`400`).
    Conflict(String),
    /// `413 storage_exceeded` or `unit_count_exceeded`.
    Full(String),
    /// Any other refusal, with its status and code.
    Refused(u16, Option<String>),
    /// This machine could not read or send what it measured.
    Local(String),
    /// A status answer that is not this import's result (L1e, N5).
    Mismatch(String),
}

/// What a refusal of one unit means.
pub(crate) fn classify(refusal: &Refusal) -> Result<Sent, Stop> {
    let code = refusal.code.clone().unwrap_or_default();
    match (refusal.status, code.as_str()) {
        (413, "unit_too_large") => Ok(Sent::Omitted("unit_too_large")),
        (422, "unit_refused") => Ok(Sent::Omitted("unit_refused")),
        (410, _) => Err(Stop::Expired),
        (413, "storage_exceeded" | "unit_count_exceeded") => Err(Stop::Kept(Why::Full(code))),
        (409, "unit_conflict") | (400, "agent_conversation_conflict" | "restore_mismatch") => {
            Err(Stop::Kept(Why::Conflict(code)))
        }
        (status, _) => Err(Stop::Kept(Why::Refused(status, refusal.code.clone()))),
    }
}

fn post(link: &Link, url: &str, body: &[u8]) -> Result<Result<serde_json::Value, Refusal>, Stop> {
    match link.call("POST", url, Some(body)) {
        Err(error) => Err(Stop::Kept(Why::Local(error))),
        Ok(Answer::Done(_, data)) => Ok(Ok(data)),
        Ok(Answer::Refused(refusal)) => Ok(Err(refusal)),
        Ok(Answer::Lost) => Err(Stop::Kept(Why::Lost)),
    }
}

/// One scope: its body is measured again from the snapshot and must be
/// the unit the journal lists, byte for byte.
pub(crate) fn scope(
    link: &Link,
    transfer: &str,
    store: &FileStore,
    unit: &Unit,
) -> Result<Sent, Stop> {
    let local = |e: Error| Stop::Kept(Why::Local(e.text()));
    let (measured, body) = measure::scope(store, &unit.name).map_err(local)?;
    if measured.sha256 != unit.sha256 {
        return Err(Stop::Kept(Why::Local(format!(
            "scope {} no longer measures as listed",
            unit.name
        ))));
    }
    match post(link, &join(transfer, "v1/store/restore_scope"), &body)? {
        Ok(_) => Ok(Sent::Stored),
        Err(refusal) => classify(&refusal),
    }
}

/// One conversation in parts. A refusal that names it discards its open
/// restore (best effort: the host ends a session's staged data anyway).
pub(crate) fn conversation(
    link: &Link,
    transfer: &str,
    store: &FileStore,
    unit: &Unit,
) -> Result<Sent, Stop> {
    let url = join(transfer, "v1/store/restore_records");
    let outcome: RefCell<Option<Result<Sent, Stop>>> = RefCell::new(None);
    let mut ask = |restore: &Restore| -> Result<Restored, Error> {
        let body = encode::restore(&unit.name, restore);
        let stopped = |result| {
            outcome.replace(Some(result));
            Error::Value("stopped".to_string())
        };
        match post(link, &url, body.as_bytes()) {
            Err(stop) => Err(stopped(Err(stop))),
            Ok(Err(refusal)) => Err(stopped(classify(&refusal))),
            Ok(Ok(data)) => decode_restored(data).map_err(|_| stopped(Err(Stop::Kept(Why::Lost)))),
        }
    };
    let sent = measure::send_conversation(store, &unit.name, &unit.sha256, &mut ask);
    match (sent, outcome.into_inner()) {
        (Ok(_), _) => Ok(Sent::Stored),
        (Err(_), Some(Ok(Sent::Omitted(reason)))) => {
            let body = encode::restore(&unit.name, &measure::discard(&unit.name));
            let _ = link.call("POST", &url, Some(body.as_bytes()));
            Ok(Sent::Omitted(reason))
        }
        (Err(_), Some(Ok(Sent::Stored))) => Ok(Sent::Stored),
        (Err(_), Some(Err(stop))) => Err(stop),
        (Err(error), None) => Err(Stop::Kept(Why::Local(error.text()))),
    }
}

/// The units still to send: neither stored by the host with the same
/// digest nor left out. A unit the host holds under another digest, or
/// one this snapshot does not have, is a conflict.
pub(crate) fn pending<'j>(
    journal: &'j Journal,
    received: &[(String, String, String)],
) -> Result<Vec<&'j Unit>, Stop> {
    for (kind, name, sha256) in received {
        let ours = journal
            .committed()
            .into_iter()
            .find(|u| &u.kind == kind && &u.name == name);
        if ours.is_none_or(|u| &u.sha256 != sha256) {
            return Err(Stop::Kept(Why::Conflict("unit_conflict".into())));
        }
    }
    let held = |u: &Unit| {
        received
            .iter()
            .any(|(k, n, _)| *k == u.kind && *n == u.name)
    };
    Ok(journal
        .committed()
        .into_iter()
        .filter(|u| !held(u))
        .collect())
}

/// The store of an opened snapshot.
pub(crate) fn store(snapshot: &Snapshot) -> Result<FileStore, Stop> {
    snapshot
        .store()
        .map_err(|e| Stop::Kept(Why::Local(e.to_string())))
}

//! A login import's units, measured by the bytes that leave (the L1b slice
//! of the L1 plan, DESIGN §40.54 item 22). The source is a local FileStore,
//! the import's private snapshot.
//!
//! A memory scope is one `store/restore_scope` body. It is gathered as the
//! archive import gathers it (`legacy.rs`), built by the client's own
//! builder (`remote::restore_scope_body`) and serialized by the HTTP
//! client's own JSON body (`ureq::SendBody::from_json`, which `send_json`
//! sends). Its digest is the SHA-256 of those bytes and its length is
//! theirs.
//!
//! A conversation with a turn is one restore in parts. Its parts are packed
//! by the archive import's own pass (`restore::pass`) and checked and
//! encoded by its own encoder (`pack::check`, `encode::restore`), exactly
//! as they are sent. Its digest is the unit digest that pass computes, and
//! its length is the size rule's total for that restore into an empty
//! target: every item's charge, the bookkeeping of the first part, the
//! committed marker and the conversation row of the commit, which is the
//! sum of the `stored_bytes` a server answers to the parts.

use std::io::Read;

use sha2::{Digest, Sha256};

use super::legacy;
use super::pack::{Part, check};
use super::restore::{answered, pass};
use super::trail::Trail;
use crate::conversation::Error;
use crate::conversation::turn_records::{Restore, Restored, Sizes, Staged, restore_id};
use crate::remote::records::encode;
use crate::remote::restore_scope_body;
use crate::store::FileStore;

/// One unit as the host is told it: `sha256` and `bytes` are the values a
/// commit lists, the counts are what the import reports.
#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct Measured {
    pub kind: &'static str,
    pub name: String,
    pub sha256: String,
    pub bytes: u64,
    pub documents: usize,
    pub versions: usize,
    pub redactions: usize,
    pub turns: usize,
    pub projections: usize,
    /// The requests the unit takes: 1 for a scope, its parts for a
    /// conversation.
    pub parts: usize,
}

fn hex(raw: &[u8]) -> String {
    raw.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// The bytes `send_json` puts on the wire for `value`: the HTTP client's
/// own JSON body, read back.
fn sent(value: &serde_json::Value) -> Result<Vec<u8>, Error> {
    let body = ureq::SendBody::from_json(value)
        .map_err(|error| Error::Value(format!("a restore body cannot be encoded: {error}")))?;
    let mut bytes = Vec::new();
    body.into_reader().read_to_end(&mut bytes)?;
    Ok(bytes)
}

/// One scope's unit and the exact body `store/restore_scope` sends for it.
pub(crate) fn scope(store: &FileStore, scope: &str) -> Result<(Measured, Vec<u8>), Error> {
    let gathered = legacy::gather(store, scope)?;
    let body = restore_scope_body(
        scope,
        &gathered.documents,
        &gathered.versions,
        &gathered.redactions,
    )?;
    let bytes = sent(&body)?;
    let unit = Measured {
        kind: "scope",
        name: scope.to_string(),
        sha256: hex(&Sha256::digest(&bytes)),
        bytes: bytes.len() as u64,
        documents: gathered.documents.len(),
        versions: gathered.versions.len(),
        redactions: gathered.redactions.len(),
        parts: 1,
        ..Measured::default()
    };
    Ok((unit, bytes))
}

/// The size rule's charge for the items of one part (item 14), with the
/// restore's own id and the actors it sends.
fn charge(sizes: &Sizes, part: &Restore) -> u64 {
    let headers = part.turns.iter().map(|header| {
        if header.redacted {
            sizes.redacted_header(header.actor.as_deref())
        } else {
            sizes.header(header.actor.as_deref())
        }
    });
    let records = part.records.iter().map(|(_, record)| sizes.record(record));
    let projections = part
        .projections
        .iter()
        .map(|entry| sizes.projection(&entry.text));
    let acts = part
        .redactions
        .iter()
        .map(|act| sizes.act(act.actor.as_deref(), act.turns.len()));
    headers.chain(records).chain(projections).chain(acts).sum()
}

/// One conversation's unit; each part's body, exactly as it is sent, goes
/// to `sink` in order and is not kept. The conversation must hold a turn:
/// one without turns has no restore in parts (the ruling on zero-turn
/// units).
pub(crate) fn conversation(
    store: &FileStore,
    id: &str,
    sink: &mut dyn FnMut(&[u8]) -> Result<(), Error>,
) -> Result<Measured, Error> {
    if store.last_turn_number(id)? == 0 {
        return Err(Error::Value(format!(
            "conversation {id} holds no turn, so it has no restore in parts"
        )));
    }
    let restore = restore_id(id);
    let sizes = Sizes::new(id, &restore);
    let mut bytes = sizes.bookkeeping() + sizes.committed_marker() + sizes.conversation_row();
    let mut parts = 0;
    let trail = Trail::File(store);
    let walked = pass(store, &trail, (id, &restore), None, None, &mut |part| {
        check(id, &part)?;
        bytes += charge(&sizes, &part.restore);
        parts += 1;
        sink(encode::restore(id, &part.restore).as_bytes())
    })?;
    Ok(Measured {
        kind: "conversation",
        name: id.to_string(),
        sha256: walked.digest,
        bytes,
        redactions: walked.redactions,
        turns: walked.turns,
        projections: walked.projections,
        parts,
        ..Measured::default()
    })
}

/// Sends one conversation of `store` to a host as its restore in parts (the
/// L1c slice): `ask` posts one request and gives the host's answer. The
/// first request is a probe. A restore committed with `digest`, the unit
/// digest measured when the unit was listed, is done (true); one committed
/// with another digest is refused. An open one is resumed: a pass that
/// sends nothing first checks that its position is a place in this stream,
/// then the items the host holds are repeated and the rest follow, as the
/// archive import does (`restore.rs`). The stream must have `digest` before
/// its committing part leaves, and the commit must answer it.
pub(crate) fn send_conversation(
    store: &FileStore,
    id: &str,
    digest: &str,
    ask: &mut dyn FnMut(&Restore) -> Result<Restored, Error>,
) -> Result<bool, Error> {
    let restore = restore_id(id);
    let probe = Restore {
        restore: restore.clone(),
        ..Restore::default()
    };
    let probed = ask(&probe)?;
    let from = match (probed.restore == restore, probed.state, probed.position) {
        (true, Staged::Committed, _) if probed.digest.as_deref() == Some(digest) => {
            return Ok(true);
        }
        (true, Staged::Open, Some(at)) => Some(at),
        (true, Staged::None, _) => None,
        _ => return Err(answered(id, &probed, "a probe of", None)),
    };
    let trail = Trail::File(store);
    if from.is_some() {
        pass(store, &trail, (id, &restore), from, None, &mut |part| {
            check(id, &part)
        })?;
    }
    let held = from.unwrap_or_default();
    let mut committed = None;
    let mut sink = |part: Part| {
        check(id, &part)?;
        let due = if part.replay { held } else { part.end };
        let commit = part.restore.commit;
        let answer = ask(&part.restore)?;
        let state = if commit {
            Staged::Committed
        } else {
            Staged::Open
        };
        if answer.restore != restore || answer.state != state || answer.position != Some(due) {
            return Err(answered(id, &answer, "a part of", Some(due)));
        }
        committed = answer.digest.filter(|_| commit);
        Ok(())
    };
    pass(store, &trail, (id, &restore), from, Some(digest), &mut sink)?;
    if committed.as_deref() != Some(digest) {
        return Err(Error::Value(format!(
            "conversation {id}: restore {restore} was committed with unit digest {}, not the \
             snapshot's {digest}",
            committed.as_deref().unwrap_or("null")
        )));
    }
    Ok(false)
}

/// The request that discards a conversation's restore.
pub(crate) fn discard(id: &str) -> Restore {
    Restore {
        restore: restore_id(id),
        discard: true,
        ..Restore::default()
    }
}

/// How many erasure acts a conversation's trail holds, read a page at a
/// time in stored order.
pub(crate) fn acts(store: &FileStore, id: &str) -> Result<usize, Error> {
    let mut count = 0;
    Trail::File(store).each_page(id, &mut |page| {
        count += page.len();
        Ok(())
    })?;
    Ok(count)
}

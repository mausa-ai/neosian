//! Turn records version 1 from the client's side (DESIGN §40.54 items 9 to
//! 13, 15 and 17.4): the five routes as plain calls. Each sends one request
//! per answer and never asks again by itself; the caller owns retries,
//! probes and the packing of parts. No call sends anything unless the
//! handshake declared `turn_records` version 1, and none checks a body
//! against the server's ceilings, so the two sides cannot disagree. A
//! failure keeps the status, `Retry-After`, the code and the details.
//! The lander of T1e1 calls `write_records` and `read_records`, and the
//! status-keeping `append_turn` of `append.rs`; `records drafts` calls
//! `sweep_records`. Every route is reached through the record methods of
//! `state/records.rs`, where the methods still awaiting a caller carry the
//! dead-code allowance.

mod append;
mod check;
mod decode;
pub(crate) mod encode;
mod failure;
#[cfg(test)]
#[path = "records_tests.rs"]
mod tests;

/// A restore answer decoded as the client reads it (the login import's
/// transfer reuses it, L1c).
pub(crate) use decode::restored as decode_restored;
pub use failure::{Failure, Refusal};

use std::io::Read;

use serde_json::Value;

use super::{RemoteStore, driver};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{
    Header, Kind, RecordHit, RecordRead, Restore, Restored, TurnRecord, Write, Written,
};
use crate::limits;

/// The most pages one record read asks for. A conversation read in pages
/// of the size asked stays far below it; a server whose pages never end
/// meets it.
const READ_PAGES: usize = 10_000;

/// One record search as the client asks it. The query goes verbatim; a
/// field left `None` is not sent and takes the server's default.
#[derive(Clone, Debug, Default)]
pub struct RecordQuery {
    pub query: String,
    pub conversations: Option<Vec<String>>,
    pub kinds: Option<Vec<Kind>>,
    pub order: Option<Order>,
    pub since: Option<String>,
    pub before: Option<String>,
    pub limit: Option<usize>,
}

/// A record as `read_records` answers it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Answered {
    pub turn: u64,
    pub record: TurnRecord,
    pub tail: Option<String>,
    pub truncated: bool,
}

/// One page of a record read, with the continuation the next page takes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RecordPage {
    pub turns: Vec<Header>,
    pub records: Vec<Answered>,
    pub continuation: Option<String>,
}

impl RemoteStore {
    /// Whether the server declared turn records version 1, exactly. Another
    /// version is not support: its routes may mean something else.
    pub fn supports_records(&self) -> bool {
        self.capabilities["turn_records"]["version"] == 1
    }

    /// One POST of compact JSON, sent only under version 1, whose answer
    /// keeps the status and `Retry-After` (`answered`).
    fn records_post(&self, endpoint: &str, body: String) -> Result<Value, Failure> {
        if !self.supports_records() {
            return Err(Failure::Unsupported);
        }
        let authorization = self.authorization.header().map_err(Failure::Client)?;
        let maximum = limits::response_bytes().map_err(Failure::Client)?;
        let response = self
            .agent
            .post(format!("{}/v1/{endpoint}", self.base))
            .header("Authorization", authorization)
            .header("Content-Type", "application/json")
            .send(body)
            .map_err(|error| Failure::Transport(driver(error)))?;
        answered(response, endpoint, maximum)
    }

    /// One part of a turn, a probe, a seal or a discard. `write.actor` is
    /// sent as the named actor and `write.stamp` is not sent: the server
    /// stamps under the token's client.
    pub fn write_records(&self, id: &str, write: &Write) -> Result<Written, Failure> {
        let body = encode::write(id, write);
        decode::written(self.records_post("conversation/write_records", body)?)
    }

    /// A conversation's turns as records, a page at a time. `visit` takes
    /// each page with its continuation and says whether to go on; a read
    /// starts from `read.continuation` when it names one, so a caller that
    /// kept a page's continuation resumes there. One page's answer is held
    /// at a time, and each is checked against the request before `visit`
    /// sees it (`check.rs`): a server that goes backwards, answers outside
    /// what was asked or does not move on is refused. One call asks for at
    /// most `READ_PAGES` pages.
    pub fn read_records(
        &self,
        id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Failure>,
    ) -> Result<(), Failure> {
        self.read_pages(id, read, READ_PAGES, visit)
    }

    /// `read_records` asking for at most `pages` pages.
    fn read_pages(
        &self,
        id: &str,
        read: &RecordRead,
        pages: usize,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Failure>,
    ) -> Result<(), Failure> {
        let mut continuation = read.continuation.clone();
        let mut checked = check::Checked::new(read);
        for _ in 0..pages {
            let body = encode::read(id, read, continuation.as_deref());
            let page = decode::page(self.records_post("conversation/read_records", body)?)?;
            checked.page(&page, continuation.as_deref())?;
            let next = page.continuation.clone();
            if !visit(page)? || next.is_none() {
                return Ok(());
            }
            continuation = next;
        }
        Err(Failure::Unreadable(format!(
            "a read that goes on past {pages} pages"
        )))
    }

    /// Every hit of one record search, in the order asked. This route does
    /// not page. The answer is checked against the request before a hit is
    /// trusted: its count, each hit's conversation, kind and time, the
    /// scores and the order.
    pub fn search_records(&self, query: &RecordQuery) -> Result<Vec<RecordHit>, Failure> {
        let data = self.records_post("conversation/search_records", encode::search(query))?;
        decode::hits(data, query)
    }

    /// One part of a restore, a probe, a commit or a discard.
    pub fn restore_records(&self, id: &str, restore: &Restore) -> Result<Restored, Failure> {
        let body = encode::restore(id, restore);
        decode::restored(self.records_post("store/restore_records", body)?)
    }

    /// One page of the stale-draft sweep; `write: false` previews.
    pub fn sweep_records(
        &self,
        limit: usize,
        cursor: Option<&str>,
        write: bool,
    ) -> Result<SweepPage, Failure> {
        let body = encode::sweep(limit, cursor, write);
        decode::swept(self.records_post("store/sweep_records", body)?)
    }
}

/// An answer read under this client's response budget before it is
/// parsed, keeping what the legacy path drops: the status and
/// `Retry-After`. A refusal whose body stalls or breaks off is still a
/// refusal, built from what arrived (ruling R41). A 2xx whose body stalls
/// or breaks off was answered, so the request may have been applied: it is
/// unreadable, its status in its words, never a lost answer that a caller
/// sends again (ruling G4). A 2xx must be a JSON object.
fn answered(
    mut response: ureq::http::Response<ureq::Body>,
    endpoint: &str,
    maximum: u64,
) -> Result<Value, Failure> {
    let status = response.status();
    let retry_after = response.headers().get("retry-after");
    let retry_after = retry_after.and_then(|value| value.to_str().ok().map(str::to_string));
    let mut bytes = Vec::new();
    let reader = response.body_mut().as_reader();
    let read = reader.take(maximum + 1).read_to_end(&mut bytes);
    if !status.is_success() {
        let retry_after = retry_after.as_deref();
        return Err(Failure::refused(status.as_u16(), retry_after, &bytes));
    }
    read.map_err(|error| {
        let status = status.as_u16();
        Failure::Unreadable(format!(
            "the {status} answer to {endpoint} broke off: {error}"
        ))
    })?;
    limits::check(limits::RESPONSE, bytes.len() as u64, maximum).map_err(Failure::Client)?;
    let data = serde_json::from_slice::<Value>(&bytes)
        .map_err(|error| Failure::Unreadable(error.to_string()))?;
    if !data.is_object() {
        return Err(Failure::Unreadable(endpoint.to_string()));
    }
    Ok(data)
}

//! Turn reads and version 1 history search from the client's side (DESIGN
//! §40.46, #417 to #419). Every request is first 2.16.0's. Only when it is
//! refused for an answer-byte budget on either side or the spool budget and
//! the server advertises `size_paging: {version: 1}` does the client ask
//! again in size pages, handing turns over one page at a time. A second size
//! refusal or a stale relevance continuation reports the first refusal.
//! Pages stay below the server's 1 MiB in-memory spool, so
//! they need none of its temporary-file budget.

use serde_json::{Value, json};

use super::codec;
use super::{Failed, RemoteStore};
use crate::conversation::{Error, Turn};
use crate::store::error::Error as StoreError;

/// The ceiling of a retried page: under the server's in-memory spool (1 MiB)
/// with room for the envelope, the scores and the continuation.
const PAGE_BYTES: u64 = 960 * 1024;

impl RemoteStore {
    /// Whether a refusal may be answered by asking again in size pages.
    pub(super) fn pages_may_answer(&self, failed: &Failed) -> bool {
        failed.sized && self.capabilities["size_paging"]["version"] == 1
    }

    /// Follow size pages of `endpoint` after `first` refused the unpaged
    /// request: `page` gets each answer and says whether to go on. A page
    /// refused for its size too, or a stale relevance continuation, reports
    /// `first`; any other failure reports itself (#419). A continuation must
    /// be a bounded string, and a page that names one must hold an item.
    pub(super) fn size_pages<E: From<StoreError>>(
        &self,
        endpoint: &str,
        mut payload: Value,
        items: &str,
        first: StoreError,
        mut page: impl FnMut(&Value) -> Result<bool, E>,
    ) -> Result<(), E> {
        payload["page_bytes"] = json!(PAGE_BYTES);
        loop {
            let data = match self.attempt(endpoint, payload.clone()) {
                Ok(data) => data,
                Err(failed) => {
                    let stale = payload["order"] == "relevance"
                        && payload["continuation"].is_string()
                        && matches!(&failed.error, StoreError::Value(message)
                            if message == crate::conversation::page::STALE);
                    return Err(if failed.sized || stale {
                        first
                    } else {
                        failed.error
                    }
                    .into());
                }
            };
            let held = codec::array(&data, items)?.len();
            let next = match codec::key(&data, "continuation")? {
                Value::Null => None,
                Value::String(token) if !token.is_empty() && token.len() <= 4096 && held > 0 => {
                    Some(token.clone())
                }
                _ => return Err(codec::malformed("continuation").into()),
            };
            if !page(&data)? {
                return Ok(());
            }
            let Some(token) = next else {
                return Ok(());
            };
            payload["continuation"] = json!(token);
        }
    }

    /// `read_turns` as 2.16.0 read it (pages of 500 turns through
    /// `next_after`), one turn at a time; a page refused for its size is
    /// read again from where it started in size pages.
    pub(super) fn read_turns_wire(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let mut payload = json!({"conversation_id": id, "after": after, "limit": limit});
        let total = payload["limit"].as_u64();
        let mut read = 0_u64;
        loop {
            let data = match self.attempt("conversation/read_turns", payload.clone()) {
                Ok(data) => data,
                Err(failed) if self.pages_may_answer(&failed) => {
                    let from = payload["after"].as_i64().unwrap_or(after);
                    let left = limit.map(|limit| limit - read as i64);
                    return self.read_turns_paged(id, from, left, failed.error, visit);
                }
                Err(failed) => return Err(failed.error.into()),
            };
            let page = codec::array(&data, "turns")?;
            let next = codec::key(&data, "next_after")?;
            for turn in page {
                read += 1;
                if !visit(codec::turn(turn)?)? {
                    return Ok(());
                }
            }
            if next.is_null() || page.is_empty() {
                return Ok(());
            }
            let remaining = total.map(|total| total.saturating_sub(read));
            if remaining == Some(0) {
                return Ok(());
            }
            if next.as_u64().is_none() {
                return Err(codec::malformed("next_after").into());
            }
            payload["after"] = next.clone();
            payload["limit"] = json!(remaining);
        }
    }

    /// `read_turns` in size pages: `next_after` is null in every page, and an
    /// explicit limit bounds the total.
    fn read_turns_paged(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        first: StoreError,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let body = json!({"conversation_id": id, "after": after, "limit": limit});
        let mut total = 0_i64;
        self.size_pages("conversation/read_turns", body, "turns", first, |data| {
            if !codec::key(data, "next_after")?.is_null() {
                return Err(codec::malformed("next_after").into());
            }
            for turn in codec::array(data, "turns")? {
                total += 1;
                if limit.is_some_and(|limit| total > limit) {
                    return Err(codec::malformed("turns").into());
                }
                if !visit(codec::turn(turn)?)? {
                    return Ok(false);
                }
            }
            Ok(true)
        })
    }

    /// Version 1 history search, one turn at a time: 2.16.0's one request,
    /// or, when that is refused for its size, size pages.
    pub(super) fn search_turns_wire(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let body = json!({"query": terms.join(" "), "conversations": ids, "limit": limit});
        let data = match self.attempt("conversation/search_turns", body.clone()) {
            Ok(data) => data,
            Err(failed) if self.pages_may_answer(&failed) => {
                let mut total = 0;
                let endpoint = "conversation/search_turns";
                return self.size_pages(endpoint, body, "turns", failed.error, |data| {
                    for turn in codec::array(data, "turns")? {
                        total += 1;
                        if total > limit {
                            return Err(codec::malformed(endpoint).into());
                        }
                        if !visit(codec::turn(turn)?)? {
                            return Ok(false);
                        }
                    }
                    Ok(true)
                });
            }
            Err(failed) => return Err(failed.error.into()),
        };
        for turn in codec::array(&data, "turns")? {
            if !visit(codec::turn(turn)?)? {
                break;
            }
        }
        Ok(())
    }
}

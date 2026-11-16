//! Size paging version 1 from the client's side (DESIGN §40.46): against a
//! server that advertises it, turn reads and history searches ask for pages
//! of at most `PAGE_BYTES` and follow each continuation, handing turns over
//! one page at a time. Without it, every request is 2.16.0's.

use serde_json::{Value, json};

use super::RemoteStore;
use super::codec;
use crate::conversation::{Error, Turn};
use crate::store::error::Error as StoreError;

/// The ceiling this client asks for; a server may serve less.
pub const PAGE_BYTES: u64 = 1 << 20;

impl RemoteStore {
    pub(super) fn supports_size_paging(&self) -> bool {
        self.capabilities["size_paging"]["version"] == 1
    }

    /// Follow size pages of `endpoint`: `page` gets each answer and says
    /// whether to go on. A continuation must be a bounded string, and a page
    /// that names one must hold at least one item.
    pub(super) fn size_pages<E: From<StoreError>>(
        &self,
        endpoint: &str,
        mut payload: Value,
        items: &str,
        mut page: impl FnMut(&Value) -> Result<bool, E>,
    ) -> Result<(), E> {
        payload["page_bytes"] = json!(PAGE_BYTES);
        loop {
            let data = self.call(endpoint, payload.clone())?;
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

    /// `read_turns` in size pages: `next_after` is null in every page, and an
    /// explicit limit bounds the total.
    pub(super) fn read_turns_paged(
        &self,
        id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let body = json!({"conversation_id": id, "after": after, "limit": limit});
        let mut total = 0_i64;
        self.size_pages("conversation/read_turns", body, "turns", |data| {
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

    /// Version 1 history search, one turn at a time: in size pages when the
    /// server pages, else the one 2.16.0 request.
    pub(super) fn search_turns_wire(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let body = json!({"query": terms.join(" "), "conversations": ids, "limit": limit});
        let paged = self.supports_size_paging();
        let mut total = 0;
        let mut each = |data: &Value| -> Result<bool, Error> {
            for turn in codec::array(data, "turns")? {
                total += 1;
                if paged && total > limit {
                    return Err(codec::malformed("conversation/search_turns").into());
                }
                if !visit(codec::turn(turn)?)? {
                    return Ok(false);
                }
            }
            Ok(true)
        };
        if paged {
            return self.size_pages("conversation/search_turns", body, "turns", each);
        }
        each(&self.call("conversation/search_turns", body)?).map(|_| ())
    }
}

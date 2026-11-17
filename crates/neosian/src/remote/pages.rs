//! Version 1 history search from the client's side, one turn at a time from
//! 2.16.0's one request. The binary's client never sends size-paging fields
//! (DESIGN §40.46, #417): a search page repeats the server's scan, and turn
//! pages measured 8 to 14% slower than 2.16.0's reads on Postgres, while the
//! server spools every answer anyway. Every request is 2.16.0's.

use serde_json::json;

use super::RemoteStore;
use super::codec;
use crate::conversation::{Error, Turn};

impl RemoteStore {
    pub(super) fn search_turns_wire(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let body = json!({"query": terms.join(" "), "conversations": ids, "limit": limit});
        let data = self.call("conversation/search_turns", body)?;
        for turn in codec::array(&data, "turns")? {
            if !visit(codec::turn(turn)?)? {
                break;
            }
        }
        Ok(())
    }
}

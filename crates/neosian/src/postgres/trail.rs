//! One conversation's erasure trail in the order its acts were stored, a
//! page at a time, for a transfer (DESIGN §40.54 item 22). A page is keyed
//! by the table's own identity, which an erasure and a restore assign in
//! the order they insert. The audit read `turn_redactions` answers newest
//! first by time, at most a limit, and stays as it is.

use super::{PostgresStore, sql};
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::{Error, Redaction};
use crate::remote::codec;
use serde_json::json;

impl PostgresStore {
    /// At most `limit` acts of one conversation's trail after the identity
    /// `after` (0 before the first act), in the order they were stored, and
    /// the identity the next page continues after when this page is full.
    pub fn trail_page(
        &self,
        id: &str,
        after: u64,
        limit: usize,
    ) -> Result<(Vec<Redaction>, Option<u64>), Error> {
        parse_conversation_id(id)?;
        if limit < 1 {
            return Err(Error::Value(format!("limit must be >= 1, got {limit}")));
        }
        let params = json!({"conversation_id": id, "after": after, "limit": limit});
        let rows = self.fetch(sql!("erasure/read_turn_trail"), params)?;
        let mut last = after;
        let mut acts = Vec::with_capacity(rows.len());
        for row in &rows {
            last = codec::number(row, "id")?;
            acts.push(codec::turn_redaction(row)?);
        }
        Ok((acts, (rows.len() == limit).then_some(last)))
    }
}

#[cfg(test)]
#[path = "trail_tests.rs"]
mod tests;

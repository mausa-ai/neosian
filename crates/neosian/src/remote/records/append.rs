//! Two calls of the lander beside the records routes (DESIGN §40.54 items
//! 15.2 and 17.4, rulings D7 and D8 of the T1e1 plan).
//!
//! Against a server without turn records the lander sends each turn as one
//! `append_turn`: the same value through the same `send_json` as the legacy
//! call (`remote/state.rs`), so its body is the released bytes, indented by
//! two spaces, and the turn is decoded as the legacy call decodes it. Only
//! its failure differs: typed as a records call's, so the rows of 17.4 read
//! its status, `Retry-After`, code and details. The legacy path itself is
//! untouched.
//!
//! The first turn's time, which a sessions document names, comes from that
//! turn's header alone: a records read with `kinds: []` in pages of one
//! byte, stopped after its first page, so a long first turn that
//! `read_turns` refuses (item 15.1) never stops a document and is never
//! transferred whole.

use serde_json::{Value, json};

use super::{Failure, RecordPage, answered};
use crate::conversation::Turn;
use crate::conversation::message::{self, Message};
use crate::conversation::turn_records::RecordRead;
use crate::limits;
use crate::remote::{RemoteStore, codec, driver};
use crate::store::time::Stamp;

impl RemoteStore {
    /// One `append_turn` with the legacy call's request bytes, refused, lost
    /// or unreadable as a records call is.
    pub fn append_turn_kept(
        &self,
        id: &str,
        messages: &[Message],
        actor: Option<&str>,
    ) -> Result<Turn, Failure> {
        let messages: Vec<Value> = messages.iter().map(message::to_json).collect();
        let payload = json!({"conversation_id":id,"messages":messages,"actor":actor});
        let endpoint = "conversation/append_turn";
        let authorization = self.authorization.header().map_err(Failure::Client)?;
        let maximum = limits::response_bytes().map_err(Failure::Client)?;
        let response = self
            .agent
            .post(format!("{}/v1/{endpoint}", self.base))
            .header("Authorization", authorization)
            .send_json(payload)
            .map_err(|error| Failure::Transport(driver(error)))?;
        let data = answered(response, endpoint, maximum)?;
        let turn = data.get("turn").ok_or(Failure::Unreadable("turn".into()))?;
        codec::turn(turn).map_err(|_| Failure::Unreadable("turn".into()))
    }

    /// The time of the conversation's first turn from its header: one
    /// header per page, and the read ends with the first page. `None` for a
    /// conversation without turns.
    pub fn first_turn_time(&self, id: &str) -> Result<Option<Stamp>, Failure> {
        let read = RecordRead {
            kinds: Some(Vec::new()),
            page_bytes: 1,
            ..RecordRead::default()
        };
        let mut first = None;
        self.read_records(id, &read, &mut |page: RecordPage| {
            first = page.turns.first().map(|header| header.created_at);
            Ok(false)
        })?;
        Ok(first)
    }
}

//! The stale-draft sweep's page and its method-scoped keyset cursor.
use serde::Serialize;
use serde_json::json;

use crate::conversation::Error;
use crate::conversation::ids::check_conversation_id;
use crate::store::cursor::{self, Kind};

const METHOD: &str = "sweep-v1";

#[derive(Debug, Serialize)]
pub struct SweepConversation {
    pub conversation_id: String,
    pub drafts: usize,
    pub restores: usize,
    pub freed_bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct SweepPage {
    pub conversations: Vec<SweepConversation>,
    pub next_cursor: Option<String>,
    pub write: bool,
}

/// Validate the request before a store visits any conversation.
pub(crate) fn start_after(limit: usize, cursor: Option<&str>) -> Result<Option<String>, Error> {
    if !(1..=100).contains(&limit) {
        return Err(Error::Value("sweep limit must be in 1..100".into()));
    }
    let Some(cursor) = cursor else {
        return Ok(None);
    };
    if !(1..=4096).contains(&cursor.len()) {
        return Err(Error::Value("sweep cursor must be 1 to 4096 bytes".into()));
    }
    let parts = cursor::decode(METHOD, cursor, &[Kind::Str]).map_err(Error::Value)?;
    let id = parts[0].as_str().expect("decoded string");
    check_conversation_id(id).map_err(Error::Value)?;
    Ok(Some(id.to_owned()))
}

pub(crate) fn cursor_after(id: &str) -> String {
    cursor::encode(METHOD, &[json!(id)])
}

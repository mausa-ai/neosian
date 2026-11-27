//! The five request bodies, written key by key in the order of wire.md's
//! examples. A null record key and a field at its default are left out, so
//! a body is as short as the route allows. A body is built once as text:
//! beside it the encoder holds one item's encoding at a time. The lander
//! and the archive import pack parts by measuring these same encodings.

use serde::Serialize;

use super::RecordQuery;
use crate::conversation::turn_records::{Form, Header, RecordRead, Restore, TurnRecord, Write};
use crate::conversation::{Projection, Redaction};
use crate::wire::encode::{projection, turn_redaction};

/// One JSON object being written.
struct Object(String);

impl Object {
    fn new() -> Self {
        Self(String::from("{"))
    }
    /// A key and its value as JSON text already encoded.
    fn raw(&mut self, key: &str, json: &str) {
        if self.0.len() > 1 {
            self.0.push(',');
        }
        self.0.push('"');
        self.0.push_str(key);
        self.0.push_str("\":");
        self.0.push_str(json);
    }
    fn put<T: Serialize + ?Sized>(&mut self, key: &str, value: &T) {
        self.raw(key, &text(value));
    }
    fn some<T: Serialize>(&mut self, key: &str, value: &Option<T>) {
        if let Some(value) = value {
            self.put(key, value);
        }
    }
    fn flag(&mut self, key: &str, value: bool) {
        if value {
            self.raw(key, "true");
        }
    }
    /// A key and a list of items already encoded, written in place.
    fn items(&mut self, key: &str, items: impl Iterator<Item = String>) {
        self.raw(key, "[");
        for (at, item) in items.enumerate() {
            if at > 0 {
                self.0.push(',');
            }
            self.0.push_str(&item);
        }
        self.0.push(']');
    }
    fn end(mut self) -> String {
        self.0.push('}');
        self.0
    }
}

fn text<T: Serialize + ?Sized>(value: &T) -> String {
    serde_json::to_string(value).expect("JSON values encode")
}

/// A record in write shape, or with `turn` in restore shape.
pub fn record(turn: Option<u64>, record: &TurnRecord) -> String {
    let mut out = Object::new();
    out.some("turn", &turn);
    out.put("seq", &record.seq);
    out.put("message", &record.message);
    out.put("role", record.role.as_str());
    out.put("kind", record.kind.as_str());
    out.some("content", &record.content.map(Form::as_str));
    out.some("block", &record.block);
    out.some("call", &record.call);
    out.some("call_id", &record.call_id);
    out.some("name", &record.name);
    out.put("chunk", &record.chunk);
    out.put("size", &record.size);
    out.put("text", &record.text);
    out.end()
}

/// `conversation/write_records`. The actor is the one the caller named,
/// unstamped; the server stamps it under the token's client.
pub fn write(id: &str, write: &Write) -> String {
    let mut out = Object::new();
    out.put("conversation_id", id);
    out.put("draft", &write.draft);
    out.some("actor", &write.actor);
    out.some("turn_bytes", &write.turn_bytes);
    out.flag("seal", write.seal);
    out.flag("discard", write.discard);
    out.items("records", write.records.iter().map(|one| record(None, one)));
    out.end()
}

/// `conversation/read_records` for one page: the selection, and the
/// continuation of the page before it.
pub fn read(id: &str, read: &RecordRead, continuation: Option<&str>) -> String {
    let mut out = Object::new();
    out.put("conversation_id", id);
    out.some("turn", &read.turn);
    if read.after > 0 {
        out.put("after", &read.after);
    }
    out.some("through", &read.through);
    out.some("seq", &read.seq);
    out.flag("piece", read.piece);
    if let Some(kinds) = &read.kinds {
        out.items("kinds", kinds.iter().map(|kind| text(kind.as_str())));
    }
    if !read.heads.is_empty() {
        let mut heads = Object::new();
        for (kind, bytes) in &read.heads {
            heads.put(kind.as_str(), bytes);
        }
        out.raw("heads", &heads.end());
    }
    if read.page_bytes != RecordRead::default().page_bytes {
        out.put("page_bytes", &read.page_bytes);
    }
    out.some("continuation", &continuation);
    out.end()
}

/// `conversation/search_records`, the query verbatim.
pub fn search(query: &RecordQuery) -> String {
    let mut out = Object::new();
    out.put("query", &query.query);
    out.some("conversations", &query.conversations);
    if let Some(kinds) = &query.kinds {
        out.items("kinds", kinds.iter().map(|kind| text(kind.as_str())));
    }
    out.some("order", &query.order.map(|order| order.name()));
    out.some("since", &query.since);
    out.some("before", &query.before);
    out.some("limit", &query.limit);
    out.end()
}

/// A turn header as a restore part carries it.
pub fn header(header: &Header) -> String {
    text(&Header::to_json(header))
}

/// A projection as a restore part carries it.
pub fn entry(entry: &Projection) -> String {
    text(&projection(entry))
}

/// A trail act as a restore part carries it.
pub fn act(act: &Redaction) -> String {
    text(&turn_redaction(act))
}

/// `store/restore_records`: a list only when it holds an item or its place
/// is named, so a probe carries the two ids alone. The archive import packs
/// parts by measuring these same encodings.
pub fn restore(id: &str, restore: &Restore) -> String {
    let mut out = Object::new();
    out.put("conversation_id", id);
    out.put("restore", &restore.restore);
    if !restore.turns.is_empty() {
        out.items("turns", restore.turns.iter().map(header));
    }
    if !restore.records.is_empty() {
        let records = restore.records.iter();
        out.items(
            "records",
            records.map(|(turn, one)| record(Some(*turn), one)),
        );
    }
    out.some("projections_from", &restore.projections_from);
    if restore.projections_from.is_some() || !restore.projections.is_empty() {
        out.items("projections", restore.projections.iter().map(entry));
    }
    out.some("redactions_from", &restore.redactions_from);
    if restore.redactions_from.is_some() || !restore.redactions.is_empty() {
        out.items("redactions", restore.redactions.iter().map(act));
    }
    out.flag("commit", restore.commit);
    out.flag("discard", restore.discard);
    out.end()
}

/// `store/sweep_records`.
pub fn sweep(limit: usize, cursor: Option<&str>, write: bool) -> String {
    let mut out = Object::new();
    out.put("limit", &limit);
    out.some("cursor", &cursor);
    out.flag("write", write);
    out.end()
}

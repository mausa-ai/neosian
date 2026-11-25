//! Logical byte charges for record writes and restores (§40.54 item 14).
//! A host can reserve for every potentially new item and settle with the
//! actual stored and freed bytes. The store measures UTF-8 fields and uses
//! fixed row allowances. Physical disk capacity is measured separately.

use super::TurnRecord;

/// The rule for one conversation and one draft or restore id.
#[derive(Clone, Copy, Debug)]
pub struct Sizes {
    /// `u(conversation_id)`.
    c: u64,
    /// `u(draft)` or `u(restore)`.
    o: u64,
}

fn u(text: Option<&str>) -> u64 {
    text.map_or(0, |text| text.len() as u64)
}

impl Sizes {
    pub fn new(conversation_id: &str, writer: &str) -> Self {
        Self {
            c: conversation_id.len() as u64,
            o: writer.len() as u64,
        }
    }

    /// `u(text) + u(call_id) + u(name) + 2c + 256`.
    pub fn record(&self, record: &TurnRecord) -> u64 {
        let named = u(record.call_id.as_deref()) + u(record.name.as_deref());
        record.text.len() as u64 + named + 2 * self.c + 256
    }

    /// A seal, or a restored turn not redacted: `u(actor) + o + 4c + 512`.
    pub fn header(&self, actor: Option<&str>) -> u64 {
        u(actor) + self.o + 4 * self.c + 512
    }

    /// A restored redacted turn: `u(actor) + 2c + 256`.
    pub fn redacted_header(&self, actor: Option<&str>) -> u64 {
        u(actor) + 2 * self.c + 256
    }

    /// `u(text) + 2c + 128`.
    pub fn projection(&self, text: &str) -> u64 {
        text.len() as u64 + 2 * self.c + 128
    }

    /// `u(actor) + 4 * len(turns) + 2c + 128`.
    pub fn act(&self, actor: Option<&str>, turns: usize) -> u64 {
        u(actor) + 4 * turns as u64 + 2 * self.c + 128
    }

    /// The first request of a draft or restore, freed at seal, commit or
    /// discard: `o + 2c + 1024`.
    pub fn bookkeeping(&self) -> u64 {
        self.o + 2 * self.c + 1024
    }

    /// A seal or commit that creates the conversation: `c + 256`.
    pub fn conversation_row(&self) -> u64 {
        self.c + 256
    }

    /// A commit's marker, kept until the conversation is erased:
    /// `o + 2c + 256`.
    pub fn committed_marker(&self) -> u64 {
        self.o + 2 * self.c + 256
    }
}

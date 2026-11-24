//! A draft's records checked as they land (§40.54 item 9 "Checks"): each
//! record alone, then against the draft, by the rules `messages_of` applies
//! to a whole turn, so a turn a seal makes visible always rebuilds. The
//! state between parts is a few integers, the message's `tool_call_id` (at
//! most 1,024 bytes) and an open JSON piece's check; never a record's text.
//! Stores keep it beside the draft (a FileStore's state file, Postgres's
//! `position`) and clone it before a request, so a refused request changes
//! nothing.

use serde::{Deserialize, Serialize};

use super::json::{JsonCheck, Shape, Top, depth};
use super::{Form, Kind, TurnRecord};

mod message;

use message::Message;

/// Why a record was refused.
#[derive(Debug, PartialEq, Eq)]
pub enum Refusal {
    /// Out of its bounds, its shape or its place: the request's fault.
    Invalid(String),
    /// Past the total the draft stated (`draft_size`).
    Stated { stated: u64, bytes: u64 },
    /// Past the store's bound on one turn.
    Bound { bytes: u64, maximum: u64 },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
struct Piece {
    kind: Kind,
    block: Option<u64>,
    call: Option<u64>,
    call_id: Option<String>,
    name: Option<String>,
    size: u64,
    filled: u64,
    chunks: u64,
    json: Option<JsonCheck>,
}

/// The draft's position: what the next record must continue.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DraftCheck {
    next_seq: u64,
    bytes: u64,
    message: Option<Message>,
    piece: Option<Piece>,
}

fn invalid(seq: u64, why: &str) -> Refusal {
    Refusal::Invalid(format!("record {seq}: {why}"))
}

impl DraftCheck {
    /// Records accepted so far.
    pub fn next_seq(&self) -> u64 {
        self.next_seq
    }

    /// The `size` of every piece begun: the sealed turn's `bytes`.
    pub fn bytes(&self) -> u64 {
        self.bytes
    }

    /// The next record, `stated` the draft's `turn_bytes`, `maximum` the
    /// store's bound on one turn. A refused record changes nothing.
    pub fn admit(
        &mut self,
        record: &TurnRecord,
        stated: Option<u64>,
        maximum: u64,
    ) -> Result<(), Refusal> {
        let mut next = self.clone();
        next.take(record, stated, maximum)?;
        *self = next;
        Ok(())
    }

    fn take(
        &mut self,
        record: &TurnRecord,
        stated: Option<u64>,
        maximum: u64,
    ) -> Result<(), Refusal> {
        let seq = record.seq;
        if seq != self.next_seq {
            return Err(invalid(
                seq,
                &format!("stands where record {} belongs", self.next_seq),
            ));
        }
        record.check().map_err(Refusal::Invalid)?;
        let text = record.text.len() as u64;
        if let Some(piece) = self.piece.as_mut() {
            let same = (record.kind, record.block, record.call, record.size)
                == (piece.kind, piece.block, piece.call, piece.size)
                && (&record.call_id, &record.name) == (&piece.call_id, &piece.name);
            let message = self.message.as_ref().map(|m| (m.index, m.role.as_str()));
            if record.chunk != piece.chunks
                || !same
                || record.text.is_empty()
                || message != Some((record.message, record.role.as_str()))
            {
                return Err(invalid(seq, "not the next chunk of its piece"));
            }
            piece.filled = piece.filled.saturating_add(text);
            piece.chunks += 1;
            if piece.filled > piece.size {
                return Err(invalid(
                    seq,
                    &format!("its piece passes size {}", piece.size),
                ));
            }
            if let Some(json) = piece.json.as_mut() {
                json.feed(&record.text).map_err(|why| invalid(seq, &why))?;
            }
        } else {
            if record.chunk != 0 {
                return Err(invalid(
                    seq,
                    &format!("chunk {} continues no piece", record.chunk),
                ));
            }
            self.place(record)?;
            self.bytes = self.bytes.checked_add(record.size).ok_or_else(|| {
                invalid(
                    seq,
                    &format!("size {} overflows the draft's bytes", record.size),
                )
            })?;
            if let Some(stated) = stated.filter(|stated| self.bytes > *stated) {
                return Err(Refusal::Stated {
                    stated,
                    bytes: self.bytes,
                });
            }
            if self.bytes > maximum {
                return Err(Refusal::Bound {
                    bytes: self.bytes,
                    maximum,
                });
            }
            let mut json = Shape::of(record.kind)
                .map(|shape| JsonCheck::new(shape, depth(record.kind, record.call)));
            if let Some(json) = json.as_mut() {
                json.feed(&record.text).map_err(|why| invalid(seq, &why))?;
            }
            self.piece = Some(Piece {
                kind: record.kind,
                block: record.block,
                call: record.call,
                call_id: record.call_id.clone(),
                name: record.name.clone(),
                size: record.size,
                filled: text,
                chunks: 1,
                json,
            });
        }
        let complete = self
            .piece
            .as_ref()
            .is_some_and(|piece| piece.filled == piece.size);
        if let Some((kind, json)) = self
            .piece
            .take_if(|_| complete)
            .and_then(|piece| Some(piece.kind).zip(piece.json))
        {
            let top = json.finish().map_err(|why| invalid(seq, &why))?;
            match (kind, top) {
                (Kind::Extra, Top::Null | Top::Falsy) => {
                    return Err(invalid(seq, "the codec leaves an empty extra out"));
                }
                (Kind::Reasoning, Top::Null) => {
                    return Err(invalid(seq, "the codec leaves a null reasoning out"));
                }
                _ => {}
            }
        }
        self.next_seq += 1;
        Ok(())
    }

    /// Whether the most recently admitted record finished its piece (the
    /// Postgres record row's `last` column).
    pub fn piece_complete(&self) -> bool {
        self.piece.is_none()
    }

    /// Whether the draft can become a turn: a record at least, its last
    /// piece complete, its last message whole.
    pub fn sealable(&self) -> Result<(), String> {
        if self.next_seq == 0 {
            return Err("a seal needs at least one record in the draft".into());
        }
        if self.piece.is_some() {
            return Err(format!(
                "record {}: the draft ends inside a piece",
                self.next_seq - 1
            ));
        }
        match &self.message {
            Some(message) if message.form == Form::Text && !message.text => Err(format!(
                "record {}: text content needs its piece",
                self.next_seq - 1
            )),
            _ => Ok(()),
        }
    }

    /// A record beginning a piece: a new message or the next piece of this one.
    fn place(&mut self, record: &TurnRecord) -> Result<(), Refusal> {
        let seq = record.seq;
        let opens = self
            .message
            .as_ref()
            .is_none_or(|m| m.index != record.message);
        if opens {
            let expected = self.message.as_ref().map_or(0, |m| m.index + 1);
            if record.message != expected {
                let why = format!(
                    "message {} where message {expected} is next",
                    record.message
                );
                return Err(invalid(seq, &why));
            }
            if self.next_seq > 0 {
                self.sealable().map_err(Refusal::Invalid)?;
            }
            let Some(form) = record.content else {
                return Err(invalid(
                    seq,
                    "a message's first record carries its content form",
                ));
            };
            self.message = Some(Message::new(record.message, record.role, form));
        } else if record.content.is_some() {
            return Err(invalid(
                seq,
                "only a message's first record carries content",
            ));
        }
        let message = self.message.as_mut().expect("a message is open");
        if message.role != record.role.as_str() {
            return Err(invalid(seq, "a message has one role"));
        }
        message.place(record)
    }
}

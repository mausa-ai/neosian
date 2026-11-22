//! Records back to a turn's messages (§40.54 item 3.3): chunks joined into
//! pieces whatever the split, pieces read in the order the codec writes
//! them, anything out of place refused with the record it was found at.

use std::iter::Peekable;
use std::slice::Iter;

use serde_json::Value;

use super::{Form, Kind, TurnRecord};
use crate::conversation::message::{self, Block, Content, Message, ToolCall, Undecodable};

/// A turn's messages from its records, in `seq` order from 0.
pub fn messages_of(records: &[TurnRecord]) -> Result<Vec<Message>, String> {
    if records.is_empty() {
        return Err("a turn stored as records holds at least one record".into());
    }
    for (at, record) in records.iter().enumerate() {
        if record.seq != at as u64 {
            return Err(format!(
                "record {} stands where record {at} belongs",
                record.seq
            ));
        }
        record.check()?;
    }
    let (mut messages, mut start) = (Vec::new(), 0);
    while start < records.len() {
        let index = records[start].message;
        if index != messages.len() as u64 {
            let next = messages.len();
            return Err(format!(
                "record {start}: message {index} where message {next} is next"
            ));
        }
        let end = records[start..]
            .iter()
            .position(|record| record.message != index)
            .map_or(records.len(), |count| start + count);
        messages.push(message(&records[start..end])?);
        start = end;
    }
    Ok(messages)
}

/// One piece: its first record, its text joined, its chunk count.
struct Piece<'a> {
    head: &'a TurnRecord,
    text: String,
    chunks: u64,
}

impl Piece<'_> {
    fn json(&self) -> Result<Value, String> {
        serde_json::from_str(&self.text).map_err(|error| {
            let (seq, kind) = (self.head.seq, self.head.kind.as_str());
            format!("record {seq}: the {kind} piece is not JSON ({error})")
        })
    }
}

fn pieces(records: &[TurnRecord]) -> Result<Vec<Piece<'_>>, String> {
    let mut pieces: Vec<Piece<'_>> = Vec::new();
    let mut open = false;
    for record in records {
        let seq = record.seq;
        if record.chunk == 0 {
            if open {
                return Err(format!("record {seq}: the piece before it is incomplete"));
            }
            let text = record.text.clone();
            pieces.push(Piece {
                head: record,
                text,
                chunks: 1,
            });
        } else {
            let piece = pieces.last_mut().filter(|_| open);
            let Some(piece) = piece else {
                return Err(format!(
                    "record {seq}: chunk {} continues no piece",
                    record.chunk
                ));
            };
            let head = piece.head;
            let same = (record.kind, record.block, record.call, record.size)
                == (head.kind, head.block, head.call, head.size)
                && (&record.call_id, &record.name) == (&head.call_id, &head.name);
            if record.chunk != piece.chunks || !same || record.text.is_empty() {
                return Err(format!("record {seq}: not the next chunk of its piece"));
            }
            piece.text.push_str(&record.text);
            piece.chunks += 1;
        }
        let piece = pieces.last().expect("a piece was just written");
        let length = piece.text.len() as u64;
        if length > piece.head.size {
            return Err(format!(
                "record {seq}: its piece passes size {}",
                piece.head.size
            ));
        }
        open = length < piece.head.size;
    }
    if open {
        let seq = records.last().map_or(0, |record| record.seq);
        return Err(format!("record {seq}: the message ends inside a piece"));
    }
    Ok(pieces)
}

fn undecodable(seq: u64, error: Undecodable) -> String {
    let why = match error {
        Undecodable::Key(key) => format!("no {key:?}"),
        Undecodable::Value(why) | Undecodable::Type(why) => why,
    };
    format!("record {seq}: the attachment is not a content block ({why})")
}

/// The next piece when `fits` says it belongs here.
fn next_if<'p, 'a>(
    queue: &mut Peekable<Iter<'p, Piece<'a>>>,
    fits: impl Fn(&TurnRecord) -> bool,
) -> Option<&'p Piece<'a>> {
    queue.next_if(|piece| fits(piece.head))
}

fn message(records: &[TurnRecord]) -> Result<Message, String> {
    let head = &records[0];
    let role = head.role;
    if let Some(other) = records.iter().find(|record| record.role != role) {
        return Err(format!("record {}: a message has one role", other.seq));
    }
    let form = head.content.ok_or(format!(
        "record {}: a message's first record carries its content form",
        head.seq
    ))?;
    if let Some(other) = records[1..].iter().find(|record| record.content.is_some()) {
        return Err(format!(
            "record {}: only a message's first record carries content",
            other.seq
        ));
    }
    let pieces = pieces(records)?;
    let lead = pieces[0].head;
    let id = lead.call_id.clone().filter(|_| lead.kind != Kind::ToolCall);
    for (at, piece) in pieces.iter().enumerate() {
        let expected = match piece.head.kind {
            Kind::ToolCall => continue,
            Kind::ToolResult => id.as_deref(),
            _ if at == 0 => id.as_deref(),
            _ => None,
        };
        if piece.head.call_id.as_deref() != expected {
            return Err(format!(
                "record {}: call_id does not match its message",
                piece.head.seq
            ));
        }
    }
    let own = Kind::of_role(role);
    let mut queue = pieces.iter().peekable();
    if form != Form::Text {
        next_if(&mut queue, |r| {
            r.kind == own && r.block.is_none() && r.size == 0
        });
    }
    let content = match form {
        Form::None => None,
        Form::Text => match next_if(&mut queue, |r| r.kind == own && r.block.is_none()) {
            Some(piece) => Some(Content::Text(piece.text.clone())),
            None => return Err(format!("record {}: text content needs its piece", head.seq)),
        },
        Form::Blocks => {
            let mut blocks = Vec::new();
            while let Some(piece) = next_if(&mut queue, |r| r.block == Some(blocks.len() as u64)) {
                blocks.push(if piece.head.kind == own {
                    Block::Text(piece.text.clone())
                } else {
                    let seq = piece.head.seq;
                    match message::block(&piece.json()?).map_err(|e| undecodable(seq, e))? {
                        Block::Text(_) => {
                            return Err(format!(
                                "record {seq}: an attachment holds a block other than text"
                            ));
                        }
                        block => block,
                    }
                });
            }
            Some(Content::Blocks(blocks))
        }
    };
    let mut tool_calls = Vec::new();
    while let Some(piece) = next_if(&mut queue, |r| {
        r.kind == Kind::ToolCall && r.call == Some(tool_calls.len() as u64)
    }) {
        let arguments = piece.json()?;
        if !arguments.is_object() {
            return Err(format!(
                "record {}: arguments must be an object",
                piece.head.seq
            ));
        }
        let at = Some(tool_calls.len() as u64);
        let extra = next_if(&mut queue, |r| r.kind == Kind::Extra && r.call == at);
        tool_calls.push(ToolCall {
            id: piece.head.call_id.clone().unwrap_or_default(),
            name: piece.head.name.clone().unwrap_or_default(),
            arguments,
            extra: extra.map_or(Ok(Value::Null), Piece::json)?,
        });
    }
    let reasoning = next_if(&mut queue, |r| r.kind == Kind::Reasoning);
    let extra = next_if(&mut queue, |r| r.kind == Kind::Extra && r.call.is_none());
    if let Some(piece) = queue.next() {
        let (seq, kind) = (piece.head.seq, piece.head.kind.as_str());
        return Err(format!("record {seq}: a {kind} piece out of place"));
    }
    Ok(Message {
        role,
        content,
        reasoning: reasoning.map_or(Ok(Value::Null), Piece::json)?,
        tool_calls,
        tool_call_id: id.filter(|id| !id.is_empty()),
        extra: extra.map_or(Ok(Value::Null), Piece::json)?,
    })
}

//! A turn's messages to records (§40.54 item 3.3, with the implementation
//! note on `tool_call_id`): per message its content, each tool call and
//! that call's extra, its reasoning, its extra; a piece longer than the
//! bound as several chunks; JSON pieces in the codec's compact form.

use super::split::split;
use super::{Form, ID_BYTES, Kind, TurnRecord};
use crate::conversation::message::{Block, Content, Message, Role, block_json, falsy};
use crate::pyfmt::dumps;

/// Every record of a turn, in `seq` order.
pub fn records_of(messages: &[Message]) -> Result<Vec<TurnRecord>, String> {
    let mut records = Vec::new();
    each_record(messages, &mut |record| records.push(record))?;
    Ok(records)
}

/// Every record of a turn to `visit`, in `seq` order. A message the
/// records cannot carry (an id or a name over 1,024 bytes) refuses the
/// turn before any record is visited.
pub fn each_record(messages: &[Message], visit: &mut dyn FnMut(TurnRecord)) -> Result<(), String> {
    for (index, message) in messages.iter().enumerate() {
        let ids = message.tool_call_id.iter().map(|id| ("tool_call_id", id));
        let calls = message
            .tool_calls
            .iter()
            .flat_map(|call| [("call id", &call.id), ("tool name", &call.name)]);
        if let Some((what, _)) = ids.chain(calls).find(|(_, id)| id.len() > ID_BYTES) {
            return Err(format!(
                "message {index} has a {what} longer than {ID_BYTES} bytes, which records cannot carry"
            ));
        }
    }
    let mut out = Out { visit, seq: 0 };
    for (index, message) in messages.iter().enumerate() {
        out.message(index as u64, message);
    }
    Ok(())
}

struct Out<'v> {
    visit: &'v mut dyn FnMut(TurnRecord),
    seq: u64,
}

/// Where a piece sits and what it names, beside its kind.
#[derive(Clone, Copy, Default)]
struct Place<'a> {
    block: Option<u64>,
    call: Option<u64>,
    call_id: Option<&'a str>,
    name: Option<&'a str>,
}

/// The message being written: its first piece carries the content form
/// on its first record and the message's `tool_call_id` on every record.
struct Current<'a> {
    index: u64,
    role: Role,
    first: Option<(Form, Option<&'a str>)>,
}

impl Out<'_> {
    fn message(&mut self, index: u64, message: &Message) {
        let role_kind = Kind::of_role(message.role);
        let (form, pieces) = match &message.content {
            None => (Form::None, 0),
            Some(Content::Text(_)) => (Form::Text, 1),
            Some(Content::Blocks(blocks)) => (Form::Blocks, blocks.len()),
        };
        let id = message.tool_call_id.as_deref();
        let rest = !message.tool_calls.is_empty()
            || !message.reasoning.is_null()
            || !falsy(&message.extra);
        let led_by_call = !message.tool_calls.is_empty() && id.is_some();
        let mut current = Current {
            index,
            role: message.role,
            first: Some((form, id)),
        };
        if pieces == 0 && (!rest || led_by_call) {
            self.piece(&mut current, role_kind, Place::default(), "");
        }
        let result = Place {
            call_id: id.filter(|_| message.role == Role::Tool),
            ..Place::default()
        };
        match &message.content {
            None => {}
            Some(Content::Text(text)) => self.piece(&mut current, role_kind, result, text),
            Some(Content::Blocks(blocks)) => {
                for (at, block) in blocks.iter().enumerate() {
                    let block_at = Some(at as u64);
                    match block {
                        Block::Text(text) => {
                            let place = Place {
                                block: block_at,
                                ..result
                            };
                            self.piece(&mut current, role_kind, place, text);
                        }
                        other => {
                            let place = Place {
                                block: block_at,
                                ..Place::default()
                            };
                            let text = dumps(&block_json(other), false);
                            self.piece(&mut current, Kind::Attachment, place, &text);
                        }
                    }
                }
            }
        }
        for (at, call) in message.tool_calls.iter().enumerate() {
            let place = Place {
                call: Some(at as u64),
                call_id: Some(&call.id),
                name: Some(&call.name),
                ..Place::default()
            };
            self.piece(
                &mut current,
                Kind::ToolCall,
                place,
                &dumps(&call.arguments, false),
            );
            if !falsy(&call.extra) {
                let place = Place {
                    call: Some(at as u64),
                    ..Place::default()
                };
                self.piece(&mut current, Kind::Extra, place, &dumps(&call.extra, false));
            }
        }
        if !message.reasoning.is_null() {
            let text = dumps(&message.reasoning, false);
            self.piece(&mut current, Kind::Reasoning, Place::default(), &text);
        }
        if !falsy(&message.extra) {
            let text = dumps(&message.extra, false);
            self.piece(&mut current, Kind::Extra, Place::default(), &text);
        }
    }

    fn piece(&mut self, current: &mut Current<'_>, kind: Kind, place: Place<'_>, text: &str) {
        let (mut content, carried) = match current.first.take() {
            Some((form, id)) => (Some(form), id),
            None => (None, None),
        };
        let call_id = place.call_id.or(carried);
        for (chunk, part) in split(text).into_iter().enumerate() {
            (self.visit)(TurnRecord {
                seq: self.seq,
                message: current.index,
                role: current.role,
                kind,
                content: content.take(),
                block: place.block,
                call: place.call,
                call_id: call_id.map(str::to_string),
                name: place.name.map(str::to_string),
                chunk: chunk as u64,
                size: text.len() as u64,
                text: part.to_string(),
            });
            self.seq += 1;
        }
    }
}

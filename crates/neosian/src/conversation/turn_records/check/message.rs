//! One message's pieces in the order `messages_of` reads them: content
//! (an empty leading record where allowed), each call and its extra, the
//! reasoning, the extra; and the `call_id` each piece must carry.

use serde::{Deserialize, Serialize};

use super::{Refusal, invalid};
use crate::conversation::message::Role;
use crate::conversation::turn_records::{Form, Kind, TurnRecord};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Serialize, Deserialize)]
enum Phase {
    Content,
    Calls,
    Reasoning,
    Extra,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) struct Message {
    pub(super) index: u64,
    pub(super) role: String,
    pub(super) form: Form,
    lead: Option<String>,
    pieces: u64,
    phase: Phase,
    pub(super) text: bool,
    blocks: u64,
    calls: u64,
    extra_for: Option<u64>,
}

impl Message {
    pub(super) fn new(index: u64, role: Role, form: Form) -> Self {
        Self {
            index,
            role: role.as_str().into(),
            form,
            lead: None,
            pieces: 0,
            phase: Phase::Content,
            text: false,
            blocks: 0,
            calls: 0,
            extra_for: None,
        }
    }

    pub(super) fn place(&mut self, record: &TurnRecord) -> Result<(), Refusal> {
        let (seq, kind) = (record.seq, record.kind);
        let own = Kind::of_role(Role::parse(&self.role).expect("a role it was opened with"));
        let first = self.pieces == 0;
        if first {
            self.lead = record.call_id.clone().filter(|_| kind != Kind::ToolCall);
        }
        let expected = match kind {
            Kind::ToolCall => record.call_id.as_ref(),
            Kind::ToolResult => self.lead.as_ref(),
            _ if first => self.lead.as_ref(),
            _ => None,
        };
        if record.call_id.as_ref() != expected {
            return Err(invalid(seq, "call_id does not match its message"));
        }
        self.pieces += 1;
        let placeholder = first
            && self.form != Form::Text
            && kind == own
            && record.block.is_none()
            && record.size == 0;
        if placeholder {
            return Ok(());
        }
        if self.phase == Phase::Content {
            match self.form {
                Form::Text if !self.text => {
                    if kind == own && record.block.is_none() {
                        self.text = true;
                        self.phase = Phase::Calls;
                        return Ok(());
                    }
                    return Err(invalid(seq, "text content needs its piece"));
                }
                Form::Blocks if record.block == Some(self.blocks) => {
                    self.blocks += 1;
                    return Ok(());
                }
                _ => {}
            }
        }
        let out_of_place = || invalid(seq, &format!("a {} piece out of place", kind.as_str()));
        match (kind, record.call) {
            (Kind::ToolCall, Some(call)) if self.phase <= Phase::Calls && call == self.calls => {
                self.calls += 1;
                self.extra_for = Some(call);
                self.phase = Phase::Calls;
            }
            (Kind::Extra, Some(call)) if self.extra_for == Some(call) => self.extra_for = None,
            (Kind::Reasoning, None) if self.phase < Phase::Reasoning => {
                self.phase = Phase::Reasoning;
                self.extra_for = None;
            }
            (Kind::Extra, None) if self.phase < Phase::Extra => {
                self.phase = Phase::Extra;
                self.extra_for = None;
            }
            _ => return Err(out_of_place()),
        }
        Ok(())
    }
}

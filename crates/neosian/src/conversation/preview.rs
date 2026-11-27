//! A conversation's turns as `continue_session` draws them (DESIGN §40.54
//! items 15.2 and 19, #448), reusable by the left-off block: each turn is
//! reduced, as its records arrive, to the log segments a view shows and
//! the prose its newest turns show verbatim. A store that keeps records is
//! read a page at a time with only the kinds and heads a view needs,
//! through the last turn its headers named before the read began, so a
//! turn landing meanwhile cannot lengthen the read. Any other store's turns
//! are read whole, one at a time, and converted to the same records first
//! (15.2). Links are numbered from what the read holds, in order: the
//! registry sees only those heads. Reasoning and extra data are never
//! read, and an attachment only as a one-byte head that holds no body.

mod build;
mod draw;
mod newest;
mod text;

pub use build::{Builder, Preview};
pub use newest::walk_newest;

use std::collections::VecDeque;

use super::Error;
use super::recall::View;
use super::recall::origin::{Item, Origin};
use super::recall::pieces::{Gather, Piece};
use super::recall::unconvertible;
use super::turn_records::{Header, Kind, RecordRead, records_of};
use crate::remote::records::Failure;
use crate::state::State;
use crate::state::records::{Fault, Records};

/// Heads, in encoded bytes: a prompt, reply or system message up to twice
/// this is read whole, a longer one as its head and tail.
const PROSE_READ: usize = 32_768;
/// The same for a call's arguments and for a tool result.
const ARGS_READ: usize = 8_192;
const RESULT_READ: usize = 8_192;

/// Where a conversation is read from.
pub enum Supply<'a> {
    /// A store that keeps records, under the authority it carries.
    Kept(&'a dyn Records),
    /// Any other store, its turns read whole.
    Whole(&'a dyn State),
}

/// The first and the last turn a read met.
pub struct Ends {
    pub first: Header,
    pub last: Header,
}

/// What a walk found.
pub enum Walked {
    /// Every turn was handed on.
    Read(Ends),
    /// The conversation has no turns.
    Empty,
    /// A turn records cannot carry: the conversation is shown as released.
    Unconvertible,
}

/// What `view` asks of a store, through turn `through`.
pub fn request(view: View, through: u64) -> RecordRead {
    let prose = [
        (Kind::Prompt, PROSE_READ),
        (Kind::Reply, PROSE_READ),
        (Kind::System, PROSE_READ),
    ];
    let result = if view == View::Calls { 1 } else { RESULT_READ };
    let (kinds, mut heads) = match view {
        View::Prompts => (vec![Kind::Prompt, Kind::Attachment], vec![prose[0]]),
        _ => (
            vec![
                Kind::Prompt,
                Kind::Reply,
                Kind::System,
                Kind::ToolCall,
                Kind::ToolResult,
                Kind::Attachment,
            ],
            [
                prose.as_slice(),
                &[(Kind::ToolCall, ARGS_READ), (Kind::ToolResult, result)],
            ]
            .concat(),
        ),
    };
    heads.push((Kind::Attachment, 1));
    RecordRead {
        through: Some(through),
        kinds: Some(kinds),
        heads,
        ..RecordRead::default()
    }
}

/// Every turn of `conversation` reduced by `builder` and handed to
/// `visit`, oldest first.
pub fn walk(
    supply: &Supply,
    conversation: &str,
    builder: &mut Builder,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<Walked, Error> {
    let walked = match supply {
        Supply::Kept(records) => kept(*records, conversation, builder, visit),
        Supply::Whole(store) => whole(*store, conversation, builder, visit),
    };
    match walked {
        Err(fault) if unconvertible(&fault) => Ok(Walked::Unconvertible),
        Err(fault) => Err(error(fault)),
        Ok(walked) => Ok(walked),
    }
}

/// A fault as the doors answer it: a server's refusal in its own words.
pub fn error(fault: Fault) -> Error {
    match fault {
        Fault::Remote(Failure::Refused(refused)) if refused.code.is_some() => {
            let code = refused.code.as_deref().unwrap_or_default();
            Error::Value(format!("[{code}] {}", refused.message))
        }
        fault => fault.into_error(),
    }
}

/// The headers first, for the last turn, then the view through it.
fn kept(
    records: &dyn Records,
    conversation: &str,
    builder: &mut Builder,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<Walked, Fault> {
    let origin = Origin::Kept {
        records,
        conversation,
    };
    let headers = RecordRead {
        kinds: Some(Vec::new()),
        ..RecordRead::default()
    };
    let mut last = None;
    origin.read(&headers, &mut |item| {
        if let Item::Header(header) = item {
            last = Some(header.turn);
        }
        Ok(true)
    })?;
    let Some(last) = last else {
        return Ok(Walked::Empty);
    };
    let read = request(builder.view(), last);
    let mut turns = Turns::new(builder, visit);
    origin.read(&read, &mut |item| turns.item(item))?;
    turns.end()
}

/// Each turn read whole, converted, and viewed as a store would answer.
fn whole(
    store: &dyn State,
    conversation: &str,
    builder: &mut Builder,
    visit: &mut dyn FnMut(Preview) -> Result<(), Fault>,
) -> Result<Walked, Fault> {
    let view = builder.view();
    let mut turns = Turns::new(builder, visit);
    let mut stopped = None;
    store.read_turns_each(conversation, 0, None, &mut |found| {
        let Ok(records) = records_of(&found.messages) else {
            stopped = Some(Walked::Unconvertible);
            return Ok(false);
        };
        let mut header = Header::of(found.turn, found.created_at, found.actor, &records);
        header.redacted = found.redacted;
        let origin = Origin::Whole { header, records };
        match origin.read(&request(view, found.turn), &mut |item| turns.item(item)) {
            Ok(()) => Ok(true),
            Err(fault) => Err(fault.into_error()),
        }
    })?;
    match stopped {
        Some(walked) => Ok(walked),
        None => turns.end(),
    }
}

/// Items in, turns out: a page's headers wait for their records, and a
/// turn ends when a later turn's record arrives or the read ends.
struct Turns<'b, 'v> {
    builder: &'b mut Builder,
    visit: &'v mut dyn FnMut(Preview) -> Result<(), Fault>,
    headers: VecDeque<Header>,
    gather: Gather,
    /// The turn of the piece being gathered.
    turn: u64,
    first: Option<Header>,
    last: Option<Header>,
}

impl<'b, 'v> Turns<'b, 'v> {
    fn new(
        builder: &'b mut Builder,
        visit: &'v mut dyn FnMut(Preview) -> Result<(), Fault>,
    ) -> Self {
        Self {
            builder,
            visit,
            headers: VecDeque::new(),
            gather: Gather::default(),
            turn: 0,
            first: None,
            last: None,
        }
    }

    fn item(&mut self, item: Item) -> Result<bool, Fault> {
        match item {
            Item::Header(header) => self.headers.push_back(header),
            Item::Record(answered) => {
                let (turn, starts) = (answered.turn, answered.record.chunk == 0);
                if let Some(piece) = self.gather.push(answered)? {
                    self.piece(self.turn, piece)?;
                }
                if starts {
                    self.turn = turn;
                }
            }
        }
        Ok(true)
    }

    fn piece(&mut self, turn: u64, piece: Piece) -> Result<(), Fault> {
        self.until(turn)?;
        if self
            .headers
            .front()
            .is_none_or(|header| header.turn != turn)
        {
            let why = "malformed record read: a record without its turn's header";
            return Err(Fault::Local(Error::Value(why.into())));
        }
        self.builder.piece(turn, piece);
        Ok(())
    }

    /// Every turn before `turn` ended and handed on.
    fn until(&mut self, turn: u64) -> Result<(), Fault> {
        while let Some(header) = self.headers.pop_front() {
            if header.turn >= turn {
                self.headers.push_front(header);
                break;
            }
            if self.first.is_none() {
                self.first = Some(header.clone());
            }
            self.last = Some(header.clone());
            (self.visit)(self.builder.finish(header))?;
        }
        Ok(())
    }

    fn end(mut self) -> Result<Walked, Fault> {
        if let Some(piece) = self.gather.finish()? {
            self.piece(self.turn, piece)?;
        }
        self.until(u64::MAX)?;
        Ok(match (self.first, self.last) {
            (Some(first), Some(last)) => Walked::Read(Ends { first, last }),
            _ => Walked::Empty,
        })
    }
}

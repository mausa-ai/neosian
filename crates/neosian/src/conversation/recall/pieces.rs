//! Answered records gathered back into pieces (DESIGN §40.54 items 3.1 and
//! 10): a piece's chunks arrive in order, possibly across pages, or as
//! one record holding its head and tail. A chunk out of place, or a piece
//! that ends short of its size, is the store's malformed answer, refused.

use crate::conversation::Error;
use crate::conversation::turn_records::{Kind, TurnRecord};
use crate::remote::records::Answered;
use crate::state::records::Fault;

/// One piece as the read answered it: whole, or its head with its tail.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Piece {
    /// Its first record.
    pub seq: u64,
    pub message: u64,
    pub kind: Kind,
    pub block: Option<u64>,
    pub call: Option<u64>,
    pub call_id: Option<String>,
    pub name: Option<String>,
    /// The whole piece's UTF-8 bytes.
    pub size: u64,
    /// The whole text, or its head when the read cut it.
    pub text: String,
    /// The tail, when the read cut the piece.
    pub tail: Option<String>,
    chunks: u64,
}

impl Piece {
    /// The piece holding `record`, by its place and size alone, its text
    /// still to be read.
    pub fn holding(record: &TurnRecord) -> Result<Self, Fault> {
        let Some(seq) = record.seq.checked_sub(record.chunk) else {
            return Err(malformed("a chunk before its piece"));
        };
        Ok(Self {
            seq,
            message: record.message,
            kind: record.kind,
            block: record.block,
            call: record.call,
            call_id: record.call_id.clone(),
            name: record.name.clone(),
            size: record.size,
            text: String::new(),
            tail: None,
            chunks: 0,
        })
    }

    /// Whether `record` belongs to this piece by its place.
    pub fn holds(&self, record: &TurnRecord) -> bool {
        (record.message, record.kind, record.block, record.call)
            == (self.message, self.kind, self.block, self.call)
            && record.size == self.size
    }

    /// The bytes a cut piece leaves out between its head and its tail.
    pub fn omitted(&self) -> u64 {
        let shown = self.text.len() + self.tail.as_ref().map_or(0, String::len);
        self.size.saturating_sub(shown as u64)
    }

    fn whole(&self) -> bool {
        self.tail.is_some() || self.text.len() as u64 == self.size
    }
}

/// Records in, finished pieces out, in order.
#[derive(Default)]
pub struct Gather {
    current: Option<Piece>,
}

fn malformed(what: &str) -> Fault {
    Fault::Local(Error::Value(format!("malformed record read: {what}")))
}

impl Gather {
    /// The next record: a first chunk starts a piece and hands back the
    /// one it ends; any other chunk must continue the current piece.
    pub fn push(&mut self, answered: Answered) -> Result<Option<Piece>, Fault> {
        let record = answered.record;
        if record.chunk == 0 {
            let done = self.finish()?;
            self.current = Some(Piece {
                seq: record.seq,
                message: record.message,
                kind: record.kind,
                block: record.block,
                call: record.call,
                call_id: record.call_id,
                name: record.name,
                size: record.size,
                text: record.text,
                tail: answered.tail.filter(|_| answered.truncated),
                chunks: 1,
            });
            return Ok(done);
        }
        let Some(piece) = self.current.as_mut() else {
            return Err(malformed("a chunk without its piece"));
        };
        let continues = piece.tail.is_none()
            && record.chunk == piece.chunks
            && record.seq == piece.seq + piece.chunks
            && (record.message, record.kind, record.block, record.call)
                == (piece.message, piece.kind, piece.block, piece.call)
            && record.size == piece.size;
        if !continues {
            return Err(malformed("a chunk out of place"));
        }
        piece.text.push_str(&record.text);
        piece.chunks += 1;
        Ok(None)
    }

    /// The piece in hand when the read ends.
    pub fn finish(&mut self) -> Result<Option<Piece>, Fault> {
        match self.current.take() {
            Some(piece) if !piece.whole() => Err(malformed("a piece short of its size")),
            done => Ok(done),
        }
    }
}

//! Where a history search answer resumes (size paging version 1, DESIGN
//! §40.46). Version 1 and newest continue by keyset: strictly after the last
//! turn returned in their total order. Relevance recounts over the same
//! bounded selection and skips the winners already returned, refusing when
//! the candidate statistics or the last returned turn changed between pages.

use super::rank::Winner;
use super::{Error, Turn};

/// A turn's place in the version 1 total order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Last {
    pub created: i64,
    pub conversation: String,
    pub turn: u64,
}

impl Last {
    pub fn of(turn: &Turn) -> Self {
        Self {
            created: turn.created_at.instant(),
            conversation: turn.conversation_id.clone(),
            turn: turn.turn,
        }
    }

    /// Whether a turn comes after this one in the version 1 order
    /// (`created_at`, conversation id, turn, each descending).
    pub fn precedes(&self, created: i64, conversation: &str, turn: u64) -> bool {
        (created, conversation, turn) < (self.created, self.conversation.as_str(), self.turn)
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum Resume {
    #[default]
    Fresh,
    /// Version 1 and newest: strictly after this turn.
    After(Last),
    /// Relevance: after the first `skip` winners, the last of which is `last`
    /// with `score`, over candidates whose statistics digest is `digest`.
    Skip {
        skip: usize,
        last: Last,
        score: u64,
        digest: String,
    },
}

/// How a turn read is read (DESIGN §40.46, #417): one statement for an
/// answer that is spooled, turned into hits or kept by its caller; a size
/// page, which may stop early, in statements or batches sized to its ceiling.
/// Every row is checked alone against `NEOSIAN_MAX_RESPONSE_BYTES` (#418).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// Every turn of the answer.
    Each,
    /// One size page of this many bytes: it may stop before the statement
    /// ends, so a database reads it in pieces sized to reach the ceiling.
    Paged(u64),
}

/// The visitor a search hands each turn to in answer order, with its score
/// under relevance; false stops the answer.
pub type Visit<'a> = dyn FnMut(Turn, Option<u64>) -> Result<bool, Error> + 'a;

/// One winner of a history search as the route answers it (DESIGN §40.54
/// item 15.1): a turn whole, or a turn stored as records that is too long to
/// answer whole, which keeps its place among the winners and is counted in
/// the answer's `omitted`.
#[derive(Debug)]
pub enum Hit {
    Turn(Turn),
    Omitted(Last),
}

/// The visitor a route hands each winner to in answer order, with its score
/// under relevance; false stops the answer.
pub type HitVisit<'a> = dyn FnMut(Hit, Option<u64>) -> Result<bool, Error> + 'a;

pub const STALE: &str =
    "page continuation is stale: the candidate turns changed since the first page; search again";

/// The winners a page still owes: all of them, or those after `skip` once
/// the digest and the last returned winner are confirmed unchanged.
pub fn owed(mut winners: Vec<Winner>, resume: &Resume, digest: &str) -> Result<Vec<Winner>, Error> {
    let Resume::Skip {
        skip,
        last,
        score,
        digest: expected,
    } = resume
    else {
        return Ok(winners);
    };
    let confirmed = *skip >= 1
        && expected == digest
        && winners.get(skip - 1).is_some_and(|(key, id, held)| {
            *held == Some(*score)
                && key.created == last.created
                && *id == last.conversation
                && key.turn == last.turn
        });
    if !confirmed {
        return Err(Error::Value(STALE.into()));
    }
    Ok(winners.split_off(*skip))
}

#[cfg(test)]
mod tests {
    use super::{Last, Resume, owed};
    use crate::conversation::rank::Key;

    fn winner(turn: u64, score: u64) -> crate::conversation::rank::Winner {
        let key = Key {
            created: 10,
            conversation: 0,
            turn,
            place: 0,
            line: 0,
        };
        (key, "c".into(), Some(score))
    }

    #[test]
    fn a_resumed_relevance_page_skips_what_was_returned_or_refuses() {
        let winners = || vec![winner(3, 9), winner(1, 7), winner(2, 5)];
        let last = Last {
            created: 10,
            conversation: "c".into(),
            turn: 1,
        };
        let resume = |digest: &str, score| Resume::Skip {
            skip: 2,
            last: last.clone(),
            score,
            digest: digest.into(),
        };
        let rest = owed(winners(), &resume("d", 7), "d").unwrap();
        assert_eq!(rest.len(), 1);
        assert_eq!(rest[0].0.turn, 2);
        assert!(owed(winners(), &resume("d", 7), "other").is_err());
        assert!(owed(winners(), &resume("d", 8), "d").is_err());
        assert_eq!(owed(winners(), &Resume::Fresh, "d").unwrap().len(), 3);
        assert!(last.precedes(9, "z", 99) && last.precedes(10, "c", 0));
        assert!(!last.precedes(10, "c", 1) && !last.precedes(10, "d", 0));
    }
}

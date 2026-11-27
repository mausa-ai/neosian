//! The mailbox beside record hits (DESIGN §34, §40.54 item 19): the
//! messages about a hit's turn ride under it, and, when no `kinds` were
//! named, a message whose body or outcomes hold every term is a hit of its
//! own, which each door labels as a message. Ranked record hits keep their
//! order and message hits follow, newest first; newest record hits and
//! message hits merge by instant, a record first on a tie. The bounds limit
//! messages by creation instant, as they limit turns (§40.43). A hit keeps
//! its annotations only within the annotations frame's budget, the last
//! one cut to the room left, so however many messages are about one turn,
//! no door holds or shows more of them than the frame could.

use super::delivery::annotation;
use super::mailbox::Mailbox;
use super::types::{CONTEXT_CHARS, InboxMessage};
use crate::conversation::Error;
use crate::conversation::rank::{Bounds, within};
use crate::conversation::search::match_terms;
use crate::conversation::turn_records::RecordHit;
use crate::text::{head, len};

/// One row of a record answer.
pub enum Row<'a> {
    /// A record hit with the annotations of the messages about its turn.
    Record {
        hit: &'a RecordHit,
        notes: Vec<String>,
    },
    /// A message holding every term, with its body and outcomes.
    Message {
        item: Box<InboxMessage>,
        text: String,
    },
}

/// What the mailbox is read for.
pub struct Beside<'t> {
    /// The terms as the version 1 query parser gives them.
    pub terms: &'t [String],
    /// The conversations a message must be about; `None` for any.
    pub ids: Option<&'t [String]>,
    pub bounds: Bounds,
    /// Whether matching messages are hits of their own: only without `kinds`.
    pub messages: bool,
    pub ranked: bool,
    pub limit: usize,
}

/// `item`'s annotation kept beside a hit's `notes` while they stay within
/// `CONTEXT_CHARS` joined as the frame joins them, cut to the room left.
fn note(notes: &mut Vec<String>, item: &InboxMessage) {
    let used = notes.iter().map(|note| len(note) + 2).sum::<usize>();
    let room = CONTEXT_CHARS.saturating_sub(used);
    if room > 0 {
        notes.push(head(&annotation(item), room).to_string());
    }
}

/// The record hits with their annotations and the message hits, at most
/// `limit` rows; without a mailbox, the record hits alone.
pub fn rows<'a>(
    mailbox: Option<&Mailbox>,
    hits: &'a [RecordHit],
    beside: &Beside,
) -> Result<Vec<Row<'a>>, Error> {
    let mut records: Vec<Row<'a>> = hits
        .iter()
        .map(|hit| Row::Record {
            hit,
            notes: Vec::new(),
        })
        .collect();
    let mut messages: Vec<(i64, String, Row<'a>)> = Vec::new();
    for receipt in mailbox.map_or(Ok(Vec::new()), |mailbox| mailbox.all(None))? {
        let item = receipt.message;
        let about = item.about.as_deref();
        if beside
            .ids
            .is_some_and(|ids| !about.is_some_and(|about| ids.iter().any(|id| id == about)))
            || !within(beside.bounds, item.created_at.instant())
        {
            continue;
        }
        for row in &mut records {
            if let Row::Record { hit, notes } = row
                && about == Some(hit.conversation_id.as_str())
                && item
                    .about_turn
                    .is_none_or(|number| number == hit.turn as i64)
            {
                note(notes, &item);
            }
        }
        let Some(mailbox) = mailbox.filter(|_| beside.messages) else {
            continue;
        };
        let mut text = item.body.clone();
        for version in mailbox.history(&item.scope, &item.id)? {
            text.push('\n');
            text.push_str(version.outcome.as_deref().unwrap_or(""));
        }
        if match_terms(&text, beside.terms) {
            let at = item.created_at.instant();
            let item = Box::new(item);
            messages.push((at, item.id.clone(), Row::Message { item, text }));
        }
    }
    messages.sort_by(|a, b| (b.0, &b.1).cmp(&(a.0, &a.1)));
    let mut merged = Vec::with_capacity(records.len() + messages.len());
    let mut pending = messages.into_iter().peekable();
    for row in records {
        if let Row::Record { hit, .. } = &row
            && !beside.ranked
        {
            let at = hit.created_at.instant();
            while let Some((_, _, message)) = pending.next_if(|(when, ..)| *when > at) {
                merged.push(message);
            }
        }
        merged.push(row);
    }
    merged.extend(pending.map(|(_, _, message)| message));
    merged.truncate(beside.limit);
    Ok(merged)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::conversation::turn_records::Kind;
    use crate::store::time::Stamp;

    fn hit(turn: u64, at: &str) -> RecordHit {
        RecordHit {
            conversation_id: "c".into(),
            turn,
            seq: 0,
            message: 0,
            kind: Kind::Prompt,
            call_id: None,
            name: None,
            created_at: Stamp::parse(at).unwrap(),
            actor: None,
            size: 1,
            excerpt: (0, "x".into()),
            score: None,
        }
    }

    #[test]
    fn a_hit_keeps_its_annotations_within_the_frame_budget() {
        use crate::memory::mounts::Mount;
        use crate::messaging::types::Delivery;
        use crate::messaging::writes::Send;
        use crate::store::FileStore;
        use crate::store::grammar::Actor;

        const SCOPE: &str = "user:test/proj:notes";
        let scratch = crate::testing::Scratch::new("record-history-notes");
        let store = FileStore::open(scratch.path()).unwrap();
        let mounts = [Mount::new(SCOPE, "project", (false, false), "").unwrap()];
        let mailbox = Mailbox::of_mounts(&store, &mounts, Some("s"))
            .with_actor(Actor::parse("test:s").unwrap());
        let about = Send {
            scope: SCOPE,
            conversation: None,
            turn: None,
            delivery: Delivery::NextActivity,
            actionable: false,
            due_at: None,
            delay_seconds: None,
            about: Some("c"),
            about_turn: Some(1),
            context: None,
        };
        for n in 0..40 {
            let body = format!("note {n} {}", "n".repeat(40));
            mailbox.send(&body, &about).unwrap();
        }
        let hits = [hit(1, "2026-11-01T00:00:00Z")];
        let beside = Beside {
            terms: &["x".to_string()],
            ids: None,
            bounds: (None, None),
            messages: false,
            ranked: false,
            limit: 1,
        };
        let rows = rows(Some(&mailbox), &hits, &beside).unwrap();
        let Row::Record { notes, .. } = &rows[0] else {
            panic!("a record hit")
        };
        let joined = notes.join("\n\n");
        assert!(
            notes.len() < 40 && len(&joined) <= CONTEXT_CHARS,
            "{}",
            notes.len()
        );
        // The frame shows what it showed when every annotation was kept.
        let receipts = mailbox.all(None).unwrap();
        let all: Vec<String> = receipts.iter().map(|r| annotation(&r.message)).collect();
        let all = all.join("\n\n");
        assert_eq!(head(&joined, CONTEXT_CHARS), head(&all, CONTEXT_CHARS));
    }

    #[test]
    fn without_a_mailbox_the_record_hits_stand_alone_within_the_limit() {
        let hits = [
            hit(2, "2026-11-02T00:00:00Z"),
            hit(1, "2026-11-01T00:00:00Z"),
        ];
        let beside = Beside {
            terms: &["x".to_string()],
            ids: None,
            bounds: (None, None),
            messages: true,
            ranked: false,
            limit: 1,
        };
        let rows = rows(None, &hits, &beside).unwrap();
        assert_eq!(rows.len(), 1);
        assert!(
            matches!(&rows[0], Row::Record { hit, notes } if hit.turn == 2 && notes.is_empty())
        );
    }
}

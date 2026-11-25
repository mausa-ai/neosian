//! Retrieve candidate facts and individual winners in the search snapshot.
use super::{Error, Reader, Statements, Stored, damaged};
use crate::conversation::page::{Hit, Last};
use crate::conversation::rank::term_counts;
use crate::conversation::search::turn_text;
use crate::conversation::turn_records::TextFacts;
use tokio_postgres::Transaction;

impl Reader<'_, '_> {
    async fn selected(&self, turn: u64, search_hit: bool) -> Result<Stored, Error> {
        let portal = self.headers(turn, turn, Some(1), true).await?;
        self.next_header_in(&portal, search_hit)
            .await?
            .ok_or_else(|| damaged(self.id, turn, "the selected history turn is missing"))
    }
}

pub(in crate::postgres) async fn history_hit(
    tx: &Transaction<'_>,
    statements: &Statements,
    id: &str,
    turn: u64,
    budget: u64,
    maximum: u64,
) -> Result<Hit, Error> {
    let reader = Reader {
        tx,
        statements,
        id,
        budget,
    };
    match reader.selected(turn, true).await? {
        Stored::Whole(whole) => Ok(Hit::Turn(whole)),
        Stored::Records(header) if header.bytes > maximum => Ok(Hit::Omitted(Last {
            conversation: id.into(),
            turn,
            created: header.created_at.instant(),
        })),
        Stored::Records(header) => {
            let whole = reader.bodies().assemble(header).await?;
            reader.check_whole(&whole, true)?;
            Ok(Hit::Turn(whole))
        }
    }
}

pub(in crate::postgres) async fn history_facts(
    tx: &Transaction<'_>,
    statements: &Statements,
    id: &str,
    turn: u64,
    terms: &[String],
    budget: u64,
    maximum: u64,
) -> Result<(Vec<u32>, u64), Error> {
    let reader = Reader {
        tx,
        statements,
        id,
        budget,
    };
    let Stored::Records(header) = reader.selected(turn, false).await? else {
        return Err(damaged(id, turn, "the selected records turn is missing"));
    };
    if header.bytes <= maximum {
        // Whole-string folding and canonical JSON match the older history
        // scorer. The outgoing turn-size check applies if this turn wins.
        let text = turn_text(&reader.bodies().assemble(header).await?);
        let folded = text.to_lowercase();
        return Ok((term_counts(&folded, terms), folded.chars().count() as u64));
    }
    let mut facts = TextFacts::new(terms);
    let mut bytes = 0_u64;
    let bad = || damaged(id, turn, "its records do not match its header");
    reader
        .bodies()
        .records(&header, 0, |record| {
            bytes = bytes
                .checked_add(record.text.len() as u64)
                .ok_or_else(bad)?;
            if bytes > header.bytes {
                return Err(bad());
            }
            facts.push(&record);
            Ok(true)
        })
        .await?;
    if bytes != header.bytes {
        return Err(bad());
    }
    Ok(facts.finish())
}

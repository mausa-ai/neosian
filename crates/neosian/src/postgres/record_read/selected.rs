//! Retrieve a selected search record or legacy turn within the searching transaction.
use super::{Error, Reader, Statements, Stored, Turn, TurnRecord, damaged};
use tokio_postgres::Transaction;

pub(in crate::postgres) async fn one_record(
    tx: &Transaction<'_>,
    statements: &Statements,
    id: &str,
    turn: u64,
    seq: u64,
    budget: u64,
) -> Result<TurnRecord, Error> {
    let reader = Reader {
        tx,
        statements,
        id,
        budget,
    };
    let mut result = None;
    reader
        .interval(turn, seq, seq + 1, |record| {
            result = Some(record);
            Ok(true)
        })
        .await?;
    result.ok_or_else(|| damaged(id, turn, "the selected record is missing"))
}

pub(in crate::postgres) async fn whole_at(
    tx: &Transaction<'_>,
    statements: &Statements,
    id: &str,
    turn: u64,
    budget: u64,
) -> Result<Turn, Error> {
    let reader = Reader {
        tx,
        statements,
        id,
        budget,
    };
    let portal = reader.headers(turn, turn, Some(1), true).await?;
    match reader.next_header(&portal).await? {
        Some(Stored::Whole(whole)) if !whole.redacted => Ok(whole),
        _ => Err(damaged(id, turn, "the selected legacy turn is missing")),
    }
}

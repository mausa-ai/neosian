//! Original headers and indexed SHA-256 digests retained by extension 2.
use super::{connection::Failure, sql};
use crate::conversation::Refused;
use crate::conversation::turn_records::{TurnRecord, conflict};
use serde_json::json;
use tokio_postgres::Transaction;

const RECORDS_PER_PAGE: u64 = 32;
const DIGEST_BYTES: usize = 32;

/// Retain the genuine seal before the same transaction folds its turn.
/// The original mapping and all of its independently verified records are
/// still present, including splits a canonical re-migration would change.
pub(super) async fn retain(
    tx: &Transaction<'_>,
    schema: &str,
    id: &str,
    turn: i32,
    owner: i64,
) -> Result<(), Failure> {
    let incomplete = || {
        Refused::format_unsupported(
            id,
            &format!("turn row {turn} could not retain its exact native seal evidence"),
        )
    };
    let header = sql!("records/fold_seal").replace("{{schema}}", schema);
    let row = tx
        .query_opt(&header, &[&id, &turn, &owner])
        .await?
        .ok_or_else(incomplete)?;
    let count: i32 = row.try_get("records")?;
    let expected = u64::try_from(count)
        .map_err(|_| incomplete())?
        .div_ceil(RECORDS_PER_PAGE);
    let pages = sql!("records/fold_seal_pages").replace("{{schema}}", schema);
    if tx.execute(&pages, &[&id, &turn, &owner, &count]).await? != expected {
        return Err(incomplete().into());
    }
    let check = sql!("records/fold_seal_check").replace("{{schema}}", schema);
    if !tx
        .query_one(&check, &[&id, &turn, &owner])
        .await?
        .try_get::<_, bool>(0)?
    {
        return Err(incomplete().into());
    }
    Ok(())
}

/// The caller has checked consecutive sequences and that every submitted
/// record predates the seal. Replays transfer 32 bytes per requested record.
pub(super) async fn confirm_range(
    tx: &Transaction<'_>,
    schema: &str,
    id: &str,
    turn: i32,
    count: u64,
    records: &[TurnRecord],
) -> Result<(), Failure> {
    let Some(first) = records.first() else {
        return Ok(());
    };
    let unreadable = || {
        Refused::format_unsupported(
            id,
            &format!("turn {turn}: its folded receipt is unreadable"),
        )
    };
    let from = first.seq;
    let until = from + records.len() as u64;
    let query = sql!("records/seal_digests").replace("{{schema}}", schema);
    let rows = tx
        .query(&query, &[&id, &turn, &(from as i64), &(until as i64)])
        .await?;
    let mut seq = from;
    for row in rows {
        let page = seq / RECORDS_PER_PAGE;
        let end = until.min((page + 1) * RECORDS_PER_PAGE);
        let digests: &[u8] = row.try_get("requested")?;
        if seq >= until
            || i64::from(row.try_get::<_, i32>("page")?) != page as i64
            || digests.len() != (end - seq) as usize * DIGEST_BYTES
        {
            return Err(unreadable().into());
        }
        for digest in digests.as_chunks::<DIGEST_BYTES>().0 {
            let record = &records[(seq - from) as usize];
            if record.digest(None) != *digest {
                return Err(
                    conflict(id, "draft_mismatch", json!({"next_seq":count,"seq":seq})).into(),
                );
            }
            seq += 1;
        }
    }
    if seq != until {
        return Err(unreadable().into());
    }
    Ok(())
}

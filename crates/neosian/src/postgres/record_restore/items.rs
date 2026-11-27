//! Stage one part under its original restore owner. The stream checks and
//! hashes each new item in source order. SQL compares replayed items by key
//! and counts newly stored records without reading any earlier part.
use super::{Part, Result, corrupt, reason};
use crate::conversation::Error;
use crate::conversation::turn_records::{
    Item, Placed, Restore, RestoreStream, Sizes, StreamRefusal, size_refusal,
};
use crate::postgres::record_fields as fields;
use crate::{postgres::sql, pyfmt::dumps, wire::encode};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

struct Entry<'a> {
    item: Item<'a>,
    repeated: bool,
    last: bool,
}

const RESTORE: &str = "restore";

fn range(field: &str) -> Error {
    fields::range(RESTORE, field)
}

fn integer(n: u64, field: &str) -> Result<()> {
    Ok(fields::integer(RESTORE, n, field)?)
}

fn text(value: Option<&str>, field: &str) -> Result<()> {
    Ok(fields::text(RESTORE, value, field)?)
}

// Positive ordinals are source turn numbers. Negative odd/even ordinals
// address projections/acts by their original zero-based index, including
// when turns have sparse numbers or later requests repeat old metadata.
fn ordinal(index: u64, kind: i64) -> Result<i64> {
    i64::try_from(index)
        .ok()
        .and_then(|n| n.checked_mul(-2))
        .and_then(|n| n.checked_sub(kind))
        .ok_or_else(|| range("item index").into())
}

fn representable(item: &Item<'_>) -> Result<()> {
    match *item {
        Item::Header(h) => {
            integer(h.turn, "turn")?;
            integer(h.records, "records")?;
            i64::try_from(h.bytes).map_err(|_| range("bytes"))?;
            text(h.actor.as_deref(), "actor")
        }
        Item::Record(turn, r) => {
            integer(turn, "turn")?;
            Ok(fields::record(RESTORE, r)?)
        }
        Item::Projection(i, p) => {
            ordinal(i, 1)?;
            integer(p.turn, "turn")?;
            integer(p.span, "span")?;
            text(Some(&p.text), "text")
        }
        Item::Act(i, a) => {
            ordinal(i, 2)?;
            for &turn in &a.turns {
                integer(turn, "turn")?;
            }
            text(a.actor.as_deref(), "actor")
        }
    }
}

fn charge(item: &Item<'_>, sizes: &Sizes) -> u64 {
    match *item {
        Item::Header(h) if h.redacted => sizes.redacted_header(h.actor.as_deref()),
        Item::Header(h) => sizes.header(h.actor.as_deref()),
        Item::Record(_, r) => sizes.record(r),
        Item::Projection(_, p) => sizes.projection(&p.text),
        Item::Act(_, a) => sizes.act(a.actor.as_deref(), a.turns.len()),
    }
}

fn hex(raw: &[u8]) -> String {
    raw.iter().map(|n| format!("{n:02x}")).collect()
}

impl Entry<'_> {
    fn row(&self) -> Result<Value> {
        let (ord, kind, body) = match self.item {
            Item::Header(h) => (h.turn as i64, "turn", h.to_json()),
            Item::Projection(i, p) => (ordinal(i, 1)?, "projection", encode::projection(p)),
            Item::Act(i, a) => (ordinal(i, 2)?, "redaction", encode::turn_redaction(a)),
            Item::Record(turn, r) => {
                let mut row = r.to_json(Some(turn));
                row["last"] = json!(self.last);
                row["repeated"] = json!(self.repeated);
                row["digest"] = json!(hex(&r.digest(None)));
                return Ok(row);
            }
        };
        let digest = Sha256::digest(dumps(&body, false).as_bytes());
        Ok(
            json!({"ord":ord, "item":kind, "body":body, "digest":hex(&digest), "repeated":self.repeated}),
        )
    }
}

pub(super) async fn stage(
    cx: &Part<'_, '_>,
    owner: i64,
    restore: &Restore,
    stream: &mut RestoreStream,
    maximum: u64,
) -> Result<u64> {
    let before = stream.position();
    let sizes = Sizes::new(cx.id, &restore.restore);
    let (mut metadata, mut records, mut stored) = (Vec::new(), Vec::new(), 0_u64);
    for item in restore.items() {
        let placed = stream.admit(&item, maximum).map_err(|error| match error {
            StreamRefusal::Gap => reason(cx.id, "restore_gap", before),
            StreamRefusal::Invalid(why) => Error::Value(why).into(),
            StreamRefusal::Bound { bytes, maximum } => {
                Error::Value(size_refusal(bytes, maximum)).into()
            }
        })?;
        let repeated = placed == Placed::Repeat;
        if !repeated {
            representable(&item)?;
            stored = stored
                .checked_add(charge(&item, &sizes))
                .ok_or_else(|| corrupt(cx.id))?;
        }
        let group = if matches!(item, Item::Record(..)) {
            &mut records
        } else {
            &mut metadata
        };
        group.push(Entry {
            item,
            repeated,
            last: stream.piece_complete(),
        });
    }

    // Store headers first so this part's new records can increment them.
    // Only a small group is encoded at a time. The transaction owns all
    // writes; any bad replay or missing header rolls back the entire part.
    for (entries, template, physical) in [
        (metadata, sql!("records/restore_stage_metadata"), false),
        (records, sql!("records/restore_stage_records"), true),
    ] {
        if entries.is_empty() {
            continue;
        }
        let statement = cx.tx.prepare(&cx.sql(template)).await?;
        for group in entries.chunks(32) {
            let mut encoded = vec![b'['];
            for entry in group {
                if entry.repeated && representable(&entry.item).is_err() {
                    return Err(reason(cx.id, "restore_mismatch", before));
                }
                if encoded.len() > 1 {
                    encoded.push(b',');
                }
                serde_json::to_writer(&mut encoded, &entry.row()?).expect("a JSON value encodes");
            }
            encoded.push(b']');
            let payload = std::str::from_utf8(&encoded).expect("JSON is UTF-8");
            let row = cx
                .tx
                .query_one(&statement, &[&cx.id, &owner, &payload])
                .await?;
            if !row.try_get::<_, bool>("matches")? {
                return Err(reason(cx.id, "restore_mismatch", before));
            }
            if physical && !row.try_get::<_, bool>("counted")? {
                return Err(corrupt(cx.id));
            }
        }
    }
    Ok(stored)
}

//! The turn-records routes (DESIGN §40.54 items 8 to 13), under the shared
//! rules of item 8.2: a strict body, the answer's keys in its table's
//! order, and on a store that keeps no records the store's own refusal
//! whatever the body holds. `write_records` takes one part of a draft
//! under the 4 MiB ceiling of a part; `read_records` is `record_pages`,
//! and `search_records` and the operator's `sweep_records` are
//! `record_search`.

use serde_json::Value;

use super::auth::stamp;
use super::backend::Backend;
use super::envelope::{CEILING, Fail, PART_CEILING};
use super::params::Payload;
use super::record_search::{search_records, sweep_records};
use super::routes::{Handler, Route};
use super::strict::Strict;
use crate::conversation::turn_records::{TurnRecord, Write};
use crate::limits;

pub const ROUTES: [Route; 4] = [
    Route {
        name: "conversation/write_records",
        handler: Handler::Whole(write_records),
        whole_store: false,
        ceiling: PART_CEILING,
    },
    Route {
        name: "conversation/read_records",
        handler: Handler::Spooled(super::record_pages::read_records),
        whole_store: false,
        ceiling: CEILING,
    },
    Route {
        name: "conversation/search_records",
        handler: Handler::Whole(search_records),
        whole_store: false,
        ceiling: CEILING,
    },
    Route {
        name: "store/sweep_records",
        handler: Handler::Whole(sweep_records),
        whole_store: true,
        ceiling: CEILING,
    },
];

const WRITE: [&str; 7] = [
    "conversation_id",
    "draft",
    "records",
    "turn_bytes",
    "seal",
    "discard",
    "actor",
];

/// A store that keeps no records answers its own refusal, whatever the
/// body holds; files are asked on every request.
pub(super) fn kept(store: &dyn Backend) -> Result<(), Fail> {
    if !store.supports_records()? {
        return Err(store.records_absent().into());
    }
    Ok(())
}

/// The record settings of a store that keeps records, read per request.
fn enabled(store: &dyn Backend) -> Result<limits::Records, Fail> {
    kept(store)?;
    Ok(limits::Records::load()?)
}

/// One record in write shape: `turn` belongs to a restore alone.
fn written_record(item: &Value) -> Result<TurnRecord, Fail> {
    match TurnRecord::from_json(item).map_err(Fail::value)? {
        (None, record) => Ok(record),
        (Some(_), _) => Err(Fail::value("unknown record field \"turn\"")),
    }
}

fn write_records(store: &dyn Backend, payload: &Payload, client: &str) -> Result<Value, Fail> {
    let limits = enabled(store)?;
    let body = Strict::new(payload, &WRITE)?;
    let conversation_id = body.string("conversation_id")?;
    let write = Write {
        draft: body.string("draft")?.to_string(),
        turn_bytes: body.unsigned("turn_bytes")?,
        seal: body.boolean("seal", false)?,
        discard: body.boolean("discard", false)?,
        actor: body
            .nullable_string("actor")?
            .map(|named| stamp(client, Some(named))),
        stamp: Some(stamp(client, None)),
        records: body
            .list("records")?
            .iter()
            .map(written_record)
            .collect::<Result<_, _>>()?,
    };
    let written = store.write_records(conversation_id, write, &limits)?;
    Ok(written.to_json())
}

#[cfg(test)]
mod tests {
    use super::super::{routes_conversation, routes_memory, routes_store};
    use super::*;

    #[test]
    fn a_part_is_read_under_4_mib_and_every_legacy_body_under_64_mib() {
        assert_eq!(PART_CEILING, 4_194_304);
        let [write, read, search, sweep] = &ROUTES;
        assert_eq!(
            (write.name, write.ceiling, write.whole_store),
            ("conversation/write_records", PART_CEILING, false)
        );
        assert_eq!(
            (read.name, read.ceiling, read.whole_store),
            ("conversation/read_records", CEILING, false)
        );
        assert!(matches!(read.handler, Handler::Spooled(_)));
        // A search is authorized by the conversations it names; the sweep
        // moves the store whole and is the operator's alone.
        assert_eq!(
            (search.name, search.ceiling, search.whole_store),
            ("conversation/search_records", CEILING, false)
        );
        assert_eq!(
            (sweep.name, sweep.ceiling, sweep.whole_store),
            ("store/sweep_records", CEILING, true)
        );
        let legacy = routes_memory::ROUTES
            .iter()
            .chain(routes_conversation::ROUTES.iter())
            .chain(routes_store::ROUTES.iter());
        assert_eq!(legacy.clone().count(), 24);
        assert!(legacy.clone().all(|route| route.ceiling == CEILING));
        assert_eq!(CEILING, 67_108_864);
    }
}

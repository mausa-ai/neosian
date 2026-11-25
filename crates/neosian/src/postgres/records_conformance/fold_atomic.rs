//! One PostgreSQL fold transaction covers every turn and all selected pending
//! owners. Real database sessions observe commit, abort and lock ordering.
mod failures;
mod races;
mod visibility;

use super::{
    Database,
    restore_stage::{ID, LIMITS, NAME, example, message, rows},
};
use crate::conversation::Error;
use crate::conversation::rank::{Order, Request};
use crate::conversation::turn_records::{RecordRead, Restore, Write, records_of};
use crate::postgres::PostgresStore;
use crate::wire::encode;
use serde_json::{Value, json};

const TABLES: [&str; 8] = [
    "conversations",
    "turns",
    "projections",
    "turn_redactions",
    "record_drafts",
    "record_staging",
    "record_turns",
    "turn_records",
];

fn snapshot(db: &Database) -> [Vec<Value>; 8] {
    TABLES.map(|t| rows(db, t))
}

fn append(store: &PostgresStore, id: &str, text: &str) {
    store
        .append_turn(id, vec![message("user", text)], Some("source:whole".into()))
        .unwrap();
}

fn pending(db: &Database, id: &str) {
    db.store
        .write_records(
            id,
            Write {
                draft: NAME.into(),
                records: records_of(&[message("user", "hidden draft")]).unwrap(),
                ..Default::default()
            },
            &LIMITS,
        )
        .unwrap();
}

// An admitted restore can stay open while ordinary messages are appended
// and migrated. Its rows must survive every failed --discard-pending fold.
fn mixed_pending(db: &Database, second: &str) {
    let (first, _) = example();
    db.store.restore_records(ID, first, &LIMITS).unwrap();
    append(&db.store, ID, "visible first");
    append(&db.store, ID, second);
    assert_eq!(db.store.migrate_records(ID, &LIMITS).unwrap().migrated, 2);
    pending(db, ID);
    pending(db, "other");
}

fn answers(store: &PostgresStore) -> Result<Value, Error> {
    let ids = [ID.to_string()];
    let turns = store.read_turns(ID, 0, None)?;
    let mut items = Vec::new();
    let after = store.read_records(ID, &RecordRead::default(), &mut |row| {
        items.push(row.to_json());
        Ok(())
    })?;
    let mut searches = Vec::new();
    for term in ["yyy", "www", "legacyalias", "unindexed"] {
        for order in [Order::Newest, Order::Relevance] {
            let hits = store.rank_turns(&Request {
                terms: vec![term.into()],
                ids: Some(ids.to_vec()),
                limit: 10,
                order,
                since: None,
                before: None,
            })?;
            searches.push(
                hits.into_iter()
                    .map(|(t, score)| (encode::turn(&t), score))
                    .collect::<Vec<_>>(),
            );
        }
    }
    Ok(
        json!({"turns":turns.iter().map(encode::turn).collect::<Vec<_>>(),"complete":after.is_none(),"items":items,"search":searches,
        "projections":store.read_projections(ID,0,None)?.iter().map(encode::projection).collect::<Vec<_>>(),
        "acts":store.turn_redactions(Some(&ids),None,10)?.iter().map(encode::turn_redaction).collect::<Vec<_>>() }),
    )
}

fn published(db: &Database) -> (Restore, Value) {
    let (first, mut final_part) = example();
    db.store.restore_records(ID, first, &LIMITS).unwrap();
    final_part.commit = true;
    let done = db
        .store
        .restore_records(ID, final_part.clone(), &LIMITS)
        .unwrap()
        .to_json();
    append(&db.store, ID, "unindexed");
    db.execute("UPDATE {{schema}}.turns SET search_text='legacyalias' WHERE turn=4");
    pending(db, ID);
    (final_part, done)
}

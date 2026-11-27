//! A transfer's trail pages on PostgreSQL: storage order through equal and
//! earlier times across page boundaries, earlier acts kept, rows refused.

use super::super::records_conformance::Database;
use crate::conversation::Redaction;
use crate::store::time::Stamp;

const ID: &str = "trail-session";

fn act(second: u32, actor: &str, turns: &[u64]) -> Redaction {
    Redaction {
        conversation_id: ID.into(),
        turns: turns.to_vec(),
        actor: Some(actor.into()),
        created_at: Stamp::parse(&format!("2026-08-19T10:00:{second:02}Z")).unwrap(),
    }
}

fn pages(db: &Database, limit: usize) -> (Vec<Redaction>, Vec<usize>) {
    let (mut after, mut acts, mut sizes) = (0, Vec::new(), Vec::new());
    loop {
        let (page, next) = db.store.trail_page(ID, after, limit).unwrap();
        sizes.push(page.len());
        acts.extend(page);
        match next {
            Some(next) => {
                assert!(next > after, "a page moves forward");
                after = next;
            }
            None => return (acts, sizes),
        }
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn pages_keep_storage_order_and_a_restore_keeps_earlier_acts() {
    let db = Database::new();
    let prior = [act(30, "prior", &[7])];
    db.store.restore_conversation(ID, &[], &[], &prior).unwrap();
    // b and c share a time across the boundary after the third act, e and f
    // after the fifth; times go backwards twice.
    let acts = [
        act(5, "a", &[1]),
        act(3, "b", &[2]),
        act(3, "c", &[1, 2]),
        act(1, "d", &[3]),
        act(1, "e", &[3]),
        act(9, "f", &[4]),
    ];
    let mut both = prior.to_vec();
    both.extend(acts.iter().cloned());
    db.store.restore_conversation(ID, &[], &[], &acts).unwrap();
    for limit in [2, 3, 7, 500] {
        let (read, sizes) = pages(&db, limit);
        assert_eq!(read, both, "pages of {limit}");
        assert!(sizes.iter().all(|size| *size <= limit));
    }
    assert_eq!(pages(&db, 7).1, [7, 0], "a full page asks once more");
    // The audit read is unchanged: newest first by time, ties by identity.
    let audit = db.store.turn_redactions(None, None, 3).unwrap();
    let actors: Vec<_> = audit.iter().map(|act| act.actor.clone().unwrap()).collect();
    assert_eq!(actors, ["prior", "f", "a"]);
    assert!(db.store.trail_page("other", 0, 5).unwrap().0.is_empty());
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_malformed_row_refuses_its_page() {
    let db = Database::new();
    db.store
        .restore_conversation(ID, &[], &[], &[act(1, "a", &[1])])
        .unwrap();
    db.execute(&format!(
        "INSERT INTO {{{{schema}}}}.turn_redactions (conversation_id, turns, actor, created_at) \
         VALUES ('{ID}', '{{}}', NULL, now())"
    ));
    let (page, next) = db.store.trail_page(ID, 0, 1).unwrap();
    assert_eq!(page.len(), 1);
    let refused = db.store.trail_page(ID, next.unwrap(), 1).unwrap_err();
    assert!(refused.text().contains("turns"), "{}", refused.text());
    assert!(db.store.trail_page(ID, 0, 0).is_err());
}

//! The import's guards (rulings H2, H4 and H6 of the T1e2 plan): an
//! archive that changes between the reads of the send is never committed;
//! a conversation without turns restored whole by a run whose answer was
//! lost is passed over when the target holds exactly its unit and refused
//! otherwise; a refusal is shown without the client's credential, its
//! message cut and a code outside the registry's grammar as unknown.

use std::cell::Cell;

use serde_json::json;

use super::driver_tests::{ID, archive};
use super::restore::{prepare, send};
use super::trail::Trail;
use crate::conversation::Redaction;
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{
    RecordHit, RecordRead, Restore, Restored, Staged, Write, Written, restore_id,
};
use crate::remote::records::{Failure, RecordPage, RecordQuery, Refusal};
use crate::state::records::{Fault, Records, Source};
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

/// Record methods that run `step` with each restore request's number
/// before it goes on; a fault `step` answers stands for the answer.
struct Stepped<'a> {
    inner: &'a dyn Records,
    calls: Cell<usize>,
    step: &'a dyn Fn(usize) -> Option<Fault>,
}

impl Records for Stepped<'_> {
    fn restore(&self, id: &str, restore: Restore) -> Result<Restored, Fault> {
        let call = self.calls.get() + 1;
        self.calls.set(call);
        match (self.step)(call) {
            Some(fault) => Err(fault),
            None => self.inner.restore(id, restore),
        }
    }

    fn write(&self, _: &str, _: Write) -> Result<Written, Fault> {
        unreachable!("the import only restores")
    }

    fn read(
        &self,
        _: &str,
        _: &RecordRead,
        _: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        unreachable!("the import only restores")
    }

    fn search(&self, _: &RecordQuery) -> Result<Vec<RecordHit>, Fault> {
        unreachable!("the import only restores")
    }

    fn sweep(&self, _: usize, _: Option<&str>, _: bool) -> Result<SweepPage, Fault> {
        unreachable!("the import only restores")
    }
}

/// A FileStore target that keeps records.
fn target(name: &str) -> (Scratch, Source) {
    let place = Scratch::new(name);
    let target = Source::File(FileStore::open(place.path()).unwrap());
    let Source::File(store) = &target else {
        unreachable!()
    };
    assert!(store.enable_records().unwrap());
    (place, target)
}

fn probe(records: &dyn Records) -> Restored {
    let probe = Restore {
        restore: restore_id(ID),
        ..Restore::default()
    };
    records.restore(ID, probe).unwrap()
}

#[test]
fn an_archive_erased_between_the_reads_of_the_send_is_not_committed() {
    let home = Scratch::new("transfer-guard-archive");
    // 2,600 acts: the second part leaves while the trail's sixth page is
    // still to be read.
    let source = archive(&home, 2_600);
    let (_place, target) = target("transfer-guard-target");
    let Source::File(store) = &target else {
        unreachable!()
    };
    let keeper = target.keeper().unwrap().unwrap();
    // The send's second part: its turns were read, the trail is still read.
    let erase = |call: usize| {
        if call == 3 {
            let late = Some("cli:late");
            source.redact_turns(ID, None, Some(&[4][..]), late).unwrap();
        }
        None
    };
    let stepped = Stepped {
        inner: &keeper,
        calls: Cell::new(0),
        step: &erase,
    };
    let trail = Trail::File(&source);
    let planned = prepare(&stepped, store, &source, &trail, (ID, &[])).unwrap();
    let refused = send(&stepped, &source, &trail, &planned, &[]).unwrap_err();
    let text = refused.text();
    assert!(
        text.contains("the archive changed while it was imported"),
        "{text}"
    );
    assert_eq!(stepped.calls.get(), 3, "no commit was sent");
    assert_eq!(probe(&keeper).state, Staged::Open);
    assert_eq!(store.last_turn_number(ID).unwrap(), 0);
}

fn act(turn: u64, second: usize) -> Redaction {
    Redaction {
        conversation_id: ID.into(),
        turns: vec![turn],
        actor: Some("cli:erase".into()),
        created_at: Stamp::parse(&format!(
            "2026-09-03T{:02}:{:02}:{:02}Z",
            second / 3_600,
            second / 60 % 60,
            second % 60
        ))
        .unwrap(),
    }
}

#[test]
fn a_unit_without_turns_restored_once_is_never_restored_again() {
    for (acts, rerun) in [(1, Some(1)), (500, None)] {
        let home = Scratch::new(&format!("transfer-guard-trail-{acts}"));
        let source = FileStore::open(home.path()).unwrap();
        let trail: Vec<Redaction> = (0..acts).map(|at| act(1, at)).collect();
        source.restore_conversation(ID, &[], &[], &trail).unwrap();
        let (_place, target) = target(&format!("transfer-guard-whole-{acts}"));
        let Source::File(store) = &target else {
            unreachable!()
        };
        let keeper = target.keeper().unwrap().unwrap();
        let read = Trail::File(&source);
        let selected = (&[][..], &[ID.to_string()][..]);
        let records: (Option<&dyn Records>, &[String]) = (Some(&keeper), &[]);
        let ids = [ID.to_string()];
        let held = || {
            store
                .turn_redactions(Some(&ids), None, 1_000)
                .unwrap()
                .len()
        };
        // The first run restores it whole; its answer is lost.
        let units = super::transfer(&source, store, &read, records, selected).unwrap();
        assert_eq!(units[0].redactions, acts);
        assert_eq!(held(), acts);
        // The rerun finds the trail: equal is done, and a trail the audit
        // read may not show whole is refused. Nothing is appended.
        let again = super::transfer(&source, store, &read, records, selected);
        match rerun {
            Some(count) => assert_eq!(again.unwrap()[0].redactions, count),
            None => assert!(again.err().unwrap().text().contains("target_occupied")),
        }
        assert_eq!(held(), acts);
    }
}

#[test]
fn a_refusal_is_shown_without_the_credential_and_with_a_lawful_code() {
    let home = Scratch::new("transfer-guard-refusal");
    let source = archive(&home, 3);
    let (_place, target) = target("transfer-guard-refused");
    let Source::File(store) = &target else {
        unreachable!()
    };
    let keeper = target.keeper().unwrap().unwrap();
    let echo = |_: usize| {
        Some(Fault::Remote(Failure::Refused(Box::new(Refusal {
            status: 429,
            retry_after: Some(30),
            code: Some("Bearer t0ken".into()),
            message: format!("Authorization: Bearer t0ken {}", "é".repeat(600)),
            details: json!({}),
        }))))
    };
    let stepped = Stepped {
        inner: &keeper,
        calls: Cell::new(0),
        step: &echo,
    };
    let trail = Trail::File(&source);
    let secrets = ["t0ken".to_string()];
    let refused = prepare(&stepped, store, &source, &trail, (ID, &secrets)).err();
    let text = refused.expect("refused").text();
    assert!(!text.contains("t0ken"), "{text}");
    assert!(
        text.contains("refused a restore request with 429 (unknown)"),
        "{text}"
    );
    assert!(text.contains("Bearer [credential]") && text.contains("retry after 30 seconds"));
    assert!(
        text.chars().filter(|c| *c == 'é').count() < 512,
        "the message is cut"
    );
}

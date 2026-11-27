//! The import's driver against a FileStore that keeps records, through the
//! same record methods a server answers: an import cut at every request,
//! before the request was applied and after it was applied with its answer
//! lost, is finished by a second import, which commits the archive's own
//! unit digest and publishes the conversation once. A request after a lost
//! answer is always the next run's probe.

use std::cell::{Cell, RefCell};

use serde_json::json;

use super::restore::{Start, prepare, send};
use super::trail::Trail;
use crate::conversation::message::{self, Message};
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{
    RecordHit, RecordRead, Restore, Restored, Staged, Write, Written, restore_id,
};
use crate::conversation::{Projection, Redaction, Turn};
use crate::remote::records::{Failure, RecordPage, RecordQuery};
use crate::state::State;
use crate::state::records::{Fault, Records, Source};
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

const ID: &str = "c-import";

/// Record methods that lose the `at`th restore request, applied first when
/// `applied`, and log every request's kind.
struct Faulty<'a> {
    inner: &'a dyn Records,
    at: usize,
    applied: bool,
    calls: Cell<usize>,
    log: RefCell<Vec<&'static str>>,
}

impl Records for Faulty<'_> {
    fn restore(&self, id: &str, restore: Restore) -> Result<Restored, Fault> {
        let call = self.calls.get() + 1;
        self.calls.set(call);
        let kind = match (restore.is_probe(), restore.commit) {
            (true, _) => "probe",
            (_, true) => "commit",
            _ => "part",
        };
        self.log.borrow_mut().push(kind);
        if call != self.at {
            return self.inner.restore(id, restore);
        }
        if self.applied {
            self.inner.restore(id, restore)?;
        }
        Err(Fault::Remote(Failure::Unreadable(
            "the answer was lost".into(),
        )))
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

fn said(role: &str, text: String) -> Message {
    message::from_json(&json!({"role": role, "content": text})).unwrap()
}

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

/// An archive whose stream takes three parts: a turn longer than a part,
/// a redacted turn, a sparse number, two projections and 2,100 acts.
fn archive(home: &Scratch) -> FileStore {
    let store = FileStore::open(home.path()).unwrap();
    let turn = |number: u64, messages: Vec<Message>| Turn {
        conversation_id: ID.into(),
        turn: number,
        redacted: messages.is_empty(),
        messages,
        created_at: stamp(&format!("2026-09-01T10:00:0{number}Z")),
        actor: Some("claude-code:t".into()),
    };
    let long = (0..18).map(|at| said("user", format!("{at:02}{}", "x".repeat(59_998))));
    let turns = [
        turn(1, long.collect()),
        turn(2, Vec::new()),
        turn(4, vec![said("assistant", "done".into())]),
    ];
    let entry = |turn: u64, text: &str| Projection {
        turn,
        kind: "log".into(),
        text: text.into(),
        span: 1,
    };
    let acts: Vec<Redaction> = (0..2_100)
        .map(|at| Redaction {
            conversation_id: ID.into(),
            turns: vec![2],
            actor: (at % 5 != 0).then(|| format!("cli:act-{at}")),
            created_at: stamp(&format!(
                "2026-09-02T0{}:{:02}:{:02}Z",
                at / 3_600,
                at / 60 % 60,
                at % 60
            )),
        })
        .collect();
    let entries = [entry(4, "four"), entry(1, "one")];
    store
        .restore_conversation(ID, &turns, &entries, &acts)
        .unwrap();
    store
}

/// One import of the archive's conversation into `records`.
fn import(records: &dyn Records, target: &dyn State, source: &FileStore) -> Result<Start, String> {
    let trail = Trail::File(source);
    let planned = prepare(records, target, source, &trail, ID).map_err(|e| e.text())?;
    send(records, source, &trail, &planned).map_err(|e| e.text())?;
    Ok(planned.start)
}

#[test]
fn an_import_cut_at_any_request_is_finished_by_the_next_import() {
    let home = Scratch::new("transfer-driver-archive");
    let source = archive(&home);
    let trail = Trail::File(&source);
    let digest = super::stream::walk(&source, &trail, ID, &mut |_, _| Ok(()))
        .unwrap()
        .digest;
    let read = |store: &FileStore| -> Vec<serde_json::Value> {
        let turns = store.read_turns(ID, 0, None).unwrap();
        turns.iter().map(crate::wire::encode::turn).collect()
    };
    let expected = read(&source);
    for at in 1..=4 {
        for applied in [false, true] {
            let place = Scratch::new(&format!("transfer-driver-{at}-{applied}"));
            let target = Source::File(FileStore::open(place.path()).unwrap());
            let Source::File(store) = &target else {
                unreachable!()
            };
            assert!(store.enable_records().unwrap());
            let keeper = target.keeper().unwrap().unwrap();
            let faulty = Faulty {
                inner: &keeper,
                at,
                applied,
                calls: Cell::new(0),
                log: RefCell::new(Vec::new()),
            };
            let cut = import(&faulty, store, &source).unwrap_err();
            assert!(cut.contains("run the same import again"), "{cut}");
            let first = faulty.log.borrow().clone();
            assert_eq!(first, ["probe", "part", "part", "commit"][..at]);
            if !(at == 4 && applied) {
                assert_eq!(
                    store.last_turn_number(ID).unwrap(),
                    0,
                    "visible before the commit"
                );
                assert!(store.conversations().unwrap().is_empty());
            }
            let again = Faulty {
                inner: &keeper,
                at: 0,
                applied: false,
                calls: Cell::new(0),
                log: RefCell::new(Vec::new()),
            };
            let start = import(&again, store, &source).unwrap();
            // The content parts the server applied before the cut.
            let held = (at - 1).saturating_sub(usize::from(!applied));
            match held {
                0 => assert_eq!(start, Start::Fresh),
                3 => assert_eq!(start, Start::Done),
                _ => assert!(matches!(start, Start::Resume(_)), "{start:?}"),
            }
            let log = again.log.borrow().clone();
            assert_eq!(log.first(), Some(&"probe"));
            assert_eq!(log.len(), if held == 3 { 1 } else { 4 }, "{log:?}");
            let probe = Restore {
                restore: restore_id(ID),
                ..Restore::default()
            };
            let done = keeper.restore(ID, probe).unwrap();
            assert_eq!(done.state, Staged::Committed);
            assert_eq!(done.digest.as_deref(), Some(digest.as_str()));
            assert_eq!(read(store), expected);
            assert_eq!(store.conversations().unwrap(), [ID]);
        }
    }
}

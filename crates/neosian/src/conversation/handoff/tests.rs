//! The handoff from whole turns and from heads, compared on the literal
//! parity corpus: the default view built from records is the released
//! rendering byte for byte on a records FileStore holding the corpus, and
//! the explicit views answer alike from records and from whole turns.

mod bounds;
mod ceilings;
pub(crate) mod corpus;

use std::cell::{Cell, RefCell};

use self::corpus::{calls, calls_as_lines, folded, sized};
use super::continue_conversation;
use crate::conversation::recall::View;
use crate::conversation::sessions::{HANDOFF_PATH, handoff_document};
use crate::conversation::turn_records::sweep::SweepPage;
use crate::conversation::turn_records::{RecordHit, RecordRead, Restore, Restored, Write, Written};
use crate::conversation::{Error, Projection, Turn};
use crate::remote::records::{RecordPage, RecordQuery};
use crate::state::State;
use crate::state::records::{Fault, Records, Source};
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;
use crate::testing::Scratch;

const SCOPE: &str = "user:handoff";

/// A records FileStore and a plain one, each holding `conversations`.
struct Stores {
    kept: Source,
    plain: FileStore,
    _dirs: (Scratch, Scratch),
}

type Conversation = (&'static str, Vec<Turn>, Vec<Projection>);

fn stores(name: &str, conversations: &[Conversation]) -> Stores {
    let (kept_dir, plain_dir) = (
        Scratch::new(&format!("{name}-kept")),
        Scratch::new(&format!("{name}-plain")),
    );
    let kept = FileStore::open(kept_dir.path()).unwrap();
    kept.enable_records().unwrap();
    let plain = FileStore::open(plain_dir.path()).unwrap();
    for (id, turns, entries) in conversations {
        kept.restore_conversation(id, turns, entries, &[]).unwrap();
        plain.restore_conversation(id, turns, entries, &[]).unwrap();
    }
    Stores {
        kept: Source::File(kept),
        plain,
        _dirs: (kept_dir, plain_dir),
    }
}

fn shown(store: &dyn State, records: Option<&dyn Records>, id: &str, view: View) -> String {
    match continue_conversation(store, records, SCOPE, Some(id), None, view) {
        Ok(Ok(delivered)) => delivered.text,
        Ok(Err(corrective)) => panic!("{}", corrective.error),
        Err(error) => panic!("{}", error.text()),
    }
}

/// The released text from whole turns, and the default view from heads,
/// both from the records store.
fn light(stores: &Stores, id: &str) -> (String, String) {
    let state = stores.kept.state();
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let released = shown(state, None, id, View::Light);
    (released, shown(state, Some(&keeper), id, View::Light))
}

fn note(store: &dyn State, id: &str) {
    let written = Stamp::parse("2026-11-27T09:00:00Z").unwrap();
    let text = handoff_document("codex:1", written, "Pick up the views.", Some(id), None);
    let actor = Actor::parse("codex:1").unwrap();
    store
        .write(SCOPE, HANDOFF_PATH, &text, &actor, None)
        .unwrap();
}

fn corpus() -> Vec<Conversation> {
    let (fold, entries) = folded("c-fold");
    vec![
        ("c-calls", calls("c-calls"), Vec::new()),
        ("c-lines", calls_as_lines("c-lines"), Vec::new()),
        ("c-fold", fold, entries),
        ("c-many", sized("c-many", 40, 1_200), Vec::new()),
        ("c-share", sized("c-share", 6, 5_000), Vec::new()),
        ("c-newest", sized("c-newest", 3, 30_000), Vec::new()),
    ]
}

#[test]
fn the_default_view_from_heads_is_the_released_rendering() {
    let stores = stores("handoff-parity", &corpus());
    note(stores.kept.state(), "c-calls");
    for (id, _, _) in corpus() {
        let (released, kept) = light(&stores, id);
        assert_eq!(kept, released, "{id}");
    }
    let checks = [
        (
            "c-calls",
            "Pick up the views.\n[end of note]\n[1] USER: Read https://",
        ),
        (
            "c-calls",
            "→ error: no match in [link c-calls:2]\nAGENT: Nothing matched.",
        ),
        (
            "c-calls",
            "TOOL Read({\"file_path\":\"/tmp/a\"}) → ?\nAGENT: It is not used.",
        ),
        (
            "c-calls",
            "TOOL Screenshot({}) → \nTOOL Bash({\"command\":\"true\"}) → ok",
        ),
        (
            "c-lines",
            "\n[2] USER: Now café: check [link c-lines:3] in [link c-lines:2] again | ",
        ),
        (
            "c-lines",
            "\"note\":\"na\\u00efve \\u2603 search\"}) → error: no match in",
        ),
        (
            "c-lines",
            "\n[3] USER: Where is [link c-lines:5] used? | AGENT: Checking two things.",
        ),
        (
            "c-fold",
            "\n[1-11] 11 earlier turns not shown\n[12] USER: prompt 12 [link c-fold:1] ",
        ),
        (
            "c-fold",
            "\n[18-20] turns 18 to 20 settled the plan\n[21] [redacted]\n[22] turn twenty",
        ),
        ("c-share", "\n[2] USER: 2: abcd"),
        (
            "c-share",
            " … [recall_turn(2)] | AGENT: reply 2\n[3] USER: 3: abcd",
        ),
        ("c-newest", "\n[2] USER: 2: abcd"),
        (
            "c-newest",
            " … [recall_turn(2)] | AGENT: reply 2\n[3] USER: 3: abcd",
        ),
    ];
    for (id, shown) in checks {
        let (released, _) = light(&stores, id);
        assert!(released.contains(shown), "{id}: {shown}\n{released}");
    }
}

#[test]
fn the_views_read_alike_from_records_and_whole_turns_within_the_budget() {
    let stores = stores("handoff-views", &corpus());
    let keeper = stores.kept.keeper().unwrap().unwrap();
    for (id, _, _) in corpus() {
        for view in [View::Prompts, View::Calls, View::Full] {
            let kept = shown(stores.kept.state(), Some(&keeper), id, view);
            let whole = shown(&stores.plain, None, id, view);
            assert_eq!(kept, whole, "{id} {view:?}");
            assert!(kept.chars().count() <= 32_768, "{id} {view:?}");
        }
        let light = shown(&stores.plain, None, id, View::Light);
        assert_eq!(light, shown(stores.kept.state(), None, id, View::Light));
    }
}

/// A store's records, counted: every request and the text bytes answered.
pub struct Counted<'a> {
    inner: &'a dyn Records,
    pub asked: RefCell<Vec<RecordRead>>,
    pub bytes: Cell<u64>,
}

impl<'a> Counted<'a> {
    pub fn new(inner: &'a dyn Records) -> Self {
        Self {
            inner,
            asked: RefCell::new(Vec::new()),
            bytes: Cell::new(0),
        }
    }
}

fn not_here<T>() -> Result<T, Fault> {
    Err(Fault::Local(Error::Value("not asked here".into())))
}

impl Records for Counted<'_> {
    fn write(&self, _: &str, _: Write) -> Result<Written, Fault> {
        not_here()
    }

    fn read(
        &self,
        id: &str,
        read: &RecordRead,
        visit: &mut dyn FnMut(RecordPage) -> Result<bool, Fault>,
    ) -> Result<(), Fault> {
        self.asked.borrow_mut().push(read.clone());
        self.inner.read(id, read, &mut |page| {
            let text = |answered: &crate::remote::records::Answered| {
                let tail = answered.tail.as_ref().map_or(0, String::len);
                (answered.record.text.len() + tail) as u64
            };
            let bytes: u64 = page.records.iter().map(text).sum();
            self.bytes.set(self.bytes.get() + bytes);
            visit(page)
        })
    }

    fn search(&self, _: &RecordQuery) -> Result<Vec<RecordHit>, Fault> {
        not_here()
    }

    fn restore(&self, _: &str, _: Restore) -> Result<Restored, Fault> {
        not_here()
    }

    fn sweep(&self, _: usize, _: Option<&str>, _: bool) -> Result<SweepPage, Fault> {
        not_here()
    }
}

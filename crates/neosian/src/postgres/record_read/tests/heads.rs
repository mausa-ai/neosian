//! The database answers heads from piece boundaries. Every legal split must
//! produce the same bytes, page cuts and positions as a FileStore.
use super::*;
use crate::conversation::turn_records::TurnRecord;

struct Pair {
    db: Database,
    files: FileStore,
    _scratch: Scratch,
}

impl Pair {
    fn new(name: &str, chunks: &[Vec<String>]) -> Self {
        let db = Database::new();
        let scratch = Scratch::new(name);
        let files = FileStore::open(scratch.path()).unwrap();
        files.enable_records().unwrap();
        let mut records = vec![];
        for (index, texts) in chunks.iter().enumerate() {
            let body = texts.concat();
            let template = records_of(&messages(&[json!({"role":"user","content":body})]))
                .unwrap()
                .remove(0);
            for (chunk, text) in texts.iter().enumerate() {
                records.push(TurnRecord {
                    seq: records.len() as u64,
                    message: index as u64,
                    chunk: chunk as u64,
                    content: template.content.filter(|_| chunk == 0),
                    text: text.clone(),
                    ..template.clone()
                });
            }
        }
        let write = || Write {
            draft: "heads".into(),
            records: records.clone(),
            seal: true,
            ..Write::default()
        };
        let limits = limits::Records::load().unwrap();
        db.store.write_records(ID, write(), &limits).unwrap();
        let stamp = files
            .write_records(ID, write(), &limits)
            .unwrap()
            .turn
            .unwrap()
            .created_at
            .z();
        // Comparable headers include the exact same timestamp and byte length.
        db.execute(&format!(
            "UPDATE {{{{schema}}}}.turns SET created_at='{stamp}';
            UPDATE {{{{schema}}}}.record_turns SET created_at='{stamp}'"
        ));
        Self {
            db,
            files,
            _scratch: scratch,
        }
    }

    fn page(&self, read: &RecordRead) -> (Vec<ReadItem>, Option<ReadPosition>) {
        let mut file = vec![];
        let mut pg = vec![];
        let file_cursor = self
            .files
            .read_records(ID, read, &mut |item| {
                file.push(item);
                Ok(())
            })
            .unwrap();
        let pg_cursor = self
            .db
            .store
            .read_records(ID, read, &mut |item| {
                pg.push(item);
                Ok(())
            })
            .unwrap();
        let position = |p: ReadPosition| (p.turn, p.seq);
        assert_eq!(
            file_cursor.map(position),
            pg_cursor.map(position),
            "continuation position"
        );
        assert_eq!(pg, file, "read {read:?}");
        (pg, pg_cursor)
    }
}

fn head_read(bytes: usize) -> RecordRead {
    RecordRead {
        turn: Some(1),
        heads: vec![(Kind::Prompt, bytes)],
        ..Default::default()
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn heads_and_page_cursors_cross_empty_boundary_windows() {
    let pair = Pair::new(
        "pg-head-range",
        &[
            vec!["αβ".to_owned(); 384],
            vec!["cd".to_owned(); 128],
            vec!["one last chunk".to_owned()],
        ],
    );
    let mut read = RecordRead {
        page_bytes: 1,
        ..head_read(5)
    };
    for (start, expected_next) in [(0, Some(384)), (384, Some(512)), (512, None)] {
        let (items, next) = pair.page(&read);
        assert_eq!(items.len(), 2);
        let (record, _, truncated) = piece(&items[1]);
        assert_eq!((record.seq, truncated), (start, true));
        assert_eq!(next.map(|at| at.seq), expected_next);
        read.continuation = next.map(|at| at.encode(&read.fingerprint(ID)));
    }
    let read = RecordRead {
        seq: Some(127),
        page_bytes: 1,
        ..head_read(5)
    };
    let (items, next) = pair.page(&read);
    let (record, _, truncated) = piece(&items[1]);
    assert_eq!((record.seq, truncated), (127, false));
    assert_eq!(next.unwrap().seq, 128);
}

fn piece(item: &ReadItem) -> (&TurnRecord, Option<&str>, bool) {
    match item {
        ReadItem::Record {
            record,
            tail,
            truncated,
            ..
        } => (record, tail.as_deref(), *truncated),
        _ => panic!("expected a record"),
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_head_skips_the_strict_interior_even_when_its_tail_needs_two_chunks() {
    let long = |ch: &str| ch.repeat(400);
    let pair = Pair::new(
        "pg-head-skips-interior",
        &[
            vec![long("a"), long("b"), long("c"), long("d"), long("e")],
            vec![long("A"), long("B"), long("C"), long("D"), "END".into()],
        ],
    );
    pair.db
        .execute("UPDATE {{schema}}.turn_records SET digest=decode('00','hex') WHERE seq IN (2,7)");
    let read = head_read(17);
    let (page, next) = pair.page(&read);
    assert!(next.is_none());
    assert_eq!(page.len(), 3);
    let (first, tail, cut) = piece(&page[1]);
    assert_eq!(
        (first.seq, first.chunk, first.size, cut),
        (0, 0, 2000, true)
    );
    assert_eq!(first.text, "a".repeat(15));
    assert_eq!(tail, Some("eeeeeeeeeeeeeee"));
    let (second, tail, cut) = piece(&page[2]);
    assert_eq!((second.seq, second.size, cut), (5, 1603, true));
    assert_eq!(second.text, "A".repeat(15));
    assert_eq!(tail, Some("DDDDDDDDDDDDEND"));
    let failed = pair
        .db
        .store
        .read_records(ID, &RecordRead::default(), &mut |_| Ok(()))
        .unwrap_err();
    assert!(failed.text().contains("record 2"), "{}", failed.text());
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn tiny_chunks_match_the_encoded_head_tail_and_twice_the_budget_boundary() {
    let small = ["é", "\"", "界", "\n", "\\", "q"]
        .repeat(12)
        .into_iter()
        .map(str::to_owned)
        .collect();
    let exact = ["\"", "a", "\"", "a", "\"", "a", "z"]
        .map(str::to_owned)
        .to_vec();
    let mut above = exact.clone();
    above.push("\"".into());
    let pair = Pair::new("pg-head-tiny-splits", &[small, exact, above]);
    let (page, _) = pair.page(&head_read(23));
    let (record, tail, cut) = piece(&page[1]);
    assert_eq!((record.size, cut), (108, true));
    assert_eq!(record.text, "é\"界\n\\qé\"界\n");
    assert_eq!(tail, Some("界\n\\qé\"界\n\\q"));
    // At exactly 2*6 encoded bytes all seven chunks stay plain. One
    // additional escaped quote makes the third piece a single head record.
    let (page, _) = pair.page(&head_read(6));
    assert_eq!(page.len(), 10);
    for (index, item) in page[2..9].iter().enumerate() {
        let (record, tail, cut) = piece(item);
        assert_eq!(
            (record.seq, record.chunk, tail, cut),
            (72 + index as u64, index as u64, None, false)
        );
    }
    let (record, tail, cut) = piece(&page[9]);
    assert_eq!(
        (record.seq, record.text.as_str(), tail, cut),
        (79, "\"a", Some("az\""), true)
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn pages_resume_inside_full_pieces_and_after_whole_head_items() {
    let pair = Pair::new(
        "pg-head-page-cuts",
        &[
            vec!["abcdefghijkl".into(); 6],
            vec!["abcdefghijkl".into(); 4],
        ],
    );
    for (heads, want) in [
        (false, vec![0, 1, 2, 3, 4, 5, 6, 7, 8, 9]),
        (true, vec![0, 6]),
    ] {
        let mut read = RecordRead {
            turn: Some(1),
            page_bytes: 1,
            ..Default::default()
        };
        if heads {
            read.heads.push((Kind::Prompt, 5));
        }
        for (n, seq) in want.iter().enumerate() {
            let (page, next) = pair.page(&read);
            assert_eq!(page.len(), 2, "one header and one record");
            let (record, _, cut) = piece(&page[1]);
            assert_eq!((record.seq, cut), (*seq, heads));
            assert_eq!(next.map(|p| p.seq), want.get(n + 1).copied());
            read.continuation = next.map(|p| p.encode(&read.fingerprint(ID)));
        }
    }
    // An explicit seq inside a piece starts with the remaining full chunks;
    // the following piece still receives its requested head.
    let read = RecordRead {
        seq: Some(2),
        ..head_read(5)
    };
    let (page, next) = pair.page(&read);
    assert!(next.is_none());
    let received: Vec<_> = page[1..]
        .iter()
        .map(|i| {
            let (r, _, cut) = piece(i);
            (r.seq, cut)
        })
        .collect();
    assert_eq!(
        received,
        [(2, false), (3, false), (4, false), (5, false), (6, true)]
    );
}

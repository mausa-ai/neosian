//! A read whose server goes backwards is refused (ruling R42): across the
//! pages of one call a header's turn never goes down and a record's turn
//! and `seq` strictly go up. A page that only repeats a header is a
//! legitimate answer and passes.

use serde_json::{Value, json};

use super::{connected, page, unreadable};
use crate::conversation::turn_records::RecordRead;
use crate::remote::records::RecordPage;

/// Read the stub's pages to the end or to a refusal: pages visited, and
/// what was refused.
fn read(pages: Vec<Value>) -> (usize, Option<String>, usize) {
    let answers = pages.into_iter().map(|page| (200, page)).collect();
    let (server, store) = connected(answers);
    let mut visited = 0;
    let mut visit = |_: RecordPage| {
        visited += 1;
        Ok(true)
    };
    let ended = store.read_records("c", &RecordRead::default(), &mut visit);
    let refused = ended.is_err().then(|| unreadable(ended));
    let requests = server.requests.lock().unwrap().len() - 1;
    (visited, refused, requests)
}

/// A page holding only the header of `turn`.
fn header_only(turn: u64, continuation: Option<&str>) -> Value {
    let mut only = page(turn, 0, continuation);
    only["records"] = json!([]);
    only
}

#[test]
fn replayed_records_under_alternating_continuations_are_refused() {
    // A, B, A, B: each continuation differs from the one just sent, so
    // only the records show the read is going round.
    let pages = vec![
        page(1, 0, Some("a")),
        page(1, 1, Some("b")),
        page(1, 0, Some("a")),
    ];
    let backwards = Some("records go backwards".to_string());
    assert_eq!(read(pages), (2, backwards.clone(), 3));
    // The same record again is not forward either.
    let pages = vec![page(1, 4, Some("a")), page(1, 4, Some("b"))];
    assert_eq!(read(pages), (1, backwards.clone(), 2));
    // Nor is an earlier turn's record behind a header that passes.
    let mut mixed = page(2, 0, Some("b"));
    mixed["records"][0]["turn"] = json!(1);
    assert_eq!(read(vec![page(2, 0, Some("a")), mixed]), (1, backwards, 2));
}

#[test]
fn a_header_of_an_earlier_turn_is_refused() {
    let pages = vec![page(2, 0, Some("a")), header_only(1, Some("b"))];
    let backwards = Some("turns go backwards".to_string());
    assert_eq!(read(pages), (1, backwards.clone(), 2));
    // Within one page too.
    let mut page = header_only(3, None);
    let earlier = header_only(2, None)["turns"][0].clone();
    page["turns"].as_array_mut().unwrap().push(earlier);
    assert_eq!(read(vec![page]), (0, backwards, 1));
}

#[test]
fn a_page_that_only_repeats_a_header_is_accepted() {
    // A page that ends inside a turn is followed by one that repeats the
    // turn's header; under a view its remaining records may all be left out.
    let pages = vec![
        page(1, 0, Some("a")),
        header_only(1, Some("b")),
        page(1, 7, Some("c")),
        header_only(1, Some("d")),
        page(2, 0, None),
    ];
    assert_eq!(read(pages), (5, None, 5));
}

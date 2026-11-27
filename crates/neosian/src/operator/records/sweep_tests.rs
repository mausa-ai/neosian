//! The page loop of `records drafts` against made-up pages: every page is
//! followed until a page names no cursor, and a page that cannot advance
//! or a later page's failure stops the loop after the rows already
//! printed.

use super::pages;
use crate::conversation::Error;
use crate::conversation::turn_records::sweep::{SweepConversation, SweepPage};
use crate::remote::records::Failure;
use crate::state::records::Fault;

fn row(id: &str) -> SweepConversation {
    SweepConversation {
        conversation_id: id.into(),
        drafts: 1,
        restores: 0,
        freed_bytes: 7,
    }
}

fn page(ids: &[&str], next: Option<&str>, write: bool) -> SweepPage {
    SweepPage {
        conversations: ids.iter().map(|id| row(id)).collect(),
        next_cursor: next.map(str::to_string),
        write,
    }
}

fn line(id: &str) -> String {
    format!("{{\"conversation_id\":\"{id}\",\"drafts\":1,\"restores\":0,\"freed_bytes\":7}}\n")
}

/// The loop over `answers` in order, with the cursors it sent.
fn run(
    write: bool,
    answers: Vec<Result<SweepPage, Fault>>,
) -> (Result<(), Error>, String, Vec<Option<String>>) {
    let mut answers = answers.into_iter();
    let mut asked = Vec::new();
    let mut out = Vec::new();
    let result = pages(
        |cursor| {
            asked.push(cursor.map(str::to_string));
            answers.next().expect("no page asked past the last")
        },
        write,
        &mut out,
    );
    (result, String::from_utf8(out).unwrap(), asked)
}

#[test]
fn every_page_is_followed_until_one_names_no_cursor() {
    for write in [false, true] {
        let (result, out, asked) = run(
            write,
            vec![
                Ok(page(&["a", "b"], Some("after-b"), write)),
                Ok(page(&["c"], Some("after-c"), write)),
                Ok(page(&["d"], None, write)),
            ],
        );
        result.unwrap();
        assert_eq!(out, line("a") + &line("b") + &line("c") + &line("d"));
        let cursors: Vec<_> = asked.iter().map(Option::as_deref).collect();
        assert_eq!(cursors, [None, Some("after-b"), Some("after-c")]);
    }
}

#[test]
fn an_empty_store_prints_nothing() {
    let (result, out, asked) = run(false, vec![Ok(page(&[], None, false))]);
    result.unwrap();
    assert_eq!((out.as_str(), asked.len()), ("", 1));
}

#[test]
fn a_later_page_failure_is_returned_after_the_rows_printed() {
    let refused = Fault::Remote(Failure::Unreadable("sweep_records".into()));
    let (result, out, _) = run(
        true,
        vec![Ok(page(&["a"], Some("after-a"), true)), Err(refused)],
    );
    assert_eq!(out, line("a"));
    let error = result.unwrap_err().text();
    assert_eq!(error, "malformed wire response: sweep_records");
}

#[test]
fn a_page_that_cannot_advance_stops_the_loop() {
    let cases = [
        (
            vec![
                Ok(page(&["a"], Some("same"), false)),
                Ok(page(&["b"], Some("same"), false)),
            ],
            "page 2 of the sweep named the cursor it was sent",
            line("a"),
        ),
        (
            vec![Ok(page(&[], Some("after"), false))],
            "page 1 of the sweep named a next cursor without a row",
            String::new(),
        ),
        (
            vec![
                Ok(page(&["b"], Some("after-b"), false)),
                Ok(page(&["b"], None, false)),
            ],
            "page 2 of the sweep answered a conversation out of order",
            line("b"),
        ),
        (
            vec![Ok(page(&["a"], None, true))],
            "page 1 of the sweep did not echo the write it was asked",
            String::new(),
        ),
    ];
    for (answers, why, printed) in cases {
        let (result, out, _) = run(false, answers);
        let error = result.unwrap_err().text();
        assert_eq!(error, format!("{why}; nothing more was asked"));
        assert_eq!(out, printed);
    }
}

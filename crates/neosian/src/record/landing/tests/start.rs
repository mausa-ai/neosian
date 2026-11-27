//! The start rules of ruling D15 (DESIGN §40.54 item 17.3): Stop,
//! SessionStart and UserPromptSubmit start a lander only for a queued turn
//! whose destination is neither blocked nor waiting, and only while a slot
//! is free; PostToolUse never does. The slots are probed last, and only
//! when the rules would start one. A hook hands its URL to the lander only
//! when the descriptor cannot carry it.

use std::cell::Cell;

use super::super::start::{Event, Found, handed, starts};
use crate::remote::selection::Selection;

#[test]
fn the_start_rules_table() {
    let events = [Event::Stop, Event::Prompt, Event::SessionStart, Event::Tool];
    let mut started = 0;
    for event in events {
        for bits in 0..16u8 {
            let found = Found {
                queued: bits & 1 != 0,
                blocked: bits & 2 != 0,
                waiting: bits & 4 != 0,
            };
            let free = bits & 8 != 0;
            let probed = Cell::new(false);
            let starts = starts(event, found, || {
                probed.set(true);
                Ok(free)
            })
            .unwrap();
            let wanted = event != Event::Tool && found.queued && !found.blocked && !found.waiting;
            assert_eq!(probed.get(), wanted, "{event:?} {found:?}");
            assert_eq!(starts, wanted && free, "{event:?} {found:?} free {free}");
            started += usize::from(starts);
        }
    }
    // One row per starting event: queued, free, a slot open.
    assert_eq!(started, 3);
}

#[test]
fn hook_events_are_read_by_their_names() {
    let read = |name: &str| Event::of(name);
    assert_eq!(read("Stop"), Event::Stop);
    assert_eq!(read("AssistantResponse"), Event::Stop);
    assert_eq!(read("UserPromptSubmit"), Event::Prompt);
    assert_eq!(read("SessionStart"), Event::SessionStart);
    for other in [
        "PostToolUse",
        "MailboxPoll",
        "MailboxContext",
        "CursorImported",
    ] {
        assert_eq!(read(other), Event::Tool, "{other}");
    }
}

#[test]
fn a_url_is_handed_over_only_when_its_descriptor_loses_part_of_it() {
    let remote = |url: &str| Selection::Remote {
        url: url.into(),
        token: "t".into(),
    };
    for plain in ["http://127.0.0.1:8080", "https://host/base/", "https://h/p"] {
        assert_eq!(handed(&remote(plain)), None, "{plain}");
    }
    for full in ["https://user:pw@host/v", "https://host/v?tenant=a"] {
        assert_eq!(handed(&remote(full)), Some(full), "{full}");
    }
    assert_eq!(handed(&Selection::File("/tmp/root".into())), None);
}

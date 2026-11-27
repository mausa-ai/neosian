//! Recall and continue through a profile whose history source keeps
//! records: the doors' reader reads the source's records under the
//! profile's history admission, so a conversation the selection does not admit is refused
//! before the source is asked, as the legacy read refuses it; the search
//! door's request is limited to what the selection admits.

use super::{OUTSIDE, enabled, profile, routed, sealed};
use crate::conversation::handoff::continue_conversation;
use crate::conversation::rank::Order;
use crate::conversation::recall::{Ask, View, recall};
use crate::conversation::record_search::Asked;
use crate::conversation::turn_records::RecordHit;
use crate::profile::model::CaptureMode;
use crate::state::records::{HistoryReader, Records, Source};
use crate::store::FileStore;
use crate::testing::Scratch;

#[test]
fn recall_reads_records_only_where_the_profile_admits() {
    let history = Scratch::new("routing-recall");
    let store = FileStore::open(history.path()).unwrap();
    store.enable_records().unwrap();
    let direct = Source::File(FileStore::open(history.path()).unwrap());
    let keeper = direct.keeper().unwrap().unwrap();
    for id in ["w-a", "x-c"] {
        keeper.write(id, sealed("a needle here")).unwrap();
    }
    let memory = Scratch::new("routing-recall-memory");
    let reader = HistoryReader::Profile(routed(
        profile(Some("w-"), &[], CaptureMode::Off),
        enabled(&memory),
        Source::File(store),
    ));
    let records = reader.records().unwrap();
    assert!(records.is_some(), "the history source keeps records");
    let state = reader.state();
    let shown = |id: &str| {
        let ask = Ask::new(id, 1, None, None, None, None).unwrap();
        recall(state.as_ref(), records.as_deref(), &ask, "Conversation")
    };
    let admitted = shown("w-a").unwrap().unwrap().text;
    assert!(admitted.ends_with("\nUSER: a needle here"), "{admitted}");
    let refused = shown("x-c").unwrap_err();
    assert_eq!(refused.text(), OUTSIDE);
}

#[test]
fn continue_reads_records_only_where_the_profile_admits() {
    let history = Scratch::new("routing-continue");
    let store = FileStore::open(history.path()).unwrap();
    store.enable_records().unwrap();
    let direct = Source::File(FileStore::open(history.path()).unwrap());
    let keeper = direct.keeper().unwrap().unwrap();
    for id in ["w-a", "x-c"] {
        keeper.write(id, sealed("a needle here")).unwrap();
    }
    let memory = Scratch::new("routing-continue-memory");
    let reader = HistoryReader::Profile(routed(
        profile(Some("w-"), &[], CaptureMode::Off),
        enabled(&memory),
        Source::File(store),
    ));
    let records = reader.records().unwrap();
    let state = reader.state();
    let scope = "workspace:history";
    for view in [View::Light, View::Full] {
        let shown = |id: &str| {
            continue_conversation(
                state.as_ref(),
                records.as_deref(),
                scope,
                Some(id),
                None,
                view,
            )
        };
        let admitted = shown("w-a").unwrap().ok().unwrap().text;
        assert!(
            admitted.contains("\n[1] USER: a needle here\n"),
            "{admitted}"
        );
        let refused = shown("x-c").err().unwrap();
        assert_eq!(refused.text(), OUTSIDE);
    }
}

#[test]
fn the_search_door_finds_records_only_where_the_profile_admits() {
    let history = Scratch::new("routing-door-search");
    let store = FileStore::open(history.path()).unwrap();
    store.enable_records().unwrap();
    let direct = Source::File(FileStore::open(history.path()).unwrap());
    let keeper = direct.keeper().unwrap().unwrap();
    for id in ["w-a", "w-b", "x-c"] {
        keeper.write(id, sealed("a needle here")).unwrap();
    }
    let memory = Scratch::new("routing-door-search-memory");
    let reader = HistoryReader::Profile(routed(
        profile(None, &["w-a", "w-b"], CaptureMode::Off),
        enabled(&memory),
        Source::File(store),
    ));
    let records = reader.records().unwrap().unwrap();
    let asked = |conversations: Option<&[&str]>| Asked {
        query: "NEEDLE".into(),
        conversations: conversations.map(|ids| ids.iter().map(|id| id.to_string()).collect()),
        kinds: None,
        order: Order::Newest,
        since: None,
        before: None,
        limit: 10,
    };
    let found = |hits: Vec<RecordHit>| {
        let mut ids: Vec<String> = hits.into_iter().map(|hit| hit.conversation_id).collect();
        ids.sort();
        ids
    };
    // Naming none searches what the selection admits, never the whole store.
    let implicit = asked(None).search(records.as_ref()).unwrap();
    assert_eq!(found(implicit), ["w-a", "w-b"]);
    let named = asked(Some(&["w-b"])).search(records.as_ref()).unwrap();
    assert_eq!(found(named), ["w-b"]);
    let refused = asked(Some(&["x-c"])).search(records.as_ref()).unwrap_err();
    assert_eq!(refused.into_error().text(), OUTSIDE);
    // Through a direct store the same request reaches every conversation.
    let everything = asked(None).search(&keeper).unwrap();
    assert_eq!(found(everything), ["w-a", "w-b", "x-c"]);
}

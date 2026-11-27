//! Recall through a profile whose history source keeps records: the
//! doors' reader reads the source's records under the profile's history
//! admission, so a conversation the selection does not admit is refused
//! before the source is asked, as the legacy read refuses it.

use super::{OUTSIDE, enabled, profile, routed, sealed};
use crate::conversation::recall::{Ask, recall};
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

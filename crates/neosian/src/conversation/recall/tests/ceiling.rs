//! The light view's whole answer, its title and its last line included,
//! stays within its 65,536 bytes (the T1fe ruling J5), whether the turn is
//! read whole or from records.

use serde_json::json;

use super::{LABEL, message};
use crate::conversation::recall::{Ask, recall};
use crate::state::State;
use crate::state::records::{Records, Source};
use crate::store::FileStore;
use crate::testing::Scratch;

const ID: &str = "c-recall";

#[test]
fn two_long_prompts_and_the_title_stay_within_the_light_ceiling() {
    let prompt = || message(json!({"role": "user", "content": "q".repeat(32_752)}));
    let (plain, kept) = (
        Scratch::new("recall-ceiling"),
        Scratch::new("recall-ceiling-kept"),
    );
    let legacy = FileStore::open(plain.path()).unwrap();
    let enabled = FileStore::open(kept.path()).unwrap();
    enabled.enable_records().unwrap();
    for store in [&legacy, &enabled] {
        store
            .append_turn(ID, vec![prompt(), prompt()], None)
            .unwrap();
    }
    let enabled = Source::File(enabled);
    let keeper = enabled.keeper().unwrap().unwrap();
    let ask = Ask::new(ID, 1, None, None, None, None).unwrap();
    let origins: [(&dyn State, Option<&dyn Records>); 2] =
        [(&legacy, None), (enabled.state(), Some(&keeper))];
    for (store, records) in origins {
        let text = recall(store, records, &ask, LABEL).unwrap().unwrap().text;
        assert!(text.len() <= 65_536, "{}", text.len());
        assert!(text.starts_with("Turn 1 (light: "));
        let end = "[the light view stops at 65,536 bytes; the turn goes on from record 1:";
        assert!(text.lines().last().unwrap().starts_with(end));
    }
}

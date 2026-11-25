//! A request's `kinds` as the stores take it (DESIGN §40.54 items 10 and
//! 11): a set. Every entry is checked, and a kind named again is folded
//! into its first mention as the list is decoded, so a store compares each
//! record with at most eight kinds however long the request's list is.

use super::envelope::Fail;
use super::strict::{Strict, echoed};
use crate::conversation::turn_records::Kind;

/// One kind by its name.
pub fn kind(name: &str) -> Result<Kind, Fail> {
    let unknown = || Fail::value(format!("unknown record kind {:?}", echoed(name)));
    Kind::parse(name).ok_or_else(unknown)
}

/// The list at `name`, each kind once in the order first named; none when
/// the field is absent.
pub fn kinds(body: &Strict, name: &str) -> Result<Option<Vec<Kind>>, Fail> {
    let Some(items) = body.optional_list(name)? else {
        return Ok(None);
    };
    let mut named = Vec::with_capacity(Kind::ALL.len());
    for item in items {
        let Some(text) = item.as_str() else {
            let why = format!("parameter '{name}' must be a list of kinds");
            return Err(Fail::value(why));
        };
        let kind = kind(text)?;
        if !named.contains(&kind) {
            named.push(kind);
        }
    }
    Ok(Some(named))
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};

    use super::*;

    fn decoded(list: Value) -> Result<Option<Vec<Kind>>, Fail> {
        let payload = json!({"kinds": list}).as_object().unwrap().clone();
        kinds(&Strict::new(&payload, &["kinds"]).unwrap(), "kinds")
    }

    #[test]
    fn a_kind_named_again_is_folded_into_its_first_mention() {
        let twice = decoded(json!(["reply", "prompt", "reply", "prompt", "reply"]));
        assert_eq!(twice.unwrap(), Some(vec![Kind::Reply, Kind::Prompt]));
        // A million repeats decode to one kind; the list never grows past
        // the eight there are.
        let mut many = vec![json!("extra"); 1_000_000];
        many.extend(Kind::ALL.iter().rev().map(|kind| json!(kind.as_str())));
        let folded = decoded(Value::Array(many)).unwrap().unwrap();
        assert_eq!(folded.len(), 8);
        assert_eq!((folded[0], folded[1]), (Kind::Extra, Kind::Attachment));
        assert!(folded.capacity() <= 8);
        assert_eq!(decoded(json!([])).unwrap(), Some(Vec::new()));
        let payload = json!({}).as_object().unwrap().clone();
        let absent = kinds(&Strict::new(&payload, &["kinds"]).unwrap(), "kinds");
        assert_eq!(absent.unwrap(), None);
    }

    #[test]
    fn every_entry_is_checked_wherever_it_stands() {
        let mut late = vec![json!("prompt"); 100];
        late.push(json!("prompts"));
        let refused = decoded(Value::Array(late)).unwrap_err();
        assert_eq!(refused.message, "unknown record kind \"prompts\"");
        // A refusal repeats at most 128 scalars of the name it was sent.
        let long = decoded(json!(["k".repeat(1 << 20)])).unwrap_err();
        let cut = format!("unknown record kind \"{}\"", "k".repeat(128));
        assert_eq!(long.message, cut);
        let mistyped = decoded(json!(["prompt", "prompt", 7])).unwrap_err();
        assert_eq!(
            mistyped.message,
            "parameter 'kinds' must be a list of kinds"
        );
    }
}

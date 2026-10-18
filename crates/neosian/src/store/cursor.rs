//! The paged listings' cursor (`memory/pageable.py`, DESIGN §8, #367):
//! the keyset of the last row kept, tagged with the method that minted
//! it, as the Python store spells it byte for byte, so a golden's literal
//! holds on both doors and a client may resume a page from either server
//! over one home. Opaque to every caller; a malformed one, or one minted
//! by another method, is refused by name.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE;
use serde_json::Value;

use super::time::Stamp;
use crate::pyfmt::dumps;
use crate::repr::repr;

/// What a cursor part must be.
#[derive(Clone, Copy)]
pub enum Kind {
    Str,
    Int,
    /// An ISO-8601 instant with an offset.
    Instant,
}

/// `encode_cursor`: `urlsafe_b64encode(json.dumps([method, *parts]))`.
pub fn encode(method: &str, parts: &[Value]) -> String {
    let mut items = vec![Value::String(method.to_string())];
    items.extend_from_slice(parts);
    URL_SAFE.encode(dumps(&Value::Array(items), true))
}

/// `decode_cursor`: the parts `encode` wrote, each checked against `kinds`.
pub fn decode(method: &str, cursor: &str, kinds: &[Kind]) -> Result<Vec<Value>, String> {
    let malformed = || format!("malformed {method} cursor: {}", repr(cursor));
    let bytes = URL_SAFE.decode(cursor).map_err(|_| malformed())?;
    let data: Value = serde_json::from_slice(&bytes).map_err(|_| malformed())?;
    let Value::Array(items) = data else {
        return Err(malformed());
    };
    let tagged = items.first().and_then(Value::as_str) == Some(method);
    if !tagged || items.len() != kinds.len() + 1 {
        return Err(malformed());
    }
    let parts = &items[1..];
    for (part, kind) in parts.iter().zip(kinds) {
        let fits = match kind {
            Kind::Str => part.is_string(),
            Kind::Int => part.is_i64(),
            Kind::Instant => part.as_str().is_some_and(|text| Stamp::parse(text).is_ok()),
        };
        if !fits {
            return Err(malformed());
        }
    }
    Ok(parts.to_vec())
}

/// A timestamp as a cursor part: `astimezone(UTC).isoformat()`.
pub fn instant(stamp: Stamp) -> String {
    stamp.utc().iso()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn a_cursor_is_pythons_byte_for_byte() {
        assert_eq!(
            encode("list", &[json!("notes/a")]),
            "WyJsaXN0Iiwibm90ZXMvYSJd"
        );
        assert_eq!(encode("versions", &[json!(2)]), "WyJ2ZXJzaW9ucyIsMl0=");
        let stamp = Stamp::parse("2026-08-19T12:00:00.5+02:00").unwrap();
        assert_eq!(instant(stamp), "2026-08-19T10:00:00.500000+00:00");
        let history = encode(
            "history",
            &[json!(instant(stamp)), json!("caf\u{e9}"), json!(3)],
        );
        assert_eq!(
            decode("history", &history, &[Kind::Instant, Kind::Str, Kind::Int]).unwrap(),
            vec![
                json!("2026-08-19T10:00:00.500000+00:00"),
                json!("café"),
                json!(3)
            ]
        );
    }

    #[test]
    fn a_foreign_or_malformed_cursor_is_refused_by_name() {
        let refused = |cursor: &str, kinds: &[Kind]| decode("list", cursor, kinds).unwrap_err();
        assert_eq!(refused("abc", &[Kind::Str]), "malformed list cursor: 'abc'");
        let versions = encode("versions", &[json!(2)]);
        assert_eq!(
            refused(&versions, &[Kind::Str]),
            format!("malformed list cursor: {}", repr(&versions))
        );
        let flagged = encode("list", &[json!(true)]);
        assert!(decode("list", &flagged, &[Kind::Int]).is_err());
        let long = encode("list", &[json!("a"), json!("b")]);
        assert!(decode("list", &long, &[Kind::Str]).is_err());
        let naive = encode(
            "history",
            &[json!("2026-08-19T10:00:00"), json!("a"), json!(1)],
        );
        assert!(decode("history", &naive, &[Kind::Instant, Kind::Str, Kind::Int]).is_err());
    }
}

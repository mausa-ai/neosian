//! Size paging version 1 (DESIGN §40.46): `page_bytes` bounds a page of
//! `conversation/search_turns` or `conversation/read_turns` to the whole
//! items that fit, always at least one, and `continuation` carries the rest.
//! A continuation is the cursor codec's opaque token: the route, a SHA-256
//! fingerprint of the request's selection, and a position. It names a place,
//! never an authority: every page is authorized from its own body.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::envelope::Fail;
use super::params::{Payload, optional_int, optional_str};
use crate::store::cursor::{self, Kind};

/// A size-paged request: its ceiling (capped by `NEOSIAN_MAX_PAGE_BYTES`)
/// and the continuation it sends back, if any.
pub struct Paging {
    pub ceiling: u64,
    pub continuation: Option<String>,
}

/// The request's size-paging fields; None when it carries neither.
pub fn requested(payload: &Payload) -> Result<Option<Paging>, Fail> {
    let bytes = optional_int(payload, "page_bytes")?;
    let continuation = optional_str(payload, "continuation")?;
    let Some(bytes) = bytes else {
        if continuation.is_some() {
            return Err(Fail::value("continuation requires page_bytes"));
        }
        return Ok(None);
    };
    if bytes < 1 {
        return Err(Fail::value(format!("page_bytes must be >= 1, got {bytes}")));
    }
    if continuation
        .as_ref()
        .is_some_and(|token| token.is_empty() || token.len() > 4096)
    {
        return Err(Fail::value("continuation must contain 1..4096 bytes"));
    }
    let cap = crate::limits::page_bytes()?;
    Ok(Some(Paging {
        ceiling: (bytes as u64).min(cap),
        continuation,
    }))
}

/// The selection a continuation binds, as a hex SHA-256.
pub fn fingerprint(selection: &Value) -> String {
    let bytes = serde_json::to_vec(selection).expect("a selection serializes");
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn method(route: &str) -> String {
    format!("page-v1/{route}")
}

pub fn encode(route: &str, fingerprint: &str, position: &[Value]) -> String {
    let mut parts = vec![json!(fingerprint)];
    parts.extend_from_slice(position);
    cursor::encode(&method(route), &parts)
}

/// The position of a continuation minted by `route` for this selection.
pub fn decode(
    route: &str,
    token: &str,
    fingerprint: &str,
    kinds: &[Kind],
) -> Result<Vec<Value>, Fail> {
    let mut all = vec![Kind::Str];
    all.extend_from_slice(kinds);
    let parts = cursor::decode(&method(route), token, &all).map_err(Fail::value)?;
    if parts[0] != fingerprint {
        return Err(Fail::value(
            "page continuation belongs to a different request",
        ));
    }
    Ok(parts[1..].to_vec())
}

/// A malformed position inside a well-formed continuation.
pub fn malformed(route: &str, token: &str) -> Fail {
    Fail::value(format!(
        "malformed {} cursor: {}",
        method(route),
        crate::repr::repr(token)
    ))
}

/// One page's fill: whole items while their bytes stay within the ceiling,
/// always at least one; a refused item marks that more remain.
pub struct Fill {
    ceiling: Option<u64>,
    used: u64,
    pub count: usize,
    pub more: bool,
}

impl Fill {
    pub fn new(ceiling: Option<u64>) -> Self {
        Self {
            ceiling,
            used: 0,
            count: 0,
            more: false,
        }
    }

    pub fn admit(&mut self, bytes: usize) -> bool {
        let over = self
            .ceiling
            .is_some_and(|ceiling| self.used + bytes as u64 > ceiling);
        if self.count > 0 && over {
            self.more = true;
            return false;
        }
        self.used += bytes as u64;
        self.count += 1;
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_page_takes_whole_items_that_fit_and_always_one() {
        let mut fill = Fill::new(Some(10));
        assert!(fill.admit(4) && fill.admit(6));
        assert!(!fill.admit(1) && fill.more && fill.count == 2);
        let mut alone = Fill::new(Some(10));
        assert!(alone.admit(50), "one oversized item still makes a page");
        assert!(!alone.admit(1));
        let mut whole = Fill::new(None);
        assert!(whole.admit(usize::MAX / 4) && whole.admit(5) && !whole.more);
    }

    #[test]
    fn a_continuation_binds_its_route_and_selection() {
        let mine = fingerprint(&json!(["read_turns", "c", 0, null]));
        let token = encode("read_turns", &mine, &[json!(7), json!(3)]);
        let parts = decode("read_turns", &token, &mine, &[Kind::Int, Kind::Int]).unwrap();
        assert_eq!(parts, [json!(7), json!(3)]);
        let other = fingerprint(&json!(["read_turns", "d", 0, null]));
        let refused = decode("read_turns", &token, &other, &[Kind::Int, Kind::Int]);
        assert_eq!(
            refused.unwrap_err().message,
            "page continuation belongs to a different request"
        );
        let foreign = decode("search_turns", &token, &mine, &[Kind::Int, Kind::Int]);
        assert!(
            foreign
                .unwrap_err()
                .message
                .starts_with("malformed page-v1/")
        );
    }
}

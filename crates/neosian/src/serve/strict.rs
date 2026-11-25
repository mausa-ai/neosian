//! A strict reader over a request body, for the turn-records routes
//! (DESIGN §40.54 item 8.2): a key the route does not list and a mistyped
//! value are `value_error`, a default applies only when a field is absent,
//! and `null` is admitted only where the route's table allows it. The
//! legacy routes keep `params`, where an unknown key is ignored and `null`
//! is absence.

use serde_json::Value;

use super::envelope::Fail;
use super::params::Payload;
use crate::repr::repr;

pub struct Strict<'a> {
    payload: &'a Payload,
}

/// The most scalars of a request's own value a refusal repeats.
const ECHO: usize = 128;

/// A value of the request as a refusal names it: its first 128 scalars, so
/// a refusal does not grow with the body that earned it.
pub fn echoed(text: &str) -> String {
    text.chars().take(ECHO).collect()
}

fn missing(name: &str) -> Fail {
    Fail::value(format!("missing parameter: {}", repr(name)))
}

fn mistyped(name: &str, label: &str) -> Fail {
    Fail::value(format!("parameter {} must be {label}", repr(name)))
}

impl<'a> Strict<'a> {
    /// The body, refused when it carries a key outside `allowed`; the first
    /// such key in the body's own order is named.
    pub fn new(payload: &'a Payload, allowed: &[&str]) -> Result<Self, Fail> {
        match payload.keys().find(|key| !allowed.contains(&key.as_str())) {
            Some(key) => {
                let why = format!("unknown parameter: {}", repr(&echoed(key)));
                Err(Fail::value(why))
            }
            None => Ok(Self { payload }),
        }
    }

    /// A present value of the right type; `null` is mistyped here.
    fn typed<T>(
        &self,
        name: &str,
        label: &str,
        read: impl Fn(&'a Value) -> Option<T>,
    ) -> Result<Option<T>, Fail> {
        match self.payload.get(name) {
            None => Ok(None),
            Some(value) => read(value).map(Some).ok_or_else(|| mistyped(name, label)),
        }
    }

    /// A required string.
    pub fn string(&self, name: &str) -> Result<&'a str, Fail> {
        self.typed(name, "a string", Value::as_str)?
            .ok_or_else(|| missing(name))
    }

    /// A string where the table also allows `null`; absent and `null` are
    /// both none.
    pub fn nullable_string(&self, name: &str) -> Result<Option<&'a str>, Fail> {
        match self.payload.get(name) {
            Some(Value::Null) => Ok(None),
            _ => self.typed(name, "a string or null", Value::as_str),
        }
    }

    /// An optional integer of at least zero; a boolean, a float and a
    /// negative number are mistyped.
    pub fn unsigned(&self, name: &str) -> Result<Option<u64>, Fail> {
        self.typed(name, "a non-negative integer", Value::as_u64)
    }

    /// A boolean with its default when absent.
    pub fn boolean(&self, name: &str, default: bool) -> Result<bool, Fail> {
        let found = self.typed(name, "a boolean", Value::as_bool)?;
        Ok(found.unwrap_or(default))
    }

    /// A required list.
    pub fn list(&self, name: &str) -> Result<&'a [Value], Fail> {
        self.optional_list(name)?.ok_or_else(|| missing(name))
    }

    /// A list, none when absent.
    pub fn optional_list(&self, name: &str) -> Result<Option<&'a [Value]>, Fail> {
        self.typed(name, "a list", |value| value.as_array().map(Vec::as_slice))
    }

    /// A string, none when absent; `null` is mistyped.
    pub fn optional_string(&self, name: &str) -> Result<Option<&'a str>, Fail> {
        self.typed(name, "a string", Value::as_str)
    }

    /// An object, none when absent.
    pub fn object(&self, name: &str) -> Result<Option<&'a Payload>, Fail> {
        self.typed(name, "an object", Value::as_object)
    }

    /// Whether the body carries the field at all, whatever it holds.
    pub fn names(&self, name: &str) -> bool {
        self.payload.contains_key(name)
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    const KEYS: [&str; 5] = ["id", "actor", "bytes", "seal", "items"];

    fn refused<T>(result: Result<T, Fail>) -> String {
        let fail = result.err().expect("the body is refused");
        assert_eq!((fail.status.as_u16(), fail.code), (400, "value_error"));
        fail.message
    }

    fn body(value: Value) -> Payload {
        value.as_object().expect("an object").clone()
    }

    #[test]
    fn an_unknown_key_is_refused_by_name_in_body_order() {
        let payload = body(json!({"id": "a", "zeta": 1, "alpha": 2}));
        assert_eq!(
            refused(Strict::new(&payload, &KEYS).map(|_| ())),
            "unknown parameter: 'zeta'"
        );
        assert!(Strict::new(&body(json!({"id": "a"})), &KEYS).is_ok());
    }

    #[test]
    fn a_refusal_repeats_at_most_128_scalars_of_an_unknown_key() {
        let exact = "k".repeat(128);
        let payload = body(json!({&exact: 1}));
        assert_eq!(
            refused(Strict::new(&payload, &KEYS).map(|_| ())),
            format!("unknown parameter: '{exact}'")
        );
        // Scalars, not bytes: a cut never splits a character.
        let long = format!("{}{}", "é".repeat(128), "x".repeat(1 << 20));
        let payload = body(json!({&long: 1}));
        let message = refused(Strict::new(&payload, &KEYS).map(|_| ()));
        assert_eq!(message, format!("unknown parameter: '{}'", "é".repeat(128)));
        assert_eq!(echoed("short"), "short");
    }

    #[test]
    fn a_mistyped_value_names_its_parameter() {
        let payload = body(json!({
            "id": 7, "actor": 7, "bytes": "9", "seal": 1, "items": {},
        }));
        let strict = Strict::new(&payload, &KEYS).unwrap();
        assert_eq!(
            refused(strict.string("id")),
            "parameter 'id' must be a string"
        );
        assert_eq!(
            refused(strict.nullable_string("actor")),
            "parameter 'actor' must be a string or null"
        );
        assert_eq!(
            refused(strict.unsigned("bytes")),
            "parameter 'bytes' must be a non-negative integer"
        );
        assert_eq!(
            refused(strict.boolean("seal", false)),
            "parameter 'seal' must be a boolean"
        );
        assert_eq!(
            refused(strict.list("items")),
            "parameter 'items' must be a list"
        );
        assert_eq!(
            refused(strict.optional_list("items")),
            "parameter 'items' must be a list"
        );
        assert_eq!(
            refused(strict.optional_string("id")),
            "parameter 'id' must be a string"
        );
        assert_eq!(
            refused(strict.object("id").map(|_| ())),
            "parameter 'id' must be an object"
        );
        assert!(
            strict
                .object("items")
                .unwrap()
                .is_some_and(|map| map.is_empty())
        );
        for bytes in [json!(-1), json!(1.5), json!(true), json!(1e30)] {
            let payload = body(json!({"bytes": bytes}));
            let strict = Strict::new(&payload, &KEYS).unwrap();
            refused(strict.unsigned("bytes"));
        }
    }

    #[test]
    fn null_is_admitted_only_where_the_table_allows_it() {
        let payload = body(json!({
            "id": null, "actor": null, "bytes": null, "seal": null, "items": null,
        }));
        let strict = Strict::new(&payload, &KEYS).unwrap();
        assert_eq!(strict.nullable_string("actor").unwrap(), None);
        refused(strict.string("id"));
        refused(strict.unsigned("bytes"));
        refused(strict.boolean("seal", false));
        refused(strict.list("items"));
        refused(strict.optional_list("items"));
        refused(strict.optional_string("id"));
        refused(strict.object("items").map(|_| ()));
        assert!(strict.names("id") && !strict.names("other"));
    }

    #[test]
    fn defaults_apply_only_when_a_field_is_absent() {
        let payload = body(json!({}));
        let strict = Strict::new(&payload, &KEYS).unwrap();
        assert_eq!(strict.nullable_string("actor").unwrap(), None);
        assert_eq!(strict.unsigned("bytes").unwrap(), None);
        assert!(!strict.boolean("seal", false).unwrap());
        assert!(strict.boolean("seal", true).unwrap());
        assert_eq!(refused(strict.string("id")), "missing parameter: 'id'");
        assert_eq!(refused(strict.list("items")), "missing parameter: 'items'");
        assert_eq!(strict.optional_list("items").unwrap(), None);
        assert_eq!(strict.optional_string("id").unwrap(), None);
        assert!(strict.object("items").unwrap().is_none());
        assert!(!strict.names("id"));
        let payload = body(json!({
            "id": "", "actor": "a", "bytes": 0, "seal": true, "items": [],
        }));
        let strict = Strict::new(&payload, &KEYS).unwrap();
        assert_eq!(strict.string("id").unwrap(), "");
        assert_eq!(strict.nullable_string("actor").unwrap(), Some("a"));
        assert_eq!(strict.unsigned("bytes").unwrap(), Some(0));
        assert!(strict.boolean("seal", false).unwrap());
        assert!(strict.list("items").unwrap().is_empty());
    }
}

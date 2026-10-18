//! Request parameters typed at the door (`server/wire.py`, DESIGN §18.2):
//! absent and `null` are one thing, a mis-typed value is `value_error`
//! naming the parameter, a JSON boolean is never an integer. Timestamps
//! take any offset and refuse a naive one.

use serde_json::{Map, Value};

use super::envelope::Fail;
use crate::pyfmt::py_repr;
use crate::repr::repr;
use crate::store::time::{Stamp, Unreadable};

pub type Payload = Map<String, Value>;

fn param<'a>(
    payload: &'a Payload,
    name: &str,
    fits: impl Fn(&Value) -> bool,
    label: &str,
    required: bool,
) -> Result<Option<&'a Value>, Fail> {
    match payload.get(name) {
        None | Some(Value::Null) if required => {
            Err(Fail::value(format!("missing parameter: {}", repr(name))))
        }
        None | Some(Value::Null) => Ok(None),
        Some(value) if fits(value) => Ok(Some(value)),
        Some(_) => Err(Fail::value(format!(
            "parameter {} must be {label}",
            repr(name)
        ))),
    }
}

fn is_int(value: &Value) -> bool {
    value.is_i64()
}

pub fn require_str(payload: &Payload, name: &str) -> Result<String, Fail> {
    let value = param(payload, name, Value::is_string, "a string", true)?;
    Ok(value
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string())
}

pub fn optional_str(payload: &Payload, name: &str) -> Result<Option<String>, Fail> {
    let value = param(payload, name, Value::is_string, "a string", false)?;
    Ok(value.and_then(Value::as_str).map(str::to_string))
}

pub fn require_int(payload: &Payload, name: &str) -> Result<i64, Fail> {
    let value = param(payload, name, is_int, "an integer", true)?;
    Ok(value.and_then(Value::as_i64).unwrap_or_default())
}

pub fn optional_int(payload: &Payload, name: &str) -> Result<Option<i64>, Fail> {
    let value = param(payload, name, is_int, "an integer", false)?;
    Ok(value.and_then(Value::as_i64))
}

/// `decode_timestamp`: ISO-8601 with any offset, normalised to UTC.
pub fn decode_timestamp(value: &Value) -> Result<Stamp, Fail> {
    let Value::String(text) = value else {
        return Err(Fail::value(format!(
            "malformed wire timestamp: {}",
            py_repr(value)
        )));
    };
    match Stamp::parse(text) {
        Ok(stamp) => Ok(stamp.utc()),
        Err(Unreadable::Naive) => Err(Fail::value(format!(
            "naive wire timestamp refused: {}",
            repr(text)
        ))),
        Err(Unreadable::Malformed) => Err(Fail::value(format!(
            "Invalid isoformat string: {}",
            repr(text)
        ))),
    }
}

pub fn optional_timestamp(payload: &Payload, name: &str) -> Result<Option<Stamp>, Fail> {
    let value = param(payload, name, Value::is_string, "a string", false)?;
    value.map(decode_timestamp).transpose()
}

fn objects<'a>(payload: &'a Payload, name: &str, required: bool) -> Result<Vec<&'a Value>, Fail> {
    let label = "a list of objects";
    let value = param(payload, name, Value::is_array, label, required)?;
    let items = value
        .and_then(Value::as_array)
        .map_or(&[][..], Vec::as_slice);
    if !items.iter().all(Value::is_object) {
        return Err(Fail::value(format!(
            "parameter {} must be {label}",
            repr(name)
        )));
    }
    Ok(items.iter().collect())
}

pub fn require_objects<'a>(payload: &'a Payload, name: &str) -> Result<Vec<&'a Value>, Fail> {
    objects(payload, name, true)
}

/// An optional list of objects; absent or `null` is empty.
pub fn optional_objects<'a>(payload: &'a Payload, name: &str) -> Result<Vec<&'a Value>, Fail> {
    objects(payload, name, false)
}

fn list<T>(
    payload: &Payload,
    name: &str,
    label: &str,
    item: impl Fn(&Value) -> Option<T>,
) -> Result<Option<Vec<T>>, Fail> {
    let Some(value) = param(payload, name, Value::is_array, label, false)? else {
        return Ok(None);
    };
    let items = value.as_array().map_or(&[][..], Vec::as_slice);
    let typed: Option<Vec<T>> = items.iter().map(&item).collect();
    typed
        .map(Some)
        .ok_or_else(|| Fail::value(format!("parameter {} must be {label}", repr(name))))
}

pub fn optional_strs(payload: &Payload, name: &str) -> Result<Option<Vec<String>>, Fail> {
    list(payload, name, "a list of strings", |item| {
        item.as_str().map(str::to_string)
    })
}

pub fn optional_ints(payload: &Payload, name: &str) -> Result<Option<Vec<i64>>, Fail> {
    list(payload, name, "a list of integers", Value::as_i64)
}

/// `data[key]`: the value, or the `KeyError` the backstop words.
pub fn key<'a>(data: &'a Payload, name: &'static str) -> Result<&'a Value, Fail> {
    data.get(name).ok_or_else(|| Fail::malformed(name))
}

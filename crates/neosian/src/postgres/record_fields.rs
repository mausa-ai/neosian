//! What a PostgreSQL records row can hold: each number within its column's
//! type and no text with a NUL, which `jsonb` and `text` refuse. A restore
//! and a native record write check a record's fields here before any row is
//! sent (finding 8 of the T1 review), so a value the store cannot hold is
//! refused as a value, `400 value_error`, never answered as an outage. The
//! messages name the request: `restore field ...`, `record field ...`.

use crate::conversation::Error;
use crate::conversation::turn_records::TurnRecord;

/// The refusal of a number past its column's type.
pub(super) fn range(request: &str, field: &str) -> Error {
    Error::Value(format!(
        "{request} field {field} is outside the PostgreSQL storage range"
    ))
}

/// A number that must fit an `integer` column.
pub(super) fn integer(request: &str, n: u64, field: &str) -> Result<(), Error> {
    i32::try_from(n)
        .map(|_| ())
        .map_err(|_| range(request, field))
}

/// A text that must hold no NUL.
pub(super) fn text(request: &str, value: Option<&str>, field: &str) -> Result<(), Error> {
    if value.is_some_and(|s| s.contains('\0')) {
        return Err(Error::Value(format!(
            "{request} field {field} contains a null character"
        )));
    }
    Ok(())
}

/// Every field of one record, in the order its row's columns are checked.
pub(super) fn record(request: &str, r: &TurnRecord) -> Result<(), Error> {
    for (field, n) in [
        ("seq", r.seq),
        ("message", r.message),
        ("block", r.block.unwrap_or(0)),
        ("call", r.call.unwrap_or(0)),
        ("chunk", r.chunk),
    ] {
        integer(request, n, field)?;
    }
    i64::try_from(r.size).map_err(|_| range(request, "size"))?;
    text(request, r.call_id.as_deref(), "call_id")?;
    text(request, r.name.as_deref(), "name")?;
    text(request, Some(&r.text), "text")
}

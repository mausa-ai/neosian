//! Typed parameters and rows for the shared SQL assets, without string SQL.

use super::connection::failure;
use crate::store::time::Stamp;
use serde_json::{Map, Value, json};
use std::io;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio_postgres::{
    Row,
    types::{ToSql, Type},
};

type Parameter = Box<dyn ToSql + Sync + Send>;

pub fn parameter(value: &Value, ty: &Type) -> io::Result<Parameter> {
    let invalid = || io::Error::other(format!("invalid Postgres parameter for {ty}"));
    macro_rules! optional {
        ($convert:expr) => {
            if value.is_null() {
                None
            } else {
                Some(($convert).ok_or_else(invalid)?)
            }
        };
    }
    Ok(match *ty {
        Type::TEXT | Type::VARCHAR => Box::new(optional!(value.as_str().map(str::to_string))),
        Type::INT4 => Box::new(optional!(
            value.as_i64().and_then(|n| i32::try_from(n).ok())
        )),
        Type::INT8 => Box::new(optional!(value.as_i64())),
        Type::BOOL => Box::new(optional!(value.as_bool())),
        Type::TIMESTAMPTZ => Box::new(optional!(
            value
                .as_str()
                .and_then(|v| Stamp::parse(v).ok())
                .map(system_time)
        )),
        Type::JSONB | Type::JSON => Box::new(value.clone()),
        Type::TEXT_ARRAY => Box::new(optional!(value.as_array().and_then(|a| {
            a.iter()
                .map(|v| v.as_str().map(str::to_string))
                .collect::<Option<Vec<_>>>()
        }))),
        Type::INT4_ARRAY => Box::new(optional!(value.as_array().and_then(|a| {
            a.iter()
                .map(|v| v.as_i64().and_then(|n| i32::try_from(n).ok()))
                .collect::<Option<Vec<_>>>()
        }))),
        _ => return Err(invalid()),
    })
}

fn system_time(stamp: Stamp) -> SystemTime {
    let delta = Duration::from_micros(stamp.instant().unsigned_abs());
    if stamp.instant() < 0 {
        UNIX_EPOCH - delta
    } else {
        UNIX_EPOCH + delta
    }
}

fn stamp(time: SystemTime) -> String {
    let micros = match time.duration_since(UNIX_EPOCH) {
        Ok(delta) => delta.as_micros() as i64,
        Err(error) => -(error.duration().as_micros() as i64),
    };
    Stamp::from_micros(micros).z()
}

pub fn row(row: &Row) -> io::Result<Value> {
    let mut fields = Map::new();
    for (index, column) in row.columns().iter().enumerate() {
        macro_rules! read {
            ($ty:ty) => {
                json!(row.try_get::<_, Option<$ty>>(index).map_err(failure)?)
            };
        }
        let value = match *column.type_() {
            Type::TEXT | Type::VARCHAR => read!(String),
            Type::INT4 => read!(i32),
            Type::INT8 => read!(i64),
            Type::BOOL => read!(bool),
            Type::JSONB | Type::JSON => read!(Value),
            Type::INT4_ARRAY => read!(Vec<i32>),
            Type::TIMESTAMPTZ => json!(
                row.try_get::<_, Option<SystemTime>>(index)
                    .map_err(failure)?
                    .map(stamp)
            ),
            _ => return Err(io::Error::other("unsupported Postgres result type")),
        };
        fields.insert(column.name().to_string(), value);
    }
    Ok(Value::Object(fields))
}

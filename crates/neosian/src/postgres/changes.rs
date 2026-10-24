//! Optional operator-owned tables; mutations and events commit in one transaction.

use serde_json::{Value, json};
use std::io;
use tokio_postgres::{Transaction, types::ToSql};

use super::{PostgresStore, connection, sql, values};
use crate::changes::{
    cursor,
    model::{Change, Event, Head, Kind, Page, Pruned, Request, invalid},
};
use crate::store::{error::Error, grammar::parse_scope};

#[derive(Clone, Copy)]
pub(super) enum Mutation {
    Write,
    Delete,
    Rename,
    Erase,
    Restore,
    Prune,
}

fn failure() -> io::Error {
    io::Error::other("invalid change-stream state")
}

impl PostgresStore {
    pub fn supports_changes(&self) -> Result<bool, Error> {
        if let Some(supported) = self.changes.get() {
            return Ok(*supported);
        }
        let found = self.one(sql!("changes/detect"), json!({}), false)?;
        let supported =
            if found["marker"] == false && found["streams"] == false && found["events"] == false {
                false
            } else if found["marker"] == true && found["streams"] == true && found["events"] == true
            {
                let version = self.one(sql!("changes/version"), json!({}), false)?;
                if version["version"] != 1 {
                    return Err(invalid("unsupported Postgres changes extension"));
                }
                true
            } else {
                return Err(invalid("incomplete Postgres changes extension"));
            };
        let _ = self.changes.set(supported);
        Ok(supported)
    }

    pub(super) fn mutate(
        &self,
        template: &'static str,
        params: Value,
        retry: bool,
        mutation: Mutation,
    ) -> Result<Value, Error> {
        if !self.supports_changes()? {
            return self.one(template, params, retry);
        }
        let mut rows = self.pool.mutate(template, &params, retry, mutation)?;
        if rows.len() != 1 {
            return Err(failure().into());
        }
        Ok(rows.remove(0))
    }

    pub fn read_changes(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        parse_scope(scope)?;
        request.validate()?;
        if !self.supports_changes()? {
            return Err(invalid("backend does not support change-feed version 1"));
        }
        let after = request
            .cursor
            .as_deref()
            .map(|c| cursor::decode(scope, c))
            .transpose()?
            .map_or(0, |(_, sequence)| sequence);
        let row = self.one(
            sql!("changes/read"),
            json!({"scope":scope,"after":after,"limit":request.limit}),
            false,
        )?;
        let head: Option<Head> =
            serde_json::from_value(row["head"].clone()).map_err(|_| failure())?;
        let (mut page, after, end) = cursor::page(scope, head.as_ref(), request)?;
        if page.gap.is_none() {
            page.events = serde_json::from_value(row["events"].clone()).map_err(|_| failure())?;
            if page.events.len() as u64 != end - after {
                return Err(failure().into());
            }
            for (i, event) in page.events.iter().enumerate() {
                event.validate()?;
                if event.scope != scope
                    || Some(&event.stream) != page.stream.as_ref()
                    || event.sequence != after + i as u64 + 1
                {
                    return Err(failure().into());
                }
            }
        }
        Ok(page)
    }

    pub fn prune_changes(&self, scope: &str, through: &str, write: bool) -> Result<Pruned, Error> {
        parse_scope(scope)?;
        cursor::decode(scope, through)?;
        if !self.supports_changes()? {
            return Err(invalid("backend does not support change-feed version 1"));
        }
        let rows = self.pool.mutate(
            sql!("changes/head"),
            &json!({"scope":scope,"through":through,"write":write}),
            false,
            Mutation::Prune,
        )?;
        serde_json::from_value(rows.into_iter().next().ok_or_else(failure)?)
            .map_err(|_| failure().into())
    }
}

/// Bound parameters only; template and schema are controlled by the binary.
async fn query(
    tx: &Transaction<'_>,
    schema: &str,
    template: &str,
    params: Value,
) -> io::Result<Vec<Value>> {
    let (sql, names) = connection::parameters(&template.replace("{{schema}}", schema));
    let statement = tx.prepare(&sql).await.map_err(connection::failure)?;
    let values = names
        .iter()
        .zip(statement.params())
        .map(|(name, ty)| values::parameter(params.get(name).ok_or_else(failure)?, ty))
        .collect::<io::Result<Vec<_>>>()?;
    let borrowed: Vec<&(dyn ToSql + Sync)> = values.iter().map(|v| &**v as _).collect();
    tx.query(&statement, &borrowed)
        .await
        .map_err(connection::failure)?
        .iter()
        .map(values::row)
        .collect()
}

fn change(mutation: Mutation, params: &Value, rows: &[Value]) -> Option<Change> {
    let row = rows.first()?;
    let path = params["path"].as_str();
    let version = row["version"].as_u64();
    match mutation {
        Mutation::Write if version.is_some() => Some(Change::new(
            if row["existed"] == true {
                Kind::Modified
            } else {
                Kind::Created
            },
            path,
            version,
        )),
        Mutation::Delete if row["existed"] == true && row["format_ok"] == true => {
            Some(Change::new(Kind::Deleted, path, version))
        }
        Mutation::Rename if version.is_some() => {
            let mut change = Change::new(Kind::Renamed, params["dst"].as_str(), version);
            change.previous_path = params["src"].as_str().map(str::to_string);
            change.previous_version = row["source_version"].as_u64();
            Some(change)
        }
        Mutation::Erase
            if row["format_ok"] == true && row["matched"].as_u64().is_some_and(|n| n > 0) =>
        {
            Some(Change::new(
                if path.is_some() {
                    Kind::Erased
                } else {
                    Kind::ScopeErased
                },
                path,
                None,
            ))
        }
        Mutation::Restore
            if row["empty"] == true
                && ["documents", "versions", "redactions"]
                    .iter()
                    .any(|key| params[key].as_array().is_some_and(|a| !a.is_empty())) =>
        {
            Some(Change::reset("restored"))
        }
        _ => None,
    }
}

/// The caller holds the scope's transaction lock until commit, including rollback.
pub(super) async fn finish(
    tx: &Transaction<'_>,
    schema: &str,
    mutation: Mutation,
    params: &Value,
    rows: &[Value],
) -> io::Result<Option<Vec<Value>>> {
    let scope = params["scope"].as_str().ok_or_else(failure)?;
    if matches!(mutation, Mutation::Prune) {
        return prune(tx, schema, params, rows).await.map(Some);
    }
    let Some(change) = change(mutation, params, rows) else {
        return Ok(None);
    };
    let found = query(tx, schema, sql!("changes/head"), json!({"scope":scope})).await?;
    let mut head: Head = match found.first() {
        Some(value) => serde_json::from_value(value.clone()).map_err(|_| failure())?,
        None => Head::new(),
    };
    head.validate().map_err(|_| failure())?;
    let mut events = vec![];
    if found.is_empty() && change.kind != Kind::Reset {
        events.push(
            head.event(scope, Change::reset("tracking_started"))
                .map_err(|_| failure())?,
        );
    }
    events.push(head.event(scope, change).map_err(|_| failure())?);
    query(
        tx,
        schema,
        sql!("changes/append"),
        json!({"scope":scope,"stream":head.stream,
        "sequence":head.sequence,"floor":head.floor,"events":events}),
    )
    .await?;
    Ok(None)
}

async fn prune(
    tx: &Transaction<'_>,
    schema: &str,
    params: &Value,
    rows: &[Value],
) -> io::Result<Vec<Value>> {
    let scope = params["scope"].as_str().ok_or_else(failure)?;
    let head: Head =
        serde_json::from_value(rows.first().ok_or_else(failure)?.clone()).map_err(|_| failure())?;
    head.validate().map_err(|_| failure())?;
    let end = cursor::through(
        scope,
        &head,
        params["through"].as_str().ok_or_else(failure)?,
    )
    .map_err(|_| io::Error::other("invalid retention cursor for current stream"))?;
    let removed = end - head.floor;
    let write = params["write"].as_bool().ok_or_else(failure)?;
    if write && removed > 0 {
        let rows = query(
            tx,
            schema,
            sql!("changes/prune"),
            json!({"scope":scope,"floor":head.floor,"through":end}),
        )
        .await?;
        if rows.len() as u64 != removed {
            return Err(failure());
        }
        for (i, row) in rows.into_iter().enumerate() {
            let event: Event =
                serde_json::from_value(row["event"].clone()).map_err(|_| failure())?;
            event.validate().map_err(|_| failure())?;
            if event.stream != head.stream
                || event.scope != scope
                || event.sequence != head.floor + i as u64 + 1
            {
                return Err(failure());
            }
        }
    }
    Ok(vec![
        serde_json::to_value(Pruned {
            format: 1,
            scope: scope.into(),
            removed,
            write,
            earliest_cursor: cursor::encode(scope, &head, end),
        })
        .expect("prune serializes"),
    ])
}

//! Four lazy connections, with transactions for optional durable change events.
//! No DDL; retry only known aborted races, never a lost commit response.
//! Reads stream their rows and check each against the response budget as it
//! arrives, so no statement is collected whole by accident (DESIGN §40.45).

mod scan;
pub(super) use scan::Passes;

use std::collections::HashMap;
use std::io;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use serde_json::Value;
use tokio::runtime::{Builder, Runtime};
use tokio::sync::Mutex;
use tokio_postgres::{Client, Config, Statement, error::SqlState, types::ToSql};
use tokio_postgres_rustls::MakeRustlsConnect;

use super::values;

const CONNECTIONS: usize = 4;
const ATTEMPTS: usize = 25;

pub(super) struct Connection {
    client: Client,
    statements: HashMap<&'static str, Statement>,
}

impl Connection {
    /// The template's statement, prepared once per connection.
    pub(super) async fn prepared(
        &mut self,
        template: &'static str,
        sql: &str,
    ) -> io::Result<Statement> {
        if !self.statements.contains_key(template) {
            let prepared = self.client.prepare(sql).await.map_err(failure)?;
            self.statements.insert(template, prepared);
        }
        Ok(self.statements[template].clone())
    }
}

pub struct Pool {
    config: Config,
    tls: MakeRustlsConnect,
    schema: String,
    runtime: Runtime,
    slots: [Mutex<Option<Connection>>; CONNECTIONS],
    next: AtomicUsize,
}

impl Pool {
    pub fn new(dsn: &str, schema: &str) -> io::Result<Self> {
        // Driver diagnostics can contain DSN values; only fixed guidance escapes.
        let mut config: Config = dsn.parse().map_err(|_| {
            io::Error::other("invalid Postgres connection string (tokio-postgres DSN syntax)")
        })?;
        if config.get_connect_timeout().is_none() {
            config.connect_timeout(Duration::from_secs(30));
        }
        Ok(Self {
            config,
            tls: super::tls::connector(
                std::env::var_os("PGSSLROOTCERT")
                    .filter(|path| !path.is_empty())
                    .as_deref()
                    .map(std::path::Path::new),
            )?,
            schema: format!("\"{schema}\""),
            runtime: Builder::new_multi_thread()
                .worker_threads(1)
                .enable_all()
                .build()?,
            slots: std::array::from_fn(|_| Mutex::new(None)),
            next: AtomicUsize::new(0),
        })
    }

    /// A connection: the first free slot from this request's turn on, else
    /// a wait on that slot, so a request never queues behind a busy slot
    /// while another is free.
    pub(super) async fn checkout(
        &self,
    ) -> io::Result<tokio::sync::MutexGuard<'_, Option<Connection>>> {
        let start = self.next.fetch_add(1, Ordering::Relaxed) % CONNECTIONS;
        let free = (0..CONNECTIONS)
            .find_map(|offset| self.slots[(start + offset) % CONNECTIONS].try_lock().ok());
        let mut held = match free {
            Some(held) => held,
            None => tokio::time::timeout(Duration::from_secs(30), self.slots[start].lock())
                .await
                .map_err(|_| io::Error::other("Postgres connection checkout timed out"))?,
        };
        if held.as_ref().is_none_or(|conn| conn.client.is_closed()) {
            *held = Some(self.connect().await.map_err(failure)?);
        }
        Ok(held)
    }

    async fn connect(&self) -> Result<Connection, tokio_postgres::Error> {
        let (client, connection) = self.config.connect(self.tls.clone()).await?;
        tokio::spawn(async move {
            let _ = connection.await;
        });
        Ok(Connection {
            client,
            statements: HashMap::new(),
        })
    }

    pub fn fetch(
        &self,
        template: &'static str,
        params: &Value,
        retry: bool,
    ) -> io::Result<Vec<Value>> {
        self.query(template, params, retry, None)
    }

    pub(super) fn mutate(
        &self,
        template: &'static str,
        params: &Value,
        retry: bool,
        mutation: super::changes::Mutation,
    ) -> io::Result<Vec<Value>> {
        self.query(template, params, retry, Some(mutation))
    }

    fn query(
        &self,
        template: &'static str,
        params: &Value,
        retry: bool,
        mutation: Option<super::changes::Mutation>,
    ) -> io::Result<Vec<Value>> {
        let (sql, names) = parameters(&template.replace("{{schema}}", &self.schema));
        let budget = crate::limits::response_bytes().map_err(crate::limits::into_io)?;
        self.runtime.block_on(async {
            for attempt in 0..ATTEMPTS {
                let mut held = self.checkout().await?;
                let conn = held.as_mut().expect("connected");
                let statement = conn.prepared(template, &sql).await?;
                let bound = bind(&names, &statement, params)?;
                let borrowed: Vec<&(dyn ToSql + Sync)> =
                    bound.iter().map(|value| &**value as _).collect();
                let result = if let Some(mutation) = mutation {
                    let tx = conn.client.transaction().await.map_err(failure)?;
                    let scope = params["scope"]
                        .as_str()
                        .ok_or_else(|| io::Error::other("missing scope"))?;
                    let lock_key = format!("{}:{scope}", self.schema);
                    tx.execute(super::sql!("changes/lock"), &[&lock_key])
                        .await
                        .map_err(failure)?;
                    match tx.query(&statement, &borrowed).await {
                        Ok(rows) => {
                            let values = rows
                                .iter()
                                .map(values::row)
                                .collect::<io::Result<Vec<_>>>()?;
                            let result = super::changes::finish(
                                &tx,
                                &self.schema,
                                mutation,
                                params,
                                &values,
                            )
                            .await?;
                            tx.commit().await.map_err(failure)?;
                            return Ok(result.unwrap_or(values));
                        }
                        Err(error) => {
                            tx.rollback().await.map_err(failure)?;
                            Err(error)
                        }
                    }
                } else {
                    let rows = conn.client.query_raw(&statement, borrowed.iter().copied());
                    match rows.await {
                        Ok(rows) => collect(rows, budget).await,
                        Err(error) => Err(error),
                    }
                };
                drop(held);
                match result {
                    Ok(Ok(rows)) => return Ok(rows),
                    Ok(Err(refusal)) => return Err(refusal),
                    Err(error) if retry && attempt + 1 < ATTEMPTS && contended(&error) => {
                        let jitter =
                            uuid::Uuid::new_v4().as_u128() % (10_000 * (attempt as u128 + 1));
                        tokio::time::sleep(Duration::from_micros(jitter as u64)).await;
                    }
                    Err(error) => return Err(failure(error)),
                }
            }
            unreachable!("the final attempt always returns")
        })
    }
}

/// Bound parameters in the statement's order; only the trusted assets name them.
pub(super) fn bind(
    names: &[String],
    statement: &Statement,
    params: &Value,
) -> io::Result<Vec<values::Parameter>> {
    names
        .iter()
        .zip(statement.params())
        .map(|(name, ty)| {
            let value = params
                .get(name)
                .ok_or_else(|| io::Error::other("missing SQL parameter"))?;
            values::parameter(value, ty)
        })
        .collect()
}

/// Every row of one statement, decoded as it arrives. A row whose compact JSON
/// is larger than `budget` stops the read and refuses, naming
/// `NEOSIAN_MAX_RESPONSE_BYTES`: one item that large has no bounded answer.
/// Rows are not charged together, so any answer 2.16.0 gave is read
/// (DESIGN §40.46, #418). The driver applies backpressure, so unread rows
/// wait in the socket, not in memory.
async fn collect(
    rows: tokio_postgres::RowStream,
    budget: u64,
) -> Result<io::Result<Vec<Value>>, tokio_postgres::Error> {
    use futures_util::TryStreamExt;
    futures_util::pin_mut!(rows);
    let mut decoded = Vec::new();
    while let Some(row) = rows.try_next().await? {
        let value = match values::row(&row) {
            Ok(value) => value,
            Err(error) => return Ok(Err(error)),
        };
        if let Err(refusal) = values::fits(&value, budget) {
            return Ok(Err(crate::limits::into_io(refusal)));
        }
        decoded.push(value);
    }
    Ok(Ok(decoded))
}

fn contended(error: &tokio_postgres::Error) -> bool {
    matches!(
        error.code(),
        Some(&SqlState::UNIQUE_VIOLATION | &SqlState::T_R_DEADLOCK_DETECTED)
    )
}

pub(super) fn failure(error: tokio_postgres::Error) -> io::Error {
    let code = error
        .code()
        .map_or("connection or codec error", SqlState::code);
    io::Error::other(format!("Postgres request failed ({code})"))
}

/// Only the trusted assets contain placeholders. All caller data stays bound.
pub(super) fn parameters(template: &str) -> (String, Vec<String>) {
    let mut sql = String::new();
    let mut names = Vec::new();
    let mut rest = template;
    while let Some((before, after)) = rest.split_once("%(") {
        sql.push_str(before);
        let (name, tail) = after.split_once(")s").expect("SQL asset placeholder");
        let index = match names.iter().position(|held| held == name) {
            Some(index) => index,
            None => {
                names.push(name.to_string());
                names.len() - 1
            }
        };
        sql.push_str(&format!("${}", index + 1));
        rest = tail;
    }
    sql.push_str(rest);
    (sql, names)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeated_parameters_share_a_bound_slot() {
        let (sql, names) = parameters("SELECT %(scope)s, %(path)s, %(scope)s::text");
        assert_eq!(sql, "SELECT $1, $2, $1::text");
        assert_eq!(names, ["scope", "path"]);
    }
}

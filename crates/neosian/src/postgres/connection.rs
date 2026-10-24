//! Four lazy autocommit connections. No BEGIN, COMMIT or DDL, and retries
//! only for known, aborted version/turn races. Never replay a lost request.

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

struct Connection {
    client: Client,
    statements: HashMap<&'static str, Statement>,
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
        let (sql, names) = parameters(&template.replace("{{schema}}", &self.schema));
        self.runtime.block_on(async {
            for attempt in 0..ATTEMPTS {
                let slot = self.next.fetch_add(1, Ordering::Relaxed) % CONNECTIONS;
                let mut held =
                    tokio::time::timeout(Duration::from_secs(30), self.slots[slot].lock())
                        .await
                        .map_err(|_| io::Error::other("Postgres connection checkout timed out"))?;
                if held.as_ref().is_none_or(|conn| conn.client.is_closed()) {
                    *held = Some(self.connect().await.map_err(failure)?);
                }
                let conn = held.as_mut().expect("connected");
                if !conn.statements.contains_key(template) {
                    let prepared = conn.client.prepare(&sql).await.map_err(failure)?;
                    conn.statements.insert(template, prepared);
                }
                let statement = &conn.statements[template];
                let bound = names
                    .iter()
                    .zip(statement.params())
                    .map(|(name, ty)| {
                        let value = params
                            .get(name)
                            .ok_or_else(|| io::Error::other("missing SQL parameter"))?;
                        values::parameter(value, ty)
                    })
                    .collect::<io::Result<Vec<_>>>()?;
                let borrowed: Vec<&(dyn ToSql + Sync)> =
                    bound.iter().map(|value| &**value as _).collect();
                let result = conn.client.query(statement, &borrowed).await;
                drop(held);
                match result {
                    Ok(rows) => return rows.iter().map(values::row).collect(),
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
fn parameters(template: &str) -> (String, Vec<String>) {
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

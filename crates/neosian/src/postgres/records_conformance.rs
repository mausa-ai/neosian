//! An isolated operator-installed schema for each live records contract.
mod erase_atomic;
mod erase_turns;
mod fold_atomic;
mod fold_receipt;
mod migrate_atomic;
mod physical;
mod restore_commit;
mod restore_cost;
mod restore_races;
mod restore_stage;
mod restore_transactions;
mod seal;
mod search_snapshot;
mod stored;
mod sweep;
mod sweep_atomic;
mod sweep_settings;

use std::path::Path;
use tokio::runtime::{Builder, Runtime};
use tokio_postgres::{Client, Row};

use super::{PostgresStore, sql, tls};

pub(super) struct Database {
    pub store: PostgresStore,
    pub schema: String,
    dsn: String,
    runtime: Runtime,
    client: Client,
}

impl Database {
    pub fn new() -> Self {
        let dsn = std::env::var("NEOSIAN_TEST_POSTGRES_DSN")
            .expect("set NEOSIAN_TEST_POSTGRES_DSN to run live Postgres tests");
        let schema = format!("t1_records_{}", uuid::Uuid::new_v4().simple());
        let store = PostgresStore::open(&dsn, &schema).expect("open test store");
        let runtime = Builder::new_current_thread().enable_all().build().unwrap();
        let root = std::env::var_os("PGSSLROOTCERT").filter(|path| !path.is_empty());
        let connector = tls::connector(root.as_deref().map(Path::new)).unwrap();
        let (client, connection) = runtime
            .block_on(tokio_postgres::connect(&dsn, connector))
            .unwrap_or_else(|_| panic!("could not connect to the explicit test database"));
        runtime.spawn(async move {
            let _ = connection.await;
        });
        let db = Self {
            store,
            schema,
            dsn,
            runtime,
            client,
        };
        db.execute(&sql!("postgres").replace("{{version}}", "4"));
        db.execute(sql!("records-postgres"));
        db
    }

    pub fn another_store(&self) -> PostgresStore {
        PostgresStore::open(&self.dsn, &self.schema).expect("reopen test store")
    }

    fn render(&self, statement: &str) -> String {
        statement.replace("{{schema}}", &format!("\"{}\"", self.schema))
    }

    pub fn execute(&self, statement: &str) {
        self.runtime
            .block_on(self.client.batch_execute(&self.render(statement)))
            .unwrap();
    }

    pub fn query(&self, statement: &str) -> Vec<Row> {
        self.runtime
            .block_on(self.client.query(&self.render(statement), &[]))
            .unwrap()
    }
}

impl Drop for Database {
    fn drop(&mut self) {
        let cleanup = self.render("DROP SCHEMA IF EXISTS {{schema}} CASCADE");
        if let Err(error) = self.runtime.block_on(self.client.batch_execute(&cleanup))
            && !std::thread::panicking()
        {
            panic!(
                "could not remove disposable schema {}: {error}",
                self.schema
            );
        }
    }
}

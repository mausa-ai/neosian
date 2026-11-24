//! Each part, including a final part that seals, commits or rolls back as
//! one unit. A collision with a schema-4 append retries the whole attempt.
use tokio_postgres::error::SqlState;

use super::{ATTEMPTS, Pool, contended, failure};
use crate::conversation::{Error, Refused};
use tokio_postgres::Transaction;

pub(in crate::postgres) enum Failure {
    Domain(Error),
    Database(tokio_postgres::Error),
}

impl From<Error> for Failure {
    fn from(error: Error) -> Self {
        Self::Domain(error)
    }
}
impl From<Refused> for Failure {
    fn from(error: Refused) -> Self {
        Self::Domain(error.into())
    }
}
impl From<tokio_postgres::Error> for Failure {
    fn from(error: tokio_postgres::Error) -> Self {
        Self::Database(error)
    }
}

impl Pool {
    #[cfg(test)]
    pub(in crate::postgres) fn set_test_repeatable_read(&self) -> std::io::Result<()> {
        self.runtime.block_on(async {
            for _ in 0..super::CONNECTIONS {
                let mut held = self.checkout().await?;
                held.as_mut().expect("checked out").client
                    .batch_execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    .await.map_err(failure)?;
            }
            Ok(())
        })
    }

    pub(in crate::postgres) fn record_mutation<T>(
        &self,
        id: &str,
        apply: impl AsyncFn(&Transaction<'_>) -> Result<T, Failure>,
    ) -> Result<T, Error> {
        let lock = format!("{}:records:{id}", self.schema);
        self.runtime.block_on(async {
            for attempt in 0..ATTEMPTS {
                let mut held = self.checkout().await?;
                let connection = held.as_mut().expect("checked out");
                // The advisory lock can wait. Subsequent reads must see the
                // winner's commit even if the operator's default is REPEATABLE READ.
                let tx = connection
                    .client
                    .build_transaction()
                    .isolation_level(tokio_postgres::IsolationLevel::ReadCommitted)
                    .start()
                    .await
                    .map_err(failure)?;
                tx.execute(super::super::sql!("changes/lock"), &[&lock])
                    .await
                    .map_err(failure)?;
                let error = match apply(&tx).await {
                    Ok(answer) => match tx.commit().await {
                        Ok(()) => return Ok(answer),
                        Err(error) => error,
                    },
                    Err(error) => {
                        tx.rollback().await.map_err(failure)?;
                        match error {
                            Failure::Domain(error) => return Err(error),
                            Failure::Database(error) => error,
                        }
                    }
                };
                // Without an explicit database abort, a lost commit response
                // leaves the outcome unknown and must be reported to the client.
                let aborted =
                    contended(&error) || error.code() == Some(&SqlState::T_R_SERIALIZATION_FAILURE);
                if !aborted || attempt + 1 == ATTEMPTS {
                    return Err(failure(error).into());
                }
                drop(held);
                let jitter = uuid::Uuid::new_v4().as_u128() % (10_000 * (attempt as u128 + 1));
                tokio::time::sleep(std::time::Duration::from_micros(jitter as u64)).await;
            }
            unreachable!("the last attempt always returns")
        })
    }
}

//! Check retained counters and references, then publish metadata and the
//! durable receipt in the same transaction as any final incoming items.
use super::{Part, Result, corrupt, reason};
use crate::conversation::Refused;
use crate::conversation::turn_records::{
    Position, Restore, RestoreReceipt, RestoreStream, Restored, Sizes,
};
use crate::postgres::sql;
use serde_json::json;

impl Part<'_, '_> {
    pub(super) async fn publish(
        &self,
        owner: i64,
        restore: &Restore,
        before: Position,
        stream: &RestoreStream,
    ) -> Result<Restored> {
        let at = stream.position();
        let incomplete = || reason(self.id, "restore_incomplete", before);
        if !at.complete {
            return Err(incomplete());
        }
        // These counts come from checked admissions, including on a reopened
        // stream. A damaged stored position may exceed the physical SQL type.
        let count = |n| i64::try_from(n).map_err(|_| corrupt(self.id));
        let query = self.sql(sql!("records/restore_check"));
        let checked = self
            .tx
            .query_one(
                &query,
                &[
                    &self.id,
                    &owner,
                    &count(at.turn)?,
                    &count(at.seq)?,
                    &count(at.projections)?,
                    &count(at.redactions)?,
                ],
            )
            .await?;
        if checked.try_get("occupied")? {
            return Err(Refused::conflict(self.id, "target_occupied").into());
        }
        if !checked.try_get::<_, bool>("complete")? {
            return Err(incomplete());
        }

        let sizes = Sizes::new(self.id, &restore.restore);
        let receipt = RestoreReceipt::new(restore, stream);
        let query = self.sql(sql!("records/restore_publish"));
        let published = self
            .tx
            .query_one(
                &query,
                &[
                    &self.id,
                    &owner,
                    &restore.restore,
                    &json!(receipt),
                    &count(sizes.committed_marker())?,
                ],
            )
            .await?;
        if !published.try_get::<_, bool>("marked")? {
            return Err(corrupt(self.id));
        }
        let mut done = receipt.answer();
        done.stored_bytes = sizes.committed_marker();
        if published.try_get("created")? {
            done.stored_bytes += sizes.conversation_row();
        }
        done.freed_bytes = sizes.bookkeeping();
        Ok(done)
    }
}

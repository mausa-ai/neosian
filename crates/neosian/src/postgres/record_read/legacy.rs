//! Convert one legacy JSON turn in place, even when it is larger than a
//! record page. Each physical database result still obeys the row budget.
use super::{Error, Reader, Value, damaged, failure, values};
use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::TryStreamExt;

impl Reader<'_, '_> {
    pub(super) async fn legacy_messages(&self, turn: u64) -> Result<Value, Error> {
        // Leave room for the JSON envelope, both int4 lengths and base64's
        // four characters per three input bytes.
        let width = ((self.budget.saturating_sub(56) / 4) * 3).clamp(1, 65_536) as i32;
        let number = turn as i32;
        let params: [&(dyn tokio_postgres::types::ToSql + Sync); 3] = [&self.id, &number, &width];
        let stream = self
            .tx
            .query_raw(&self.statements.legacy, params)
            .await
            .map_err(failure)?;
        futures_util::pin_mut!(stream);
        let bad = || damaged(self.id, turn, "its legacy messages are unreadable");
        let mut body = Vec::new();
        let mut received = 0;
        while let Some(row) = stream.try_next().await.map_err(failure)? {
            let chunk = values::row(&row)?;
            values::fits(&chunk, self.budget)?;
            let total = chunk["total"].as_u64().ok_or_else(bad)? as usize;
            if body.is_empty() {
                // A legacy JSON envelope can extend just past a power of
                // two. Growing the buffer repeatedly would then double it.
                body.try_reserve_exact(total)
                    .map_err(std::io::Error::other)?;
                body.resize(total, 0);
            }
            if body.len() != total {
                return Err(bad());
            }
            let pos = chunk["pos"].as_u64().ok_or_else(bad)? as usize;
            let part = STANDARD
                .decode(chunk["chunk"].as_str().ok_or_else(bad)?)
                .map_err(|_| bad())?;
            // SQL leaves rows unordered; placing them by their byte offset
            // avoids an ORDER BY that would copy or sort the document.
            let end = pos.checked_add(part.len()).ok_or_else(bad)?;
            body.get_mut(pos..end)
                .ok_or_else(bad)?
                .copy_from_slice(&part);
            received += part.len();
        }
        if received != body.len() {
            return Err(bad());
        }
        serde_json::from_slice(&body).map_err(|_| bad())
    }
}

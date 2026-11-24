//! Convert one legacy JSON turn in place, even when it is larger than a
//! record page. Each physical database result still obeys the row budget.
use super::{Error, Reader, Value, damaged, failure, values};
use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::TryStreamExt;

impl Reader<'_, '_> {
    pub(super) async fn legacy_messages(&self, turn: u64) -> Result<Value, Error> {
        // The envelope has two keys and a signed int4 offset. Leave room for
        // both and for base64's four characters per three input bytes.
        let width = ((self.budget.saturating_sub(36) / 4) * 3).clamp(1, 65_536) as i32;
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
        while let Some(row) = stream.try_next().await.map_err(failure)? {
            let chunk = values::row(&row)?;
            values::fits(&chunk, self.budget)?;
            let pos = chunk["pos"].as_u64().ok_or_else(bad)? as usize;
            let part = STANDARD
                .decode(chunk["chunk"].as_str().ok_or_else(bad)?)
                .map_err(|_| bad())?;
            // SQL leaves rows unordered; placing them by their byte offset
            // avoids an ORDER BY that would copy or sort the document.
            let end = pos.checked_add(part.len()).ok_or_else(bad)?;
            if end > body.len() {
                body.resize(end, 0);
            }
            body[pos..end].copy_from_slice(&part);
        }
        serde_json::from_slice(&body).map_err(|_| bad())
    }
}

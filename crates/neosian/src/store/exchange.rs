use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use super::{
    FileStore,
    error::{Error, MemoryError},
    exchange_data::check_layout,
    exchange_files::fingerprint,
    layout,
    transaction::WriteOp,
};
use crate::changes::model::Change;
use crate::exchange::model::{Bundle, Checkpoint};
use crate::knowledge::{json::parse, model::invalid};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    format: u32,
    bundle_id: String,
    checkpoint: Checkpoint,
    fingerprint: String,
}

impl FileStore {
    fn receipt_path(&self, scope: &str) -> Result<PathBuf, Error> {
        layout::contained(
            &self.root,
            layout::scope_dir(&self.root, scope).join(".exchange/receipt.json"),
            scope,
        )
    }

    fn receipt(&self, scope: &str) -> Result<Option<Receipt>, Error> {
        let path = self.receipt_path(scope)?;
        if !path.exists() {
            return Ok(None);
        }
        if fs::metadata(&path)?.len() > 16384 {
            return Err(invalid("invalid exchange receipt; snapshot_required"));
        }
        let receipt: Receipt = parse(&fs::read_to_string(path)?)?;
        receipt.checkpoint.validate()?;
        let hash = |s: &str| {
            s.len() == 64
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        };
        if receipt.format != 1
            || receipt.checkpoint.scope != scope
            || !hash(&receipt.bundle_id)
            || !hash(&receipt.fingerprint)
        {
            return Err(invalid("invalid exchange receipt; snapshot_required"));
        }
        Ok(Some(receipt))
    }

    pub fn exchange_status(&self, scope: &str) -> Result<Value, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let receipt = self.receipt(scope)?;
        let files = self.exchange_files(scope)?;
        let digest = fingerprint(&files)?;
        let status = match &receipt {
            Some(r) if r.fingerprint == digest => "ready",
            Some(_) => "conflict",
            None if files.is_empty()
                && !layout::scope_dir(&self.root, scope)
                    .join(".changes")
                    .exists() =>
            {
                "empty"
            }
            None => "snapshot_required",
        };
        Ok(json!({"format":1, "scope":scope, "status":status,
            "checkpoint":receipt.as_ref().map(|r| &r.checkpoint),
            "bundle_id":receipt.as_ref().map(|r| &r.bundle_id)}))
    }

    pub fn apply_exchange(
        &self,
        scope: &str,
        bundle: &Bundle,
        write: bool,
    ) -> Result<Value, Error> {
        bundle.validate()?;
        if bundle.checkpoint.scope != scope {
            return Err(invalid(
                "bundle does not match explicitly selected receiver scope",
            ));
        }
        let _held = self.lock()?;
        self.scope(scope)?;
        let receipt = self.receipt(scope)?;
        let before = self.exchange_files(scope)?;
        let refuse = |reason| MemoryError::conflict(scope, None, reason);
        if let Some(receipt) = &receipt {
            if receipt.fingerprint != fingerprint(&before)? {
                return Err(refuse("replica_changed").into());
            }
            if receipt.bundle_id == bundle.id {
                return Ok(
                    json!({"status":"duplicate", "write":write, "checkpoint":receipt.checkpoint, "bundle_id":bundle.id}),
                );
            }
            if bundle.base.as_ref() != Some(&receipt.checkpoint) {
                return Err(refuse("checkpoint_mismatch").into());
            }
        } else {
            if bundle.base.is_some() {
                return Err(refuse("missing_checkpoint_snapshot_required").into());
            }
            if !before.is_empty()
                || layout::scope_dir(&self.root, scope)
                    .join(".changes")
                    .exists()
            {
                return Err(refuse("target_occupied_snapshot_required").into());
            }
        }
        let mut after = before.clone();
        let staging = self.root.join(".transactions");
        super::fileio::private_mkdir(&staging)?;
        let scratch = crate::limits::Scratch::beside(&staging)?;
        self.exchange_data(scope, bundle, &mut after, &scratch)?;
        check_layout(&self.root, &layout::scope_dir(&self.root, scope), &after)?;
        let receipt = Receipt {
            format: 1,
            bundle_id: bundle.id.clone(),
            checkpoint: bundle.checkpoint.clone(),
            fingerprint: fingerprint(&after)?,
        };
        let directory = layout::scope_dir(&self.root, scope);
        if write {
            let puts = after
                .iter()
                .filter(|(name, artifact)| !before.get(*name).is_some_and(|old| old.same(artifact)))
                .map(|(name, artifact)| {
                    let text = artifact.text().map_err(crate::limits::into_io)?;
                    WriteOp::put(&self.root, &directory.join(name), text)
                });
            let removes = before
                .keys()
                .filter(|name| !after.contains_key(*name))
                .map(|name| WriteOp::remove(&self.root, &directory.join(name)));
            let receipt = WriteOp::put(
                &self.root,
                &self.receipt_path(scope)?,
                serde_json::to_string(&receipt).expect("receipt serializes"),
            )?;
            self.commit_changes_stream(
                scope,
                puts.chain(removes).chain(std::iter::once(Ok(receipt))),
                Change::reset("restored"),
                scratch.remaining(),
            )?;
        }
        Ok(
            json!({"status":if write {"applied"} else {"preview"}, "write":write,
            "bundle_id":bundle.id, "checkpoint":bundle.checkpoint,
            "updates":bundle.updates.len(), "erasures":bundle.erasures.len()}),
        )
    }
}

//! Publish a staged conversation under the root lock. The intent is
//! durable before any move. It becomes the receipt only after both sides
//! of the moves and the removal of staging have reached disk.
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use super::staging::{RestoreState, Staging};
use crate::conversation::rows::{PROJECTIONS, REDACTIONS, TURNS};
use crate::conversation::turn_records::{Restore, RestoreReceipt, Restored, check_id};
use crate::conversation::{Error, Refused};
use crate::store::FileStore;
use crate::store::fileio::atomic_write;

const MARKER: &str = ".committing";

#[cfg(test)]
thread_local! {
    pub(super) static STOP: std::cell::Cell<u8> = const { std::cell::Cell::new(0) };
}

fn step(_n: u8) -> Result<(), Error> {
    #[cfg(test)]
    if STOP.with(std::cell::Cell::get) == _n {
        return Err(Error::Value(format!("stopped after restore step {_n}")));
    }
    Ok(())
}

fn read(path: &Path, id: &str) -> Result<Option<RestoreReceipt>, Error> {
    let contents = match fs::read(path) {
        Ok(text) => text,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    let receipt: RestoreReceipt = serde_json::from_slice(&contents)
        .map_err(|_| Refused::format_unsupported(id, "unreadable restore commit marker"))?;
    check_id("restore", &receipt.restore).map_err(Error::Value)?;
    Ok(Some(receipt))
}

impl FileStore {
    fn restore_receipt_path(&self, id: &str, restore: &str) -> Result<PathBuf, Error> {
        self.contained(
            self.conversation_dir(id)?.join(".restored").join(restore),
            id,
        )
    }

    pub(super) fn committed(&self, id: &str, r: &Restore) -> Result<Option<Restored>, Error> {
        let Some(receipt) = read(&self.restore_receipt_path(id, &r.restore)?, id)? else {
            return Ok(None);
        };
        Ok(Some(receipt.replay(id, r)?))
    }

    pub(super) fn publish(
        &self,
        id: &str,
        r: &Restore,
        staging: &Staging,
        state: &RestoreState,
    ) -> Result<String, Error> {
        let receipt = RestoreReceipt::new(r, &state.stream);
        let marker = self.conversation_file(id, MARKER)?;
        // The records directory contains the entries created by earlier
        // parts. Sync it before the intent promises to publish those files.
        self.sync_existing(&staging.records)?;
        atomic_write(
            &marker,
            &serde_json::to_string(&receipt).map_err(io::Error::other)?,
            true,
        )?;
        self.sync(&marker)?;
        step(1)?;
        self.finish_commit(id, &marker, &receipt)?;
        Ok(receipt.digest)
    }

    pub(super) fn settle_commit(&self, id: &str) -> Result<(), Error> {
        let marker = self.conversation_dir(id)?.join(MARKER);
        if !marker.try_exists()? {
            return Ok(());
        }
        let marker = self.contained(marker, id)?;
        if !self.records_enabled()? {
            return Err(Refused::format_unsupported(
                id,
                "a restore commit marker requires records to be enabled",
            )
            .into());
        }
        if let Some(receipt) = read(&marker, id)? {
            self.finish_commit(id, &marker, &receipt)?;
        }
        Ok(())
    }

    fn finish_commit(
        &self,
        id: &str,
        marker: &Path,
        receipt: &RestoreReceipt,
    ) -> Result<(), Error> {
        let staging = self.staging(id, &receipt.restore)?;
        let at = receipt.position;
        let moves = [
            (&staging.records, super::RECORD_FILES, at.turn > 0),
            (&staging.redactions, REDACTIONS, at.redactions > 0),
            (&staging.turns, TURNS, at.turn > 0),
            (&staging.projections, PROJECTIONS, at.projections > 0),
        ];
        for (index, (source, name, needed)) in moves.into_iter().enumerate() {
            if needed {
                let target = self.conversation_file(id, name)?;
                if source.try_exists()? {
                    fs::rename(source, &target)?;
                } else if !target.try_exists()? {
                    return Err(Refused::format_unsupported(
                        id,
                        &format!("restore commit is missing {name}"),
                    )
                    .into());
                }
            }
            step(index as u8 + 2)?;
        }
        self.sync(marker)?;
        self.sync_existing(&staging.dir)?;
        super::restore_stale::remove(self, &staging.dir)?;
        step(6)?;
        let path = self.restore_receipt_path(id, &receipt.restore)?;
        atomic_write(
            &path,
            &serde_json::to_string(receipt).map_err(io::Error::other)?,
            true,
        )?;
        self.sync(&path)?;
        step(7)?;
        fs::remove_file(marker)?;
        self.sync(marker)?;
        Ok(())
    }
}

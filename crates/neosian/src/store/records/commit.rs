//! Publish a staged conversation under the root lock. The intent is
//! durable before any move. It becomes the receipt only after both sides
//! of the moves and the removal of staging have reached disk.
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::restore::reason;
use super::staging::{RestoreState, Staging};
use crate::conversation::rows::{PROJECTIONS, REDACTIONS, TURNS};
use crate::conversation::turn_records::{
    Item, Position, Restore, Restored, Staged, UnitDigest, check_id,
};
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

#[derive(Serialize, Deserialize)]
struct Receipt {
    restore: String,
    position: Position,
    digest: String,
    // Recognize a lost reply to a final request which itself carried data.
    last_part: Option<(u64, u64, String)>,
}

fn part_digest(r: &Restore) -> Option<(u64, u64, String)> {
    let items = r.items();
    if items.is_empty() {
        return None;
    }
    let mut digest = UnitDigest::default();
    for item in items {
        match item {
            Item::Header(h) => digest.header(h),
            Item::Record(t, row) => digest.record(t, row),
            Item::Projection(_, p) => digest.projection(p),
            Item::Act(_, act) => digest.act(act),
        }
    }
    Some((
        r.projections_from.unwrap_or(0),
        r.redactions_from.unwrap_or(0),
        digest.finish(),
    ))
}

fn read(path: &Path, id: &str) -> Result<Option<Receipt>, Error> {
    let contents = match fs::read(path) {
        Ok(text) => text,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    let receipt: Receipt = serde_json::from_slice(&contents)
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
        if receipt.restore != r.restore {
            return Err(Refused::format_unsupported(
                id,
                "the restore receipt names another restore",
            )
            .into());
        }
        if !r.is_probe()
            && !(r.commit && (r.items_count() == 0 || part_digest(r) == receipt.last_part))
        {
            return Err(reason(id, "restore_committed", receipt.position));
        }
        let mut done = Restored::new(&r.restore, Staged::Committed, Some(receipt.position));
        done.digest = Some(receipt.digest);
        Ok(Some(done))
    }

    pub(super) fn publish(
        &self,
        id: &str,
        r: &Restore,
        staging: &Staging,
        state: &RestoreState,
    ) -> Result<String, Error> {
        let receipt = Receipt {
            restore: r.restore.clone(),
            position: state.stream.position(),
            digest: state.stream.digest(),
            last_part: part_digest(r),
        };
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

    fn finish_commit(&self, id: &str, marker: &Path, receipt: &Receipt) -> Result<(), Error> {
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

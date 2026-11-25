//! Prepare the visible trail while parts arrive. The raw incoming acts
//! remain separate for repeat checks, reference validation, and the digest.
//! Only erasure acts are copied. Publication itself moves a prepared file.
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use super::step;
use crate::conversation::rows::REDACTIONS;
use crate::conversation::turn_records::check_id;
use crate::conversation::{Error, Refused};
use crate::store::FileStore;
use crate::store::fileio::{atomic_write_with, needs_separator, remove_file};
use crate::store::records::staging::Staging;

fn open(path: &Path) -> io::Result<Option<File>> {
    match File::open(path) {
        Ok(file) => Ok(Some(file)),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e),
    }
}

impl FileStore {
    // A trail-only destination can only grow while a restore remains eligible.
    // A legacy write prepares its new length before exposing it. A crash leaves
    // the preparation for the still-visible length usable in either case.
    fn combined_path(&self, staging: &Staging, id: &str, len: u64) -> Result<PathBuf, Error> {
        self.contained(staging.dir.join(format!("trail-{len}")), id)
    }

    /// Refresh a prepared file after an accepted part. Drop an unacknowledged
    /// suffix and copy only the bytes this part added to a current preparation.
    pub(in crate::store::records) fn prepare_staged_trail(
        &self,
        id: &str,
        staging: &Staging,
        prior: u64,
    ) -> Result<(), Error> {
        let Some(mut live) = open(&self.conversation_file(id, REDACTIONS)?)? else {
            return Ok(());
        };
        let len = live.metadata()?.len();
        if len == 0 {
            return Ok(());
        }
        let combined = self.combined_path(staging, id, len)?;
        let mut raw = File::open(&staging.redactions)?;
        let separate = needs_separator(&mut live, len)?;
        let start = len
            .checked_add(u64::from(separate))
            .and_then(|n| n.checked_add(prior))
            .ok_or_else(|| {
                Refused::format_unsupported(id, "restore trail exceeds the file size limit")
            })?;
        if let Some(prepared) = open(&combined)?
            && prepared.metadata()?.len() >= start
        {
            let size = prepared.metadata()?.len();
            if size == start && raw.metadata()?.len() == prior {
                return Ok(());
            }
            let mut prepared = OpenOptions::new().write(true).open(&combined)?;
            prepared.set_len(start)?;
            prepared.seek(SeekFrom::Start(start))?;
            raw.seek(SeekFrom::Start(prior))?;
            io::copy(&mut raw, &mut prepared)?;
            prepared.sync_all()?;
        } else {
            combine(&combined, &mut live, &mut raw)?;
        }
        Ok(())
    }

    /// A legacy trail-only restore may append acts after all the record parts
    /// were staged. Prepare its prospective destination before the atomic
    /// rename, so the later record commit still publishes with renames alone.
    pub(in crate::store) fn prepare_after_legacy_trail(
        &self,
        id: &str,
        future: &mut File,
    ) -> Result<(), Error> {
        let dir = self.restores_dir(id)?;
        let entries = match fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(e.into()),
        };
        let live = open(&self.conversation_file(id, REDACTIONS)?)?
            .map(|f| f.metadata())
            .transpose()?
            .map_or(0, |m| m.len());
        for entry in entries {
            let entry = entry?;
            let name = entry.file_name();
            let Some(restore) = name.to_str() else {
                continue;
            };
            if !entry.file_type()?.is_dir() || check_id("restore", restore).is_err() {
                continue;
            }
            let staging = self.staging(id, restore)?;
            if let Some(mut raw) = open(&staging.redactions)? {
                // Keep the pre-rename generation until the legacy write lands.
                // A crash can leave its other candidate behind; retire it here.
                for previous in fs::read_dir(&staging.dir)? {
                    let entry = previous?;
                    if let Some(len) = entry
                        .file_name()
                        .to_str()
                        .and_then(|name| name.strip_prefix("trail-")?.parse::<u64>().ok())
                        && len != live
                    {
                        fs::remove_file(self.combined_path(&staging, id, len)?)?;
                    }
                }
                let path = self.combined_path(&staging, id, future.metadata()?.len())?;
                combine(&path, future, &mut raw)?;
                self.sync(&path)?;
            }
        }
        Ok(())
    }

    pub(super) fn publish_trail(
        &self,
        id: &str,
        staging: &Staging,
        target: &Path,
    ) -> Result<(), Error> {
        let ready = self.contained(staging.dir.join("trail-ready"), id)?;
        let changed = || Refused::format_unsupported(id, "restore commit trail changed");
        let (len, final_len) = if let Some(mut marker) = open(&ready)? {
            let mut prior = [0; 8];
            let mut total = [0; 8];
            if marker.metadata()?.len() != 16 {
                return Err(changed().into());
            }
            marker.read_exact(&mut prior)?;
            marker.read_exact(&mut total)?;
            (u64::from_le_bytes(prior), u64::from_le_bytes(total))
        } else {
            let Some(mut raw) = open(&staging.redactions)? else {
                return if target.try_exists()? {
                    Ok(())
                } else {
                    Err(Refused::format_unsupported(
                        id,
                        "restore commit is missing redactions.jsonl",
                    )
                    .into())
                };
            };
            let len = open(target)?
                .map(|f| f.metadata())
                .transpose()?
                .map_or(0, |m| m.len());
            if len == 0 {
                return Ok(fs::rename(&staging.redactions, target)?);
            }
            let prepared = self.combined_path(staging, id, len)?;
            if !prepared.try_exists()? {
                // A marker from an older binary can precede its original trail
                // move. Prepare once from the still-visible old and staged acts.
                combine(&prepared, &mut File::open(target)?, &mut raw)?;
            }
            let final_len = fs::metadata(&prepared)?.len();
            atomic_write_with(&ready, true, |f| {
                f.write_all(&len.to_le_bytes())?;
                f.write_all(&final_len.to_le_bytes())
            })?;
            (len, final_len)
        };
        self.sync(&ready)?;
        step(8)?;
        let prepared = self.combined_path(staging, id, len)?;
        if prepared.try_exists()? {
            if fs::metadata(target)?.len() != len || fs::metadata(&prepared)?.len() != final_len {
                return Err(changed().into());
            }
            // Remove the raw source durably before the prepared rename. Even an
            // older receipt-only marker can then be replayed without duplication.
            remove_file(&staging.redactions)?;
            self.sync(&staging.redactions)?;
            step(9)?;
            fs::rename(prepared, target)?;
        } else if staging.redactions.try_exists()? || fs::metadata(target)?.len() != final_len {
            return Err(changed().into());
        }
        Ok(())
    }
}

fn combine(path: &Path, live: &mut File, raw: &mut File) -> io::Result<()> {
    atomic_write_with(path, true, |out| {
        let len = live.metadata()?.len();
        let separate = needs_separator(live, len)?;
        live.rewind()?;
        io::copy(live, out)?;
        if separate {
            out.write_all(b"\n")?;
        }
        io::copy(raw, out)?;
        Ok(())
    })
}

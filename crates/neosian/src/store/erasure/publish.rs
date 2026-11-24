//! The erasure transaction owns three private directories under the
//! conversation. Only a synced rename from preparation to pending commits
//! the selection. After all replacements and removals are durable, rename
//! pending to retired so interrupted cleanup can resume without the plan.
use super::stage;
use crate::conversation::rows::{PROJECTIONS, REDACTIONS, TURNS};
use crate::conversation::turn_records::check_draft_id;
use crate::conversation::{Error, Refused};
use crate::store::{FileStore, fileio, transaction};
use serde::{Deserialize, Serialize};
use std::fs;
use std::io;
use std::path::Path;

const PREPARE: &str = ".erase-preparing";
const PENDING: &str = ".erase-pending";
const RETIRED: &str = ".erase-retired";
pub(super) const FILES: &str = "files";
#[derive(Serialize, Deserialize)]
pub(super) struct Plan {
    pub all: bool,
    pub turns: bool,
    pub projections: bool,
}
#[cfg(test)]
thread_local! {
    pub(in crate::store) static STOP:std::cell::Cell<u8>=const {std::cell::Cell::new(0)};
}
fn step(_n: u8) -> Result<(), Error> {
    #[cfg(test)]
    if STOP.with(std::cell::Cell::get) == _n {
        return Err(Error::Value(format!("stopped after erase step {_n}")));
    }
    Ok(())
}
fn remove(path: &Path, dir: bool) -> io::Result<()> {
    let result = if dir {
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    };
    match result {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        v => v,
    }
}

impl FileStore {
    pub(super) fn erase_selected(
        &self,
        id: &str,
        through: Option<u64>,
        named: Option<&[u64]>,
        actor: Option<&str>,
    ) -> Result<usize, Error> {
        let dir = self.conversation_dir(id)?;
        let all = through.is_none() && named.is_none();
        let turns = self.conversation_file(id, TURNS)?;
        if !turns.is_file() && !(all && (dir.is_dir() || self.restores_dir(id)?.is_dir())) {
            return Ok(0);
        }
        let preparing = self.conversation_file(id, PREPARE)?;
        fileio::private_mkdir(&preparing)?;
        let result = (|| {
            let (plan, count) = stage::prepare(self, &preparing, id, through, named, actor)?;
            if count == 0 && !all {
                return Ok(0);
            }
            if all {
                // Check every live path before making the intent durable.
                for name in ["records", ".restored", ".drafts"] {
                    self.conversation_file(id, name)?;
                }
                self.restores_dir(id)?;
            }
            let manifest = preparing.join("plan");
            fileio::atomic_write(
                &manifest,
                &serde_json::to_string(&plan).map_err(io::Error::other)?,
                true,
            )?;
            transaction::sync_parents(&self.root, &manifest)?;
            step(1)?;
            let pending = self.conversation_file(id, PENDING)?;
            fs::rename(&preparing, &pending)?;
            transaction::sync_parents(&self.root, &pending)?;
            step(2)?;
            self.apply_erasure(id, &pending, plan)?;
            Ok(count)
        })();
        remove(&preparing, true)?;
        result
    }

    pub(in crate::store) fn settle_erasure(&self, id: &str) -> Result<(), Error> {
        let dir = self.conversation_dir(id)?;
        for name in [PREPARE, RETIRED] {
            if dir.join(name).try_exists()? {
                remove(&self.conversation_file(id, name)?, true)?;
            }
        }
        if !dir.join(PENDING).try_exists()? {
            return Ok(());
        }
        let pending = self.conversation_file(id, PENDING)?;
        let manifest = self.conversation_file(id, &format!("{PENDING}/plan"))?;
        let plan = serde_json::from_slice(&fs::read(manifest)?)
            .map_err(|_| Refused::format_unsupported(id, "unreadable erasure plan"))?;
        self.apply_erasure(id, &pending, plan)
    }

    fn apply_erasure(&self, id: &str, pending: &Path, plan: Plan) -> Result<(), Error> {
        for (index, (name, needed)) in [
            (TURNS, plan.turns),
            (PROJECTIONS, plan.projections),
            (REDACTIONS, plan.turns),
        ]
        .into_iter()
        .enumerate()
        {
            if needed {
                let src = self.conversation_file(id, &format!("{PENDING}/{name}"))?;
                let dest = self.conversation_file(id, name)?;
                if src.try_exists()? {
                    fs::rename(&src, &dest)?;
                } else if !dest.is_file() {
                    return Err(
                        Refused::format_unsupported(id, "missing erasure replacement").into(),
                    );
                }
            }
            step(3 + index as u8)?;
        }
        if plan.all {
            for name in ["records", ".drafts", ".restored"] {
                remove(&self.conversation_file(id, name)?, true)?;
            }
            let restores = self.restores_dir(id)?;
            remove(&restores, true)?;
            self.sync_existing(
                restores
                    .parent()
                    .expect("restore conversation directory has a parent"),
            )?;
        } else {
            let files = self.conversation_file(id, &format!("{PENDING}/{FILES}"))?;
            if !files.is_file() {
                return Err(Refused::format_unsupported(id, "missing erasure file list").into());
            }
            stage::each(&files, |raw, _| {
                let (turn, draft): (u64, Option<String>) =
                    serde_json::from_str(raw).map_err(io::Error::other)?;
                if let Some(draft) = draft {
                    check_draft_id(&draft).map_err(Error::Value)?;
                    remove(
                        &self.conversation_file(id, &format!(".drafts/{draft}.sealed"))?,
                        false,
                    )?;
                }
                remove(&self.record_file(id, turn)?, false)?;
                Ok(())
            })?;
            for name in ["records", ".drafts"] {
                self.sync_existing(&self.conversation_file(id, name)?)?;
            }
        }
        step(6)?;
        transaction::sync_parents(&self.root, &pending.join("plan"))?;
        let retired = self.conversation_file(id, RETIRED)?;
        fs::rename(pending, &retired)?;
        transaction::sync_parents(&self.root, &retired)?;
        step(7)?;
        remove(&retired, true)?;
        transaction::sync_parents(&self.root, &retired)?;
        Ok(())
    }
}

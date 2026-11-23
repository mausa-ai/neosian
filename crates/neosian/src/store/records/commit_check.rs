//! Validate references against staged and incoming headers before changing
//! the restore. Record counts and byte totals are checked as records arrive.
use std::collections::HashSet;
use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use super::restore::reason;
use super::staging::Staging;
use crate::conversation::rows::{PROJECTIONS, TURNS, parse_projection, parse_redaction};
use crate::conversation::rows_header::{Row, parse_row};
use crate::conversation::turn_records::{Position, Restore};
use crate::conversation::{Error, Refused};
use crate::store::FileStore;

fn each(path: &Path, mut visit: impl FnMut(&str, usize) -> Result<(), Error>) -> Result<(), Error> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e.into()),
    };
    for (index, line) in BufReader::new(file).lines().enumerate() {
        visit(&line?, index + 1)?;
    }
    Ok(())
}

impl FileStore {
    pub(super) fn check_commit(
        &self,
        id: &str,
        r: &Restore,
        staging: Option<&Staging>,
        before: Position,
        after: Position,
    ) -> Result<Vec<PathBuf>, Error> {
        let incomplete = || reason(id, "restore_incomplete", before);
        if !after.complete {
            return Err(incomplete());
        }
        if self.conversation_file(id, TURNS)?.is_file()
            || self.conversation_file(id, PROJECTIONS)?.is_file()
        {
            return Err(Refused::conflict(id, "target_occupied").into());
        }
        let target_records = self.conversation_file(id, super::RECORD_FILES)?;
        self.conversation_file(id, crate::conversation::rows::REDACTIONS)?;
        if after.turn > 0
            && target_records.try_exists()?
            && (!target_records.is_dir() || fs::read_dir(&target_records)?.next().is_some())
        {
            return Err(
                Refused::format_unsupported(id, "the empty target already holds records").into(),
            );
        }
        let mut turns: HashSet<u64> = r.turns.iter().map(|h| h.turn).collect();
        let mut record_files = HashSet::new();
        if let Some(staging) = staging {
            each(&staging.turns, |line, n| {
                let row = parse_row(line, n, id, true)?;
                turns.insert(row.turn());
                if let Row::Records(header) = row {
                    let file = self.staged_records(staging, header.turn, id)?;
                    if !file.is_file() {
                        return Err(super::drafts::missing(id, header.turn));
                    }
                    record_files.insert(file);
                }
                Ok(())
            })?;
        }
        let exists = |turn: u64| turns.contains(&turn).then_some(()).ok_or_else(incomplete);
        if let Some(staging) = staging {
            each(&staging.projections, |line, n| {
                exists(parse_projection(line, n, id)?.turn)
            })?;
            each(&staging.redactions, |line, n| {
                parse_redaction(line, n, id)?
                    .turns
                    .into_iter()
                    .try_for_each(exists)
            })?;
        }
        for entry in &r.projections {
            exists(entry.turn)?;
        }
        for act in &r.redactions {
            act.turns.iter().copied().try_for_each(exists)?;
        }
        // A killed, unacknowledged part may have created record files past
        // its last durable header. Never publish those with the directory.
        let mut leftovers = Vec::new();
        if let Some(staging) = staging.filter(|s| s.records.is_dir()) {
            for entry in fs::read_dir(&staging.records)? {
                let entry = entry?;
                if !record_files.contains(&entry.path()) {
                    leftovers.push(entry.path());
                }
            }
        }
        Ok(leftovers)
    }
}

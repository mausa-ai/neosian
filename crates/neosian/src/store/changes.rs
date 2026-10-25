//! The event files and stream head participate in the same redo plan as memory.

use std::fs;
use std::path::PathBuf;

use super::{
    FileStore,
    error::Error,
    layout,
    transaction::{self, WriteOp},
};
use crate::changes::{
    cursor,
    model::{Change, Event, Head, Kind, Page, Pruned, Request, invalid},
};

impl FileStore {
    fn change_file(&self, scope: &str, name: &str) -> Result<PathBuf, Error> {
        layout::contained(
            &self.root,
            layout::scope_dir(&self.root, scope)
                .join(".changes")
                .join(name),
            name,
        )
    }

    fn change_head(&self, scope: &str) -> Result<Option<Head>, Error> {
        let file = self.change_file(scope, "head.json")?;
        if !file.exists() {
            let events = self.change_file(scope, "events")?;
            if events.exists() && fs::read_dir(events)?.next().is_some() {
                return Err(invalid("change stream is missing its head"));
            }
            return Ok(None);
        }
        if fs::metadata(&file)?.len() > 2048 {
            return Err(invalid("oversized change-stream head"));
        }
        let head: Head = serde_json::from_slice(&fs::read(file)?)
            .map_err(|_| invalid("invalid change-stream head"))?;
        head.validate()?;
        Ok(Some(head))
    }

    fn change_event(&self, scope: &str, head: &Head, sequence: u64) -> Result<Event, Error> {
        let file = self.change_file(scope, &format!("events/{sequence:020}.json"))?;
        if fs::metadata(&file)?.len() > 32768 {
            return Err(invalid("oversized change event"));
        }
        let event: Event = serde_json::from_slice(&fs::read(file)?)
            .map_err(|_| invalid("invalid change event"))?;
        event.validate()?;
        if event.scope != scope || event.stream != head.stream || event.sequence != sequence {
            return Err(invalid("change event does not match its stream position"));
        }
        Ok(event)
    }

    pub(super) fn commit_changes(
        &self,
        scope: &str,
        mut writes: Vec<WriteOp>,
        change: Change,
    ) -> Result<(), Error> {
        writes.extend(self.change_writes(scope, change)?);
        transaction::commit(&self.root, writes)?;
        Ok(())
    }

    pub(super) fn commit_changes_stream(
        &self,
        scope: &str,
        writes: impl Iterator<Item = std::io::Result<WriteOp>>,
        change: Change,
        maximum: u64,
    ) -> Result<(), Error> {
        let events = self.change_writes(scope, change)?;
        transaction::commit_stream(
            &self.root,
            writes.chain(events.into_iter().map(Ok)),
            maximum,
        )?;
        Ok(())
    }

    fn change_writes(&self, scope: &str, change: Change) -> Result<Vec<WriteOp>, Error> {
        let mut writes = vec![];
        let found = self.change_head(scope)?;
        let mut head = found.clone().unwrap_or_else(Head::new);
        let mut changes = vec![];
        if found.is_none() && change.kind != Kind::Reset {
            changes.push(Change::reset("tracking_started"));
        }
        changes.push(change);
        for change in changes {
            let event = head.event(scope, change)?;
            let file = self.change_file(scope, &format!("events/{:020}.json", event.sequence))?;
            writes.push(WriteOp::put(
                &self.root,
                &file,
                serde_json::to_string(&event).expect("event serializes"),
            )?);
        }
        writes.push(WriteOp::put(
            &self.root,
            &self.change_file(scope, "head.json")?,
            serde_json::to_string(&head).expect("head serializes"),
        )?);
        Ok(writes)
    }

    pub fn read_changes(&self, scope: &str, request: &Request) -> Result<Page, Error> {
        request.validate()?;
        let _held = self.lock()?;
        self.scope(scope)?;
        let head = self.change_head(scope)?;
        let (mut page, after, end) = cursor::page(scope, head.as_ref(), request)?;
        if let Some(head) = head {
            for sequence in after + 1..=end {
                page.events.push(self.change_event(scope, &head, sequence)?);
            }
        }
        Ok(page)
    }

    pub fn prune_changes(&self, scope: &str, through: &str, write: bool) -> Result<Pruned, Error> {
        let _held = self.lock()?;
        self.scope(scope)?;
        let mut head = self
            .change_head(scope)?
            .ok_or_else(|| invalid("scope has no change stream"))?;
        let end = cursor::through(scope, &head, through)?;
        let removed = end - head.floor;
        let mut writes = vec![];
        for sequence in head.floor + 1..=end {
            self.change_event(scope, &head, sequence)?;
            writes.push(WriteOp::remove(
                &self.root,
                &self.change_file(scope, &format!("events/{sequence:020}.json"))?,
            )?);
        }
        head.floor = end;
        if write && removed > 0 {
            writes.push(WriteOp::put(
                &self.root,
                &self.change_file(scope, "head.json")?,
                serde_json::to_string(&head).expect("head serializes"),
            )?);
            transaction::commit(&self.root, writes)?;
        }
        Ok(Pruned {
            format: 1,
            scope: scope.into(),
            removed,
            write,
            earliest_cursor: cursor::encode(scope, &head, end),
        })
    }
}

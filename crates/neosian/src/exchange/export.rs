use std::collections::BTreeSet;

use super::model::{
    Bundle, Checkpoint, Deletion, Erasure, MAX_EVENTS, MAX_PATHS, Record, Update, selected,
};
use crate::changes::model::{Kind, Page, Request};
use crate::knowledge::model::invalid;
use crate::state::State;
use crate::store::{error::Error, journal::Action};

fn page(store: &dyn State, scope: &str, cursor: Option<String>) -> Result<Page, Error> {
    let request = Request { cursor, limit: 100 };
    let page = store.read_changes(scope, &request)?;
    request.check_page(scope, &page)?;
    Ok(page)
}

fn checkpoint(page: &Page) -> Result<Checkpoint, Error> {
    Ok(Checkpoint {
        format: 1,
        scope: page.scope.clone(),
        stream: page.stream.clone().ok_or_else(|| {
            invalid("source untracked; snapshot_required after a tracked mutation")
        })?,
        cursor: page
            .head_cursor
            .clone()
            .ok_or_else(|| invalid("source untracked; snapshot_required"))?,
    })
}

pub fn collect(store: &dyn State, scope: &str, base: Option<Checkpoint>) -> Result<Bundle, Error> {
    if let Some(base) = &base {
        base.validate()?;
        if base.scope != scope {
            return Err(invalid("checkpoint belongs to another scope"));
        }
    }
    let mut read = page(store, scope, base.as_ref().map(|c| c.cursor.clone()))?;
    let end = checkpoint(&read)?;
    let mut paths = BTreeSet::new();
    let mut erasures = vec![];
    if let Some(base) = &base {
        if base.stream != end.stream {
            return Err(invalid("source stream changed; snapshot_required"));
        }
        let mut count = 0;
        let mut last_sequence = None;
        let mut cursors = BTreeSet::from([base.cursor.clone()]);
        loop {
            if read.gap.is_some() || checkpoint(&read)? != end {
                return Err(invalid(
                    "source gap or concurrent mutation; snapshot_required",
                ));
            }
            count += read.events.len();
            if count > MAX_EVENTS {
                return Err(invalid(
                    "exchange interval exceeds 10000 events; snapshot_required",
                ));
            }
            for event in &read.events {
                if last_sequence.is_some_and(|n| event.sequence != n + 1) {
                    return Err(invalid("noncontiguous source events; snapshot_required"));
                }
                last_sequence = Some(event.sequence);
                let change = &event.change;
                if change.kind == Kind::Reset {
                    return Err(invalid("source reset; snapshot_required"));
                }
                for path in change
                    .path
                    .iter()
                    .chain(&change.previous_path)
                    .filter(|p| selected(p))
                {
                    paths.insert(path.clone());
                }
                if change.kind == Kind::ScopeErased
                    || (change.kind == Kind::Erased
                        && change.path.as_ref().is_some_and(|p| selected(p)))
                {
                    erasures.push(Erasure {
                        path: change.path.clone(),
                        created_at: change.created_at.clone(),
                    });
                }
            }
            if !read.has_more {
                if read.cursor != read.head_cursor {
                    return Err(invalid("incomplete change interval; snapshot_required"));
                }
                break;
            }
            let cursor = read.cursor.clone().expect("validated page cursor");
            if !cursors.insert(cursor.clone()) {
                return Err(invalid("repeated change cursor; snapshot_required"));
            }
            read = page(store, scope, Some(cursor))?;
        }
    }
    if base.is_none() || erasures.iter().any(|e| e.path.is_none()) {
        for entry in store.list_documents(scope, "")? {
            if selected(&entry.path) {
                paths.insert(entry.path);
            }
        }
    }
    if paths.len() > MAX_PATHS {
        return Err(invalid("exchange selects more than 1000 paths"));
    }
    let mut updates = vec![];
    let mut bytes = 0;
    for path in paths {
        let (document, deletion) = match store.read(scope, &path)? {
            Some(doc) => (Some(Record::from_document(scope, &path, &doc)?), None),
            None if base.is_some() => {
                let rows = store.versions(scope, &path, 1)?;
                let row = rows
                    .first()
                    .filter(|r| r.action == Action::Deleted)
                    .ok_or_else(|| invalid("missing deletion skeleton; snapshot_required"))?;
                (
                    None,
                    Some(Deletion {
                        version: row.version,
                        actor: row.actor.clone(),
                        created_at: row.created_at.utc().z(),
                        redacted: row.redacted,
                    }),
                )
            }
            None => {
                return Err(invalid(
                    "source changed during snapshot; retry with paused writers",
                ));
            }
        };
        if let Some(record) = &document {
            record.validate()?;
        }
        let update = Update {
            path,
            document,
            deletion,
        };
        bytes += serde_json::to_vec(&update)
            .expect("update serializes")
            .len();
        if bytes > super::model::MAX_BYTES {
            return Err(invalid("exchange bundle exceeds 20 MiB"));
        }
        updates.push(update);
    }
    let check = page(store, scope, Some(end.cursor.clone()))?;
    if checkpoint(&check)? != end || check.gap.is_some() || !check.events.is_empty() {
        return Err(invalid(
            "source changed during export; retry with paused writers",
        ));
    }
    let mut bundle = Bundle {
        format: 1,
        id: String::new(),
        base,
        checkpoint: end,
        updates,
        erasures,
    };
    bundle.id = bundle.digest();
    bundle.validate()?;
    Ok(bundle)
}

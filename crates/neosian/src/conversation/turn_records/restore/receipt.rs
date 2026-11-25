//! The durable final position and fingerprints shared by both restore stores.
//! Keep the serialized form compatible with the FileStore commit marker.
use super::{Item, Position, Restore, RestoreStream, Restored, Staged};
use crate::conversation::turn_records::{UnitDigest, conflict};
use crate::conversation::{Error, Refused};
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Serialize, Deserialize)]
pub(crate) struct RestoreReceipt {
    pub restore: String,
    pub position: Position,
    pub digest: String,
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

impl RestoreReceipt {
    pub fn new(r: &Restore, stream: &RestoreStream) -> Self {
        Self {
            restore: r.restore.clone(),
            position: stream.position(),
            digest: stream.digest(),
            last_part: part_digest(r),
        }
    }

    pub fn answer(self) -> Restored {
        let mut done = Restored::new(&self.restore, Staged::Committed, Some(self.position));
        done.digest = Some(self.digest);
        done
    }

    /// A probe, empty commit, or replay of the final content-bearing commit.
    pub fn replay(self, id: &str, r: &Restore) -> Result<Restored, Error> {
        if self.restore != r.restore {
            return Err(Refused::format_unsupported(
                id,
                "the restore receipt names another restore",
            )
            .into());
        }
        if !r.is_probe()
            && !(r.commit && (r.items_count() == 0 || part_digest(r) == self.last_part))
        {
            return Err(conflict(
                id,
                "restore_committed",
                json!({"position":self.position.to_json()}),
            )
            .into());
        }
        Ok(self.answer())
    }
}

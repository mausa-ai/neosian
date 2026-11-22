//! A staged restore's files on a FileStore (DESIGN §40.54 items 4.2 and
//! 12, T1b2b1), nested under its conversation so two conversations
//! restored with one id never share files:
//!
//! ```text
//! .records/restore/<conversation>/<restore>/state              its state, written last
//! .records/restore/<conversation>/<restore>/turns.jsonl        headers and skeletons, as committed
//! .records/restore/<conversation>/<restore>/records/<n>.jsonl  each turn's record lines
//! .records/restore/<conversation>/<restore>/projections.jsonl  projection rows
//! .records/restore/<conversation>/<restore>/redactions.jsonl   trail rows
//! ```
//!
//! No reader, numbering, listing or search looks there. Every path is held
//! to the store's containment check. The state counts each appended file's
//! bytes, so lines a cut-off request left past the count are dropped by the
//! next request, as a draft's are.

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::conversation::rows::{PROJECTIONS, REDACTIONS, TURNS, render_turn};
use crate::conversation::rows_header::{RecordHeader, parse_row, render_header};
use crate::conversation::search::line_text;
use crate::conversation::turn_records::{Header, RestoreStream};
use crate::conversation::{Error, Refused, Turn};
use crate::store::FileStore;
use crate::store::fileio::{atomic_write, within};
use crate::store::time::Stamp;

const RESTORES: &str = ".records/restore";
const STATE: &str = "state";

/// A staged restore's files.
pub struct Staging {
    pub dir: PathBuf,
    pub state: PathBuf,
    pub turns: PathBuf,
    pub records: PathBuf,
    pub projections: PathBuf,
    pub redactions: PathBuf,
}

/// The bytes of each appended file the state counts; `records` is the
/// current turn's records file, None when that turn has none (redacted).
#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize)]
pub struct Files {
    pub turns: u64,
    pub records: Option<u64>,
    pub projections: u64,
    pub redactions: u64,
}

/// What a staged restore keeps between requests.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RestoreState {
    pub neosian_format: u64,
    pub opened_at: String,
    pub touched_at: String,
    /// Size-rule bytes staged: its bookkeeping and every item.
    pub bytes: u64,
    pub stream: RestoreStream,
    pub files: Files,
}

impl RestoreState {
    pub fn new(bytes: u64) -> Self {
        let now = Stamp::now().z();
        Self {
            neosian_format: 1,
            opened_at: now.clone(),
            touched_at: now,
            bytes,
            stream: RestoreStream::default(),
            files: Files::default(),
        }
    }
}

fn escapes(conversation_id: &str) -> Error {
    Refused::id_invalid(conversation_id, "escapes the store root").into()
}

fn unreadable(conversation_id: &str, what: &str) -> Error {
    Refused::format_unsupported(conversation_id, &format!("{what} is unreadable")).into()
}

impl FileStore {
    /// A path the store keeps a restore in, refused when a symlink would
    /// carry it outside the root.
    pub(super) fn contained(&self, path: PathBuf, conversation_id: &str) -> Result<PathBuf, Error> {
        if within(&self.root, &path)? {
            Ok(path)
        } else {
            Err(escapes(conversation_id))
        }
    }

    /// A conversation's restores, `.records/restore/<conversation>/`.
    pub(super) fn restores_dir(&self, conversation_id: &str) -> Result<PathBuf, Error> {
        self.conversation_dir(conversation_id)?;
        self.contained(
            self.root.join(RESTORES).join(conversation_id),
            conversation_id,
        )
    }

    /// One staged restore's files, each held inside the root.
    pub(super) fn staging(&self, conversation_id: &str, restore: &str) -> Result<Staging, Error> {
        let dir = self.contained(
            self.restores_dir(conversation_id)?.join(restore),
            conversation_id,
        )?;
        let file = |name: &str| self.contained(dir.join(name), conversation_id);
        Ok(Staging {
            state: file(STATE)?,
            turns: file(TURNS)?,
            records: file(super::RECORD_FILES)?,
            projections: file(PROJECTIONS)?,
            redactions: file(REDACTIONS)?,
            dir,
        })
    }

    /// A staged turn's records file, held inside the root.
    pub(super) fn staged_records(
        &self,
        staging: &Staging,
        turn: u64,
        id: &str,
    ) -> Result<PathBuf, Error> {
        self.contained(staging.records.join(format!("{turn}.jsonl")), id)
    }
}

/// A file's length, 0 when absent.
fn length(path: &Path) -> u64 {
    fs::metadata(path).map_or(0, |meta| meta.len())
}

/// Cut a file back to the bytes the state counts; shorter is unreadable.
fn cut(path: &Path, counted: u64, conversation_id: &str) -> Result<(), Error> {
    let found = length(path);
    if found < counted {
        return Err(unreadable(conversation_id, "a staged restore's file"));
    }
    if found > counted {
        fs::OpenOptions::new()
            .write(true)
            .open(path)?
            .set_len(counted)?;
    }
    Ok(())
}

/// The restore's state, its files cut back to what it counts.
pub fn load(
    store: &FileStore,
    staging: &Staging,
    conversation_id: &str,
) -> Result<Option<RestoreState>, Error> {
    let text = match fs::read_to_string(&staging.state) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) if error.kind() == io::ErrorKind::InvalidData => {
            return Err(unreadable(conversation_id, "a restore's state"));
        }
        Err(error) => return Err(error.into()),
    };
    let state: RestoreState = serde_json::from_str(&text)
        .ok()
        .filter(|state: &RestoreState| state.neosian_format == 1)
        .ok_or_else(|| unreadable(conversation_id, "a restore's state"))?;
    let files = state.files;
    cut(&staging.turns, files.turns, conversation_id)?;
    cut(&staging.projections, files.projections, conversation_id)?;
    cut(&staging.redactions, files.redactions, conversation_id)?;
    if let Some(counted) = files.records {
        let turn = state.stream.position().turn;
        cut(
            &store.staged_records(staging, turn, conversation_id)?,
            counted,
            conversation_id,
        )?;
    }
    Ok(Some(state))
}

pub fn save(staging: &Staging, state: &RestoreState) -> Result<(), Error> {
    let text = serde_json::to_string(state).map_err(io::Error::other)?;
    atomic_write(&staging.state, &text, true)?;
    Ok(())
}

/// A header's line as the conversation's log will hold it: a format-2
/// header naming the restore, or a redacted turn's format-1 skeleton.
pub fn header_line(header: &Header, restore: &str, conversation_id: &str) -> String {
    if header.redacted {
        return render_turn(&Turn {
            conversation_id: conversation_id.to_string(),
            turn: header.turn,
            messages: Vec::new(),
            created_at: header.created_at,
            actor: header.actor.clone(),
            redacted: true,
        });
    }
    render_header(&RecordHeader {
        turn: header.turn,
        created_at: header.created_at,
        actor: header.actor.clone(),
        records: header.records,
        bytes: header.bytes,
        draft: restore.to_string(),
    })
}

/// Lines `from..from + count` of a staged file, a line at a time.
pub fn lines(path: &Path, from: u64, count: usize) -> Result<Vec<String>, Error> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
    };
    let lines = BufReader::new(file).lines().skip(from as usize).take(count);
    Ok(lines.collect::<io::Result<Vec<_>>>()?)
}

/// The staged lines of the headers of `turns` (ascending), in one pass
/// over the log a line at a time, whatever their number: a repeated part
/// of many headers costs the log once, never once per header. None where
/// no header of that turn is staged.
pub fn headers_of(
    staging: &Staging,
    turns: &[u64],
    conversation_id: &str,
) -> Result<Vec<Option<String>>, Error> {
    let mut found = vec![None; turns.len()];
    let file = match fs::File::open(&staging.turns) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(found),
        Err(error) => return Err(error.into()),
    };
    let mut next = 0;
    for (index, text) in BufReader::new(file).lines().enumerate() {
        if next == turns.len() {
            break;
        }
        let text = text?;
        let turn = parse_row(line_text(&text), index + 1, conversation_id, true)?.turn();
        while next < turns.len() && turns[next] < turn {
            next += 1;
        }
        if next < turns.len() && turns[next] == turn {
            found[next] = Some(text);
            next += 1;
        }
    }
    Ok(found)
}

//! A FileStore draft's files (DESIGN §40.54 item 4.2): its records, one
//! compact line each with its digest, in `<draft>.jsonl`; its state in
//! `<draft>.state`, written after the lines it counts, so a request cut off
//! between the two leaves lines past `file_bytes` that the next request
//! drops; and `<draft>.sealed`, the header its seal made.

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use super::super::fileio::atomic_write;
use crate::conversation::rows_header::{RecordHeader, Row, parse_row};
use crate::conversation::search::line_text;
use crate::conversation::turn_records::{DraftCheck, TurnRecord};
use crate::conversation::{Error, Refused};
use crate::pyfmt::dumps;
use crate::store::time::Stamp;

/// A draft's three files.
pub struct Paths {
    pub records: PathBuf,
    pub state: PathBuf,
    pub sealed: PathBuf,
}

impl Paths {
    pub fn new(drafts: &Path, draft: &str) -> Self {
        Self {
            records: drafts.join(format!("{draft}.jsonl")),
            state: drafts.join(format!("{draft}.state")),
            sealed: drafts.join(format!("{draft}.sealed")),
        }
    }
}

/// What a draft keeps between requests.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DraftState {
    pub neosian_format: u64,
    pub actor: Option<String>,
    pub turn_bytes: Option<u64>,
    pub opened_at: String,
    pub touched_at: String,
    /// Size-rule bytes the draft holds: its bookkeeping and its records.
    pub bytes: u64,
    /// Bytes of `<draft>.jsonl` the state counts.
    pub file_bytes: u64,
    pub check: DraftCheck,
}

impl DraftState {
    pub fn new(actor: Option<String>, turn_bytes: Option<u64>, bytes: u64) -> Self {
        let now = Stamp::now().z();
        Self {
            neosian_format: 1,
            actor,
            turn_bytes,
            opened_at: now.clone(),
            touched_at: now,
            bytes,
            file_bytes: 0,
            check: DraftCheck::default(),
        }
    }
}

fn unreadable(conversation_id: &str, what: &str) -> Error {
    Refused::format_unsupported(conversation_id, &format!("{what} is unreadable")).into()
}

/// The open draft's state, its records file cut back to what it counts.
pub fn load(paths: &Paths, conversation_id: &str) -> Result<Option<DraftState>, Error> {
    let text = match fs::read_to_string(&paths.state) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let state: DraftState = serde_json::from_str(&text)
        .ok()
        .filter(|state: &DraftState| state.neosian_format == 1)
        .ok_or_else(|| unreadable(conversation_id, "a draft's state"))?;
    let length = fs::metadata(&paths.records).map_or(0, |meta| meta.len());
    if length < state.file_bytes {
        return Err(unreadable(conversation_id, "a draft's records file"));
    }
    if length > state.file_bytes {
        fs::OpenOptions::new()
            .write(true)
            .open(&paths.records)?
            .set_len(state.file_bytes)?;
    }
    Ok(Some(state))
}

pub fn save(paths: &Paths, state: &DraftState) -> Result<(), Error> {
    let text = serde_json::to_string(state).map_err(io::Error::other)?;
    atomic_write(&paths.state, &text, true)?;
    Ok(())
}

/// The header a sealed draft made, if it was sealed.
pub fn sealed(paths: &Paths, conversation_id: &str) -> Result<Option<RecordHeader>, Error> {
    match fs::read_to_string(&paths.sealed) {
        Ok(text) => match parse_row(line_text(&text), 1, conversation_id, true)? {
            Row::Records(header) => Ok(Some(header)),
            Row::Whole(_) => Err(unreadable(conversation_id, "a sealed draft's header")),
        },
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

/// One record's line: `neosian_format`, the write keys, its digest.
pub fn line(record: &TurnRecord) -> String {
    let mut map = Map::new();
    map.insert("neosian_format".into(), json!(1));
    if let Value::Object(fields) = record.to_json(None) {
        map.extend(fields);
    }
    map.insert("sha256".into(), json!(hex(&record.digest(None))));
    format!("{}\n", dumps(&Value::Object(map), false))
}

pub fn hex(digest: &[u8; 32]) -> String {
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn parse_line(
    text: &str,
    number: usize,
    conversation_id: &str,
) -> Result<(TurnRecord, String), Error> {
    let bad = || unreadable(conversation_id, &format!("records line {number}"));
    let Ok(Value::Object(mut map)) = serde_json::from_str::<Value>(text) else {
        return Err(bad());
    };
    let format = map.remove("neosian_format");
    let digest = map.remove("sha256");
    let (Some(1), Some(Value::String(digest))) = (format.as_ref().and_then(Value::as_u64), digest)
    else {
        return Err(bad());
    };
    let (_, record) = TurnRecord::from_json(&Value::Object(map)).map_err(|_| bad())?;
    if hex(&record.digest(None)) != digest {
        return Err(bad());
    }
    Ok((record, digest))
}

/// Every record of a records file, in order, each line checked by its digest.
pub fn read_records(path: &Path, conversation_id: &str) -> Result<Vec<TurnRecord>, Error> {
    let reader = BufReader::new(fs::File::open(path)?);
    let mut records = Vec::new();
    for (index, text) in reader.lines().enumerate() {
        records.push(parse_line(&text?, index + 1, conversation_id)?.0);
    }
    Ok(records)
}

/// The digests of records `from..from + count` of a records file, read a
/// line at a time.
pub fn digests(
    path: &Path,
    from: u64,
    count: usize,
    conversation_id: &str,
) -> Result<Vec<String>, Error> {
    let reader = BufReader::new(fs::File::open(path)?);
    let mut found = Vec::with_capacity(count);
    for (index, text) in reader.lines().enumerate().skip(from as usize).take(count) {
        found.push(parse_line(&text?, index + 1, conversation_id)?.1);
    }
    Ok(found)
}

/// The draft states in `drafts`, by draft id.
fn states(drafts: &Path) -> Result<Vec<(String, PathBuf)>, Error> {
    let entries = match fs::read_dir(drafts) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error.into()),
    };
    let mut found = Vec::new();
    for entry in entries {
        let path = entry?.path();
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default();
        if let Some(draft) = name.strip_suffix(".state") {
            found.push((draft.to_string(), path.clone()));
        }
    }
    Ok(found)
}

/// Discard the drafts untouched for more than `seconds`; the size-rule
/// bytes they held.
pub fn discard_stale(drafts: &Path, seconds: u64, conversation_id: &str) -> Result<u64, Error> {
    let now = Stamp::now().instant();
    let mut freed = 0;
    for (draft, _) in states(drafts)? {
        let paths = Paths::new(drafts, &draft);
        let Some(state) = load(&paths, conversation_id)? else {
            continue;
        };
        let touched = Stamp::parse(&state.touched_at)
            .map_err(|_| unreadable(conversation_id, "a draft's state"))?;
        if now - touched.instant() > seconds as i64 * 1_000_000 {
            remove(&paths)?;
            freed += state.bytes;
        }
    }
    Ok(freed)
}

pub fn open_count(drafts: &Path) -> Result<usize, Error> {
    Ok(states(drafts)?.len())
}

/// Remove an open draft's files.
pub fn remove(paths: &Paths) -> Result<(), Error> {
    for path in [&paths.records, &paths.state] {
        match fs::remove_file(path) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => return Err(error.into()),
            _ => {}
        }
    }
    Ok(())
}

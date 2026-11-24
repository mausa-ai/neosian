//! A FileStore draft's files (DESIGN §40.54 item 4.2): its records, one
//! compact line each with its digest, in `<draft>.jsonl`; its state in
//! `<draft>.state`, written after the lines it counts, so a request cut off
//! between the two leaves lines past `file_bytes` that the next request
//! drops; and `<draft>.sealed`, the header its seal made. Every path is
//! held to the store's containment check (the review of 2481787, finding
//! 7); the stale cleanup is in `stale.rs`.

use std::fs;
use std::io::{self, BufRead, BufReader};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use super::super::fileio::{atomic_write, within};
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
    /// The files owned by a draft in the checked parent directory. Unlinking
    /// the entries themselves never follows a link at the leaf.
    pub(super) fn entries(drafts: &Path, draft: &str) -> Self {
        Self {
            records: drafts.join(format!("{draft}.jsonl")),
            state: drafts.join(format!("{draft}.state")),
            sealed: drafts.join(format!("{draft}.sealed")),
        }
    }

    /// The files of `draft` in a drafts directory already held inside the
    /// root; each refused when a symlink would carry it outside.
    pub fn new(
        root: &Path,
        drafts: &Path,
        draft: &str,
        conversation_id: &str,
    ) -> Result<Self, Error> {
        let paths = Self::entries(drafts, draft);
        for path in [&paths.records, &paths.state, &paths.sealed] {
            if !within(root, path)? {
                return Err(Refused::id_invalid(conversation_id, "escapes the store root").into());
            }
        }
        Ok(paths)
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

/// A draft's state as stored: None when absent, Err(()) when unreadable.
pub(super) fn read_state(paths: &Paths) -> Option<Result<DraftState, ()>> {
    let text = match fs::read_to_string(&paths.state) {
        Ok(text) => text,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return None,
        Err(_) => return Some(Err(())),
    };
    let state = serde_json::from_str::<DraftState>(&text)
        .ok()
        .filter(|state| state.neosian_format == 1 && Stamp::parse(&state.touched_at).is_ok());
    Some(state.ok_or(()))
}

/// The open draft's state, its records file cut back to what it counts.
pub fn load(paths: &Paths, conversation_id: &str) -> Result<Option<DraftState>, Error> {
    let Some(state) = read_state(paths) else {
        return Ok(None);
    };
    let state = state.map_err(|()| unreadable(conversation_id, "a draft's state"))?;
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
    if number > 0 && record.seq != (number - 1) as u64 {
        return Err(Refused::format_unsupported(
            conversation_id,
            &format!("records line {number} holds record {}", record.seq),
        )
        .into());
    }
    if hex(&record.digest(None)) != digest {
        return Err(bad());
    }
    Ok((record, digest))
}

/// The refusal of a records file that is not where its header says.
pub fn missing(conversation_id: &str, turn: u64) -> Error {
    Refused::format_unsupported(
        conversation_id,
        &format!("turn {turn}: its records file is missing"),
    )
    .into()
}

/// Open a records file, a missing one refused by name (finding 8).
pub fn open_records(path: &Path, conversation_id: &str, turn: u64) -> Result<fs::File, Error> {
    fs::File::open(path).map_err(|error| match error.kind() {
        io::ErrorKind::NotFound => missing(conversation_id, turn),
        _ => error.into(),
    })
}

/// Every record of a sealed turn's records file, in order, each line
/// checked by its digest.
pub fn read_records(
    path: &Path,
    conversation_id: &str,
    turn: u64,
) -> Result<Vec<TurnRecord>, Error> {
    let reader = BufReader::new(open_records(path, conversation_id, turn)?);
    let mut records = Vec::new();
    for (index, text) in reader.lines().enumerate() {
        records.push(parse_line(&text?, index + 1, conversation_id)?.0);
    }
    Ok(records)
}

/// The digests of records `from..from + count` of a records file, read a
/// line at a time.
pub fn digests(
    file: fs::File,
    from: u64,
    count: usize,
    conversation_id: &str,
) -> Result<Vec<String>, Error> {
    let reader = BufReader::new(file);
    let mut found = Vec::with_capacity(count);
    for (index, text) in reader.lines().enumerate().skip(from as usize).take(count) {
        found.push(parse_line(&text?, index + 1, conversation_id)?.1);
    }
    if found.len() != count {
        return Err(unreadable(
            conversation_id,
            "the requested range of stored records",
        ));
    }
    Ok(found)
}

/// Remove an open draft's files.
pub fn remove(paths: &Paths) -> Result<(), Error> {
    for path in [&paths.records, &paths.state] {
        crate::store::fileio::remove_file(path)?;
    }
    Ok(())
}

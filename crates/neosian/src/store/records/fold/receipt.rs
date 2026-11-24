//! What a fold keeps of a sealed draft's records (DESIGN §40.54 item 21,
//! T1b2c4, the ruling of 2026-11-24): `.drafts/folded/<turn>`. Its first
//! line is a small JSON object naming the draft, the turn and the number
//! of records; then one line per record, in `seq` order: the SHA-256 the
//! seal accepted it under, 64 hex digits. No text is kept. The file's
//! length is exactly what its first line says, so a digest is found by its
//! place and a repeat reads only the digests it names, and a receipt cut
//! short or changed in length is refused whole. A seal stays repeatable
//! after its records file is gone, whatever its parts' boundaries were.
//! The receipt outlives a later migration of the same turn, whose new
//! records are split by the default rule, and goes with the turn's erasure.

use std::fs;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use super::super::{DRAFTS, drafts};
use crate::conversation::rows_header::RecordHeader;
use crate::conversation::turn_records::check_draft_id;
use crate::conversation::{Error, Refused};
use crate::store::FileStore;

const FOLDED: &str = "folded";
/// A first line is far shorter: three small keys and a draft id of 64.
const HEAD_BYTES: u64 = 256;
/// One digest and its line feed.
const LINE_BYTES: u64 = 65;

/// A folded receipt's first line.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Owner {
    neosian_format: u64,
    pub draft: String,
    turn: u64,
    records: u64,
}

fn refused(id: &str, turn: u64, why: &str) -> Error {
    Refused::format_unsupported(id, &format!("turn {turn}: its folded receipt {why}")).into()
}

/// Write a sealed turn's folded receipt from its records file, a record
/// at a time: the first line, then each record's digest as stored.
pub(super) fn write(
    out: &mut impl Write,
    records: fs::File,
    header: &RecordHeader,
    id: &str,
) -> Result<(), Error> {
    let owner = Owner {
        neosian_format: 1,
        draft: header.draft.clone(),
        turn: header.turn,
        records: header.records,
    };
    serde_json::to_writer(&mut *out, &owner).map_err(io::Error::other)?;
    out.write_all(b"\n")?;
    let mut count = 0;
    for (index, text) in BufReader::new(records).lines().enumerate() {
        writeln!(out, "{}", drafts::parse_line(&text?, index + 1, id)?.1)?;
        count += 1;
    }
    if count != header.records {
        let why = format!("turn {}: its records do not match its header", header.turn);
        return Err(Refused::format_unsupported(id, &why).into());
    }
    Ok(())
}

impl FileStore {
    /// The directory of a conversation's folded receipts, inside the root.
    pub(in crate::store) fn folded_dir(&self, id: &str) -> Result<PathBuf, Error> {
        self.conversation_file(id, &format!("{DRAFTS}/{FOLDED}"))
    }

    /// A turn's folded receipt's path, inside the root.
    pub(in crate::store) fn folded_file(&self, id: &str, turn: u64) -> Result<PathBuf, Error> {
        self.conversation_file(id, &format!("{DRAFTS}/{FOLDED}/{turn}"))
    }

    /// A turn's folded receipt opened after its first line, with that line
    /// and its length: None when the turn has none, refused by name when it
    /// is not a receipt of this turn of exactly the length it states. Only
    /// the first line is read.
    fn folded_open(&self, id: &str, turn: u64) -> Result<Option<(Owner, u64, fs::File)>, Error> {
        let file = match fs::File::open(self.folded_file(id, turn)?) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let length = file.metadata()?.len();
        let mut reader = BufReader::new(file).take(HEAD_BYTES);
        let mut line = Vec::new();
        reader.read_until(b'\n', &mut line)?;
        let head = line.len() as u64;
        let owner = serde_json::from_slice::<Owner>(&line).ok().filter(|owner| {
            let whole = owner.records.checked_mul(LINE_BYTES);
            line.ends_with(b"\n")
                && owner.neosian_format == 1
                && owner.turn == turn
                && check_draft_id(&owner.draft).is_ok()
                && whole.and_then(|bytes| bytes.checked_add(head)) == Some(length)
        });
        match owner {
            Some(owner) => Ok(Some((owner, head, reader.into_inner().into_inner()))),
            None => Err(refused(id, turn, "is unreadable")),
        }
    }

    /// The draft whose seal a turn's folded receipt keeps, if it has one.
    pub(in crate::store) fn folded_owner(
        &self,
        id: &str,
        turn: u64,
    ) -> Result<Option<Owner>, Error> {
        Ok(self.folded_open(id, turn)?.map(|(owner, ..)| owner))
    }

    /// The digests of records `from..from + count` of a sealed draft's
    /// turn, as its seal accepted them: from the folded receipt when the
    /// turn was folded under this draft, only that range read; else from
    /// its records file. A range either does not hold whole is refused.
    pub(in crate::store) fn sealed_digests(
        &self,
        id: &str,
        sealed: &RecordHeader,
        from: u64,
        count: usize,
    ) -> Result<Vec<String>, Error> {
        let folded = self.folded_open(id, sealed.turn)?;
        let Some((owner, head, mut file)) =
            folded.filter(|(owner, ..)| owner.draft == sealed.draft)
        else {
            let path = self.record_file(id, sealed.turn)?;
            let file = drafts::open_records(&path, id, sealed.turn)?;
            return drafts::digests(file, from, count, id);
        };
        let short = || refused(id, sealed.turn, "is short");
        let end = from.checked_add(count as u64).ok_or_else(short)?;
        if end > owner.records {
            return Err(short());
        }
        file.seek(SeekFrom::Start(head + from * LINE_BYTES))?;
        let mut reader = BufReader::new(file);
        let mut found = Vec::with_capacity(count);
        let mut line = [0_u8; LINE_BYTES as usize];
        for _ in 0..count {
            reader.read_exact(&mut line)?;
            let (digest, end) = line.split_at(64);
            if end != b"\n" || !digest.iter().all(u8::is_ascii_hexdigit) {
                return Err(refused(id, sealed.turn, "is unreadable"));
            }
            found.push(String::from_utf8_lossy(digest).into_owned());
        }
        Ok(found)
    }
}

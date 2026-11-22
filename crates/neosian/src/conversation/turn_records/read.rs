//! A record read as every store takes it (§40.54 item 10): the selection,
//! its continuation, the items a page holds, and the head and tail of a
//! long piece. The route spools a page's headers before its records (T1d);
//! a store hands it the items in turn order, each header first.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::split::width;
use super::{Header, Kind, TurnRecord};
use crate::pyfmt::dumps;
use crate::store::cursor;

/// A page's bytes when the request names none.
pub const PAGE_BYTES: u64 = 983_040;
/// The most a head or a tail may encode to.
pub const HEAD_BYTES: usize = 32_768;
const METHOD: &str = "records-v1/read";

/// One `read_records` request, its `page_bytes` already capped by the
/// server's `NEOSIAN_MAX_PAGE_BYTES`.
#[derive(Clone, Debug)]
pub struct RecordRead {
    pub turn: Option<u64>,
    pub after: u64,
    pub through: Option<u64>,
    pub seq: Option<u64>,
    pub piece: bool,
    /// None selects all eight kinds; an empty list answers headers only.
    pub kinds: Option<Vec<Kind>>,
    pub heads: Vec<(Kind, usize)>,
    pub page_bytes: u64,
    pub continuation: Option<String>,
}

impl Default for RecordRead {
    fn default() -> Self {
        Self {
            turn: None,
            after: 0,
            through: None,
            seq: None,
            piece: false,
            kinds: None,
            heads: Vec::new(),
            page_bytes: PAGE_BYTES,
            continuation: None,
        }
    }
}

impl RecordRead {
    /// The rules between the fields (item 10's table).
    pub fn check(&self) -> Result<(), String> {
        if self.turn.is_some() && (self.after > 0 || self.through.is_some()) {
            return Err("turn excludes after and through".into());
        }
        if self.turn == Some(0) || self.through == Some(0) {
            return Err("turn and through are at least 1".into());
        }
        if self.seq.is_some() && self.turn.is_none() {
            return Err("seq requires turn".into());
        }
        if self.piece && self.seq.is_none() {
            return Err("piece requires turn and seq".into());
        }
        if let Some((kind, _)) = self
            .heads
            .iter()
            .find(|(_, n)| !(1..=HEAD_BYTES).contains(n))
        {
            return Err(format!(
                "heads {:?} must be 1 to {HEAD_BYTES}",
                kind.as_str()
            ));
        }
        if self.page_bytes == 0 {
            return Err("page_bytes must be >= 1".into());
        }
        Ok(())
    }

    pub fn selects(&self, kind: Kind) -> bool {
        self.kinds
            .as_ref()
            .is_none_or(|kinds| kinds.contains(&kind))
    }

    pub fn head(&self, kind: Kind) -> Option<usize> {
        self.heads
            .iter()
            .find(|(named, _)| *named == kind)
            .map(|(_, n)| *n)
    }

    /// The first turn the read reaches and the record it starts at.
    pub fn start(&self) -> (u64, u64) {
        match self.turn {
            Some(turn) => (turn, self.seq.unwrap_or(0)),
            None => (self.after + 1, 0),
        }
    }

    /// The last turn the read may reach.
    pub fn last(&self) -> u64 {
        self.turn.or(self.through).unwrap_or(u64::MAX)
    }

    /// The selection a continuation binds: everything but the page size.
    pub fn fingerprint(&self, conversation_id: &str) -> String {
        let mut kinds: Option<Vec<&str>> = self
            .kinds
            .as_ref()
            .map(|k| k.iter().map(|k| k.as_str()).collect());
        if let Some(kinds) = kinds.as_mut() {
            kinds.sort_unstable();
            kinds.dedup();
        }
        let mut heads: Vec<(&str, usize)> =
            self.heads.iter().map(|(k, n)| (k.as_str(), *n)).collect();
        heads.sort_unstable();
        let selection = json!([
            METHOD,
            conversation_id,
            self.turn,
            self.after,
            self.through,
            self.seq,
            self.piece,
            kinds,
            heads
        ]);
        hex(&Sha256::digest(dumps(&selection, true).as_bytes()))
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Where the next page starts: a turn, a record in it (0 is the turn's
/// header), and where a FileStore found them (0 when not known).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ReadPosition {
    pub turn: u64,
    pub seq: u64,
    pub log_offset: u64,
    pub log_line: u64,
    pub record_offset: u64,
}

impl ReadPosition {
    pub fn encode(&self, fingerprint: &str) -> String {
        let parts = [
            self.turn,
            self.seq,
            self.log_offset,
            self.log_line,
            self.record_offset,
        ]
        .map(|n| json!(n));
        let mut all = vec![json!(fingerprint)];
        all.extend(parts);
        cursor::encode(METHOD, &all)
    }

    /// A continuation's position, refused when another selection minted it.
    pub fn decode(token: &str, fingerprint: &str) -> Result<Self, String> {
        let kinds = [
            cursor::Kind::Str,
            cursor::Kind::Int,
            cursor::Kind::Int,
            cursor::Kind::Int,
            cursor::Kind::Int,
            cursor::Kind::Int,
        ];
        let parts = cursor::decode(METHOD, token, &kinds)?;
        if parts[0] != fingerprint {
            return Err("page continuation belongs to a different request".into());
        }
        let number = |at: usize| {
            parts[at]
                .as_u64()
                .ok_or_else(|| format!("malformed {METHOD} cursor"))
        };
        Ok(Self {
            turn: number(1)?,
            seq: number(2)?,
            log_offset: number(3)?,
            log_line: number(4)?,
            record_offset: number(5)?,
        })
    }
}

/// One item of a page: a turn's header, or a record as answered.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadItem {
    Header(Header),
    Record {
        turn: u64,
        record: TurnRecord,
        tail: Option<String>,
        truncated: bool,
    },
}

impl ReadItem {
    pub fn to_json(&self) -> Value {
        match self {
            Self::Header(header) => header.to_json(),
            Self::Record {
                turn,
                record,
                tail,
                truncated,
            } => {
                let mut value = record.to_json(Some(*turn));
                let map = value.as_object_mut().expect("a record is an object");
                map.insert("tail".into(), json!(tail));
                map.insert("truncated".into(), json!(truncated));
                value
            }
        }
    }

    /// The item's compact encoding and its separator: what it costs a page.
    pub fn bytes(&self) -> u64 {
        dumps(&self.to_json(), false).len() as u64 + 1
    }
}

/// The longest prefix of `text` that encodes to at most `bytes`, quotes included.
pub fn head(text: &str, bytes: usize) -> &str {
    let mut used = 2;
    for (at, ch) in text.char_indices() {
        used += width(ch);
        if used > bytes {
            return &text[..at];
        }
    }
    if used > bytes { "" } else { text }
}

/// The longest suffix of `text` that encodes to at most `bytes`, quotes included.
pub fn tail(text: &str, bytes: usize) -> &str {
    let mut used = 2;
    for (at, ch) in text.char_indices().rev() {
        used += width(ch);
        if used > bytes {
            return &text[at + ch.len_utf8()..];
        }
    }
    if used > bytes { "" } else { text }
}

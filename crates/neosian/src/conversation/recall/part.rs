//! One part of one piece (DESIGN §40.54 item 19; the T1f plan's rulings):
//! `call` opens the last result of that tool call, `seq` the piece holding
//! that record, from its first chunk. Part `k`, counted from 0, is the
//! piece's stored chunk `k`, so its text is at most 65,536 bytes as the
//! wire encodes it, with bounded framing beside it; a marker says when
//! more follows and which part continues. The piece is found from heads
//! (`call`) or from the record itself (`seq`), and part `k` is read from
//! its own record with one record of lookahead, never from the piece's
//! start. Only the five visible kinds are ever asked for.

use super::origin::{Item, Origin};
use super::pieces::{Gather, Piece};
use super::view::shown;
use super::{Corrective, Outcome, Selector, grouped, hint, marker, part_of};
use crate::conversation::turn_records::{Kind, RECORD_BYTES, RecordRead};
use crate::repr::repr;
use crate::state::records::Fault;

/// The kinds a door serves (§40.54 item 3.2's searchable five).
const VISIBLE: [Kind; 5] = [
    Kind::Prompt,
    Kind::Reply,
    Kind::System,
    Kind::ToolResult,
    Kind::ToolCall,
];
/// A part read's page: its header, the chunk and the next one.
const PART_PAGE: u64 = 2 * RECORD_BYTES as u64 + 4_096;
/// The head and tail of a call's arguments beside part 0.
const ARGS_HEAD: usize = 512;

pub fn render(
    origin: &Origin,
    turn: u64,
    selector: &Selector,
    part: u64,
) -> Result<Outcome, Fault> {
    match selector {
        Selector::Call(id) => call(origin, turn, id, part),
        Selector::Seq(seq) => seq_part(origin, turn, *seq, part),
    }
}

fn refused(error: String, key: Option<&str>) -> Outcome {
    Outcome::Refused(Corrective::new(error, key.map(hint)))
}

/// Chunk `part` of `piece` and whether another follows, read from its own
/// record; None when the piece has no such part.
fn chunk(
    origin: &Origin,
    turn: u64,
    piece: &Piece,
    part: u64,
) -> Result<Option<(String, bool)>, Fault> {
    let Some(seq) = piece
        .seq
        .checked_add(part)
        .filter(|_| part < piece.size.max(1))
    else {
        return Ok(None);
    };
    let read = RecordRead {
        turn: Some(turn),
        seq: Some(seq),
        kinds: Some(vec![piece.kind]),
        page_bytes: PART_PAGE,
        ..RecordRead::default()
    };
    let (mut found, mut more) = (None, false);
    origin.read(&read, &mut |item| {
        let Item::Record(answered) = item else {
            return Ok(true);
        };
        let record = &answered.record;
        if found.is_some() {
            more = record.seq == seq + 1 && record.chunk == part + 1 && piece.holds(record);
            return Ok(false);
        }
        if record.seq != seq || record.chunk != part || !piece.holds(record) || answered.truncated {
            return Ok(false);
        }
        let last = part == 0 && record.text.len() as u64 == piece.size;
        found = Some(answered.record.text);
        Ok(!last)
    })?;
    Ok(found.map(|text| (text, more)))
}

/// The marker after part `part` of `piece`, when it says anything.
fn ending(piece: &Piece, part: u64, more: bool) -> Option<String> {
    let noun = super::piece_noun(piece.kind);
    let (number, next, size) = (
        part.to_string(),
        (part + 1).to_string(),
        grouped(piece.size),
    );
    let vars = [
        ("part", number.as_str()),
        ("piece", noun.as_str()),
        ("bytes", size.as_str()),
        ("next", next.as_str()),
    ];
    match (more, part) {
        (true, _) => Some(marker("recall_turn_more", &vars)),
        (false, 0) => None,
        (false, _) => Some(marker("recall_turn_last", &vars)),
    }
}

fn past(turn: u64, piece: &Piece, part: u64) -> Outcome {
    let noun = super::piece_noun(piece.kind);
    let what = format!(
        "Part {part} is past the end of {noun} of {} bytes in turn {turn}",
        grouped(piece.size)
    );
    refused(what, Some("recall_turn_parts_hint"))
}

fn call(origin: &Origin, turn: u64, id: &str, part: u64) -> Result<Outcome, Fault> {
    let read = RecordRead {
        turn: Some(turn),
        kinds: Some(vec![Kind::ToolCall, Kind::ToolResult]),
        heads: vec![(Kind::ToolCall, ARGS_HEAD), (Kind::ToolResult, 1)],
        ..RecordRead::default()
    };
    let (mut header, mut made, mut result, mut ids) = (None, None, None, Vec::new());
    let mut gather = Gather::default();
    let mut take = |piece: Piece| match piece.kind {
        Kind::ToolCall => {
            let named = piece.call_id.clone().unwrap_or_default();
            if named == id && made.is_none() {
                made = Some(piece);
            }
            ids.push(named);
        }
        _ if piece.call_id.as_deref() == Some(id) => result = Some(piece),
        _ => {}
    };
    origin.read(&read, &mut |item| {
        match item {
            Item::Header(found) if found.turn == turn => header = Some(found),
            Item::Header(_) => {}
            Item::Record(answered) => {
                if let Some(piece) = gather.push(answered)? {
                    take(piece);
                }
            }
        }
        Ok(true)
    })?;
    if let Some(piece) = gather.finish()? {
        take(piece);
    }
    if header.is_none() {
        return Ok(Outcome::Missing);
    }
    let Some(made) = made else {
        let listed = if ids.is_empty() {
            "none".to_string()
        } else {
            ids.join(", ")
        };
        let error = format!("Turn {turn} made no call {}", repr(id));
        return Ok(Outcome::Refused(Corrective::new(
            error,
            Some(format!("Its calls: {listed}.")),
        )));
    };
    let Some(result) = result else {
        let error = format!("Turn {turn}'s call {} has no result", repr(id));
        return Ok(refused(error, Some("recall_turn_no_result_hint")));
    };
    let Some((text, more)) = chunk(origin, turn, &result, part)? else {
        return Ok(past(turn, &result, part));
    };
    let name = made.name.clone().unwrap_or_default();
    let turn_text = turn.to_string();
    let mut lines = vec![marker(
        "recall_turn_call",
        &[("turn", &turn_text), ("part", &part_of(part)), ("call", id)],
    )];
    if part == 0 {
        let args = shown(&made, &format!("seq={}", made.seq));
        lines.push(format!("AGENT calls {name}({args})"));
    }
    lines.push(format!("TOOL {name} → {text}"));
    lines.extend(ending(&result, part, more));
    Ok(Outcome::Shown(lines.join("\n")))
}

fn seq_part(origin: &Origin, turn: u64, seq: u64, part: u64) -> Result<Outcome, Fault> {
    let read = RecordRead {
        turn: Some(turn),
        seq: Some(seq),
        kinds: Some(VISIBLE.to_vec()),
        page_bytes: PART_PAGE,
        ..RecordRead::default()
    };
    let (mut header, mut first, mut next) = (None, None, None);
    origin.read(&read, &mut |item| {
        match item {
            Item::Header(found) if found.turn == turn => header = Some(found),
            Item::Header(_) => {}
            Item::Record(answered) if first.is_none() => first = Some(answered),
            Item::Record(answered) => {
                next = Some(answered.record);
                return Ok(false);
            }
        }
        Ok(true)
    })?;
    let Some(header) = header else {
        return Ok(Outcome::Missing);
    };
    let Some(found) = first.filter(|found| found.record.seq == seq) else {
        if seq < header.records {
            let error = format!("Record {seq} of turn {turn} is not one recall shows");
            return Ok(refused(error, Some("recall_turn_unshown_hint")));
        }
        let error = format!("Turn {turn} has no record {seq}");
        let hint = match header.records {
            0 => "It holds no records.".to_string(),
            n => format!("Its records are 0 to {}.", n - 1),
        };
        return Ok(Outcome::Refused(Corrective::new(error, Some(hint))));
    };
    let record = found.record;
    let piece = Piece::holding(&record)?;
    let answer = if part == record.chunk && !found.truncated {
        let more = next.is_some_and(|n| n.seq == seq + 1 && n.chunk == part + 1 && piece.holds(&n));
        Some((record.text, more))
    } else {
        chunk(origin, turn, &piece, part)?
    };
    let Some((text, more)) = answer else {
        return Ok(past(turn, &piece, part));
    };
    let label = match piece.kind {
        Kind::Prompt => "USER: ".to_string(),
        Kind::Reply => "AGENT: ".to_string(),
        Kind::System => "SYSTEM: ".to_string(),
        Kind::ToolCall => format!("AGENT calls {}: ", piece.name.as_deref().unwrap_or("")),
        _ => format!(
            "TOOL {} → ",
            tool_name(origin, turn, piece.call_id.as_deref())?
        ),
    };
    let (turn_text, start_text) = (turn.to_string(), piece.seq.to_string());
    let mut lines = vec![marker(
        "recall_turn_seq",
        &[
            ("turn", &turn_text),
            ("seq", &start_text),
            ("part", &part_of(part)),
        ],
    )];
    lines.push(format!("{label}{text}"));
    lines.extend(ending(&piece, part, more));
    Ok(Outcome::Shown(lines.join("\n")))
}

/// The tool a result's call names, the last call with its id; `tool`
/// when none does. Calls are read as one-byte heads.
fn tool_name(origin: &Origin, turn: u64, id: Option<&str>) -> Result<String, Fault> {
    let mut name = None;
    if let Some(id) = id {
        let read = RecordRead {
            turn: Some(turn),
            kinds: Some(vec![Kind::ToolCall]),
            heads: vec![(Kind::ToolCall, 1)],
            ..RecordRead::default()
        };
        origin.read(&read, &mut |item| {
            if let Item::Record(answered) = item
                && answered.record.chunk == 0
                && answered.record.call_id.as_deref() == Some(id)
            {
                name = answered.record.name;
            }
            Ok(true)
        })?;
    }
    Ok(name.unwrap_or_else(|| "tool".to_string()))
}

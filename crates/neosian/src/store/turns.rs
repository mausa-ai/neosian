//! Turn persistence over the root (`conversation/file_turns.py`, DESIGN
//! §9.8): `root/conversations/<id>/turns.jsonl` and `projections.jsonl`,
//! each read whole under the root-wide lock the memory side holds too, a
//! malformed or newer row refused and never skipped; the appends the
//! record verb and the wire land, unsynced as the Python side writes them.

use std::fs;
use std::path::{Path, PathBuf};

use super::FileStore;
use super::fileio::{append_line, private_mkdir};
use super::time::Stamp;
use crate::conversation::ids::parse_conversation_id;
use crate::conversation::message::Message;
use crate::conversation::page::{Mode, Resume, Visit, owed};
use crate::conversation::rank::{Key, Order, Ranked, Ranking, Request};
use crate::conversation::rows::{
    CONVERSATIONS, PROJECTIONS, TURNS, parse_projection, parse_turn, render_projection, render_turn,
};
use crate::conversation::search::{held_ids, line_text, read_at, scan_log, turn_text};
use crate::conversation::{Error, Projection, Refused, Turn};

/// `_check_cursor`: the ABC's own refusals of a negative start or limit.
fn check_cursor(after: i64, limit: Option<i64>) -> Result<(), Error> {
    if after < 0 {
        return Err(Error::Value(format!("after must be >= 0, got {after}")));
    }
    if let Some(limit) = limit.filter(|limit| *limit < 0) {
        return Err(Error::Value(format!("limit must be >= 0, got {limit}")));
    }
    Ok(())
}

/// The rows past `after`, at most `limit`.
fn window<T>(rows: Vec<T>, after: i64, limit: Option<i64>, turn_of: impl Fn(&T) -> u64) -> Vec<T> {
    let mut kept: Vec<T> = rows
        .into_iter()
        .filter(|row| turn_of(row) > after as u64)
        .collect();
    if let Some(limit) = limit {
        kept.truncate(limit as usize);
    }
    kept
}

impl FileStore {
    /// The turns past `after`, oldest first, at most `limit`; none for a
    /// conversation never written.
    pub fn read_turns(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Turn>, Error> {
        let mut turns = Vec::new();
        self.read_turns_each(conversation_id, after, limit, &mut |turn| {
            turns.push(turn);
            Ok(true)
        })?;
        Ok(turns)
    }

    /// `read_turns` one turn at a time (DESIGN §40.46): the log streams a
    /// line at a time under the lock and every row is still decoded, so a
    /// malformed row anywhere refuses as a whole read would, even after
    /// `visit` stopped taking turns.
    pub fn read_turns_each(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        check_cursor(after, limit)?;
        if !file.is_file() {
            return Ok(());
        }
        let mut reader = std::io::BufReader::new(fs::File::open(&file)?);
        let (mut buffer, mut number, mut taken) = (String::new(), 0_usize, 0_i64);
        let mut open = limit != Some(0);
        loop {
            buffer.clear();
            if std::io::BufRead::read_line(&mut reader, &mut buffer)? == 0 {
                return Ok(());
            }
            number += 1;
            let turn = parse_turn(line_text(&buffer), number, conversation_id)?;
            if open && turn.turn > after as u64 {
                taken += 1;
                open = visit(turn)? && limit.is_none_or(|limit| taken < limit);
            }
        }
    }

    /// One size page of `read_turns` (DESIGN §40.46): from `hint`, the byte
    /// offset and line number of the row after `after` when an earlier page
    /// found it, else from the log's start, decoding each row it passes; it
    /// stops when `visit` declines a turn and answers where that turn's row
    /// starts. A hint is trusted only at a line start whose row holds turn
    /// `after + 1`; otherwise the page reads from the start. Across the pages
    /// of one read every row is decoded once, so a malformed row still
    /// refuses the read, on the page that reaches it.
    pub fn read_turns_page(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
        hint: Option<(u64, u64)>,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<Option<(u64, u64)>, Error> {
        use std::io::{BufRead, Read, Seek, SeekFrom};
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        check_cursor(after, limit)?;
        if !file.is_file() || limit == Some(0) {
            return Ok(None);
        }
        let mut reader = std::io::BufReader::new(fs::File::open(&file)?);
        let (mut offset, mut number) = (0_u64, 0_u64);
        let mut buffer = String::new();
        if let Some((at, line)) = hint.filter(|(at, line)| *at > 0 && *line > 1) {
            reader.seek(SeekFrom::Start(at - 1))?;
            let mut before = [0_u8];
            let boundary = reader.read(&mut before)? == 1 && before[0] == b'\n';
            let next = boundary && reader.read_line(&mut buffer)? > 0 && {
                let row = parse_turn(line_text(&buffer), line as usize, conversation_id);
                row.is_ok_and(|turn| turn.turn == after as u64 + 1)
            };
            (offset, number) = if next { (at, line - 1) } else { (0, 0) };
            reader.seek(SeekFrom::Start(offset))?;
        }
        let mut taken = 0_i64;
        loop {
            buffer.clear();
            let read = reader.read_line(&mut buffer)? as u64;
            if read == 0 {
                return Ok(None);
            }
            let start = offset;
            (offset, number) = (offset + read, number + 1);
            let turn = parse_turn(line_text(&buffer), number as usize, conversation_id)?;
            if turn.turn > after as u64 {
                if !visit(turn)? {
                    return Ok(Some((start, number)));
                }
                taken += 1;
                if limit.is_some_and(|limit| taken >= limit) {
                    return Ok(None);
                }
            }
        }
    }

    /// The projection entries past `after` in store order (by turn, then
    /// span, insertion order breaking ties), at most `limit`.
    pub fn read_projections(
        &self,
        conversation_id: &str,
        after: i64,
        limit: Option<i64>,
    ) -> Result<Vec<Projection>, Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, PROJECTIONS)?;
        check_cursor(after, limit)?;
        let mut entries = rows(&file, |line, number| {
            parse_projection(line, number, conversation_id)
        })?;
        entries.sort_by_key(|entry| (entry.turn, entry.span));
        Ok(window(entries, after, limit, |entry| entry.turn))
    }

    /// The last turn's number, 0 for a conversation never written.
    pub fn last_turn_number(&self, conversation_id: &str) -> Result<u64, Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        last_number(&file, conversation_id)
    }

    /// One turn appended under the root lock: the number after the log's
    /// last row, the row written whole in one unsynced append.
    pub fn append_turn(
        &self,
        conversation_id: &str,
        messages: Vec<Message>,
        actor: Option<String>,
    ) -> Result<Turn, Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        if messages.is_empty() {
            return Err(Error::Value(
                "a turn must carry at least one message".to_string(),
            ));
        }
        let turn = Turn {
            conversation_id: conversation_id.to_string(),
            turn: last_number(&file, conversation_id)? + 1,
            messages,
            created_at: Stamp::now(),
            actor,
            redacted: false,
        };
        append_line(&file, &render_turn(&turn), false)?;
        Ok(turn)
    }

    /// Projection entries appended in one unsynced write; none is nothing.
    pub fn append_projections(
        &self,
        conversation_id: &str,
        entries: &[Projection],
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, PROJECTIONS)?;
        if entries.is_empty() {
            return Ok(());
        }
        private_mkdir(file.parent().unwrap_or(&self.root))?;
        let text: String = entries.iter().map(render_projection).collect();
        append_line(&file, &text, false)?;
        Ok(())
    }

    /// When a conversation began: its first row's stamp, that row alone
    /// parsed; None for one never written.
    pub fn first_turn_stamp(&self, conversation_id: &str) -> Result<Option<Stamp>, Error> {
        let _held = self.lock()?;
        let file = self.conversation_file(conversation_id, TURNS)?;
        if !file.is_file() {
            return Ok(None);
        }
        let text = fs::read_to_string(&file)?;
        match text.lines().next() {
            Some(line) => Ok(Some(parse_turn(line, 1, conversation_id)?.created_at)),
            None => Ok(None),
        }
    }

    /// The turns holding every term, newest first, at most `limit`;
    /// `ids` narrows to those conversations, and an empty list answers
    /// nothing.
    pub fn search_turns(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
    ) -> Result<Vec<Turn>, Error> {
        let mut found = Vec::new();
        self.search_each(terms, ids, limit, &mut |turn| {
            found.push(turn);
            Ok(true)
        })?;
        Ok(found)
    }

    /// `search_turns` one turn at a time, newest first (DESIGN §40.46).
    pub fn search_each(
        &self,
        terms: &[String],
        ids: Option<&[String]>,
        limit: usize,
        visit: &mut dyn FnMut(Turn) -> Result<bool, Error>,
    ) -> Result<(), Error> {
        let _held = self.lock()?;
        if let Some(ids) = ids {
            for conversation_id in ids {
                parse_conversation_id(conversation_id)?;
            }
            if ids.is_empty() {
                return Ok(());
            }
        }
        let listing = match ids {
            Some(ids) => Listing::Given(ids),
            None => Listing::All(held_ids(&self.root.join(CONVERSATIONS))?),
        };
        // Version 1 is newest without bounds: the same pipeline, at most
        // `limit` keys held while the logs stream (DESIGN §40.45).
        let request = Request {
            terms: terms.to_vec(),
            ids: None,
            limit,
            order: Order::Newest,
            since: None,
            before: None,
        };
        self.rank_logs(&request, listing, &Resume::Fresh, &mut |turn, _| {
            visit(turn)
        })?;
        Ok(())
    }

    /// History ranking version 1 under the store lock (DESIGN §40.43).
    pub fn rank_turns(&self, request: &Request) -> Result<Vec<Ranked>, Error> {
        let mut ranked = Vec::new();
        self.rank_each(
            request,
            &Resume::Fresh,
            Mode::Collected,
            &mut |turn, score| {
                ranked.push((turn, score));
                Ok(true)
            },
        )?;
        Ok(ranked)
    }

    /// Ranked or newest search handing each turn to `visit` in answer order,
    /// from `resume` (DESIGN §40.46); under relevance it answers the
    /// candidate statistics' digest. Files keep no response budget, so the
    /// read mode changes nothing here.
    pub fn rank_each(
        &self,
        request: &Request,
        resume: &Resume,
        _mode: Mode,
        visit: &mut Visit,
    ) -> Result<Option<String>, Error> {
        request.bounds().map_err(Error::Value)?;
        let _held = self.lock()?;
        let listing = match &request.ids {
            Some(ids) => {
                for conversation_id in ids {
                    parse_conversation_id(conversation_id)?;
                }
                Listing::Given(ids)
            }
            None => Listing::All(held_ids(&self.root.join(CONVERSATIONS))?),
        };
        self.rank_logs(request, listing, resume, visit)
    }

    /// Pass one streams every selected log and keeps facts; pass two reads
    /// the winners' lines again by offset and hands each to `visit`. The
    /// caller holds the lock, so the logs cannot change between the passes.
    fn rank_logs(
        &self,
        request: &Request,
        listing: Listing,
        resume: &Resume,
        visit: &mut Visit,
    ) -> Result<Option<String>, Error> {
        let mut ranking = Ranking::new(request, false)?;
        if let Resume::After(last) = resume {
            ranking.resume_after(last.clone());
        }
        let all = request.order == Order::Newest;
        let scan = |ranking: &mut Ranking, id: &str, conversation: u32| {
            let file = self.conversation_file(id, TURNS)?;
            if !file.is_file() {
                return Ok::<(), Error>(());
            }
            scan_log(&file, id, &request.terms, all, |turn, place, line| {
                let key = Key {
                    created: turn.created_at.instant(),
                    conversation,
                    turn: turn.turn,
                    place,
                    line: u32::try_from(line).unwrap_or(u32::MAX),
                };
                ranking.offer(key, &turn_text(&turn))
            })
        };
        match listing {
            Listing::Given(ids) => {
                for id in ids {
                    let conversation = ranking.conversation(id);
                    scan(&mut ranking, id, conversation)?;
                }
            }
            Listing::All(mut ids) => {
                // Sorted, the listing is the id table: no second copy (#417).
                ids.sort_unstable();
                ids.dedup();
                let count = u32::try_from(ids.len()).expect("conversation count fits u32");
                ranking.adopt_sorted(ids);
                for conversation in 0..count {
                    let id = ranking.name(conversation).to_string();
                    scan(&mut ranking, &id, conversation)?;
                }
            }
        }
        let digest = (request.order == Order::Relevance).then(|| ranking.digest());
        let winners = owed(ranking.winners(), resume, digest.as_deref().unwrap_or(""))?;
        // One log open at a time, whatever the number of conversations: the
        // previous one closes before the next opens.
        let mut open: Option<(u32, std::io::BufReader<fs::File>)> = None;
        for (key, id, score) in winners {
            if open
                .as_ref()
                .is_none_or(|(held, _)| *held != key.conversation)
            {
                drop(open.take());
                let file = fs::File::open(self.conversation_file(&id, TURNS)?)?;
                open = Some((key.conversation, std::io::BufReader::new(file)));
            }
            let (_, reader) = open.as_mut().expect("a log is open");
            let turn = read_at(reader, key.place, key.line as usize, &id)?;
            if !visit(turn, score)? {
                break;
            }
        }
        Ok(digest)
    }

    /// The conversation's directory, refused when a symlink would carry
    /// it outside the root.
    pub(super) fn conversation_dir(&self, conversation_id: &str) -> Result<PathBuf, Error> {
        parse_conversation_id(conversation_id)?;
        let candidate = self.root.join(CONVERSATIONS).join(conversation_id);
        if super::fileio::within(&self.root, &candidate)? {
            Ok(candidate)
        } else {
            Err(Refused::id_invalid(conversation_id, "escapes the store root").into())
        }
    }

    pub(super) fn conversation_file(&self, id: &str, name: &str) -> Result<PathBuf, Error> {
        let file = self.conversation_dir(id)?.join(name);
        if !super::fileio::within(&self.root, &file)? {
            return Err(Refused::id_invalid(id, "escapes the store root").into());
        }
        Ok(file)
    }
}

/// The conversations a file search reads: those a request names, in its
/// order, or every one the store holds.
enum Listing<'a> {
    Given(&'a [String]),
    All(Vec<String>),
}

/// The number of a log's last row, 0 for no log; the last line alone is
/// parsed, and refused as any row would be.
fn last_number(file: &Path, conversation_id: &str) -> Result<u64, Error> {
    if !file.is_file() {
        return Ok(0);
    }
    let text = fs::read_to_string(file)?;
    let count = text.lines().count();
    match text.lines().last() {
        None => Ok(0),
        Some(line) => Ok(parse_turn(line, count, conversation_id)?.turn),
    }
}

/// Every row of a JSONL file through `parse`, numbered from 1; a missing
/// file is no rows.
fn rows<T>(
    file: &Path,
    parse: impl Fn(&str, usize) -> Result<T, Refused>,
) -> Result<Vec<T>, Error> {
    if !file.is_file() {
        return Ok(Vec::new());
    }
    let text = fs::read_to_string(file)?;
    text.lines()
        .enumerate()
        .map(|(index, line)| parse(line, index + 1).map_err(Error::from))
        .collect()
}

//! The watcher's record of one turn (X4a): it reads the harness's native
//! output as it relays it, keeps the turn's tool calls in a private spool
//! file and the words its answer is made of, and hands the filing what the
//! child's own hooks would have handed the record verb. The file is
//! removed from the directory as soon as it is made, so it goes with the
//! watcher however the watcher ends, and nothing the child runs can open it.
//! One completed turn gives one record: a repeated completion or a repeated
//! tool call is ignored, and what follows the completion is not this turn.
//! The turn is kept whole within its bound or not at all: a line longer
//! than `LINE_BYTES` or a turn past `TURN_BYTES` marks it over its bound,
//! and the filing then records nothing for it. Everything held is charged
//! to that bound (spooled calls, retained words, tool inputs waiting for
//! their results), and once it is passed the capture lets go of all of it
//! and reads no more, while the watcher still relays the output (X4e P5).

use std::collections::{HashSet, VecDeque};
use std::fs::File;
use std::io::{self, BufWriter, Seek, Write};

use serde_json::{Value, json};

use super::adapter::Agent;
use super::native::{self, Said};

/// The longest line of native output the record reads.
pub const LINE_BYTES: usize = 8 << 20;
/// The most one turn's spooled tool calls and answer may hold.
pub const TURN_BYTES: u64 = 64 << 20;
/// Claude Code tool calls whose results have not arrived yet.
const CALLS_KEPT: usize = 256;

/// What the record holds of a turn while it runs.
pub struct Capture {
    agent: Agent,
    line: Vec<u8>,
    line_bytes: usize,
    turn_bytes: u64,
    session: Option<String>,
    calls: VecDeque<(String, String, Value)>,
    /// The bytes of the calls in `calls`, ids and names included.
    pending: u64,
    /// The bytes of the answer held.
    answer_bytes: u64,
    seen: HashSet<String>,
    texts: Vec<String>,
    text_bytes: u64,
    answer: Option<String>,
    completed: bool,
    failed: bool,
    over: bool,
    spool: Option<BufWriter<File>>,
    spooled: u64,
    error: Option<String>,
}

/// The turn as the filing receives it.
pub struct Captured {
    pub session: Option<String>,
    /// The provider completed the turn and reported no failure first.
    pub completed: bool,
    /// The answer the child's Stop hook would have carried.
    pub answer: String,
    /// A line or the turn passed its bound.
    pub over: bool,
    /// The spooled PostToolUse payloads, one JSON object per line, read
    /// from the start.
    pub tools: Option<File>,
    /// The spool could not be written.
    pub error: Option<String>,
}

/// A private file with no name: made in the temporary directory and
/// unlinked at once.
fn unnamed() -> io::Result<File> {
    use std::os::unix::fs::OpenOptionsExt;
    let path = std::env::temp_dir().join(format!(
        "neosian-capture-{}-{}",
        std::process::id(),
        uuid::Uuid::new_v4().simple()
    ));
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)?;
    std::fs::remove_file(&path)?;
    Ok(file)
}

impl Capture {
    pub fn new(agent: Agent) -> Self {
        Self::bounded(agent, LINE_BYTES, TURN_BYTES)
    }

    /// A capture with the given bounds (the watcher uses `new`).
    pub fn bounded(agent: Agent, line_bytes: usize, turn_bytes: u64) -> Self {
        Self {
            agent,
            line: Vec::new(),
            line_bytes,
            turn_bytes,
            session: None,
            calls: VecDeque::new(),
            pending: 0,
            answer_bytes: 0,
            seen: HashSet::new(),
            texts: Vec::new(),
            text_bytes: 0,
            answer: None,
            completed: false,
            failed: false,
            over: false,
            spool: None,
            spooled: 0,
            error: None,
        }
    }

    /// Bytes of native output as they arrive; none once over the bound.
    pub fn push(&mut self, mut bytes: &[u8]) {
        while !bytes.is_empty() && !self.over {
            let (chunk, newline) = match bytes.iter().position(|b| *b == b'\n') {
                Some(at) => (&bytes[..at], Some(at)),
                None => (bytes, None),
            };
            if self.line.len() + chunk.len() > self.line_bytes {
                self.release();
                return;
            }
            self.line.extend_from_slice(chunk);
            match newline {
                Some(at) => {
                    let line = std::mem::take(&mut self.line);
                    self.line(&String::from_utf8_lossy(&line));
                    bytes = &bytes[at + 1..];
                }
                None => bytes = &[],
            }
        }
    }

    fn line(&mut self, line: &str) {
        for said in native::read(self.agent, line) {
            self.take(said);
        }
    }

    fn take(&mut self, said: Said) {
        // What follows the completion is not this turn's.
        if self.completed || self.over {
            return;
        }
        match said {
            Said::Session(session) => {
                self.session.get_or_insert(session);
            }
            Said::Call { id, name, input } => {
                if self.calls.len() == CALLS_KEPT
                    && let Some((id, name, input)) = self.calls.pop_front()
                {
                    self.pending -= size(&id, &name, &input);
                }
                // The whole call is held, its id and name too (X4f Q7).
                self.pending += size(&id, &name, &input);
                self.calls.push_back((id, name, input));
                self.check();
            }
            Said::Result { id, response, ok } => {
                let Some(at) = self.calls.iter().position(|(call, _, _)| *call == id) else {
                    return;
                };
                let (id, name, input) = self.calls.remove(at).expect("found");
                self.pending -= size(&id, &name, &input);
                if ok {
                    self.tool(Some(id), name, input, response);
                }
            }
            Said::Tool {
                id,
                name,
                input,
                response,
            } => self.tool(id, name, input, response),
            Said::Text(words) => {
                if self.agent != Agent::OpenCode {
                    self.texts.clear();
                    self.text_bytes = 0;
                }
                self.text_bytes += words.len() as u64;
                self.texts.push(words);
                self.check();
            }
            Said::Step => {
                self.texts.clear();
                self.text_bytes = 0;
            }
            Said::Answer(answer) => {
                self.answer_bytes = answer.len() as u64;
                self.answer = Some(answer);
                self.check();
            }
            Said::Completed => self.completed = !self.failed,
            Said::Failed => self.failed = true,
        }
    }

    fn check(&mut self) {
        if self.spooled + self.text_bytes + self.pending + self.answer_bytes > self.turn_bytes {
            self.release();
        }
    }

    /// Over the bound: nothing of the turn is kept or read any more.
    fn release(&mut self) {
        self.over = true;
        self.line = Vec::new();
        self.calls = VecDeque::new();
        self.pending = 0;
        self.seen = HashSet::new();
        self.texts = Vec::new();
        self.text_bytes = 0;
        self.answer = None;
        self.answer_bytes = 0;
        self.spool = None;
    }

    /// One completed tool call, spooled as the PostToolUse payload the
    /// child's hook would have sent; a call seen before is not repeated.
    fn tool(&mut self, id: Option<String>, name: String, input: Value, response: Value) {
        if self.over || self.error.is_some() {
            return;
        }
        if let Some(id) = &id
            && !self.seen.insert(id.clone())
        {
            return;
        }
        let payload = json!({
            "hook_event_name": crate::record::payload::TOOL_EVENT,
            "tool_name": name,
            "tool_input": input,
            "tool_use_id": id,
            "tool_response": response,
        });
        let mut line = payload.to_string();
        line.push('\n');
        self.spooled += line.len() as u64;
        self.check();
        if self.over {
            return;
        }
        let written = match self.spool.as_mut() {
            Some(spool) => spool.write_all(line.as_bytes()),
            None => unnamed().and_then(|file| {
                let spool = self.spool.insert(BufWriter::new(file));
                spool.write_all(line.as_bytes())
            }),
        };
        if let Err(error) = written {
            self.error = Some(error.to_string());
        }
    }

    /// The turn as it stands, its spool read from the start.
    pub fn finish(mut self) -> Captured {
        if !self.over && !self.line.is_empty() {
            let line = std::mem::take(&mut self.line);
            self.line(&String::from_utf8_lossy(&line));
        }
        let tools = match self.spool.take().map(BufWriter::into_inner) {
            Some(Ok(mut file)) => match file.rewind() {
                Ok(()) => Some(file),
                Err(error) => {
                    self.error = Some(error.to_string());
                    None
                }
            },
            Some(Err(error)) => {
                self.error = Some(error.error().to_string());
                None
            }
            None => None,
        };
        let answer = match (self.agent, self.answer) {
            (Agent::ClaudeCode, Some(answer)) => answer,
            _ => self.texts.join("\n"),
        };
        Captured {
            session: self.session,
            completed: self.completed,
            answer,
            over: self.over,
            tools,
            error: self.error,
        }
    }
}

/// What one held call takes: its id, its name and its input as JSON text.
fn size(id: &str, name: &str, input: &Value) -> u64 {
    (id.len() + name.len() + input.to_string().len()) as u64
}

#[cfg(test)]
mod tests;

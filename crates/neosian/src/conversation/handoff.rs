//! The handoff delivered (`conversation/handoff.py`, DESIGN §33): the
//! conversation whole under a budget, every user prompt and final answer
//! verbatim with tool rounds one line each, the older turns as log lines,
//! its pending note first. This is the shell's read: the baton stays
//! pending, and the tool's pickup lands with the MCP server.

use super::ids::parse_conversation_id;
use super::links::LinkRegistry;
use super::message::{Role, text_of};
use super::projection::{REDACTED, one_line, results, tool_segment};
use super::sessions::{
    HANDOFF_PATH, Handoff, SESSIONS_PREFIX, continues, parse_handoff, sessions_path,
};
use super::views::project_conversation;
use super::{Error, Projection, Turn};
use crate::assets::{CONTEXT, folded, render};
use crate::repr::repr;
use crate::store::{Document, Entry, FileStore};
use crate::text::{len, strip};

/// Four times the index budget: the newest turns verbatim under three
/// quarters of it, the older ones as log lines under the rest (§9.6).
pub const CONTINUE_BUDGET_CHARS: usize = 32768;
/// `CompactionConfig()`'s widths: the digest, and the user line at four
/// times it.
pub const DIGEST_CHARS: usize = 200;
pub const USER_CHARS: usize = 4 * DIGEST_CHARS;

/// What a continue call delivered: the conversation, the text, and
/// whether its pending note rode along.
pub struct Continuation {
    pub conversation: String,
    pub text: String,
    pub note: bool,
}

/// A corrective failure as the tool answers it: the error and the
/// reminder for whoever asked.
pub struct Corrective {
    pub error: String,
    pub hint: Option<&'static str>,
}

/// The call's work, or the corrective failure it answers with.
pub type Answer<T> = Result<Result<T, Corrective>, Error>;

fn prompt(key: &str) -> String {
    folded(CONTEXT, key).expect("context.yaml carries the continue frame")
}

/// The conversation delivered: the note first when there is one, the
/// older turns as log lines, the newest verbatim (always the last one),
/// framed so the reader knows it is carrying on. Pure, no model.
pub fn render_continuation(
    turns: &[Turn],
    entries: &[Projection],
    continued: &[String],
    note: Option<&Handoff>,
    budget_chars: usize,
) -> String {
    let last = turns.last().expect("a delivered conversation has turns");
    let conversation_id = last.conversation_id.as_str();
    let links = LinkRegistry::of(turns, Some(conversation_id));
    let mut verbatim: Vec<String> = Vec::new();
    let mut spent = 0;
    for turn in turns.iter().rev() {
        let block = verbatim_block(turn, &links);
        if !verbatim.is_empty() && spent + len(&block) > budget_chars * 3 / 4 {
            break;
        }
        spent += len(&block);
        verbatim.push(block);
    }
    verbatim.reverse();
    let older = &turns[..turns.len() - verbatim.len()];
    let lines = if older.is_empty() {
        Vec::new()
    } else {
        let budget = budget_chars.saturating_sub(spent).max(1);
        project_conversation(older, entries, DIGEST_CHARS, USER_CHARS, budget, &links)
    };
    let lineage = if continued.is_empty() {
        String::new()
    } else {
        render(
            &prompt("continue_continues"),
            &[("ids", &continued.join(", "))],
        )
    };
    let head = render(
        &prompt("continue_header"),
        &[
            ("conversation_id", conversation_id),
            ("actor", last.actor.as_deref().unwrap_or("-")),
            ("turns", &last.turn.to_string()),
            ("first", &turns[0].created_at.minutes()),
            ("last", &last.created_at.minutes()),
            ("continues", &lineage),
        ],
    );
    let mut parts = vec![head];
    if let Some(note) = note {
        let written = note.written.minutes();
        let frame = [
            ("written", written.as_str()),
            ("actor", note.actor.as_str()),
        ];
        parts.push(render(&prompt("continue_note"), &frame));
        parts.push(note.note.clone());
        parts.push(prompt("continue_note_end"));
    }
    parts.extend(lines);
    parts.extend(verbatim);
    parts.push(prompt("continue_footer"));
    parts.join("\n")
}

/// `[n] USER:` verbatim, each tool round one line, the final answer
/// verbatim and any earlier prose one line (the agent thinking aloud);
/// a redacted turn is its label and the one word (§38).
fn verbatim_block(turn: &Turn, links: &LinkRegistry) -> String {
    if turn.redacted {
        return format!("[{}] {REDACTED}", turn.turn);
    }
    let results = results(turn);
    let spoke = |message: &super::message::Message| {
        message.role == Role::Assistant && !strip(&text_of(message)).is_empty()
    };
    let final_prose = turn.messages.iter().rposition(spoke);
    let mut lines = Vec::new();
    for (index, message) in turn.messages.iter().enumerate() {
        match message.role {
            Role::User => lines.push(format!("[{}] USER: {}", turn.turn, text_of(message))),
            Role::Assistant => {
                let prose = text_of(message);
                let text = strip(&prose);
                if !text.is_empty() {
                    let shown = if Some(index) == final_prose {
                        text.to_string()
                    } else {
                        one_line(text, DIGEST_CHARS, Some(links), " …")
                    };
                    lines.push(format!("AGENT: {shown}"));
                }
                for call in &message.tool_calls {
                    let result = results.get(call.id.as_str()).copied();
                    lines.push(tool_segment(&call.name, &call.arguments, result, links));
                }
            }
            Role::System | Role::Tool => {}
        }
    }
    lines.join("\n")
}

/// The continue call's work for a read (`pick_up=False`): the target
/// resolved, delivered with its pending note, the baton untouched.
pub fn continue_conversation(
    store: &FileStore,
    scope: &str,
    conversation: Option<&str>,
) -> Answer<Continuation> {
    let document = store.read(scope, HANDOFF_PATH)?;
    let baton = pending(document.as_ref());
    let target = match conversation {
        Some(named) => named.to_string(),
        None => match default_target(store, scope, baton.as_ref())? {
            Ok(target) => target,
            Err(corrective) => return Ok(Err(corrective)),
        },
    };
    parse_conversation_id(&target)?;
    let turns = store.read_turns(&target)?;
    if turns.is_empty() {
        return Ok(Err(Corrective {
            error: format!("Conversation {} has no turns", repr(&target)),
            hint: None,
        }));
    }
    let listed = store.read(scope, &sessions_path(&target))?;
    let continued = listed.map_or_else(Vec::new, |listed| continues(&listed.content));
    let delivered = baton.filter(|baton| belongs(baton, &target, conversation.is_some()));
    let entries = store.read_projections(&target)?;
    let text = render_continuation(
        &turns,
        &entries,
        &continued,
        delivered.as_ref(),
        CONTINUE_BUDGET_CHARS,
    );
    Ok(Ok(Continuation {
        conversation: target,
        text,
        note: delivered.is_some(),
    }))
}

fn pending(document: Option<&Document>) -> Option<Handoff> {
    let document = document.filter(|document| !document.envelope.redacted)?;
    parse_handoff(&document.content).filter(|baton| baton.pending)
}

/// Whether the pending note is the delivered session's: linked to it, or
/// unlinked when the call named nothing (the default chose it).
fn belongs(baton: &Handoff, target: &str, named: bool) -> bool {
    baton.conversation.as_deref() == Some(target) || (baton.conversation.is_none() && !named)
}

/// The session a bare call means: the note's when the record linked it,
/// else the newest listed session, written after the note when one is
/// pending and unlinked.
fn default_target(store: &FileStore, scope: &str, baton: Option<&Handoff>) -> Answer<String> {
    if let Some(linked) = baton.and_then(|baton| baton.conversation.clone()) {
        return Ok(Ok(linked));
    }
    let mut entries: Vec<Entry> = store
        .list_documents(scope, SESSIONS_PREFIX)?
        .into_iter()
        .filter(|entry| !entry.redacted)
        .collect();
    entries.sort_by_key(|entry| std::cmp::Reverse(entry.updated_at.instant()));
    if let Some(baton) = baton {
        entries.retain(|entry| entry.updated_at.instant() > baton.written.instant());
        if entries.is_empty() {
            return Ok(Err(Corrective {
                error: "The handoff note's session has not landed yet".to_string(),
                hint: Some("Name the conversation, or retry in a moment."),
            }));
        }
    }
    Ok(match entries.first() {
        Some(entry) => Ok(entry.path[SESSIONS_PREFIX.len()..].to_string()),
        None => Err(Corrective {
            error: "Nothing to continue: no session is listed in this scope".to_string(),
            hint: Some("Name a conversation id to continue one anyway."),
        }),
    })
}

//! The handoff delivered (`conversation/handoff.py`, DESIGN §33, §40.54
//! item 19): the conversation under a budget, every user prompt and final
//! answer verbatim with tool rounds one line each, the older turns as log
//! lines, its pending note first. The shell's read leaves the baton
//! pending; the tool's call picks it up (R1 slice D). The default view,
//! light, is that released rendering: from whole turns on a store without
//! records, from bounded heads on one that keeps them (`assemble`). The
//! views `prompts`, `calls` and `full` are drawn from heads on both.

mod assemble;
mod baton;
mod frame;

#[cfg(test)]
pub(crate) mod tests;

use self::assemble::Assembled;
use self::baton::{belongs, default_target, pending, picked_up};
use self::frame::frame;
use super::ids::parse_conversation_id;
use super::links::LinkRegistry;
use super::message::{Role, text_of};
use super::projection::{REDACTED, one_line, results, tool_segment};
use super::recall::View;
use super::sessions::{HANDOFF_PATH, Handoff, continues, sessions_path};
use super::views::project_conversation;
use super::{Error, Projection, Turn};
use crate::assets::{CONTEXT, folded};
use crate::repr::repr;
use crate::state::State;
use crate::state::records::Records;
use crate::store::Document;
use crate::store::grammar::Actor;
use crate::store::time::Stamp;
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
    let ends = (turns[0].created_at, last.created_at);
    let header = (conversation_id, last.actor.as_deref(), last.turn, ends);
    let mut parts = frame(header, continued, note, View::Light);
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

/// The continue call's work: the target resolved, delivered in `view`
/// with its pending note, and the baton marked picked up by `pick_up`'s
/// session at its moment; None is the shell's read, the baton untouched.
/// `records` is the store's records side when it keeps records.
pub fn continue_conversation(
    store: &dyn State,
    records: Option<&dyn Records>,
    scope: &str,
    conversation: Option<&str>,
    pick_up: Option<(&Actor, Stamp)>,
    view: View,
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
    let ask = Ask {
        store,
        scope,
        target: &target,
        named: conversation.is_some(),
        document: document.as_ref(),
        pick_up,
    };
    let assembled = match (records, view) {
        (None, View::Light) => None,
        _ => Some(assemble::read(store, records, &target, view)?),
    };
    let text = match assembled {
        None => released(&ask, baton, None)?,
        Some(Assembled::Unconvertible) => {
            let line = (view != View::Light).then(|| prompt("continue_whole"));
            released(&ask, baton, line)?
        }
        Some(Assembled::Empty) => Err(no_turns(&target)),
        Some(Assembled::Read(assembler, ends)) => {
            let (continued, delivered) = deliver(&ask, baton)?;
            let actor = ends.last.actor.as_deref();
            let times = (ends.first.created_at, ends.last.created_at);
            let head = (target.as_str(), actor, ends.last.turn, times);
            let parts = frame(head, &continued, delivered.as_ref(), view);
            let text = assembler.finish(parts, prompt("continue_footer"));
            Ok((text, delivered.is_some()))
        }
    };
    Ok(text.map(|(text, note)| Continuation {
        conversation: target.clone(),
        text,
        note,
    }))
}

/// One continue call's target and what delivering it may touch.
struct Ask<'a> {
    store: &'a dyn State,
    scope: &'a str,
    target: &'a str,
    named: bool,
    document: Option<&'a Document>,
    pick_up: Option<(&'a Actor, Stamp)>,
}

fn no_turns(target: &str) -> Corrective {
    Corrective {
        error: format!("Conversation {} has no turns", repr(target)),
        hint: None,
    }
}

/// The lineage the session document names, and the note delivered, the
/// baton picked up when the call may.
fn deliver(ask: &Ask, baton: Option<Handoff>) -> Result<(Vec<String>, Option<Handoff>), Error> {
    let listed = ask.store.read(ask.scope, &sessions_path(ask.target))?;
    let continued = listed.map_or_else(Vec::new, |listed| continues(&listed.content));
    let delivered = baton.filter(|baton| belongs(baton, ask.target, ask.named));
    if let (Some((by, now)), Some(document), Some(baton)) = (ask.pick_up, ask.document, &delivered)
    {
        picked_up(ask.store, ask.scope, document, baton, ask.target, by, now)?;
    }
    Ok((continued, delivered))
}

/// The released delivery from whole turns, `line` above it when a view
/// was asked of a conversation records cannot carry.
fn released(
    ask: &Ask,
    baton: Option<Handoff>,
    line: Option<String>,
) -> Result<Result<(String, bool), Corrective>, Error> {
    let turns = ask.store.read_turns(ask.target, 0, None)?;
    if turns.is_empty() {
        return Ok(Err(no_turns(ask.target)));
    }
    let (continued, delivered) = deliver(ask, baton)?;
    let entries = ask.store.read_projections(ask.target, 0, None)?;
    let text = render_continuation(
        &turns,
        &entries,
        &continued,
        delivered.as_ref(),
        CONTINUE_BUDGET_CHARS,
    );
    let text = match line {
        Some(line) => format!("{line}\n{text}"),
        None => text,
    };
    Ok(Ok((text, delivered.is_some())))
}

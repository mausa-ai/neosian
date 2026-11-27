//! The frame around a delivered conversation: its header, the line an
//! explicit view adds under it, and the pending note when one rides along.

use super::prompt;
use crate::assets::render;
use crate::conversation::recall::View;
use crate::conversation::sessions::Handoff;
use crate::store::time::Stamp;

/// What the header names: the conversation, its last turn's actor and
/// number, and the times of its first and last turns.
pub(super) type Head<'a> = (&'a str, Option<&'a str>, u64, (Stamp, Stamp));

/// The frame's opening parts: the header, the line an explicit view adds,
/// and the pending note when one rides along.
pub(super) fn frame(
    head: Head,
    continued: &[String],
    note: Option<&Handoff>,
    view: View,
) -> Vec<String> {
    let (conversation_id, actor, turns, (first, last)) = head;
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
            ("actor", actor.unwrap_or("-")),
            ("turns", &turns.to_string()),
            ("first", &first.minutes()),
            ("last", &last.minutes()),
            ("continues", &lineage),
        ],
    );
    let mut parts = vec![head];
    let named = match view {
        View::Light => None,
        View::Prompts => Some("continue_view_prompts"),
        View::Calls => Some("continue_view_calls"),
        View::Full => Some("continue_view_full"),
    };
    parts.extend(named.map(prompt));
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
    parts
}

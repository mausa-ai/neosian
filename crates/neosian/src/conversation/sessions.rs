//! The sessions documents and the handoff baton (`memory/sessions.py`,
//! DESIGN §20.9, §22.2, §32, §33), read back: a session is listed as a
//! memory document `sessions/<conversation_id>`, and the mount holds one
//! baton, `handoff`. Pure vocabulary; the writers land with their callers.

use crate::store::time::Stamp;
use crate::text::{splitlines, strip};

pub const SESSIONS_PREFIX: &str = "sessions/";
pub const HANDOFF_PATH: &str = "handoff";

pub fn sessions_path(session_id: &str) -> String {
    format!("{SESSIONS_PREFIX}{session_id}")
}

/// The `- key: value` lines of a document, in order.
fn pairs(text: &str) -> Vec<(&str, &str)> {
    splitlines(text)
        .into_iter()
        .filter_map(|line| {
            let (key, value) = line.strip_prefix("- ")?.split_once(": ")?;
            Some((key, strip(value)))
        })
        .collect()
}

/// Which sessions a sessions document says it continues, in order.
pub fn continues(content: &str) -> Vec<String> {
    pairs(content)
        .into_iter()
        .filter(|(key, _)| *key == "continues")
        .map(|(_, value)| value.to_string())
        .collect()
}

/// The baton as a reader sees it: who wrote it and when, the note, the
/// session it belongs to once the record linked it, and whether a
/// session has picked it up.
pub struct Handoff {
    pub actor: String,
    pub written: Stamp,
    pub note: String,
    pub conversation: Option<String>,
    pub pending: bool,
}

/// The baton read back, or None when the document is not one (a hand
/// edit): a reader shows nothing rather than guessing.
pub fn parse_handoff(content: &str) -> Option<Handoff> {
    let rest = content.split_once("\n\n").map_or("", |(_, rest)| rest);
    let (head, note) = rest.split_once("\n\n").unwrap_or((rest, ""));
    let fields = pairs(head);
    // `dict(pairs)`: the last value of a repeated key.
    let field = |name: &str| {
        fields
            .iter()
            .rev()
            .find(|(key, _)| *key == name)
            .map(|(_, value)| *value)
    };
    let written = Stamp::parse(&field("written")?.replace('Z', "+00:00")).ok()?;
    Some(Handoff {
        actor: field("from")?.to_string(),
        written,
        note: strip(note).to_string(),
        conversation: field("conversation").map(str::to_string),
        pending: field("status")? == "pending",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const BATON: &str = "# handoff from claude-code:s\n\n- from: claude-code:s\n- written: 2026-10-12T22:27:56.085324Z\n- status: pending\n- conversation: s\n\nPick up the release.\n";

    #[test]
    fn the_baton_reads_back() {
        let baton = parse_handoff(BATON).unwrap();
        assert_eq!(
            (baton.actor.as_str(), baton.note.as_str()),
            ("claude-code:s", "Pick up the release.")
        );
        assert_eq!(baton.written.z(), "2026-10-12T22:27:56.085324Z");
        assert_eq!(
            (baton.conversation.as_deref(), baton.pending),
            (Some("s"), true)
        );
        let picked = BATON.replace(
            "status: pending",
            "status: picked up 2026-10-12T23:00:00Z by mcp:codex",
        );
        assert!(!parse_handoff(&picked).unwrap().pending);
        let unlinked = BATON.replace("- conversation: s\n", "");
        assert!(parse_handoff(&unlinked).unwrap().conversation.is_none());
        assert!(parse_handoff("# hand edit\n\nno fields\n").is_none());
        assert!(parse_handoff(&BATON.replace(".085324Z", "")).is_none());
    }

    #[test]
    fn a_sessions_document_names_what_it_continues() {
        let document = "# codex session s\n\n- agent: codex\n- conversation: s\n- turns: 3\n- continues: a\n- continues: b\n";
        assert_eq!(continues(document), ["a", "b"]);
        assert!(continues("# nothing\n").is_empty());
        assert_eq!(sessions_path("s"), "sessions/s");
    }
}

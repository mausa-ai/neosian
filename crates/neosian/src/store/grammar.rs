//! The three grammars that decide what the store may keep (`memory/
//! scope.py`, `paths.py`, `actor.py`; ECOSYSTEM §2, DESIGN §8, §20):
//! scopes, document paths and actors. Validated, never interpreted: a
//! scope's meaning is the application's, and nothing here normalizes.

use super::error::MemoryError;
use crate::repr::repr;

const MAX_LENGTH: usize = 512;
const SCOPE_SEGMENTS: usize = 8;
const PATH_SEGMENTS: usize = 16;

/// `[A-Za-z0-9_.-]{1,128}`: an id, and a document path's segment.
fn is_id(text: &str) -> bool {
    (1..=128).contains(&text.len())
        && text
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
}

/// `[a-z][a-z0-9_]{0,31}`; an actor's kind may also carry a `-`.
fn is_kind(text: &str, dash: bool) -> bool {
    let mut bytes = text.bytes();
    text.len() <= 32
        && bytes.next().is_some_and(|b| b.is_ascii_lowercase())
        && bytes.all(|b| {
            b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || (dash && b == b'-')
        })
}

/// The shape scopes and actors share: `<kind>:<id>` segments joined by
/// `/`. `suffix` is the actor's turn, already judged. The refusal is the
/// reason alone; each grammar words its own error around it.
fn segments(value: &str, dash: bool, suffix: bool, shape: &str) -> Result<(), String> {
    let parts = || value.split('/').map(|part| part.split_once(':'));
    if value.chars().count() > MAX_LENGTH {
        return Err(format!("longer than {MAX_LENGTH} characters"));
    }
    if !(suffix
        && parts().all(|part| part.is_some_and(|(kind, id)| is_kind(kind, dash) && is_id(id))))
    {
        return Err(shape.to_string());
    }
    if parts().count() > SCOPE_SEGMENTS {
        return Err(format!("more than {SCOPE_SEGMENTS} segments"));
    }
    match parts().flatten().find(|(_, id)| matches!(*id, "." | "..")) {
        Some((_, id)) => Err(format!("id {} is reserved", repr(id))),
        None => Ok(()),
    }
}

/// ECOSYSTEM §2's scope grammar, the refusal as the reason alone (for a
/// flag that words its own error around it). Never normalizes.
pub fn check_scope(value: &str) -> Result<(), String> {
    segments(value, false, true, "does not match <type>:<id>[/...]")
}

/// ECOSYSTEM §2's scope grammar, the refusal as the store's error.
pub fn parse_scope(value: &str) -> Result<(), MemoryError> {
    check_scope(value).map_err(|reason| MemoryError::scope_invalid(value, &reason))
}

/// Who wrote a row, held to DESIGN §20's grammar: `<kind>:<id>[/...]`
/// with an optional `#<turn>`. Every writer of the binary names one, and
/// the grammar makes it a plain YAML scalar, which is how an envelope
/// spells it.
#[derive(Clone, Debug)]
pub struct Actor(String);

impl Actor {
    /// The refusal is the reason alone, for the flag that names it.
    pub fn parse(value: &str) -> Result<Self, String> {
        let (head, turn) = value
            .split_once('#')
            .map_or((value, None), |(head, turn)| (head, Some(turn)));
        let counted = turn.is_none_or(|turn| {
            !turn.is_empty() && !turn.starts_with('0') && turn.bytes().all(|b| b.is_ascii_digit())
        });
        let shape = "does not match <kind>:<id>[/...][#<turn>]";
        segments(head, true, counted, shape).map(|()| Self(value.to_string()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A logical document path: extension-free segments joined by `/`.
pub fn validate_document_path(path: &str) -> Result<(), MemoryError> {
    let refuse = |reason: &str| Err(MemoryError::path_invalid(path, reason));
    if path.chars().count() > MAX_LENGTH {
        return refuse(&format!("longer than {MAX_LENGTH} characters"));
    }
    if !path.split('/').all(is_id) {
        return refuse("segments must be [A-Za-z0-9_.-]{1,128} joined by '/'");
    }
    if path.split('/').count() > PATH_SEGMENTS {
        return refuse(&format!("more than {PATH_SEGMENTS} segments"));
    }
    match path
        .split('/')
        .find(|segment| matches!(*segment, "." | ".."))
    {
        Some(segment) => refuse(&format!("segment {} is reserved", repr(segment))),
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reason(error: MemoryError) -> String {
        error
            .message
            .split_once(": ")
            .map(|(_, reason)| reason.to_string())
            .unwrap_or_default()
    }

    #[test]
    fn the_scope_grammar_is_ecosystems() {
        for scope in [
            "user:me",
            "user:burak/proj:neosian",
            "tenant:acme/kb:A.b-c_1",
        ] {
            assert!(parse_scope(scope).is_ok(), "{scope}");
        }
        for (scope, why) in [
            ("user", "does not match <type>:<id>[/...]"),
            ("User:me", "does not match <type>:<id>[/...]"),
            ("user:me/", "does not match <type>:<id>[/...]"),
            ("user:a:b", "does not match <type>:<id>[/...]"),
            ("user:é", "does not match <type>:<id>[/...]"),
            ("user:..", "id '..' is reserved"),
            (
                "a:1/b:1/c:1/d:1/e:1/f:1/g:1/h:1/i:1",
                "more than 8 segments",
            ),
        ] {
            assert_eq!(
                parse_scope(scope).map_err(reason),
                Err(why.to_string()),
                "{scope}"
            );
        }
        let long = format!("user:{}", "a".repeat(600));
        assert_eq!(
            parse_scope(&long).map_err(reason),
            Err("longer than 512 characters".to_string())
        );
        assert_eq!(
            parse_scope("bad").unwrap_err().message,
            "Invalid memory scope 'bad': does not match <type>:<id>[/...]"
        );
    }

    #[test]
    fn the_actor_grammar_adds_a_dash_and_a_turn() {
        let parse_actor = |actor| Actor::parse(actor).map(|parsed| parsed.as_str().to_string());
        for actor in [
            "cli:local",
            "claude-code:abc#3",
            "serve:mcp/conv:x#12",
            "mcp:stdio",
        ] {
            assert_eq!(parse_actor(actor).as_deref(), Ok(actor));
        }
        let shape = Err("does not match <kind>:<id>[/...][#<turn>]".to_string());
        for actor in [
            "seed",
            "cli:local#0",
            "cli:local#",
            "cli:local#1#2",
            "Cli:local",
        ] {
            assert_eq!(parse_actor(actor), shape, "{actor}");
        }
        assert_eq!(parse_actor("cli:."), Err("id '.' is reserved".to_string()));
    }

    #[test]
    fn the_path_grammar_is_extension_free_segments() {
        for path in ["facts", "notes/reading", "..foo", "a.b-c_d/E"] {
            assert!(validate_document_path(path).is_ok(), "{path}");
        }
        for (path, why) in [
            (
                "/user",
                "segments must be [A-Za-z0-9_.-]{1,128} joined by '/'",
            ),
            ("", "segments must be [A-Za-z0-9_.-]{1,128} joined by '/'"),
            (
                "a//b",
                "segments must be [A-Za-z0-9_.-]{1,128} joined by '/'",
            ),
            ("a/../b", "segment '..' is reserved"),
            ("a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a/a", "more than 16 segments"),
        ] {
            assert_eq!(
                validate_document_path(path).map_err(reason),
                Err(why.to_string()),
                "{path}"
            );
        }
    }
}

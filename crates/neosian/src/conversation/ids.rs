//! The conversation-id grammar (`conversation/ids.py`, DESIGN §9.4),
//! validated and never interpreted: one flat segment of `[A-Za-z0-9_.-]`,
//! at most 128 characters, `.` and `..` reserved.

use super::Refused;
use crate::repr::repr;

const MAX_LENGTH: usize = 128;

/// The grammar, the refusal as the reason alone (for a flag that words
/// its own error around it).
pub fn check_conversation_id(value: &str) -> Result<(), String> {
    if value.chars().count() > MAX_LENGTH {
        return Err(format!("longer than {MAX_LENGTH} characters"));
    }
    let grammatical = |ch: char| ch.is_ascii_alphanumeric() || "_.-".contains(ch);
    if value.is_empty() || !value.chars().all(grammatical) {
        return Err("must match [A-Za-z0-9_.-]{1,128} — one flat segment".to_string());
    }
    if matches!(value, "." | "..") {
        return Err(format!("id {} is reserved", repr(value)));
    }
    Ok(())
}

/// The grammar, the refusal as the seam's error.
pub fn parse_conversation_id(value: &str) -> Result<(), Refused> {
    check_conversation_id(value).map_err(|reason| Refused::id_invalid(value, &reason))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_grammar_is_one_flat_segment() {
        for id in ["thread-829", "a", "A.b_c-9", &"x".repeat(128)] {
            assert!(check_conversation_id(id).is_ok(), "{id}");
        }
        let shape = Err("must match [A-Za-z0-9_.-]{1,128} — one flat segment".to_string());
        for id in ["", "a/b", "a:b", "é", "a b", "a\n"] {
            assert_eq!(check_conversation_id(id), shape, "{id:?}");
        }
        assert_eq!(
            check_conversation_id(&"x".repeat(129)),
            Err("longer than 128 characters".to_string())
        );
        assert_eq!(
            check_conversation_id(".."),
            Err("id '..' is reserved".to_string())
        );
        assert_eq!(
            parse_conversation_id("a/b").unwrap_err().message,
            "Invalid conversation id 'a/b': must match [A-Za-z0-9_.-]{1,128} — one flat segment"
        );
    }
}

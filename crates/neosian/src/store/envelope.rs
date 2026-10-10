//! The storage envelope around a document (`memory/envelope.py`, DESIGN
//! §8): the first `---` fence pair holds the metadata as YAML, and every
//! byte after the closing fence line is the content, verbatim, a content
//! that itself begins with `---` included.
//!
//! The reader is bounded to what the stores write and a plain hand edit
//! leaves: one `key: value` per line for the reserved keys, anything else
//! refused as `memory_format_unsupported`, never coerced.

use super::FORMAT;
use super::error::MemoryError;
use super::time::{Stamp, Unreadable};

const FENCE: &str = "---";
const RESERVED: [&str; 6] = [
    "neosian_format",
    "version",
    "created_at",
    "updated_at",
    "actor",
    "redacted",
];
const NULL: [&str; 4] = ["~", "null", "Null", "NULL"];
const TRUE: [&str; 9] = [
    "true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON",
];
const FALSE: [&str; 9] = [
    "false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF",
];

/// The storage metadata of one document.
#[derive(Debug, PartialEq, Eq)]
pub struct Envelope {
    pub version: u64,
    pub updated_at: Stamp,
    pub redacted: bool,
}

/// Split a stored file into its envelope and its byte-exact content.
pub fn parse<'a>(
    text: &'a str,
    scope: &str,
    path: &str,
) -> Result<(Envelope, &'a str), MemoryError> {
    let refuse = |reason: &str| MemoryError::format_unsupported(scope, path, reason);
    let fence = |line: &str| line.trim_end_matches('\r') == FENCE;
    let mut lines = text.split('\n');
    let Some(open) = lines.next().filter(|line| fence(line)) else {
        return Err(refuse("missing envelope fence"));
    };
    // The first later fence line closes the envelope, whatever follows.
    let mut close = open.len() + 1;
    let mut width = None;
    for line in lines {
        if fence(line) {
            width = Some(line.len());
            break;
        }
        close += line.len() + 1;
    }
    let Some(width) = width else {
        return Err(refuse("unterminated envelope fence"));
    };
    let mut values: [Option<&str>; 6] = [None; 6];
    let mut entries = 0;
    for line in text[open.len() + 1..close].lines() {
        let item = line == "-" || line.starts_with("- ");
        if line.is_empty() || line.starts_with([' ', '\t', '#']) || (item && entries > 0) {
            continue; // a comment, or a longer value continuing
        }
        let Some((key, value)) = key_line(line).filter(|_| !line.starts_with(['[', '{'])) else {
            let reason = if entries == 0 && !line.contains(": ") {
                "envelope is not a mapping"
            } else {
                "invalid envelope YAML"
            };
            return Err(refuse(reason));
        };
        entries += 1;
        if let Some(slot) = RESERVED.iter().position(|reserved| *reserved == key) {
            values[slot] = Some(value);
        }
    }
    if entries == 0 {
        return Err(refuse("envelope is not a mapping"));
    }
    let [format, version, created_at, updated_at, _actor, redacted] = values;
    let integer = |raw: Option<&str>| {
        raw.filter(|text| !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit()))
            .and_then(|text| text.parse::<u64>().ok())
            .filter(|number| *number >= 1)
    };
    let stamp = |raw: Option<&str>, key: &str| {
        let absent = || refuse(&format!("missing or invalid {key} timestamp"));
        let raw = raw
            .filter(|text| !text.is_empty() && !NULL.contains(text))
            .ok_or_else(absent)?;
        let quoted = ['\'', '"'].iter().find_map(|quote| {
            raw.strip_prefix(*quote)
                .and_then(|rest| rest.strip_suffix(*quote))
        });
        match Stamp::parse(quoted.unwrap_or(raw)) {
            Ok(stamp) => Ok(stamp),
            // A bare YAML date is a date, not a moment.
            Err(Unreadable::Naive) if quoted.is_none() && raw.len() == 10 => Err(absent()),
            Err(Unreadable::Naive) => Err(refuse(&format!("naive {key} timestamp"))),
            Err(Unreadable::Malformed) => Err(refuse(&format!("unreadable {key} timestamp"))),
        }
    };
    let declared = integer(format).ok_or_else(|| refuse("missing neosian_format"))?;
    if declared > FORMAT {
        return Err(refuse(&format!("format {declared} is newer than {FORMAT}")));
    }
    let version = integer(version).ok_or_else(|| refuse("missing or invalid version"))?;
    stamp(created_at, "created_at")?;
    let updated_at = stamp(updated_at, "updated_at")?;
    let redacted = match redacted {
        None => false,
        Some(word) if TRUE.contains(&word) => true,
        Some(word) if FALSE.contains(&word) => false,
        Some(_) => return Err(refuse("redacted must be a boolean")),
    };
    let envelope = Envelope {
        version,
        updated_at,
        redacted,
    };
    Ok((envelope, text.get(close + width + 1..).unwrap_or("")))
}

/// `key: value` at the left margin: the key, and the value trimmed.
fn key_line(line: &str) -> Option<(&str, &str)> {
    match line.split_once(": ") {
        Some((key, value)) => Some((key, value.trim())),
        None => line.strip_suffix(':').map(|key| (key, "")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // What `envelope.render` writes for version 3 by `seed`, as PyYAML
    // spells it (tests/unit/memory/test_envelope.py holds the Python side).
    const HEAD: &str = "---\nneosian_format: 1\nversion: 3\ncreated_at: '2026-08-19T10:00:00Z'\nupdated_at: '2026-08-19T10:05:00.123456Z'\nactor: seed\n---\n";

    fn read(text: &str) -> Result<(Envelope, &str), String> {
        parse(text, "user:test", "notes/api").map_err(|error| error.message)
    }

    fn reason(text: &str) -> String {
        let message = read(text).unwrap_err();
        let prefix = "Unsupported memory format for 'notes/api' in scope 'user:test': ";
        message.strip_prefix(prefix).unwrap_or(&message).to_string()
    }

    #[test]
    fn the_content_is_every_byte_after_the_closing_fence() {
        for content in [
            "",
            "x",
            "x\n",
            "x\n\n",
            "\n",
            "  leading and trailing  ",
            "a\r\nb",
            "---\ntitle: t\n---\nbody",
            "---",
            "unicode: café ✓",
        ] {
            let text = format!("{HEAD}{content}");
            let (envelope, restored) = read(&text).unwrap();
            assert_eq!(restored, content);
            assert_eq!(envelope.version, 3);
            assert_eq!(
                envelope.updated_at.iso(),
                "2026-08-19T10:05:00.123456+00:00"
            );
            assert!(!envelope.redacted);
        }
    }

    #[test]
    fn unknown_keys_and_longer_values_are_read_past() {
        let text = HEAD.replace(
            "actor: seed\n",
            "actor: seed\nredacted: true\ncustom: kept\nnote: 'a\n\n  ---\n\n  b'\ntags:\n- one\n- two\n",
        );
        let text = format!("{text}body");
        let (envelope, content) = read(&text).unwrap();
        assert!(envelope.redacted);
        assert_eq!(content, "body");
    }

    #[test]
    fn a_crlf_fence_and_an_aware_plain_timestamp_are_accepted() {
        let text = HEAD
            .replacen("---\n", "---\r\n", 1)
            .replace("'2026-08-19T10:00:00Z'", "2026-08-19 10:00:00+00:00");
        assert_eq!(read(&format!("{text}body")).unwrap().1, "body");
    }

    #[test]
    fn a_broken_or_newer_envelope_is_refused_never_coerced() {
        let swap = |from: &str, to: &str| HEAD.replace(from, to);
        for (text, why) in [
            ("no fence at all".to_string(), "missing envelope fence"),
            (
                "---\nversion: 1\nno closing".to_string(),
                "unterminated envelope fence",
            ),
            (
                "---\n[not: a: mapping\n---\n".to_string(),
                "invalid envelope YAML",
            ),
            (
                "---\n- just\n- a list\n---\n".to_string(),
                "envelope is not a mapping",
            ),
            ("---\n---\n".to_string(), "envelope is not a mapping"),
            (
                "---\nversion: 1\n---\n".to_string(),
                "missing neosian_format",
            ),
            (
                swap("neosian_format: 1", "neosian_format: true"),
                "missing neosian_format",
            ),
            (
                swap("neosian_format: 1", "neosian_format: 2"),
                "format 2 is newer than 1",
            ),
            (
                swap("version: 3", "version: 0"),
                "missing or invalid version",
            ),
            (
                swap("'2026-08-19T10:00:00Z'", "'2026-08-19T10:00:00'"),
                "naive created_at timestamp",
            ),
            (
                swap("'2026-08-19T10:00:00Z'", "2026-08-19"),
                "missing or invalid created_at timestamp",
            ),
            (
                swap("'2026-08-19T10:00:00Z'", "'soon'"),
                "unreadable created_at timestamp",
            ),
            (
                swap("updated_at: '2026-08-19T10:05:00.123456Z'\n", ""),
                "missing or invalid updated_at timestamp",
            ),
            (
                swap("actor: seed", "actor: seed\nredacted: maybe"),
                "redacted must be a boolean",
            ),
        ] {
            assert_eq!(reason(&text), why, "{text:?}");
        }
    }
}

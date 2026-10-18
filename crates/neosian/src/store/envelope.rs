//! The storage envelope around a document (`memory/envelope.py`, DESIGN
//! §8): the first `---` fence pair holds the metadata as YAML, and every
//! byte after the closing fence line is the content, verbatim, a content
//! that itself begins with `---` included.
//!
//! The codec is bounded to what the stores write and a plain hand edit
//! leaves: one `key: value` per line for the reserved keys, anything else
//! about them refused as `memory_format_unsupported`, never coerced. An
//! unknown key is kept as the lines it was read from and written back
//! after the reserved ones (C6), so what PyYAML wrote comes back as PyYAML
//! wrote it.

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
    pub created_at: Stamp,
    pub updated_at: Stamp,
    /// The actor as the file spells it: a YAML scalar, plain for every
    /// actor of the §20 grammar.
    pub actor: Option<String>,
    pub redacted: bool,
    /// The unknown keys' lines, verbatim.
    pub extra: Vec<String>,
}

impl Envelope {
    /// The actor's value: the spelled scalar read back, a quoted one
    /// unquoted (the wire's `actor`, §18.2).
    pub fn actor_value(&self) -> Option<String> {
        self.actor
            .as_deref()
            .map(|spelled| match super::scalar::read(spelled) {
                Some(serde_json::Value::String(value)) => value,
                _ => spelled.to_string(),
            })
    }
}

/// Serialize envelope and content; `parse` is its exact inverse. The
/// timestamps are quoted because YAML would otherwise read them as its
/// own timestamp type; a false `redacted` and an absent actor are left out.
pub fn render(envelope: &Envelope, content: &str) -> String {
    let (created, updated) = (envelope.created_at.z(), envelope.updated_at.z());
    let mut text = format!(
        "{FENCE}\nneosian_format: {FORMAT}\nversion: {}\ncreated_at: '{created}'\nupdated_at: '{updated}'\n",
        envelope.version
    );
    if let Some(actor) = &envelope.actor {
        text += &format!("actor: {actor}\n");
    }
    if envelope.redacted {
        text += "redacted: true\n";
    }
    for line in &envelope.extra {
        text += &format!("{line}\n");
    }
    format!("{text}{FENCE}\n{content}")
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
    let mut extra = Vec::new();
    let mut entries = 0;
    let mut unknown = false; // is the entry being read an unknown key's?
    for line in text[open.len() + 1..close].lines() {
        let item = line == "-" || line.starts_with("- ");
        let longer = line.starts_with([' ', '\t']) || (item && entries > 0);
        if longer || line.is_empty() || line.starts_with('#') {
            if unknown {
                extra.push(line.to_string());
            } else if longer {
                // A reserved key holds one scalar, on its own line.
                return Err(refuse("invalid envelope YAML"));
            }
            continue;
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
        let slot = RESERVED.iter().position(|reserved| *reserved == key);
        unknown = slot.is_none();
        match slot {
            Some(slot) => values[slot] = Some(value),
            None => extra.push(line.to_string()),
        }
    }
    if entries == 0 {
        return Err(refuse("envelope is not a mapping"));
    }
    let [format, version, created_at, updated_at, actor, redacted] = values;
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
    let created_at = stamp(created_at, "created_at")?;
    let updated_at = stamp(updated_at, "updated_at")?;
    let named = |word: &&str| !word.is_empty() && !NULL.contains(word);
    let typed =
        |word| TRUE.contains(&word) || FALSE.contains(&word) || integer(Some(word)).is_some();
    let actor = match actor.filter(named) {
        Some(word) if typed(word) => return Err(refuse("actor must be a string")),
        actor => actor.map(str::to_string),
    };
    let redacted = match redacted {
        None => false,
        Some(word) if TRUE.contains(&word) => true,
        Some(word) if FALSE.contains(&word) => false,
        Some(_) => return Err(refuse("redacted must be a boolean")),
    };
    let envelope = Envelope {
        version,
        created_at,
        updated_at,
        actor,
        redacted,
        extra,
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
mod tests;

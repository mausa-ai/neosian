//! The codec against PyYAML's own spelling: every expectation here is
//! `memory/envelope.py`'s output for the same envelope, verbatim
//! (tests/unit/memory/test_envelope.py holds the Python side).

use super::*;

// Version 3 by `seed`, created 10:00, updated 10:05 and a fraction.
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
        assert_eq!((envelope.version, envelope.redacted), (3, false));
        assert_eq!(envelope.actor.as_deref(), Some("seed"));
        assert_eq!(render(&envelope, restored), text);
    }
}

#[test]
fn an_envelope_is_spelled_as_pyyaml_spells_it() {
    let at = |text| Stamp::parse(text).unwrap();
    let mut envelope = Envelope {
        version: 3,
        created_at: at("2026-08-19T10:00:00Z"),
        updated_at: at("2026-08-19T10:05:00.123456Z"),
        actor: Some("cli:local".to_string()),
        redacted: false,
        extra: Vec::new(),
    };
    let head = HEAD.replace("seed", "cli:local");
    assert_eq!(render(&envelope, "body\n"), format!("{head}body\n"));
    envelope.created_at = at("2026-08-19T12:00:00+02:00");
    assert!(render(&envelope, "").contains("\ncreated_at: '2026-08-19T12:00:00+02:00'\n"));
    envelope = Envelope {
        version: 1,
        created_at: at("2026-08-19T10:00:00Z"),
        updated_at: at("2026-08-19T10:00:00Z"),
        actor: None,
        redacted: true,
        extra: Vec::new(),
    };
    assert_eq!(
        render(&envelope, ""),
        "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-08-19T10:00:00Z'\nupdated_at: '2026-08-19T10:00:00Z'\nredacted: true\n---\n"
    );
}

#[test]
fn unknown_keys_come_back_as_they_were_written() {
    // `envelope.render` with eleven extra keys: a quoted multi-line value
    // holding a `---` line, a list and a nested mapping among them.
    let extra = "custom: kept\ncount: 3\nflag: true\nnote: 'a\n\n  ---\n\n  b'\n'yes': 'yes'\ne: café\ntags:\n- a\n- b\nnested:\n  k: 1\n  deep:\n  - x\nempty: ''\nnum: '42'\ntrail: 'ends\n\n  '\n";
    let head = HEAD.replace(
        "actor: seed\n",
        &format!("actor: seed\nredacted: true\n{extra}"),
    );
    let text = format!("{head}body");
    let (envelope, content) = read(&text).unwrap();
    assert!(envelope.redacted);
    assert_eq!((content, envelope.extra.len()), ("body", 22));
    assert_eq!(render(&envelope, content), text);
    // A hand edit that put a foreign key first: the reserved keys lead.
    let moved = text.replacen("---\n", "---\nfirst: 1\n", 1);
    let (envelope, content) = read(&moved).unwrap();
    let again = render(&envelope, content);
    assert!(again.starts_with("---\nneosian_format: 1\n") && again.contains("\nfirst: 1\n"));
    assert_eq!(read(&again).unwrap().0, envelope);
}

#[test]
fn an_actor_is_kept_as_the_file_spells_it() {
    let with = |actor: &str| HEAD.replace("actor: seed", actor);
    for (line, kept) in [
        ("actor: '123'", Some("'123'")),
        ("actor: conv 1", Some("conv 1")),
        ("actor: ''", Some("''")),
        ("actor: null", None),
        ("actor:", None),
        ("no_actor: 1", None),
    ] {
        let text = with(line);
        assert_eq!(read(&text).unwrap().0.actor.as_deref(), kept, "{line}");
    }
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
    let stamp = "'2026-08-19T10:00:00Z'";
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
            swap(stamp, "'2026-08-19T10:00:00'"),
            "naive created_at timestamp",
        ),
        (
            swap(stamp, "2026-08-19"),
            "missing or invalid created_at timestamp",
        ),
        (swap(stamp, "'soon'"), "unreadable created_at timestamp"),
        (
            swap("updated_at: '2026-08-19T10:05:00.123456Z'\n", ""),
            "missing or invalid updated_at timestamp",
        ),
        (
            swap("actor: seed", "actor: seed\nredacted: maybe"),
            "redacted must be a boolean",
        ),
        (swap("actor: seed", "actor: 123"), "actor must be a string"),
        (swap("actor: seed", "actor: yes"), "actor must be a string"),
        (
            swap("actor: seed", "actor:\n  kind: cli"),
            "invalid envelope YAML",
        ),
    ] {
        assert_eq!(reason(&text), why, "{text:?}");
    }
}

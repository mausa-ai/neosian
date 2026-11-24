//! Numbering and the first stamp read one line (§40.54 item 4.4): a
//! counting reader shows the last line read back from the end costs the
//! line and at most a block, however long the log; lines are those
//! `str::lines` gives; a refusal still names the row's line number.

use std::fs;
use std::io::{self, Cursor, Read, Seek, SeekFrom};

use serde_json::json;

use super::super::lines::{first_line, last_line, line_count};
use super::{enabled, memory, messages, written};
use crate::testing::Scratch;

/// A reader that counts the bytes read through it and the reads.
struct Counting<R> {
    inner: R,
    read: u64,
    calls: u64,
}

impl<R: Read> Read for Counting<R> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let count = self.inner.read(buffer)?;
        self.read += count as u64;
        self.calls += 1;
        Ok(count)
    }
}

impl<R: Seek> Seek for Counting<R> {
    fn seek(&mut self, to: SeekFrom) -> io::Result<u64> {
        self.inner.seek(to)
    }
}

#[test]
fn the_last_line_costs_itself_and_a_block_however_long_the_log() {
    let header = r#"{"neosian_format":2,"turn":3,"created_at":"2026-11-21T14:03:07.512000Z","actor":null,"records":1,"bytes":5,"draft":"d"}"#;
    let log = format!(
        "{}\n{}\n{header}\n",
        "x".repeat(16 << 20),
        "y".repeat(1_000)
    );
    let mut reader = Counting {
        inner: Cursor::new(log.into_bytes()),
        read: 0,
        calls: 0,
    };
    assert_eq!(
        last_line(&mut reader).unwrap().as_deref(),
        Some(header.as_bytes())
    );
    assert!(
        reader.read <= header.len() as u64 + 1 + 8_192 + 1,
        "read {} bytes",
        reader.read
    );
}

/// Finding 1 of the review of 2481787: the work, not only the bytes read,
/// is linear in the last line. Each block is read once and searched once,
/// and the bytes allocated and copied stay within twice the line and a
/// block, where joining every block to the ones before it allocated about
/// 268 MB for this 2 MiB line (5.6 s for 16 MiB in a release build).
#[test]
fn the_last_line_costs_work_linear_in_its_length() {
    let line = 2 << 20;
    let log = format!("first\n{}\n", "z".repeat(line));
    let mut reader = Counting {
        inner: Cursor::new(log.into_bytes()),
        read: 0,
        calls: 0,
    };
    let (found, asked) = memory::asked(|| last_line(&mut reader).unwrap().unwrap());
    assert_eq!(found.len(), line);
    let blocks = (line / 8_192 + 1) as u64;
    assert!(
        reader.calls <= blocks + 2,
        "{} reads for {blocks} blocks",
        reader.calls
    );
    assert!(
        asked <= 2 * line + 64 * 1_024,
        "allocated {asked} bytes for a {line}-byte line"
    );
}

/// Finding 11: a store without records now appends past a log line that is
/// not UTF-8, since numbering reads the last line alone (stated in §40.54),
/// but a bad last line or first line is refused naming its line, as a
/// malformed row is.
#[test]
fn a_line_that_is_not_utf8_is_refused_by_its_number() {
    let home = Scratch::new("records-lines-utf8");
    let store = crate::store::FileStore::open(home.path()).unwrap();
    for text in ["one", "two"] {
        store
            .append_turn(
                "c-1",
                messages(&[json!({"role": "user", "content": text})]),
                None,
            )
            .unwrap();
    }
    let log = store.conversation_dir("c-1").unwrap().join("turns.jsonl");
    let good = fs::read(&log).unwrap();
    let spoiled = |word: &[u8]| {
        let mut bytes = good.clone();
        let at = bytes.windows(3).position(|w| w == word).unwrap();
        bytes[at] = 0xff;
        bytes
    };
    fs::write(&log, spoiled(b"two")).unwrap();
    for refused in [
        store.last_turn_number("c-1").unwrap_err().text(),
        store
            .append_turn(
                "c-1",
                messages(&[json!({"role": "user", "content": "x"})]),
                None,
            )
            .unwrap_err()
            .text(),
    ] {
        assert!(
            refused.contains("agent_conversation_format_unsupported")
                && refused.contains("turn-log line 2 is not UTF-8"),
            "{refused}"
        );
    }
    fs::write(&log, spoiled(b"one")).unwrap();
    let refused = store.first_turn_stamp("c-1").unwrap_err().text();
    assert!(
        refused.contains("turn-log line 1 is not UTF-8"),
        "{refused}"
    );
    let appended = store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "three"})]),
            None,
        )
        .unwrap();
    assert_eq!(appended.turn, 3);
}

#[test]
fn lines_are_what_str_lines_gives() {
    for text in [
        "a",
        "a\n",
        "a\nb",
        "a\nb\n",
        "a\n\n",
        "\n",
        "a\r\nb\r\n",
        "a\rb",
        "a\r",
        "a\r\r\n",
        "é\n中",
        "\n\n\n",
    ] {
        let mut reader = Cursor::new(text.as_bytes().to_vec());
        assert_eq!(
            last_line(&mut reader).unwrap().as_deref(),
            text.lines().last().map(str::as_bytes),
            "{text:?}"
        );
        let home = Scratch::new("records-lines");
        home.plant("log", text);
        let path = home.path().join("log");
        assert_eq!(line_count(&path).unwrap(), text.lines().count(), "{text:?}");
        assert_eq!(
            first_line(&path).unwrap().as_deref(),
            text.lines().next().map(str::as_bytes),
            "{text:?}"
        );
    }
    assert_eq!(last_line(&mut Cursor::new(Vec::new())).unwrap(), None);
    let long = format!("{}\nlast", "z".repeat(20_000));
    assert_eq!(
        last_line(&mut Cursor::new(long.into_bytes()))
            .unwrap()
            .as_deref(),
        Some(&b"last"[..])
    );
    let one = "w".repeat(20_000);
    assert_eq!(
        last_line(&mut Cursor::new(one.clone().into_bytes())).unwrap(),
        Some(one.into_bytes())
    );
}

#[test]
fn numbering_after_a_header_is_gapless_and_a_bad_last_row_names_its_line() {
    let (_home, store) = enabled("records-numbering");
    let first = messages(&[json!({"role": "user", "content": "w".repeat(3 << 20)})]);
    store.append_turn("c-1", first, None).unwrap();
    written(
        &store,
        "c-1",
        "d",
        &messages(&[json!({"role": "user", "content": "small"})]),
    );
    assert_eq!(store.last_turn_number("c-1").unwrap(), 2);
    let third = store
        .append_turn(
            "c-1",
            messages(&[json!({"role": "user", "content": "x"})]),
            None,
        )
        .unwrap();
    assert_eq!(third.turn, 3);
    let log = store.conversation_dir("c-1").unwrap().join("turns.jsonl");
    let mut text = fs::read_to_string(&log).unwrap();
    text.push_str("{broken\n");
    fs::write(&log, text).unwrap();
    let refused = store.last_turn_number("c-1").unwrap_err().text();
    assert!(refused.contains("turn-log line 4"), "{refused}");
}

/// The last valid row can be unterminated in an imported or hand-edited
/// log. Appending the next row must leave that predecessor readable.
fn append_to_unterminated_log(seal: bool) {
    let name = if seal {
        "unterminated-seal"
    } else {
        "unterminated-append"
    };
    for first_sealed in [false, true] {
        let (_home, store) = enabled(name);
        let first = messages(&[json!({"role": "user", "content": "first"})]);
        if first_sealed {
            written(&store, "c-1", "old", &first);
        } else {
            store.append_turn("c-1", first, None).unwrap();
        }
        let log = store.conversation_dir("c-1").unwrap().join("turns.jsonl");
        let original = fs::read(&log).unwrap();
        fs::write(&log, original.strip_suffix(b"\n").unwrap()).unwrap();
        let second = messages(&[json!({"role": "assistant", "content": "second"})]);
        if seal {
            written(&store, "c-1", "new", &second);
        } else {
            store.append_turn("c-1", second, None).unwrap();
        }
        assert!(fs::read(&log).unwrap().starts_with(&original));
        let turns = store.read_turns("c-1", 0, None).unwrap();
        assert_eq!(turns.len(), 2);
        for (turn, text) in turns.iter().zip(["first", "second"]) {
            assert_eq!(turn.messages.len(), 1);
            assert_eq!(
                crate::conversation::message::to_json(&turn.messages[0])["content"],
                text
            );
        }
        assert_eq!((turns[0].turn, turns[1].turn), (1, 2));
    }
}

#[test]
fn whole_append_separates_an_unterminated_last_row() {
    append_to_unterminated_log(false);
}

#[test]
fn seal_separates_an_unterminated_last_row() {
    append_to_unterminated_log(true);
}

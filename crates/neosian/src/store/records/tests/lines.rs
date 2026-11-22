//! Numbering and the first stamp read one line (§40.54 item 4.4): a
//! counting reader shows the last line read back from the end costs the
//! line and at most a block, however long the log; lines are those
//! `str::lines` gives; a refusal still names the row's line number.

use std::fs;
use std::io::{self, Cursor, Read, Seek, SeekFrom};

use serde_json::json;

use super::super::lines::{first_line, last_line, line_count};
use super::{enabled, messages, written};
use crate::testing::Scratch;

/// A reader that counts the bytes read through it.
struct Counting<R> {
    inner: R,
    read: u64,
}

impl<R: Read> Read for Counting<R> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let count = self.inner.read(buffer)?;
        self.read += count as u64;
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
    };
    assert_eq!(last_line(&mut reader).unwrap().as_deref(), Some(header));
    assert!(
        reader.read <= header.len() as u64 + 1 + 8_192 + 1,
        "read {} bytes",
        reader.read
    );
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
        "é\n中",
        "\n\n\n",
    ] {
        let mut reader = Cursor::new(text.as_bytes().to_vec());
        assert_eq!(
            last_line(&mut reader).unwrap().as_deref(),
            text.lines().last(),
            "{text:?}"
        );
        let home = Scratch::new("records-lines");
        home.plant("log", text);
        let path = home.path().join("log");
        assert_eq!(line_count(&path).unwrap(), text.lines().count(), "{text:?}");
        assert_eq!(
            first_line(&path).unwrap().as_deref(),
            text.lines().next(),
            "{text:?}"
        );
    }
    assert_eq!(last_line(&mut Cursor::new(Vec::new())).unwrap(), None);
    let long = format!("{}\nlast", "z".repeat(20_000));
    assert_eq!(
        last_line(&mut Cursor::new(long.into_bytes()))
            .unwrap()
            .as_deref(),
        Some("last")
    );
    let one = "w".repeat(20_000);
    assert_eq!(
        last_line(&mut Cursor::new(one.clone().into_bytes())).unwrap(),
        Some(one)
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

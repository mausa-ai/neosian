//! A log's first and last lines without reading the rest (DESIGN §40.54
//! item 4.4): numbering reads the last line back from the end in blocks,
//! the first stamp reads the first line alone. Lines are what `str::lines`
//! gives, so every row reads as the whole-file reads did; only a refusal
//! counts the lines, to name the row it refuses.

use std::fs;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;

use crate::conversation::search::line_text;

const BLOCK: u64 = 8_192;

fn text(bytes: Vec<u8>) -> io::Result<String> {
    String::from_utf8(bytes)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "a log line is not UTF-8"))
}

/// The last line, or None for an empty log; at most the line and a block
/// are read.
pub fn last_line<R: Read + Seek>(reader: &mut R) -> io::Result<Option<String>> {
    let length = reader.seek(SeekFrom::End(0))?;
    if length == 0 {
        return Ok(None);
    }
    reader.seek(SeekFrom::Start(length - 1))?;
    let mut last = [0_u8];
    reader.read_exact(&mut last)?;
    let terminated = last[0] == b'\n';
    let end = if terminated { length - 1 } else { length };
    let (mut held, mut start) = (Vec::new(), end);
    loop {
        if let Some(at) = held.iter().rposition(|byte| *byte == b'\n') {
            held.drain(..=at);
            break;
        }
        if start == 0 {
            break;
        }
        let step = BLOCK.min(start);
        start -= step;
        reader.seek(SeekFrom::Start(start))?;
        let mut block = vec![0_u8; step as usize];
        reader.read_exact(&mut block)?;
        block.extend_from_slice(&held);
        held = block;
    }
    if terminated && held.last() == Some(&b'\r') {
        held.pop();
    }
    text(held).map(Some)
}

/// The first line, or None for an empty or absent log.
pub fn first_line(path: &Path) -> io::Result<Option<String>> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut buffer = String::new();
    if BufReader::new(file).read_line(&mut buffer)? == 0 {
        return Ok(None);
    }
    Ok(Some(line_text(&buffer).to_string()))
}

/// How many lines `str::lines` finds, counted a buffer at a time.
pub fn line_count(path: &Path) -> io::Result<usize> {
    let mut reader = BufReader::new(fs::File::open(path)?);
    let (mut count, mut last) = (0, None);
    loop {
        let buffer = reader.fill_buf()?;
        if buffer.is_empty() {
            break;
        }
        count += buffer.iter().filter(|byte| **byte == b'\n').count();
        last = buffer.last().copied();
        let used = buffer.len();
        reader.consume(used);
    }
    Ok(count + usize::from(last.is_some_and(|byte| byte != b'\n')))
}

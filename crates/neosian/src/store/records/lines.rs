//! A log's first and last lines without reading the rest (DESIGN §40.54
//! item 4.4): numbering reads the last line back from the end in blocks,
//! the first stamp reads the first line alone. Lines are what `str::lines`
//! gives, so every row reads as the whole-file reads did; only a refusal
//! counts the lines, to name the row it refuses. Both answer the line's
//! bytes, so that a line that is not UTF-8 is refused by its number.

use std::fs;
use std::io::{self, BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;

const BLOCK: u64 = 8_192;

/// The last line's bytes, or None for an empty log. The line and at most a
/// block are read, each block once, and only the block just read is
/// searched for the line's start: work linear in the line (review finding
/// 1 of 2481787, where joining each block to the ones before it was
/// quadratic).
pub fn last_line<R: Read + Seek>(reader: &mut R) -> io::Result<Option<Vec<u8>>> {
    let length = reader.seek(SeekFrom::End(0))?;
    if length == 0 {
        return Ok(None);
    }
    reader.seek(SeekFrom::Start(length - 1))?;
    let mut last = [0_u8];
    reader.read_exact(&mut last)?;
    let terminated = last[0] == b'\n';
    let end = if terminated { length - 1 } else { length };
    let (mut blocks, mut start) = (Vec::new(), end);
    while start > 0 {
        let step = BLOCK.min(start);
        start -= step;
        reader.seek(SeekFrom::Start(start))?;
        let mut block = vec![0_u8; step as usize];
        reader.read_exact(&mut block)?;
        let begins = block.iter().rposition(|byte| *byte == b'\n');
        if let Some(at) = begins {
            block.drain(..=at);
        }
        blocks.push(block);
        if begins.is_some() {
            break;
        }
    }
    let mut line = Vec::with_capacity(blocks.iter().map(Vec::len).sum());
    for block in blocks.iter().rev() {
        line.extend_from_slice(block);
    }
    if terminated && line.last() == Some(&b'\r') {
        line.pop();
    }
    Ok(Some(line))
}

/// The first line's bytes, or None for an empty or absent log.
pub fn first_line(path: &Path) -> io::Result<Option<Vec<u8>>> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut line = Vec::new();
    if BufReader::new(file).read_until(b'\n', &mut line)? == 0 {
        return Ok(None);
    }
    if line.last() == Some(&b'\n') {
        line.pop();
        if line.last() == Some(&b'\r') {
            line.pop();
        }
    }
    Ok(Some(line))
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

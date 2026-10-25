//! Streaming codec for the existing format-1 redo document. Validate fully before apply.

use super::*;
use serde::de::{DeserializeSeed, Error as _, MapAccess, SeqAccess, Visitor};
use std::fmt;
use std::io::{BufReader, BufWriter};

struct Rows<'a, F>(&'a mut F);
impl<'de, F: FnMut(WriteOp) -> io::Result<()>> DeserializeSeed<'de> for Rows<'_, F> {
    type Value = usize;
    fn deserialize<D: serde::Deserializer<'de>>(self, deserializer: D) -> Result<usize, D::Error> {
        deserializer.deserialize_seq(self)
    }
}
impl<'de, F: FnMut(WriteOp) -> io::Result<()>> Visitor<'de> for Rows<'_, F> {
    type Value = usize;
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("redo writes")
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<usize, A::Error> {
        let mut count = 0;
        while let Some(write) = seq.next_element()? {
            (self.0)(write).map_err(A::Error::custom)?;
            count += 1;
        }
        Ok(count)
    }
}
struct Document<'a, F>(&'a mut F);
impl<'de, F: FnMut(WriteOp) -> io::Result<()>> Visitor<'de> for Document<'_, F> {
    type Value = ();
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("format-1 redo plan")
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<(), A::Error> {
        let mut format: Option<u32> = None;
        let mut count = None;
        while let Some(key) = map.next_key::<String>()? {
            match key.as_str() {
                "format" if format.is_none() => format = Some(map.next_value()?),
                "writes" if count.is_none() => count = Some(map.next_value_seed(Rows(self.0))?),
                _ => return Err(A::Error::custom("unexpected or duplicate recovery field")),
            }
        }
        if format != Some(1) || count.is_none_or(|n| n == 0) {
            return Err(A::Error::custom("invalid recovery plan"));
        }
        Ok(())
    }
}

pub(super) fn read(
    path: &Path,
    mut visit: impl FnMut(WriteOp) -> io::Result<()>,
) -> io::Result<()> {
    use serde::Deserializer;
    let mut parser = serde_json::Deserializer::from_reader(BufReader::new(fs::File::open(path)?));
    parser
        .deserialize_map(Document(&mut visit))
        .map_err(|_| bad())?;
    parser.end().map_err(|_| bad())
}

pub(super) fn validate_file(root: &Path, pending: &Path) -> io::Result<()> {
    let mut seen = BTreeSet::new();
    read(pending, |write| validate_write(root, &write, &mut seen))
}

fn validate_write(root: &Path, write: &WriteOp, seen: &mut BTreeSet<String>) -> io::Result<()> {
    let target = write.target(root)?;
    if !seen.insert(write.path.clone()) {
        return Err(bad());
    }
    if let Operation::Append {
        offset,
        prefix_sha256,
        ..
    } = &write.operation
    {
        prefix(&target, *offset, prefix_sha256)?;
    }
    Ok(())
}

struct Bounded<W> {
    writer: W,
    used: u64,
    maximum: u64,
}
impl<W: Write> Write for Bounded<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let used = self.used.saturating_add(bytes.len() as u64);
        if used > self.maximum {
            return Err(crate::limits::io_refusal(format!(
                "resource limit NEOSIAN_MAX_TEMP_BYTES exceeded while preparing recovery plan: {used} > {}; reduce the batch or raise the setting",
                self.maximum
            )));
        }
        let written = self.writer.write(bytes)?;
        self.used += written as u64;
        Ok(written)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.writer.flush()
    }
}

pub(super) fn commit(
    root: &Path,
    writes: impl Iterator<Item = io::Result<WriteOp>>,
    maximum: u64,
    mut boundary: impl FnMut(usize) -> io::Result<()>,
) -> io::Result<()> {
    let directory = root.join(DIRECTORY);
    safe(root, &directory)?;
    let pending = directory.join("pending.json");
    safe(root, &pending)?;
    if pending.exists() {
        return Err(bad());
    }
    fileio::private_mkdir(&directory)?;
    let draft = directory.join(format!(".neosian-tmp-{}", uuid::Uuid::new_v4()));
    struct Draft(PathBuf);
    impl Drop for Draft {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.0);
        }
    }
    let draft = Draft(draft);
    let file = fileio::private()
        .write(true)
        .create_new(true)
        .open(&draft.0)?;
    let mut out = Bounded {
        writer: BufWriter::new(file),
        used: 0,
        maximum,
    };
    out.write_all(b"{\"format\":1,\"writes\":[")?;
    let mut seen = BTreeSet::new();
    let mut count = 0;
    for write in writes {
        let write = write?;
        validate_write(root, &write, &mut seen)?;
        let size = match &write.operation {
            Operation::Put { text } => text.len() as u64,
            Operation::Append { offset, text, .. } => offset.saturating_add(text.len() as u64),
            Operation::Remove => 0,
        };
        let check =
            || crate::limits::check(crate::limits::FILE, size, crate::limits::file_bytes()?);
        check().map_err(crate::limits::into_io)?;
        if count != 0 {
            out.write_all(b",")?;
        }
        serde_json::to_writer(&mut out, &write).map_err(io::Error::from)?;
        count += 1;
    }
    if count == 0 {
        return Ok(());
    }
    out.write_all(b"]}")?;
    out.flush()?;
    out.writer.get_ref().sync_all()?;
    drop(out);
    fs::rename(&draft.0, &pending)?;
    sync_parents(root, &pending)?;
    boundary(0)?;
    let mut applied = 0;
    read(&pending, |write| {
        apply(root, &write)?;
        applied += 1;
        boundary(applied)
    })?;
    fs::remove_file(&pending)?;
    sync_parents(root, &pending)
}

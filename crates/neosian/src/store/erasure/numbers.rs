//! The matched turn numbers, in their original order, on disk. A projection
//! checks its interval by binary search on a normally ordered log. Old logs
//! restored out of order retain their trail order and use a streaming scan.
use crate::store::fileio::private;
use std::fs::File;
use std::io::{self, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::Path;

pub(super) struct Numbers {
    file: BufWriter<File>,
    pub count: usize,
    last: u64,
    sorted: bool,
}

impl Numbers {
    pub fn new(path: &Path) -> io::Result<Self> {
        let file = private()
            .create_new(true)
            .write(true)
            .read(true)
            .open(path)?;
        Ok(Self {
            file: BufWriter::new(file),
            count: 0,
            last: 0,
            sorted: true,
        })
    }

    pub fn push(&mut self, n: u64) -> io::Result<()> {
        self.file.write_all(&n.to_be_bytes())?;
        self.sorted &= self.last <= n;
        self.last = n;
        self.count += 1;
        Ok(())
    }

    pub fn visit(&mut self, mut f: impl FnMut(u64) -> io::Result<()>) -> io::Result<()> {
        self.file.seek(SeekFrom::Start(0))?;
        let mut reader = BufReader::new(self.file.get_ref());
        for _ in 0..self.count {
            f(read(&mut reader)?)?;
        }
        Ok(())
    }

    pub fn covers(&mut self, first: u64, last: u64) -> io::Result<bool> {
        if !self.sorted {
            let mut found = false;
            self.visit(|n| {
                found |= (first..=last).contains(&n);
                Ok(())
            })?;
            return Ok(found);
        }
        let (mut lo, mut hi) = (0, self.count);
        while lo < hi {
            let mid = lo + (hi - lo) / 2;
            self.file.seek(SeekFrom::Start(mid as u64 * 8))?;
            let n = read(&mut self.file.get_ref())?;
            if n < first {
                lo = mid + 1;
            } else if n > last {
                hi = mid;
            } else {
                return Ok(true);
            }
        }
        Ok(false)
    }
}
fn read(r: &mut impl Read) -> io::Result<u64> {
    let mut bytes = [0; 8];
    r.read_exact(&mut bytes)?;
    Ok(u64::from_be_bytes(bytes))
}

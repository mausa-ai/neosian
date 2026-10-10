//! Private file I/O (`shared/fileio.py`, ledger #126): everything the store
//! leaves on a user's disk is theirs alone, files 0600 and directories
//! 0700, set at creation and never retrofitted.

use std::fs::{DirBuilder, OpenOptions};
use std::io;
use std::path::Path;

/// `mkdir -p` with every newly created level private.
pub fn private_mkdir(directory: &Path) -> io::Result<()> {
    let mut missing = Vec::new();
    let mut current = directory;
    while !current.exists() {
        missing.push(current);
        match current.parent() {
            Some(parent) => current = parent,
            None => break,
        }
    }
    let mut builder = DirBuilder::new();
    #[cfg(unix)]
    std::os::unix::fs::DirBuilderExt::mode(&mut builder, 0o700);
    for level in missing.into_iter().rev() {
        match builder.create(level) {
            Err(error) if error.kind() != io::ErrorKind::AlreadyExists => return Err(error),
            _ => {}
        }
    }
    Ok(())
}

/// Open options under which a new file is born private.
pub fn private() -> OpenOptions {
    let mut options = OpenOptions::new();
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, 0o600);
    options
}

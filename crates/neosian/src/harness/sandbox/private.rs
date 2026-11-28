//! A turn's private directory: mode 0700 under the system temporary
//! directory, holding the child marker; every child's TMPDIR on macOS and
//! Codex's elsewhere. It is removed when its guard goes, and also when the
//! marker cannot be written, since a directory without one is never swept
//! as stale (X4e P8).

use std::io;
use std::path::{Path, PathBuf};

pub struct Private(PathBuf);

impl Private {
    pub fn make() -> io::Result<Self> {
        use std::os::unix::fs::DirBuilderExt;
        let dir = std::env::temp_dir().join(format!(
            "neosian-harness-{}-{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::DirBuilder::new().mode(0o700).create(&dir)?;
        Self::marked(dir, |dir| {
            std::fs::write(dir.join(super::super::MARKER), "")
        })
    }

    /// The directory made, kept only once `mark` succeeded.
    pub fn marked(dir: PathBuf, mark: impl FnOnce(&Path) -> io::Result<()>) -> io::Result<Self> {
        let made = Self(dir);
        mark(&made.0)?;
        Ok(made)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for Private {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

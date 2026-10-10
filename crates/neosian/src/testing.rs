//! Test scaffolding: a private directory per test, removed when it drops.

use std::fs;
use std::path::{Path, PathBuf};

pub struct Scratch(PathBuf);

impl Scratch {
    /// A fresh directory named for the test that owns it.
    pub fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("neosian-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("the scratch directory is creatable");
        Self(dir)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    /// Plant a file below the directory, its parents created.
    pub fn plant(&self, relative: &str, text: &str) {
        let file = self.0.join(relative);
        fs::create_dir_all(file.parent().expect("a planted file has a parent"))
            .and_then(|()| fs::write(&file, text))
            .expect("the planted file is writable");
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

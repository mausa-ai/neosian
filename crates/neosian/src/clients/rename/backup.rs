//! The rename's backup (DESIGN §40.53 item 5): a private folder under the
//! neosian home with a copy of each file before its first change in a run,
//! and `index.json` naming the files and the entries each move wrote (the
//! receipt is rebuilt from it when lost).

use super::{Step, places::Container};
use crate::{
    clients::{context::Context, files::Failure, naming::NEW, receipt},
    store::fileio,
};
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};

/// `$NEOSIAN_HOME` as the client environment names it, else `~/.neosian`.
pub fn home(context: &Context) -> PathBuf {
    match context.env.get("NEOSIAN_HOME").filter(|v| !v.is_empty()) {
        Some(named) => super::super::context::expand(named, &context.home),
        None => context.home.join(".neosian"),
    }
}

pub fn migrations(context: &Context) -> PathBuf {
    home(context).join("migrations")
}

fn private(path: &Path, directory: bool) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = if directory { 0o700 } else { 0o600 };
        fs::set_permissions(path, fs::Permissions::from_mode(mode))
    }
    #[cfg(not(unix))]
    {
        let _ = (path, directory);
        Ok(())
    }
}

fn failed(error: impl std::fmt::Display) -> Failure {
    let message = super::text::fill("backup_failed", &[("error", &error.to_string())]);
    Failure::new(message, super::text::get("backup_fix"))
}

fn sha(path: &Path) -> Option<String> {
    fs::read(path).ok().map(|bytes| receipt::sha256(&bytes))
}

pub struct Backup {
    pub dir: PathBuf,
    index: Value,
}

impl Backup {
    pub fn begin(context: &Context) -> Result<Self, Failure> {
        let root = migrations(context);
        fs::create_dir_all(&root).map_err(failed)?;
        let stamp = receipt::now().replace(['-', ':'], "");
        let mut run = format!("{stamp}-rename");
        let mut n = 2;
        while root.join(&run).exists() {
            run = format!("{stamp}-rename-{n}");
            n += 1;
        }
        let dir = root.join(&run);
        fs::create_dir(&dir)
            .and_then(|()| private(&dir, true))
            .and_then(|()| fs::create_dir(dir.join("files")))
            .and_then(|()| private(&dir.join("files"), true))
            .map_err(failed)?;
        let index = json!({"format": 1, "run": run, "home": home(context), "state": "running",
            "created_at": receipt::now(), "files": [], "moves": []});
        let backup = Self { dir, index };
        backup.save()?;
        Ok(backup)
    }

    fn save(&self) -> Result<(), Failure> {
        let path = self.dir.join("index.json");
        fileio::atomic_write(&path, &crate::clients::files::pretty(&self.index), false)
            .and_then(|()| private(&path, false))
            .map_err(failed)
    }

    /// Copy `path` before its first change in this run.
    pub fn keep(&mut self, path: &Path) -> Result<(), Failure> {
        let files = self.index["files"].as_array().expect("files");
        if files.iter().any(|f| f["file"] == json!(path)) || !path.is_file() {
            return Ok(());
        }
        let copy = format!("files/{}", files.len());
        let target = self.dir.join(&copy);
        fs::copy(path, &target)
            .and_then(|_| private(&target, false))
            .map_err(failed)?;
        let entry = json!({"file": path, "copy": copy, "sha256_before": sha(path)});
        self.index["files"]
            .as_array_mut()
            .expect("files")
            .push(entry);
        self.save()
    }

    /// A client's completed steps: the entry each left under `to`.
    pub fn moved(
        &mut self,
        context: &Context,
        steps: &[(&Step, Option<Value>)],
        to: &str,
    ) -> Result<(), Failure> {
        for (step, entry) in steps {
            let container: &Container = &step.container;
            let record = json!({"client": container.client,
                "client_dir": context.directory(container.client), "file": container.file,
                "pointer": container.pointer(NEW), "entry": entry,
                "direction": if to == NEW { "forward" } else { "back" }});
            self.index["moves"]
                .as_array_mut()
                .expect("moves")
                .push(record);
        }
        self.save()
    }

    pub fn finish(&mut self) -> Result<(), Failure> {
        for file in self.index["files"].as_array_mut().expect("files") {
            let path = PathBuf::from(file["file"].as_str().unwrap_or(""));
            file["sha256_after"] = json!(sha(&path));
        }
        self.index["state"] = json!("done");
        self.save()
    }
}

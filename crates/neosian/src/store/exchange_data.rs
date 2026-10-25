//! Receiver artifacts, hashed in full so local edits cannot be silently replaced.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use super::exchange_files::{Artifact, Files};
use super::{FileStore, envelope, error::Error, journal, layout};
use crate::exchange::model::{Bundle, selected};
use crate::knowledge::model::invalid;

/// Probe names on the receiver's filesystem before committing a redo plan.
/// Case/normalization aliases and file/directory overlaps must not leave an
/// unrecoverable plan. Probe files are empty and the fresh directory is private.
pub(super) fn check_layout(root: &Path, scope: &Path, files: &Files) -> Result<(), Error> {
    struct Probe(PathBuf);
    impl Drop for Probe {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    let directory = root.join(format!(".exchange-probe-{}", uuid::Uuid::new_v4()));
    super::fileio::private_builder().create(&directory)?;
    let probe = Probe(directory);
    for name in files.keys() {
        let target = scope.join(name);
        if target.is_dir()
            || target
                .ancestors()
                .skip(1)
                .take_while(|p| *p != scope)
                .any(|p| p.exists() && !p.is_dir())
        {
            return Err(invalid(
                "exchange paths collide with the receiver layout; snapshot_required",
            ));
        }
        let file = probe.0.join(name);
        super::fileio::private_mkdir(file.parent().expect("artifact parent"))?;
        super::fileio::private()
            .write(true)
            .create_new(true)
            .open(file)
            .map_err(|_| {
                invalid("exchange paths collide or are unsupported on the receiver filesystem")
            })?;
    }
    Ok(())
}

impl FileStore {
    pub(super) fn exchange_files(&self, scope: &str) -> Result<Files, Error> {
        let directory = layout::scope_dir(&self.root, scope);
        let mut files = Files::new();
        let mut total = 0;
        let mut distinct = BTreeSet::new();
        let maximum = crate::limits::setting(crate::limits::REPLICA, 1 << 30, 64 << 30)?;
        let paths = crate::limits::setting("NEOSIAN_REPLICA_MAX_PATHS", 100_000, 1_000_000)?;
        for (folder, suffix) in [(layout::DOCUMENTS, ".md"), (layout::VERSIONS, ".jsonl")] {
            let mut after = String::new();
            loop {
                let page = layout::path_page(&directory.join(folder), suffix, "", &after, 256)?;
                if page.is_empty() {
                    break;
                }
                for path in &page {
                    let relative = format!("{folder}/{path}{suffix}");
                    let file = layout::contained(&self.root, directory.join(&relative), path)?;
                    let artifact = Artifact::open(file.clone())?;
                    total += artifact.bytes;
                    crate::limits::check(crate::limits::REPLICA, total, maximum)?;
                    crate::limits::check(
                        "NEOSIAN_REPLICA_MAX_PATHS",
                        {
                            distinct.insert(path.clone());
                            distinct.len() as u64
                        },
                        paths,
                    )?;
                    if folder == layout::DOCUMENTS {
                        self.load(scope, path, &file)?;
                    } else {
                        self.rows(scope, path)?;
                    }
                    files.insert(relative, artifact);
                }
                after = page.last().expect("nonempty").clone();
            }
        }
        let trail = layout::trail_file(&self.root, scope)?;
        if trail.exists() {
            let artifact = Artifact::open(trail.clone())?;
            total += artifact.bytes;
            crate::limits::check(crate::limits::REPLICA, total, maximum)?;
            journal::read_acts(&trail, scope)?;
            files.insert(layout::REDACTIONS.into(), artifact);
        }
        Ok(files)
    }

    pub(super) fn exchange_data(
        &self,
        scope: &str,
        bundle: &Bundle,
        files: &mut Files,
        scratch: &crate::limits::Scratch,
    ) -> Result<(), Error> {
        let mut paths = BTreeSet::new();
        for name in files.keys() {
            if let Some(path) = name
                .strip_prefix("documents/")
                .and_then(|s| s.strip_suffix(".md"))
                .or_else(|| {
                    name.strip_prefix("versions/")
                        .and_then(|s| s.strip_suffix(".jsonl"))
                })
            {
                paths.insert(path.to_string());
            }
        }
        let observed = paths.clone();
        paths.extend(bundle.updates.iter().map(|u| u.path.clone()));
        paths.extend(bundle.erasures.iter().filter_map(|e| e.path.clone()));
        crate::limits::check(
            "NEOSIAN_REPLICA_MAX_PATHS",
            paths.len() as u64,
            crate::limits::setting("NEOSIAN_REPLICA_MAX_PATHS", 100_000, 1_000_000)?,
        )?;
        if paths.iter().any(|p| !selected(p)) {
            return Err(invalid("replica must contain only ordinary paths"));
        }
        let mut folded = BTreeSet::new();
        if paths.iter().any(|p| !folded.insert(p.to_lowercase())) {
            return Err(invalid("case-aliased replica path"));
        }
        let updates: BTreeMap<_, _> = bundle
            .updates
            .iter()
            .map(|u| (u.path.as_str(), u))
            .collect();
        let erased_paths: BTreeSet<_> = bundle
            .erasures
            .iter()
            .filter_map(|e| e.path.as_deref())
            .collect();
        let erased_scope = bundle.erasures.iter().any(|e| e.path.is_none());
        for path in paths {
            let update = updates.get(path.as_str());
            let erased = erased_scope || erased_paths.contains(path.as_str());
            if update.is_none() && !erased {
                continue;
            }
            let doc_name = format!("documents/{path}.md");
            let row_name = format!("versions/{path}.jsonl");
            let (_, mut rows) = self.rows(scope, &path)?;
            if erased {
                for row in &mut rows {
                    row.content.clear();
                    row.redacted = true;
                }
                if let Some(text) = files.get(&doc_name) {
                    let text = text.text()?;
                    let (mut envelope, _) = envelope::parse(&text, scope, &path)?;
                    envelope.redacted = true;
                    files.insert(
                        doc_name.clone(),
                        Artifact::stage(scratch, &envelope::render(&envelope, ""))?,
                    );
                }
            }
            if let Some(update) = update {
                let row = if let Some(record) = &update.document {
                    let doc = record.document()?;
                    let row = journal::Row {
                        path: path.clone(),
                        version: doc.envelope.version,
                        action: if files.contains_key(&doc_name) {
                            journal::Action::Modified
                        } else {
                            journal::Action::Created
                        },
                        content: doc.content.clone(),
                        actor: doc.envelope.actor_value(),
                        created_at: doc.envelope.updated_at,
                        redacted: doc.envelope.redacted,
                    };
                    files.insert(
                        doc_name.clone(),
                        Artifact::stage(scratch, &envelope::render(&doc.envelope, &doc.content))?,
                    );
                    row
                } else {
                    let deletion = update.deletion.as_ref().expect("validated deletion");
                    files.remove(&doc_name);
                    journal::Row {
                        path: path.clone(),
                        version: deletion.version,
                        action: journal::Action::Deleted,
                        content: String::new(),
                        actor: deletion.actor.clone(),
                        created_at: super::time::Stamp::parse(&deletion.created_at)
                            .expect("validated timestamp"),
                        redacted: deletion.redacted,
                    }
                };
                match rows.last() {
                    Some(previous) if previous.version > row.version => {
                        return Err(invalid("source version regressed; snapshot_required"));
                    }
                    Some(previous) if previous.version == row.version => {
                        if previous.content != row.content
                            || previous.redacted != row.redacted
                            || previous.actor != row.actor
                            || previous.created_at != row.created_at
                            || (previous.action == journal::Action::Deleted)
                                != (row.action == journal::Action::Deleted)
                        {
                            return Err(invalid(
                                "source revision changed without erasure; snapshot_required",
                            ));
                        }
                    }
                    _ => rows.push(row),
                }
            }
            if !rows.is_empty() {
                files.insert(
                    row_name,
                    Artifact::stage(
                        scratch,
                        &rows.iter().map(journal::render_row).collect::<String>(),
                    )?,
                );
            }
        }
        let mut trail = files
            .get(layout::REDACTIONS)
            .map(Artifact::text)
            .transpose()?
            .unwrap_or_default();
        for erasure in &bundle.erasures {
            let act = journal::Act {
                path: erasure.path.clone(),
                actor: Some(format!("exchange:{}", bundle.checkpoint.stream)),
                created_at: super::time::Stamp::parse(&erasure.created_at)
                    .expect("validated timestamp"),
                count: observed
                    .iter()
                    .filter(|path| erasure.path.as_ref().is_none_or(|p| p == *path))
                    .count() as u64,
            };
            trail.push_str(&journal::render_act(&act));
        }
        if !bundle.erasures.is_empty() {
            files.insert(layout::REDACTIONS.into(), Artifact::stage(scratch, &trail)?);
        }
        crate::limits::check(
            crate::limits::REPLICA,
            files.values().map(|f| f.bytes).sum(),
            crate::limits::setting(crate::limits::REPLICA, 1 << 30, 64 << 30)?,
        )?;
        Ok(())
    }
}

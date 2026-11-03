//! Bounded Markdown snapshots. No tool-supplied paths, symlinks or host config parsing.

use super::{config::Source, invalid};
use crate::store::error::Error;
use std::fs;
use std::io::Read;
use std::path::Path;

const MAX_FILES: usize = 256;
const MAX_ENTRIES: usize = 1024;
const MAX_FILE_BYTES: u64 = 1024 * 1024;
const MAX_TOTAL_BYTES: usize = 8 * 1024 * 1024;

pub struct File {
    pub name: String,
    pub content: String,
}

fn markdown(path: &Path) -> bool {
    path.extension()
        .and_then(|s| s.to_str())
        .is_some_and(|s| s.eq_ignore_ascii_case("md"))
}

fn regular(path: &Path) -> Result<fs::Metadata, Error> {
    let meta = fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() || !(meta.is_file() || meta.is_dir()) {
        return Err(invalid(
            "native memory sources must be regular files or directories without symlinks",
        ));
    }
    Ok(meta)
}

fn no_links(path: &Path) -> Result<(), Error> {
    for ancestor in path.ancestors() {
        regular(ancestor)?;
    }
    Ok(())
}

fn walk(
    root: &Path,
    dir: &Path,
    recursive: bool,
    files: &mut Vec<std::path::PathBuf>,
    visited: &mut usize,
) -> Result<(), Error> {
    if dir
        .strip_prefix(root)
        .expect("beneath root")
        .components()
        .count()
        > 8
    {
        return Err(invalid("native memory nesting exceeds 8 levels"));
    }
    for entry in fs::read_dir(dir)? {
        *visited += 1;
        if *visited > MAX_ENTRIES {
            return Err(invalid(
                "native memory scan exceeds 1024 entries; register smaller sources",
            ));
        }
        let entry = entry?;
        if entry
            .file_name()
            .to_str()
            .is_some_and(|s| s.starts_with('.'))
        {
            continue;
        }
        let path = entry.path();
        let meta = regular(&path)?;
        if meta.is_dir() && recursive {
            walk(root, &path, true, files, visited)?;
        } else if meta.is_file() && markdown(&path) {
            files.push(path);
            if files.len() > MAX_FILES {
                return Err(invalid(
                    "native memory scan exceeds 256 files; register smaller sources",
                ));
            }
        }
    }
    Ok(())
}

pub fn read(source: &Source) -> Result<Vec<File>, Error> {
    no_links(&source.source)?;
    let meta = regular(&source.source)?;
    let mut paths = Vec::new();
    let root = if meta.is_dir() {
        walk(
            &source.source,
            &source.source,
            source.recursive,
            &mut paths,
            &mut 0,
        )?;
        source.source.as_path()
    } else {
        if !markdown(&source.source) {
            return Err(invalid("native memory files must have the .md extension"));
        }
        paths.push(source.source.clone());
        source.source.parent().expect("absolute source")
    };
    paths.sort();
    let mut files = Vec::new();
    let mut total = 0;
    for path in paths {
        no_links(&path)?;
        let before = regular(&path)?;
        if !before.is_file() || before.len() > MAX_FILE_BYTES {
            return Err(invalid(
                "native memory file exceeds 1 MiB or is no longer a regular file",
            ));
        }
        let name = path
            .strip_prefix(root)
            .expect("beneath root")
            .to_str()
            .ok_or_else(|| invalid("native memory filenames must be UTF-8"))?
            .replace(std::path::MAIN_SEPARATOR, "/");
        crate::store::grammar::validate_document_path(&name)?;
        let mut bytes = Vec::new();
        fs::File::open(&path)?
            .take(MAX_FILE_BYTES + 1)
            .read_to_end(&mut bytes)?;
        no_links(&path)?;
        let after = regular(&path)?;
        if bytes.len() as u64 != before.len()
            || before.len() != after.len()
            || before.modified()? != after.modified()?
        {
            return Err(invalid(
                "native memory file changed while reading; retry at the next hook",
            ));
        }
        total += bytes.len();
        if total > MAX_TOTAL_BYTES {
            return Err(invalid(
                "native memory scan exceeds 8 MiB; register smaller sources",
            ));
        }
        let content =
            String::from_utf8(bytes).map_err(|_| invalid("native memory files must be UTF-8"))?;
        files.push(File { name, content });
    }
    Ok(files)
}

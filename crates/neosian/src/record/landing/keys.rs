//! The destination records a home's `spool/landing/` holds (DESIGN §40.54
//! item 17.5): every `<key>.json` whose key is 32 lowercase hex digits, as
//! `destination::key` names a record. Beside them the same directory holds
//! `drops.json`, the slots, the locks and the notices' acknowledgement,
//! which are never taken for a record, so ending every block never removes
//! the home's drop counts.

use std::fs;
use std::io;
use std::path::Path;

/// Whether `stem` names a destination record.
pub fn is_key(stem: &str) -> bool {
    stem.len() == 32 && stem.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// The keys of the records under `landing`, sorted; none when it is missing.
pub fn keys(landing: &Path) -> io::Result<Vec<String>> {
    let listing = match fs::read_dir(landing) {
        Ok(listing) => listing,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut keys = Vec::new();
    for entry in listing {
        let name = entry?.file_name().to_string_lossy().into_owned();
        let key = name.strip_suffix(".json").filter(|stem| is_key(stem));
        keys.extend(key.map(str::to_string));
    }
    keys.sort();
    Ok(keys)
}

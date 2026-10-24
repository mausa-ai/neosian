//! The shell's TOML document and provider names (`_cli/config.py`, `providers.py`).

use crate::store::fileio;
use std::fs;
use std::io;
use std::path::Path;

pub const INVALID: &str = "not valid TOML: repair config.toml before changing update policy";
pub fn read(path: &Path) -> io::Result<toml::Table> {
    if !path.exists() {
        return Ok(toml::Table::new());
    }
    let text = fs::read_to_string(path)?;
    text.parse()
        .map_err(|_| io::Error::other(format!("{} is {INVALID}", path.display())))
}

pub fn write(path: &Path, document: &toml::Table) -> io::Result<()> {
    let parent = path.parent().expect("config under home");
    fileio::private_mkdir(parent)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    }
    let text = toml::to_string(document).map_err(io::Error::other)?;
    // The replacement is born private, tightening an existing file too.
    fileio::atomic_write(path, &text, false)
}

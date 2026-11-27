//! A successful rotation retires its prior device even when the installer
//! exits between the local commit and the best-effort network revocation.
use super::{
    host::{self, Result},
    local,
};
use std::{fs, path::Path};

pub const STAGED: &str = "retire-next.toml";
const RECEIPT: &str = "retire.toml";

pub fn activate(home: &Path) -> Result<()> {
    let staged = local::path(home, STAGED)?;
    if staged.exists() {
        fs::rename(staged, local::path(home, RECEIPT)?)
            .map_err(|_| "cannot activate credential retirement")?;
    }
    Ok(())
}

/// Retire the prior device, telling the person when the host did not confirm it.
pub fn drain(home: &Path, tell: impl FnOnce(&str) -> std::io::Result<()>) -> Result<()> {
    let Some(cred) = local::read_secret(home, RECEIPT)? else {
        return Ok(());
    };
    host::endpoint(&cred.host, &cred.revocation_endpoint, false)?;
    let confirmed = host::agent(5)
        .and_then(|agent| host::revoke(&agent, &cred.revocation_endpoint, &cred.token))
        .is_ok();
    if !confirmed {
        // The device's name and ID are host text, shown only cleaned.
        tell(&format!(
            "Could not confirm revocation of device {} ({}). Revoke it on the host's devices page.",
            super::output::host(&cred.name),
            super::output::host(&cred.token_id)
        ))
        .map_err(|_| "cannot display credential retirement result")?;
    }
    local::remove(home, RECEIPT)
}

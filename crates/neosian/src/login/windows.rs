//! Windows protects the complete credential, including its approved origin,
//! under the logged-in user's DPAPI key before any bytes reach a file.
use super::{device::Credential, host::Result, local};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};
use std::{path::Path, ptr};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::Cryptography::{
        CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN, CryptProtectData, CryptUnprotectData,
    },
};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Protected {
    dpapi_user_v1: String,
}

fn transform(bytes: &[u8], protect: bool) -> Result<Vec<u8>> {
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes.len().try_into().map_err(|_| "credential too large")?,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    // SAFETY: input borrows the caller's bytes for this call. DPAPI takes a
    // const input blob and allocates output with LocalAlloc on success.
    let success = unsafe {
        if protect {
            CryptProtectData(
                &input,
                ptr::null(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                ptr::null_mut(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        }
    };
    if success == 0 || output.pbData.is_null() {
        return Err("Windows could not access this user's protected credential".into());
    }
    // SAFETY: DPAPI returned cbData initialized bytes. Copy them before
    // releasing the allocation with the same Windows allocator.
    let value = unsafe {
        let data = std::slice::from_raw_parts_mut(output.pbData, output.cbData as usize);
        let copy = data.to_vec();
        if !protect {
            for byte in data {
                ptr::write_volatile(byte, 0);
            }
        }
        LocalFree(output.pbData.cast());
        copy
    };
    Ok(value)
}

pub fn write_secret(home: &Path, file: &str, credential: &Credential) -> Result<()> {
    let bytes = serde_json::to_vec(credential).map_err(|_| "cannot encode private credential")?;
    let envelope = Protected {
        dpapi_user_v1: STANDARD.encode(transform(&bytes, true)?),
    };
    local::write(home, file, &envelope)
}

pub fn read_secret(home: &Path, file: &str) -> Result<Option<Credential>> {
    let Some(envelope) = local::read::<Protected>(home, file)? else {
        return Ok(None);
    };
    let bytes = STANDARD
        .decode(envelope.dpapi_user_v1)
        .map_err(|_| "invalid protected credential")?;
    serde_json::from_slice(&transform(&bytes, false)?)
        .map(Some)
        .map_err(|_| "invalid protected credential".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn current_user_storage_round_trips_without_plaintext() {
        let home = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let issued = Credential {
            credential_format: 1,
            revocation_endpoint: "https://example.test/revoke".into(),
            host: "https://example.test/host".into(),
            account_id: "account".into(),
            token: "private-round-trip-bearer".into(),
            token_id: "one".into(),
            name: "Windows".into(),
            expires_at: None,
        };
        write_secret(&home, local::STAGED, &issued).unwrap();
        let staged = local::path(&home, local::STAGED).unwrap();
        assert!(
            !std::fs::read_to_string(&staged)
                .unwrap()
                .contains(&issued.token)
        );
        std::fs::rename(staged, local::path(&home, local::CREDENTIAL).unwrap()).unwrap();
        assert_eq!(
            read_secret(&home, local::CREDENTIAL)
                .unwrap()
                .unwrap()
                .token,
            issued.token
        );
        local::write(&home, local::CREDENTIAL, &issued).unwrap();
        assert!(read_secret(&home, local::CREDENTIAL).is_err());
        std::fs::remove_dir_all(home).unwrap();
    }
}

//! Reading an environment bearer requires trusted routing metadata too.
//! Refuse Windows homes/configuration that another unprivileged user can edit.
use super::host::Result;
use std::{os::windows::ffi::OsStrExt, path::Path, ptr};
use windows_sys::Win32::{
    Foundation::{CloseHandle, GENERIC_ALL, GENERIC_WRITE, LocalFree},
    Security::{
        ACCESS_ALLOWED_ACE, ACE_HEADER, ACL,
        Authorization::{GetNamedSecurityInfoW, SE_FILE_OBJECT},
        DACL_SECURITY_INFORMATION, EqualSid, GetAce, GetTokenInformation, IsWellKnownSid,
        OWNER_SECURITY_INFORMATION, PSID, TOKEN_QUERY, TOKEN_USER, TokenUser,
        WinBuiltinAdministratorsSid, WinCreatorOwnerSid, WinLocalSystemSid,
    },
    Storage::FileSystem::{
        DELETE, FILE_APPEND_DATA, FILE_DELETE_CHILD, FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA,
        FILE_WRITE_EA, WRITE_DAC, WRITE_OWNER,
    },
    System::{
        SystemServices::{ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE},
        Threading::{GetCurrentProcess, OpenProcessToken},
    },
};

const REFUSAL: &str =
    "managed login requires a Windows home writable only by its user, Administrators and SYSTEM";

pub fn protected(path: &Path) -> Result<()> {
    let name: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut owner = ptr::null_mut();
    let mut acl = ptr::null_mut();
    let mut descriptor = ptr::null_mut();
    // SAFETY: the wide string ends in NUL. The Windows allocation holds both
    // returned pointers and stays live until check_acl has inspected them.
    let result = unsafe {
        GetNamedSecurityInfoW(
            name.as_ptr(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            ptr::null_mut(),
            &mut acl,
            ptr::null_mut(),
            &mut descriptor,
        )
    };
    if result != 0 || descriptor.is_null() {
        return Err(REFUSAL.into());
    }
    // SAFETY: as above. LocalFree releases the entire descriptor once.
    let allowed = unsafe {
        let allowed = !owner.is_null() && !acl.is_null() && check_acl(owner, acl);
        LocalFree(descriptor);
        allowed
    };
    if allowed { Ok(()) } else { Err(REFUSAL.into()) }
}

unsafe fn check_acl(owner: PSID, acl: *mut ACL) -> bool {
    let mut process_token = ptr::null_mut();
    // SAFETY: this process owns its query handle. TOKEN_USER and the maximum
    // Windows SID fit in the aligned buffer; a size failure refuses access.
    unsafe {
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut process_token) == 0 {
            return false;
        }
        let mut buffer = [0usize; 128];
        let mut used = 0;
        let ok = GetTokenInformation(
            process_token,
            TokenUser,
            buffer.as_mut_ptr().cast(),
            std::mem::size_of_val(&buffer) as u32,
            &mut used,
        );
        CloseHandle(process_token);
        if ok == 0 {
            return false;
        }
        let user = (*buffer.as_ptr().cast::<TOKEN_USER>()).User.Sid;
        let trusted = |sid| {
            EqualSid(user, sid) != 0
                || IsWellKnownSid(sid, WinLocalSystemSid) != 0
                || IsWellKnownSid(sid, WinBuiltinAdministratorsSid) != 0
        };
        if !trusted(owner) {
            return false;
        }
        let write = GENERIC_ALL
            | GENERIC_WRITE
            | DELETE
            | FILE_APPEND_DATA
            | FILE_DELETE_CHILD
            | FILE_WRITE_ATTRIBUTES
            | FILE_WRITE_DATA
            | FILE_WRITE_EA
            | WRITE_DAC
            | WRITE_OWNER;
        for index in 0..(*acl).AceCount {
            let mut entry = ptr::null_mut();
            if GetAce(acl, index.into(), &mut entry) == 0 {
                return false;
            }
            match u32::from((*entry.cast::<ACE_HEADER>()).AceType) {
                ACCESS_DENIED_ACE_TYPE => continue,
                ACCESS_ALLOWED_ACE_TYPE => {
                    let ace = entry.cast::<ACCESS_ALLOWED_ACE>();
                    let sid = ptr::addr_of_mut!((*ace).SidStart).cast();
                    if (*ace).Mask & write != 0
                        && !trusted(sid)
                        && IsWellKnownSid(sid, WinCreatorOwnerSid) == 0
                    {
                        return false;
                    }
                }
                _ => return false,
            }
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_a_directory_writable_by_everyone() {
        let dir = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir(&dir).unwrap();
        protected(&dir).unwrap();
        // icacls takes a SID independently of the runner's display language.
        let result = std::process::Command::new("icacls")
            .arg(&dir)
            .args(["/grant", "*S-1-1-0:(M)"])
            .output()
            .unwrap();
        assert!(result.status.success());
        assert!(protected(&dir).is_err());
        std::fs::remove_dir(dir).unwrap();
    }
}

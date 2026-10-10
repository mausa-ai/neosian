//! Mounts: the tool layer's virtual path space (`memory/mounts.py`, DESIGN
//! §8, C7). A mount binds an opaque scope to a top-level directory name;
//! the store never sees one.

use crate::repr::repr;
use crate::store::error::MemoryError;
use crate::store::grammar::{parse_scope, validate_document_path};

/// The documents a lifecycle maintains, never a memory command.
const STATE_PREFIXES: [&str; 2] = ["messages/", "message-links/"];

/// One binding of a scope to a directory of the path space. `description`
/// is model-facing prose for the index; `edit_only` fixes the document
/// set while leaving every document editable.
#[derive(Debug)]
pub struct Mount {
    pub scope: String,
    pub path: String,
    pub read_only: bool,
    pub edit_only: bool,
    pub description: &'static str,
}

/// Why an invocation is refused before anything is built (exit 2): the
/// grammar's own message, or a scope or mount path outside its grammar.
#[derive(Debug)]
pub enum Refusal {
    Usage(String),
    Memory(MemoryError),
}

impl From<MemoryError> for Refusal {
    fn from(error: MemoryError) -> Self {
        Self::Memory(error)
    }
}

impl Mount {
    pub fn new(
        scope: &str,
        path: &str,
        (read_only, edit_only): (bool, bool),
        description: &'static str,
    ) -> Result<Self, MemoryError> {
        parse_scope(scope)?;
        validate_document_path(path)?;
        if path.contains('/') {
            return Err(MemoryError::path_invalid(
                path,
                "a mount path is a single segment",
            ));
        }
        Ok(Self {
            scope: scope.to_string(),
            path: path.to_string(),
            read_only,
            edit_only,
            description,
        })
    }

    /// A `--mount` token: `scope=...,path=...[,ro|,eo]`.
    pub fn parse(token: &str) -> Result<Self, Refusal> {
        let (mut scope, mut path, mut read_only, mut edit_only) = (None, None, false, false);
        for part in token.split(',') {
            match part.split_once('=') {
                None if part == "ro" && !edit_only => read_only = true,
                None if part == "eo" && !read_only => edit_only = true,
                Some(("scope", value)) if scope.is_none() => scope = Some(value),
                Some(("path", value)) if path.is_none() => path = Some(value),
                _ => {
                    return Err(Refusal::Usage(format!(
                        "malformed --mount {}: expected scope=...,path=...[,ro|,eo] \
                         (offending part: {})",
                        repr(token),
                        repr(part)
                    )));
                }
            }
        }
        let (Some(scope), Some(path)) = (scope, path) else {
            let token = repr(token);
            return Err(Refusal::Usage(format!(
                "--mount {token} needs both scope= and path="
            )));
        };
        Ok(Self::new(scope, path, (read_only, edit_only), "")?)
    }
}

/// A virtual path to its mount and the store's document path: slashes at
/// either end ignored, the first segment the mount, the rest (empty at a
/// mount's root) the document. The root itself names no single mount.
pub fn resolve<'a>(
    mounts: &'a [Mount],
    path: &'a str,
) -> Result<(&'a Mount, &'a str), MemoryError> {
    let stripped = path.trim_matches('/');
    if stripped.is_empty() {
        return Err(MemoryError::path_invalid(
            path,
            "the root is not a single mount",
        ));
    }
    let (first, rest) = stripped.split_once('/').unwrap_or((stripped, ""));
    if let Some(mount) = mounts.iter().find(|mount| mount.path == first) {
        return Ok((mount, rest));
    }
    let known: Vec<String> = mounts
        .iter()
        .map(|mount| format!("/{}", mount.path))
        .collect();
    let reason = format!(
        "no mount named {} (available mounts: {})",
        repr(first),
        known.join(", ")
    );
    Err(MemoryError::path_invalid(path, &reason))
}

/// Is this a document a lifecycle owns (the mailbox, its links)?
pub fn state_path(path: &str) -> bool {
    STATE_PREFIXES.iter().any(|prefix| path.starts_with(prefix))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn usage(token: &str) -> String {
        match Mount::parse(token) {
            Err(Refusal::Usage(message)) => message,
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_token_is_a_scope_a_path_and_one_policy() {
        let mount = Mount::parse("scope=user:me/proj:x,path=project,eo").unwrap();
        assert_eq!(
            (mount.scope.as_str(), mount.path.as_str()),
            ("user:me/proj:x", "project")
        );
        assert!(mount.edit_only && !mount.read_only);
        assert!(
            Mount::parse("ro,path=kb,scope=tenant:acme")
                .unwrap()
                .read_only
        );
        assert_eq!(
            usage("scope=user:me,path=a,ro,eo"),
            "malformed --mount 'scope=user:me,path=a,ro,eo': expected \
             scope=...,path=...[,ro|,eo] (offending part: 'eo')"
        );
        assert_eq!(
            usage("scope=user:me"),
            "--mount 'scope=user:me' needs both scope= and path="
        );
    }

    #[test]
    fn a_mount_path_is_one_segment_of_the_grammar() {
        let refused = |path: &str| match Mount::parse(&format!("scope=user:me,path={path}")) {
            Err(Refusal::Memory(error)) => error.message,
            other => panic!("{other:?}"),
        };
        assert_eq!(
            refused("/user"),
            "Invalid memory document path '/user': segments must be [A-Za-z0-9_.-]{1,128} \
             joined by '/'"
        );
        assert_eq!(
            refused("a/b"),
            "Invalid memory document path 'a/b': a mount path is a single segment"
        );
    }

    #[test]
    fn a_virtual_path_resolves_to_a_mount_and_a_document() {
        let mounts = [
            Mount::new("user:me", "user", (false, false), "").unwrap(),
            Mount::new("user:me/proj:x", "project", (false, false), "").unwrap(),
        ];
        let at = |path| resolve(&mounts, path).map(|(mount, rest)| (mount.path.as_str(), rest));
        assert_eq!(at("/project/notes/a").unwrap(), ("project", "notes/a"));
        assert_eq!(at("user/").unwrap(), ("user", ""));
        assert_eq!(
            at("/").unwrap_err().message,
            "Invalid memory document path '/': the root is not a single mount"
        );
        assert_eq!(
            at("/nope/x").unwrap_err().message,
            "Invalid memory document path '/nope/x': no mount named 'nope' (available mounts: \
             /user, /project)"
        );
        assert!(state_path("messages/1") && state_path("message-links/a/b"));
        assert!(!state_path("messages") && !state_path("notes/messages/1"));
    }
}

//! The host's import session routes (the L1c slice of the L1 plan): Begin,
//! commit, status and discard under `import_endpoint`, as the host contract
//! (`login-import.md`, "Contract between the CLI and the host") states
//! them. What a host returns is validated before it is used: the session
//! id before it is placed in a path, `transfer_url` against the origin the
//! person signed in to, the status against the six the contract names.

use serde_json::{Value, json};

use super::journal::{Journal, Opened, id_ok};
use super::link::{Answer, Link, Refusal, join};
use crate::login::host::{self, Result};
use crate::login::terminal::clean;
use crate::store::time::Stamp;

/// A session as Begin answered it.
#[derive(Debug)]
pub(crate) struct Begun {
    pub opened: Opened,
    pub limits: Option<Limits>,
    /// Units already stored: kind, name, sha256.
    pub received: Vec<(String, String, String)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Limits {
    pub unit_bytes: u64,
    pub units: u64,
    pub storage_bytes: u64,
    pub workspaces: u64,
}

/// Where the import of one source scope landed.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct Target {
    pub source: String,
    pub mount: String,
    pub profile: Option<String>,
}

/// `GET {import_endpoint}/{id}`.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub(crate) struct Progress {
    pub status: String,
    pub total: u64,
    pub done: u64,
    pub error_code: Option<String>,
    pub waiting_reason: Option<String>,
    pub resume_at: Option<String>,
    pub targets: Vec<Target>,
    pub failed_unit: Option<(String, String)>,
}

/// Why a route did not give what the session needs.
#[derive(Debug)]
pub(crate) enum Miss {
    Refused(Refusal),
    /// No answer, or one that is not the route's: it may have been applied.
    Lost,
}

/// A string the host sent about its own state, kept cleaned and bounded.
fn told(value: &Value, key: &str) -> Option<String> {
    text(value, key).map(|s| clean(&s, 512, false))
}

fn text(value: &Value, key: &str) -> Option<String> {
    value[key].as_str().map(str::to_string)
}

fn parse_begun(origin: &str, data: &Value) -> Option<Begun> {
    let id = text(data, "id").filter(|id| id_ok(id))?;
    let transfer_url = text(data, "transfer_url")?;
    host::endpoint(origin, &transfer_url, false).ok()?;
    let expires_at = text(data, "expires_at").filter(|t| Stamp::parse(t).is_ok());
    let limit = |key: &str| data["limits"][key].as_u64();
    let limits = (|| {
        Some(Limits {
            unit_bytes: limit("unit_bytes")?,
            units: limit("units")?,
            storage_bytes: limit("storage_bytes")?,
            workspaces: limit("workspaces")?,
        })
    })();
    let mut received = Vec::new();
    for unit in data["received"].as_array()? {
        received.push((
            text(unit, "kind")?,
            text(unit, "name")?,
            text(unit, "sha256")?,
        ));
    }
    Some(Begun {
        opened: Opened {
            id,
            transfer_url,
            expires_at,
        },
        limits,
        received,
    })
}

/// `POST {import_endpoint}` with `{}`: a new session (201) or this
/// machine's open one (200).
pub(crate) fn begin(
    link: &Link,
    origin: &str,
    endpoint: &str,
) -> Result<std::result::Result<Begun, Miss>> {
    Ok(match link.call("POST", endpoint, Some(b"{}"))? {
        Answer::Done(200 | 201, data) => {
            // A transfer URL off the signed-in origin is refused by name.
            if let Some(url) = data["transfer_url"].as_str() {
                host::endpoint(origin, url, false)?;
            }
            parse_begun(origin, &data).ok_or(Miss::Lost)
        }
        Answer::Done(..) | Answer::Lost => Err(Miss::Lost),
        Answer::Refused(refusal) => Err(Miss::Refused(refusal)),
    })
}

/// The commit body: every unit not left out, the host's own omissions
/// (never one this machine named itself) and the personal scope.
pub(crate) fn commit_body(journal: &Journal) -> Value {
    let units: Vec<Value> = journal
        .committed()
        .iter()
        .map(|u| json!({"kind": u.kind, "name": u.name, "sha256": u.sha256, "bytes": u.bytes}))
        .collect();
    let omitted: Vec<Value> = journal
        .host_omitted()
        .iter()
        .map(|l| json!({"kind": l.kind, "name": l.name, "reason": l.reason}))
        .collect();
    let personal = journal.personal.as_ref().filter(|p| {
        journal
            .committed()
            .iter()
            .any(|u| u.kind == "scope" && &u.name == *p)
    });
    json!({"units": units, "omitted": omitted, "personal": personal})
}

fn session_url(journal: &Journal, id: &str) -> String {
    join(&journal.import_endpoint, id)
}

/// `POST {import_endpoint}/{id}/commit`; Ok(()) on 202.
pub(crate) fn commit(
    link: &Link,
    journal: &Journal,
    id: &str,
) -> Result<std::result::Result<(), Miss>> {
    let body = commit_body(journal).to_string();
    let url = join(&session_url(journal, id), "commit");
    Ok(match link.call("POST", &url, Some(body.as_bytes()))? {
        Answer::Done(202 | 200, _) => Ok(()),
        Answer::Done(..) | Answer::Lost => Err(Miss::Lost),
        Answer::Refused(refusal) => Err(Miss::Refused(refusal)),
    })
}

const STATUSES: [&str; 6] = [
    "open",
    "queued",
    "running",
    "waiting",
    "succeeded",
    "failed",
];

fn parse_progress(data: &Value) -> Option<Progress> {
    let status = text(data, "status").filter(|s| STATUSES.contains(&s.as_str()))?;
    let mut targets = Vec::new();
    for target in data["targets"].as_array().into_iter().flatten() {
        let mount = text(target, "mount").filter(|m| m == "me" || m == "workspace")?;
        targets.push(Target {
            source: told(target, "source")?,
            mount,
            profile: told(target, "profile"),
        });
    }
    let failed = &data["failed_unit"];
    Some(Progress {
        status,
        total: data["total_units"].as_u64().unwrap_or(0),
        done: data["completed_units"].as_u64().unwrap_or(0),
        error_code: told(data, "error_code"),
        waiting_reason: told(data, "waiting_reason"),
        resume_at: told(data, "resume_at"),
        targets,
        failed_unit: told(failed, "kind").zip(told(failed, "name")),
    })
}

/// `GET {import_endpoint}/{id}`.
pub(crate) fn status(
    link: &Link,
    journal: &Journal,
    id: &str,
) -> Result<std::result::Result<Progress, Miss>> {
    Ok(match link.call("GET", &session_url(journal, id), None)? {
        Answer::Done(200, data) => parse_progress(&data).ok_or(Miss::Lost),
        Answer::Done(..) | Answer::Lost => Err(Miss::Lost),
        Answer::Refused(refusal) => Err(Miss::Refused(refusal)),
    })
}

/// `DELETE {import_endpoint}/{id}`: true when the host confirmed that
/// nothing of the session is left to discard (204).
pub(crate) fn discard(
    link: &Link,
    journal: &Journal,
    id: &str,
) -> Result<std::result::Result<(), Miss>> {
    Ok(
        match link.call("DELETE", &session_url(journal, id), None)? {
            Answer::Done(204 | 200, _) => Ok(()),
            Answer::Done(..) | Answer::Lost => Err(Miss::Lost),
            Answer::Refused(refusal) => Err(Miss::Refused(refusal)),
        },
    )
}

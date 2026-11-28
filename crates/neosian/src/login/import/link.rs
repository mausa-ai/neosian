//! Every request of an import at login (the L1c slice of the L1 plan). A
//! request goes only to a URL on the origin the person signed in to,
//! checked by `host::endpoint` before the machine bearer is attached, and
//! no redirect is followed (the login agent's own policy). Each answer is
//! typed, so the session branches on statuses and codes and never on
//! prose. A `429` is repeated after its `Retry-After`, within a bound.

use std::io::Read;
use std::time::Duration;

use serde_json::Value;

use crate::login::host::{self, Result};
use crate::login::terminal::clean;
use crate::record::landing::kept;

/// The longest `Retry-After` honoured; a longer one stops the import.
pub(crate) const WAIT_MAX: u64 = 60;
/// How many times one request is repeated after a `429`.
const RETRIES: u32 = 6;
/// A whole request, a part of up to 983,040 bytes included.
const SECONDS: u64 = 120;
const BODY: u64 = 4 * 1024 * 1024;

/// An answer outside 2xx.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Refusal {
    pub status: u16,
    /// The envelope's code (the host's `code`, or the wire's `error.code`),
    /// only when it has the error registry's grammar.
    pub code: Option<String>,
    /// The envelope's message, cleaned and cut, the credential removed.
    pub message: String,
    pub retry_after: Option<u64>,
    pub details: Value,
}

impl Refusal {
    pub(crate) fn is(&self, status: u16, code: &str) -> bool {
        self.status == status && self.code.as_deref() == Some(code)
    }
}

#[derive(Debug)]
pub(crate) enum Answer {
    /// 2xx with its status and JSON body (null when empty).
    Done(u16, Value),
    Refused(Refusal),
    /// No usable answer: the request may have been applied.
    Lost,
}

pub(crate) struct Link {
    agent: ureq::Agent,
    origin: String,
    bearer: String,
    /// How a wait passes; tests pass one that only records it.
    pub sleep: fn(Duration),
}

impl Link {
    pub(crate) fn new(origin: &str, bearer: &str) -> Result<Self> {
        Ok(Self {
            agent: host::agent(SECONDS)?,
            origin: origin.to_string(),
            bearer: bearer.to_string(),
            sleep: std::thread::sleep,
        })
    }

    /// One request, repeated after a `429` within the bound. Err only when
    /// `url` is not on the host's origin; then nothing was sent.
    pub(crate) fn call(&self, method: &str, url: &str, body: Option<&[u8]>) -> Result<Answer> {
        host::endpoint(&self.origin, url, false)?;
        let mut tries = 0;
        loop {
            let answer = self.once(method, url, body);
            let Answer::Refused(refusal) = &answer else {
                return Ok(answer);
            };
            let Some(wait) = retry_wait(refusal, tries) else {
                return Ok(answer);
            };
            (self.sleep)(Duration::from_secs(wait));
            tries += 1;
        }
    }

    fn once(&self, method: &str, url: &str, body: Option<&[u8]>) -> Answer {
        let bearer = format!("Bearer {}", self.bearer);
        let sent = match (method, body) {
            ("GET", _) => self.agent.get(url).header("Authorization", &bearer).call(),
            ("DELETE", _) => self
                .agent
                .delete(url)
                .header("Authorization", &bearer)
                .call(),
            (_, body) => self
                .agent
                .post(url)
                .header("Authorization", &bearer)
                .header("Content-Type", "application/json")
                .send(body.unwrap_or(b"{}")),
        };
        let Ok(mut response) = sent else {
            return Answer::Lost;
        };
        let status = response.status().as_u16();
        let retry_after = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.trim().parse::<u64>().ok());
        let mut bytes = Vec::new();
        let read = response
            .body_mut()
            .as_reader()
            .take(BODY + 1)
            .read_to_end(&mut bytes);
        let parsed = if bytes.is_empty() {
            Some(Value::Null)
        } else {
            serde_json::from_slice::<Value>(&bytes).ok()
        };
        if (200..300).contains(&status) {
            return match (read, parsed) {
                (Ok(_), Some(value)) if bytes.len() as u64 <= BODY => Answer::Done(status, value),
                _ => Answer::Lost,
            };
        }
        let data = parsed.unwrap_or(Value::Null);
        let envelope = if data["error"].is_object() {
            &data["error"]
        } else {
            &data
        };
        let secrets = [self.bearer.clone()];
        let code = envelope["code"]
            .as_str()
            .map(|c| kept::code(c, &secrets))
            .filter(|c| c != "unknown");
        let message = envelope["message"].as_str().unwrap_or_default();
        Answer::Refused(Refusal {
            status,
            code,
            message: clean(&kept::kept(message, &secrets), 320, false),
            retry_after,
            details: envelope["details"].clone(),
        })
    }
}

/// How long to wait before repeating a refused request: only a `429`, its
/// `Retry-After` (1 second when absent) up to `WAIT_MAX`, at most `RETRIES`
/// times. None: the refusal stands.
pub(crate) fn retry_wait(refusal: &Refusal, tries: u32) -> Option<u64> {
    let wait = refusal.retry_after.unwrap_or(1);
    (refusal.status == 429 && tries < RETRIES && wait <= WAIT_MAX).then_some(wait)
}

/// `base` and a path below it, without a doubled `/`.
pub(crate) fn join(base: &str, path: &str) -> String {
    format!("{}/{path}", base.trim_end_matches('/'))
}

//! The synchronous wire client for the Rust doors (DESIGN §18, §39.11).
//! The handshake precedes every opened-store lifetime; one pooled agent
//! serves its calls. No application retries, redirects or async runtime.

mod codec;
mod errors;
pub mod selection;
mod state;
#[cfg(test)]
mod tests;

use std::io;
use std::time::Duration;

use serde_json::{Value, json};
use ureq::tls::{PemItem, RootCerts, TlsConfig, parse_pem};

use crate::pyfmt::py_repr;
use crate::store::error::{Error, MemoryError};
use crate::wire::WIRE_VERSION;

pub struct RemoteStore {
    agent: ureq::Agent,
    base: String,
    authorization: String,
    client: Option<String>,
    // Retained verbatim, never inferred from the name of the backend. The
    // optional protocol methods arrive with their operator callers.
    capabilities: Value,
}

impl RemoteStore {
    pub fn connect(url: &str, token: &str) -> Result<Self, Error> {
        Self::with_timeout(url, token, Duration::from_secs(30))
    }

    fn with_timeout(url: &str, token: &str, timeout: Duration) -> Result<Self, Error> {
        let config = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .max_redirects(0)
            .max_redirects_will_error(false)
            .timeout_connect(Some(timeout))
            .timeout_resolve(Some(timeout))
            .timeout_per_read(Some(timeout))
            .timeout_per_write(Some(timeout))
            .tls_config(tls()?)
            .build();
        let mut remote = Self {
            agent: config.new_agent(),
            base: url.trim_end_matches('/').to_string(),
            authorization: format!("Bearer {token}"),
            client: None,
            capabilities: Value::Null,
        };
        let response = remote
            .agent
            .get(format!("{}/v1/capabilities", remote.base))
            .header("Authorization", &remote.authorization)
            .call()
            .map_err(driver)?;
        if response.status() == 401 {
            return Err(MemoryError::configuration(
                "the server rejected the bearer token — it must equal the server's NEOSIAN_SERVE_TOKEN",
            ).into());
        }
        let data = remote.answer(response, "capabilities", false)?;
        let wire = data.get("wire_version").unwrap_or(&Value::Null);
        if wire.as_u64() != Some(WIRE_VERSION) {
            return Err(MemoryError::configuration(format!(
                "wire version skew: the server speaks {}, this client speaks {WIRE_VERSION} — align the neosian versions on both ends (the wire is versioned with the library, DESIGN §18)",
                py_repr(wire)
            )).into());
        }
        remote.client = data
            .get("client")
            .and_then(Value::as_str)
            .map(str::to_string);
        remote.capabilities = data;
        Ok(remote)
    }

    fn call(&self, endpoint: &str, payload: Value) -> Result<Value, Error> {
        let response = self
            .agent
            .post(format!("{}/v1/{endpoint}", self.base))
            .header("Authorization", &self.authorization)
            .send_json(payload)
            .map_err(driver)?;
        self.answer(response, endpoint, true)
    }

    fn answer(
        &self,
        mut response: ureq::http::Response<ureq::Body>,
        endpoint: &str,
        envelope: bool,
    ) -> Result<Value, Error> {
        let status = response.status();
        // httpx has no 10 MB response cap. Read the body whole, preserving
        // transport errors as infrastructure failures, before decoding JSON.
        let mut bytes = Vec::new();
        io::Read::read_to_end(&mut response.body_mut().as_reader(), &mut bytes)?;
        let decoded = serde_json::from_slice::<Value>(&bytes);
        if !status.is_success() {
            if envelope
                && let Ok(data) = &decoded
                && data["error"].is_object()
            {
                return Err(errors::decode(&data["error"]));
            }
            return Err(driver(ureq::Error::StatusCode(status.as_u16())));
        }
        let data = decoded.map_err(|error| Error::Value(error.to_string()))?;
        if !data.is_object() {
            return Err(Error::Value(format!(
                "malformed wire response for '{}'",
                endpoint
            )));
        }
        Ok(data)
    }

    /// Python's `_follow`: limits count rows, projections advance by whole
    /// turns on the server, and a missing continuation is malformed data.
    fn follow(
        &self,
        endpoint: &str,
        mut payload: Value,
        items: &str,
        after: bool,
    ) -> Result<Vec<Value>, Error> {
        let (send, answer) = if after {
            ("after", "next_after")
        } else {
            ("cursor", "next_cursor")
        };
        let limit = payload["limit"].as_u64();
        let mut rows = Vec::new();
        loop {
            let data = self.call(endpoint, payload.clone())?;
            let page = codec::array(&data, items)?;
            let next = codec::key(&data, answer)?;
            rows.extend(page.iter().cloned());
            if next.is_null() || page.is_empty() {
                return Ok(rows);
            }
            let remaining = limit.map(|limit| limit.saturating_sub(rows.len() as u64));
            if remaining == Some(0) {
                return Ok(rows);
            }
            if (after && next.as_u64().is_none()) || (!after && !next.is_string()) {
                return Err(codec::malformed(answer));
            }
            payload[send] = next.clone();
            payload["limit"] = json!(remaining);
        }
    }
}

fn driver(error: ureq::Error) -> Error {
    io::Error::other(error).into()
}

/// Match httpx's custom CA-file door, with verification always enabled.
/// Otherwise rustls uses the Mozilla roots, as Python's certifi does.
fn tls() -> Result<TlsConfig, Error> {
    let mut config = TlsConfig::builder();
    if let Some(path) = std::env::var_os("SSL_CERT_FILE").filter(|s| !s.is_empty()) {
        let pem = std::fs::read(path)?;
        let mut certs = Vec::new();
        for item in parse_pem(&pem) {
            if let PemItem::Certificate(cert) = item.map_err(driver)? {
                certs.push(cert);
            }
        }
        if certs.is_empty() {
            return Err(driver(ureq::Error::Tls("No pem encoded cert found")));
        }
        config = config.root_certs(RootCerts::new_with_certs(&certs));
    }
    Ok(config.build())
}

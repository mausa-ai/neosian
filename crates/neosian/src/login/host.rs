//! Versioned host discovery. All destinations, including the state gateway,
//! share the explicitly chosen HTTPS origin. No request follows a redirect.
use crate::profile::Profile;
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use serde_json::Value;
use std::{collections::BTreeMap, time::Duration};

pub const DEFAULT: &str = "https://cloud.neosian.com/api/v1/machine/host";
pub type Result<T> = std::result::Result<T, String>;
pub type Response = ureq::http::Response<ureq::Body>;

pub fn endpoint(origin: &str, url: &str, allow_query: bool) -> Result<()> {
    fn parts(text: &str, query: bool) -> Result<(String, u16)> {
        let uri = text
            .parse::<ureq::http::Uri>()
            .map_err(|_| "invalid host URL")?;
        let host = uri.host().ok_or("expected an absolute HTTPS URL")?;
        if uri.scheme_str() != Some("https")
            || uri.authority().is_none_or(|a| a.as_str().contains('@'))
            || text.contains(['\\', '#'])
            || host.contains('%')
            || !query && uri.query().is_some()
        {
            return Err(
                "host URLs require HTTPS and must exclude credentials and fragments".into(),
            );
        }
        Ok((host.to_ascii_lowercase(), uri.port_u16().unwrap_or(443)))
    }
    if parts(origin, false)? != parts(url, allow_query)? {
        return Err("host advertised an endpoint on a different origin".into());
    }
    Ok(())
}

pub fn agent(seconds: u64) -> Result<ureq::Agent> {
    Ok(ureq::Agent::config_builder()
        .http_status_as_error(false)
        .max_redirects(0)
        .max_redirects_will_error(false)
        .timeout_global(Some(Duration::from_secs(seconds)))
        .tls_config(crate::remote::tls().map_err(|_| "cannot load trusted TLS roots")?)
        .build()
        .new_agent())
}

pub fn body<T: DeserializeOwned>(response: &mut Response) -> Result<T> {
    response
        .body_mut()
        .with_config()
        .limit(4 * 1024 * 1024)
        .read_json()
        .map_err(|_| "invalid or oversized host response".into())
}

pub fn status(response: &Response, allowed: &[u16]) -> Result<()> {
    let code = response.status().as_u16();
    if allowed.contains(&code) {
        return Ok(());
    }
    Err(match code {
        401 | 403 => "the host refused this credential; run neosian login".into(),
        _ => format!("host request failed (HTTP {code})"),
    })
}

pub fn transport<T>(_: T) -> String {
    "cannot contact the host over verified HTTPS".into()
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Discovery {
    pub host_format: u32,
    pub name: String,
    pub device_authorization_endpoint: String,
    pub token_endpoint: String,
    pub machine_endpoint: String,
    pub revocation_endpoint: String,
    pub workspaces_endpoint: Option<String>,
    pub scopes: Vec<String>,
}

pub fn display(text: &str, max: usize) -> bool {
    !text.trim().is_empty() && text.len() <= max && !text.chars().any(char::is_control)
}

impl Discovery {
    pub fn get(agent: &ureq::Agent, url: &str) -> Result<Self> {
        endpoint(url, url, false)?;
        let mut response = agent.get(url).call().map_err(transport)?;
        status(&response, &[200])?;
        let doc: Self = body(&mut response)?;
        doc.validate(url)?;
        Ok(doc)
    }

    pub fn validate(&self, url: &str) -> Result<()> {
        if self.host_format != 1
            || !display(&self.name, 128)
            || self.scopes.is_empty()
            || self.scopes.len() > 64
            || self.scopes.iter().any(|s| !display(s, 128))
        {
            return Err("invalid host discovery document or unsupported host_format".into());
        }
        for path in [
            &self.device_authorization_endpoint,
            &self.token_endpoint,
            &self.machine_endpoint,
            &self.revocation_endpoint,
        ] {
            endpoint(url, path, false)?;
        }
        if let Some(endpoint_url) = &self.workspaces_endpoint {
            endpoint(url, endpoint_url, false)?;
        }
        Ok(())
    }
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Account {
    pub id: String,
    pub email: String,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct Device {
    pub id: String,
    pub name: String,
}
#[derive(Deserialize)]
pub struct Machine {
    pub machine_format: u32,
    pub account: Account,
    pub device: Option<Device>,
    pub state_url: String,
    pub default_profile: String,
    #[serde(default = "default_refresh")]
    pub refresh_after_seconds: u64,
    #[serde(default)]
    pub notice: String,
    #[serde(default)]
    pub capture_locked_off: bool,
    pub profiles: BTreeMap<String, Profile>,
    #[serde(default)]
    pub repositories: Vec<Repository>,
}
fn default_refresh() -> u64 {
    3600
}

#[derive(Deserialize)]
pub struct Repository {
    pub key: String,
    pub profile: String,
}

pub fn fetch(
    agent: &ureq::Agent,
    host: &str,
    url: &str,
    bearer: &str,
    etag: Option<&str>,
) -> Result<Option<(Machine, Option<String>)>> {
    endpoint(host, url, false)?;
    let mut request = agent
        .get(url)
        .header("Authorization", format!("Bearer {bearer}"));
    if let Some(tag) = etag {
        request = request.header("If-None-Match", tag);
    }
    let mut response = request.call().map_err(transport)?;
    if response.status() == 304 && etag.is_some() {
        return Ok(None);
    }
    status(&response, &[200])?;
    let tag = response
        .headers()
        .get("etag")
        .and_then(|h| h.to_str().ok())
        .filter(|s| display(s, 512))
        .map(str::to_string);
    let doc: Machine = body(&mut response)?;
    endpoint(host, &doc.state_url, false)?;
    if doc.machine_format != 1
        || !display(&doc.account.id, 128)
        || doc
            .device
            .as_ref()
            .is_some_and(|d| !display(&d.id, 128) || !display(&d.name, 128))
        || !display(&doc.account.email, 320)
        || !doc.profiles.contains_key(&doc.default_profile)
        || doc.profiles.len() > 256
        || doc
            .repositories
            .iter()
            .any(|r| !doc.profiles.contains_key(&r.profile))
    {
        return Err("invalid machine document or unsupported machine_format".into());
    }
    for (name, profile) in &doc.profiles {
        crate::profile::validate::name(name)?;
        if crate::profile::encode(profile)?.len() > 262_144 {
            return Err("managed profile exceeds 256 KiB".into());
        }
    }
    Ok(Some((doc, tag)))
}

pub fn post(agent: &ureq::Agent, url: &str, payload: Value) -> Result<Response> {
    agent.post(url).send_json(&payload).map_err(transport)
}

pub fn revoke(agent: &ureq::Agent, url: &str, bearer: &str) -> Result<()> {
    let response = agent
        .post(url)
        .header("Authorization", format!("Bearer {bearer}"))
        .send_empty()
        .map_err(transport)?;
    status(&response, &[200, 204])
}

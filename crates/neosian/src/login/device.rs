use super::host::{self, Discovery, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    io::{IsTerminal, Write},
    time::{Duration, Instant},
};

#[derive(Serialize, Deserialize)]
pub struct Credential {
    pub credential_format: u32,
    pub host: String,
    pub revocation_endpoint: String,
    pub account_id: String,
    pub token: String,
    pub token_id: String,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<String>,
}

#[derive(Deserialize)]
struct Started {
    device_code: String,
    user_code: String,
    verification_uri: String,
    verification_uri_complete: String,
    expires_in: u64,
    interval: u64,
}

#[derive(Deserialize)]
struct Minted {
    secret: String,
    id: String,
    name: String,
    expires_at: Option<String>,
}

pub fn token_ok(token: &str) -> bool {
    !token.is_empty() && token.len() <= 8192 && token.bytes().all(|b| b.is_ascii_graphic())
}

fn open_browser(url: &str) {
    if !std::io::stdin().is_terminal() || std::env::var_os("SSH_CONNECTION").is_some() {
        return;
    }
    #[cfg(target_os = "macos")]
    let cmd = "open";
    #[cfg(target_os = "windows")]
    let cmd = "rundll32";
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let cmd = {
        if std::env::var_os("DISPLAY").is_none() && std::env::var_os("WAYLAND_DISPLAY").is_none() {
            return;
        }
        "xdg-open"
    };
    let mut command = std::process::Command::new(cmd);
    #[cfg(target_os = "windows")]
    command.arg("url.dll,FileProtocolHandler");
    use std::process::Stdio;
    let _ = command
        .arg(url)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .stdout(Stdio::null())
        .spawn();
}

pub fn authorize(
    agent: &ureq::Agent,
    host: &str,
    doc: &Discovery,
    json_output: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> Result<Credential> {
    let machine = std::env::var("HOSTNAME")
        .or_else(|_| std::env::var("COMPUTERNAME"))
        .ok()
        .filter(|s| host::display(s, 128))
        .unwrap_or_else(|| "this machine".into());
    let name: String = format!("neosian on {machine} ({})", std::env::consts::OS)
        .chars()
        .take(64)
        .collect();
    let mut response = host::post(
        agent,
        &doc.device_authorization_endpoint,
        json!({"name":name,"scopes":doc.scopes}),
    )?;
    host::status(&response, &[201, 200])?;
    let grant: Started = host::body(&mut response)?;
    host::endpoint(host, &grant.verification_uri, true)?;
    host::endpoint(host, &grant.verification_uri_complete, true)?;
    if !(1..=3600).contains(&grant.expires_in)
        || grant.interval == 0
        || !host::display(&grant.user_code, 64)
        || !token_ok(&grant.device_code)
    {
        return Err("invalid device authorization response".into());
    }
    writeln!(
        err,
        "Open {} and approve code {}.",
        grant.verification_uri_complete, grant.user_code
    )
    .map_err(|_| "cannot display verification code")?;
    if json_output {
        writeln!(out, "{}", json!({"event":"device_authorization","verification_uri":grant.verification_uri,
            "verification_uri_complete":grant.verification_uri_complete,"user_code":grant.user_code,"expires_in":grant.expires_in}))
            .and_then(|()| out.flush()).map_err(|_| "cannot display verification code")?;
    }
    open_browser(&grant.verification_uri_complete);
    let deadline = Instant::now() + Duration::from_secs(grant.expires_in);
    let mut interval = grant.interval;
    loop {
        std::thread::sleep(
            Duration::from_secs(interval).min(deadline.saturating_duration_since(Instant::now())),
        );
        if Instant::now() >= deadline {
            return Err("device code expired".into());
        }
        let mut response = host::post(
            agent,
            &doc.token_endpoint,
            json!({"device_code":grant.device_code}),
        )?;
        if response.status() == 200 {
            let data: Minted = host::body(&mut response)?;
            if !token_ok(&data.secret)
                || !host::display(&data.name, 128)
                || !host::display(&data.id, 128)
                || data
                    .expires_at
                    .as_ref()
                    .is_some_and(|s| crate::store::time::Stamp::parse(s).is_err())
            {
                return Err("invalid issued credential".into());
            }
            return Ok(Credential {
                credential_format: 1,
                host: host.into(),
                revocation_endpoint: doc.revocation_endpoint.clone(),
                account_id: String::new(),
                token: data.secret,
                token_id: data.id,
                name: data.name,
                expires_at: data.expires_at,
            });
        }
        if !response.status().is_client_error() {
            return Err("device poll failed".into());
        }
        let failure: Value = host::body(&mut response)?;
        match failure["code"].as_str().or(failure["error"].as_str()) {
            Some("authorization_pending") => (),
            Some("slow_down") => {
                interval = failure["details"]["interval"]
                    .as_u64()
                    .filter(|n| *n >= interval)
                    .unwrap_or(interval.saturating_add(5))
            }
            Some("access_denied") => return Err("device authorization denied".into()),
            Some("expired_token") => return Err("device code expired".into()),
            _ => return Err("device poll failed".into()),
        }
    }
}

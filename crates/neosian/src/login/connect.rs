//! Verify the credential and a complete machine document before changing the home.
use super::{
    Login, apply,
    device::{self, Credential},
    host::{self, Device, Discovery, Machine, Result},
    local::{self, Source, State},
    project,
};
use crate::{
    profile::files::{self, Project},
    store::time::Stamp,
};
use serde_json::{Value, json};
use std::{
    io::{self, Write},
    path::Path,
};

struct Session<'a> {
    observed: &'a Option<local::CaptureOff>,
    args: &'a Login,
    automatic: bool,
    home: &'a Path,
    url: &'a str,
    agent: ureq::Agent,
    discovery: Discovery,
    prior: Option<&'a State>,
}

impl Session<'_> {
    fn capture_preference(&self) -> Result<bool> {
        match self.args.capture.as_deref() {
            Some("off") => Ok(false),
            Some("on") => Ok(&local::off_revision(self.home)? == self.observed),
            _ => Ok(self.prior.is_none_or(|s| s.capture) && !local::capture_off(self.home)?),
        }
    }
    fn credential(
        &self,
        out: &mut impl Write,
        err: &mut impl Write,
    ) -> Result<(String, Source, Option<Credential>)> {
        if self.args.refresh {
            let state = self.prior.ok_or("no managed login to refresh")?;
            return Ok((state.token(self.home)?, state.credential_source, None));
        }
        if let Some(value) = local::environment() {
            if !device::token_ok(&value) {
                return Err("invalid machine credential".into());
            }
            return Ok((value, Source::Environment, None));
        }
        if self.args.non_interactive {
            return Err("non-interactive login requires NEOSIAN_LOGIN_TOKEN".into());
        }
        let cred = device::authorize(
            &self.agent,
            self.url,
            &self.discovery,
            self.args.json,
            out,
            err,
        )?;
        Ok((cred.token.clone(), Source::File, Some(cred)))
    }

    fn assignments(
        &self,
        bearer: &str,
        etag: Option<&str>,
    ) -> Result<Option<(Machine, Option<String>)>> {
        let fetched = host::fetch(
            &self.agent,
            self.url,
            &self.discovery.machine_endpoint,
            bearer,
            etag,
        )?;
        if let Some((doc, _)) = &fetched
            && self.prior.is_some_and(|s| s.account.id != doc.account.id)
        {
            return Err("a different account manages this home; use another NEOSIAN_HOME or logout --forget".into());
        }
        Ok(fetched)
    }

    fn checkout(
        &self,
        doc: &mut Machine,
        etag: &mut Option<String>,
        bearer: &str,
        err: &mut impl Write,
    ) -> Result<Option<Project>> {
        if self.args.refresh && self.args.workspace.is_none() {
            return Ok(None);
        }
        let create = self.discovery.workspaces_endpoint.as_deref();
        let choice = project::select(
            doc,
            self.args.project.as_deref().unwrap_or(Path::new(".")),
            self.args.workspace.as_deref(),
            !self.args.non_interactive && !self.args.json,
            create.is_some(),
            err,
        )?;
        let Some(mut p) = choice else { return Ok(None) };
        if p.profile == "new" {
            let endpoint = create.ok_or("this host does not offer workspace creation")?;
            p.profile = project::create(&self.agent, endpoint, bearer, &p.root)?;
            let (updated, tag) = self
                .assignments(bearer, None)?
                .ok_or("expected updated machine assignments")?;
            if doc.account.id != updated.account.id || !updated.profiles.contains_key(&p.profile) {
                return Err("new workspace is not assigned to the authenticated account".into());
            }
            *doc = updated;
            *etag = tag;
        }
        Ok(Some(p))
    }

    fn install(
        &self,
        token: &str,
        credential_source: Source,
        issued: &mut Option<Credential>,
        err: &mut impl Write,
    ) -> Result<Value> {
        let args = self.args;
        let capture = self.capture_preference()?;
        let endpoint = &self.discovery.machine_endpoint;
        let conditional = super::conditional::etag(self.prior, args, capture, endpoint);
        let Some((mut doc, mut etag)) = self.assignments(token, conditional)? else {
            let mut s = self
                .prior
                .expect("conditional refresh has an existing login")
                .clone();
            s.refreshed_at = Stamp::now().iso();
            s.discovery = self.discovery.clone();
            local::write(self.home, local::STATE, &s)?;
            return Ok(
                json!({"event":"refreshed","updated":false,"account":s.account,"device":s.device,"managed":s.managed}),
            );
        };
        let project = self.checkout(&mut doc, &mut etag, token, err)?;
        if let Some(c) = issued {
            c.account_id = doc.account.id.clone();
        }
        let capture = self.capture_preference()?;
        let mut notice = doc.notice.replace("\r\n", "\n");
        if notice.trim().is_empty()
            || notice.len() > 8192
            || notice.chars().any(|c| c.is_control() && c != '\n')
            || self.automatic && self.prior.is_none_or(|s| s.notice != notice)
        {
            notice.clear();
        }
        let permitted = capture && !doc.capture_locked_off && !notice.is_empty();
        let recording = apply::capture(&mut doc.profiles, permitted)?;
        if !self.automatic {
            if !notice.is_empty() {
                writeln!(err, "{notice}").map_err(|_| "cannot display capture notice")?;
            }
            err.flush().map_err(|_| "cannot display capture notice")?;
        }
        if notice.is_empty() {
            etag = None;
        }
        let device = doc
            .device
            .clone()
            .or_else(|| {
                issued.as_ref().map(|c| Device {
                    id: c.token_id.clone(),
                    name: c.name.clone(),
                })
            })
            .or_else(|| {
                self.prior
                    .filter(|_| args.refresh)
                    .and_then(|s| s.device.clone())
            });
        let state = State {
            login_format: 1,
            host: self.url.into(),
            discovery: self.discovery.clone(),
            account: doc.account.clone(),
            device,
            state_url: doc.state_url.clone(),
            default_profile: doc.default_profile.clone(),
            managed: doc.profiles.keys().cloned().collect(),
            installed: Default::default(),
            projects: vec![],
            capture,
            notice,
            etag,
            capabilities: Some(host::CAPABILITIES.into()),
            refreshed_at: Stamp::now().iso(),
            refresh_after_seconds: doc.refresh_after_seconds,
            credential_source,
            credential_digest: local::digest(token.as_bytes()),
            signed_out: false,
        };
        let removed = self
            .prior
            .iter()
            .flat_map(|s| s.managed.difference(&state.managed))
            .collect::<Vec<_>>();
        let report = json!({"event":if args.refresh {"refreshed"} else {"connected"},"updated":true,
            "account":state.account,"device":state.device,"default_profile":state.default_profile,
            "managed":state.managed,"removed":removed,"project":project,"capture":if recording {"per_profile"} else {"off"}});
        let old_unavailable = apply::install(
            self.home,
            self.prior,
            state,
            doc.profiles,
            project,
            issued.as_ref(),
        )?;
        if old_unavailable {
            writeln!(err,"The prior device credential was unavailable for revocation. Review that device on the host's devices page.")
                .map_err(|_| "cannot display credential retirement result")?;
        }
        Ok(report)
    }
}

pub(super) fn run(
    args: &Login,
    automatic: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> Result<Value> {
    let home = files::home()?;
    let disable = args.capture.as_deref() == Some("off");
    let enable = args.capture.as_deref() == Some("on");
    if disable {
        local::disable_capture(&home)?;
        writeln!(
            err,
            "Recording is off for managed workspaces on this machine."
        )
        .and_then(|()| err.flush())
        .map_err(|_| "cannot display local recording preference")?;
    }
    let observed = if enable {
        local::off_revision(&home)?
    } else {
        None
    };
    let _op = if automatic || disable {
        local::try_lock(&home)?
    } else {
        local::lock(&home)?
    };
    apply::recover(&home)?;
    if !automatic {
        super::retire::drain(&home, err)?;
    }
    let state = local::state(&home)?;
    let prior = state.as_ref();
    if args.refresh && prior.is_none() {
        return Err("no managed login to refresh".into());
    }
    if automatic && prior.is_some_and(|s| !s.due() || s.signed_out) {
        return Ok(Value::Null);
    }
    let url = args
        .host
        .as_deref()
        .or_else(|| prior.map(|s| s.host.as_str()))
        .unwrap_or(host::DEFAULT);
    if prior.is_some_and(|s| s.host != url) {
        return Err(
            "a different host manages this home; use another NEOSIAN_HOME or logout --forget"
                .into(),
        );
    }
    if !args.refresh && args.non_interactive && local::environment().is_none() {
        return Err("non-interactive login requires NEOSIAN_LOGIN_TOKEN".into());
    }
    let agent = host::agent(if automatic { 3 } else { 15 })?;
    let discovery = if automatic {
        prior
            .expect("only refresh can be automatic")
            .discovery
            .clone()
    } else {
        Discovery::get(&agent, url)?
    };
    let session = Session {
        args,
        observed: &observed,
        automatic,
        home: &home,
        url,
        agent,
        discovery,
        prior,
    };
    let (token, source, mut issued) = session.credential(out, err)?;
    let mut installed = session.install(&token, source, &mut issued, err);
    let unused = if installed.is_err() && !local::path(&home, local::JOURNAL)?.exists() {
        issued.as_ref()
    } else {
        None
    };
    if let Some(cred) = unused
        && host::revoke(
            &session.agent,
            &session.discovery.revocation_endpoint,
            &cred.token,
        )
        .is_err()
    {
        writeln!(err,"Could not revoke the unused credential. Review this machine on the host's devices page.").map_err(|_| "cannot display revocation result")?;
    }
    if let Ok(report) = &mut installed {
        super::unblock::changed(&home, prior, report);
        super::unblock::signed_in(&home, args);
        if !automatic {
            super::retire::drain(&home, err)?;
        }
        if enable {
            local::enable_capture(&home, &observed)?;
        }
        if local::capture_off(&home)? {
            report["capture"] = json!("off");
        }
        if !automatic && report["updated"] == true {
            let text = if report["capture"] == "off" {
                "Recording is off on this machine. Memory and skills remain available."
            } else {
                "Workspaces with shared recording save prompts and assistant replies. Turn recording off on this machine with neosian login --refresh --capture off."
            };
            writeln!(err, "{text}").map_err(|_| "cannot display capture policy")?;
        }
    }
    installed
}

pub fn refresh_if_due(home: &Path) -> bool {
    let Ok(Some(s)) = local::state(home) else {
        return false;
    };
    !s.signed_out
        && s.due()
        && run(
            &Login {
                refresh: true,
                ..Default::default()
            },
            true,
            &mut io::sink(),
            &mut io::sink(),
        )
        .is_ok()
}

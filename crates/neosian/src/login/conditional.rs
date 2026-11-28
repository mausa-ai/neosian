//! When a machine refresh may ask for a 304 (DESIGN §40.54 item 18). A host
//! may vary the machine document by the `Neosian-Capabilities` value, so an
//! ETag is reused only with the value its document was fetched with.
use super::{Login, host::CAPABILITIES, local::State};

/// The ETag to send in `If-None-Match`, if any. A plain refresh that keeps
/// the capture preference and the machine endpoint reuses the ETag of the
/// installed document only when that document was fetched with the value
/// this release sends. A state with another value, or one written before
/// the value was kept, gets one full fetch.
pub fn etag<'a>(
    prior: Option<&'a State>,
    args: &Login,
    capture: bool,
    endpoint: &str,
) -> Option<&'a str> {
    prior
        .filter(|s| {
            args.refresh
                && s.capture == capture
                && args.capture.is_none()
                && args.workspace.is_none()
                && s.discovery.machine_endpoint == endpoint
                && s.capabilities.as_deref() == Some(CAPABILITIES)
        })
        .and_then(|s| s.etag.as_deref())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::login::{
        host::{Account, Discovery},
        local::Source,
    };

    const MACHINE: &str = "https://example.test/machine";
    const OTHER: &str = "https://example.test/other";
    const TAG: &str = "\"1\"";

    fn state(capabilities: Option<&str>, etag: Option<&str>) -> State {
        let url = |path: &str| format!("https://example.test/{path}");
        State {
            login_format: 1,
            host: url("host"),
            discovery: Discovery {
                host_format: 1,
                name: "Test host".into(),
                device_authorization_endpoint: url("device"),
                token_endpoint: url("token"),
                machine_endpoint: MACHINE.into(),
                revocation_endpoint: url("logout"),
                workspaces_endpoint: None,
                import_endpoint: None,
                scopes: vec!["state:read".into()],
            },
            account: Account {
                id: "account".into(),
                email: "user@example.test".into(),
            },
            device: None,
            state_url: url("state"),
            default_profile: "personal".into(),
            managed: ["personal".to_string()].into(),
            installed: Default::default(),
            projects: vec![],
            capture: true,
            notice: "notice".into(),
            etag: etag.map(str::to_string),
            capabilities: capabilities.map(str::to_string),
            refreshed_at: "2026-11-27T00:00:00Z".into(),
            refresh_after_seconds: 3600,
            credential_source: Source::File,
            credential_digest: "digest".into(),
            signed_out: false,
        }
    }

    #[test]
    fn reuses_an_etag_only_with_the_value_it_was_fetched_with() {
        let (now, other, tag) = (Some(CAPABILITIES), Some("turn_records=0"), Some(TAG));
        // saved value, saved ETag, refresh, --capture, --workspace, capture,
        // machine endpoint, the ETag sent
        let rows = [
            (now, tag, true, None, None, true, MACHINE, tag),
            (other, tag, true, None, None, true, MACHINE, None),
            (None, tag, true, None, None, true, MACHINE, None),
            (now, None, true, None, None, true, MACHINE, None),
            (now, tag, false, None, None, true, MACHINE, None),
            (now, tag, true, Some("on"), None, true, MACHINE, None),
            (now, tag, true, None, Some("none"), true, MACHINE, None),
            (now, tag, true, None, None, false, MACHINE, None),
            (now, tag, true, None, None, true, OTHER, None),
        ];
        for (i, (value, kept, refresh, capture, workspace, keep, endpoint, sent)) in
            rows.into_iter().enumerate()
        {
            let prior = state(value, kept);
            let args = Login {
                refresh,
                capture: capture.map(str::to_string),
                workspace: workspace.map(str::to_string),
                ..Default::default()
            };
            assert_eq!(etag(Some(&prior), &args, keep, endpoint), sent, "row {i}");
        }
        let args = Login {
            refresh: true,
            ..Default::default()
        };
        assert_eq!(etag(None, &args, true, MACHINE), None);
    }

    #[test]
    fn an_older_state_without_the_value_reads_and_fetches_in_full() {
        let text = toml::to_string(&state(Some(CAPABILITIES), Some(TAG))).unwrap();
        let line = format!("capabilities = \"{CAPABILITIES}\"\n");
        assert!(text.contains(&line));
        let rows = [(text.clone(), Some(TAG)), (text.replace(&line, ""), None)];
        let args = Login {
            refresh: true,
            ..Default::default()
        };
        for (i, (encoded, sent)) in rows.into_iter().enumerate() {
            let decoded: State = toml::from_str(&encoded).unwrap();
            assert_eq!(decoded.etag.as_deref(), Some(TAG), "row {i}");
            assert_eq!(etag(Some(&decoded), &args, true, MACHINE), sent, "row {i}");
        }
        let unset = toml::to_string(&state(None, Some(TAG))).unwrap();
        assert!(!unset.contains("capabilities"));
    }
}

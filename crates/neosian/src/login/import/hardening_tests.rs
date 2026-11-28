//! L1e: one import at a time under the login lock (N1), and a snapshot of
//! store data only that is never a link (N3). The races are driven through
//! the seams between routing, the lock and the journal, never with sleeps.

use std::fs;
use std::path::Path;

use super::consent::Offer;
use super::entry::{Route, offered, resumed_at, route_at};
use super::finish::finishing;
use super::journal::{self, Journal, Stage, Unit};
use super::snapshot::{self, DIRECTORY, Fault};
use crate::login::Login;
use crate::login::host::{Account, Discovery};
use crate::login::local::{self, Source, State};
use crate::login::output::{Style, View, say};
use crate::store::fileio::private_mkdir;
use crate::testing::Scratch;

const HOST: &str = "https://example.test/host";
const DOC: &str = "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-10-01T00:00:00Z'\nupdated_at: '2026-10-01T00:00:00Z'\nactor: seed\n---\nkept\n";

fn url(path: &str) -> String {
    format!("https://example.test/{path}")
}

/// A machine login whose installed credential has `digest`.
fn signed_in(home: &Path, digest: &str) {
    let state = State {
        login_format: 1,
        host: HOST.into(),
        discovery: Discovery {
            host_format: 1,
            name: "Test host".into(),
            device_authorization_endpoint: url("device"),
            token_endpoint: url("token"),
            machine_endpoint: url("machine"),
            revocation_endpoint: url("logout"),
            workspaces_endpoint: None,
            import_endpoint: Some(url("import-sessions")),
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
        capture: false,
        notice: String::new(),
        etag: None,
        capabilities: None,
        refreshed_at: "2026-11-28T00:00:00Z".into(),
        refresh_after_seconds: 3600,
        credential_source: Source::File,
        credential_digest: digest.into(),
        signed_out: false,
        imported: Default::default(),
    };
    private_mkdir(&home.join("login")).unwrap();
    local::write(home, local::STATE, &state).unwrap();
}

fn waiting(home: &Path, stage: Stage) -> Journal {
    let journal = Journal {
        journal_format: 1,
        host: HOST.into(),
        import_endpoint: url("import-sessions"),
        account: "account".into(),
        credential: "approved".into(),
        snapshot: "snapshot".into(),
        begun_at: "2026-11-28T00:00:00Z".into(),
        personal: None,
        units: vec![Unit {
            kind: "scope".into(),
            name: "user:me".into(),
            sha256: "0".repeat(64),
            bytes: 1,
        }],
        local: Vec::new(),
        session: None,
        omitted: Vec::new(),
        refused: Vec::new(),
        stage,
        targets: Vec::new(),
    };
    private_mkdir(&home.join(DIRECTORY)).unwrap();
    journal::write(home, &journal).unwrap();
    journal
}

#[test]
fn an_accepted_offer_binds_its_journal_to_the_credential_it_approved() {
    let home = Scratch::new("l1e-credential");
    home.plant("user%3Ame/documents/a.md", DOC);
    // Another login installed its own credential of the same account.
    signed_in(home.path(), "installed-by-another-login");
    let offer = Offer {
        home: home.path().to_path_buf(),
        host: HOST.into(),
        endpoint: url("import-sessions"),
        account: None,
        snapshot: snapshot::take(home.path()).unwrap(),
        units: Vec::new(),
        local: Vec::new(),
        armed: false,
        held: Some(local::lock(home.path()).unwrap()),
        credential: Some("approved".into()),
    };
    let mut ui = View::new(Vec::new(), Style::PLAIN);
    let refused = offered(offer, false, &mut ui).unwrap_err();
    assert_eq!(refused, say("import_other_credential"));
    assert!(journal::read(home.path()).unwrap().is_none());
}

#[test]
fn a_resume_acts_on_the_journal_under_the_lock_not_the_routed_one() {
    let home = Scratch::new("l1e-reread");
    waiting(home.path(), Stage::Transfer);
    let args = Login {
        import: true,
        ..Default::default()
    };
    let Route::Resume(routed) = route_at(&args, home.path()).unwrap() else {
        panic!("a waiting import is resumed");
    };
    // Another login ended the import while this one waited for the lock.
    assert!(snapshot::dispose(home.path()).unwrap());
    let mut ui = View::new(Vec::new(), Style::PLAIN);
    let (answer, code, _) = resumed_at(&args, *routed, home.path(), &mut ui).unwrap();
    assert_eq!((answer["status"].as_str(), code), (Some("gone"), 0));
}

#[test]
fn a_login_refuses_an_import_that_began_after_it_was_routed() {
    let home = Scratch::new("l1e-routed");
    let plain = Login::default();
    assert!(matches!(route_at(&plain, home.path()), Ok(Route::Login)));
    waiting(home.path(), Stage::Transfer);
    let refused = finishing(&plain, home.path(), HOST).err().unwrap();
    assert!(refused.contains("is waiting to finish"), "{refused}");
    let refresh = Login {
        refresh: true,
        ..Default::default()
    };
    assert!(finishing(&refresh, home.path(), HOST).unwrap().is_none());
}

#[test]
fn the_copy_admits_only_what_the_store_reads() {
    let home = Scratch::new("l1e-admission");
    home.plant("user%3Ame/documents/a.md", DOC);
    home.plant("user%3Ame/redactions.jsonl", "");
    home.plant("user%3Ame/proj%3Ax/documents/b.md", DOC);
    home.plant("user%3Ame/notes/n.txt", "foreign");
    home.plant("backup%3Aold%3Acopy/user%3Ame/documents/a.md", DOC);
    let taken = snapshot::take(home.path()).unwrap();
    let root = taken.root();
    for kept in [
        "user%3Ame/documents/a.md",
        "user%3Ame/redactions.jsonl",
        "user%3Ame/proj%3Ax/documents/b.md",
    ] {
        assert!(root.join(kept).is_file(), "{kept}");
    }
    for left in ["user%3Ame/notes", "backup%3Aold%3Acopy"] {
        assert!(!root.join(left).exists(), "{left}");
    }
}

#[cfg(unix)]
#[test]
fn a_linked_store_or_manifest_is_refused_before_it_is_opened() {
    use std::os::unix::fs::symlink;
    let home = Scratch::new("l1e-links");
    home.plant("user%3Ame/documents/a.md", DOC);
    let taken = snapshot::take(home.path()).unwrap();
    let outside = Scratch::new("l1e-links-outside");
    for name in ["store", "manifest.json"] {
        let path = taken.path.join(name);
        let elsewhere = outside.path().join(name);
        fs::rename(&path, &elsewhere).unwrap();
        symlink(&elsewhere, &path).unwrap();
        match snapshot::open(home.path()) {
            Err(Fault::Unsupported { path, what }) => {
                assert_eq!((path.as_str(), what), (name, "a symbolic link"));
            }
            other => panic!("{name}: {other:?}"),
        }
        fs::remove_file(&path).unwrap();
        fs::rename(&elsewhere, &path).unwrap();
    }
    assert!(snapshot::open(home.path()).unwrap().is_some());
}

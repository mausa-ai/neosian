//! The state read strictly, and destinations canonical and without secrets
//! (rulings D2 and E3).

use std::fs;
use std::io;

use super::super::state::{Destination, State, describe, public_url};
use super::state;
use crate::remote::selection::Selection;
use crate::testing::Scratch;

fn remote(url: &str, token: &str) -> Destination {
    describe(&Selection::Remote {
        url: url.into(),
        token: token.into(),
    })
}

#[test]
fn the_state_is_read_strictly() {
    let scratch = Scratch::new("landing-strict");
    let file = scratch.path().join("1.state");
    let written = state("{}", 7);
    written.write(&file).unwrap();
    assert_eq!(State::read(&file).unwrap(), written);
    let text = fs::read_to_string(&file).unwrap();
    for broken in [
        text.replace(",\"parent\":null", ""),
        text.replace("\"draft\":", "\"extra\":1,\"draft\":"),
        text.replace("\"root\":\"/store\"", "\"root\":\"/store\",\"token\":\"x\""),
        text.replace("\"kind\":\"file\"", "\"kind\":\"elsewhere\""),
    ] {
        assert_ne!(broken, text);
        fs::write(&file, broken).unwrap();
        let refused = State::read(&file).unwrap_err();
        assert_eq!(refused.kind(), io::ErrorKind::InvalidData);
    }
}

#[test]
fn a_destination_names_a_credential_only_by_its_digest() {
    // The digests are computed by hand (Python's hashlib), not by `digest`:
    // SHA-256 of `["http://127.0.0.1:9","secret-token"]` and of the DSN.
    assert_eq!(
        remote("http://127.0.0.1:9/", "secret-token"),
        Destination::Remote {
            url: "http://127.0.0.1:9".into(),
            credential_sha256: "964937d08ea1233233eebcc651ab402309e3c4cc2d2b8e68359d4a75626ff17b"
                .into(),
        }
    );
    let postgres = describe(&Selection::Postgres {
        dsn: "postgresql://u:secret-password@h/db".into(),
        schema: "neosian".into(),
    });
    assert_eq!(
        postgres,
        Destination::Postgres {
            schema: "neosian".into(),
            dsn_sha256: "1b01e843cdc11014a3299578b5c387ea35c4079641214a8d7c353540db1c6c0f".into(),
        }
    );
    let mut written = state("{}", 0);
    for destination in [remote("http://127.0.0.1:9/", "secret-token"), postgres] {
        written.destination = Some(destination);
        let text = written.text();
        assert!(!text.contains("secret"), "{text}");
    }
}

#[test]
fn a_trailing_slash_names_the_same_destination_and_a_token_another() {
    assert_eq!(remote("http://h:8000", "t"), remote("http://h:8000/", "t"));
    assert_eq!(
        remote("http://h:8000/base//", "t"),
        remote("http://h:8000/base", "t")
    );
    assert_ne!(remote("http://h:8000", "t"), remote("http://h:8000", "u"));
    assert_ne!(remote("http://h:8000", "t"), remote("http://h:8001", "t"));
}

#[test]
fn a_url_s_userinfo_and_query_are_never_stored_but_still_bind() {
    let typed = "https://someone:secret-pass@h.example:8443/base/?key=secret-query#frag";
    let Destination::Remote { url, .. } = remote(typed, "t") else {
        panic!("a remote destination");
    };
    assert_eq!(url, "https://h.example:8443/base");
    assert_eq!(public_url("http://h"), "http://h");
    assert_eq!(public_url("http://a@b@h/"), "http://h");
    let mut written = state("{}", 0);
    written.destination = Some(remote(typed, "t"));
    assert!(!written.text().contains("secret"), "{}", written.text());
    // Another userinfo is another credential at the same shown URL.
    let other = "https://another:pass@h.example:8443/base/?key=secret-query#frag";
    assert_ne!(remote(typed, "t"), remote(other, "t"));
}

#[cfg(unix)]
#[test]
fn a_root_reached_through_a_symlink_is_its_target() {
    let scratch = Scratch::new("landing-symlink");
    let target = scratch.path().join("store");
    fs::create_dir_all(&target).unwrap();
    let link = scratch.path().join("link");
    std::os::unix::fs::symlink(&target, &link).unwrap();
    let through = describe(&Selection::File(link.join(".")));
    assert_eq!(through, describe(&Selection::File(target.clone())));
    let canonical = fs::canonicalize(&target).unwrap();
    assert_eq!(
        through,
        Destination::File {
            root: canonical.to_string_lossy().into_owned()
        }
    );
    // A root not created yet shows as its existing ancestor's target and the
    // rest, the same as once it exists.
    let later = describe(&Selection::File(link.join("later")));
    assert_eq!(
        later,
        Destination::File {
            root: canonical.join("later").to_string_lossy().into_owned()
        }
    );
    fs::create_dir(target.join("later")).unwrap();
    assert_eq!(describe(&Selection::File(link.join("later"))), later);
}

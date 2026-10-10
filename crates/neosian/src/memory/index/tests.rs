//! The tiers against `memory/index.py`'s own rendering of the same
//! listings: each expectation below is that renderer's output, verbatim.

use super::*;
use crate::store::time::Stamp;

const FULL: &str = "## /user — durable facts\n- /user/notes/a\n- /user/notes/b\n- /user/notes/c\n- /user/profile\n- /user/zeta\n\n## /project (read-only)\n- /project/decisions\n- /project/facts (redacted)\n- /project/skills/deploy\n- /project/skills/release\n\n## /kb (edit-only)\n(empty)";

fn entry(path: &str, minute: u32, redacted: bool) -> Entry {
    Entry {
        path: path.to_string(),
        updated_at: Stamp::parse(&format!("2026-01-01T00:{minute:02}:00Z")).unwrap(),
        redacted,
    }
}

fn mounts() -> [Mount; 3] {
    [
        Mount::new("user:me", "user", (false, false), "durable facts").unwrap(),
        Mount::new("user:me/proj:x", "project", (true, false), "").unwrap(),
        Mount::new("tenant:acme", "kb", (false, true), "").unwrap(),
    ]
}

fn listings(mounts: &[Mount; 3]) -> Vec<Listing<'_>> {
    let user = [
        ("notes/a", 1),
        ("notes/b", 9),
        ("notes/c", 3),
        ("profile", 8),
        ("zeta", 2),
    ];
    let project = [
        entry("decisions", 7, false),
        entry("facts", 4, true),
        entry("skills/deploy", 6, false),
        entry("skills/release", 5, false),
    ];
    vec![
        (
            &mounts[0],
            user.map(|(path, minute)| entry(path, minute, false)).into(),
        ),
        (&mounts[1], project.into()),
        (&mounts[2], Vec::new()),
    ]
}

#[test]
fn under_the_budget_every_document_keeps_its_line() {
    let mounts = mounts();
    assert_eq!(blocks(&listings(&mounts), None), FULL);
    assert_eq!(chars(FULL), 254);
}

#[test]
fn over_it_the_newest_stay_and_the_rest_fold_by_directory() {
    let mounts = mounts();
    let listings = listings(&mounts);
    for (budget, warm) in [
        (
            253,
            "## /user — durable facts\n- /user/notes/b\n- /user/profile\n- /user/notes/ (2 documents)\n- /user/ (1 more)\n\n## /project (read-only)\n- /project/decisions\n- /project/skills/ (2 documents)\n- /project/ (1 more)\n\n## /kb (edit-only)\n(empty)",
        ),
        (
            230,
            "## /user — durable facts\n- /user/notes/b\n- /user/profile\n- /user/notes/ (2 documents)\n- /user/ (1 more)\n\n## /project (read-only)\n- /project/skills/ (2 documents)\n- /project/ (2 more)\n\n## /kb (edit-only)\n(empty)",
        ),
        (
            200,
            "## /user — durable facts\n- /user/notes/b\n- /user/notes/ (2 documents)\n- /user/ (2 more)\n\n## /project (read-only)\n- /project/skills/ (2 documents)\n- /project/ (2 more)\n\n## /kb (edit-only)\n(empty)",
        ),
    ] {
        assert_eq!(
            tiered(&listings, budget).as_deref(),
            Some(warm),
            "budget {budget}"
        );
    }
    assert_eq!(tiered(&listings, 160), None);
}

#[test]
fn the_floor_collapses_every_mount_to_its_total() {
    let mounts = mounts();
    assert_eq!(
        floor(&listings(&mounts)),
        "## /user — durable facts\n(5 documents — view /user/ to list)\n\n## /project (read-only)\n(4 documents — view /project/ to list)\n\n## /kb (edit-only)\n(empty)"
    );
}

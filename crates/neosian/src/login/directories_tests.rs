//! The map's target validation and the directory rule (L1d).

use std::collections::{BTreeMap, BTreeSet};
use std::fs;

use serde_json::json;

use super::{Finish, Imported, map, this_directory};
use crate::login::import::Target;
use crate::profile::files::Project;
use crate::testing::Scratch;

fn target(source: &str, mount: &str, profile: Option<&str>) -> Target {
    Target {
        source: source.into(),
        mount: mount.into(),
        profile: profile.map(str::to_string),
    }
}

fn finish(targets: Vec<Target>) -> Finish {
    let scopes = ["user:c", "user:c/proj:a", "user:c/proj:b", "user:c/proj:d"];
    Finish {
        account: "account".into(),
        scopes: scopes.iter().map(|s| s.to_string()).collect(),
        targets,
        directory: ".".into(),
    }
}

#[test]
fn each_target_is_checked_against_the_commit_and_the_assignments() {
    let assigned = |p: &str| ["personal", "w-a", "w-d", "w-e"].contains(&p);
    let made = finish(vec![
        target("user:c", "me", None),
        target("user:c/proj:a", "workspace", Some("w-a")),
        target("user:c/proj:b", "workspace", Some("w-gone")),
        target("user:c/proj:x", "workspace", Some("w-a")),
        target("user:c/proj:d", "workspace", None),
        target("user:c/proj:d", "workspace", Some("w-d")),
        target("user:c/proj:d", "workspace", Some("w-e")),
    ]);
    let (mapped, left) = map(&made, assigned);
    let a = ("user:c/proj:a".to_string(), "w-a".to_string());
    assert_eq!(mapped, BTreeMap::from([a]));
    let reasons: Vec<_> = left
        .iter()
        .map(|l| (l["source"].clone(), l["reason"].clone()))
        .collect();
    assert_eq!(
        reasons,
        [
            (json!("user:c/proj:b"), json!("unassigned")),
            (json!("user:c/proj:x"), json!("unknown")),
            (json!("user:c/proj:d"), json!("unnamed")),
            (json!("user:c/proj:d"), json!("twice")),
        ]
    );
}

#[test]
fn a_registered_binding_is_kept_and_only_the_exact_directory_is_bound() {
    let scratch = Scratch::new("login-directories");
    let demo = scratch.path().join("a").join("demo");
    let below = demo.join("src");
    fs::create_dir_all(&below).unwrap();
    let demo = demo.canonicalize().unwrap();
    let Ok(scope) = crate::home::project_scope(&demo) else {
        return; // no login name in this environment: nothing derives a scope
    };
    let scopes = BTreeMap::from([(scope.clone(), "w-demo".to_string())]);
    let manual = Project {
        root: demo.clone(),
        profile: "personal".into(),
    };
    let mut bound = BTreeMap::new();
    let kept = this_directory(
        &demo,
        &scopes,
        std::slice::from_ref(&manual),
        None,
        &mut bound,
    );
    assert_eq!(
        (kept["bound"].clone(), kept["profile"].clone()),
        (json!("kept"), json!("personal"))
    );
    let chosen = this_directory(&below, &scopes, &[], Some(&manual), &mut bound);
    assert_eq!(chosen["bound"], "kept");
    assert!(bound.is_empty());
    let made = this_directory(&demo, &scopes, &[], None, &mut bound);
    assert_eq!(
        (made["bound"].clone(), made["scope"].clone()),
        (json!("import"), json!(scope))
    );
    let key = demo.to_str().unwrap().to_string();
    assert_eq!(bound, BTreeMap::from([(key.clone(), "w-demo".to_string())]));
    // Below the bound directory, its own scope decides: none was imported.
    let none = this_directory(&below, &scopes, &[], None, &mut bound);
    assert_eq!(
        (none["bound"].clone(), none["profile"].clone()),
        (json!("none"), json!(null))
    );

    let imported = Imported {
        scopes,
        directories: BTreeMap::from([(key, "w-demo".to_string())]),
    };
    let managed: BTreeSet<String> = ["personal".to_string(), "w-demo".to_string()].into();
    assert_eq!(imported.select(&managed, &demo), Some("w-demo"));
    assert_eq!(imported.select(&managed, &below), None);
    // Another directory with the same name shares the historical scope.
    let twin = scratch.path().join("b").join("demo");
    fs::create_dir_all(&twin).unwrap();
    assert_eq!(imported.select(&managed, &twin), Some("w-demo"));
    // A profile no longer assigned selects nothing, and a refresh drops it.
    let stale: BTreeSet<String> = ["personal".to_string()].into();
    assert_eq!(imported.select(&stale, &demo), None);
    assert!(imported.kept(|p| stale.contains(p)).is_empty());
    assert_eq!(imported.kept(|p| managed.contains(p)), imported);
}

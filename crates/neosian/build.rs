//! Repository builds read canonical assets; published crates carry a snapshot.

use std::{env, path::PathBuf};

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let repository = root.join("../../assets");
    let assets = if repository.is_dir() {
        repository
    } else {
        root.join("assets")
    };
    let assets = assets.canonicalize().expect("missing packaged assets");
    println!("cargo:rerun-if-changed={}", assets.display());
    println!("cargo:rustc-env=NEOSIAN_ASSETS={}", assets.display());
    let docs = assets.join("docs");
    let manifest = std::fs::read_to_string(docs.join("topics.txt")).expect("docs manifest");
    let topics: Vec<_> = manifest.lines().filter(|line| !line.is_empty()).collect();
    let mut listed = std::collections::BTreeSet::new();
    let mut generated = String::from("const PAGES: &[(&str, &str)] = &[\n");
    for topic in topics {
        assert!(
            topic
                .bytes()
                .all(|ch| ch.is_ascii_lowercase() || ch == b'-'),
            "invalid docs topic"
        );
        assert!(listed.insert(format!("{topic}.md")), "duplicate docs topic");
        let path = docs.join(format!("{topic}.md"));
        let text = std::fs::read_to_string(&path).expect("listed page missing");
        assert!(
            text.starts_with("---\n") && text.contains("\ntitle:") && text.contains("\nsummary:"),
            "page metadata missing"
        );
        generated.push_str(&format!(
            "({topic:?}, include_str!({:?})),\n",
            path.to_str().unwrap()
        ));
    }
    let present: std::collections::BTreeSet<_> = std::fs::read_dir(&docs)
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_str().unwrap().to_string())
        .filter(|name| name.ends_with(".md"))
        .collect();
    assert_eq!(listed, present, "docs manifest must list every page");
    generated.push_str("];\n");
    std::fs::write(
        PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("docs_pages.rs"),
        generated,
    )
    .unwrap();
}

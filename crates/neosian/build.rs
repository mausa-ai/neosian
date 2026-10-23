//! Repository builds read the wheel's assets; published crates carry a snapshot.

use std::{env, path::PathBuf};

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let repository = root.join("../../neosian/assets");
    let assets = if repository.is_dir() {
        repository
    } else {
        root.join("assets")
    };
    let assets = assets.canonicalize().expect("missing packaged assets");
    println!("cargo:rerun-if-changed={}", assets.display());
    println!("cargo:rustc-env=NEOSIAN_ASSETS={}", assets.display());
}

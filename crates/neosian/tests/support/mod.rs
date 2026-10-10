//! What the binary's integration tests share: a scratch home, and the
//! literal `neosian memory` over it.

#[path = "../../src/testing.rs"]
mod testing;

use std::path::Path;
use std::process::{Command, Output};

pub use testing::Scratch;

/// `neosian memory <args> --scope user:me --root <home>`, the environment
/// naming no store of its own.
pub fn command(home: &Path, args: &[&str]) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_neosian"));
    command
        .arg("memory")
        .args(args)
        .args(["--scope", "user:me", "--root"])
        .arg(home)
        .env_remove("NEOSIAN_SCOPE")
        .env_remove("NEOSIAN_POSTGRES_DSN");
    command
}

pub fn memory(home: &Path, args: &[&str]) -> Output {
    command(home, args).output().expect("the binary runs")
}

pub fn text(bytes: Vec<u8>) -> String {
    String::from_utf8(bytes).unwrap()
}

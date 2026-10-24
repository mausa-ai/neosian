//! Model-bearing commands keep their execution in the Python shell.

use std::ffi::OsString;
use std::io::{self, Write};

use serde_json::json;

const GUIDANCE: &str = "needs the Python shell: `uv tool install neosian`";

pub fn requested(args: &[OsString]) -> bool {
    let verb = args.get(1).and_then(|arg| arg.to_str());
    matches!(verb, Some("chat" | "playground" | "eval" | "reflect"))
        || (verb == Some("memory") && args.get(2).is_some_and(|arg| arg == "maintain"))
}

pub fn run(args: &[OsString], out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    if args.iter().any(|arg| arg == "--json") {
        writeln!(out, "{}", json!({"error": GUIDANCE, "hint": null}))?;
    }
    writeln!(err, "{GUIDANCE}")?;
    Ok(1)
}

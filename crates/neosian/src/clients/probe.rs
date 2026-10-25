//! A bounded subprocess probe of this binary, with disposable file storage only.

use serde_json::{Value, json};
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const MAX_OUTPUT: u64 = 256 * 1024;
const TIMEOUT: Duration = Duration::from_secs(10);

struct Temporary(PathBuf);

impl Temporary {
    fn new() -> io::Result<Self> {
        let path = std::env::temp_dir().join(format!("neosian-setup-{}", uuid::Uuid::new_v4()));
        crate::store::fileio::private_builder().create(&path)?;
        Ok(Self(path))
    }
}

impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn request(id: u8, method: &str, params: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})
}

fn exchange(executable: &Path, directory: &Path) -> io::Result<Value> {
    let nonce = uuid::Uuid::new_v4().to_string();
    let input = directory.join("requests");
    let output = directory.join("responses");
    let errors = directory.join("errors");
    let mut requests = File::create(&input)?;
    for value in [
        request(
            1,
            "initialize",
            json!({"protocolVersion":"2025-06-18","capabilities":{},
            "clientInfo":{"name":"neosian-setup","version":crate::version::VERSION}}),
        ),
        json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
        request(2, "tools/list", json!({})),
        request(
            3,
            "tools/call",
            json!({"name":"memory","arguments":
            {"command":"create","path":"/memories/setup-check","content":nonce}}),
        ),
        request(
            4,
            "tools/call",
            json!({"name":"memory","arguments":
            {"command":"view","path":"/memories/setup-check"}}),
        ),
    ] {
        writeln!(requests, "{value}")?;
    }
    drop(requests);
    let mut command = Command::new(executable);
    command.env_clear();
    // Windows needs its OS location. No profiles, credentials, client settings or
    // ambient store selectors enter this subprocess.
    for name in ["SystemRoot", "WINDIR"] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    let mut child = command
        .current_dir(directory)
        .env("HOME", directory)
        .env("USERPROFILE", directory)
        .env("NEOSIAN_HOME", directory.join("home"))
        .args(["mcp", "--root"])
        .arg(directory.join("store"))
        .args(["--scope", "user:setup-check", "--actor", "cli:setup-check"])
        .stdin(File::open(input)?)
        .stdout(File::create(&output)?)
        .stderr(Stdio::from(File::create(&errors)?))
        .spawn()?;
    let deadline = Instant::now() + TIMEOUT;
    let status = loop {
        let too_large = [&output, &errors]
            .iter()
            .any(|p| fs::metadata(p).is_ok_and(|m| m.len() > MAX_OUTPUT));
        if too_large || Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(io::Error::other(if too_large {
                "MCP probe exceeded its output limit"
            } else {
                "MCP probe timed out after 10 seconds"
            }));
        }
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        }
    };
    if !status.success() {
        return Err(io::Error::other("MCP probe process failed"));
    }
    let mut text = String::new();
    File::open(output)?
        .take(MAX_OUTPUT + 1)
        .read_to_string(&mut text)?;
    if text.len() as u64 > MAX_OUTPUT {
        return Err(io::Error::other("MCP probe exceeded its output limit"));
    }
    let replies: Vec<Value> = text
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    let result = |id| replies.iter().find(|r| r["id"] == id).map(|r| &r["result"]);
    let initialized = result(1).is_some_and(|r| {
        r["serverInfo"]["name"] == "neosian-memory"
            && r["serverInfo"]["version"] == crate::version::VERSION
    });
    let tools = result(2)
        .and_then(|r| r["tools"].as_array())
        .is_some_and(|t| t.iter().any(|t| t["name"] == "memory"));
    let created = result(3).is_some_and(|r| {
        r["isError"] != true && r["content"][0]["text"] == "Created /memories/setup-check (v1)"
    });
    let read = result(4).is_some_and(|r| {
        r["isError"] != true
            && r["content"][0]["text"] == format!("/memories/setup-check (v1):\n1: {nonce}")
    });
    Ok(json!({"success":initialized && tools && created && read,
        "initialize":initialized,"tools":tools,"write":created,"read":read}))
}

pub fn run() -> Value {
    let result = (|| {
        let temporary = Temporary::new()?;
        let result = exchange(&std::env::current_exe()?, &temporary.0);
        // A failed cleanup is visible, even if all protocol checks passed.
        fs::remove_dir_all(&temporary.0)?;
        result
    })();
    let mut report =
        result.unwrap_or_else(|e: io::Error| json!({"success":false,"error":e.to_string()}));
    report["storage"] = json!("temporary_files");
    report["executable"] = json!(std::env::current_exe().ok());
    report
}

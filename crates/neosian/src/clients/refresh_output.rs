//! The refresh report for a person: what changed, what was left alone, then restart.

use serde_json::Value;
use std::io::{self, Write};

fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("-")
}

fn listed(labels: &[Value]) -> String {
    let labels: Vec<_> = labels.iter().map(text).collect();
    match labels.as_slice() {
        [] => String::new(),
        [one] => (*one).into(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

fn action(part: &Value, failed: bool) -> String {
    match text(&part["action"]) {
        "update" if failed && part["kept"] == true => "updated, and kept after the failure".into(),
        "update" if failed => "not updated".into(),
        "update" => "updated".into(),
        "left_alone" => format!("left alone: {}", text(&part["reason"])),
        "off" => "kept off".into(),
        other => other.into(),
    }
}

pub fn render(report: &Value, out: &mut impl Write) -> io::Result<()> {
    let clients = report["clients"].as_array().expect("clients");
    writeln!(
        out,
        "neosian {} refreshes the user-level registrations it finds",
        text(&report["version"])
    )?;
    if clients.is_empty() {
        writeln!(out, "No user-level registration found; nothing changed.")?;
    }
    for client in clients {
        let failed = client["result"] == "failed";
        let changed = client["changed"].as_array().map_or(&[][..], Vec::as_slice);
        let result = match text(&client["result"]) {
            "failed" if changed.is_empty() => "failed; nothing was changed",
            "failed" => "failed; these files differ from before the refresh",
            "left_alone" => "left alone",
            other => other,
        };
        writeln!(out, "{}: {result}", text(&client["label"]))?;
        for line in client["rename_lines"].as_array().into_iter().flatten() {
            writeln!(out, "  {}", text(line))?;
        }
        if failed {
            for path in changed {
                writeln!(out, "  changed {}", text(path))?;
            }
        }
        for part in client["parts"].as_array().expect("parts") {
            writeln!(
                out,
                "  {:<7} {}  {}",
                text(&part["kind"]),
                text(&part["path"]),
                action(part, failed)
            )?;
            if let Some(note) = part["note"].as_str().filter(|_| !failed) {
                writeln!(out, "  note    {note}")?;
            }
        }
        if failed {
            writeln!(out, "  error   {}", text(&client["error"]))?;
            writeln!(out, "  hint    {}", text(&client["hint"]))?;
        }
    }
    let project = report["project"].as_array().expect("project");
    if !project.is_empty() {
        writeln!(out, "Project files are listed here and never edited:")?;
        for entry in project {
            writeln!(
                out,
                "  {} {}  {}",
                text(&entry["label"]),
                text(&entry["kind"]),
                text(&entry["path"])
            )?;
        }
    }
    let restart = report["restart"].as_array().expect("restart");
    if !restart.is_empty() {
        writeln!(out, "Restart {} to load these changes.", listed(restart))?;
    } else if !clients.is_empty() {
        writeln!(out, "No registration was changed.")?;
    }
    Ok(())
}

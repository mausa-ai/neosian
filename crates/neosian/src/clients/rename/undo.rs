//! `neosian setup --undo-rename [--client NAME]` (DESIGN §40.53 item 5): the
//! same move in the other direction for each client on `neosian` whose entry
//! is still ours, refusing where a person has since replaced it. The client
//! is then held: only `setup --refresh --rename` moves it again.

use super::{Plan, Step, backup::Backup, moves, places, report, text};
use crate::clients::{
    context::Context,
    naming::{self, NEW, OLD},
    receipt, targets,
};
use serde_json::{Value, json};
use std::io::{self, Write};

fn plan(client: &'static str, context: &Context) -> (Plan, Vec<String>) {
    let label = targets::mcp(client, context, "user").label;
    let mut plan = Plan {
        client,
        label,
        steps: vec![],
        stops: vec![],
        found: Default::default(),
        hosted: vec![],
    };
    let mut refused = vec![];
    if !super::installed(client, context) {
        return (plan, refused);
    }
    for container in places::containers(client, context) {
        let Ok(entries) = container.entries() else {
            continue;
        };
        let Some(new) = entries.new else {
            continue;
        };
        if !super::ours(&container, &new, context) || entries.old.is_some() {
            refused.push(format!(
                "{} {}",
                container.file.display(),
                container.pointer(NEW)
            ));
            continue;
        }
        plan.steps.push(Step {
            container,
            entry: new,
            remove_only: false,
        });
    }
    (plan, refused)
}

pub fn run(
    context: &Context,
    only: Option<&str>,
    json_out: bool,
    out: &mut impl Write,
) -> io::Result<u8> {
    let mut rows = vec![];
    let mut backup = None;
    let mut ok = true;
    for client in naming::CLIENTS
        .into_iter()
        .filter(|c| only.is_none_or(|o| o == *c))
    {
        let (plan, refused) = plan(client, context);
        if plan.steps.is_empty() && refused.is_empty() {
            continue;
        }
        let mut row = json!({"client": client, "label": plan.label, "refused": refused});
        let result = match &mut backup {
            Some(b) => Ok(b),
            None => Backup::begin(context).map(|b| backup.insert(b)),
        }
        .and_then(|b| moves::apply(context, &plan, NEW, OLD, b));
        match result {
            Ok(()) => {
                let _ = receipt::settle(client, context, &[], Some(("undo", "held")));
                row["back"] = json!(plan.steps.len());
            }
            Err(failure) => {
                ok = false;
                row["error"] = json!(failure.message);
            }
        }
        ok &= row["refused"].as_array().is_none_or(Vec::is_empty);
        rows.push(row);
    }
    if let Some(mut b) = backup {
        let _ = b.finish();
    }
    let report = json!({"success": ok, "clients": rows});
    if json_out {
        writeln!(out, "{report}")?;
    } else {
        report::undo_text(&report, out)?;
        if rows_empty(&report) {
            writeln!(out, "{}", text::get("undo_none"))?;
        }
    }
    Ok(u8::from(!ok))
}

fn rows_empty(report: &Value) -> bool {
    report["clients"].as_array().is_none_or(Vec::is_empty)
}

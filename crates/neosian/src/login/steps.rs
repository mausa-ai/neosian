//! Login's steps in text mode, in order: sign-in, this directory, the
//! recording notice, then (text mode only) workspaces, agents, details
//! under `--verbose`, and the actions left to the person.
use super::{
    Login,
    output::{Mark, View, fill, host as shown, say},
};
use crate::clients::{
    setup::{self, Setup},
    setup_outcome::{self, Outcome, Ran, State, Why},
    setup_text,
};
use serde_json::Value;
use std::io::{self, IsTerminal, Write};

/// The sign-in step for a refresh or an environment credential.
pub fn sign_in(ui: &mut View<impl Write>, refresh: bool) -> io::Result<()> {
    if refresh {
        return ui.step(&say("step_refresh"));
    }
    ui.step(&say("step_sign_in"))?;
    ui.say(&say("sign_in_environment"))
}

/// The host confirmed the account with a new machine document.
pub fn connected(
    ui: &mut View<impl Write>,
    refresh: bool,
    host: &str,
    email: &str,
) -> io::Result<()> {
    let key = if refresh { "refreshed" } else { "connected" };
    let line = fill(key, &[("host", &shown(host)), ("email", &shown(email))]);
    ui.mark(Mark::Ok, &line)
}

/// The host confirmed the account and that nothing changed.
pub fn unchanged(ui: &mut View<impl Write>, email: &str) -> io::Result<()> {
    ui.mark(Mark::Ok, &fill("unchanged", &[("email", &shown(email))]))
}

/// The one recording notice, shown before anything that could record is
/// installed: the host's words, attributed and set apart. None is shown
/// when the host sent none (or one that recording cannot use).
pub fn notice(ui: &mut View<impl Write>, host: &str, text: &str) -> io::Result<()> {
    if !text.is_empty() {
        ui.step(&say("step_recording"))?;
        ui.say(&fill("notice_from", &[("host", &shown(host))]))?;
        ui.quote(text)?;
    }
    ui.flush()
}

/// What recording does on this machine now; neosian's words, said once.
pub fn recording(ui: &mut View<impl Write>, off: bool) -> io::Result<()> {
    ui.step(&say("step_recording"))?;
    ui.say(&say(if off { "capture_off" } else { "capture_on" }))
}

/// The rest of a text-mode login that succeeded; its exit code.
pub fn finish(args: &Login, doc: &Value, ui: &mut View<impl Write>) -> io::Result<u8> {
    workspaces(doc, ui)?;
    let mut next = Vec::new();
    let mut code = 0;
    if args.refresh {
        if doc["updated"] == true {
            next.push(say("next_restart_refreshed"));
        }
    } else if !args.non_interactive && io::stdin().is_terminal() {
        ui.step(&say("step_agents"))?;
        ui.prompt(&say("agents_offer"))?;
        let mut response = String::new();
        io::stdin().read_line(&mut response)?;
        if matches!(response.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
            code = agents(ui, args.verbose, &mut next)?;
        } else {
            next.push(say("next_setup"));
        }
    } else {
        next.push(say("next_setup"));
    }
    if args.verbose {
        details(doc, ui)?;
    }
    if !next.is_empty() {
        ui.step(&say("step_next"))?;
        ui.list(&next)?;
    }
    Ok(code)
}

fn names(list: &Value) -> Vec<String> {
    list.as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(shown)
        .collect()
}

fn workspaces(doc: &Value, ui: &mut View<impl Write>) -> io::Result<()> {
    let default = doc["default_profile"].as_str().map(shown);
    let ready: Vec<_> = names(&doc["managed"])
        .into_iter()
        .map(|name| match &default {
            Some(d) if *d == name => fill("workspaces_default", &[("name", &name)]),
            _ => name,
        })
        .collect();
    ui.step(&say("step_workspaces"))?;
    ui.mark(
        Mark::Ok,
        &fill("workspaces_ready", &[("names", &ready.join(", "))]),
    )?;
    if let Some(root) = doc["project"]["root"].as_str() {
        let name = shown(doc["project"]["profile"].as_str().unwrap_or_default());
        ui.mark(
            Mark::Ok,
            &fill(
                "workspaces_directory",
                &[("root", &shown(root)), ("name", &name)],
            ),
        )?;
    }
    let removed = names(&doc["removed"]);
    if !removed.is_empty() {
        ui.mark(
            Mark::Warn,
            &fill("workspaces_removed", &[("names", &removed.join(", "))]),
        )?;
    }
    Ok(())
}

/// Setup's own selection and writes, shown as one line for each agent.
fn agents(ui: &mut View<impl Write>, verbose: bool, next: &mut Vec<String>) -> io::Result<u8> {
    // Setup's guidance (stderr when it runs alone) follows its lines here.
    let mut notes = Vec::new();
    let ran = setup::apply(&Setup::login(), &mut ui.nested(), &mut notes)?;
    let code = match ran {
        Ran::Exit(code) => code,
        Ran::NoClient(searched) => {
            ui.mark(Mark::Warn, &say("agents_none_found"))?;
            if verbose {
                write!(ui.nested(), "{}", setup_text::discovery_text(&searched))?;
            }
            1
        }
        Ran::Cancelled => ui.mark(Mark::Warn, &say("agents_cancelled")).map(|()| 1)?,
        Ran::NoneSelected => ui
            .mark(Mark::Warn, &say("agents_none_selected"))
            .map(|()| 0)?,
        Ran::Done(outcome) => {
            outcome_lines(&outcome, ui, next)?;
            if verbose {
                setup_text::full(&outcome, &mut ui.nested(), &mut notes)?;
            }
            return finish_notes(ui, &notes).map(|()| u8::from(outcome.failed));
        }
    };
    next.push(say("next_setup"));
    finish_notes(ui, &notes).map(|()| u8::from(code != 0))
}

fn finish_notes(ui: &mut View<impl Write>, notes: &[u8]) -> io::Result<()> {
    ui.nested().write_all(notes)
}

fn outcome_lines(
    outcome: &Outcome,
    ui: &mut View<impl Write>,
    next: &mut Vec<String>,
) -> io::Result<()> {
    let mut failing = Vec::new();
    for agent in setup_outcome::agents(outcome) {
        let label = shown(&agent.label);
        let Some((part, why)) = &agent.problem else {
            ui.mark(Mark::Ok, &label)?;
            continue;
        };
        let part = say(&format!("part_{part}"));
        let reason = match why {
            Why::Refused => fill("why_refused", &[("part", &part)]),
            Why::Missing(program) => fill("why_missing", &[("part", &part), ("program", program)]),
            Why::Failed(program) => fill("why_failed", &[("part", &part), ("program", program)]),
            Why::NotApplied => fill("why_unapplied", &[("part", &part)]),
        };
        let mark = if agent.state == State::Partial {
            Mark::Warn
        } else {
            Mark::Fail
        };
        let line = fill("agent_problem", &[("label", &label), ("reason", &reason)]);
        ui.mark(mark, &line)?;
        failing.push(agent);
    }
    if setup_outcome::restart(outcome) {
        next.push(say("next_restart"));
    }
    if setup_outcome::codex_review(outcome) {
        next.push(say("next_codex_hooks"));
    }
    if !failing.is_empty() {
        let labels: Vec<_> = failing.iter().map(|a| shown(&a.label)).collect();
        let options: Vec<_> = failing
            .iter()
            .map(|a| format!("--client {}", a.client))
            .collect();
        let key = if failing.len() == 1 {
            "next_fix_one"
        } else {
            "next_fix_many"
        };
        let labels = match labels.split_last() {
            Some((last, [])) => last.clone(),
            Some((last, rest)) => format!("{} and {last}", rest.join(", ")),
            None => String::new(),
        };
        next.push(fill(
            key,
            &[("labels", &labels), ("options", &options.join(" "))],
        ));
    }
    Ok(())
}

/// What the compact view leaves out of the account: identifiers and policy.
fn details(doc: &Value, ui: &mut View<impl Write>) -> io::Result<()> {
    let account = shown(doc["account"]["id"].as_str().unwrap_or("-"));
    let device = match (doc["device"]["name"].as_str(), doc["device"]["id"].as_str()) {
        (Some(name), Some(id)) => format!("{} ({})", shown(name), shown(id)),
        _ => "-".into(),
    };
    let capture = shown(doc["capture"].as_str().unwrap_or("-"));
    let rows = [
        (say("detail_account"), account),
        (say("detail_device"), device),
        (say("detail_capture"), capture),
    ];
    let pad = rows.iter().map(|(label, _)| label.len()).max().unwrap_or(0);
    ui.step(&say("step_details"))?;
    for (label, value) in rows {
        ui.pair(&label, pad, &value)?;
    }
    Ok(())
}

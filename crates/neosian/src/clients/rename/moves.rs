//! A client moved whole or not at all (DESIGN §40.53 item 2): each step is
//! made in order and recorded; a failure reverses the steps already made and
//! the client is back on the old name. The same steps in the other direction
//! put a client back (`setup --undo-rename`).

use super::{
    Plan, Step,
    backup::Backup,
    claude_cli::{self, Scope},
    jsonedit,
    places::{Container, Kind},
    read, text, writes,
};
use crate::clients::{
    context::Context,
    files::{self, Failure},
    naming::{NEW, OLD},
    receipt,
};
use serde_json::Value;

/// How a client ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// Nothing of ours under the old key.
    Unchanged,
    Moved,
    Left,
    Failed,
}

pub struct Outcome {
    pub plan: Plan,
    pub state: State,
    pub error: Option<String>,
}

/// One step, `from` to `to` (a removal of `from` when `remove`).
fn step(context: &Context, step: &Step, from: &str, to: &str, remove: bool) -> Result<(), Failure> {
    let container = &step.container;
    match &container.kind {
        Kind::Claude(Scope::Local(project)) if !claude_cli::addressable(project) => {
            writes::in_project(container, project, from, to, remove)
        }
        Kind::Claude(scope) if remove => claude_cli::removed(context, scope, from),
        Kind::Claude(scope) => claude_cli::moved(context, scope, &step.entry, from, to),
        Kind::Json(member) => writes::json_file(container, member, from, to, remove),
        Kind::Toml if remove => Err(Failure::new(
            text::get("toml_both"),
            super::claude_cli::AFTER_FAILURE,
        )),
        Kind::Toml => writes::toml_file(container, from, to),
    }
}

/// The entry now under `key` in the container.
fn now(container: &Container, key: &str) -> Option<Value> {
    container
        .entries()
        .ok()
        .and_then(|e| if key == NEW { e.new } else { e.old })
}

/// Reverse one made step on the files as they are now.
fn reverse(context: &Context, made: &Step, from: &str, to: &str) -> Result<(), Failure> {
    let container = &made.container;
    if made.remove_only {
        return match &container.kind {
            Kind::Claude(scope) => claude_cli::restored(context, scope, from, &made.entry)
                .map_err(|e| Failure::new(e, super::claude_cli::AFTER_FAILURE)),
            _ => {
                let read = read::file(&container.file).ok_or_else(|| Failure::new("gone", ""))?;
                let mut intended = read.json().unwrap_or_default();
                let member = match container.kind {
                    Kind::Json(m) => m,
                    _ => "mcpServers",
                };
                intended[member][from] = made.entry.clone();
                let text_value = made.entry.to_string();
                let edited = jsonedit::insert_member(
                    &read.text,
                    &[member.to_owned()],
                    from,
                    &text_value,
                    &intended,
                )
                .ok_or_else(|| Failure::new("not editable", ""))?;
                files::write_text(&container.file, &read.with_bom(&edited))
            }
        };
    }
    if now(container, to).is_none() && now(container, from).is_some() {
        return Ok(());
    }
    let back = Step {
        entry: now(container, to).unwrap_or_else(|| made.entry.clone()),
        ..made.clone()
    };
    step(context, &back, to, from, false)
}

/// Move one client's steps from `from` to `to`; on a failure the steps made
/// are reversed, newest first. The receipt follows each step.
pub fn apply(
    context: &Context,
    plan: &Plan,
    from: &str,
    to: &str,
    backup: &mut Backup,
) -> Result<(), Failure> {
    let mut made: Vec<(&Step, Option<Value>)> = vec![];
    for s in &plan.steps {
        // A shared file another client moved earlier in this run.
        if now(&s.container, from).is_none() && now(&s.container, to).is_some() {
            continue;
        }
        backup.keep(&s.container.file)?;
        let result =
            step(context, s, from, to, s.remove_only).and_then(|()| settle(context, s, to));
        if let Err(mut failure) = result {
            // The failed step's intent is dropped, then the made steps reversed.
            let _ = settle(context, s, from);
            let mut left = vec![];
            for (done, _) in made.iter().rev() {
                if let Err(e) =
                    reverse(context, done, from, to).and_then(|()| settle(context, done, from))
                {
                    left.push(format!(
                        "{} {}: {}",
                        done.container.file.display(),
                        done.container.pointer(to),
                        e.message
                    ));
                }
            }
            if !left.is_empty() {
                failure.message = format!("{}; {}", failure.message, left.join("; "));
            }
            return Err(failure);
        }
        made.push((s, now(&s.container, to)));
    }
    backup.moved(context, &made, to)
}

/// The receipt after a step: the entry under `neosian` recorded when the
/// client is now on it, forgotten when it is back on the old key.
fn settle(context: &Context, step: &Step, on: &str) -> Result<(), Failure> {
    let container = &step.container;
    let pointer = container.pointer(NEW);
    if on == NEW {
        let written = now(container, NEW).unwrap_or_else(|| step.entry.clone());
        receipt::record(
            container.client,
            context,
            &container.file,
            &pointer,
            &written,
        )
    } else {
        receipt::settle(
            container.client,
            context,
            &[(container.file.clone(), pointer)],
            None,
        )
    }
}

/// Run the rename for every client: by itself, or by the person's command
/// (`forced`), `only` limiting it to one client. Every client is reported.
pub fn run(context: &Context, forced: bool, only: Option<&str>) -> Vec<Outcome> {
    let plans = super::plan_all(context, forced);
    let mut backup: Option<Backup> = None;
    let mut outcomes = vec![];
    for plan in plans {
        let selected = only.is_none_or(|c| c == plan.client);
        let moves = selected
            && if forced {
                plan.by_command()
            } else {
                plan.by_itself()
            };
        if !moves {
            let state = if plan.wanted() {
                State::Left
            } else {
                State::Unchanged
            };
            outcomes.push(Outcome {
                state,
                error: None,
                plan,
            });
            continue;
        }
        if forced {
            let _ = receipt::release(plan.client, context);
        }
        let started = match backup.take() {
            Some(b) => Ok(b),
            None => Backup::begin(context),
        };
        let mut current = match started {
            Ok(b) => b,
            Err(failure) => {
                outcomes.push(Outcome {
                    state: State::Failed,
                    error: Some(failure.message),
                    plan,
                });
                continue;
            }
        };
        let result = apply(context, &plan, OLD, NEW, &mut current);
        let (state, error) = match result {
            Ok(()) => (State::Moved, None),
            Err(failure) => (State::Failed, Some(failure.message)),
        };
        backup = Some(current);
        outcomes.push(Outcome { plan, state, error });
    }
    if let Some(mut done) = backup {
        let _ = done.finish();
    }
    outcomes
}

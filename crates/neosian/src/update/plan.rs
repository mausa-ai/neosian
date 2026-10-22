//! Update decisions are pure over a release and an installation's receipt.

use super::{receipt, version::Version};
use crate::{
    clients::context::{Context, shell_join},
    shape,
};

pub struct Decision {
    pub action: &'static str,
    pub line: String,
    pub latest: Option<String>,
    pub argv: Vec<String>,
}

pub fn plan(
    current: &str,
    latest: Option<&Version>,
    context: &Context,
    applying: bool,
) -> Decision {
    let Some(latest) = latest else {
        return Decision {
            action: "unreachable",
            line: "GitHub Releases is unreachable; nothing checked".into(),
            latest: None,
            argv: vec![],
        };
    };
    let mut decision = Decision {
        action: "current",
        line: format!("neosian {current} is current"),
        latest: Some(latest.to_string()),
        argv: vec![],
    };
    let installed = Version::parse(current);
    if installed.as_ref().is_some_and(|v| latest <= v) {
        return decision;
    }
    let owned = receipt::updater(context, current);
    let kind = shape::kind(&context.env);
    decision.argv = vec![
        owned
            .as_ref()
            .map_or_else(|_| "neosian-update".into(), |p| p.display().to_string()),
        "--tag".into(),
        format!("v{latest}"),
    ];
    let command = if kind == "container" {
        shape::upgrade(kind, &latest.to_string())
    } else {
        shell_join(&decision.argv)
    };
    decision.action = "notify";
    decision.line = format!("neosian {latest} is available (you have {current}): {command}");
    if !applying {
        return decision;
    }
    let refusal = if installed.is_none() {
        Some("the installed version is not recognised".into())
    } else if installed.as_ref().is_some_and(|v| v.major != latest.major) {
        Some(format!("{latest} is a new major"))
    } else if !latest.final_release && installed.as_ref().is_some_and(|v| v.final_release) {
        Some(format!("{latest} is a pre-release and {current} is not"))
    } else {
        owned.err().map(str::to_string)
    };
    if let Some(why) = refusal {
        decision.action = "refuse";
        decision.line = format!("not applied ({why}); run it yourself: {command}");
    } else {
        decision.action = "apply";
        decision.line = command;
    }
    decision
}

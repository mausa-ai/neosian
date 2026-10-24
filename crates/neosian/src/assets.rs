//! The shipped prompts (DESIGN §7, §39.3): embedded from `assets/`
//! at build time from one canonical source, never prose in Rust.

/// `assets/prompts/messages.yaml`.
pub const MESSAGES: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/messages.yaml"));

/// `assets/prompts/context.yaml`.
pub const CONTEXT: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/context.yaml"));

/// `assets/prompts/tools.yaml`: the built-in tools' descriptions
/// and their parameters' prose.
pub const TOOLS: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/tools.yaml"));

/// `assets/prompts/memory.yaml`: the memory tool and the system
/// section.
pub const MEMORY: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/memory.yaml"));

/// The binary's own skills, `assets/skills/*.md`, by name: the
/// directory the MCP server lists after the mounts' (§24.3). A unit test
/// keeps this list equal to the directory.
pub const SKILLS: [(&str, &str); 2] = [
    (
        "handoff",
        include_str!(concat!(env!("NEOSIAN_ASSETS"), "/skills/handoff.md")),
    ),
    (
        "upkeep",
        include_str!(concat!(env!("NEOSIAN_ASSETS"), "/skills/upkeep.md")),
    ),
];

/// The lines of a top-level block scalar introduced by `key: <head>`, the
/// two-space indent dropped and the trailing blank lines clipped.
fn scalar<'a>(text: &'a str, key: &str, head: &str) -> Option<Vec<&'a str>> {
    let opening = format!("{key}: {head}");
    let mut lines: Vec<&str> = text
        .lines()
        .skip_while(|line| *line != opening)
        .skip(1)
        .take_while(|line| line.is_empty() || line.starts_with(' '))
        .map(|line| line.strip_prefix("  ").unwrap_or(line))
        .collect();
    while lines.last().is_some_and(|line| line.is_empty()) {
        lines.pop();
    }
    (!lines.is_empty()).then_some(lines)
}

/// A `key: |` block scalar as YAML reads it: the lines kept, one newline
/// at the end. None when the file has no such key.
pub fn block(text: &str, key: &str) -> Option<String> {
    scalar(text, key, "|").map(|lines| format!("{}\n", lines.join("\n")))
}

/// A `key: |-` block scalar as YAML reads it: the lines kept, nothing at
/// the end.
pub fn literal(text: &str, key: &str) -> Option<String> {
    scalar(text, key, "|-").map(|lines| lines.join("\n"))
}

/// A `key: >-` block scalar as YAML reads it: consecutive lines folded
/// into one with a space, a blank line a newline, nothing at the end.
pub fn folded(text: &str, key: &str) -> Option<String> {
    let lines = scalar(text, key, ">-")?;
    let mut out = String::new();
    for line in lines {
        if line.is_empty() {
            out.push('\n');
        } else {
            if !out.is_empty() && !out.ends_with('\n') {
                out.push(' ');
            }
            out.push_str(line);
        }
    }
    Some(out)
}

/// A one-line scalar as YAML reads it: double-quoted with JSON's escapes,
/// single-quoted with `''`, or plain to the end of the line.
fn plain(value: &str) -> String {
    let value = value.trim();
    if value.starts_with('"')
        && let Ok(text) = serde_json::from_str::<String>(value)
    {
        return text;
    }
    if let Some(inner) = value
        .strip_prefix('\'')
        .and_then(|rest| rest.strip_suffix('\''))
    {
        return inner.replace("''", "'");
    }
    value.to_string()
}

/// A top-level `key: <one-line scalar>` as YAML reads it.
pub fn quoted(text: &str, key: &str) -> Option<String> {
    let prefix = format!("{key}: ");
    text.lines()
        .find_map(|line| line.strip_prefix(&prefix))
        .map(plain)
}

/// A top-level `key:` whose value is a mapping of one-line scalars, in
/// order: the parameters' prose of a tool.
pub fn mapping(text: &str, key: &str) -> Option<Vec<(String, String)>> {
    let opening = format!("{key}:");
    let entries: Vec<(String, String)> = text
        .lines()
        .skip_while(|line| *line != opening)
        .skip(1)
        .take_while(|line| line.starts_with("  "))
        .filter_map(|line| {
            let (name, value) = line.trim_start().split_once(": ")?;
            Some((name.to_string(), plain(value)))
        })
        .collect();
    (!entries.is_empty()).then_some(entries)
}

/// `prompt_assets.render`: every `{{key}}` replaced, in the order given.
pub fn render(template: &str, variables: &[(&str, &str)]) -> String {
    variables
        .iter()
        .fold(template.to_string(), |text, (key, value)| {
            text.replace(&format!("{{{{{key}}}}}"), value)
        })
}

#[cfg(test)]
mod tests {
    use super::{
        CONTEXT, MEMORY, MESSAGES, TOOLS, block, folded, literal, mapping, quoted, render,
    };

    #[test]
    fn a_quoted_scalar_and_a_mapping_read_as_yaml_reads_them() {
        let footer = quoted(TOOLS, "search_history_footer").unwrap();
        assert!(footer.starts_with(
            "[newest first, at most {{shown}}; call recall_turn(n, conversation=\"<id>\")"
        ));
        assert!(
            quoted(TOOLS, "skill_list")
                .unwrap()
                .starts_with("List the available skills")
        );
        assert_eq!(quoted(TOOLS, "absent"), None);
        let params = mapping(TOOLS, "recall_turn_any_params").unwrap();
        assert_eq!(params.len(), 3);
        assert_eq!(params[0].0, "turn");
        assert!(params[2].1.starts_with("The id of one tool call"));
        let messages = mapping(MESSAGES, "params").unwrap();
        assert_eq!(
            messages[0],
            (
                "command".to_string(),
                "send, list, view, ack, claim, renew, release, complete, snooze, or cancel."
                    .to_string()
            )
        );
        assert_eq!(messages.len(), 21);
        assert_eq!(mapping(MEMORY, "params").unwrap().len(), 11);
        assert!(
            folded(MEMORY, "tool")
                .unwrap()
                .starts_with("Read and edit your persistent memory directory.")
        );
        assert!(
            literal(MEMORY, "system_section")
                .unwrap()
                .ends_with("{{index}}")
        );
        assert_eq!(mapping(TOOLS, "absent"), None);
    }

    #[test]
    fn a_block_scalar_reads_as_yaml_reads_it() {
        assert_eq!(
            block(MESSAGES, "index").as_deref(),
            Some("- /{{mount}}/messages/ ({{count}} mailbox records; use messages list/view)\n")
        );
        assert!(block(MESSAGES, "tool").is_some_and(|text| text.lines().count() > 5));
        assert_eq!(block(MESSAGES, "absent"), None);
        assert_eq!(
            block("a: |\n  one\n\n  two\n\nb: |\n  x\n", "a").as_deref(),
            Some("one\n\ntwo\n")
        );
    }

    #[test]
    fn a_folded_scalar_reads_as_yaml_reads_it() {
        assert_eq!(
            folded(CONTEXT, "view_fold").as_deref(),
            Some("[{{first}}-{{last}}] {{count}} earlier turns not shown")
        );
        let header = folded(CONTEXT, "continue_header").unwrap();
        assert!(header.starts_with("[continuing conversation {{conversation_id}} — written by"));
        assert!(header.ends_with("re-reads any turn whole]") && !header.contains('\n'));
        assert_eq!(
            folded("k: >-\n  a\n  b\n\n  c\n\nz: >-\n  x\n", "k").as_deref(),
            Some("a b\nc")
        );
        assert_eq!(folded(CONTEXT, "absent"), None);
        let instructions = literal(CONTEXT, "start_instructions").unwrap();
        assert!(instructions.starts_with("Your persistent memory is the `memory` tool"));
        assert!(
            instructions.contains("\n  of writing a duplicate.\n") && !instructions.ends_with('\n')
        );
        assert!(instructions.ends_with("New work does not call it."));
        assert_eq!(
            render(
                "[{{first}}-{{last}}] {{count}}",
                &[("first", "1"), ("last", "3"), ("count", "3")]
            ),
            "[1-3] 3"
        );
    }
}

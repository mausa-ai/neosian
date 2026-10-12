//! The shipped prompts (DESIGN §7, §39.3): embedded from `neosian/assets/`
//! at build time, one source with the wheel, never prose in Rust.

/// `neosian/assets/prompts/messages.yaml`.
pub const MESSAGES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../neosian/assets/prompts/messages.yaml"
));

/// `neosian/assets/prompts/context.yaml`.
pub const CONTEXT: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../neosian/assets/prompts/context.yaml"
));

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
    use super::{CONTEXT, MESSAGES, block, folded, render};

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
        assert_eq!(
            render(
                "[{{first}}-{{last}}] {{count}}",
                &[("first", "1"), ("last", "3"), ("count", "3")]
            ),
            "[1-3] 3"
        );
    }
}

//! The shipped prompts (DESIGN §7, §39.3): embedded from `neosian/assets/`
//! at build time, one source with the wheel, never prose in Rust.

/// `neosian/assets/prompts/messages.yaml`.
pub const MESSAGES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../neosian/assets/prompts/messages.yaml"
));

/// A top-level `key: |` block scalar as YAML reads it: the two-space
/// indent dropped, the trailing blank lines clipped to one newline. None
/// when the file has no such key.
pub fn block(text: &str, key: &str) -> Option<String> {
    let head = format!("{key}: |");
    let lines: Vec<&str> = text
        .lines()
        .skip_while(|line| *line != head)
        .skip(1)
        .take_while(|line| line.is_empty() || line.starts_with(' '))
        .map(|line| line.strip_prefix("  ").unwrap_or(line))
        .collect();
    let body = lines.join("\n");
    (!lines.is_empty()).then(|| format!("{}\n", body.trim_end_matches('\n')))
}

#[cfg(test)]
mod tests {
    use super::{MESSAGES, block};

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
}

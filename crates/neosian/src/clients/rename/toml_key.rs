//! Codex's `[mcp_servers.<key>]` tables moved by their header lines (DESIGN
//! §40.53 item 1, from `phase/x2a-rename`): the table's own members
//! (`env`, `enabled_tools`, `disabled_tools`, `tools.<t>.approval_mode`)
//! travel with it. The result must parse to the original with that one key
//! moved; any other form is refused.

/// The header's key when the line is `[mcp_servers.<key>]` or a subtable of
/// it (bare or quoted key); the byte range of the key.
fn header_key(line: &str) -> Option<(usize, usize, String)> {
    let start = line.len() - line.trim_start().len();
    let rest = line[start..].strip_prefix('[')?;
    let tail = rest.trim_start().strip_prefix("mcp_servers")?.trim_start();
    let tail = tail.strip_prefix('.')?;
    let key_text = tail.trim_start();
    let key_at = line.len() - key_text.len();
    let (key, length, inner) = match key_text.chars().next()? {
        quote @ ('"' | '\'') => {
            let end = key_text[1..].find(quote)? + 2;
            (key_text[1..end - 1].to_owned(), end, 1)
        }
        _ => {
            let end =
                key_text.find(|c: char| !(c.is_ascii_alphanumeric() || c == '-' || c == '_'))?;
            (key_text[..end].to_owned(), end, 0)
        }
    };
    let after = key_text[length..].trim_start();
    let range = (key_at + inner, key_at + length - inner);
    (after.starts_with(']') || after.starts_with('.')).then_some((range.0, range.1, key))
}

/// The text with every header of `from`'s tables naming `to` instead.
pub fn moved(text: &str, from: &str, to: &str) -> Result<String, String> {
    let renamed: String = text
        .split_inclusive('\n')
        .map(|line| match header_key(line) {
            Some((start, end, key)) if key == from => {
                format!("{}{to}{}", &line[..start], &line[end..])
            }
            _ => line.to_owned(),
        })
        .collect();
    let parse = |text: &str| text.parse::<toml::Table>().ok();
    let (Some(mut before), Some(after)) = (parse(text), parse(&renamed)) else {
        return Err("the file does not parse as TOML".into());
    };
    if let Some(toml::Value::Table(servers)) = before.get_mut("mcp_servers") {
        *servers = servers
            .iter()
            .map(|(k, v)| (if k == from { to } else { k }.to_owned(), v.clone()))
            .collect();
    }
    if renamed == text || before != after {
        return Err("the entry is written in a form this release does not move".into());
    }
    Ok(renamed)
}

/// The line ranges (from 1, inclusive) of the tables of `keys` under
/// `mcp_servers`, for the mention search to pass over.
pub fn regions(text: &str, keys: &[&str]) -> Vec<(usize, usize)> {
    let mut regions = vec![];
    let mut open: Option<usize> = None;
    for (index, line) in text.lines().enumerate() {
        let number = index + 1;
        if line.trim_start().starts_with('[') {
            let ours = header_key(line).is_some_and(|(_, _, key)| keys.contains(&key.as_str()));
            match (open, ours) {
                (Some(start), false) => {
                    regions.push((start, number - 1));
                    open = None;
                }
                (None, true) => open = Some(number),
                _ => {}
            }
        }
    }
    if let Some(start) = open {
        regions.push((start, text.lines().count()));
    }
    regions
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_move_with_their_subtables_and_other_forms_are_refused() {
        let text = "[mcp_servers.neosian-memory]\ncommand = \"neosian\"\nargs = [\"mcp\"]\n\n[mcp_servers.\"neosian-memory\".tools.memory]\napproval_mode = \"approve\"\n\n[mcp_servers.neosian-memoryx]\ncommand = \"x\"\n";
        let moved_text = moved(text, "neosian-memory", "neosian").unwrap();
        assert!(moved_text.contains("[mcp_servers.neosian]\n"));
        assert!(moved_text.contains("[mcp_servers.\"neosian\".tools.memory]"));
        assert!(moved_text.contains("[mcp_servers.neosian-memoryx]"));
        assert_eq!(
            moved(&moved_text, "neosian", "neosian-memory").unwrap(),
            text
        );
        let inline = "mcp_servers = { neosian-memory = { command = \"neosian\" } }\n";
        assert!(moved(inline, "neosian-memory", "neosian").is_err());
        assert_eq!(regions(text, &["neosian-memory"]), [(1, 7)]);
    }
}

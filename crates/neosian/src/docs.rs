//! `neosian docs` (DESIGN §14.4, §39.3): the shipped pages, embedded from
//! `neosian/assets/docs/` at build time (one source with the wheel) and
//! printed byte for byte as the Python door prints them on a pipe. The
//! topic order is the manifest's; a unit test in the Python tier keeps the
//! list below equal to `docs_assets._TOPICS`.

use std::io::{self, Write};

use serde_json::json;

macro_rules! pages {
    ($($topic:literal),+ $(,)?) => {
        /// The curated reading order, each topic with its page's text.
        const PAGES: &[(&str, &str)] = &[$((
            $topic,
            include_str!(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../../neosian/assets/docs/",
                $topic,
                ".md"
            )),
        ),)+];
    };
}

pages!(
    "quickstart",
    "memory",
    "skills",
    "messaging",
    "stores",
    "agents",
    "cli",
    "mcp",
    "interop",
    "agent",
    "tools",
    "local",
    "topology",
    "wire",
    "baselines",
);

/// One shipped page; `topic` is the argv token.
pub struct Page {
    pub topic: &'static str,
    pub title: String,
    pub summary: String,
    pub body: String,
}

/// Every shipped page, in curated reading order.
pub fn list_topics() -> Vec<Page> {
    PAGES
        .iter()
        .map(|(topic, text)| parse(topic, text))
        .collect()
}

/// One page by topic name; None when the topic is unknown.
pub fn load_page(topic: &str) -> Option<Page> {
    let (name, text) = PAGES.iter().find(|(name, _)| *name == topic)?;
    Some(parse(name, text))
}

/// The Python reader's exact split (`frontmatter.parse_frontmatter`): the
/// stripped text, whole-line `---` fences, the body stripped again. The
/// pages are validated at the Python gate, so a page is never fenceless
/// here; one would simply be all body.
fn parse(topic: &'static str, text: &str) -> Page {
    let lines: Vec<&str> = text.trim().split('\n').collect();
    let is_fence = |line: &str| line.trim_end_matches('\r') == "---";
    let close = if lines.first().is_some_and(|line| is_fence(line)) {
        lines
            .iter()
            .skip(1)
            .position(|line| is_fence(line))
            .map(|at| at + 1)
    } else {
        None
    };
    let (mut title, mut summary) = (String::new(), String::new());
    let mut body = lines.as_slice();
    if let Some(close) = close {
        for line in &lines[1..close] {
            if let Some(value) = line.strip_prefix("title:") {
                title = scalar(value);
            } else if let Some(value) = line.strip_prefix("summary:") {
                summary = scalar(value);
            }
        }
        body = &lines[close + 1..];
    }
    let body = body.join("\n").trim().to_string();
    Page {
        topic,
        title,
        summary,
        body,
    }
}

/// A YAML scalar as the pages write it: plain, or double-quoted with `\"`
/// and `\\` the only escapes in use.
fn scalar(value: &str) -> String {
    let value = value.trim();
    match value
        .strip_prefix('"')
        .and_then(|rest| rest.strip_suffix('"'))
    {
        Some(quoted) => quoted.replace("\\\"", "\"").replace("\\\\", "\\"),
        None => value.to_string(),
    }
}

/// Python's `repr` of the one token a message carries: single quotes,
/// or double quotes when the token holds a single quote and no double.
fn repr(token: &str) -> String {
    let escaped = token.replace('\\', "\\\\");
    if token.contains('\'') && !token.contains('"') {
        format!("\"{escaped}\"")
    } else {
        format!("'{}'", escaped.replace('\'', "\\'"))
    }
}

fn known_topics() -> String {
    PAGES
        .iter()
        .map(|(topic, _)| *topic)
        .collect::<Vec<_>>()
        .join(", ")
}

/// Print a page or the listing; the exit code is the argv tier's.
pub fn run(
    topic: Option<&str>,
    json: bool,
    out: &mut impl Write,
    err: &mut impl Write,
) -> io::Result<u8> {
    let Some(topic) = topic else {
        return listing(json, out, err);
    };
    let Some(page) = load_page(topic) else {
        let known = known_topics();
        if json {
            let hint = format!("unknown topic {}; known topics: {known}", repr(topic));
            writeln!(out, "{}", json!({"error": "usage", "hint": hint}))?;
        }
        writeln!(err, "error: unknown topic {}", repr(topic))?;
        writeln!(err, "hint: known topics: {known}")?;
        return Ok(2);
    };
    if json {
        let payload = json!({
            "topic": page.topic,
            "title": page.title,
            "summary": page.summary,
            "body": page.body,
        });
        writeln!(out, "{payload}")?;
        return Ok(0);
    }
    writeln!(out, "{}", page.body)?;
    Ok(0)
}

fn listing(json: bool, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    let pages = list_topics();
    if json {
        let topics: Vec<_> = pages
            .iter()
            .map(|page| json!({"topic": page.topic, "title": page.title, "summary": page.summary}))
            .collect();
        writeln!(out, "{}", json!({"topics": topics}))?;
        return Ok(0);
    }
    let width = pages.iter().map(|page| page.topic.len()).max().unwrap_or(0);
    for page in &pages {
        writeln!(out, "{:<width$}  {}", page.topic, page.summary)?;
    }
    writeln!(err, "hint: neosian docs <topic> prints a page")?;
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_shipped_page_loads() {
        let pages = list_topics();
        assert_eq!(pages.len(), PAGES.len());
        for page in pages {
            assert!(!page.title.is_empty(), "{}", page.topic);
            assert!(!page.summary.is_empty(), "{}", page.topic);
            assert!(
                !page.body.is_empty() && !page.body.starts_with("---"),
                "{}",
                page.topic
            );
        }
    }

    #[test]
    fn scalars_are_plain_or_double_quoted() {
        assert_eq!(scalar(" Messages and reminders "), "Messages and reminders");
        assert_eq!(
            scalar(r#" "@Tool: a \"quoted\" word" "#),
            r#"@Tool: a "quoted" word"#
        );
    }

    #[test]
    fn repr_is_pythons() {
        assert_eq!(repr("nope"), "'nope'");
        assert_eq!(repr("it's"), "\"it's\"");
        assert_eq!(repr("a'b\"c"), "'a\\'b\"c'");
    }

    #[test]
    fn an_unknown_topic_is_an_argv_error() {
        let (mut out, mut err) = (Vec::new(), Vec::new());
        assert_eq!(run(Some("nope"), true, &mut out, &mut err).unwrap(), 2);
        let payload: serde_json::Value = serde_json::from_slice(&out).unwrap();
        assert_eq!(payload["error"], "usage");
        assert!(
            String::from_utf8(err)
                .unwrap()
                .starts_with("error: unknown topic 'nope'\n")
        );
        assert!(load_page("nope").is_none());
    }
}

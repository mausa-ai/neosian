//! `neosian docs` (DESIGN §14.4, §39.3): the shipped pages, embedded from
//! `assets/docs/` at build time. The build script checks the manifest;
//! independent contract tests compare every embedded page with its source.

use std::io::{self, Write};

use serde_json::json;

use crate::repr::repr;

include!(concat!(env!("OUT_DIR"), "/docs_pages.rs"));

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

/// Retain v1's whole-line frontmatter fences and stripped body. The build
/// script validates every shipped page before embedding it.
fn parse(topic: &'static str, text: &str) -> Page {
    let lines: Vec<&str> = text.trim().lines().collect();
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
    fn a_windows_checkout_prints_the_same_document_bytes() {
        let text =
            "---\r\ntitle: Quickstart\r\nsummary: Read me\r\n---\r\n\r\nCost in µ$.\r\nNext…\r\n";
        let page = parse("quickstart", text);
        assert_eq!(page.body, "Cost in µ$.\nNext…");
        assert_eq!(
            page.body,
            parse("quickstart", &text.replace("\r\n", "\n")).body
        );
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

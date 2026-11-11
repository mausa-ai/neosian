//! The nearest Markdown heading above a passage (DESIGN §40.42).
//!
//! ATX headings only: up to three leading spaces, one to six `#`, then a space,
//! a tab or the line end. Lines inside fenced code, a leading `---` frontmatter
//! block and a leading `<!--` comment block (such as the knowledge header) are
//! never headings. Setext headings are not recognized.

const LIMIT: usize = 240;

#[derive(Clone, Copy, PartialEq)]
enum Preamble {
    Start,
    Front,
    AfterFront,
    Comment,
    Body,
}

fn title(rest: &str) -> Option<String> {
    let text = rest.trim();
    let stripped = text.trim_end_matches('#');
    let text = if stripped.is_empty() {
        ""
    } else if stripped.ends_with([' ', '\t']) {
        stripped.trim_end()
    } else {
        text
    };
    (!text.is_empty()).then(|| text.chars().take(LIMIT).collect())
}

/// (line start scalar offset, heading text) for every recognized heading.
pub(super) fn scan(text: &str) -> Vec<(usize, Option<String>)> {
    let mut found = Vec::new();
    let mut offset = 0;
    let mut fence: Option<(char, usize)> = None;
    let mut preamble = Preamble::Start;
    for raw in text.split('\n') {
        let start = offset;
        offset += raw.chars().count() + 1;
        let line = raw.strip_suffix('\r').unwrap_or(raw);
        match preamble {
            Preamble::Front => {
                if line.trim_end() == "---" {
                    preamble = Preamble::AfterFront;
                }
                continue;
            }
            Preamble::Comment => {
                if line.contains("-->") {
                    preamble = Preamble::Body;
                }
                continue;
            }
            Preamble::Start if line.trim_end() == "---" => {
                preamble = Preamble::Front;
                continue;
            }
            Preamble::Start | Preamble::AfterFront if line.trim_start().starts_with("<!--") => {
                preamble = if line.contains("-->") {
                    Preamble::Body
                } else {
                    Preamble::Comment
                };
                continue;
            }
            _ => preamble = Preamble::Body,
        }
        let indent = line.len() - line.trim_start_matches(' ').len();
        if indent > 3 {
            continue;
        }
        let body = &line[indent..];
        if let Some((mark, length)) = fence {
            let run = body.chars().take_while(|c| *c == mark).count();
            if run >= length && body[run * mark.len_utf8()..].trim().is_empty() {
                fence = None;
            }
            continue;
        }
        if let Some(mark @ ('`' | '~')) = body.chars().next() {
            let run = body.chars().take_while(|c| *c == mark).count();
            if run >= 3 {
                fence = Some((mark, run));
                continue;
            }
        }
        let hashes = body.chars().take_while(|c| *c == '#').count();
        let rest = &body[hashes..];
        if (1..=6).contains(&hashes) && (rest.is_empty() || rest.starts_with([' ', '\t'])) {
            found.push((start, title(rest)));
        }
    }
    found
}

/// The heading whose line starts at or before `offset`; an empty one is null.
pub(super) fn before(found: &[(usize, Option<String>)], offset: usize) -> Option<String> {
    let index = found.partition_point(|(start, _)| *start <= offset);
    index.checked_sub(1).and_then(|i| found[i].1.clone())
}

#[cfg(test)]
mod tests {
    use super::{before, scan};

    #[test]
    fn headings_skip_fences_frontmatter_and_leading_comments() {
        let text = "---\ntitle: x\n# not a heading\n---\n<!-- neosian-knowledge:1\n# no\n-->\n\
                    # Intro #\nbody\n```\n# code\n```\n  ## Setup ##\n####### seven\n#tag\n\
                    ~~~~\n## fenced\n~~~\n## still fenced\n~~~~\n### Last\n";
        let found = scan(text);
        let titles: Vec<_> = found.iter().map(|(_, t)| t.as_deref()).collect();
        assert_eq!(titles, [Some("Intro"), Some("Setup"), Some("Last")]);
        let intro = text.find("# Intro").unwrap();
        assert_eq!(found[0].0, text[..intro].chars().count());
        assert_eq!(before(&found, 0), None);
        assert_eq!(before(&found, found[1].0).as_deref(), Some("Setup"));
        assert_eq!(before(&found, found[1].0 - 1).as_deref(), Some("Intro"));
    }

    #[test]
    fn closing_sequences_need_a_space_and_empty_headings_are_null() {
        let found = scan("# Title#\n#\n## Done ##  \n");
        let titles: Vec<_> = found.iter().map(|(_, t)| t.as_deref()).collect();
        assert_eq!(titles, [Some("Title#"), None, Some("Done")]);
    }
}

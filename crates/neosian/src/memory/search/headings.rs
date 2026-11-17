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

fn title(rest: &str) -> Option<&str> {
    let text = rest.trim();
    let stripped = text.trim_end_matches('#');
    let text = if stripped.is_empty() {
        ""
    } else if stripped.ends_with([' ', '\t']) {
        stripped.trim_end()
    } else {
        text
    };
    (!text.is_empty()).then_some(text)
}

/// (line start scalar offset, heading text) for every recognized heading.
fn scan(text: &str) -> impl Iterator<Item = (usize, Option<&str>)> {
    let mut offset = 0;
    let mut fence: Option<(char, usize)> = None;
    let mut preamble = Preamble::Start;
    text.split('\n').filter_map(move |raw| {
        let start = offset;
        offset += raw.chars().count() + 1;
        let line = raw.strip_suffix('\r').unwrap_or(raw);
        match preamble {
            Preamble::Front => {
                if line.trim_end() == "---" {
                    preamble = Preamble::AfterFront;
                }
                return None;
            }
            Preamble::Comment => {
                if line.contains("-->") {
                    preamble = Preamble::Body;
                }
                return None;
            }
            Preamble::Start if line.trim_end() == "---" => {
                preamble = Preamble::Front;
                return None;
            }
            Preamble::Start | Preamble::AfterFront if line.trim_start().starts_with("<!--") => {
                preamble = if line.contains("-->") {
                    Preamble::Body
                } else {
                    Preamble::Comment
                };
                return None;
            }
            _ => preamble = Preamble::Body,
        }
        let indent = line.len() - line.trim_start_matches(' ').len();
        if indent > 3 {
            return None;
        }
        let body = &line[indent..];
        if let Some((mark, length)) = fence {
            let run = body.chars().take_while(|c| *c == mark).count();
            if run >= length && body[run * mark.len_utf8()..].trim().is_empty() {
                fence = None;
            }
            return None;
        }
        if let Some(mark @ ('`' | '~')) = body.chars().next() {
            let run = body.chars().take_while(|c| *c == mark).count();
            if run >= 3 {
                fence = Some((mark, run));
                return None;
            }
        }
        let hashes = body.chars().take_while(|c| *c == '#').count();
        let rest = &body[hashes..];
        if (1..=6).contains(&hashes) && (rest.is_empty() || rest.starts_with([' ', '\t'])) {
            return Some((start, title(rest)));
        }
        None
    })
}

/// Nearest heading at or before each successive (ascending) passage offset.
pub(super) fn lookup(text: &str) -> impl FnMut(usize) -> Option<String> + '_ {
    let mut headings = scan(text).peekable();
    let mut current = None;
    move |offset| {
        while let Some((_, title)) = headings.next_if(|(start, _)| *start <= offset) {
            current = title;
        }
        current.map(|s| s.chars().take(LIMIT).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::{lookup, scan};

    #[test]
    fn headings_skip_fences_frontmatter_and_leading_comments() {
        let text = "---\ntitle: x\n# not a heading\n---\n<!-- neosian-knowledge:1\n# no\n-->\n\
                    # Intro #\nbody\n```\n# code\n```\n  ## Setup ##\n####### seven\n#tag\n\
                    ~~~~\n## fenced\n~~~\n## still fenced\n~~~~\n### Last\n";
        let found: Vec<_> = scan(text).collect();
        let titles: Vec<_> = found.iter().map(|(_, t)| *t).collect();
        assert_eq!(titles, [Some("Intro"), Some("Setup"), Some("Last")]);
        let intro = text.find("# Intro").unwrap();
        assert_eq!(found[0].0, text[..intro].chars().count());
        let mut heading = lookup(text);
        assert_eq!(heading(0), None);
        assert_eq!(heading(found[1].0 - 1).as_deref(), Some("Intro"));
        assert_eq!(heading(found[1].0).as_deref(), Some("Setup"));
    }

    #[test]
    fn closing_sequences_need_a_space_and_empty_headings_are_null() {
        let found: Vec<_> = scan("# Title#\n#\n## Done ##  \n").collect();
        let titles: Vec<_> = found.iter().map(|(_, t)| *t).collect();
        assert_eq!(titles, [Some("Title#"), None, Some("Done")]);
    }
}

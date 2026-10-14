//! The grammar's tests, and the pin that holds the bounded scans to the
//! full scan: on texts built from prose, long tokens, handles, quotes and
//! every delimiter, `links`, `contract` and `boundary` answer as the
//! unbounded `find_iter` and `replace_all` do.

use super::*;
use crate::text::len;

/// The unbounded scans, the reference the bounded ones are held to.
mod full {
    use super::*;

    pub fn links(text: &str) -> Vec<&str> {
        atom()
            .find_iter(text)
            .flatten()
            .map(|found| found.as_str())
            .filter(|token| registrable(token))
            .collect()
    }

    pub fn boundary(text: &str, at: usize) -> (usize, usize) {
        let mut cursor = Cursor {
            text,
            byte: 0,
            chars: 0,
        };
        for found in atom().find_iter(text).flatten() {
            let start = cursor.at(found.start());
            let end = cursor.at(found.end());
            if start < at && at < end {
                return (start, end);
            }
            if start >= at {
                break;
            }
        }
        (at, at)
    }

    pub fn contract(registry: &LinkRegistry, text: &str) -> String {
        atom()
            .replace_all(text, |caps: &fancy_regex::Captures<str>| {
                let token = caps.get(0).map_or("", |found| found.as_str());
                registry
                    .numbers
                    .get(token)
                    .map_or_else(|| token.to_string(), |number| registry.handle(*number))
            })
            .into_owned()
    }
}

/// A deterministic text: words, long and short tokens, handles and
/// delimiters drawn by a linear congruential generator.
fn built(seed: u64, pieces: usize) -> String {
    const VOCABULARY: [&str; 24] = [
        "the",
        "cache",
        "ttl",
        "é",
        "café",
        "deploy.",
        "v1.2.3,",
        "(x)",
        "a/b",
        "ab-12",
        "https://example.com/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w",
        "/var/log/neosian/store/documents/user/profile/history/versions/one.jsonl",
        "01HZX4YQ6B3K2M8R9T0V1W2X3Y01HZX4YQ6B3K2M8R9T0V1W2X3Y",
        "[link 3]",
        "[link c:12]",
        "\"quoted\"",
        "'single'",
        "<tag>",
        "a|b",
        "`code`",
        "back\\slash",
        "Foo_(bar)_https://x.y/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        "[link",
        "]",
    ];
    const SEPARATORS: [&str; 8] = [" ", "\n", "\u{1c}", "\t", "", ".", " \u{a0} ", ", "];
    let mut state = seed;
    let mut next = |bound: usize| {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((state >> 33) as usize) % bound
    };
    let mut text = String::new();
    for _ in 0..pieces {
        text.push_str(VOCABULARY[next(VOCABULARY.len())]);
        text.push_str(SEPARATORS[next(SEPARATORS.len())]);
    }
    text
}

#[test]
fn the_bounded_scans_answer_as_the_full_scan_does() {
    let mut texts: Vec<String> = (0..120)
        .map(|seed| built(seed, 1 + seed as usize % 40))
        .collect();
    texts.push(String::new());
    texts.push("x".repeat(LINK_CHARS));
    texts.push(format!("[link {}", "a".repeat(60)));
    texts.push(format!(
        "{} [link 1] {}",
        "b".repeat(60),
        "https://x.y/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
    ));
    for text in &texts {
        assert_eq!(links(text), full::links(text), "{text:?}");
        let turn = Turn {
            conversation_id: "c".to_string(),
            turn: 1,
            messages: vec![
                super::super::message::from_json(
                    &serde_json::json!({"role": "user", "content": text}),
                )
                .unwrap(),
            ],
            created_at: crate::store::time::Stamp::now(),
            actor: None,
            redacted: false,
        };
        let registry = LinkRegistry::of(std::slice::from_ref(&turn), Some("c"));
        assert_eq!(
            registry.contract(text),
            full::contract(&registry, text),
            "{text:?}"
        );
        for at in 0..=len(text) + 1 {
            assert_eq!(
                boundary(text, at),
                full::boundary(text, at),
                "{text:?} at {at}"
            );
        }
    }
}

fn atoms(text: &str) -> Vec<&str> {
    atom()
        .find_iter(text)
        .flatten()
        .map(|found| found.as_str())
        .collect()
}

#[test]
fn a_token_is_one_atom_and_punctuation_stays_outside() {
    for token in [
        "https://example.com/a/b?q=1#x",
        "/var/log/neosian/store.lock",
        "01HZX4YQ6B3K2M8R9T0V1W2X3Y",
        "https://en.wikipedia.org/wiki/Foo_(bar)",
        "[link 3]",
    ] {
        assert_eq!(atoms(token), [token], "{token}");
        assert_eq!(atoms(&format!("see {token}.")), [token], "{token}");
        assert_eq!(atoms(&format!("({token}),")), [token], "{token}");
    }
    assert!(atoms("plain words only here").is_empty());
    assert_eq!(
        atoms("\"https://x.y/z\" and `a/b`"),
        ["https://x.y/z", "a/b"]
    );
    assert_eq!(atoms("v1.2.3. next"), ["v1.2.3"]);
    assert_eq!(atoms("ab/cd"), ["ab/cd"]);
    assert!(atoms("xhttps://a.b/c").len() == 1);
}

#[test]
fn a_boundary_is_the_atom_the_cut_falls_in() {
    let text = "see https://example.com/a/b now";
    assert_eq!(boundary(text, 10), (4, 27));
    assert_eq!(boundary(text, 4), (4, 4));
    assert_eq!(boundary(text, 27), (27, 27));
    assert_eq!(boundary("café /x/y end", 7), (5, 9));
    assert_eq!(boundary("", 0), (0, 0));
}

#[test]
fn a_registry_numbers_long_tokens_by_first_appearance() {
    let long = format!("https://example.com/{}", "a".repeat(40));
    let turns = vec![Turn {
        conversation_id: "c".to_string(),
        turn: 1,
        messages: vec![
            super::super::message::from_json(&serde_json::json!({
                "role": "user", "content": format!("read {long} and {long} and /short")
            }))
            .unwrap(),
        ],
        created_at: crate::store::time::Stamp::now(),
        actor: None,
        redacted: false,
    }];
    let links = LinkRegistry::of(&turns, Some("c"));
    assert_eq!(links.handle(1), "[link c:1]");
    assert_eq!(
        links.contract(&format!("at {long}. [link 9] /short")),
        "at [link c:1]. [link 9] /short"
    );
    let none = LinkRegistry::of(&[], None);
    assert_eq!(none.contract(&long), long);
}

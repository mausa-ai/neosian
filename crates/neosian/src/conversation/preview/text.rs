//! A message's text pieces as the views show them: joined as `text_of`
//! joins blocks, a piece the read cut as its bounded head and tail around
//! a marker naming its record; a result digested as the released segment
//! digests it, from its head and tail when it was cut; links registered
//! from whole tokens only; arguments the read cut escaped as the released
//! segment dumps them.

use super::build::{Builder, Outcome};
use crate::assets::{CONTEXT, quoted, render};
use crate::conversation::links::{LinkRegistry, whole_tokens};
use crate::conversation::projection::{RESULT_HEAD, RESULT_TAIL, digest_of};
use crate::conversation::recall::{View, grouped};
use crate::text::{head, len, tail};

/// The most of a cut prose piece a verbatim block shows, in characters:
/// its head and its tail.
const SHOWN_HEAD: usize = 8_192;
const SHOWN_TAIL: usize = 4_096;
/// How wide `calls` and `full` draw a call's arguments, and `full` the
/// head and the tail of a result, in characters.
pub(super) const WIDE_ARGS: usize = 1_024;
const WIDE_HEAD: usize = 1_024;
const WIDE_TAIL: usize = 512;

/// One text piece of a message: whole, or the head and tail the read kept.
pub(super) struct Text {
    pub text: String,
    pub seq: u64,
    pub size: u64,
    pub tail: Option<String>,
}

impl Builder {
    /// A message's text pieces joined as `text_of` joins its blocks, a
    /// cut one as its bounded head and tail around the marker.
    pub(super) fn verbatim(&self, texts: &[Text], turn: u64) -> String {
        let shown: Vec<String> = texts.iter().map(|text| self.shown(text, turn)).collect();
        shown.join("\n")
    }

    fn shown(&self, text: &Text, turn: u64) -> String {
        let Some(back) = &text.tail else {
            return text.text.clone();
        };
        let front = head(&text.text, SHOWN_HEAD);
        let back = tail(back, len(back).saturating_sub(SHOWN_TAIL));
        let omitted = text.size.saturating_sub((front.len() + back.len()) as u64);
        let template = quoted(CONTEXT, "continue_omitted").expect("context.yaml has the marker");
        let marker = render(
            &template,
            &[
                ("bytes", &grouped(omitted)),
                ("size", &grouped(text.size)),
                ("turn", &turn.to_string()),
                ("conversation_id", &self.conversation),
                ("seq", &text.seq.to_string()),
            ],
        );
        format!("{front}{marker}{back}")
    }

    /// A tool message's result: digested as the released segment does
    /// when its text is whole, from the head and tail when it was cut.
    pub(super) fn outcome(&self, texts: &[Text]) -> Outcome {
        let joins = texts.len().saturating_sub(1) as u64;
        let size = texts.iter().map(|text| text.size).sum::<u64>() + joins;
        if self.view == View::Calls {
            let (digest, wide) = (String::new(), String::new());
            return Outcome { digest, wide, size };
        }
        let raw: Vec<String> = texts
            .iter()
            .map(|text| match &text.tail {
                None => text.text.clone(),
                Some(tail) => format!("{} {tail}", text.text),
            })
            .collect();
        let raw = raw.join("\n");
        let wide = if self.view == View::Full {
            digest_of(&raw, &self.links, WIDE_HEAD, WIDE_TAIL)
        } else {
            String::new()
        };
        Outcome {
            digest: digest_of(&raw, &self.links, RESULT_HEAD, RESULT_TAIL),
            wide,
            size,
        }
    }
}

/// A piece's links: all of a whole one, the whole tokens of a cut one.
pub(super) fn register(links: &mut LinkRegistry, text: &str, tail: Option<&str>) {
    match tail {
        None => links.register(text),
        Some(tail) => {
            let (front, back) = whole_tokens(text, tail);
            links.register(front);
            links.register(back);
        }
    }
}

/// Compact JSON text with every character past ASCII escaped, as the
/// released segment dumps arguments; for arguments the read cut.
pub(super) fn escaped(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        if ch.is_ascii() {
            out.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut [0; 2]) {
                out.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    out
}

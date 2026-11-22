//! The check of a JSON piece as its chunks land (§40.54 item 9, with the
//! T1b1a note): compact JSON only, depth at most 100, an object for a call's
//! arguments, the codec's block rules for an attachment. The state between
//! chunks, and between requests, is a container stack and a few bytes: no
//! string is kept beyond a key or a block type of at most 24 bytes, and a
//! number of at most 1,100 characters.

use serde::{Deserialize, Serialize};

use super::Kind;

mod block;
mod string;

use block::Block;
use string::Esc;

/// Deepest nesting a piece may hold: well inside serde_json's 127, which
/// the piece meets again wrapped in a turn, a row or an answer.
pub const DEPTH: usize = 100;
const NAME: usize = 24;
const NUMBER: usize = 1_100;

/// What a piece's value must be.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Shape {
    Any,
    Object,
    Block,
}

impl Shape {
    pub fn of(kind: Kind) -> Option<Self> {
        match kind {
            Kind::ToolCall => Some(Self::Object),
            Kind::Attachment => Some(Self::Block),
            Kind::Reasoning | Kind::Extra => Some(Self::Any),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
enum Next {
    Value,
    ValueOrClose,
    KeyOrClose,
    Key,
    Colon,
    CommaOrClose,
    Done,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
enum Num {
    Minus,
    Zero,
    Int,
    Dot,
    Frac,
    E,
    Sign,
    Exp,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
enum Token {
    None,
    Str { key: bool, esc: Esc },
    Num { at: Num, text: String },
    Word { word: u8, at: u8 },
}

/// One JSON piece's check, fed chunk by chunk.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct JsonCheck {
    shape: Shape,
    stack: Vec<u8>,
    next: Next,
    token: Token,
    started: bool,
    block: Block,
}

const WORDS: [&str; 3] = ["true", "false", "null"];

fn bad(why: &str) -> String {
    format!("not compact JSON: {why}")
}

impl JsonCheck {
    pub fn new(shape: Shape) -> Self {
        Self {
            shape,
            stack: Vec::new(),
            next: Next::Value,
            token: Token::None,
            started: false,
            block: Block::default(),
        }
    }

    /// The next chunk of the piece's text.
    pub fn feed(&mut self, text: &str) -> Result<(), String> {
        text.chars().try_for_each(|ch| self.char(ch))
    }

    /// The piece is complete: its value whole, its shape right.
    pub fn finish(mut self) -> Result<(), String> {
        if matches!(self.token, Token::Num { .. }) {
            self.end_number()?;
        }
        if self.next != Next::Done || self.token != Token::None {
            return Err(bad("the value ends early"));
        }
        if self.shape == Shape::Block {
            self.block.rules()
        } else {
            Ok(())
        }
    }

    fn top(&self) -> bool {
        self.stack.len() == 1
    }

    fn char(&mut self, ch: char) -> Result<(), String> {
        match self.token.clone() {
            Token::Str { key, esc } => return self.string(ch, key, esc),
            Token::Word { word, at } => {
                let spelled = WORDS[word as usize].as_bytes();
                if spelled.get(at as usize) != Some(&(ch as u8)) || !ch.is_ascii() {
                    return Err(bad("an unknown word"));
                }
                self.token = if at as usize + 1 == spelled.len() {
                    self.value_done();
                    Token::None
                } else {
                    Token::Word { word, at: at + 1 }
                };
                return Ok(());
            }
            Token::Num { at, mut text } => {
                if let Some(state) = number(at, ch) {
                    text.push(ch);
                    if text.len() > NUMBER {
                        return Err(bad("a number longer than 1,100 characters"));
                    }
                    self.token = Token::Num { at: state, text };
                    return Ok(());
                }
                self.end_number()?;
            }
            Token::None => {}
        }
        self.structure(ch)
    }

    fn structure(&mut self, ch: char) -> Result<(), String> {
        match (self.next, ch) {
            (Next::Value | Next::ValueOrClose, _) if ch != ']' => self.value(ch),
            (Next::ValueOrClose | Next::CommaOrClose, ']') if self.stack.last() == Some(&b'[') => {
                self.close()
            }
            (Next::KeyOrClose | Next::CommaOrClose, '}') if self.stack.last() == Some(&b'{') => {
                self.close()
            }
            (Next::KeyOrClose | Next::Key, '"') => {
                if self.top() && self.shape == Shape::Block {
                    self.block.held = Some(String::new());
                }
                self.token = Token::Str {
                    key: true,
                    esc: Esc::No,
                };
                Ok(())
            }
            (Next::Colon, ':') => {
                self.next = Next::Value;
                Ok(())
            }
            (Next::CommaOrClose, ',') => {
                self.next = if self.stack.last() == Some(&b'{') {
                    Next::Key
                } else {
                    Next::Value
                };
                Ok(())
            }
            _ => Err(bad(&format!("{ch:?} out of place"))),
        }
    }

    fn value(&mut self, ch: char) -> Result<(), String> {
        let first = !self.started;
        self.started = true;
        if first && self.shape != Shape::Any && ch != '{' {
            return Err(match self.shape {
                Shape::Object => "arguments must be an object".to_string(),
                _ => "the attachment is not a content block".to_string(),
            });
        }
        if self.top() && self.shape == Shape::Block {
            self.block.start_value(ch);
        }
        match ch {
            '{' | '[' => {
                if self.stack.len() == DEPTH {
                    return Err(bad("nested deeper than 100"));
                }
                self.stack.push(ch as u8);
                self.next = if ch == '{' {
                    Next::KeyOrClose
                } else {
                    Next::ValueOrClose
                };
            }
            '"' => {
                self.token = Token::Str {
                    key: false,
                    esc: Esc::No,
                }
            }
            '-' => {
                self.token = Token::Num {
                    at: Num::Minus,
                    text: "-".into(),
                }
            }
            '0' => {
                self.token = Token::Num {
                    at: Num::Zero,
                    text: "0".into(),
                }
            }
            '1'..='9' => {
                self.token = Token::Num {
                    at: Num::Int,
                    text: ch.to_string(),
                }
            }
            't' | 'f' | 'n' => {
                let word = b"tfn".iter().position(|w| *w == ch as u8).unwrap() as u8;
                self.token = Token::Word { word, at: 1 };
            }
            _ => return Err(bad(&format!("{ch:?} cannot start a value"))),
        }
        Ok(())
    }

    fn close(&mut self) -> Result<(), String> {
        self.stack.pop();
        self.value_done();
        Ok(())
    }

    /// A value ended: the container's separator or close is next.
    fn value_done(&mut self) {
        if self.top() && self.shape == Shape::Block {
            self.block.field = None;
        }
        self.next = if self.stack.is_empty() {
            Next::Done
        } else {
            Next::CommaOrClose
        };
    }

    fn end_number(&mut self) -> Result<(), String> {
        let Token::Num { at, text } = std::mem::replace(&mut self.token, Token::None) else {
            return Ok(());
        };
        if !matches!(at, Num::Zero | Num::Int | Num::Frac | Num::Exp) {
            return Err(bad("a number ends early"));
        }
        let whole = text.parse::<i64>().is_ok() || text.parse::<u64>().is_ok();
        if !whole && !text.parse::<f64>().is_ok_and(f64::is_finite) {
            return Err(bad("a number out of range"));
        }
        self.value_done();
        Ok(())
    }
}

/// The number state after `ch`, or None when `ch` ends the number.
fn number(at: Num, ch: char) -> Option<Num> {
    Some(match (at, ch) {
        (Num::Minus, '0') => Num::Zero,
        (Num::Minus, '1'..='9') | (Num::Int, '0'..='9') => Num::Int,
        (Num::Zero | Num::Int, '.') => Num::Dot,
        (Num::Dot | Num::Frac, '0'..='9') => Num::Frac,
        (Num::Zero | Num::Int | Num::Frac, 'e' | 'E') => Num::E,
        (Num::E, '+' | '-') => Num::Sign,
        (Num::E | Num::Sign | Num::Exp, '0'..='9') => Num::Exp,
        _ => return None,
    })
}

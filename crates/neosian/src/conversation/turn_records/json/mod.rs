//! The check of a JSON piece as its chunks land (§40.54 item 9, with the
//! T1b1a note and the corrections after the review of 2481787): compact
//! JSON in the codec's own spelling only (numbers, escapes, no key twice in
//! an object within a bound), nested no deeper than a format-1 row holds
//! the piece, an object for a call's arguments, the codec's block rules for
//! an attachment. The state between chunks, and between requests, is a
//! container stack and a few bytes: of strings, a key or a block type of at
//! most 24 bytes, a number of at most 1,100 characters, and the keys of the
//! open objects up to 4,096 bytes.

use serde::{Deserialize, Serialize};

mod block;
mod keys;
mod number;
mod shape;
mod string;

use block::Block;
use keys::Keys;
use number::Num;
pub use shape::{Shape, Top, depth};
use string::Esc;

const NAME: usize = 24;
const NUMBER: usize = 1_100;

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
    depth: usize,
    stack: Vec<u8>,
    next: Next,
    token: Token,
    started: bool,
    block: Block,
    keys: Keys,
    /// Whether the outermost string or container holds anything.
    filled: bool,
    top: Top,
}

const WORDS: [&str; 3] = ["true", "false", "null"];

fn bad(why: &str) -> String {
    format!("not compact JSON: {why}")
}

impl JsonCheck {
    pub fn new(shape: Shape, depth: usize) -> Self {
        Self {
            shape,
            depth,
            stack: Vec::new(),
            next: Next::Value,
            token: Token::None,
            started: false,
            block: Block::default(),
            keys: Keys::default(),
            filled: false,
            top: Top::default(),
        }
    }

    /// The next chunk of the piece's text.
    pub fn feed(&mut self, text: &str) -> Result<(), String> {
        text.chars().try_for_each(|ch| self.char(ch))
    }

    /// The piece is complete: its value whole, its shape right; what it was.
    pub fn finish(mut self) -> Result<Top, String> {
        if matches!(self.token, Token::Num { .. }) {
            self.end_number()?;
        }
        if self.next != Next::Done || self.token != Token::None {
            return Err(bad("the value ends early"));
        }
        if self.shape == Shape::Block {
            self.block.rules()?;
        }
        Ok(self.top)
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
                    if self.stack.is_empty() {
                        self.top = [Top::Truthy, Top::Falsy, Top::Null][word as usize];
                    }
                    self.value_done();
                    Token::None
                } else {
                    Token::Word { word, at: at + 1 }
                };
                return Ok(());
            }
            Token::Num { at, mut text } => {
                if let Some(state) = number::next(at, ch) {
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
                if self.top() {
                    self.filled = true;
                    if self.shape == Shape::Block {
                        self.block.held = Some(String::new());
                    }
                }
                self.keys.start();
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
        if self.top() {
            self.filled = true;
            if self.shape == Shape::Block {
                self.block.start_value(ch);
            }
        }
        match ch {
            '{' | '[' => {
                if self.stack.len() == self.depth {
                    return Err(bad(&format!("nested deeper than {}", self.depth)));
                }
                self.stack.push(ch as u8);
                self.next = if ch == '{' {
                    self.keys.open();
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
            '-' | '0'..='9' => {
                let at = match ch {
                    '-' => Num::Minus,
                    '0' => Num::Zero,
                    _ => Num::Int,
                };
                self.token = Token::Num {
                    at,
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
        if self.stack.pop() == Some(b'{') {
            self.keys.close();
        }
        if self.stack.is_empty() {
            self.top = if self.filled { Top::Truthy } else { Top::Falsy };
        }
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
        if !number::whole(at) {
            return Err(bad("a number ends early"));
        }
        number::spelled(&text).map_err(bad)?;
        if self.stack.is_empty() && text.parse::<f64>() == Ok(0.0) {
            self.top = Top::Falsy;
        }
        self.value_done();
        Ok(())
    }
}

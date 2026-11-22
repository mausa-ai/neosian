//! Strings inside a JSON piece: escapes, surrogate pairs, raw control
//! characters refused, and the few bytes of a key or a block type kept.

use serde::{Deserialize, Serialize};

use super::{JsonCheck, NAME, Next, Shape, Token, bad};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Esc {
    No,
    Slash,
    Hex {
        left: u8,
        value: u16,
        high: Option<u16>,
    },
    Low {
        high: u16,
        slash: bool,
    },
}

impl JsonCheck {
    pub(super) fn string(&mut self, ch: char, key: bool, esc: Esc) -> Result<(), String> {
        let (decoded, esc) = match esc {
            Esc::No => match ch {
                '"' => return self.string_done(key),
                '\\' => (None, Esc::Slash),
                ch if ch < ' ' => return Err(bad("a raw control character in a string")),
                ch => (Some(ch), Esc::No),
            },
            Esc::Slash => match ch {
                'u' => (
                    None,
                    Esc::Hex {
                        left: 4,
                        value: 0,
                        high: None,
                    },
                ),
                '"' | '\\' | '/' => (Some(ch), Esc::No),
                'b' | 'f' | 'n' | 'r' | 't' => (Some(' '), Esc::No),
                _ => return Err(bad("an unknown escape")),
            },
            Esc::Hex { left, value, high } => {
                let digit = ch.to_digit(16).ok_or_else(|| bad("a short \\u escape"))? as u16;
                let value = value * 16 + digit;
                if left > 1 {
                    (
                        None,
                        Esc::Hex {
                            left: left - 1,
                            value,
                            high,
                        },
                    )
                } else {
                    match (high, value) {
                        (Some(high), 0xDC00..=0xDFFF) => {
                            let code = 0x10000
                                + ((u32::from(high) - 0xD800) << 10)
                                + (u32::from(value) - 0xDC00);
                            (char::from_u32(code), Esc::No)
                        }
                        (Some(_), _) | (None, 0xDC00..=0xDFFF) => {
                            return Err(bad("a lone surrogate"));
                        }
                        (None, 0xD800..=0xDBFF) => (
                            None,
                            Esc::Low {
                                high: value,
                                slash: false,
                            },
                        ),
                        (None, value) => (char::from_u32(u32::from(value)), Esc::No),
                    }
                }
            }
            Esc::Low { high, slash: false } if ch == '\\' => (None, Esc::Low { high, slash: true }),
            Esc::Low { high, slash: true } if ch == 'u' => (
                None,
                Esc::Hex {
                    left: 4,
                    value: 0,
                    high: Some(high),
                },
            ),
            Esc::Low { .. } => return Err(bad("a lone surrogate")),
        };
        if let (Some(ch), Some(held)) = (decoded, self.block.held.as_mut()) {
            if held.len() + ch.len_utf8() <= NAME {
                held.push(ch);
            } else {
                // Past what any name the rules read can be: an unknown key, or no block type.
                self.block.held = None;
            }
        }
        self.token = Token::Str { key, esc };
        Ok(())
    }

    fn string_done(&mut self, key: bool) -> Result<(), String> {
        self.token = Token::None;
        if key {
            if self.top() && self.shape == Shape::Block {
                self.block.key_done();
            }
            self.next = Next::Colon;
        } else {
            if self.top() && self.shape == Shape::Block {
                self.block.string_done();
            }
            self.value_done();
        }
        Ok(())
    }
}

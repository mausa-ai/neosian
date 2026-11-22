//! Strings inside a JSON piece: raw control characters refused, and only
//! the escapes the codec writes (review finding 10 of 2481787): `\"`,
//! `\\`, `\b`, `\f`, `\n`, `\r`, `\t`, and `\u00xx` in lowercase for the
//! other control characters; everything else raw, as `pyfmt` writes it
//! with non-ASCII raw. The few bytes of a key or a block type are kept.

use serde::{Deserialize, Serialize};

use super::{JsonCheck, NAME, Next, Shape, Token, Top, bad};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Esc {
    No,
    Slash,
    Hex { left: u8, value: u16 },
}

fn uncanonical() -> String {
    bad("an escape the codec does not write")
}

impl JsonCheck {
    pub(super) fn string(&mut self, ch: char, key: bool, esc: Esc) -> Result<(), String> {
        let closing = esc == Esc::No && ch == '"';
        if !closing {
            if key {
                self.keys.push(ch);
            } else if self.stack.is_empty() {
                self.filled = true;
            }
        }
        let (decoded, esc) = match esc {
            Esc::No => match ch {
                '"' => return self.string_done(key),
                '\\' => (None, Esc::Slash),
                ch if ch < ' ' => return Err(bad("a raw control character in a string")),
                ch => (Some(ch), Esc::No),
            },
            Esc::Slash => match ch {
                'u' => (None, Esc::Hex { left: 4, value: 0 }),
                '"' | '\\' => (Some(ch), Esc::No),
                'b' => (Some('\u{8}'), Esc::No),
                'f' => (Some('\u{c}'), Esc::No),
                'n' => (Some('\n'), Esc::No),
                'r' => (Some('\r'), Esc::No),
                't' => (Some('\t'), Esc::No),
                '/' => return Err(uncanonical()),
                _ => return Err(bad("an unknown escape")),
            },
            Esc::Hex { left, value } => {
                let digit = ch.to_digit(16).ok_or_else(|| bad("a short \\u escape"))? as u16;
                if ch.is_ascii_uppercase() {
                    return Err(uncanonical());
                }
                let value = value * 16 + digit;
                if left > 1 {
                    (
                        None,
                        Esc::Hex {
                            left: left - 1,
                            value,
                        },
                    )
                } else if value >= 0x20 || matches!(value, 0x08 | 0x09 | 0x0a | 0x0c | 0x0d) {
                    return Err(uncanonical());
                } else {
                    (char::from_u32(u32::from(value)), Esc::No)
                }
            }
        };
        if let Some(ch) = decoded.filter(|_| !key && self.top() && self.shape == Shape::Block) {
            self.block.value_char(ch)?;
        }
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
            self.keys.done().map_err(bad)?;
            if self.top() && self.shape == Shape::Block {
                self.block.key_done()?;
            }
            self.next = Next::Colon;
        } else {
            if self.top() && self.shape == Shape::Block {
                self.block.string_done();
            }
            if self.stack.is_empty() {
                self.top = if self.filled { Top::Truthy } else { Top::Falsy };
            }
            self.value_done();
        }
        Ok(())
    }
}

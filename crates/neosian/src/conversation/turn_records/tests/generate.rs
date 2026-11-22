//! A seeded generator of messages over every shape the message codec
//! reads: each role, absent, null, text and block content, every block
//! type, tool calls with and without arguments and extras, reasoning and
//! extras both truthy and falsy, ids up to the 1,024-byte bound, and texts
//! past the record bound. No dependency; the same messages on every run.

use serde_json::{Map, Value, json};

/// splitmix64.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed ^ 0x5851_f42d_4c95_7f2d)
    }

    pub fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    pub fn below(&mut self, bound: u64) -> u64 {
        self.next() % bound.max(1)
    }

    pub fn one_in(&mut self, bound: u64) -> bool {
        self.below(bound) == 0
    }
}

/// Characters chosen for how they encode and how the split rule treats
/// them: escapes, other control characters, the four separators Python
/// counts as space, Unicode spaces, multi-byte characters.
pub const CHARS: &[char] = &[
    'a', 'q', 'Z', '7', ' ', ' ', '\n', '\t', '\r', '"', '\\', '/', '\u{0}', '\u{1}', '\u{8}',
    '\u{c}', '\u{1c}', '\u{1f}', '\u{7f}', '\u{a0}', '\u{2028}', '\u{3000}', 'é', 'ß', '中', '😀',
    '{', ':',
];

fn char(rng: &mut Rng) -> char {
    CHARS[rng.below(CHARS.len() as u64) as usize]
}

pub fn text(rng: &mut Rng, most: u64) -> String {
    (0..rng.below(most + 1)).map(|_| char(rng)).collect()
}

/// A text past the record bound: lines, runs of one character (forced
/// cuts, control characters six bytes each) and short mixed stretches.
pub fn long_text(rng: &mut Rng) -> String {
    let target = 60_000 + rng.below(140_000) as usize;
    let mut out = String::with_capacity(target + 80_000);
    while out.len() < target {
        match rng.below(3) {
            0 => {
                out.push_str(&text(rng, 80));
                out.push('\n');
            }
            1 => {
                let ch = char(rng);
                (0..=rng.below(20_000)).for_each(|_| out.push(ch));
            }
            _ => out.push_str(&text(rng, 400)),
        }
    }
    out
}

/// A float whose spelling serde_json reads back exactly: nine significant
/// digits, between 1e-12 and 1e13, so that even the trailing `.0` the
/// codec writes keeps the mantissa within 2^53. Larger spellings can read
/// back one unit off, in a format-1 line as in a record; the float
/// property in `properties.rs` covers every float against that.
fn decimal(rng: &mut Rng) -> Value {
    let sign = if rng.one_in(2) { "-" } else { "" };
    let text = format!(
        "{sign}{}e{}",
        rng.below(1_000_000_000),
        rng.below(17) as i64 - 12
    );
    serde_json::from_str(&text).expect("a decimal")
}

pub fn json(rng: &mut Rng, depth: u32) -> Value {
    match rng.below(if depth == 0 { 6 } else { 8 }) {
        0 => Value::Null,
        1 => json!(rng.one_in(2)),
        2 => json!((rng.next() as i64) >> rng.below(64)),
        3 => json!(rng.next() >> rng.below(64)),
        4 => decimal(rng),
        5 => json!(text(rng, 12)),
        6 => Value::Array((0..rng.below(4)).map(|_| json(rng, depth - 1)).collect()),
        _ => object(rng, depth - 1),
    }
}

pub fn object(rng: &mut Rng, depth: u32) -> Value {
    let mut map = Map::new();
    for _ in 0..rng.below(4) {
        map.insert(text(rng, 6), json(rng, depth));
    }
    Value::Object(map)
}

fn falsy(rng: &mut Rng) -> Value {
    [
        json!(null),
        json!(false),
        json!(0),
        json!(0.0),
        json!(""),
        json!([]),
        json!({}),
    ][rng.below(7) as usize]
        .clone()
}

fn id(rng: &mut Rng) -> String {
    match rng.below(12) {
        0 => "i".repeat(1_024),
        1 => "é".repeat(512),
        _ => text(rng, 20),
    }
}

fn body(rng: &mut Rng, big: bool) -> String {
    if big && rng.one_in(2) {
        long_text(rng)
    } else {
        text(rng, 40)
    }
}

fn block(rng: &mut Rng, big: bool) -> Value {
    match rng.below(5) {
        0 | 1 => json!({"type": "text", "text": body(rng, big)}),
        2 => json!({"type": "image", "media_type": "image/png", "data": "iVBO Rw0K\nGgo="}),
        3 => json!({"type": "document", "url": text(rng, 30)}),
        _ => {
            json!({"type": "compaction", "content": json(rng, 2), "encrypted_content": json(rng, 1)})
        }
    }
}

fn call(rng: &mut Rng, big: bool) -> Value {
    let mut map = Map::new();
    map.insert("id".into(), json!(id(rng)));
    map.insert("name".into(), json!(id(rng)));
    match rng.below(4) {
        0 => {}
        1 => _ = map.insert("arguments".into(), Value::Null),
        _ if big && rng.one_in(2) => {
            _ = map.insert("arguments".into(), json!({"input": long_text(rng)}))
        }
        _ => _ = map.insert("arguments".into(), object(rng, 3)),
    }
    match rng.below(3) {
        0 => {}
        1 => _ = map.insert("extra".into(), falsy(rng)),
        _ => _ = map.insert("extra".into(), object(rng, 2)),
    }
    Value::Object(map)
}

/// One message as a client would send it, read through the message codec
/// by the caller.
pub fn message(rng: &mut Rng, big: bool) -> Value {
    let mut map = Map::new();
    let role = ["user", "assistant", "system", "tool"][rng.below(4) as usize];
    map.insert("role".into(), json!(role));
    let content = match rng.below(4) {
        0 => None,
        1 => Some(Value::Null),
        2 => Some(json!(body(rng, big))),
        _ => Some(Value::Array(
            (0..rng.below(5)).map(|_| block(rng, big)).collect(),
        )),
    };
    let calls = rng
        .one_in(2)
        .then(|| Value::Array((0..rng.below(4)).map(|_| call(rng, big)).collect()));
    let reasoning = match rng.below(4) {
        0 => None,
        1 => Some(Value::Null),
        2 => Some(json(rng, 2)),
        _ => Some(json!(text(rng, 30))),
    };
    let tool_call_id = match rng.below(4) {
        0 => None,
        1 => Some(json!("")),
        _ => Some(json!(id(rng))),
    };
    let extra = match rng.below(3) {
        0 => None,
        1 => Some(falsy(rng)),
        _ => Some(object(rng, 2)),
    };
    let fields = [
        ("content", content),
        ("tool_calls", calls),
        ("reasoning", reasoning),
        ("tool_call_id", tool_call_id),
        ("extra", extra),
    ];
    for (key, value) in fields {
        if let Some(value) = value {
            map.insert(key.into(), value);
        }
    }
    Value::Object(map)
}

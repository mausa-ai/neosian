//! An attachment's top-level fields, kept as the bytes its block rules need
//! (the codec's `message::block`), the rules themselves, and the layout the
//! codec writes (`message::block_json`, found while answering the review of
//! 2481787): `type` first, then every field of its kind in order, null ones
//! included, none other, and base64 data without white space, since the
//! codec takes it out. A block laid out otherwise would read back otherwise
//! than its record holds.

use serde::{Deserialize, Serialize};

/// A top-level field an attachment's block rules read.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Field {
    Type,
    MediaType,
    Data,
    Url,
    Other,
}

/// What a field held: absent, null, a string, or something else.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub(super) enum Class {
    #[default]
    Absent,
    Null,
    Str,
    Other,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub(super) struct Block {
    pub(super) field: Option<Field>,
    pub(super) held: Option<String>,
    pub(super) kind: Option<String>,
    pub(super) media_type: Class,
    pub(super) data: Class,
    pub(super) url: Class,
    /// Top-level keys read so far.
    keys: u8,
}

const MEDIA: [&str; 4] = ["type", "media_type", "data", "url"];
const COMPACTION: [&str; 3] = ["type", "content", "encrypted_content"];

fn laid_out() -> String {
    "the attachment is not laid out as the codec writes it".into()
}

impl Block {
    /// The keys the codec writes for the block's kind, once known.
    fn layout(&self) -> Option<&'static [&'static str]> {
        match self.kind.as_deref() {
            Some("image" | "document") => Some(&MEDIA),
            Some("compaction") => Some(&COMPACTION),
            _ => None,
        }
    }

    pub(super) fn key_done(&mut self) -> Result<(), String> {
        let name = self.held.take();
        let at = usize::from(self.keys);
        self.keys = self.keys.saturating_add(1);
        let expected = if at == 0 {
            Some("type")
        } else {
            self.layout()
                .map(|keys| keys.get(at).copied().unwrap_or(""))
        };
        if expected.is_some_and(|key| name.as_deref() != Some(key)) {
            return Err(laid_out());
        }
        self.field = Some(match name.as_deref() {
            Some("type") => Field::Type,
            Some("media_type") => Field::MediaType,
            Some("data") => Field::Data,
            Some("url") => Field::Url,
            _ => Field::Other,
        });
        Ok(())
    }

    pub(super) fn start_value(&mut self, ch: char) {
        let class = match ch {
            'n' => Class::Null,
            '"' => Class::Str,
            _ => Class::Other,
        };
        match self.field {
            Some(Field::Type) if class == Class::Str => self.held = Some(String::new()),
            Some(Field::Type) => self.kind = Some(String::new()),
            Some(Field::MediaType) => self.media_type = class,
            Some(Field::Data) => self.data = class,
            Some(Field::Url) => self.url = class,
            _ => {}
        }
    }

    /// A character of a top-level string value: base64 data holds no white
    /// space as the codec writes it.
    pub(super) fn value_char(&self, ch: char) -> Result<(), String> {
        if self.field == Some(Field::Data) && crate::text::is_space(ch) {
            return Err(laid_out());
        }
        Ok(())
    }

    pub(super) fn string_done(&mut self) {
        if self.field == Some(Field::Type) {
            self.kind = Some(self.held.take().unwrap_or_default());
        }
        self.held = None;
    }

    /// The codec's block rules (`message::block`), a text block refused;
    /// then every field of its layout present.
    pub(super) fn rules(&self) -> Result<(), String> {
        let name = match self.kind.as_deref() {
            Some("text") => return Err("an attachment holds a block other than text".into()),
            Some("compaction") => None,
            Some("image") => Some("ImageBlock"),
            Some("document") => Some("DocumentBlock"),
            Some(other) => return Err(format!("Unknown content block type: {other:?}")),
            None => return Err("Unknown content block type: None".into()),
        };
        if let Some(name) = name {
            for (key, class) in [
                ("media_type", self.media_type),
                ("data", self.data),
                ("url", self.url),
            ] {
                if class == Class::Other {
                    return Err(format!("{name} {key} must be a string"));
                }
            }
            if (self.data == Class::Str) == (self.url == Class::Str) {
                return Err(format!("{name} requires exactly one of 'data' or 'url'"));
            }
            if self.data == Class::Str && self.media_type != Class::Str {
                return Err(format!("{name} requires media_type with base64 data"));
            }
        }
        if self.layout().map(<[&str]>::len) != Some(usize::from(self.keys)) {
            return Err(laid_out());
        }
        Ok(())
    }
}

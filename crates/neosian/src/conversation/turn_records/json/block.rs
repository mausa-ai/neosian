//! An attachment's top-level fields, kept as the bytes its block rules need
//! (the codec's `message::block`), and the rules themselves.

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
}

impl Block {
    pub(super) fn key_done(&mut self) {
        let name = self.held.take();
        self.field = Some(match name.as_deref() {
            Some("type") => Field::Type,
            Some("media_type") => Field::MediaType,
            Some("data") => Field::Data,
            Some("url") => Field::Url,
            _ => Field::Other,
        });
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

    pub(super) fn string_done(&mut self) {
        if self.field == Some(Field::Type) {
            self.kind = Some(self.held.take().unwrap_or_default());
        }
        self.held = None;
    }

    /// The codec's block rules (`message::block`), a text block refused.
    pub(super) fn rules(&self) -> Result<(), String> {
        let name = match self.kind.as_deref() {
            Some("text") => return Err("an attachment holds a block other than text".into()),
            Some("compaction") => return Ok(()),
            Some("image") => "ImageBlock",
            Some("document") => "DocumentBlock",
            Some(other) => return Err(format!("Unknown content block type: {other:?}")),
            None => return Err("Unknown content block type: None".into()),
        };
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
        Ok(())
    }
}

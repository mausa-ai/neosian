//! The memory-search CLI shares its validation and result with MCP.

use clap::Args;
use std::io::Write;

use super::{Request, mounted};
use crate::memory::dispatch::Memory;
use crate::store::error::Error;

#[derive(Args)]
#[group(id = "MemorySearchOptions")]
pub struct Search {
    /// Whitespace-separated terms; every term must match a selected field.
    query: String,
    /// Mounted path prefix; / searches all mounted memory.
    #[arg(long, default_value = "/")]
    path: String,
    /// Select fields; repeat for multiple fields (default: path and content).
    #[arg(long = "field", value_parser = ["path", "content", "metadata"])]
    fields: Vec<String>,
    /// Top-level extra key whose string values are searched; requires --field metadata.
    #[arg(long = "metadata-key")]
    metadata_keys: Vec<String>,
    /// Hits per page, between 1 and 100.
    #[arg(long, default_value_t = 20, value_parser = clap::value_parser!(u32).range(1..=100))]
    limit: u32,
    /// Opaque continuation returned by the previous identical query.
    #[arg(long)]
    cursor: Option<String>,
}

impl Search {
    fn request(&self) -> Request {
        Request {
            query: self.query.clone(),
            prefix: String::new(),
            fields: if self.fields.is_empty() {
                super::fields()
            } else {
                self.fields.clone()
            },
            metadata_keys: self.metadata_keys.clone(),
            limit: self.limit as usize,
            cursor: self.cursor.clone(),
        }
    }
    pub fn validate(&self, mounts: &[crate::memory::mounts::Mount]) -> Result<(), String> {
        self.request().validate()?;
        mounted::selection(mounts, &self.path)
            .map_err(|e| crate::conversation::Error::from(e).text())?;
        Ok(())
    }
    pub fn run(&self, memory: &Memory, json: bool, out: &mut impl Write) -> Result<u8, Error> {
        let page = mounted::run(memory, &self.path, &self.request())?;
        if json {
            writeln!(
                out,
                "{}",
                serde_json::to_string(&page).expect("search page serializes")
            )?;
        } else {
            for hit in page.hits {
                writeln!(
                    out,
                    "{} (v{}, scope={}, actor={}):\n{}",
                    hit.reference.as_deref().unwrap_or(&hit.path),
                    hit.version,
                    hit.scope,
                    hit.actor.as_deref().unwrap_or("-"),
                    hit.excerpt.text
                )?;
            }
            if let Some(cursor) = page.next_cursor {
                writeln!(out, "next_cursor: {cursor}")?;
            }
        }
        Ok(0)
    }
}

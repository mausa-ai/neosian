//! Machine contracts and operator-owned schema DDL, without a Python package.

use clap::{Args, ValueEnum};
use std::io::{self, Write};

#[derive(Clone, ValueEnum)]
pub enum Kind {
    State,
    Wire,
    Errors,
    Postgres,
    Profile,
    Grants,
    Knowledge,
    Skill,
}

#[derive(Args)]
pub struct Schema {
    kind: Kind,
    /// Schema name for Postgres SQL (default: neosian).
    #[arg(long)]
    schema: Option<String>,
}

pub fn run(args: &Schema, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    if args.schema.is_some() && !matches!(args.kind, Kind::Postgres) {
        return crate::cli::usage(false, "--schema applies only to postgres", out, err);
    }
    let text = match args.kind {
        Kind::State => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/state-v1.schema.json"
        ))
        .to_string(),
        Kind::Wire => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/wire-v6.openapi.json"
        ))
        .to_string(),
        Kind::Errors => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/error-codes.json"
        ))
        .to_string(),
        Kind::Profile => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/profile-v1.schema.json"
        ))
        .to_string(),
        Kind::Grants => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/grants-v1.schema.json"
        ))
        .to_string(),
        Kind::Skill => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/skill-v1.schema.json"
        ))
        .to_string(),
        Kind::Knowledge => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/knowledge-v1.schema.json"
        ))
        .to_string(),
        Kind::Postgres => {
            let name = args.schema.as_deref().unwrap_or("neosian");
            if let Err(error) = crate::postgres::validate_schema(name) {
                return crate::cli::usage(false, &error.message, out, err);
            }
            include_str!(concat!(env!("NEOSIAN_ASSETS"), "/sql/postgres.sql"))
                .replace("{{schema}}", &format!("\"{name}\""))
                .replace("{{version}}", "4")
        }
    };
    write!(out, "{text}")?;
    Ok(0)
}

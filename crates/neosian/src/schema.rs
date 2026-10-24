//! Machine contracts and operator-owned schema DDL, without a Python package.

use clap::{Args, ValueEnum};
use std::io::{self, Write};

#[derive(Clone, ValueEnum)]
pub enum Kind {
    State,
    Wire,
    Errors,
    Postgres,
    ChangesPostgres,
    Changes,
    Exchange,
    Profile,
    Grants,
    Knowledge,
    Skill,
    Upkeep,
    Governance,
}

#[derive(Args)]
pub struct Schema {
    kind: Kind,
    /// Schema name for Postgres SQL (default: neosian).
    #[arg(long)]
    schema: Option<String>,
}

pub fn run(args: &Schema, out: &mut impl Write, err: &mut impl Write) -> io::Result<u8> {
    if args.schema.is_some() && !matches!(args.kind, Kind::Postgres | Kind::ChangesPostgres) {
        return crate::cli::usage(false, "--schema applies only to postgres", out, err);
    }
    let text = match args.kind {
        Kind::Exchange => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/exchange-v1.schema.json"
        ))
        .to_string(),
        Kind::Changes => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/changes-v1.schema.json"
        ))
        .to_string(),
        Kind::Governance => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/governance-v1.schema.json"
        ))
        .to_string(),
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
        Kind::Upkeep => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/upkeep-v1.schema.json"
        ))
        .to_string(),
        Kind::Knowledge => include_str!(concat!(
            env!("NEOSIAN_ASSETS"),
            "/contracts/knowledge-v1.schema.json"
        ))
        .to_string(),
        Kind::Postgres | Kind::ChangesPostgres => {
            let name = args.schema.as_deref().unwrap_or("neosian");
            if let Err(error) = crate::postgres::validate_schema(name) {
                return crate::cli::usage(false, &error.message, out, err);
            }
            (if matches!(args.kind, Kind::ChangesPostgres) {
                include_str!(concat!(env!("NEOSIAN_ASSETS"), "/sql/changes-postgres.sql"))
            } else {
                include_str!(concat!(env!("NEOSIAN_ASSETS"), "/sql/postgres.sql"))
            })
            .replace("{{schema}}", &format!("\"{name}\""))
            .replace("{{version}}", "4")
        }
    };
    write!(out, "{text}")?;
    Ok(0)
}

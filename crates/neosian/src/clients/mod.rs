//! Client registration and discovery (`shared/client_config.py`, §22.6).

pub mod context;
mod diagnostics;
pub mod files;
pub mod hooks;
pub mod install;
mod install_output;
pub mod mcp;
mod muse;
mod probe;
mod runner;
mod selection;
mod settings;
pub mod setup;
mod setup_check;
pub mod status;
pub mod targets;

//! The memory tool layer over the store (DESIGN §8, §14.2): mounts, the
//! index, the commands and the shell grammar that carries them. Policy
//! lives here and never in the store (C7).

pub mod cli;
pub mod commands;
pub mod dispatch;
mod index;
pub mod mounts;
mod operate;

//! `neosian version`: the one line every installer greps.

/// The crate's version, the wheel's by construction (a unit test pins the
/// two literals equal).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// The banner's first line on the Python shell, and the whole of it here:
/// `neosian v<X.Y.Z>`.
pub fn line() -> String {
    format!("neosian v{VERSION}")
}

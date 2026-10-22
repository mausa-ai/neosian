//! The release axis: X.Y.Z[rcN] and Cargo's X.Y.Z-rc.N spelling.

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Version {
    pub major: u64,
    minor: u64,
    patch: u64,
    pub final_release: bool,
    rc: u64,
}

impl Version {
    pub fn parse(text: &str) -> Option<Self> {
        let (base, rc) = text
            .split_once("-rc.")
            .or_else(|| text.split_once("rc"))
            .map_or((text, None), |(base, rc)| (base, Some(rc)));
        let number = |s: &str| {
            (!s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
                .then(|| s.parse().ok())
                .flatten()
        };
        let mut parts = base.split('.');
        let result = Self {
            major: number(parts.next()?)?,
            minor: number(parts.next()?)?,
            patch: number(parts.next()?)?,
            final_release: rc.is_none(),
            rc: match rc {
                Some(rc) => number(rc)?,
                None => 0,
            },
        };
        parts.next().is_none().then_some(result)
    }
}

impl std::fmt::Display for Version {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}.{}.{}", self.major, self.minor, self.patch)?;
        if !self.final_release {
            write!(f, "rc{}", self.rc)?;
        }
        Ok(())
    }
}

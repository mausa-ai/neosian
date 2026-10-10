//! Timestamps as the Python side writes them (DESIGN §39.3): tz-aware,
//! microsecond resolution, in `datetime.isoformat()`'s text. A stamp keeps
//! the offset it was read with, so it prints as Python would print it.

const DAY: i64 = 86_400_000_000;
const MINUTE: i64 = 60_000_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Stamp {
    /// Microseconds since the epoch, UTC.
    micros: i64,
    /// Minutes east of UTC.
    offset: i64,
}

/// Why a text is no stamp: not ISO 8601 as `fromisoformat` reads it, or
/// without an offset (naive is refused, never coerced: ECOSYSTEM §9).
#[derive(Debug, PartialEq, Eq)]
pub enum Unreadable {
    Malformed,
    Naive,
}

impl Stamp {
    /// `YYYY-MM-DD`, any one separator, `HH:MM[:SS[.f…]]`, then `Z`, `±HH`,
    /// `±HHMM` or `±HH:MM`: every form the stores write and a hand edit
    /// plausibly leaves; `fromisoformat`'s rarer spellings are refused.
    pub fn parse(text: &str) -> Result<Self, Unreadable> {
        let bytes = text.as_bytes();
        let number = |from: usize, len: usize| {
            let digits = bytes.get(from..from + len)?;
            let value = digits.iter().fold(0, |n, d| n * 10 + i64::from(d & 0x0f));
            digits.iter().all(u8::is_ascii_digit).then_some(value)
        };
        let is = |at: usize, ch: u8| bytes.get(at) == Some(&ch);
        let field = |from, len| number(from, len).ok_or(Unreadable::Malformed);
        let (year, month, day) = (field(0, 4)?, field(5, 2)?, field(8, 2)?);
        if !(is(4, b'-') && is(7, b'-')) || civil(days(year, month, day)) != (year, month, day) {
            return Err(Unreadable::Malformed);
        }
        if bytes.len() == 10 {
            return Err(Unreadable::Naive);
        }
        let (hour, minute) = (field(11, 2)?, field(14, 2)?);
        let (mut second, mut micros, mut at) = (0, 0, 16);
        if is(at, b':') {
            (second, at) = (field(17, 2)?, 19);
            if is(at, b'.') {
                let digits = bytes[20..]
                    .iter()
                    .take_while(|b| b.is_ascii_digit())
                    .count();
                let kept = digits.min(6);
                micros = field(20, kept)? * 10_i64.pow(6 - kept as u32);
                at = 20 + digits;
            }
        }
        if !is(13, b':') || hour >= 24 || minute >= 60 || second >= 60 {
            return Err(Unreadable::Malformed);
        }
        let offset = match text.get(at..) {
            Some("") => return Err(Unreadable::Naive),
            Some("Z") => 0,
            Some(zone) => {
                let sign = match zone.as_bytes()[0] {
                    b'+' => 1,
                    b'-' => -1,
                    _ => return Err(Unreadable::Malformed),
                };
                let minutes = match zone.len() {
                    3 => 0,
                    5 => field(at + 3, 2)?,
                    6 if is(at + 3, b':') => field(at + 4, 2)?,
                    _ => return Err(Unreadable::Malformed),
                };
                let hours = field(at + 1, 2)?;
                if hours >= 24 || minutes >= 60 {
                    return Err(Unreadable::Malformed);
                }
                sign * (hours * 60 + minutes)
            }
            None => return Err(Unreadable::Malformed),
        };
        let local = days(year, month, day) * DAY
            + (hour * 3600 + minute * 60 + second) * 1_000_000
            + micros;
        Ok(Self {
            micros: local - offset * MINUTE,
            offset,
        })
    }

    /// `datetime.isoformat()`: the fraction only when it is not zero, the
    /// offset always (`+00:00` for UTC).
    pub fn iso(self) -> String {
        let local = self.micros + self.offset * MINUTE;
        let (year, month, day) = civil(local.div_euclid(DAY));
        let rest = local.rem_euclid(DAY);
        let (seconds, micros) = (rest / 1_000_000, rest % 1_000_000);
        let (hour, minute, second) = (seconds / 3600, seconds / 60 % 60, seconds % 60);
        let fraction = if micros == 0 {
            String::new()
        } else {
            format!(".{micros:06}")
        };
        let sign = if self.offset < 0 { '-' } else { '+' };
        let (east, odd) = (self.offset.abs() / 60, self.offset.abs() % 60);
        format!(
            "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{fraction}{sign}{east:02}:{odd:02}"
        )
    }

    /// The instant, for ordering: two stamps compare as their moments do.
    pub fn instant(self) -> i64 {
        self.micros
    }
}

/// Days since the epoch for a proleptic Gregorian date.
fn days(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let (era, of_era) = (year.div_euclid(400), year.rem_euclid(400));
    let of_year = (153 * ((month + 9) % 12) + 2) / 5 + day - 1;
    era * 146_097 + of_era * 365 + of_era / 4 - of_era / 100 + of_year - 719_468
}

/// The date of a day count: the inverse of `days`.
fn civil(days: i64) -> (i64, i64, i64) {
    let shifted = days + 719_468;
    let (era, of_era) = (shifted.div_euclid(146_097), shifted.rem_euclid(146_097));
    let year = (of_era - of_era / 1460 + of_era / 36_524 - of_era / 146_096) / 365;
    let of_year = of_era - (365 * year + year / 4 - year / 100);
    let shifted_month = (5 * of_year + 2) / 153;
    let day = of_year - (153 * shifted_month + 2) / 5 + 1;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    };
    (year + era * 400 + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::{Stamp, Unreadable};

    #[test]
    fn a_stamp_prints_as_isoformat_prints_it() {
        for (read, printed) in [
            (
                "2026-10-10T18:20:18.273843Z",
                "2026-10-10T18:20:18.273843+00:00",
            ),
            ("2026-08-19T10:00:00Z", "2026-08-19T10:00:00+00:00"),
            ("2026-08-19 10:00:00+00:00", "2026-08-19T10:00:00+00:00"),
            ("2026-08-19T10:00+02:00", "2026-08-19T10:00:00+02:00"),
            (
                "2024-02-29T23:59:59.5-05:30",
                "2024-02-29T23:59:59.500000-05:30",
            ),
            (
                "1969-12-31T23:59:59.999999Z",
                "1969-12-31T23:59:59.999999+00:00",
            ),
            (
                "2026-01-01T00:00:00.1234567Z",
                "2026-01-01T00:00:00.123456+00:00",
            ),
            ("2026-08-19T10:00:00+0200", "2026-08-19T10:00:00+02:00"),
            ("2026-08-19T10:00:00-02", "2026-08-19T10:00:00-02:00"),
        ] {
            assert_eq!(Stamp::parse(read).map(Stamp::iso).as_deref(), Ok(printed));
        }
    }

    #[test]
    fn an_offset_moves_the_text_and_never_the_instant() {
        let utc = Stamp::parse("2026-08-19T10:00:00Z").unwrap();
        let east = Stamp::parse("2026-08-19T12:00:00+02:00").unwrap();
        assert_eq!(utc.instant(), east.instant());
        assert_eq!(utc.instant(), 1_787_133_600_000_000);
    }

    #[test]
    fn naive_and_malformed_are_told_apart() {
        for naive in ["2026-08-19T10:00:00", "2026-08-19", "2026-08-19 10:00"] {
            assert_eq!(Stamp::parse(naive), Err(Unreadable::Naive), "{naive}");
        }
        for malformed in [
            "",
            "yesterday",
            "2026-02-30T10:00:00Z",
            "2026-08-19T24:00:00Z",
            "2026-08-19T10:00:60Z",
            "2026-08-19T10:00:00 UTC",
            "2026-08-19T10:00:00+2",
        ] {
            assert_eq!(
                Stamp::parse(malformed),
                Err(Unreadable::Malformed),
                "{malformed}"
            );
        }
    }
}

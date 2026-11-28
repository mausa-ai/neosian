//! How login reads in text mode: named steps, indented lines with an
//! outcome mark, a host's notice set apart, and prompts. Colour and bold
//! only on a terminal that allows them; never on a pipe and never on text
//! a host sent. Nothing moves the cursor.
use super::terminal::{clean, wrap};
use crate::assets::{quoted, render};
use std::io::{self, IsTerminal, Write};

const WORDS: &str = include_str!(concat!(env!("NEOSIAN_ASSETS"), "/prompts/login.yaml"));
/// The width of output that is not a terminal, and of a terminal whose size is unknown.
const PLAIN_WIDTH: usize = 80;
/// The bounds of a terminal's width, so that lines stay readable.
const WIDTH: std::ops::RangeInclusive<usize> = 20..=100;
/// Host text in one line (a name, an address, an account) is cut here.
pub const NAME: usize = 320;

/// A sentence by key; a missing key is a build defect a unit test catches.
pub fn say(key: &str) -> String {
    quoted(WORDS, key).unwrap_or_else(|| panic!("login.yaml carries {key}"))
}

/// A sentence with its `{{name}}` placeholders filled.
pub fn fill(key: &str, variables: &[(&str, &str)]) -> String {
    render(&say(key), variables)
}

/// A display failure, as a login error.
pub fn lost(_: io::Error) -> String {
    "cannot display login progress".into()
}

/// One line of host text, cleaned and bounded.
pub fn host(text: &str) -> String {
    clean(text, NAME, false)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Style {
    pub color: bool,
    pub width: usize,
}

impl Style {
    pub const PLAIN: Self = Self {
        color: false,
        width: PLAIN_WIDTH,
    };

    /// What stderr allows: colour on a Unix terminal unless `NO_COLOR` is
    /// set or `TERM` is `dumb`, and the terminal's width.
    pub fn stderr() -> Self {
        let tty = io::stderr().is_terminal();
        let columns = if tty {
            console::Term::stderr()
                .size_checked()
                .map(|(_, c)| usize::from(c))
        } else {
            None
        };
        let set = |key: &str| std::env::var_os(key).filter(|v| !v.is_empty());
        let dumb = std::env::var_os("TERM").is_some_and(|t| t == "dumb");
        Self::detect(tty, columns, set("NO_COLOR").is_some() || dumb)
    }

    fn detect(tty: bool, columns: Option<usize>, refuse_color: bool) -> Self {
        Self {
            color: tty && cfg!(unix) && !refuse_color,
            // A terminal that reports no width (0 columns) is read as 80.
            width: match columns {
                Some(c) if tty && c > 0 => c.clamp(*WIDTH.start(), *WIDTH.end()),
                _ => PLAIN_WIDTH,
            },
        }
    }

    fn paint(self, code: &str, text: &str) -> String {
        if self.color {
            format!("\u{1b}[{code}m{text}\u{1b}[0m")
        } else {
            text.to_owned()
        }
    }
}

#[derive(Clone, Copy)]
pub enum Mark {
    Ok,
    Warn,
    Fail,
}

impl Mark {
    fn text(self) -> (&'static str, &'static str) {
        match self {
            Self::Ok => ("ok", "32"),
            Self::Warn => ("!", "33"),
            Self::Fail => ("x", "31"),
        }
    }
}

pub struct View<W: Write> {
    out: W,
    style: Style,
    step: Option<String>,
    written: bool,
}

impl<W: Write> View<W> {
    pub fn new(out: W, style: Style) -> Self {
        Self {
            out,
            style,
            step: None,
            written: false,
        }
    }

    /// A step's heading, after a blank line; the same step continues.
    pub fn step(&mut self, title: &str) -> io::Result<()> {
        if self.step.as_deref() == Some(title) {
            return Ok(());
        }
        if self.written {
            writeln!(self.out)?;
        }
        writeln!(self.out, "{}", self.style.paint("1", title))?;
        self.step = Some(title.to_owned());
        self.written = true;
        Ok(())
    }

    /// A line outside every step.
    pub fn line(&mut self, text: &str) -> io::Result<()> {
        self.lines("", 0, text)
    }

    /// A sentence in the current step.
    pub fn say(&mut self, text: &str) -> io::Result<()> {
        self.lines("  ", 2, text)
    }

    /// An outcome: `ok`, `!` or `x`, then the text.
    pub fn mark(&mut self, mark: Mark, text: &str) -> io::Result<()> {
        let (word, code) = mark.text();
        let lead = format!(
            "  {}{}",
            self.style.paint(code, word),
            " ".repeat(4 - word.len())
        );
        self.lines(&lead, 6, text)
    }

    /// A failure: in the current step, or alone when no step has started.
    pub fn fail(&mut self, text: &str) -> io::Result<()> {
        let text = clean(text, 4096, false);
        if self.step.is_some() {
            self.mark(Mark::Fail, &text)
        } else {
            self.line(&text)
        }
    }

    /// A label in a column of `pad` characters, then the text.
    pub fn pair(&mut self, label: &str, pad: usize, text: &str) -> io::Result<()> {
        let lead = format!("  {label:<pad$}  ");
        self.lines(&lead, pad + 4, text)
    }

    /// Numbered actions.
    pub fn list(&mut self, items: &[String]) -> io::Result<()> {
        for (n, item) in items.iter().enumerate() {
            let lead = format!("  {}. ", n + 1);
            let indent = lead.len();
            self.lines(&lead, indent, item)?;
        }
        Ok(())
    }

    /// A host's text, cleaned, wrapped and set apart by a bar; never styled.
    pub fn quote(&mut self, text: &str) -> io::Result<()> {
        let text = clean(text, 8192, true);
        for paragraph in text.lines() {
            for line in wrap(paragraph, self.room(4)) {
                writeln!(self.out, "  | {line}")?;
            }
        }
        self.written = true;
        Ok(())
    }

    /// A question on the current line; the answer is read by the caller.
    pub fn prompt(&mut self, question: &str) -> io::Result<()> {
        write!(self.out, "  {question} ")?;
        self.written = true;
        self.out.flush()
    }

    /// A writer that indents every line into the current step and cleans it.
    pub fn nested(&mut self) -> Nested<'_, W> {
        self.written = true;
        Nested {
            out: &mut self.out,
            line: Vec::new(),
            fresh: true,
        }
    }

    pub fn flush(&mut self) -> io::Result<()> {
        self.out.flush()
    }

    fn room(&self, indent: usize) -> usize {
        self.style.width.saturating_sub(indent).max(10)
    }

    fn lines(&mut self, lead: &str, indent: usize, text: &str) -> io::Result<()> {
        let pad = " ".repeat(indent);
        for (n, line) in wrap(text, self.room(indent)).iter().enumerate() {
            writeln!(self.out, "{}{line}", if n == 0 { lead } else { &pad })?;
        }
        self.written = true;
        Ok(())
    }
}

/// Lines written through it appear two columns in, cleaned of escape
/// sequences and control characters; a flushed partial line (a prompt)
/// is shown at once.
pub struct Nested<'a, W: Write> {
    out: &'a mut W,
    line: Vec<u8>,
    fresh: bool,
}

impl<W: Write> Nested<'_, W> {
    fn emit(&mut self, end: bool) -> io::Result<()> {
        let text = clean(&String::from_utf8_lossy(&self.line), usize::MAX, false);
        self.line.clear();
        if self.fresh && !(text.is_empty() && end) {
            write!(self.out, "  ")?;
        }
        write!(self.out, "{text}")?;
        self.fresh = end;
        if end {
            writeln!(self.out)?;
        }
        Ok(())
    }
}

impl<W: Write> Write for Nested<'_, W> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        for byte in buf {
            if *byte == b'\n' {
                self.emit(true)?;
            } else {
                self.line.push(*byte);
            }
        }
        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        if !self.line.is_empty() {
            self.emit(false)?;
        }
        self.out.flush()
    }
}

impl<W: Write> Drop for Nested<'_, W> {
    fn drop(&mut self) {
        let _ = self.flush();
    }
}

#[cfg(test)]
mod tests;

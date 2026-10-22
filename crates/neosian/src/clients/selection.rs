//! The terminal checklist: numbers toggle, Enter confirms, EOF cancels.

use std::io::{self, BufRead, Write};

pub fn select(
    labels: &[&str],
    input: &mut impl BufRead,
    out: &mut impl Write,
) -> io::Result<Option<Vec<usize>>> {
    let mut selected = vec![true; labels.len()];
    writeln!(out, "Connect clients")?;
    loop {
        for (index, label) in labels.iter().enumerate() {
            writeln!(
                out,
                "  {}. [{}] {label}",
                index + 1,
                if selected[index] { "x" } else { " " }
            )?;
        }
        write!(out, "Numbers toggle; Enter confirms; q cancels: ")?;
        out.flush()?;
        let mut line = String::new();
        if input.read_line(&mut line)? == 0 || line.trim().eq_ignore_ascii_case("q") {
            return Ok(None);
        }
        if line.trim().is_empty() {
            return Ok(Some(
                selected
                    .iter()
                    .enumerate()
                    .filter_map(|(i, yes)| yes.then_some(i))
                    .collect(),
            ));
        }
        let toggles: Result<Vec<_>, _> = line
            .replace(',', " ")
            .split_whitespace()
            .map(str::parse::<usize>)
            .collect();
        match toggles {
            Ok(mut toggles) if toggles.iter().all(|i| *i > 0 && *i <= labels.len()) => {
                toggles.sort_unstable();
                toggles.dedup();
                for i in toggles {
                    selected[i - 1] = !selected[i - 1];
                }
            }
            _ => writeln!(out, "Enter numbers from the list.")?,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn toggles_are_a_set_and_empty_selection_is_explicit() {
        let mut output = Vec::new();
        assert_eq!(
            select(&["a", "b"], &mut &b"1,1\n\n"[..], &mut output).unwrap(),
            Some(vec![1])
        );
        assert_eq!(
            select(&["a", "b"], &mut &b"1 2\n\n"[..], &mut output).unwrap(),
            Some(vec![])
        );
        assert_eq!(
            select(&["a"], &mut &b"9\nq\n"[..], &mut output).unwrap(),
            None
        );
        assert_eq!(select(&["a"], &mut &b""[..], &mut output).unwrap(), None);
    }
}

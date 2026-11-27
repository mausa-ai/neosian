use super::*;

fn shown(style: Style, draw: impl FnOnce(&mut View<&mut Vec<u8>>) -> io::Result<()>) -> String {
    let mut out = Vec::new();
    draw(&mut View::new(&mut out, style)).unwrap();
    String::from_utf8(out).unwrap()
}

#[test]
fn a_narrow_plain_view_wraps_each_step_and_keeps_a_long_url_whole() {
    let text = shown(
        Style {
            color: false,
            width: 30,
        },
        |v| {
            v.step("Approve this machine")?;
            v.say("Open this page and check that it shows the same code:")?;
            v.say("https://cloud.neosian.com/connect?user_code=ABCD-EFGH")?;
            v.pair("Code", 4, "ABCD-EFGH")?;
            v.mark(Mark::Ok, "Connected to neosian cloud as you@example.com")?;
            v.step("Recording")?;
            v.say("Notice from neosian cloud:")?;
            v.quote("Prompts and replies are saved.\n\u{1b}[31mred\u{1b}[0m line")?;
            v.step("Next")?;
            v.list(&["Restart your agents.".into()])
        },
    );
    assert_eq!(
        text,
        "Approve this machine\n\
         \x20 Open this page and check\n\
         \x20 that it shows the same code:\n\
         \x20 https://cloud.neosian.com/connect?user_code=ABCD-EFGH\n\
         \x20 Code  ABCD-EFGH\n\
         \x20 ok  Connected to neosian\n\
         \x20     cloud as you@example.com\n\
         \n\
         Recording\n\
         \x20 Notice from neosian cloud:\n\
         \x20 | Prompts and replies are\n\
         \x20 | saved.\n\
         \x20 | red line\n\
         \n\
         Next\n\
         \x20 1. Restart your agents.\n"
    );
}

#[test]
fn unicode_wraps_by_columns() {
    let text = shown(
        Style {
            color: false,
            width: 13,
        },
        |v| v.say("日本語 日本語 café"),
    );
    assert_eq!(text, "  日本語\n  日本語 café\n");
}

#[test]
fn colour_marks_neosian_words_and_never_host_text() {
    let text = shown(
        Style {
            color: true,
            width: 80,
        },
        |v| {
            v.step("Agents")?;
            v.mark(Mark::Ok, "Codex")?;
            v.mark(Mark::Fail, "Cursor: hooks refused")?;
            v.quote("\u{1b}[1mThe host\u{1b}[0m \u{1b}]8;;https://x.test\u{7}says\u{202e}")
        },
    );
    assert_eq!(
        text,
        "\u{1b}[1mAgents\u{1b}[0m\n  \u{1b}[32mok\u{1b}[0m  Codex\n  \u{1b}[31mx\u{1b}[0m   Cursor: hooks refused\n  | The host says\n"
    );
}

#[test]
fn a_failure_without_a_step_stands_alone_and_is_cleaned() {
    let alone = shown(Style::PLAIN, |v| v.fail("cannot \u{1b}[2Jcontact"));
    assert_eq!(alone, "cannot contact\n");
    let inside = shown(Style::PLAIN, |v| {
        v.step("Refresh")?;
        v.fail("login update already running")
    });
    assert_eq!(inside, "Refresh\n  x   login update already running\n");
}

#[test]
fn colour_needs_a_terminal_that_allows_it() {
    assert_eq!(
        Style::detect(false, Some(50), false),
        Style {
            color: false,
            width: 80
        }
    );
    assert_eq!(
        Style::detect(true, Some(50), true),
        Style {
            color: false,
            width: 50
        }
    );
    assert_eq!(
        Style::detect(true, Some(50), false),
        Style {
            color: cfg!(unix),
            width: 50
        }
    );
    assert_eq!(Style::detect(true, Some(5), false).width, 20);
    assert_eq!(Style::detect(true, Some(400), false).width, 100);
    assert_eq!(Style::detect(true, None, false).width, 80);
}

#[test]
fn nested_lines_are_indented_cleaned_and_a_flushed_prompt_shows_at_once() {
    let mut out = Vec::new();
    {
        let mut view = View::new(&mut out, Style::PLAIN);
        let mut nested = view.nested();
        write!(nested, "Connect clients\n  1. [x] Codex\n\nprompt: ").unwrap();
        nested.flush().unwrap();
        write!(nested, "typed\n\u{1b}[2Jdone\n").unwrap();
    }
    assert_eq!(
        String::from_utf8(out).unwrap(),
        "  Connect clients\n    1. [x] Codex\n\n  prompt: typed\n  done\n"
    );
}

#[test]
fn every_sentence_the_code_names_is_in_the_asset() {
    let source = [
        include_str!("../connect.rs"),
        include_str!("../device.rs"),
        include_str!("../project.rs"),
        include_str!("../steps.rs"),
    ]
    .concat();
    let pattern = fancy_regex::Regex::new(r#"(?:say|fill)\(\s*"([a-z_]+)""#).unwrap();
    let mut keys: Vec<String> = pattern
        .captures_iter(&source)
        .map(|found| found.unwrap().get(1).unwrap().as_str().to_owned())
        .collect();
    keys.extend(["capture_on", "capture_off", "refreshed", "connected"].map(String::from));
    keys.extend(crate::clients::setup_outcome::PARTS.map(|p| format!("part_{p}")));
    assert!(keys.len() > 40, "{keys:?}");
    for key in keys {
        assert!(quoted(WORDS, &key).is_some(), "login.yaml lacks {key}");
    }
}

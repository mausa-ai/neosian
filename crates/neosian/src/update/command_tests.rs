use super::*;
use clap::Parser;

#[derive(Parser)]
struct Command {
    #[command(flatten)]
    args: Update,
}

#[test]
fn preview_and_explicit_write_are_mutually_exclusive() {
    assert!(Command::try_parse_from(["update", "--check", "--write"]).is_err());
}

#[cfg(unix)]
#[test]
fn explicit_updates_install_by_default_regardless_of_passive_policy() {
    use crate::testing::Scratch;
    use std::{fs, os::unix::fs::PermissionsExt};

    let current = crate::version::VERSION;
    let (base, patch) = current.rsplit_once('.').unwrap();
    let newer = format!("{base}.{}", patch.parse::<u64>().unwrap() + 1);
    let latest = version::Version::parse(&newer).unwrap();
    for policy in MODES {
        for flag in [None, Some("--write"), Some("--check")] {
            let scratch = Scratch::new("update-command");
            let (context, mut receipt) = tests::installation(&scratch);
            receipt["version"] = json!(current);
            scratch.plant("neosian-receipt.json", &receipt.to_string());
            scratch.plant("config.toml", &format!("[update]\nmode = '{policy}'\n"));
            scratch.plant(
                "install dir/neosian",
                &format!("#!/bin/sh\necho 'neosian v{current}'\n"),
            );
            fs::set_permissions(&context.executable, fs::Permissions::from_mode(0o700)).unwrap();
            scratch.plant(
                "install dir/neosian-update",
                &format!(
                    "#!/bin/sh\n[ \"$1\" = --tag ] && [ \"$2\" = v{newer} ] || exit 8\n\
                     printf '#!/bin/sh\\necho neosian v{newer}\\n' > \"$(dirname \"$0\")/neosian\"\n\
                     echo installed\n"
                ),
            );
            let mut argv = vec!["update", "--json"];
            argv.extend(flag);
            let args = Command::try_parse_from(argv).unwrap().args;
            let mut out = vec![];
            let mut err = vec![];
            assert_eq!(
                complete(
                    &args,
                    &context,
                    Some(&latest),
                    &mode(scratch.path()),
                    &mut out,
                    &mut err
                )
                .unwrap(),
                0
            );
            let report: serde_json::Value = serde_json::from_slice(&out).unwrap();
            let preview = flag == Some("--check");
            assert_eq!(report["action"], if preview { "notify" } else { "apply" });
            assert_eq!(report["mode"], policy);
            assert_eq!(
                err,
                if preview {
                    b"".as_slice()
                } else {
                    b"installed\n"
                }
            );
            let installed = fs::read_to_string(&context.executable).unwrap();
            assert!(installed.contains(if preview { current } else { &newer }));
            assert_eq!(mode(scratch.path()), policy);
        }
    }
}

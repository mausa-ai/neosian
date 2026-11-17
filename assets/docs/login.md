---
title: "Connect a machine"
summary: Device sign-in, machine credentials and host-managed workspace profiles
---

# Connect a machine

Run `neosian login` to connect to neosian cloud. Compare the public code in
your terminal with the one on the approval page. The host identifies the
account, assigns workspaces and supplies its recording notice. Run
`neosian setup --write`, restart your agents and check `neosian status`.
The default discovery URL is
`https://cloud.neosian.com/api/v1/machine/host`. Use `--host URL` to select
a host implementing the same HTTPS contract.

Shared recording saves prompts and assistant text after login displays the
notice. Each profile and its local source binding must permit it. Use
`neosian login --refresh --capture off` to stop recording on this machine
while keeping memory and skills available. The off switch takes effect locally even when the host is offline, another
login is waiting for approval or assignments conflict. The command reports a
refresh failure separately. A successful refresh also turns off the installed
profile and binding. While offline, run the current binary on this home;
older binaries cannot observe the local switch until the files catch up.
The preference survives refreshes, reauthentication and forgetting the login. Use `--capture on` to permit the host's per-workspace
policy again. A host-locked off policy takes precedence. A missing notice,
or a changed notice during a background refresh, keeps recording off until a
foreground login or refresh displays a valid notice. The host’s notice applies
to shared capture on workspaces that permit it.

## Workspace selection

An explicit `--profile`, then `NEOSIAN_PROFILE`, then the closest bound
project root select the profile. The managed default serves other directories.
Whole-store operator commands continue to require their own storage selection.
Ordinary setup writes registrations that follow the checkout. An explicit
`setup --profile NAME` pins the registration. Hooks select on each invocation.
Restart an MCP client to pick up a changed workspace.

Use `login --workspace NAME --project DIR` to bind a checkout to an assigned
workspace. A repository with one matching origin digest is suggested in an
interactive terminal. The picker reserves `new` and `none`. Hosts should
choose other aliases for workspaces they offer in the picker.
`--workspace none` preserves existing checkout bindings. When the host
offers workspace creation, `--workspace new` sends the directory name and
optional normalized repository digest. The CLI fetches the assignment before
installing it. A retry after an interrupted creation can select the workspace
by its assigned name.

Use `login --refresh` to fetch changed assignments. A session start also
checks when the host's refresh interval elapses. A stdio MCP startup performs
the same check for clients with no session-start hook, including OpenCode. An unavailable host leaves
the cached assignment in place. The gateway still authorizes each operation.
Managed names cannot take over an existing local profile or binding.
Login tracks its checkout bindings. If a manually bound checkout still uses
a removed assignment, rebind it to an available profile and retry. After
an interrupted local update, `login --refresh` completes the journaled change.

## Credentials and disconnecting

On Unix, the device flow stores its credential under the neosian home in
`login/credential.toml` (0600) inside `login/` (0700). On Windows the file
contains a current-user DPAPI encrypted credential. Keep the home in a
directory controlled by the operating-system user. Windows login checks the
home and private login configuration for permissions that let an unrelated
principal write them. Store a container's home in its own private volume. Anyone with access to
the running user's credentials can act as that machine. Disconnect it on
the host's devices page if the machine is lost.

Containers and CI can supply an existing bearer in `NEOSIAN_LOGIN_TOKEN`
and run `neosian login --non-interactive --workspace none`. The token is
verified against the machine endpoint and remains in the environment.
Changing the variable requires another login to verify the new credential.
Use a separate `NEOSIAN_HOME` for another host or account. Profile export,
registration files, command arguments and diagnostic output exclude the
secret. `status --json` reports cached identity and local credential readiness.
A server can revoke that credential while the machine is offline. A running
bridge uses the current verified credential for each request. Same-account
file-based rotation takes effect there, and local logout clears access even
when the remote revocation service is unavailable. Restart the bridge after
changing its workspace assignment or process environment.

`neosian logout` requests revocation of the current credential and clears
local access. Managed selections still resolve and require login. Review
the host's devices page if revocation could not be confirmed. For an
environment credential, also unset the variable. `logout --forget` removes
the managed assignments and default. A replacement login retires the prior
file credential after the new assignment commits. Its protected retirement
receipt survives interruption and is processed on the next login or logout. Unbound directories then record
locally. An open capture remains in the private spool. See `neosian docs
profiles` for pending-span recovery.

## Host contract

Discovery returns JSON with `host_format: 1`, `name`, `scopes`,
`device_authorization_endpoint`, `token_endpoint`, `machine_endpoint` and
`revocation_endpoint`. `workspaces_endpoint` is optional. All destinations,
including verification links and the state gateway, must share the
discovery document's HTTPS origin. Redirects are refused. `SSL_CERT_FILE`
can supply the host's trusted CA.

Device start receives `{name, scopes}` and returns `device_code`, `user_code`,
`verification_uri`, `verification_uri_complete`, `expires_in` and `interval`.
Polling posts `{device_code}`. A success returns `{id, name, secret, expires_at}`,
with optional expiry. Pending, slow down, denied and expired responses can
carry RFC 8628 names in `code` or `error`. In JSON mode the public code and
final outcome appear on separate stdout lines.

The authenticated machine GET returns `machine_format: 1`, `account`
with `id` and `email`, `state_url`, `default_profile`, a map of local aliases
to portable format-1 `profiles`, and `notice`. It may include
`device: {id, name}`, `capture_locked_off`, `refresh_after_seconds` and
`repositories: [{key, profile}]`. Unchanged assignments may return HTTP 304
when the client sends its ETag. A new credential always gets a complete
authenticated document. The optional workspace POST accepts
`{name, repository_key}` and returns `{profile}`, the new alias. Revocation
posts an empty body with the calling bearer and accepts HTTP 200 or 204.

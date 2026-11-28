---
title: "Connect a machine"
summary: Device sign-in, machine credentials and host-managed workspace profiles
---

# Connect a machine

Run `neosian login --host URL` with the HTTPS discovery URL supplied by a
host that supports machine sign-in. On first login, omitting `--host` selects
`https://cloud.neosian.com/api/v1/machine/host`. Later logins in the same
neosian home reuse the chosen URL. The first-party neosian cloud sign-in
service is still in development for 2.18.0. For a storage-only HTTP service,
see the manual bindings in `neosian docs profiles`.

Compare the public code in your terminal with the one on the approval page.
The host identifies the account, assigns workspaces and supplies its recording
notice. Run `neosian setup --write`, restart your agents and check
`neosian status`.

## What login shows

In text mode login writes a few named steps to stderr: approving the machine
(or signing in with an environment credential, or refreshing), the workspace
question for this directory, the host's recording notice, the workspaces now
ready, your agents and the actions left to you. The notice appears once,
attributed to the host and set apart by a bar; neosian adds one line saying
whether recording is on and how to turn it off. In an interactive terminal a
first login offers to connect your agents. The answer runs setup's own
checklist and writes, then shows one line for each selected agent: `ok`, `!`
when the MCP registration holds but another part did not apply, or `x`. The
closing list names what applies: restart your agents, approve the new hook in
Codex's `/hooks`, the `neosian setup --client` command that shows what a
failed agent needs, or `neosian setup --write` when setup did not run.

Paths, commands and setup's per-client report stay behind `--verbose`, which
also shows the account, device and capture policy. Bold and colour appear
only on a Unix terminal, never when output is redirected, and `NO_COLOR` or
`TERM=dumb` turns them off. Lines wrap to the terminal's width (80 columns
when output is not a terminal); a word wider than that, such as a long
address, stays whole so it can be copied. Text a host sends (its name, its
notice, the approval address, the account) is shown without control
characters, escape sequences or invisible formatting, and is never styled.
With `--json`, stdout carries only the JSON lines and the exit codes are the
same; stderr carries the same steps without colour. Standalone
`neosian setup` keeps its full report.

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

To stop recording one conversation on this machine, use the full conversation
ID from the agent's session context and run `neosian record pause <conversation-id>`.
An open, unfinished turn is discarded, with the conversation's closed turns
still waiting to land. Run `neosian record resume <conversation-id>`
to resume at a fresh prompt when the machine and workspace both allow capture.
You can continue to use memory and skills while recording is paused.

## Bring local memory along

When the host offers an import and this neosian home holds memory, a login
in an interactive terminal shows what the home holds (memory scopes,
conversations and their size) under "Your memory" and asks once whether to
bring it to the host. The default is no. Without `--import`, no unattended
login imports or asks: a login with `--non-interactive`, with
`NEOSIAN_LOGIN_TOKEN`, with `--json`, or from a script whose input is not a
terminal, and every refresh. With `--import`, the person who typed it has
said yes, wherever it is typed, piped input included: it imports without
asking, or resumes a waiting import. A host without an import is never
mentioned, and `--import` against it is refused before the device start.

Imported history includes recorded tool arguments and tool output exactly
as stored, so it may contain secrets; nothing is removed. Approving this
machine and importing are separate choices: the approval page can approve
the machine without the import, and the login then says so and imports
nothing.

To show those figures, login first takes a private snapshot of the home's
store data under `login-import/` in the home (owner-only; the home itself
is never changed): exact figures need a copy recovered and read apart from
the home, since reading the live store can take its lock and run its
recovery. The snapshot is taken before the question, so a large home costs
time and about as much disk space again as its store data before you
answer; a no removes it at once, and a snapshot that no journal names (a
login stopped at the question) is removed by the next login before
anything else, which says so with `--verbose`. The copy takes what the
store reads: `conversations`, `.records` and `.transactions`, and each
directory whose path is a valid scope with its `documents/`, `versions/`,
`redactions.jsonl` and the scopes below it; any other file or directory of
the home is not copied, and a symbolic link among what it takes, or at the
snapshot's own directories, is refused. On a yes the device start carries the
offered totals, and after the machine is installed the "Import" step sends
each unit and commits. A unit the host refuses is left out and the other
units go on. The result names every unit left out and why: a conversation
without turns has no route to the host (`no_turns`), one with an item
larger than a restore part cannot be sent (`unfit`), and the host may
refuse a unit as too large or at all.

When the host reports success, login refreshes the account's assignments
(the whole machine document, never a 304), binds this directory to its
imported workspace and says what it did, all before it offers to connect
your agents. See "Imported projects and their directories" below.

The import resumes. Before Begin, login writes `login-import/journal.json`
(0600): the host, the account, the digest of the machine credential, the
snapshot, the units and, as they happen, the host's session, its omissions
and the stage reached. It holds no secret. When the import stops (a lost
answer, a conflict, a full account, an interrupted run), `neosian login
--import` resumes the same session with the same credential: units the host
holds are not sent again and a conversation continues from the position
the host reports. A commit whose answer was lost is settled by reading the
session's status, never by sending units again. While an import is waiting,
every other login except a refresh is refused, because a new approval would
replace the credential the session belongs to; a refresh says the import is
waiting and leaves it as it is. A resume to another host, or
with another account or credential, sends nothing. `neosian login
--discard-import` discards the host's open session when this machine still
holds its credential and removes the snapshot and journal. They are also
removed when the import ends: a failure the host cannot narrow to one
unit, a refused approval, a session the host ended, or success once the
directories are bound. When the host left every unit out, they stay, and
the import is reported as stopped, until the host confirms the session's
discard (or that it has ended). A status answer that names another session,
or a success whose counts differ from the commit, stops the import with its
state kept. One import runs at a time: the login lock is held from the
login's start until the journal is written and through the transfer, and a
resume reads the journal again under it. After the host's success the snapshot goes at once
and the journal stays, at the stage `succeeded` with the host's targets,
until the assignments and the binding are installed; a run that stops in
between is finished by the next login (a refresh on a connected machine,
whatever was asked), which sends nothing to the import endpoint. When the
host continues without this machine (for example waiting for the next
day's allowance), they stay until `neosian login --import` reads the
result.

With `--json` the final line gains an `import` object: `status`
(`succeeded`, `queued`, `running`, `waiting`, `failed`, `not_authorized`,
`nothing`, `ended` or `stopped`), `session`, `units` (kind, name, sha256,
bytes), `omitted` (kind, name, reason), `targets`, `error_code`,
`waiting_reason`, `resume_at`, `stopped` and `kept`; after a success also
`finished`, `directory` (`root`, `scope`, `profile` and `bound`: `import`,
`kept` or `none`) and `unbound` (`source`, `profile`, `reason`), and the
line's `managed` lists the refreshed assignments. A login that finishes an
earlier success prints the refresh's line with that `import` object. A
resumed import prints `{"event":"import","import":{...}}`; a discard prints
`{"event":"import_discarded","removed":...,"host":...}`. The exit code is 1
when the import stopped or failed, or when the host succeeded and the
refresh after it did not, else that of the login.

`neosian logout` says when an import is waiting: it revokes the credential
the import's session belongs to, so this machine can neither resume it nor
read its result after signing in again, and `neosian login --discard-import`
removes the local copy first. An import the host finished and whose
directories are not bound yet is bound by the next login.

## Imported projects and their directories

Locally, a project's memory lived in the scope `user:<login>/proj:<name>`,
where `<name>` is the directory's own name. The host's result maps each
imported scope to your own memory (`/me`) or to an assigned workspace and
carries no local path. Login checks each workspace target against the
scopes the commit sent and the assignments it just refreshed: a scope the
import did not send, a workspace the host did not name, one this account is
not assigned and a scope named twice bind nothing, and each is named with
its reason. The rest is kept, with the account, in the login state.

The directory login runs for (`--project`, or the working directory) is
bound to the workspace its own scope was imported to, exactly: the binding
does not cover the directories below it. A directory with a binding of its
own (`neosian profile bind ... --project`, or a checkout bound with
`--workspace`) keeps it. Neosian cannot know your other project directories
ahead of time, so each resolves by the same rule the first time an agent or
command runs there, and nothing is written when it does. Two directories
with the same name shared one scope on this machine, so both use its
workspace; to keep one apart, run `neosian profile bind WORKSPACE --project
DIR --write`. A later refresh drops every imported workspace the account is
no longer assigned, and those directories use the default workspace again.

## Workspace selection

An explicit `--profile`, then `NEOSIAN_PROFILE`, then the closest bound
project root select the profile, then a directory an import resolved (see
above). The managed default serves other directories.
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

Use `login --refresh` to fetch changed assignments; it keeps the imported
projects whose workspaces stay assigned. A session start also
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
and run `neosian login --host URL --non-interactive --workspace none` with
the host's discovery URL. The token is verified against the machine endpoint
and remains in the environment.
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
locally. An open capture, and any closed turn that has not landed (one
waiting to land, or one set aside under `held/`), remains in the private
spool; logout says which, and its `--json` answer has `pending_capture: true`
for either. See `neosian docs profiles` for
pending-span recovery.

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
authenticated document. The machine GET, and no other request, carries
`Neosian-Capabilities: turn_records=1`, a Structured Fields dictionary of the
capabilities this client uses. A host that varies the document by it keys its
ETag by the value and answers `Vary: Neosian-Capabilities`. The client keeps
the value with the ETag of the installed document, and its first fetch after
the value changes sends no ETag. The optional workspace POST accepts
`{name, repository_key}` and returns `{profile}`, the new alias. Revocation
posts an empty body with the calling bearer and accepts HTTP 200 or 204.

An optional `import_endpoint` (HTTPS, same origin, no query) offers the
import. The device start then carries `import: {scopes, conversations,
bytes}` when the person accepted. `POST {import_endpoint}` with `{}` begins
(201) or returns this machine's open session (200) with `id`,
`transfer_url`, `expires_at`, `limits` and `received` (required on a 200; a
201 without it holds nothing yet). Units go to
`{transfer_url}/v1/store/restore_scope` (the exact body the core sends) and
`{transfer_url}/v1/store/restore_records` (a probe, then parts of at most
983,040 bytes). `413 unit_too_large` and `422 unit_refused` leave a unit out;
`409 unit_conflict`, a conflicting part, `413 storage_exceeded` and
`unit_count_exceeded` stop; `410` begins again once; `429` waits for
`Retry-After` up to 60 seconds, six times. `POST {import_endpoint}/{id}/commit`
lists `units`, the host's `omitted` and `personal` (the current login's
`user:` scope when it is imported). `GET {import_endpoint}/{id}` is read for
up to 60 seconds of waiting. After `succeeded`, its `targets` (`source`,
`mount`, `profile`) are checked against the commit and the machine document
fetched next, in full; nothing about local directories is sent. Every import
request carries the machine bearer only to the discovery document's origin,
and the session id must be letters, digits, `-` or `_`.

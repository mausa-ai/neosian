---
title: "Bring and structure your knowledge"
summary: Preview Markdown imports, choose collection rules and references, and export readable knowledge bundles.
---

# Bring and structure your knowledge

`neosian knowledge` imports existing Markdown into an explicit mounted
collection, with source provenance and normal document versions. Its commands
use the same `--scope`, `--mount`, `--profile`, `--root`, `--url`, `--schema`
and `--actor` selection as memory commands. Results are JSON, including when
`--json` is omitted. Imports and exports use existing storage operations.
`knowledge govern/read` adds reviewed procedures/decisions and explicit retrieval
policies; see `neosian docs governance`. `validate` checks governance record
structure as well as collection rules. No model account is required.

```sh
neosian knowledge import ./notes --to /memories/notes --scope user:demo --json
neosian knowledge import ./notes --to /memories/notes --scope user:demo --write
neosian memory search 'deployment decision' --scope user:demo --json
neosian knowledge export /memories/notes --scope user:demo --output ./knowledge-copy
neosian knowledge import ./knowledge-copy --format bundle --to /memories/imported --scope user:demo --write
```

Imports and collection changes preview by default. `--write` applies the
reported work. An import needs a collection prefix such as `/memories/notes`,
not a mount root. A collection is a directory boundary you choose, not a
fixed personal/project/company taxonomy. Plain documents need no definition.

Pause other writers during apply and export. Keep input and output directories
stable too; path checks are not a sandbox against hostile filesystem writers. All inputs, permissions and
conflicts are checked before the first write, then destination snapshots are
rechecked and replacements use expected versions. Wire 6 does not provide
atomic compare-and-create or a batch transaction; these operations are not
safe against concurrent uncooperative writers or erasure. An apply failure
reports already landed paths and versions in `applied`; it does not roll them
back. Read previews can initialize a file root and acquire its lock, but never
write knowledge documents. Invalid input is rejected before opening storage.

## Markdown and conflicts

Markdown input is one UTF-8 `.md`/`.markdown` file or a directory tree.
NUL bytes are refused so content remains portable to Postgres. Relative
names, minus that extension, become document paths under the destination.
Original body bytes, including line endings and foreign YAML frontmatter,
are preserved. Other files are reported in `ignored`; attachments are not
copied or followed. Filenames must fit the documented memory path grammar.
For names containing spaces, a bundle can map a filename to a valid logical
path without renaming the source file.

The importer records a source label, relative filename and SHA-256. `--source`
sets the label; Markdown defaults to the input filename/directory name.
It never derives a scope, permission or executable instruction from source
content. Source provenance is evidence supplied with the data, not proof of
authenticated authorship; the normal destination actor records the import.

Byte-identical generated content and metadata is a `duplicate` and consumes
no version. Other occupied paths are `conflict` by default. `--conflict skip`
keeps the existing document; `--conflict replace` creates a normal new version.
Different paths with the same text remain separate documents. Duplicate input
paths and case-colliding components are refused consistently across platforms.
Read-only mounts refuse writes; edit-only mounts permit replacements but not
new documents or definitions.

Generic imports cannot target `sessions/`, `handoff`, `messages/`,
`message-links/`, or a `.collection` component, including case aliases. Ledger files are outside the
document address space. Redacted destinations and absent paths with retained
history are refused even in replace mode. An import is not a way to undo an
erasure. The separate archive commands retain their operator restore contract.

## Optional collection rules

Create a JSON definition using your own names and field vocabulary:

```json
{
  "format": 1,
  "name": "Decisions",
  "schema": {
    "allow_unknown": true,
    "fields": {
      "kind": {"type": "string", "required": true, "values": ["decision", "fact"]},
      "owner": {"type": "string"}
    }
  }
}
```

```sh
neosian knowledge collection /memories/decisions --scope user:demo --file ./rules.json
neosian knowledge collection /memories/decisions --scope user:demo --file ./rules.json --write
neosian knowledge collection /memories/decisions --scope user:demo
neosian knowledge validate /memories/decisions --scope user:demo
```

The definition is a normal versioned document at `decisions/.collection`.
Omitting `schema` or setting it to null disables field rules. With a schema,
field types are `string`, `integer`, `number`, `boolean`, `array`, `object`
and `null`. Integer values use JSON integer notation; `2.0` is a number.
`required` defaults false; `values`, when present, contains 1..64 allowed
values of that type. `allow_unknown` defaults true. This is a small field-rule
format, not a full JSON Schema evaluator. Arbitrary nested attribute values
remain data; no universal ontology or graph database is required.

Imports check incoming attributes. A definition change also checks retained
documents before it can apply. Failures identify the document, field and
reason. Ordinary memory edits and raw storage writes can still change these
documents; run `knowledge validate` to check them. Collection rules are
advisory validation, never an authorization mechanism. Nested collections
must be imported, checked and exported separately, using their own prefixes.

## Structured readable documents

Format-1 metadata is JSON in a marked HTML comment at the start of Markdown:

```markdown
<!-- neosian-knowledge:1
{"format":1,"collection":"decisions","attributes":{"kind":"decision","owner":"Ada"},"references":[{"relation":"supports","path":"evidence"}],"sources":[]}
-->
# Keep the reason
The ordinary Markdown body follows unchanged.
```

Header delimiters accept LF and CRLF; generated headers use LF. An input already carrying this marker is parsed
strictly; unknown versions, unknown fields, duplicate JSON keys and malformed
metadata refuse. Foreign Markdown without the marker stays uninterpreted.
Collection definitions use a `neosian-collection:1` comment containing their
JSON definition. Use the collection command to write those definitions.

Attributes, references and provenance travel with the body. Normal versions,
same-scope rename, archival export and body erasure therefore include them.
They are not envelope `extra` keys: S2's default content search can match them;
`--field metadata` still selects the storage envelope's `extra` object.
Read them through the memory tool or CLI and edit them as document content.
Imports rebind `collection` to the explicit destination prefix.

## References and lifecycle

A reference contains `relation` and a logical `path`, optionally `scope`.
Without a scope its path is relative to the document's recorded collection;
with a scope it names that scope's full document path. Relationship labels are
user-defined. References are not fetched during import or recursively exported.

```sh
neosian knowledge references /memories/decisions/reason --scope user:demo
```

Resolution uses currently mounted read authority and reports `available`,
`missing` or `unavailable`. Only available targets return a version and mounted
reference. Unmounted, denied, redacted or failing sources are unavailable;
no lookup is made in an unmounted scope. Target bodies are not returned.
An imported link cannot add a mount or grant permission.

Links identify paths, not immutable document IDs. Rename/delete leaves the
old link unresolved; no incoming document is rewritten implicitly. Reusing a
path makes the link refer to its current occupant. Moving a document with
ordinary memory commands preserves its recorded collection anchor; adjust it
deliberately when reorganizing. Validation reports a mismatched anchor.
Bundle imports into another collection rebind relative links together.

## Knowledge bundles and full archives

`knowledge export` creates a new private directory containing `knowledge.json`
and the current readable Markdown under `documents/`. It refuses an existing
destination. The manifest is written last; an interrupted export without it
is incomplete. Source documents are unchanged. Redacted/deleted documents and
history are excluded. A definition, if present, is included separately.

The manifest has `format: 1`, optional `collection` and a `documents` array.
Each record contains a logical `path` relative to the collection, contained
relative `file`, SHA-256 `sha256`, and `origin`: source `scope`, `path`,
`version`, UTC `created_at`/`updated_at`, optional opaque `actor`, and JSON
`extra`. `neosian schema knowledge` prints the structural contract.

Bundle import checks file hashes before parsing. It retains existing document
metadata and appends the source envelope as provenance, preserving opaque JSON
metadata there. The destination gets new versions, timestamps and actor;
source `extra` remains source evidence inside the content, rather than becoming
active destination envelope keys. Replacing a document preserves its existing
destination envelope extras. Bundle imports default the label to
`neosian-bundle`; repeated imports of the same bundle are duplicates.

Use top-level `neosian export` / `neosian import` for faithful storage archives
with original envelopes, full histories and redaction skeletons. Knowledge
bundles are current-document ingestion sources, not replicas or incremental
sync. Previously exported/copied knowledge requires separate erasure; importing
an old bundle into a fresh destination cannot know about later source erasure.

Knowledge operations default to 10,000 documents and 256 MiB of normalized
content. `NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS` (up to 100,000) and
`NEOSIAN_KNOWLEDGE_MAX_BYTES` (up to 2 GiB) select explicit local budgets. Byte
settings are positive decimal integers. For example, to import up to 20,000
notes within a 512 MiB content budget:

```sh
NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS=20000 NEOSIAN_KNOWLEDGE_MAX_BYTES=536870912 NEOSIAN_MAX_TEMP_BYTES=2147483648 neosian knowledge import ./notes --to /memories/notes --scope user:demo
```

Review the preview, pause writers, then repeat with `--write`. Import bodies are
staged privately on disk; plans retain paths and hashes of full destination
snapshots. Collection validation and export consume document pages and read one
body at a time. Export stages a complete bundle before publishing its output
name. Budget failures never silently truncate a batch; a write failure still
reports exact partial receipts. Local staging and recovery-plan preparation each
have a 512 MiB default budget through `NEOSIAN_MAX_TEMP_BYTES`; exchange accounts
for its staging and redo plan together. Knowledge stages use the OS temp
location (exports stage beside the output); receiver stages stay under its root. Ensure that location has room;
normal completion and errors clean stages, while a killed process may leave a
private `.neosian-stage-*` directory. Remove an orphan only after its process has
stopped. Retain committed `.transactions/pending.json` for automatic recovery.

Fixed validation bounds remain 1 MiB per stored knowledge document including its
header, 64 KiB per metadata/definition header, 128 references and 128 source
records per document, and 64 defined fields. Field names and relation labels are
1..128 characters; collection names and source labels are 1..256. Source filenames
are at most 1024 UTF-8 bytes. A manifest is bounded by the configured content
budget or 64 MiB, whichever is smaller. Directory walking stops at ten times the
document budget or 16 levels. Inputs refuse symlinks, traversal and unsupported
formats. Larger bundles keep format 1; older binaries may refuse their size.

Exports preflight provenance growth against ingestion limits; use a full archive
when that evidence exceeds them. A longer destination prefix or custom source
label can require additional header space on import. These budgets do not enlarge
skill loading or context injection, change backend permissions, or make
full-history archives incremental. See `neosian docs stores` for file, HTTP and
replica budgets and their practical limits.

Skills can use these same readable headers and provenance. `neosian skill`
adds skill-specific validation, directory packages, dependency/resource checks
and profile revision guards over ordinary stored documents. Its optional
manifest lives in `attributes.skill`; see `neosian docs skills`.

`upkeep` provides reviewed corrections to existing knowledge bodies, attributes
and references. Preparation retains source provenance, records current source
snapshots and checks the resulting collection attributes. Apply rechecks those
observations, including collection definitions, and uses normal versioned
writes. It does not rewrite definitions, resolve semantic disagreements or
repair inbound links automatically. See `neosian docs memory` for approval,
revision recovery and paused-writer requirements.

Imports, reviews, renames and erasures emit ordinary document changes on capable
backends. A host can follow their durable references without copying knowledge
bodies into a notification log. Scope restoration instead announces reconciliation.
The feed supplies references; `exchange` adds one-way current-memory replicas
with atomic receiver checkpoints. See `neosian docs changes` for polling and
`neosian docs exchange` for snapshots, deltas, conflicts and erasure.

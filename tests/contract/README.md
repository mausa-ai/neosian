# Independent core contracts

Run `cargo build --locked`, then `make test-contract`. Set `NEOSIAN_BINARY`
to test another executable. A missing binary fails; this tier never silently
skips it. CI runs the release binary on Linux.

The runner uses pytest, JSON Schema validation, a subprocess and raw HTTP/JSON-RPC.
It does not import `neosian`, its codecs, fakes, stores or conformance classes.
`--confcutdir=tests/contract` excludes the SDK's root fixtures; an assertion also
checks that no product module was imported. Python is a test runner here.
Every process gets a temporary home and isolated client configuration.

`fixtures/v1/records.json` and `fixtures/v1/home/` are independently authored
format-1 records and their physical FileStore representation. They are literal
inputs and expectations, never snapshots regenerated from the implementation
under test. The fixtures cover Unicode, nested metadata, full message content
(media, tool calls, reasoning and opaque provider fields), historical versions,
erasures, projection spans and a mailbox record. The physical memory erasure
trail uses `ts`; the wire uses `created_at`. Neither difference is normalised
away in the fixture.

The tests cover all 21 storage POST operations, the handshake, transport and
allowance refusals, paging and occupied restores, the six memory commands,
all ten mailbox operations, and a stdio MCP session. Successful storage requests
and responses validate against `neosian/assets/contracts/wire-v6.openapi.json`;
stored rows validate against the adjacent `state-v1.schema.json` definitions.
Generated timestamps, IDs and claim tokens are checked by shape and lifecycle;
the expected content, versions, actors and failure codes stay literal.

This is the first independent contract slice. It does not replace the existing
Python/Rust differential corpus or certify a third-party service. Backend races,
Postgres, TLS, HTTP MCP sessions, recording hooks, client adapters and the full
operator corpus still rely on their existing suites. Their expectations must
be preserved independently before removing the Python implementation they use.

Change an expectation only with an explained contract decision. Preserve old
readable fixtures when a new format is introduced; add a new version directory
and explicit conversion/refusal cases. Do not bless a regression by recording
the current program's output as the new expected answer.

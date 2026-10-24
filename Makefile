# neosian development targets — `make help` (DESIGN §11)

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-postgres test-container size rust-lint rust-test test-contract test-storage-contract bench-hooks release phase-tag

help: ## List targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2} /^##@/ {printf "\n%s\n", substr($$0, 5)}' $(MAKEFILE_LIST)

##@ Setup

install: ## Sync all dependency groups from the lockfile
	uv sync --locked --all-groups

##@ Gates

lint: ## ruff + black --check over development tooling
	uv run ruff check .
	uv run black --check .

format: ## Autofix: black + ruff --fix
	uv run black .
	uv run ruff check --fix .

typecheck: ## mypy --strict over tests and development tooling
	uv run mypy --strict tests scripts

test: ## Independent binary contracts and tooling tests; no model keys
	cargo build --locked
	uv run pytest -q -m 'not postgres'

test-postgres: ## Independent Postgres conformance and corruption tests; explicit test DSN
	cargo build --locked
	uv run pytest --confcutdir=tests/contract tests/contract/storage tests/contract/postgres -q --storage-backend=postgres

test-container: ## Build the state-process image; HTTP conformance against it (needs docker)
	./scripts/container_test.sh

size: ## File-size gate (warn 300 / fail 500) over crates/**/*.rs
	uv run python scripts/check_file_size.py

rust-lint: ## cargo fmt --check + clippy -D warnings + cargo deny (licenses, advisories) + the generated release workflow current
	cargo fmt --all --check
	cargo clippy --workspace --all-targets --locked -- -D warnings
	cargo deny check
	dist generate --check

rust-test: ## cargo test over the workspace, the lockfile asserted
	cargo test --workspace --locked

test-contract: ## Independent public formats and CLI/MCP/HTTP behavior
	cargo build --locked
	uv run pytest --confcutdir=tests/contract tests/contract -m 'contract and not postgres' -q

test-storage-contract: ## Independent storage HTTP suite; backend=file|postgres|http, profile=full|base (defaults: file, full)
	uv run pytest --confcutdir=tests/contract tests/contract/storage -m contract -q -rs --storage-backend=$(or $(backend),file) --storage-profile=$(or $(profile),full)

bench-hooks: ## The latency gate: p50 of 20 warm runs per binary event; home=DIR copies yours (default: a seeded reference home), binary=PATH
	uv run python scripts/bench_hooks.py $(if $(home),--home "$(home)",--seed) $(if $(binary),--binary "$(binary)")

##@ Release

release: ## Cut annotated release tag: v=X.Y.Z [notes=FILE] (must match Cargo + CHANGELOG; clean tree)
ifndef v
	$(error v=X.Y.Z is required)
endif
	@git diff --quiet && git diff --cached --quiet || { echo "refusing: dirty tree"; exit 1; }
	@grep -q '^version = "$(subst rc,-rc.,$(v))"$$' Cargo.toml || { echo "refusing: v$(v) is not Cargo.toml [workspace.package].version"; exit 1; }
	@grep -q '^## \[$(subst .,\.,$(v))\]' docs/CHANGELOG.md || { echo "refusing: docs/CHANGELOG.md has no '## [$(v)]' section"; exit 1; }
ifdef notes
	@test -s "$(notes)" || { echo "refusing: notes=$(notes) is missing or empty"; exit 1; }
	git tag -a "v$(v)" --cleanup=whitespace -F "$(notes)"
else
	git tag -a "v$(v)" -m "release v$(v)"
endif

docs-artifacts: ## Versioned docs JSON and independent contracts archive
	uv run python scripts/package_docs.py target/docs

phase-tag: ## Cut annotated phase tag: id=<phase id> (clean tree)
ifndef id
	$(error id=<phase id> is required)
endif
	@git diff --quiet && git diff --cached --quiet || { echo "refusing: dirty tree"; exit 1; }
	git tag -a "$(id)-done" -m "phase $(id) complete"

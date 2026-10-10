# neosian development targets — `make help` (DESIGN §11)

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-external test-postgres test-container size rust-lint rust-test test-differential bench-hooks release phase-tag readme-media

help: ## List targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2} /^##@/ {printf "\n%s\n", substr($$0, 5)}' $(MAKEFILE_LIST)

##@ Setup

install: ## Sync all dependency groups from the lockfile
	uv sync --locked --all-groups

##@ Gates

lint: ## ruff + black --check + import-linter contracts
	uv run ruff check .
	uv run black --check .
	uv run lint-imports

format: ## Autofix: black + ruff --fix
	uv run black .
	uv run ruff check --fix .

typecheck: ## mypy --strict over library, tests and examples
	uv run mypy --strict neosian tests examples

test: ## Unit tier — the default gate, zero API keys, coverage floor
	uv run pytest --cov --cov-report=term-missing:skip-covered --cov-fail-under=89

test-external: ## Real-API suite: provider=<openai|anthropic|cerebras|xai|gemini|kimi|qwen|local> [file=creds]
ifndef provider
	$(error provider=<openai|anthropic|cerebras|xai|gemini|kimi|qwen|local> is required)
endif
	uv run python scripts/external_env.py --provider $(provider) $(if $(file),--file $(file))

test-postgres: ## Postgres suite: needs NEOSIAN_TEST_POSTGRES_DSN (self-skips when unset)
	uv run pytest -m external_postgres -v

test-container: ## Build the state-process image; both kits against it (needs docker)
	./scripts/container_test.sh

size: ## File-size gate (warn 300 / fail 500) over neosian/**/*.py and crates/**/*.rs
	uv run python scripts/check_file_size.py

rust-lint: ## cargo fmt --check + clippy -D warnings + cargo deny (licenses, advisories) + the generated release workflow current
	cargo fmt --all --check
	cargo clippy --workspace --all-targets --locked -- -D warnings
	cargo deny check
	dist generate --check

rust-test: ## cargo test over the workspace, the lockfile asserted
	cargo test --workspace --locked

test-differential: ## The corpus through the shell and the binary over one seeded home (NEOSIAN_BINARY, else target/release, else target/debug)
	uv run pytest tests/differential -m differential -q

bench-hooks: ## The latency gate: p50 of 20 warm runs per door and event; home=DIR copies yours (default: a seeded reference home), binary=PATH
	uv run python scripts/bench_hooks.py $(if $(home),--home "$(home)",--seed) $(if $(binary),--binary "$(binary)")

##@ Release

release: ## Cut annotated release tag: v=X.Y.Z [notes=FILE] (must match pyproject + CHANGELOG; clean tree)
ifndef v
	$(error v=X.Y.Z is required)
endif
	@git diff --quiet && git diff --cached --quiet || { echo "refusing: dirty tree"; exit 1; }
	@grep -q '^version = "$(v)"$$' pyproject.toml || { echo "refusing: v$(v) is not pyproject [project].version"; exit 1; }
	@grep -q '^version = "$(subst rc,-rc.,$(v))"$$' Cargo.toml || { echo "refusing: v$(v) is not Cargo.toml [workspace.package].version"; exit 1; }
	@grep -q '^## \[$(subst .,\.,$(v))\]' docs/CHANGELOG.md || { echo "refusing: docs/CHANGELOG.md has no '## [$(v)]' section"; exit 1; }
	uv run python scripts/check_model_release.py
ifdef notes
	@test -s "$(notes)" || { echo "refusing: notes=$(notes) is missing or empty"; exit 1; }
	git tag -a "v$(v)" --cleanup=whitespace -F "$(notes)"
else
	git tag -a "v$(v)" -m "release v$(v)"
endif

readme-media: ## Re-render the four README recordings from their tapes (vhs; ANTHROPIC_API_KEY + OPENAI_API_KEY in the environment; isolated temporary homes)
	scripts/readme_media.sh

phase-tag: ## Cut annotated phase tag: id=<phase id> (clean tree)
ifndef id
	$(error id=<phase id> is required)
endif
	@git diff --quiet && git diff --cached --quiet || { echo "refusing: dirty tree"; exit 1; }
	git tag -a "$(id)-done" -m "phase $(id) complete"

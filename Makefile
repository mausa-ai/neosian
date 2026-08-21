# neosian development targets — `make help` (DESIGN §11)

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-external test-postgres size release phase-tag

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

typecheck: ## mypy --strict over library and tests
	uv run mypy --strict neosian tests

test: ## Unit tier — the default gate, zero API keys
	uv run pytest

test-external: ## Real-API suite: provider=<openai|anthropic|cerebras> [file=creds]
ifndef provider
	$(error provider=<openai|anthropic|cerebras> is required)
endif
	uv run python scripts/external_env.py --provider $(provider) $(if $(file),--file $(file))

test-postgres: ## Postgres suite: needs NEOSIAN_TEST_POSTGRES_DSN (self-skips when unset)
	uv run pytest -m external_postgres -v

size: ## File-size gate (warn 300 / fail 500)
	uv run python scripts/check_file_size.py

##@ Release

release: ## Cut annotated release tag: v=X.Y.Z (must match pyproject; clean tree)
ifndef v
	$(error v=X.Y.Z is required)
endif
	@git diff --quiet && git diff --cached --quiet || { echo "refusing: dirty tree"; exit 1; }
	@grep -q '^version = "$(v)"$$' pyproject.toml || { echo "refusing: v$(v) is not pyproject [project].version"; exit 1; }
	git tag -a "v$(v)" -m "release v$(v)"

phase-tag: ## Cut annotated phase tag: id=<phase id> (clean tree)
ifndef id
	$(error id=<phase id> is required)
endif
	@git diff --quiet && git diff --cached --quiet || { echo "refusing: dirty tree"; exit 1; }
	git tag -a "$(id)-done" -m "phase $(id) complete"

# Interactive harness for the sandbox-isolation experiments.
#   make setup     # configure → install → doctor → seed → run → report  (one shot)
#   make help      # list all targets
#
# Everything runs in YOUR environment against YOUR Gemini Enterprise project;
# nothing here needs to be edited by hand — `make configure` writes .env for you.

SHELL := /bin/bash
.ONESHELL:
.DEFAULT_GOAL := help

ENV_FILE := .env

# Defaults offered by `make configure` (overridden by any existing .env values).
DEF_LOCATION   := global
DEF_BASE_AGENT := antigravity-preview-05-2026
DEF_PREFIX_A   := workspaces/userA
DEF_PREFIX_B   := workspaces/userB

## help: show this list
help:
	@echo "Sandbox-isolation experiments — interactive harness"
	@echo
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  make /'
	@echo
	@echo "Typical first run:  make setup"

## setup: full interactive flow (configure, install, doctor, seed, run, report)
setup: configure install doctor seed run report

## configure: prompt for project/bucket/etc. and write .env
configure:
	@if [ -f "$(ENV_FILE)" ]; then
	  read -rp ".env already exists — overwrite? [y/N] " a
	  if [[ ! "$$a" =~ ^[Yy]$$ ]]; then echo "Keeping existing .env."; exit 0; fi
	fi
	[ -f "$(ENV_FILE)" ] && source "$(ENV_FILE)" || true
	echo "Enter configuration (press Enter to accept [default]):"
	read -rp "  PROJECT_ID            [$${PROJECT_ID:-}]: " IN_PROJECT;     PROJECT_ID="$${IN_PROJECT:-$${PROJECT_ID:-}}"
	read -rp "  GCS_BUCKET (gs://...) [$${GCS_BUCKET:-}]: " IN_BUCKET;      GCS_BUCKET="$${IN_BUCKET:-$${GCS_BUCKET:-}}"
	read -rp "  LOCATION             [$${LOCATION:-$(DEF_LOCATION)}]: " IN_LOC;       LOCATION="$${IN_LOC:-$${LOCATION:-$(DEF_LOCATION)}}"
	read -rp "  BASE_AGENT           [$${BASE_AGENT:-$(DEF_BASE_AGENT)}]: " IN_BA;    BASE_AGENT="$${IN_BA:-$${BASE_AGENT:-$(DEF_BASE_AGENT)}}"
	read -rp "  PREFIX_A             [$${PREFIX_A:-$(DEF_PREFIX_A)}]: " IN_PA;        PREFIX_A="$${IN_PA:-$${PREFIX_A:-$(DEF_PREFIX_A)}}"
	read -rp "  PREFIX_B             [$${PREFIX_B:-$(DEF_PREFIX_B)}]: " IN_PB;        PREFIX_B="$${IN_PB:-$${PREFIX_B:-$(DEF_PREFIX_B)}}"
	read -rp "  GATEWAY_URL (opt, for exp 05) [$${GATEWAY_URL:-}]: " IN_GW;          GATEWAY_URL="$${IN_GW:-$${GATEWAY_URL:-}}"
	read -rp "  MCP_TOKEN   (opt, for exp 05) [$${MCP_TOKEN:+<set>}]: " IN_TOK;       MCP_TOKEN="$${IN_TOK:-$${MCP_TOKEN:-}}"
	if [ -z "$$PROJECT_ID" ] || [ -z "$$GCS_BUCKET" ]; then
	  echo "ERROR: PROJECT_ID and GCS_BUCKET are required."; exit 1
	fi
	GCS_BUCKET="$${GCS_BUCKET%/}"
	umask 077
	cat > "$(ENV_FILE)" <<-EOF
		PROJECT_ID=$$PROJECT_ID
		GCS_BUCKET=$$GCS_BUCKET
		LOCATION=$$LOCATION
		BASE_AGENT=$$BASE_AGENT
		PREFIX_A=$$PREFIX_A
		PREFIX_B=$$PREFIX_B
		GATEWAY_URL=$$GATEWAY_URL
		MCP_TOKEN=$$MCP_TOKEN
	EOF
	echo "Wrote $(ENV_FILE) (chmod 600)."

## install: npm install (@google/genai)
install:
	@command -v npm >/dev/null || { echo "ERROR: npm not found — install Node 18+."; exit 1; }
	npm install

## doctor: preflight checks (tools, auth, API, bucket) — never fails the build
doctor:
	@echo "── preflight ───────────────────────────────────────────────"
	check() { if eval "$$2" >/dev/null 2>&1; then echo "  PASS  $$1"; else echo "  FAIL  $$1 — $$3"; fi; }
	[ -f "$(ENV_FILE)" ] && source "$(ENV_FILE)" || echo "  WARN  no .env yet (run: make configure)"
	check "node present"     "command -v node"   "install Node 18+"
	check "gcloud present"   "command -v gcloud" "install the Google Cloud CLI"
	check "ADC available"    "gcloud auth application-default print-access-token" "run: gcloud auth application-default login"
	check "deps installed"   "test -d node_modules/@google/genai" "run: make install"
	if [ -n "$${PROJECT_ID:-}" ]; then
	  check "aiplatform API enabled" "gcloud services list --enabled --project $$PROJECT_ID --filter aiplatform.googleapis.com --format 'value(config.name)' | grep -q aiplatform" "enable: gcloud services enable aiplatform.googleapis.com"
	fi
	if [ -n "$${GCS_BUCKET:-}" ]; then
	  check "bucket reachable" "gcloud storage ls $$GCS_BUCKET" "check the bucket name / permissions"
	fi
	echo "────────────────────────────────────────────────────────────"

## seed: write hello.txt into PREFIX_A and PREFIX_B (for the cross-tenant test)
seed: require-env
	@source "$(ENV_FILE)"
	echo "Seeding $$GCS_BUCKET/$$PREFIX_A and /$$PREFIX_B …"
	echo hi | gcloud storage cp - "$$GCS_BUCKET/$$PREFIX_A/hello.txt"
	echo hi | gcloud storage cp - "$$GCS_BUCKET/$$PREFIX_B/hello.txt"
	echo "Seeded."

## run: run all experiments → results/report-*.md
run: require-env
	@node run_all.mjs

## identity / two-agents / cross-tenant / controls / mcp: run one experiment
identity:     require-env ; @node run_all.mjs 01
two-agents:   require-env ; @node run_all.mjs 02
cross-tenant: require-env ; @node run_all.mjs 03
controls:     require-env ; @node run_all.mjs 04
mcp:          require-env ; @node run_all.mjs 05

## report: print the path of the latest report and show it
report:
	@latest=$$(ls -t results/report-*.md 2>/dev/null | head -1)
	if [ -z "$$latest" ]; then echo "No report yet — run: make run"; exit 0; fi
	echo "Latest report: $$latest"
	echo "────────────────────────────────────────────────────────────"
	cat "$$latest"

## cleanup: delete any leftover probe-* agents
cleanup: require-env
	@node cleanup.mjs

require-env:
	@[ -f "$(ENV_FILE)" ] || { echo "No .env — run: make configure"; exit 1; }

.PHONY: help setup configure install doctor seed run report cleanup require-env identity two-agents cross-tenant controls mcp

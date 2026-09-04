# Prompted content pipeline
#
# `make` with no target prints the target list.

SHELL := /bin/bash

# Prefer an existing virtualenv; otherwise create .venv with the newest
# supported interpreter found on PATH.
VENV        := .venv
PYTHON_BOOT := $(shell command -v python3.12 || command -v python3.11)
PYTHON      := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip

TF := terraform

# Operator credentials live in an untracked, gitignored env-file at the repo
# root. Loading it here means credentialed targets work without any command
# ever echoing a secret. Optional flags for apply (e.g. TF_FLAGS=-auto-approve).
-include .env
export
TF_FLAGS ?=

.DEFAULT_GOAL := help

.PHONY: help venv seed validate build publish clean \
        ai-select ai-dry-run ai-generate \
        ingest-init ingest-scan ingest-quality ingest-cluster ingest-derive \
        ingest-prompts ingest-draft approve-prompts ingest-finalize \
        publish-dev verify-dev promote-prod rollback-prod verify-published \
        pins pins-dry-run pins-generate pins-upload pins-csv pins-status pins-scan-rights test \
        reels reels-dry-run reels-generate \
        tf-plan-dev tf-apply-dev tf-plan-prod tf-apply-prod

# Every ingest target after init takes SHOOT=<shoot-name>
require-shoot:
	@test -n "$(SHOOT)" || { echo "error: SHOOT=<shoot-name> is required" >&2; exit 1; }

help:
	@echo "Prompted content pipeline"
	@echo ""
	@echo "  make seed           Generate the 240-pose development catalog"
	@echo "  make validate       Validate every pose (schema, taxonomy refs, images)"
	@echo "  make build          Build dist/catalog.json (runs validate first)"
	@echo "  make publish        Dry-run publish to R2 (add CONFIRM=1 to upload)"
	@echo "  make publish-dev    Build the catalog and publish it to dev (CONFIRM=1 to upload)"
	@echo "  make verify-dev     Fetch the published dev catalog, validate, report counts"
	@echo "  make promote-prod   Copy the EXACT dev catalog version to prod (CONFIRM=1; prints diff)"
	@echo "  make rollback-prod  Repoint prod latest.json (TO=<version> CONFIRM=1)"
	@echo "  make clean          Remove dist/ output and caches"
	@echo ""
	@echo "  make ingest-init    Create a shoot manifest interactively"
	@echo "  make ingest-scan     SHOOT=<name>  Extract EXIF -> _scan.json"
	@echo "  make ingest-quality  SHOOT=<name>  Score frames, flag rejects"
	@echo "  make ingest-cluster  SHOOT=<name>  Collapse near-duplicates"
	@echo "  make ingest-derive   SHOOT=<name>  Solar light band + gear"
	@echo "  make ingest-prompts  SHOOT=<name>  Gemini prompt copy (CONFIRM=1 to spend)"
	@echo "  make ingest-draft    SHOOT=<name>  Emit drafts + _review.md"
	@echo "  make approve-prompts SHOOT=<name>  Bulk-approve reviewed prompts"
	@echo "  make ingest-finalize SHOOT=<name>  Promote completed drafts into poses/"
	@echo ""
	@echo "  make ai-select      Pick the 50-pose AI subset (deterministic)"
	@echo "  make ai-dry-run     Print the image prompts; no API calls"
	@echo "  make ai-generate    Generate AI images (requires CONFIRM=1 and GEMINI_API_KEY)"
	@echo ""
	@echo "  make pins-dry-run   Render the 12-pin contact sheet (4 per cohort); uploads nothing"
	@echo "  make pins-generate  Generate + schedule new pins (PINS_ARGS='--limit 100 --start-date ...')"
	@echo "  make pins-upload    Upload rendered pins to R2 under pins/ (CONFIRM=1 to upload)"
	@echo "  make pins-csv       Write Pinterest bulk-upload CSV batches (PINS_ARGS='--batch-size 100')"
	@echo "  make pins-status    Pin counts by cohort / category / board and the schedule"
	@echo "  make pins-scan-rights  Exclusion report + shoot->pose drift check"
	@echo "  make pins ARGS=...  Any pins subcommand verbatim"
	@echo ""
	@echo "  make reels-dry-run   Render 3 first-frame PNGs + contact sheet; CSVs; no MP4s"
	@echo "  make reels-generate  Render reels (REELS_ARGS='--limit 20 --category maternity')"
	@echo "  make reels ARGS=...  Any reels subcommand verbatim"
	@echo "  make test           Run the test suite"
	@echo ""
	@echo "  make tf-plan-dev    terraform plan for envs/dev"
	@echo "  make tf-apply-dev   terraform apply for envs/dev (requires CONFIRM=1)"
	@echo "  make tf-plan-prod   terraform plan for envs/prod"
	@echo "  make tf-apply-prod  terraform apply for envs/prod (requires CONFIRM=1)"

$(VENV)/bin/activate: requirements.txt
	@test -n "$(PYTHON_BOOT)" || { echo "error: python3.11+ not found on PATH" >&2; exit 1; }
	$(PYTHON_BOOT) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@touch $(VENV)/bin/activate

venv: $(VENV)/bin/activate

seed: venv
	$(PYTHON) tools/generate_seed.py
	$(PYTHON) tools/make_placeholders.py

validate: venv
	$(PYTHON) tools/validate.py

build: venv
	$(PYTHON) tools/build_catalog.py

publish: venv
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/publish.py --env $(or $(ENV),dev) --confirm
else
	$(PYTHON) tools/publish.py --env $(or $(ENV),dev)
endif

ingest-init: venv
	$(PYTHON) tools/ingest_init.py $(SHOOT)

ingest-scan: venv require-shoot
	$(PYTHON) tools/ingest_scan.py $(SHOOT)

ingest-quality: venv require-shoot
	$(PYTHON) tools/ingest_quality.py $(SHOOT) $(INGEST_ARGS)

ingest-cluster: venv require-shoot
	$(PYTHON) tools/ingest_cluster.py $(SHOOT) $(INGEST_ARGS)

ingest-derive: venv require-shoot
	$(PYTHON) tools/ingest_derive.py $(SHOOT)

ingest-prompts: venv require-shoot
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/ingest_prompts.py $(SHOOT) --yes $(INGEST_ARGS)
else
	$(PYTHON) tools/ingest_prompts.py $(SHOOT) $(INGEST_ARGS)
endif

ingest-draft: venv require-shoot
	$(PYTHON) tools/ingest_draft.py $(SHOOT)

approve-prompts: venv require-shoot
	$(PYTHON) tools/ingest_draft.py $(SHOOT) --approve-prompts

ingest-finalize: venv require-shoot
	$(PYTHON) tools/ingest_finalize.py $(SHOOT) $(INGEST_ARGS)

ai-select: venv
	$(PYTHON) tools/select_ai_subset.py

ai-dry-run: venv
	$(PYTHON) tools/generate_ai_images.py --dry-run

ai-generate: venv
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/generate_ai_images.py --yes $(AI_ARGS)
else
	@echo "Refusing to spend on generation without CONFIRM=1 (make ai-generate CONFIRM=1)" >&2; exit 1
endif

ai-instructions: venv
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/generate_instructions.py $(or $(AI_ARGS),--missing) --yes
else
	$(PYTHON) tools/generate_instructions.py $(or $(AI_ARGS),--missing)
endif

verify-published: venv
	$(PYTHON) tools/verify_published.py --env $(or $(ENV),dev)

# Dev -> prod is a promotion pipeline: the catalog is built ONCE, published
# and verified in dev, and promote-prod copies that exact artifact. Prod is
# never rebuilt and never deleted from; rollback is a latest.json repoint.
publish-dev: build
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/publish.py --env dev --confirm
else
	$(PYTHON) tools/publish.py --env dev
endif

verify-dev: venv
	$(PYTHON) tools/verify_published.py --env dev

promote-prod: venv
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/publish.py --promote --confirm
else
	$(PYTHON) tools/publish.py --promote
endif

rollback-prod: venv
	@test -n "$(TO)" || { echo "usage: make rollback-prod TO=<version> [CONFIRM=1]" >&2; exit 1; }
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/publish.py --rollback-to $(TO) --confirm
else
	$(PYTHON) tools/publish.py --rollback-to $(TO)
endif

# Pinterest pin pipeline (tools/pins.py). Uploads are dry-run without CONFIRM=1.
pins: venv
	$(PYTHON) tools/pins.py $(ARGS)

pins-dry-run: venv
	$(PYTHON) tools/pins.py generate --dry-run

pins-generate: venv
	$(PYTHON) tools/pins.py generate $(PINS_ARGS)

pins-upload: venv
ifeq ($(CONFIRM),1)
	$(PYTHON) tools/pins.py upload --env $(or $(ENV),dev) --confirm
else
	$(PYTHON) tools/pins.py upload --env $(or $(ENV),dev)
endif

pins-csv: venv
	$(PYTHON) tools/pins.py csv $(PINS_ARGS)

pins-status: venv
	$(PYTHON) tools/pins.py status

pins-scan-rights: venv
	$(PYTHON) tools/pins.py scan-rights

# Reels pipeline (tools/reels.py): short vertical videos for Reels/TikTok/Shorts.
reels: venv
	$(PYTHON) tools/reels.py $(ARGS)

reels-dry-run: venv
	$(PYTHON) tools/reels.py generate --dry-run

reels-generate: venv
	$(PYTHON) tools/reels.py generate $(REELS_ARGS)

test: venv
	$(PYTHON) -m pytest tests -q

clean:
	rm -rf dist/catalog.json dist/pins dist/pins_csv dist/reels .pytest_cache tools/__pycache__ tools/pinterest/__pycache__ tools/reels_gen/__pycache__

tf-plan-bootstrap:
	$(TF) -chdir=infra/bootstrap plan

tf-apply-bootstrap:
ifeq ($(CONFIRM),1)
	$(TF) -chdir=infra/bootstrap apply $(TF_FLAGS)
else
	@echo "Refusing to apply without CONFIRM=1 (make tf-apply-bootstrap CONFIRM=1)" >&2; exit 1
endif

tf-init-dev:
	$(TF) -chdir=infra/envs/dev init -input=false -reconfigure

tf-init-prod:
	$(TF) -chdir=infra/envs/prod init -input=false -reconfigure

tf-plan-dev:
	$(TF) -chdir=infra/envs/dev plan

tf-plan-prod:
	$(TF) -chdir=infra/envs/prod plan

tf-apply-dev:
ifeq ($(CONFIRM),1)
	$(TF) -chdir=infra/envs/dev apply $(TF_FLAGS)
else
	@echo "Refusing to apply without CONFIRM=1 (make tf-apply-dev CONFIRM=1)" >&2; exit 1
endif

tf-apply-prod:
ifeq ($(CONFIRM),1)
	$(TF) -chdir=infra/envs/prod apply $(TF_FLAGS)
else
	@echo "Refusing to apply without CONFIRM=1 (make tf-apply-prod CONFIRM=1)" >&2; exit 1
endif

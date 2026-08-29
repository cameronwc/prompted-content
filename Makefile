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

.DEFAULT_GOAL := help

.PHONY: help venv seed validate build publish clean \
        ai-select ai-dry-run ai-generate \
        tf-plan-dev tf-apply-dev tf-plan-prod tf-apply-prod

help:
	@echo "Prompted content pipeline"
	@echo ""
	@echo "  make seed           Generate the 240-pose development catalog"
	@echo "  make validate       Validate every pose (schema, taxonomy refs, images)"
	@echo "  make build          Build dist/catalog.json (runs validate first)"
	@echo "  make publish        Dry-run publish to R2 (add CONFIRM=1 to upload)"
	@echo "  make clean          Remove dist/ output and caches"
	@echo ""
	@echo "  make ai-select      Pick the 50-pose AI subset (deterministic)"
	@echo "  make ai-dry-run     Print the image prompts; no API calls"
	@echo "  make ai-generate    Generate AI images (requires CONFIRM=1 and GEMINI_API_KEY)"
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

clean:
	rm -rf dist/catalog.json .pytest_cache tools/__pycache__

tf-plan-dev:
	$(TF) -chdir=infra/envs/dev plan

tf-plan-prod:
	$(TF) -chdir=infra/envs/prod plan

tf-apply-dev:
ifeq ($(CONFIRM),1)
	$(TF) -chdir=infra/envs/dev apply
else
	@echo "Refusing to apply without CONFIRM=1 (make tf-apply-dev CONFIRM=1)" >&2; exit 1
endif

tf-apply-prod:
ifeq ($(CONFIRM),1)
	$(TF) -chdir=infra/envs/prod apply
else
	@echo "Refusing to apply without CONFIRM=1 (make tf-apply-prod CONFIRM=1)" >&2; exit 1
endif

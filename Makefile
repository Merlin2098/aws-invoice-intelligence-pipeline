ifeq ($(OS),Windows_NT)
PYTHON ?= ./.venv/Scripts/python.exe
UV ?= py -3 -m uv
BOOTSTRAP_PYTHON ?= py -3
NPM ?= npm.cmd
SHELL := C:/Program Files/Git/usr/bin/bash.exe
.SHELLFLAGS := -ec
else
PYTHON ?= ./.venv/bin/python
UV ?= uv
BOOTSTRAP_PYTHON ?= python3
NPM ?= npm
endif

.PHONY: init uv-init uv-update uv-reset package package-chat treemap lint fmt clean ai-refresh \
        bootstrap-init bootstrap-apply \
        tf-init tf-plan tf-apply upload-lambdas bootstrap-lambdas \
        frontend-install build-frontend sync-frontend-env deploy-frontend \
        verify-dev \
        tf-destroy-plan tf-destroy verify-destroyed backup-data-lake

init:
	$(BOOTSTRAP_PYTHON) scripts/run_uv_sync.py init

uv-init:
	$(BOOTSTRAP_PYTHON) scripts/run_uv_sync.py init

uv-update:
	$(BOOTSTRAP_PYTHON) scripts/run_uv_sync.py update

uv-reset:
	$(BOOTSTRAP_PYTHON) scripts/run_uv_sync.py reset

package:
	uv run python scripts/package.py --package-manager uv

package-chat:
	uv run python scripts/package.py --package-manager uv --target chat

treemap:
	$(PYTHON) scripts/generate_treemap.py

lint:
	$(PYTHON) scripts/quality/run_ruff_check.py

fmt:
	$(PYTHON) scripts/quality/run_ruff_format.py

clean:
	uv run python scripts/package.py --package-manager uv --clean

ai-refresh:
	$(PYTHON) scripts/hooks/ai_refresh.py

bootstrap-init:
	terraform -chdir=infra/bootstrap init

bootstrap-apply:
	terraform -chdir=infra/bootstrap apply

tf-init:
	terraform -chdir=infra/envs/dev init

tf-plan:
	terraform -chdir=infra/envs/dev plan -var-file=terraform.tfvars -out=tfplan

tf-apply:
	terraform -chdir=infra/envs/dev apply tfplan

upload-lambdas:
	$(eval ARTIFACT_BUCKET := $(shell terraform -chdir=infra/envs/dev output -raw artifact_bucket_name))
	aws s3 cp artifacts/lambda/control_plane_bundle.zip \
		s3://$(ARTIFACT_BUCKET)/artifacts/lambda/control_plane_bundle.zip
	aws s3 cp artifacts/lambda/chat_bundle.zip \
		s3://$(ARTIFACT_BUCKET)/artifacts/lambda/chat_bundle.zip

# From-scratch bootstrap only: builds the zips, applies once (Lambda resources
# are expected to fail because the artifact bucket doesn't have the zips yet),
# uploads the zips, then applies again so Lambda + dependents succeed.
# Requires approval per apply, same as tf-apply.
bootstrap-lambdas: package package-chat
	$(MAKE) tf-plan
	$(MAKE) tf-apply
	$(MAKE) upload-lambdas
	$(MAKE) tf-plan
	$(MAKE) tf-apply

frontend-install:
	cd frontend && $(NPM) install

build-frontend:
	cd frontend && $(NPM) run build

# Regenerates frontend/.env.local from the live API Gateway URL. Run any time
# the API Gateway is recreated (new web_api_base_url) before rebuilding.
sync-frontend-env:
	$(eval API_BASE_URL := $(shell terraform -chdir=infra/envs/dev output -raw web_api_base_url))
	echo "VITE_API_BASE_URL=$(API_BASE_URL)" > frontend/.env.local

deploy-frontend:
	$(eval SITE_BUCKET ?= $(shell terraform -chdir=infra/envs/dev output -raw site_bucket_name))
	$(eval CF_DIST_ID ?= $(shell terraform -chdir=infra/envs/dev output -raw cloudfront_distribution_id))
	aws s3 sync frontend/dist/ s3://$(SITE_BUCKET)/ --delete
	@if [ -n "$(CF_DIST_ID)" ]; then \
		aws cloudfront create-invalidation --distribution-id $(CF_DIST_ID) --paths "/*"; \
	fi

# Runs the read-only step6-step9 verification scripts against dev in sequence.
verify-dev:
	$(PYTHON) tests/terraform/step6_check_portal_access.py
	$(PYTHON) tests/terraform/step7_test_raw_uploads.py
	$(PYTHON) tests/terraform/step8_browser_upload_repro.py
	$(PYTHON) tests/terraform/step9_test_chat_nlq.py

# Backs up the data lake bucket before a destroy. force_destroy=true means
# terraform destroy deletes bucket contents with no confirmation prompt.
backup-data-lake:
	$(eval DATA_LAKE_BUCKET := $(shell terraform -chdir=infra/envs/dev output -raw data_lake_bucket_name))
	aws s3 sync s3://$(DATA_LAKE_BUCKET)/ ./backup-data-lake/

# Dry run only — writes the full plan to logs/tf_step3_destroy_<timestamp>.log,
# touches nothing. Equivalent to tests/terraform/step3_destroy.sh (no args).
tf-destroy-plan:
	tests/terraform/step3_destroy.sh

# Real destroy. Requires the same explicit approval as any destroy, per
# AGENTS.md -- this target is not itself a substitute for that approval.
tf-destroy:
	tests/terraform/step3_destroy.sh --confirm

# Post-destroy check: confirms Terraform state is empty and the data lake /
# artifact / site buckets are gone. Pass the bucket names as env vars if you
# captured them before destroying (terraform output no longer works once the
# state is empty).
verify-destroyed:
	terraform -chdir=infra/envs/dev state list
	@if [ -n "$(DATA_LAKE_BUCKET)" ]; then aws s3api head-bucket --bucket $(DATA_LAKE_BUCKET) 2>&1 || true; fi
	@if [ -n "$(ARTIFACT_BUCKET)" ]; then aws s3api head-bucket --bucket $(ARTIFACT_BUCKET) 2>&1 || true; fi
	@if [ -n "$(SITE_BUCKET)" ]; then aws s3api head-bucket --bucket $(SITE_BUCKET) 2>&1 || true; fi
	aws cloudfront list-distributions \
		--query "DistributionList.Items[?Comment=='invoice-pipeline-dev']"

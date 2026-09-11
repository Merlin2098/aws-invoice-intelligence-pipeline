# Deployment Guide — `dev` Environment

This guide is the single end-to-end checklist for standing up (or updating)
the `invoice-pipeline-dev` AWS environment: Terraform infrastructure, Lambda
artifacts, and the static web portal. `terraform apply` alone is **not**
enough — Lambda zips must be packaged and uploaded to S3 before `apply`, and
the frontend must be built with the correct API URL and synced to S3 +
CloudFront after `apply`.

> Do not run `terraform apply` / `terraform destroy` without explicit
> approval, per [AGENTS.md](../AGENTS.md).

## 1. Prerequisites

- AWS credentials configured for account `184670914470`, region
  `us-east-1`.
- Git Bash as the terminal (all commands in this guide use Bash syntax).
- Terraform, `uv`, Node/npm, and `make` available. On Windows without
  `make.exe` in `PATH`, see
  [docs/windows_setup/make_cheatlist.md](windows_setup/make_cheatlist.md).
- `infra/envs/dev/terraform.tfvars` exists and is filled in (copy from
  [infra/envs/dev/terraform.tfvars.example](../infra/envs/dev/terraform.tfvars.example)
  if missing). This file is gitignored — it must be created locally.

## 2. Bootstrap (one-time only)

Only needed if the Terraform S3 backend (`infra/bootstrap`) doesn't exist
yet for this account/region:

```bash
make bootstrap-init
make bootstrap-apply   # requires approval
```

## 3. First Terraform apply — initial infrastructure

Every Lambda module in
[infra/envs/dev/main.tf](../infra/envs/dev/main.tf) sets:

```hcl
source_code_hash = filebase64sha256("${path.root}/../../../artifacts/lambda/control_plane_bundle.zip")
```

Terraform reads this **local file** at plan time, so the zip must exist on
disk before any plan/apply. Separately, `s3_bucket`/`s3_key` point at the
artifact bucket (name from the `artifact_bucket_name` Terraform output, i.e.
`${name_prefix}-${account_id}-${artifact_bucket_suffix}`), which doesn't
exist until Terraform creates it. On a from-scratch environment this is a
two-pass bootstrap:

```bash
make tf-init
make bootstrap-lambdas   # requires approval (applies twice)
```

`bootstrap-lambdas` builds both Lambda zips, applies once (Lambda resources
are **expected to fail** here — the S3 keys don't exist in the artifact
bucket yet; buckets, IAM, Step Functions, API Gateway, and CloudFront should
still apply successfully), uploads the zips to the now-existing artifact
bucket, then applies again so Lambda and everything depending on it
(Step Functions, API Gateway integrations, CloudFront) creates successfully.

If the artifact bucket **already exists** (e.g. re-running this guide
against an existing `dev` environment), skip straight to a single-pass
update:

```bash
make package
make package-chat
make upload-lambdas
make tf-plan
make tf-apply     # requires approval
```

## 4. Frontend configuration and deploy

This is the step that caused both portal bugs fixed in this session — a
stale `frontend/.env.local` pointed at an old API Gateway ID, and the
upload `Content-Type` for `.tif`/`.tiff` files didn't match what was signed.

1. Sync `frontend/.env.local` to the live API Gateway URL:

   ```bash
   make sync-frontend-env
   ```

   **This file is gitignored and must be regenerated any time the API
   Gateway is recreated (new ID).** A stale value here is the most likely
   cause of "upload and chat fail only from the web portal" symptoms. Rerun
   this any time `web_api_base_url` changes.
2. Install dependencies (first time, or after `package.json` changes):

   ```bash
   make frontend-install
   ```
3. Build and deploy — `deploy-frontend` auto-discovers the site bucket and
   CloudFront distribution ID from Terraform outputs, so no need to look
   them up manually (pass `SITE_BUCKET=`/`CF_DIST_ID=` explicitly only if
   you need to target something other than the current `dev` state):

   ```bash
   make build-frontend
   make deploy-frontend
   ```

## 5. Verification

```bash
make verify-dev
```

Runs the read-only `tests/terraform/step6`–`step9` scripts in sequence
against dev (see each script's docstring for options — questions, files,
timeouts — if you need to run one individually):

- `step6_check_portal_access.py` — confirms site bucket contents and
  CloudFront returns 200 for the portal and key assets. Note: this script
  has some asset filenames/bucket/distribution IDs hardcoded and may need
  updating after a fresh deploy with new asset hashes.
- `step7_test_raw_uploads.py` — end-to-end upload of a `data/raw` file via
  presigned URLs, polling invoice status to `Completed`.
- `step8_browser_upload_repro.py` — reproduces the browser's upload flow
  (same `Content-Type` logic, `Origin` header) with full HTTP error capture.
- `step9_test_chat_nlq.py` — sends natural-language questions to `POST /chat` and logs the generated SQL, rows, and answer.

After any frontend deploy, do a **hard refresh** (Ctrl+Shift+R) or open the
portal in an incognito window — CloudFront invalidation can take a minute,
and browsers cache the old JS bundle aggressively.

## 6. Subsequent updates

- **Backend code change** (`src/`): `make package` (and `make package-chat`
  if `src/analytics` or `src/aws` changed) → `make upload-lambdas` →
  `make tf-plan` → `make tf-apply` (requires approval). The updated
  `source_code_hash` (computed from the local zip) is what tells Terraform
  the Lambda code changed — both the local rebuild and the S3 upload are
  required.
- **Frontend-only change**: `make build-frontend` → `make deploy-frontend`.
  No Terraform needed unless `VITE_API_BASE_URL` changed.
- **Infra change**: standard `make tf-plan` → `make tf-apply` (requires
  approval). If the API Gateway was replaced (new `web_api_base_url`),
  redo step 4 (`make sync-frontend-env`, rebuild, redeploy the frontend).

## 7. Teardown

See [docs/destruction_guide.md](destruction_guide.md) for the full safe
teardown checklist (what gets deleted, backup steps, dry run via
`tests/terraform/step3_destroy.sh`, and post-destroy verification).
`terraform destroy` requires the same explicit approval as `apply`, per
[AGENTS.md](../AGENTS.md).

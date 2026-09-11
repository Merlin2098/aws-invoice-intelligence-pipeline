# Tests — Git Bash Guide

Everything under `tests/` runs from **Git Bash**, not PowerShell. This is
the entry point: what each subdirectory is for, prerequisites, and the
commands to run.

## Prerequisites (all scripts)

- Git Bash as the terminal (see [docs/deployment_guide.md](../docs/deployment_guide.md)).
- AWS credentials in the environment or default profile, with access to the
  deployed `invoice-pipeline-dev` resources.
- Terraform state for `infra/envs/dev` initialized and up to date
  (`terraform -chdir=infra/envs/dev init`) — most scripts read live values
  via `terraform output`, so an uninitialized or out-of-date state will make
  them fail before they even reach AWS.
- Python scripts: the project's `uv`-managed virtualenv, with `boto3`
  available (already a project dependency).
- Run everything from the repo root unless a script says otherwise; every
  script resolves paths relative to its own location, so `cd`-ing into a
  subdirectory first is never required.

## What's here

### `tests/aws/` — pipeline and web API smoke tests

The main day-to-day smoke suite: uploads real documents through the
pipeline, exercises the upload/status API, and asks the chat API natural-
language questions. AWS service calls (S3, Step Functions) use `boto3`
directly; the web API endpoints are called over real HTTP since API
Gateway isn't reachable through a boto3 service client.

```bash
tests/aws/pre_ui_smoke_suite.sh                    # all three, in sequence
python tests/aws/e2e_pipeline_smoke.py --count 5   # direct pipeline only
python tests/aws/upload_status_api_smoke.py        # upload + status API only
python tests/aws/chat_api_smoke.py                 # chat API only
```

See [tests/aws/README.md](aws/README.md) for full flag reference on each
script.

### `tests/terraform/` — infra lifecycle and diagnostics

Ordered steps for standing up, verifying, and tearing down the
`infra/envs/dev` stack, plus point-in-time diagnostic scripts written while
debugging specific issues. Steps 1–4 are Bash wrappers around
`terraform`/`aws` CLI calls; steps 5–9 are Python (some predate this
Git Bash migration and already used `subprocess/urllib`, so they were left
as-is).

```bash
tests/terraform/step1_fmt_validate_init.sh                 # fmt + validate + init
tests/terraform/step2_plan_apply_output.sh                 # plan -> tfplan
tests/terraform/step2c_check_lock.sh                       # inspect state lock, apply tfplan
tests/terraform/step2d_lookup_remaining_ids.sh              # look up AWS resource IDs for imports.tf
tests/terraform/step3_destroy.sh [--confirm]                # plan -destroy (dry run) / real destroy
tests/terraform/step4_get_portal_url.sh                     # print CloudFront portal URL + distribution ID

python tests/terraform/step5_check_site_bucket.py           # diagnose site bucket AccessDenied
python tests/terraform/step6_check_portal_access.py         # diagnose portal not loading via CloudFront
python tests/terraform/step7_test_raw_uploads.py             # end-to-end upload via presigned URLs
python tests/terraform/step8_browser_upload_repro.py         # reproduce the browser's exact upload flow
python tests/terraform/step9_test_chat_nlq.py                # send NL questions to /chat, log SQL + answer
```

Every step logs to its own timestamped file under `logs/`, in addition to
stdout. `step3_destroy.sh` requires the same explicit approval as
`terraform destroy` itself, per [AGENTS.md](../AGENTS.md) — running it with
`--confirm` is not, on its own, that approval.

### `tests/model_comparison/` — Bedrock model comparison harness

Not a smoke test — a one-off (but reusable) harness that compares candidate
Bedrock models against the parser and NLQ->SQL prompts using the real
production prompts and validators. See
[tests/model_comparison/README.md](model_comparison/README.md) and
[docs/model_comparison_report.md](../docs/model_comparison_report.md) for
the latest results.

```bash
python tests/model_comparison/run_comparison.py --task parser
python tests/model_comparison/run_comparison.py --task nlq
```

## Notes

- All AWS-touching scripts here are read-heavy or additive (uploads,
  reads, polls) except `terraform apply`/`terraform destroy` calls, which
  carry the same approval requirement as running those commands directly —
  see [AGENTS.md](../AGENTS.md) for the project's approval boundaries.
- If a script fails immediately with a Terraform backend/output error,
  check `terraform -chdir=infra/envs/dev init` and `terraform -chdir=infra/envs/dev output`
  work on their own first — most failures here are an out-of-date or
  uninitialized local Terraform state, not a bug in the script.

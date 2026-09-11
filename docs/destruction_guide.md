# Safe Destruction Guide — `dev` Environment

This is the companion to [docs/deployment_guide.md](deployment_guide.md): a
checklist for tearing down (partially or fully) the `invoice-pipeline-dev`
AWS environment without losing data you actually want to keep, and without
leaving orphaned resources that keep billing.

> `terraform destroy` (and any state modification) requires explicit
> approval, per [AGENTS.md](../AGENTS.md). This applies to every `apply`/
> `destroy` step below — they are listed for completeness, not as
> pre-authorized actions.

## 1. Before you destroy — what you will lose

This environment is configured with `force_destroy = true`
([infra/envs/dev/terraform.tfvars](../infra/envs/dev/terraform.tfvars)),
which applies to:

- **Data lake bucket** (`raw/`, `bronze/`, `silver/`, `gold/`, `errors/`) —
  all uploaded invoices, Textract output, and processed Gold data.
- **Artifact bucket** (Lambda zips) — re-creatable via `make package` /
  `make package-chat` + `aws s3 cp`, so low risk.
- **Site bucket** (frontend static assets) — re-creatable via
  `make build-frontend` + `make deploy-frontend`.

`force_destroy = true` means Terraform will delete these buckets **even if
they contain objects** — no manual emptying step is needed, but it also
means **`terraform destroy` permanently deletes all pipeline data with no
prompt for confirmation about bucket contents**.

If you want to keep any processed invoices or Gold tables, back them up
first:

```bash
make backup-data-lake
```

(looks up the bucket name via `terraform output` and syncs it to
`./backup-data-lake/`.)

## 2. What `terraform destroy` does NOT touch

- **Terraform state backend** (`infra/bootstrap`) — the S3 bucket holding
  remote state has `force_destroy = false` and its own lifecycle. Destroying
  `infra/envs/dev` does not destroy the state bucket itself, only the
  resources tracked in that state.
- **CloudWatch Logs created by Lambda invocations** — log groups are
  Terraform-managed (so `destroy` removes them), but if retention expired
  or groups were created outside Terraform, check manually after destroy.
- **Bedrock model access / Athena query history** — these are account-level
  or service-level, not resources this stack creates directly.

## 3. Partial teardown (recommended for cost control between sessions)

If you just want to stop ongoing costs (Bedrock/Textract calls, API Gateway,
CloudFront) but plan to redeploy soon, a full `destroy` + later full
`apply` is usually simpler than trying to selectively stop services —
Terraform doesn't have a "pause" concept. There is no partial-teardown
target in this repo; treat destroy as all-or-nothing for `infra/envs/dev`.

If cost is the only concern, check the budget alert first instead of
tearing down:

```bash
terraform -chdir=infra/envs/dev output budget_name
```

(configured via `budget_alert_email` in
[infra/envs/dev/terraform.tfvars](../infra/envs/dev/terraform.tfvars),
currently `rfuculmana@gmail.com`).

## 4. Full teardown — dry run first

Always preview what will be destroyed before running the real thing:

```bash
make tf-destroy-plan
```

Runs `terraform plan -destroy` (via `tests/terraform/step3_destroy.sh`) and
writes the full plan to `logs/tf_step3_destroy_<timestamp>.log` — **no
resources are touched** by this target.

Review the plan for anything unexpected, in particular:

- Resource count roughly matches what `terraform state list` shows.
- No resources outside this project's naming prefix (`invoice-pipeline-dev*`)
  appear in the plan — if they do, stop and investigate before proceeding
  (could indicate a misconfigured backend/workspace).

## 5. Full teardown — actual destroy

Only after the dry run looks correct and you have **explicit approval**:

```bash
make tf-destroy
```

Runs `terraform destroy -auto-approve` (via `tests/terraform/step3_destroy.sh
--confirm`) and logs to `logs/tf_step3_destroy_<timestamp>.log`. Note
`-auto-approve` skips Terraform's own interactive confirmation prompt — this
is why the explicit approval step in this workflow matters; there is no
second safety net from Terraform itself. `make tf-destroy` is not itself the
approval — get explicit approval first, same as `tf-apply`.

## 6. If destroy fails on CloudFront OAC / WAF Web ACL ("still in use")

If `terraform destroy` reports errors like:

```
Error: deleting CloudFront Origin Access Control (<id>): ... 409,
OriginAccessControlInUse: The CloudFront origin access control is still
being used.

Error: deleting WAFv2 WebACL (<id>): ... 400,
WAFAssociatedItemException: ... your resource is being used by another
resource ...
```

this means `aws_cloudfront_distribution.site` was removed from the
Terraform state (the `destroy` step for it reported success and Terraform
no longer tracks it), but the **distribution itself is still live in AWS**
— CloudFront deletion requires the distribution to be disabled and fully
propagated (`Status: Deployed` with `Enabled: false`) *before* it can be
deleted, and that propagation can outlast the single `terraform destroy`
call. The orphaned distribution keeps referencing the OAC and WAF Web ACL,
so AWS refuses to delete either.

`terraform state list` will show only the two leftover resources:

```
aws_cloudfront_origin_access_control.site
aws_wafv2_web_acl.portal
```

To resolve, use `tests/terraform/step10_cleanup_orphaned_distribution.py` to
finish deleting the orphaned distribution, then re-run `terraform destroy`.
The script matches the distribution by `Comment` (default
`invoice-pipeline-dev`), disables it, and polls for propagation (typically
5-15 minutes) — split from the actual delete so an interrupted run never
leaves a half-deleted distribution:

```bash
# Find + disable + wait for propagation (no delete yet)
tests/terraform/step10_cleanup_orphaned_distribution.py

# Once it reports propagation done, delete it
tests/terraform/step10_cleanup_orphaned_distribution.py --delete
```

Pass `--id <distribution_id>` instead of `--comment` if you already have the
ID from the error (or from a `Comment` collision). Once the delete step
succeeds, re-run `terraform destroy` — the OAC and WAF Web ACL should now
delete cleanly since nothing references them.

## 7. After destroy — verify and clean up leftovers

Terraform-managed resources should all be gone, but verify the things most
likely to linger or cost money if orphaned:

```bash
make verify-destroyed
```

Confirms `terraform state list` is empty and that the CloudFront
distribution is gone. Since `terraform output` no longer works once state is
empty, capture the bucket names *before* destroying (e.g.
`terraform -chdir=infra/envs/dev output -raw data_lake_bucket_name`, and
likewise for `artifact_bucket_name` / `site_bucket_name`) and pass them as
env vars to get `head-bucket` 404 checks on each:

```bash
make verify-destroyed DATA_LAKE_BUCKET=<bucket> ARTIFACT_BUCKET=<bucket> SITE_BUCKET=<bucket>
```

Things that are **not** part of `infra/envs/dev` state and will survive a
destroy (by design — these are either one-time bootstrap or local-only):

- `infra/bootstrap` (Terraform state bucket) — only tear down separately if
  you're decommissioning the project entirely; see Section 8.
- Local artifacts: `artifacts/lambda/*.zip`, `frontend/dist/`,
  `frontend/.env.local` — harmless to keep, needed again on redeploy.
- `logs/*.log` — local diagnostic history, not AWS state.

## 8. Decommissioning the state backend (rare — separate stack)

Only do this if you are permanently removing the project from this AWS
account, after `infra/envs/dev` has been fully destroyed (the state bucket
is what `infra/envs/dev` itself depends on for remote state):

```bash
terraform -chdir=infra/bootstrap plan -destroy
terraform -chdir=infra/bootstrap destroy   # requires approval
```

The state bucket has `force_destroy = false` and versioning enabled
([infra/bootstrap/main.tf](../infra/bootstrap/main.tf)) — if it contains any
state file versions, `destroy` will fail until those are removed. This is an
intentional extra safeguard against accidentally deleting the only record of
what was deployed; do not bypass it by toggling `force_destroy` unless you
are certain no other environment depends on this backend.

## 9. Redeploying after a full teardown

A full `destroy` removes the artifact bucket, so the next `apply` is a
from-scratch run — follow
[docs/deployment_guide.md](deployment_guide.md) Section 3 (two-pass
bootstrap) again, including re-packaging Lambdas and re-running Section 4
(frontend `.env.local` + rebuild + redeploy), since the new API Gateway and
CloudFront distribution will have new IDs/URLs.

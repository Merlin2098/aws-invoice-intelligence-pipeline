# AWS Smoke Tests

Git Bash-native replacements for the former PowerShell smoke test scripts
(`tests/*.ps1`). AWS service calls (S3, Step Functions) use `boto3` directly;
the web API endpoints (`/uploads`, `/invoices/*/status`, `/chat`) are called
over real HTTP since they're only reachable through API Gateway, not through
a boto3 service client. Terraform outputs (bucket names, ARNs, the API base
URL) are read via the `terraform` binary, same as `tests/terraform/*.py`.

All scripts require AWS credentials in the environment (or default profile)
with access to the deployed `invoice-pipeline-dev` resources, and assume
Terraform state for `infra/envs/dev` is initialized and up to date.

## Scripts

- **`e2e_pipeline_smoke.py`** — uploads a random sample of `data/raw`
  documents straight to the data lake's `raw/` prefix (bypassing the web
  upload API), waits for the triggered Step Functions executions to reach a
  terminal state, and checks for terminal S3 outputs
  (`silver/valid`, `silver/rejected`, `errors/silver_failed`).

  ```bash
  python tests/aws/e2e_pipeline_smoke.py --count 5
  ```

- **`upload_status_api_smoke.py`** — exercises the web upload API end to
  end: requests presigned PUT URLs from `POST /uploads`, uploads document(s)
  through them, polls `GET /invoices/{id}/status` until terminal, then
  checks `GET /invoices`.

  ```bash
  python tests/aws/upload_status_api_smoke.py --count 1
  ```

- **`chat_api_smoke.py`** — sends natural-language questions to
  `POST /chat` and reports the generated SQL, answer, row count, and
  latency for each.

  ```bash
  python tests/aws/chat_api_smoke.py --questions "How much did we spend?" "Top 5 suppliers?"
  ```

- **`gold_partition_registration_smoke.py`** — regression test for
  automatic Gold partition registration. Uploads document(s) through the
  real web upload API, waits for the pipeline to complete, then checks via
  `glue:GetPartitions` (boto3) that the new `batch_id` partition appears in
  the `gold_documents` catalog **without running `MSCK REPAIR TABLE`**, and
  cross-checks with a `/chat` question. `consolidate_gold` registers each
  batch's partition itself right after writing the parquet; a failure here
  usually means its Glue IAM permissions or `GLUE_DATABASE` /
  `GLUE_GOLD_DOCUMENTS_TABLE` env vars drifted.

  ```bash
  python tests/aws/gold_partition_registration_smoke.py --count 1
  ```

- **`pre_ui_smoke_suite.sh`** — runs all three above in sequence (direct
  pipeline, then upload/status API, then chat API). Stops on the first
  failure.

  ```bash
  tests/aws/pre_ui_smoke_suite.sh
  tests/aws/pre_ui_smoke_suite.sh --skip-chat-api --direct-count 3
  tests/aws/pre_ui_smoke_suite.sh --chat-questions "Question one?|Question two?"
  ```

  `--chat-questions` takes one string with questions separated by `|`
  (rather than a shell array) so a question containing spaces doesn't need
  extra quoting.

Every script writes its own timestamped log to `logs/` in addition to
stdout, matching the convention used by `tests/terraform/*.py`.

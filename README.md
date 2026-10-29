# My Housing Platform

Helps young people in and leaving foster care get ready for housing. This repo holds
the app (Next.js web, FastAPI API, PostgreSQL) and its AWS infrastructure (Terraform).

| Folder | What's in it |
|--------|--------------|
| `app/web` | Next.js site: sign-in, the Doc Vault pages (`/vault`, `/shared`), staff admin (`/admin`) |
| `app/api` | FastAPI API, database migrations (Alembic), the Doc Vault worker and tests |
| `terraform/` | One folder per AWS stack (see [Infrastructure](#infrastructure)) |
| `scripts/deploy-dev.sh` | Builds and deploys the app to dev |
| `docker-compose.yml` | The whole stack on your machine |

Sign-in uses Amazon Cognito: a users pool (youth and supporters) and a staff pool
(for `/admin`). Accounts are created by staff; there is no self sign-up.

## Run it locally

Everything runs in Docker. Local storage, queue and keys stand in for AWS, so no
real documents or AWS data are used.

1. Copy `.env.example` to `.env` and fill it in. The Cognito values come from
   `terraform output` in `terraform/identity` (the comments in `.env.example` say which).
2. Add a local vault key (the command doesn't print it):

   ```
   grep -q '^VAULT_LOCAL_KEY=.\+' .env || { sed -i '/^VAULT_LOCAL_KEY=/d' .env; echo "VAULT_LOCAL_KEY=$(openssl rand -base64 32)" >> .env; }
   ```

3. Start everything:

   ```
   docker compose up --build
   ```

| Address | What |
|---------|------|
| http://localhost:3000 | The site |
| http://localhost:8000/docs | API docs |
| http://localhost:9325 | Local queue (ElasticMQ) |

Local stand-ins: SeaweedFS for S3 (MinIO no longer publishes images), ElasticMQ for
SQS, a key in `.env` for KMS, and `local_scanner.py` for GuardDuty (it flags only the
standard EICAR antivirus test file). The database keeps its data between runs.

## Tests

```
docker compose run --rm api-test
docker compose run --rm web-test
```

The API tests create and drop their own database (`mhp_test_*`); your local data
isn't touched. They never call AWS: S3, SQS and KMS are faked. The web tests
(Vitest) cover the pieces of the pages that hold logic, and building the web image
runs the TypeScript check.

## Deploy to dev

Commit your work first; the script stops if anything is uncommitted, so every image
matches a commit. Then:

```
scripts/deploy-dev.sh <api-tag> <web-tag>
```

for example `scripts/deploy-dev.sh 0.11.0 0.7.0`. Image tags are permanent: bump the
tag of anything you changed. A tag that's already in ECR is reused, not rebuilt.

The script builds and pushes new images, shows the Terraform plan for `terraform/app`,
waits for you to type `yes`, applies exactly that plan, waits for ECS, and checks the
services, migrations, logs and pages. Database migrations run when the API starts.

To see what would change without changing anything:

```
scripts/deploy-dev.sh <api-tag> <web-tag> --plan-only
```

## Infrastructure

Each `terraform/<stack>` folder is applied on its own, with its own state in S3. The
script above covers only `app`. For the others, always save the plan and apply it:

```
cd terraform/<stack>
terraform plan -out=change.tfplan
terraform apply change.tfplan
rm change.tfplan
```

| Stack | What it holds | Changes |
|-------|---------------|---------|
| `bootstrap` | State bucket, vault KMS key and its policy, Object Lock audit bucket | Rarely; the key policy limits the key to the app roles |
| `network` | VPC, subnets, security groups | Rarely |
| `dns` | Route 53 zone for dev.myhousingplatform.org | Rarely |
| `cicd` | ECR image repositories | Rarely |
| `identity` | Cognito pools, app clients, login domains, SES | Sometimes |
| `vault` | Doc Vault buckets, GuardDuty malware scanning, scan queue, alerts | Sometimes |
| `audit` | Account CloudTrail into the audit bucket | Rarely |
| `app` | ALB, ECS services (api, web, vault-worker), RDS, roles, alarms | Every deploy |

## Doc Vault

Youth keep copies of their documents; they can share them with supporters and delete
them for good. The design is in "Doc Vault: Architecture" (v3, confidential, not in
this repo); code comments cite its sections (§).

- **Upload:** the browser posts straight to the quarantine bucket with a one-time link.
  GuardDuty scans it; the worker checks the type and size, rebuilds the file
  (`sanitize.py`: no metadata; PDFs are redrawn as page images), encrypts it with the
  youth's own key and stores it in the vault bucket.
- **Download:** always through the API, which checks access and decrypts on every
  request. No storage address reaches the browser.
- **Sharing:** a supporter makes a one-use code; the youth shares one document or a
  whole category. Stopping a share applies on the supporter's next request.
- **Delete:** the document's key is removed at once; the worker removes every stored
  version within 5 minutes.
- **Audit:** every upload, view, download, share, stop and delete is logged in a
  hash-chained table. The worker checks the chain hourly and seals each day's rows
  into the Object Lock audit bucket.
- **Who can read documents:** only the app's roles. Engineers can't, by key and bucket
  policy.
- **Alerts:** email via the `id-vault-dev-alerts` topic: malware, any access to vault
  documents by anyone but the app, changes to key or bucket protection, decrypt
  failures, refused-request bursts, a broken audit chain, a stopped worker, failed or
  overdue seals.

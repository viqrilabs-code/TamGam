# tamgam Low-Cost Deployment Guide (GCP)

This guide deploys a production-ready baseline with low fixed cost and acceptable performance.

## 1) Target architecture

- Frontend: Firebase Hosting
- API: Cloud Run (`min-instances=0`, `cpu=0.5`, `memory=512Mi`, `concurrency=40`)
- DB: Cloud SQL PostgreSQL shared-core starter tier
- Optional at launch:
  - Redis/Memorystore: disabled
  - Cloud Tasks: disabled (code falls back to FastAPI background tasks)

## 2) Prerequisites

- `gcloud`, `docker`, `firebase` CLI installed
- Billing enabled on GCP project
- Region: `asia-south1`

## 3) Create Artifact Registry

```bash
gcloud artifacts repositories create tamgam \
  --repository-format=docker \
  --location=asia-south1 \
  --description="tamgam images"
```

## 4) Create Cloud SQL (lowest-cost starter)

```bash
gcloud sql instances create tamgam-pg \
  --database-version=POSTGRES_16 \
  --cpu=1 \
  --memory=3840MiB \
  --region=asia-south1

gcloud sql databases create tamgam --instance=tamgam-pg
gcloud sql users create tamgam --instance=tamgam-pg --password='<strong-password>'
```

If shared-core PostgreSQL tiers are available in your project, pick the cheapest shared-core tier instead.

Then enable extension:

```bash
gcloud sql connect tamgam-pg --user=postgres
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

## 5) Set secrets and runtime variables

Use Secret Manager for secrets (`JWT_SECRET_KEY`, `DB_PASS`, API keys, webhooks), and env vars for non-sensitive config.

Minimum runtime vars:

- `APP_ENV=production`
- `AUTO_MIGRATE_ON_STARTUP=false`
- `DB_USER=tamgam`
- `DB_NAME=tamgam`
- `DB_CONNECTION_NAME=<project>:asia-south1:tamgam-pg`
- `DB_POOL_SIZE=2`
- `DB_MAX_OVERFLOW=2`
- `REDIS_URL=` (empty)
- `CLOUD_TASKS_ENABLED=false`

## 6) Deploy backend through Cloud Build

```bash
gcloud builds submit --config infra/cloudbuild.yaml \
  --substitutions=_CLOUDSQL_CONNECTION='<project>:asia-south1:tamgam-pg',_SET_ENV_VARS='APP_ENV=production,DB_USER=tamgam,DB_NAME=tamgam,DB_CONNECTION_NAME=<project>:asia-south1:tamgam-pg,DB_POOL_SIZE=2,DB_MAX_OVERFLOW=2,REDIS_URL=,CLOUD_TASKS_ENABLED=false',_SET_SECRETS='JWT_SECRET_KEY=jwt-secret:latest,DB_PASS=db-pass:latest'
```

## 7) Run migrations once per release

Use Cloud Run job or a one-off container execution to run:

```bash
alembic upgrade head
```

Keep `AUTO_MIGRATE_ON_STARTUP=false` to avoid startup failures and repeated migration checks on cold starts.

## 8) Deploy frontend

From `tamgam-frontend/`:

```bash
firebase deploy --only hosting
```

## 9) Cost/performance scale-up triggers

- Enable Cloud Tasks when transcript/note generation volume causes request timeouts.
- Add Redis only when you need durable pub/sub across instances or caching bottlenecks appear.
- Increase Cloud Run memory to `1Gi` only if OOM/restarts appear in logs.
- Increase `DB_POOL_SIZE` only after confirming active connection saturation.

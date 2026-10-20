# Time Tracker

Track time against projects with a live timer or manual entries, review a weekly timesheet, and see reports (with CSV export).

| Layer    | Tech |
|----------|------|
| Frontend | React 18 + TypeScript (Vite), hosted on S3 behind CloudFront |
| Auth     | Amazon Cognito Hosted UI (OAuth code flow with PKCE) |
| API      | API Gateway HTTP API with a Cognito JWT authorizer → Python 3.12 Lambda |
| Data     | DynamoDB (single table, on-demand, point-in-time recovery) |
| Infra    | AWS CDK (Python) |

```
frontend/   React app (src/auth.ts handles login, src/api.ts the API client)
backend/    Lambda code (app/handler.py routes + validation, app/repo.py DynamoDB) and tests
infra/      CDK app (stacks/time_tracker_stack.py)
deploy.sh   build frontend + cdk deploy
```

## Deploy

Requirements: Node 20+, Python 3.12+, AWS CDK CLI v2, and AWS credentials.

```bash
cd infra && cdk bootstrap   # once per account/region
cd .. && ./deploy.sh
```

The stack outputs `SiteUrl`; open it and sign up. CDK writes `config.json` (API URL, Cognito client and domain) into the site bucket, so the frontend build has no environment-specific values baked in.

Region defaults to `us-east-1`; set `CDK_DEFAULT_REGION` to change it.

The DynamoDB table and Cognito user pool are **retained** when the stack is destroyed so time data and accounts aren't lost by accident. Delete them by hand if you want everything gone.

## Local development

Backend tests (DynamoDB is mocked with moto):

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Frontend against a deployed stack:

```bash
cd frontend
cp config.example.json config.local.json   # fill in from the stack outputs
npm install && npm run dev                  # http://localhost:5173
```

`http://localhost:5173` is already an allowed Cognito callback and CORS origin.

## API

All routes require `Authorization: Bearer <Cognito access token>`. Times are UTC ISO-8601 strings like `2026-01-31T09:00:00Z`.

| Method & path | Body / query | Notes |
|---|---|---|
| `GET /projects` | | |
| `POST /projects` | `{name, color?}` | |
| `PATCH /projects/{id}` | `{name?, color?, archived?}` | |
| `DELETE /projects/{id}` | | 409 if the project has entries (archive instead) |
| `GET /entries` | `?from=&to=` | entries whose start falls in the range, newest first |
| `POST /entries` | `{projectId, description?, startedAt, endedAt}` | manual entry |
| `PATCH /entries/{id}` | any of the above | |
| `DELETE /entries/{id}` | | |
| `GET /timer` | | `{running: entry \| null}` |
| `POST /timer/start` | `{projectId, description?}` | stops any running timer first |
| `POST /timer/stop` | | |
| `GET /reports/summary` | `?from=&to=` | totals by project and by UTC day |

Data model: one table keyed by `PK = USER#<cognito sub>`, so every query is scoped to the signed-in user. Projects are `SK = PROJECT#<id>`, entries are `SK = ENTRY#<id>` (with a `byStart` local index on `startedAt` for range queries), and `SK = TIMER` points to the running entry.

# Railway Deployment Guide

This guide deploys the current Service Intelligence MVP to Railway as a single-instance FastAPI
service with a persistent SQLite database.

## Important security boundary

The current release does not provide authentication or role-based authorization. A Railway public
domain would therefore expose the application and its APIs to anyone who can reach the URL.

- Use dummy or de-identified data for an initial Railway smoke test.
- Do not upload real customer work orders or operational records to a public deployment until
  authentication and authorization have been implemented and tested.
- Treat the instructions below as an MVP test deployment, not a production-security approval.

## Recommended MVP architecture

- One Railway service running the FastAPI application.
- One Railway persistent volume mounted at `/data`.
- SQLite stored at `/data/service_intelligence.db`.
- One application replica only. SQLite on a mounted volume is not suitable for horizontal replicas
  or concurrent multi-instance writes.
- Railway-managed HTTPS on the generated public domain.
- `/health` configured as the deployment health-check path.

For a shared operational deployment, the planned target is authentication plus PostgreSQL rather
than scaling this SQLite arrangement.

## Prerequisites

1. A Railway account.
2. A GitHub repository containing this project.
3. An OpenAI API key with available API credit.
4. A test PDF containing no sensitive or identifiable operational data.

Do not commit `.env`, the SQLite database, work orders, or API keys to Git.

## 1. Put the project in GitHub

Create a private GitHub repository and push the contents of the `service-intelligence` directory.
The repository root should contain `pyproject.toml`, `README.md`, and the `backend` directory.

## 2. Create the Railway service

1. In Railway, select **New Project**.
2. Choose **Deploy from GitHub repo**.
3. Connect GitHub if requested and select the private repository.
4. Allow Railway to detect the Python project from `pyproject.toml`.
5. Set the build command to:

   ```text
   pip install .
   ```

6. Set the start command to:

   ```text
   uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
   ```

Railway supplies `PORT` at runtime. The application must listen on `0.0.0.0` and that port.

## 3. Attach persistent storage

1. Add a Railway volume to the application service.
2. Set its mount path to `/data`.
3. Keep the service at one replica.
4. Enable volume backups in Railway and define a retention policy suitable for the test.

Without the volume, the SQLite database may be lost when Railway replaces the deployment.

## 4. Add environment variables

Add these variables in the Railway service's **Variables** page:

| Variable | Value |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI API key; seal this variable |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `OPENAI_MAX_OUTPUT_TOKENS` | `16000` |
| `OPENAI_REASONING_EFFORT` | `low` |
| `SERVICE_INTELLIGENCE_DB_PATH` | `/data/service_intelligence.db` |

OCR variables and Tesseract are unnecessary for the current embedded-text PDF scope.

## 5. Configure the health check

In the service deployment settings, set the health-check path to:

```text
/health
```

The endpoint should return a successful response before Railway promotes the deployment.

## 6. Deploy and create a test domain

1. Deploy the service and inspect the Railway build and deploy logs.
2. Open **Settings > Networking** for the service.
3. Generate a Railway public domain.
4. Open `https://<your-domain>/health` and confirm the service is healthy.
5. Open `https://<your-domain>/` and confirm the application loads.

Remember that the generated domain is public. Use only dummy or de-identified data until access
control is implemented.

## 7. Post-deployment smoke test

Use a non-sensitive test record and verify all of the following:

1. `/health` responds successfully.
2. The home, machine register, review, holiday, calendar-profile, and report pages load.
3. A registered test machine survives a service restart.
4. One de-identified embedded-text PDF extracts and saves successfully.
5. The event can be reviewed and approved.
6. A custom-date report is generated and its PDF and Excel downloads work.
7. Redeploy the same commit and confirm the machine and event still exist. This proves the database
   is using the mounted volume rather than ephemeral storage.

## 8. Backup and restore check

Before treating the deployment as durable:

1. Create or confirm a Railway volume backup.
2. Record the database mount path and backup retention setting.
3. Perform a restore rehearsal with test data.
4. Confirm restored events, machine registrations, holidays, aliases, and audit history are present.

A backup that has never been restored is not yet a verified recovery method.

## Updating the deployment

Push a tested commit to the connected branch. Railway will build and deploy it automatically unless
automatic deployments have been disabled. Database migrations must remain backward compatible with
the persisted SQLite file. Expect a short interruption when a deployment remounts the volume.

## Troubleshooting

### Application does not start

- Confirm the start command uses `--host 0.0.0.0 --port $PORT`.
- Confirm Railway detected the repository root containing `pyproject.toml`.
- Review build logs for dependency-installation failures.

### Data disappears after a deploy

- Confirm the volume is attached to the application service at `/data`.
- Confirm `SERVICE_INTELLIGENCE_DB_PATH=/data/service_intelligence.db`.
- Confirm the service is running only one replica.

### Extraction returns an OpenAI error

- Confirm `OPENAI_API_KEY` is set in the Railway service, not only on the local computer.
- Confirm the API project has available credit.
- Confirm the configured model is `gpt-4o-mini`.

### Health check fails while the app appears to run

- Confirm the path is exactly `/health`.
- Confirm the server listens on Railway's `PORT` rather than a hard-coded local-only port.

## Production-readiness gate

Do not use the public deployment for real operational data until these controls are complete:

- user authentication;
- role-based correction and approval authorization;
- secure session handling and logout;
- secrets review;
- database backup and tested restore;
- access and audit-log review;
- an agreed data-retention policy;
- PostgreSQL migration when multi-user use or horizontal scaling is required.

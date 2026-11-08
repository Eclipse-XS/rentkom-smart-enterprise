# Grafana monitoring

This directory contains Grafana dashboards and the provisioning configuration used by the Docker Compose deployment.

## Dashboards

| File | Panels | Purpose |
| --- | ---: | --- |
| `dashboards/business_analytics.json` | 10 | Requests, statuses, property types, feedback, and activity trends |
| `dashboards/real_estate_monitoring.json` | 5 | Earlier real-estate and feedback overview retained alongside newer dashboards |
| `dashboards/technical_observability.json` | 11 | Notification backlog, delivery attempts, retries, failures, and audit events |
| **Total** | **26** | Three versioned dashboard definitions |

The exported dashboard files describe panel configuration. They do not establish that every panel currently receives live data from external providers.

## Provisioning

The active Docker volume mounts are defined in [`compose.yaml`](../../compose.yaml):

- `provisioning/dashboards/dashboards.yml` provisions dashboard files from `/var/lib/grafana/dashboards`.
- `provisioning/datasources/datasources.yml` provisions PostgreSQL (`lab5-postgres`) and the authenticated Business Core API datasource (`rentkom-core-api`).
- `provisioning/datasources.yml` is a second copy in the repository, but the current Compose definition mounts the nested `provisioning/datasources/` directory.

The Compose image is `grafana/grafana:11.3.0`, with PostgreSQL `postgres:16-alpine`. These versions describe the containerized demo and should not be confused with historical locally installed tools used during the coursework.

## Start and inspect

```bash
cp .env.example .env
docker compose --profile demo up -d --build
```

Grafana runs at `http://127.0.0.1:3000`. Its credentials are configured by `GRAFANA_ADMIN_USER` and `GRAFANA_ADMIN_PASSWORD` in `.env`. Demo credentials must not be reused for shared or public deployments.

See the [root README](../../README.md) for architecture and demo scope and [deployment guide](../../DEPLOYMENT.md) for setup details.

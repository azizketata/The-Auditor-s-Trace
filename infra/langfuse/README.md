# Langfuse (self-hosted)

LLM observability for this project. Runs entirely on localhost via Docker Compose.

## Start

```powershell
docker compose --env-file .env up -d      # from infra/langfuse
```

Docker Desktop must be running first. First boot pulls ~3 GB (postgres, clickhouse,
redis, minio, langfuse-web, langfuse-worker) and takes a few minutes to become healthy.

| Service        | URL                     |
| -------------- | ----------------------- |
| Langfuse UI    | http://localhost:3000   |
| MinIO console  | http://localhost:9091   |

Create an account at the UI on first visit (it becomes the instance owner), then make an
organization + project and copy the public/secret API keys from **Project Settings → API Keys**.

## Stop / reset

```powershell
docker compose down            # stop, keep data
docker compose down -v         # stop and DELETE all traces
```

## Port notes

Postgres binds `127.0.0.1:5432` and Redis `127.0.0.1:6379`. If you already run either
locally, remap the host side in `docker-compose.yml` before starting.

## Wiring an app to it

Set these in the *application's* environment (not this file):

```
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Langfuse also accepts plain OTLP, so an OpenTelemetry SDK using the GenAI semantic
conventions can export straight to it — no Langfuse SDK required:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64 of "pk-lf-...:sk-lf-...">
```

Emit spans named `chat <model>` / `execute_tool <name>` with `gen_ai.*` attributes
(`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`) and Langfuse renders them as traces, generations, and cost.

Spec: https://opentelemetry.io/docs/specs/semconv/gen-ai/

## Files

- `docker-compose.yml` — upstream Langfuse v4 compose file, unmodified.
- `.env` — generated secrets. **Gitignored, never commit.**
- `.env.example` — template for other machines.

# SummarAIzer CLI

SummarAIzer ships a Typer-based operational CLI for local development and container maintenance.

## Shorter invocation options

You can run the CLI in three ways:

```bash
# Recommended in the current dev container / bind-mount setup
python -m app.cli --help

# Explicit module path (still supported)
python -m app.cli.main --help
```

Inside Docker, that becomes:

```bash
docker exec summaraizer python -m app.cli --help
```

## API keys

The public command name is `api-keys`.

```bash
# Show help
docker exec summaraizer python -m app.cli api-keys --help

# List all users
docker exec summaraizer python -m app.cli api-keys users list

# List active keys for one user
docker exec summaraizer python -m app.cli api-keys keys list --username api_user

# Create additional key without revoking existing keys
docker exec summaraizer python -m app.cli api-keys keys create --username api_user --name prod-2026-05

# Rotate key safely and keep old key active during migration
docker exec summaraizer python -m app.cli api-keys keys rotate --username api_user --name prod-2026-05

# Rotate key and revoke old active keys
docker exec summaraizer python -m app.cli api-keys keys rotate --username api_user --name prod-2026-06 --revoke-old

# Revoke one specific key
docker exec summaraizer python -m app.cli api-keys keys revoke --key-id 123
```

## Seed development data

```bash
docker exec summaraizer python -m app.cli seed-dev-data
```

## Workflow task recovery

Use these commands to inspect running executions, detect stale DB records, and repair executions orphaned by worker restarts or crashes.

```bash
# List active workflow executions and flag stale ones older than 30 minutes
docker exec summaraizer python -m app.cli workflow-tasks list --older-than-minutes 30

# Show only stale executions
docker exec summaraizer python -m app.cli workflow-tasks list --stale-only --older-than-minutes 30

# Kill one stale execution record and revoke the Celery task if still present
docker exec summaraizer python -m app.cli workflow-tasks kill --execution-id 123

# Restart one stale execution by marking the old record failed and queueing a new execution
docker exec summaraizer python -m app.cli workflow-tasks restart --execution-id 123

# Bulk-restart all stale executions older than 60 minutes
docker exec summaraizer python -m app.cli workflow-tasks restart-stale --older-than-minutes 60 --yes
```

The stale detector compares `workflow_executions` rows in `queued` or `running` state against current Celery worker visibility and the task backend state.
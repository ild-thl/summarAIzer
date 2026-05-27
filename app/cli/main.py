"""Unified CLI entrypoint for SummarAIzer operational tooling."""

from __future__ import annotations

import typer

from app.cli.api_keys import app as api_keys_app
from app.cli.seed_dev_data import app as seed_dev_data_app
from app.cli.workflow_tasks import app as workflow_tasks_app

app = typer.Typer(help="SummarAIzer CLI", no_args_is_help=True)
app.add_typer(api_keys_app, name="api-keys")
app.add_typer(seed_dev_data_app, name="seed-dev-data")
app.add_typer(workflow_tasks_app, name="workflow-tasks")


def run() -> None:
    """Execute the top-level CLI."""
    app(prog_name="summaraizer")


if __name__ == "__main__":
    run()

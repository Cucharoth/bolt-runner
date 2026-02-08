import typer
from pathlib import Path
from typing import Optional
from src.service.workflow_orchestrator import WorkflowOrchestrator
from src.utils.logger import logger

app = typer.Typer()

@app.command()
def run(
    config: Optional[Path] = typer.Option(
        None, 
        "--config", "-c", 
        help="Path to JSON configuration file. Defaults to workflows.json if present."
    )
):
    """
    Run the configured workflows and services defined in configuration file.
    """
    try:
        orchestrator = WorkflowOrchestrator()
        orchestrator.run(config_path=config)
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        raise typer.Exit(code=1)



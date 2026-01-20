import typer
from src.service.workflow_orchestrator import WorkflowOrchestrator
from src.utils.logger import logger

app = typer.Typer()

@app.command()
def run():
    """
    Run the configured workflows and services defined in environment variables.
    """
    try:
        orchestrator = WorkflowOrchestrator()
        orchestrator.run()
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        raise typer.Exit(code=1)



import typer
from dotenv import load_dotenv
from src.command import workflow

# Load environment variables from .env file
load_dotenv()

app = typer.Typer()

@app.callback()
def main(
    verbose: bool = typer.Option(
        False, 
        "--verbose", "-v", 
        help="Enable verbose logging (DEBUG level)."
    )
):
    from src.utils.logger import configure_logging
    configure_logging(verbose)

app.add_typer(workflow.app, name="workflow", help="Manage GitHub Action Workflows")

if __name__ == "__main__":
    app()


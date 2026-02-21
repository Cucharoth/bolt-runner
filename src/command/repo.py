import typer
import json
from pathlib import Path
from src.service.repo_onboarder_service import RepoOnboarderService
from src.utils.logger import logger

app = typer.Typer()

DEFAULT_ORG = "cucharoth-eco"

@app.command()
def onboard(
    file: Path = typer.Option(
        Path("repos.json"), 
        "--file", "-f", 
        help="Path to JSON file containing list of repositories to onboard."
    ),
    org: str = typer.Option(
        DEFAULT_ORG, 
        "--org", "-o", 
        help=f"Target GitHub Organization. Defaults to '{DEFAULT_ORG}'."
    )
):
    """
    Onboard public repositories into your organization/account.
    Reads a list of repos from JSON file and creates/mirrors them if they don't exist.
    """
    if not file.exists():
        logger.error(f"Repository list file not found: {file}")
        raise typer.Exit(code=1)

    try:
        with open(file, 'r', encoding='utf-8') as f:
            repo_list = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON format in {file}: {e}")
        raise typer.Exit(code=1)

    if not isinstance(repo_list, list):
        logger.error("JSON file must contain a list of repository objects.")
        raise typer.Exit(code=1)

    try:
        service = RepoOnboarderService(target_org=org)
        service.onboard_repos(repo_list)
    except Exception as e:
        logger.error(f"Onboarding failed: {e}")
        raise typer.Exit(code=1)

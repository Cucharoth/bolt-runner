# Bolt Runner

Automated GitHub Actions workflow runner/orchestrator with energy profiling.

## Features

- **Multi-repo Orchestration**: Trigger multiple workflows across different repositories as defined in configuration.
- **Energy Logging**: Uses `ec-toolkit` to log CPU and Energy metrics locally while the workflow runs.
- **Log Retrieval**: Automatically waits for workflow completion and downloads the execution logs.

## Setup

1.  **Install uv** (if not installed).
2.  **Install dependencies**:
    ```bash
    uv sync
    ```
3.  **Configure Environment**:
    Copy `.env.example` to `.env`:

    ```bash
    cp .env.example .env
    ```

    Edit `.env` with your settings:
    - `GITHUB_TOKEN`: Your GitHub Personal Access Token (scope: `repo`).
    - `WORKFLOW_CONFIG`: A JSON string (must be single line) defining the workflows.
        ```json
        [
            {
                "owner": "org",
                "repo": "repo-name",
                "workflow": "main.yml",
                "ref": "main"
            }
        ]
        ```

## Usage

Run the orchestrator:

```bash
uv run main.py workflow run
```

## Output

Artifacts are saved in the `logs/` directory, organized by timestamp:

- `logs/<timestamp>/cpu_total_interval.csv`: CPU usage metrics.
- `logs/<timestamp>/<repo>_<run_id>.zip`: Downloaded GitHub Action logs.

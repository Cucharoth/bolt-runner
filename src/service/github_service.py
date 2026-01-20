import httpx
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from src.utils.logger import logger

class GitHubService:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN is not set in environment or provided.")
        
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    def trigger_workflow(self, owner: str, repo: str, workflow_id: str, ref: str, inputs: Dict[str, Any] = None) -> bool:
        """
        Triggers a GitHub Actions workflow dispatch event.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        
        payload = {
            "ref": ref
        }
        if inputs:
            payload["inputs"] = inputs

        with httpx.Client() as client:
            response = client.post(url, headers=self.headers, json=payload)
            
            if response.status_code == 204:
                return True
            else:
                raise Exception(f"Failed to trigger workflow: {response.status_code} - {response.text}")

    def wait_for_run_start(self, owner: str, repo: str, workflow_id: str, ref: str, trigger_time: datetime, timeout: int = 120) -> Optional[Dict[str, Any]]:
        """
        Polls for the workflow run to start.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        params = {"branch": ref, "event": "workflow_dispatch", "per_page": 5}
        
        # Ensure trigger_time is timezone-aware (UTC)
        if trigger_time.tzinfo is None:
            trigger_time = trigger_time.replace(tzinfo=timezone.utc)
        
        start_wait = time.time()
        attempt = 0
        while time.time() - start_wait < timeout:
            attempt += 1
            if attempt % 10 == 0:
                logger.info(f"Waiting for run start... Attempt #{attempt} ({(time.time() - start_wait):.0f}s elapsed)")

            try:
                with httpx.Client() as client:
                    response = client.get(url, headers=self.headers, params=params)
                    response.raise_for_status()
                    runs = response.json().get("workflow_runs", [])
                    
                    for run in runs:
                        created_at_str = run.get("created_at")
                        if created_at_str:
                            created_at = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                            # Allow a small buffer for clock skew, or strictly greater
                            if created_at >= trigger_time - timedelta(seconds=10):
                                return run
            except Exception as e:
                logger.warning(f"Error checking for run start: {e}")
            
            time.sleep(5)
            
        return None

    def wait_for_completion(self, owner: str, repo: str, run_id: int, timeout: int = 1200) -> Optional[Dict[str, Any]]:
        """
        Polls for the workflow run to complete.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        
        attempt = 0
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            attempt += 1
            if attempt % 6 == 0:
                logger.info(f"Waiting for completion... Attempt #{attempt} ({(time.time() - start_wait):.0f}s elapsed)")

            try:
                with httpx.Client() as client:
                    response = client.get(url, headers=self.headers)
                    if response.status_code == 200:
                        run = response.json()
                        status = run.get("status")
                        if status in ["completed", "success", "failure", "cancelled", "timed_out", "skipped"]:
                            return run
            except Exception:
                pass
            
            time.sleep(10)
            
        return None

    def download_logs(self, owner: str, repo: str, run_id: int, destination_dir: str):
        """
        Downloads the logs for a specific run.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
        
        with httpx.Client(follow_redirects=True) as client:
            response = client.get(url, headers=self.headers)
            if response.status_code == 200:
                file_path = os.path.join(destination_dir, f"{repo}_{run_id}.zip")
                with open(file_path, "wb") as f:
                    f.write(response.content)
                return file_path
            else:
                raise Exception(f"Failed to download logs: {response.status_code}")


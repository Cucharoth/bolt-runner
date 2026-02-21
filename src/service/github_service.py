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
        # Increase transport timeout
        self.timeout = 60.0
        
        # Configure a transport with retries for connection issues (connect, read, write timeouts)
        # Note: This handles network layer retries, but not HTTP 502/503 status codes.
        self.transport = httpx.HTTPTransport(retries=3)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Internal helper to make HTTP requests with logic for 5xx retries.
        """
        max_retries = 10
        wait_seconds = 30
        
        for attempt in range(1, max_retries + 1):
            try:
                # Re-create client to ensure fresh connection pool on long retries
                with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=True) as client:
                    response = client.request(method, url, headers=self.headers, **kwargs)
                    
                    # If success or client error (4xx), return immediately
                    if response.status_code < 500:
                        return response
                    
                    # Server Error (5xx)
                    logger.warning(f"GitHub API Error {response.status_code}. Retry {attempt}/{max_retries} in {wait_seconds}s...")
                    time.sleep(wait_seconds)
            
            except httpx.RequestError as e:
                logger.warning(f"Network error: {e}. Retry {attempt}/{max_retries} in {wait_seconds}s...")
                time.sleep(wait_seconds)
        
        # Final attempt
        with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=True) as client:
             return client.request(method, url, headers=self.headers, **kwargs)

    def get_current_user(self) -> Dict[str, Any]:
        """
        Get the authenticated user's profile.
        """
        url = f"{self.base_url}/user"
        response = self._request("GET", url)
        if response.status_code == 200:
            return response.json()
        raise Exception(f"Failed to get current user: {response.status_code}")

    def check_repo_exists(self, owner: str, repo: str) -> bool:
        """
        Check if a repository exists and is accessible.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}"
        try:
            response = self._request("GET", url)
            return response.status_code == 200
        except Exception:
            return False

    def get_repo(self, owner: str, repo: str) -> Optional[Dict[str, Any]]:
        """
        Get repository details if it exists.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}"
        try:
            response = self._request("GET", url)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.warning(f"Error getting repo info {owner}/{repo}: {e}")
            return None

    def create_repo(self, name: str, org: Optional[str] = None, description: str = "", private: bool = False) -> Dict[str, Any]:
        """
        Create a new repository. If org is provided, creates in that organization.
        Otherwise creates under the authenticated user.
        """
        if org:
            url = f"{self.base_url}/orgs/{org}/repos"
        else:
            url = f"{self.base_url}/user/repos"
        
        payload = {
            "name": name,
            "description": description,
            "private": private,
            "has_issues": True,
            "has_projects": True,
            "has_wiki": True
        }

        response = self._request("POST", url, json=payload)
        
        if response.status_code == 201:
            return response.json()
        else:
            raise Exception(f"Failed to create repository: {response.status_code} - {response.text}")

    def update_repo(self, owner: str, repo: str, description: Optional[str] = None) -> bool:
        """
        Updates repository details.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}"
        payload = {}
        if description is not None:
            payload["description"] = description
            
        if not payload:
            return True
        
        response = self._request("PATCH", url, json=payload)
        return response.status_code == 200

    def trigger_workflow(self, owner: str, repo: str, workflow_id: str, ref: str, inputs: Dict[str, Any] = None) -> bool:
        """
        Triggers a GitHub Actions workflow dispatch event.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
        
        payload = {"ref": ref}
        if inputs:
            payload["inputs"] = inputs

        response = self._request("POST", url, json=payload)
        
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
                with httpx.Client(timeout=self.timeout) as client:
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
                with httpx.Client(timeout=self.timeout) as client:
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
        
        # Use _request to handle 502/5xx errors during download
        response = self._request("GET", url)
        
        if response.status_code == 200:
            file_path = os.path.join(destination_dir, f"{repo}_{run_id}.zip")
            with open(file_path, "wb") as f:
                f.write(response.content)
            return file_path
        else:
            raise Exception(f"Failed to download logs: {response.status_code}")


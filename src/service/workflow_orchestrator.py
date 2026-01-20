
from datetime import datetime, timezone
import os
import json
from typing import List, Dict, Any
from src.service.github_service import GitHubService
from src.service.energy_logger_service import EnergyLoggerService
from src.utils.logger import logger

class WorkflowOrchestrator:
    def __init__(self):
        try:
            self.gh_service = GitHubService()
            self.energy_logger = EnergyLoggerService()
        except ValueError as e:
            logger.critical(f"Service initialization failed: {e}")
            raise

    def run(self):
        """
        Orchestrates the execution of workflows defined in environment variables.
        """
        logger.info("Starting Bolt Runner execution...")

        workflow_config_str = os.getenv("WORKFLOW_CONFIG")
        if not workflow_config_str:
            logger.warning("No WORKFLOW_CONFIG found in environment variables.")
            return

        try:
            workflows = json.loads(workflow_config_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WORKFLOW_CONFIG JSON: {e}")
            raise

        logger.info(f"Found {len(workflows)} workflows to process.")
        
        # Start Energy Logger
        self.energy_logger.start()
        try:
            self._process_workflows(workflows)
        finally:
            # Stop Energy Logger even if workflows fail
            self.energy_logger.stop()


    def _process_workflows(self, workflows: List[Dict[str, Any]]):
        for item in workflows:
            owner = item.get("owner")
            repo = item.get("repo")
            workflow_id = item.get("workflow")
            ref = item.get("ref", "main")
            inputs = item.get("inputs", {})

            if not all([owner, repo, workflow_id]):
                logger.error(f"Invalid workflow configuration item: {item}. Skipping.")
                continue

            logger.info(f"Triggering workflow '{workflow_id}' on {owner}/{repo}@{ref}...")
            
            trigger_time = datetime.now(timezone.utc)
            
            try:
                self.gh_service.trigger_workflow(owner, repo, workflow_id, ref, inputs)
                logger.info(f"Successfully triggered {workflow_id}. Waiting for run to start...")
                
                # Wait for the run to appear
                run = self.gh_service.wait_for_run_start(owner, repo, workflow_id, ref, trigger_time)
                
                if run:
                    run_id = run["id"]
                    run_url = run["html_url"]
                    logger.info(f"Workflow run started: {run_url} (ID: {run_id})")
                    logger.info("Waiting for execution to complete...")
                    
                    completed_run = self.gh_service.wait_for_completion(owner, repo, run_id)
                    if completed_run:
                        conclusion = completed_run.get("conclusion")
                        logger.info(f"Workflow completed with status: {conclusion}")
                        
                        logger.info("Downloading logs...")
                        # Download logs to the same directory as energy logs
                        log_path = self.gh_service.download_logs(owner, repo, run_id, str(self.energy_logger.run_dir))
                        logger.info(f"Logs downloaded to: {log_path}")
                    else:
                        logger.error("Timed out waiting for workflow completion.")
                else:
                    logger.error("Timed out waiting for workflow run to start (check if 'workflow_dispatch' is enabled).")
                    
            except Exception as e:
                logger.error(f"Failed to process workflow {workflow_id} on {repo}: {e}")

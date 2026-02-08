
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import List, Dict, Any
from src.service.github_service import GitHubService
from src.service.energy_logger_service import EnergyLoggerService
from src.utils.logger import logger
from ec_toolkit.utils.freq import set_freq_or_default, restore_default, read_cpu_freq_per_core

class WorkflowOrchestrator:
    def __init__(self):
        try:
            self.gh_service = GitHubService()
            # Energy logger is now instantiated per workflow run
        except ValueError as e:
            logger.critical(f"Service initialization failed: {e}")
            raise

    def run(self, config_path: Path = None):
        """
        Orchestrates the execution of workflows.
        Priority:
        1. config_path argument
        2. workflows.json in CWD
        """
        logger.info("Starting Bolt Runner execution...")
        
        workflows = []

        # 1. Try explicit config path
        if config_path:
            if config_path.exists():
                logger.info(f"Loading configuration from {config_path}")
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        workflows = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to load config file: {e}")
                    raise
            else:
                logger.error(f"Config file not found: {config_path}")
                raise FileNotFoundError(f"Config file {config_path} does not exist.")
        
        # 2. Try default workflows.json
        elif Path("workflows.json").exists():
            logger.info("Loading configuration from workflows.json")
            try:
                with open("workflows.json", 'r', encoding='utf-8') as f:
                    workflows = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load workflows.json: {e}")
                raise

        # 3. No config found
        else:
            logger.error("No configuration file found.")
            logger.info("Please provide a config file with --config or ensure 'workflows.json' exists in the current directory.")
            return

        if not workflows:
            logger.warning("No workflows found to process.")
            return

        logger.info(f"Found {len(workflows)} workflows to process.")
        
        self._process_workflows(workflows)

    def _process_workflows(self, workflows: List[Dict[str, Any]]):
        # Create base date directory
        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base_log_dir = Path("logs") / timestamp_str
        
        if not base_log_dir.exists():
            base_log_dir.mkdir(parents=True, exist_ok=True)

        for i, item in enumerate(workflows):
            owner = item.get("owner")
            repo = item.get("repo")
            workflow_id = item.get("workflow")
            ref = item.get("ref", "main")
            inputs = item.get("inputs", {})

            if not all([owner, repo, workflow_id]):
                logger.error(f"Invalid workflow configuration item: {item}. Skipping.")
                continue

            # Extract CPU config early to include in folder name
            cpu_config = item.get("cpu_config")
            freq_suffix = ""
            if cpu_config and cpu_config.get("enabled", False):
                val = cpu_config.get("value")
                if val:
                    freq_suffix = f"_{val}"

            # Create specific directory for this workflow run
            # underlying folder: {repo}_{workflow}_{index}_{freq} to ensure uniqueness if multiple same workflows
            safe_workflow_name = workflow_id.replace(".yml", "").replace(".yaml", "")
            run_dir_name = f"{repo}_{safe_workflow_name}_{i+1}{freq_suffix}"
            workflow_log_dir = base_log_dir / run_dir_name
            
            logger.info(f"Processing workflow {i+1}/{len(workflows)}: {workflow_id} (Log dir: {workflow_log_dir})")

            # Initialize and start Energy Logger for this specific workflow
            energy_logger = EnergyLoggerService(str(workflow_log_dir))
            
            # Save description if present
            description = item.get("description")
            if description:
                try:
                    desc_path = workflow_log_dir / "description.txt"
                    with open(desc_path, "w", encoding="utf-8") as f:
                        f.write(description)
                except Exception as e:
                    logger.warning(f"Failed to save description: {e}")
            
            # Application of CPU freq configuration if present
            if cpu_config and cpu_config.get("enabled", False):
                try:
                    target_val = cpu_config.get("value")
                    if target_val:
                        logger.info(f"Setting CPU config: {target_val}")
                        set_freq_or_default(target_val)
                        
                        # Verify the frequency change
                        current_freqs = read_cpu_freq_per_core()
                        logger.debug(f"Current CPU frequencies per core: {current_freqs}")
                except Exception as e:
                    logger.warning(f"Failed to set CPU frequency, this is not be a Linux system or there not enough permissions: {e}")
            else:
                logger.info("No CPU configuration provided or disabled; using default CPU settings.")

            energy_logger.start()

            try:
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
                        
                        start_time = datetime.now()
                        completed_run = self.gh_service.wait_for_completion(owner, repo, run_id)
                        if completed_run:
                            end_time = datetime.now()
                            duration = end_time - start_time
                            minutes, seconds = divmod(duration.total_seconds(), 60)
                            
                            conclusion = completed_run.get("conclusion")
                            logger.info(f"Workflow completed with status: {conclusion}")
                            logger.info(f"Workflow execution duration: {int(minutes)}m {int(seconds)}s")
                            
                            # Save completion metadata
                            try:
                                metadata_path = workflow_log_dir / "run_metadata.json"
                                with open(metadata_path, 'w', encoding='utf-8') as f:
                                    json.dump(completed_run, f, indent=2)
                                logger.info(f"Run metadata saved to: {metadata_path}")
                            except Exception as e:
                                logger.error(f"Failed to save run metadata: {e}")

                            logger.info("Downloading logs...")
                            # Download logs to the same directory as energy logs
                            log_path = self.gh_service.download_logs(owner, repo, run_id, str(workflow_log_dir))
                            logger.info(f"Logs downloaded to: {log_path}")
                        else:
                            logger.error("Timed out waiting for workflow completion.")
                    else:
                        logger.error("Timed out waiting for workflow run to start (check if 'workflow_dispatch' is enabled).")
                        
                except Exception as e:
                    logger.error(f"Failed to process workflow {workflow_id} on {repo}: {e}")
            
            finally:
                # Stop energy logger for this workflow
                energy_logger.stop()
                
                # Restore default CPU settings if they were modified
                if cpu_config and cpu_config.get("enabled", False):
                    try:
                        logger.info("Restoring default CPU frequency...")
                        restore_default()
                        
                        # Verify the frequency restoration
                        current_freqs = read_cpu_freq_per_core()
                        logger.debug(f"Current CPU frequencies per core after restore: {current_freqs}")
                    except Exception as e:
                        logger.warning(f"Failed to restore default CPU frequency: {e}")
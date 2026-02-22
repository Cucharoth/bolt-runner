
from datetime import datetime, timezone
import json
import time
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
        
        try:
            self._process_workflows(workflows)
        except KeyboardInterrupt:
            logger.warning("Execution interrupted by user.")
        except Exception as e:
            logger.error(f"Unexpected error during orchestration: {e}")

    def _process_workflows(self, workflows: List[Dict[str, Any]]):
        # Create base date directory
        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base_log_dir = Path("logs") / timestamp_str
        
        if not base_log_dir.exists():
            base_log_dir.mkdir(parents=True, exist_ok=True)

        total_runs = 0
        for item in workflows:
            if not item.get("enabled", True):
                continue
            if "cpu_configs" in item and isinstance(item["cpu_configs"], list):
                total_runs += len([c for c in item["cpu_configs"] if c.get("enabled", True)])
            else:
                total_runs += 1

        current_run_index = 0

        for i, item in enumerate(workflows):
            # Check for enabled status: defaults to True if missing
            if not item.get("enabled", True):
                logger.info(f"Skipping disabled workflow item {i+1} ({item.get('repo')}, {item.get('description', 'No description')})")
                continue

            owner = item.get("owner")
            repo = item.get("repo")
            workflow_id = item.get("workflow")
            ref = item.get("ref", "main")
            base_inputs = item.get("inputs", {})
            base_description = item.get("description", "")

            if not all([owner, repo, workflow_id]):
                logger.error(f"Invalid workflow configuration item: {item}. Skipping.")
                continue

            # normalize configs
            configs_to_run = []
            if "cpu_configs" in item and isinstance(item["cpu_configs"], list):
                # New format: list of CPU configs
                for cfg in item["cpu_configs"]:
                    if cfg.get("enabled", True):
                        # Merge description: Config description > Base description
                        cfg_copy = cfg.copy()
                        if "description" not in cfg_copy:
                             cfg_copy["description"] = base_description
                        configs_to_run.append(cfg_copy)
            else:
                # Old format: single cpu_config object in item
                # cpu_config might be None if not provided
                single_config = item.get("cpu_config", {})
                if single_config.get("enabled", False):
                     single_config_copy = single_config.copy()
                     single_config_copy["description"] = base_description
                     configs_to_run.append(single_config_copy)
                elif not single_config:
                     # No cpu_config provided at all, just run once with default inputs
                     configs_to_run.append({"description": base_description, "enabled": True})
            
            if not configs_to_run:
                 logger.info(f"No enabled configurations for {repo}/{workflow_id}. Skipping.")
                 continue

            for config in configs_to_run:
                current_run_index += 1
                self._run_single_workflow(
                    base_log_dir, 
                    current_run_index, 
                    total_runs, 
                    owner, 
                    repo, 
                    workflow_id, 
                    ref, 
                    base_inputs, 
                    config
                )
                
                # Add a small buffer between runs
                if current_run_index < total_runs:
                    logger.info("Cooling down for 10 seconds before next workflow...")
                    time.sleep(10)

    def _run_single_workflow(self, base_log_dir: Path, run_index: int, total_runs: int, owner: str, repo: str, workflow_id: str, ref: str, inputs: Dict[str, Any], cpu_config: Dict[str, Any]):
        
        # Extract values
        freq_value = cpu_config.get("value")
        description = cpu_config.get("description")
        
        # Directory naming
        safe_workflow_name = workflow_id.replace(".yml", "").replace(".yaml", "")
        freq_suffix = f"_{freq_value}" if freq_value else ""
        run_dir_name = f"{repo}_{safe_workflow_name}_{run_index}{freq_suffix}"
        workflow_log_dir = base_log_dir / run_dir_name
        
        logger.info(f"Processing run {run_index}/{total_runs}: {workflow_id} (Log dir: {workflow_log_dir})")

        # Initialize and start Energy Logger
        energy_logger = EnergyLoggerService(str(workflow_log_dir))
        
        # Save description
        if description:
            try:
                if not workflow_log_dir.exists():
                    workflow_log_dir.mkdir(parents=True, exist_ok=True)
                desc_path = workflow_log_dir / "description.txt"
                with open(desc_path, "w", encoding="utf-8") as f:
                    f.write(description)
            except Exception as e:
                logger.warning(f"Failed to save description: {e}")
        
        # Apply CPU Config
        cpu_modified = False
        if freq_value:
            try:
                logger.info(f"Setting CPU config: {freq_value}")
                set_freq_or_default(freq_value)
                cpu_modified = True
                
                # Verify
                current_freqs = read_cpu_freq_per_core()
                logger.debug(f"Current CPU frequencies per core: {current_freqs}")
            except Exception as e:
                logger.warning(f"Failed to set CPU frequency: {e}")
        else:
            logger.info("No CPU frequency configured; using default settings.")

        energy_logger.start()

        try:
            logger.info(f"Triggering workflow '{workflow_id}' on {owner}/{repo}@{ref}...")
            
            trigger_time = datetime.now(timezone.utc)
            
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
                    # Download logs
                    log_path = self.gh_service.download_logs(owner, repo, run_id, str(workflow_log_dir))
                    logger.info(f"Logs downloaded to: {log_path}")
                else:
                    logger.error("Timed out waiting for workflow completion.")
            else:
                logger.error("Timed out waiting for workflow run to start (check if 'workflow_dispatch' is enabled).")
                
        except Exception as e:
            logger.error(f"Failed to process workflow {workflow_id} on {repo}: {e}")
        
        finally:
            # Stop energy logger
            energy_logger.stop()
            
            # Restore default CPU settings
            if cpu_modified:
                try:
                    logger.info("Restoring default CPU frequency...")
                    restore_default()
                    
                    # Verify
                    current_freqs = read_cpu_freq_per_core()
                    logger.debug(f"Current CPU frequencies per core after restore: {current_freqs}")
                except Exception as e:
                    logger.warning(f"Failed to restore default CPU frequency: {e}")
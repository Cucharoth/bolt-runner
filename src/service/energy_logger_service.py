from pathlib import Path
from typing import Dict, Any
from datetime import datetime
from src.utils.logger import logger
from ec_toolkit.logger.manager import LoggerManager

class EnergyLoggerService:
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.manager = None
        self._ensure_log_dir()
        # Create a unique run ID for this session
        self.run_id = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
        self.run_dir = self.log_dir / self.run_id
        if not self.run_dir.exists():
            self.run_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_log_dir(self):
        if not self.log_dir.exists():
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_default_config(self) -> Dict[str, Any]:
        import platform
        
        is_windows = platform.system() == "Windows"
        
        config = {
            "interval": 1,
            "loggers": {
                "execution_time": [{"enabled": True, "mode": "edge"}],
                "cpu_total": [{"enabled": True, "mode": "interval"}],
            }
        }
        
        # Only enable RAPL if not on Windows (it relies on /sys/class/powercap on Linux)
        if not is_windows:
            config["loggers"]["rapl"] = [{"enabled": True, "mode": "interval"}]
        else:
            logger.info("Windows detected: RAPL logging disabled (Linux only feature).")
            
        return config

    def start(self):
        try:
            logger.info(f"Initializing Energy Logger (Run ID: {self.run_id})...")
            config = self._get_default_config()
            
            # Use the unique run directory instead of the base log directory
            self.manager = LoggerManager.from_config(config, self.run_dir)
            self.manager.start_all()
            logger.info("Energy Logger started.")
            
        except ImportError as e:
            logger.warning(f"ec-toolkit dependency missing: {e}. Energy logging skipped.")
        except Exception as e:
            logger.error(f"Failed to start Energy Logger: {e}")
            self.manager = None

    def stop(self):
        if self.manager:
            try:
                logger.info("Stopping Energy Logger...")
                self.manager.stop_all()
                logger.info(f"Energy Logger stopped. Logs saved to {self.run_dir.absolute()}")
            except Exception as e:
                logger.error(f"Error stopping Energy Logger: {e}")
            finally:
                self.manager = None

from pathlib import Path
from typing import Dict, Any
from src.utils.logger import logger
from ec_toolkit.logger.manager import LoggerManager

class EnergyLoggerService:
    def __init__(self, output_dir: str):
        self.run_dir = Path(output_dir)
        self.manager = None
        self._ensure_log_dir()

    def _ensure_log_dir(self):
        if not self.run_dir.exists():
            self.run_dir.mkdir(parents=True, exist_ok=True)

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
            logger.info(f"Initializing Energy Logger at {self.run_dir}...")
            config = self._get_default_config()
            
            # Use the unique run directory directly
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

import logging
from rich.logging import RichHandler

class Logger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        # Configure the root logger
        logging.basicConfig(
            level="INFO",
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True, show_path=False)]
        )
        
        # Suppress HTTPX logging
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        
        self.logger = logging.getLogger("bolt-runner")

    def get_logger(self):
        return self.logger

def configure_logging(verbose: bool = False):
    """
    Configure logging level.
    :param verbose: If True, set level to DEBUG, else INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger().setLevel(level)
    # Ensure httpx/httpcore remain quiet unless extremely verbose (optional, keeping them warning for now to reduce noise)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING) 

# Singleton instance
logger = Logger().get_logger()

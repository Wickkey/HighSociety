import logging
import threading
import traceback
from enum import Enum
from highsociety.code.common.logger_module.logger.concurrent_log_handler import ConcurrentLogHandler
import os


class LogType(Enum):
    GENERIC = 'GENERIC'
    SECURITY = 'SECURITY'


class LoggingManager:
    """
        This class handles the logging for the game. It's a singleton class. Not threadsafe.
    """
    _instance = None 
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with  cls._lock:
                if cls._instance is None:
                    # another thread could have created the instance before we acquired the lock. So check that the instance is still nonexistent.
                    cls._instance = super().__new__(cls)
                    cls._initialize(*args, **kwargs)
        return cls._instance

    @classmethod
    def _initialize(cls, config: dict[str, any]):
        base_path = config.get("base_path").get("abs_root_dir")
        log_dir = config.get("logging").get("log_dir")
        generic_log_file = config.get("logging").get("generic_log_file")
        security_log_file = config.get("logging").get("security_log_file")
        generic_log_path = os.path.join(base_path, log_dir, generic_log_file)
        security_log_path = os.path.join(base_path, log_dir, security_log_file)
        log_format = config.get("log_format", "[%(asctime)s] %(levelname)-8s %(filename)s:%(lineno)-20d %(message)s")
        formatter = logging.Formatter(log_format)

        max_nbytes = config.get("logging").get("max_nbytes", 10000000)
        backup_count = config.get("logging").get("backup_count", 10)
        log_level = config.get("logging").get("log_level", "INFO")

        generic_log_handler = ConcurrentLogHandler(path=generic_log_path,
                                                   max_bytes=max_nbytes,
                                                   backup_count=backup_count)
        generic_log_handler.setFormatter(formatter)
        cls._generic_logger = logging.getLogger('generic')
        cls._generic_logger.addHandler(generic_log_handler)

        security_log_handler = ConcurrentLogHandler(path=security_log_path,
                                                    max_bytes=max_nbytes,
                                                    backup_count=backup_count)
        security_log_handler.setFormatter(formatter)
        cls._security_logger = logging.getLogger('security')
        cls._security_logger.addHandler(security_log_handler)

        LoggingManager.set_level(log_level)
        LoggingManager.info("Logger successfully initialized")
    
    def get_instance(cls):
        return cls._instance

    @classmethod
    def _ensure_initialized(cls):
        """
        Lazily initializes the singleton with the default configuration if no
        caller has explicitly constructed a LoggingManager yet. This lets any
        module log safely regardless of import/startup order.
        """
        if cls._instance is None:
            from highsociety.code.common.utils.utility import get_all_configurations
            cls(get_all_configurations())

    @classmethod
    def log(cls, level, module, msg, log_type: LogType = LogType.GENERIC, *args, **kwargs):
        """
            Function responsible for logging.

            This function takes level, module, msg as arguments for logging in the pre-defined constant format.

            Parameters:
                level       - type of log (DEBUG, INFO, ERROR, etc.)
                module      - module from which the log was called.
                msg         - contents of the log message.
                *args       -
                **kwargs    -
        """
        cls._ensure_initialized()
        args = args or ()
        kwargs = kwargs or {}
        kwargs['stacklevel'] = 2
        logger = cls._security_logger if log_type == LogType.SECURITY else cls._generic_logger
        logger.log(level, msg, *args, **kwargs)

    @classmethod
    def info(cls, msg: str, log_type: LogType = LogType.GENERIC, *args, **kwargs):
        """
            Function responsible for logging information.

            This function takes msg as arguments for logging in the pre-defined constant format.

            Parameters:
                msg         - contents of the log message.
                *args       -
                **kwargs    -
        """
        cls._ensure_initialized()
        kwargs = kwargs or {}
        kwargs['stacklevel'] = 2
        logger = cls._security_logger if log_type == LogType.SECURITY else cls._generic_logger
        logger.info(msg, *args, **kwargs)

    @classmethod
    def debug(cls, msg: str, log_type: LogType = LogType.GENERIC, *args, **kwargs):
        """
            Function responsible for logging debug information.

            This function takes msg as arguments for logging in the pre-defined constant format.

            Parameters:
                msg         - contents of the log message.
                *args       -
                **kwargs    -
        """
        cls._ensure_initialized()
        kwargs = kwargs or {}
        kwargs['stacklevel'] = 2
        logger = cls._security_logger if log_type == LogType.SECURITY else cls._generic_logger
        logger.debug(msg, *args, **kwargs)

    @classmethod
    def warning(cls, msg: str, log_type: LogType = LogType.GENERIC, *args, **kwargs):
        """
            Function responsible for logging error information.

            This function takes msg as arguments for logging in the pre-defined constant format.

            Parameters:
                msg         - contents of the log message.
                *args       -
                **kwargs    -
        """
        cls._ensure_initialized()
        kwargs = kwargs or {}
        kwargs['stacklevel'] = 2
        logger = cls._security_logger if log_type == LogType.SECURITY else cls._generic_logger
        logger.warning(msg, *args, **kwargs)

    @classmethod
    def error(cls, msg: str, log_type: LogType = LogType.GENERIC, *args, **kwargs):
        """
            Function responsible for logging error information.

            This function takes msg as arguments for logging in the pre-defined constant format.

            Parameters:
                msg         - contents of the log message.
                *args       -
                **kwargs    -
        """
        cls._ensure_initialized()
        kwargs = kwargs or {}
        kwargs['stacklevel'] = 2
        logger = cls._security_logger if log_type == LogType.SECURITY else cls._generic_logger
        logger.error(msg, *args, **kwargs)

    @classmethod
    def exception(cls, msg: str, log_type: LogType = LogType.GENERIC, *args, **kwargs):
        """
            Function responsible for logging exceptions.

            This function takes msg as arguments for logging in the pre-defined constant format.

            Parameters:
                msg         - contents of the log message.
                *args       -
                **kwargs    -
        """
        cls._ensure_initialized()
        kwargs = kwargs or {}
        kwargs['stacklevel'] = 2
        msg = f'{msg} \n {traceback.format_exc()}'
        logger = cls._security_logger if log_type == LogType.SECURITY else cls._generic_logger
        logger.error(msg, *args, **kwargs)

    @classmethod
    def set_level(cls, level):
        """
            Function responsible for changing log level.

            This function takes level as argument for changing log level of the logger.
           
            Parameters:
                level       - type of log level (DEBUG, INFO, ERROR, etc.)
        """
        try:
            level = logging._nameToLevel[level.upper()]
        except KeyError:
            LoggingManager.exception(f'Tried to set invalid logging level {level}')
        else:
            cls._generic_logger.setLevel(level)
            cls._security_logger.setLevel(level)

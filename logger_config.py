import logging
import maya.cmds as cmds

def logger_setup(name=__name__):
    """Setup and return configured logger"""
    logger = logging.getLogger(name)

    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = False
        cmds.scriptEditorInfo(sw=1) # Maya suppressWarnings

        class ExitHandler(logging.StreamHandler):
            def emit(self, record):
                super().emit(record)
                if record.levelno in (logging.ERROR, logging.CRITICAL):
                    raise SystemExit(-1)

        exit_handler = ExitHandler()
        fmt = logging.Formatter('%(levelname)s: %(funcName)s: %(message)s')
        exit_handler.setFormatter(fmt)
        logger.addHandler(exit_handler)

    return logger

"""
# logger_config.py
author: Daisy Jane @gnitemouse

Shared logging setup for Rig Tail.

Every module calls logger_setup(__name__) and gets a child of one shared
'rig_tail' logger, so verbosity is set in one place and can be narrowed
to a single module:

    from logger_config import set_level, TRACE
    set_level(logging.WARNING)             # quiet whole build
    set_level(TRACE, 'rig_tail_fk')        # everything from one module

Levels, from least to most detail:

    WARNING  something is wrong; the build continues but may be suspect
    INFO     phase headers and per-rig-part progress
    DEBUG    routine build steps (curves, controls, stretch)
    TRACE    per-node detail (arguments, joint chains, node names)

Severity does not control flow. A message says how bad something is; it
never decides whether the build stops. Code that cannot meaningfully
continue calls raise_build_error(), which reports and then raises
RigTailBuildError for a caller to catch.

This split matters because the two are independent: 'geometry override
is locked, skipping' is severe but recoverable, while a rig part with no
usable joint chain is fatal however quietly it is phrased. Wiring an
exit into the log handler forces one to stand in for the other, and
makes every diagnostic message a potential build-killer.
"""

import logging

# Detail below DEBUG. Routine build steps sit at DEBUG; the per-node
# dumps that are only useful when tracing one function sit here.
TRACE = 5
logging.addLevelName(TRACE, 'TRACE')

# Parent logger. Module loggers are children of this, so setting a level
# here applies to all of them unless a module overrides it.
ROOT = 'rig_tail'


class RigTailBuildError(RuntimeError):
    """
    The build cannot continue.

    Deliberately a normal exception rather than SystemExit: SystemExit
    derives from BaseException, so it slips past 'except Exception' and
    skips caller cleanup, and Maya's interpreter handles it
    inconsistently. This can be caught and reported to the user.
    """


def _trace(self, message, *args, **kwargs):
    """logger.trace(...) - detail below DEBUG."""
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


logging.Logger.trace = _trace


def _configure_root():
    """Attach the one handler to the shared parent logger, once."""
    root = logging.getLogger(ROOT)
    if not root.handlers:
        root.setLevel(logging.INFO)
        root.propagate = False       # do not double-print through Maya's root
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter('%(levelname)s: %(funcName)s: %(message)s'))
        root.addHandler(handler)
    return root


def logger_setup(name=__name__):
    """
    Get this module's logger.

    Arguments:
        name (str): Module name, normally __name__

    Return:
        logging.Logger: Child of the shared 'rig_tail' logger
    """
    _configure_root()
    return logging.getLogger(f'{ROOT}.{name}')


def set_level(level, module=None):
    """
    Set logging verbosity.

    Arguments:
        level (int): logging.WARNING / INFO / DEBUG, or TRACE
        module (str): Single module to change (e.g. 'rig_tail_fk'), or
            None for every Rig Tail module

    Return:
        logging.Logger: The logger whose level was set
    """
    logger = logging.getLogger(f'{ROOT}.{module}' if module else ROOT)
    logger.setLevel(level)
    return logger


def reset_levels():
    """
    Clear per-module level overrides so every module follows the parent
    again. Module loggers set to NOTSET inherit the parent's level.
    """
    prefix = f'{ROOT}.'
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(prefix):
            logging.getLogger(name).setLevel(logging.NOTSET)


def raise_build_error(logger, message):
    """
    Report that the build cannot continue, then stop it.

    Use where carrying on would produce a rig that is wrong rather than
    merely incomplete. Everything recoverable should log and return
    instead, leaving the caller to decide.

    Arguments:
        logger (logging.Logger): Reporting module's logger
        message (str): What went wrong, naming the rig part or node

    Raises:
        RigTailBuildError: Always
    """
    logger.error(message)
    raise RigTailBuildError(message)

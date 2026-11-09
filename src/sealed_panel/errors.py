"""Errors raised by sealed-panel. Every refusal is an EvalError."""


class EvalError(Exception):
    """A step the protocol refuses, or an input it cannot use."""


class ConfigError(EvalError):
    """A configuration file that does not fit the defaults' shape."""


class MalformedReply(EvalError):
    """A reply the transcript holds in a shape that cannot be read: recorded as rejected."""


def user_message(exc: BaseException) -> str | None:
    """What the caller is told for an error it can fix (a refusal, a missing file, a file of
    the wrong shape), or None for an error in sealed-panel itself. The command line and the
    MCP server both use it."""
    if isinstance(exc, (EvalError, OSError)):
        return str(exc)
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return (
            f"unusable input ({type(exc).__name__}: {exc}); check the files given to this command"
        )
    return None

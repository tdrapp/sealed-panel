"""Errors raised by sealed-panel. Every refusal is an EvalError."""


class EvalError(Exception):
    """A step the protocol refuses, or an input it cannot use."""


class ConfigError(EvalError):
    """A configuration file that does not fit the defaults' shape."""


class MalformedReply(EvalError):
    """A reply the transcript holds in a shape that cannot be read: recorded as rejected."""

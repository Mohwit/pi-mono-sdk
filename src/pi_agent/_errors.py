class PiAgentError(Exception):
    """Base class for all pi-agent errors."""


class PiBridgeError(PiAgentError):
    """An error message attributed to a specific request from the bridge process."""


class PiConnectionError(PiAgentError):
    """Bridge binary not found, or the subprocess failed to start."""


class PiToolError(PiAgentError):
    """Python tool execution failed."""

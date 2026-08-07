"""Domain errors, raised by code that does real work and has no business
knowing how it was called.

The console now answers on two surfaces: the SPA's /api routes and the
machine-facing /v1 routes and MCP tools. Shared logic raises these; each surface
translates them into whatever its callers understand (an HTTP status, an MCP
tool error). Without them, moving a helper out of a route body would drag
HTTPException along with it."""


class ConsoleError(Exception):
    """Base for everything here. The message is written to be shown to the
    caller, so it must never contain a secret."""


class NotFound(ConsoleError):
    """The thing addressed does not exist."""


class Invalid(ConsoleError):
    """The request is malformed or asks for something nonsensical."""


class Conflict(ConsoleError):
    """Valid, but not possible in the current state: a deploy is mid-flight, the
    app is not running, the build never served traffic."""


class Unavailable(ConsoleError):
    """A dependency the console needs is not configured or not reachable."""

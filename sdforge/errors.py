"""Named errors raised by the specification compiler."""


class SpecError(ValueError):
    """A machine-readable specification error with a useful location."""

    def __init__(self, code: str, message: str, path: str = "") -> None:
        self.code = code
        self.message = message
        self.path = path
        location = f" at {path}" if path else ""
        super().__init__(f"{code}{location}: {message}")


"""Domain errors for the Agentic-Evo research runtime."""


class AgenticEvoError(Exception):
    """Base class for expected runtime failures."""


class GenesisExistsError(AgenticEvoError):
    """Raised when Genesis is attempted over an existing installation."""


class IntegrityError(AgenticEvoError):
    """Raised when a commitment, signature, or evidence chain is invalid."""


class SensitiveContentError(AgenticEvoError):
    """Raised when an evidence payload contains a prohibited sensitive key."""


class AuthorityError(AgenticEvoError):
    """Raised when a caller does not match the bound host authority."""


class RuntimeOffError(AgenticEvoError):
    """Raised when active work is attempted while the host has turned the runtime off."""


class BodyLeaseError(AgenticEvoError):
    """Raised when a Current Body session has no live lineage lease."""


class HeadConflictError(AgenticEvoError):
    """Raised when an attempted Head transition does not start at the current Head."""


class RootBindingError(AgenticEvoError):
    """Raised when a body commitment belongs to a different Root."""


class BodyNotFoundError(AgenticEvoError):
    """Raised when a referenced body commitment is unavailable."""


class InvalidBodyError(AgenticEvoError):
    """Raised when a body manifest or file path is invalid."""


class PolicyGateError(AgenticEvoError):
    """Raised when a candidate repair is denied by loop policy."""


class InvalidCandidateError(AgenticEvoError):
    """Raised when a candidate repair violates loop safety constraints."""


class MemoryIntegrityError(AgenticEvoError):
    """Raised when a CAMU store hash chain or record integrity is invalid."""


class MemoryRecordError(AgenticEvoError):
    """Raised when a CAMU record violates the memory contract."""

class DomainError(Exception):
    """Base class for all domain-level exceptions."""


class NotFoundError(DomainError):
    """Aggregate not found."""


class PermissionDeniedError(DomainError):
    """Caller lacks permission for this action."""


class ValidationError(DomainError):
    """Input failed business validation."""


class PluginNotRegisteredError(DomainError):
    """Requested plugin name does not exist in the registry."""


class PluginIncompatibleError(DomainError):
    """Plugin's api_version is outside the supported range."""


class PlatformPublishError(DomainError):
    """Publishing to the social platform failed."""


class LLMBudgetExceededError(DomainError):
    """Org has hit its monthly LLM spend cap."""


class AuthError(DomainError):
    """OIDC / OAuth token validation failed."""


class NoMembershipError(DomainError):
    """The caller's auth identity is valid, but they have no membership in
    any org and bootstrap isn't applicable. Maps to HTTP 403 with a clear
    "ask your admin to invite you" message — never to 401, since the JWT
    itself was fine."""

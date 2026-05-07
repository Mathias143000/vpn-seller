from __future__ import annotations


class DomainError(Exception):
    """Base domain exception."""


class NotFoundError(DomainError):
    """Requested entity not found."""


class AccessDeniedError(DomainError):
    """User has insufficient permissions."""


class OutOfStockError(DomainError):
    """Inventory is not available."""


class InvalidStateError(DomainError):
    """Current entity state does not allow the requested action."""


class PaymentMismatchError(DomainError):
    """Webhook payload does not match the expected order/payment values."""


class ProvisioningError(DomainError):
    """Remote access provisioning failed."""

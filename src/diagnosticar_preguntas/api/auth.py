"""Shared-secret authentication between the platform backend and this service.

The backend sends ``X-Internal-Token`` on every call. The service is never
exposed publicly, so the token guards against other containers on the same
network, not against browsers. Without a configured token the service fails
closed: it refuses every round unless unauthenticated calls were allowed
explicitly, which only local development should do.
"""

from __future__ import annotations

import hmac

from pydantic import SecretStr

TOKEN_HEADER = "X-Internal-Token"


def is_authorized(
    expected: SecretStr | None, presented: str | None, *, allow_unauthenticated: bool
) -> bool:
    """Whether a call presenting ``presented`` may run a round.

    Compared in constant time so response timing does not reveal how much of a
    guess was right.
    """
    if expected is None:
        return allow_unauthenticated
    if presented is None:
        return False
    return hmac.compare_digest(
        presented.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    )

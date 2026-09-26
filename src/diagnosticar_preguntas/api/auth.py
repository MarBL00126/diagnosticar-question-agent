"""Shared-secret authentication between the platform backend and this service.

The backend sends ``X-Internal-Token`` on every call. The service is never
exposed publicly, so the token guards against other containers on the same
network, not against browsers.
"""

from __future__ import annotations

import hmac

from pydantic import SecretStr

TOKEN_HEADER = "X-Internal-Token"


def token_matches(expected: SecretStr | None, presented: str | None) -> bool:
    """True when no token is configured, or when ``presented`` equals it.

    Compared in constant time so response timing does not reveal how much of a
    guess was right.
    """
    if expected is None:
        return True
    if presented is None:
        return False
    return hmac.compare_digest(
        presented.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    )

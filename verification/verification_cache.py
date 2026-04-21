from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import diskcache

if TYPE_CHECKING:
    from verification.verification_input import VerificationInput
    from verification.verification_result import VerificationResult


class VerificationCache:
    def __init__(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache = diskcache.Cache(str(cache_dir))

    def get(self, vinput: "VerificationInput") -> "VerificationResult | None":
        return self._cache.get(vinput.cache_key())

    def set(self, vinput: "VerificationInput", result: "VerificationResult") -> None:
        self._cache.set(vinput.cache_key(), result)

    def close(self) -> None:
        self._cache.close()

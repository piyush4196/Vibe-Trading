"""Best-effort xxhash import for Windows Application Control blocks.

langsmith (pulled in by langchain-openai) imports the native ``xxhash``
extension. On some Windows setups Smart App Control / WDAC blocks the
``.pyd``, which surfaces as a misleading ``langchain-openai is not installed``.

If the real package cannot load, install a tiny pure-Python stub so LLM
imports can proceed. Hash quality is irrelevant for our use (langsmith UUID
helpers); we only need the symbols to exist.
"""

from __future__ import annotations

import sys
import types


def ensure_xxhash() -> str:
    """Ensure ``xxhash`` is importable.

    Returns:
        ``"native"`` when the real package loads, ``"stub"`` when a fallback
        was installed, or ``"cached"`` when it was already present.
    """
    if "xxhash" in sys.modules:
        return "cached"
    try:
        import xxhash  # noqa: F401

        return "native"
    except ImportError:
        stub = types.ModuleType("xxhash")

        class _Hasher:
            def __init__(self, *_args, **_kwargs) -> None:
                self._n = 0

            def update(self, data=b"", *_args, **_kwargs):  # noqa: ANN001
                if data:
                    self._n = (self._n + len(data)) & 0xFFFFFFFFFFFFFFFF
                return self

            def digest(self) -> bytes:
                return self._n.to_bytes(8, "little", signed=False) * 2

            def hexdigest(self) -> str:
                return f"{self._n:016x}"

            def intdigest(self) -> int:
                return self._n

            def copy(self) -> "_Hasher":
                other = _Hasher()
                other._n = self._n
                return other

        stub.xxh32 = _Hasher  # type: ignore[attr-defined]
        stub.xxh64 = _Hasher  # type: ignore[attr-defined]
        stub.xxh3_64 = _Hasher  # type: ignore[attr-defined]
        stub.xxh3_128 = _Hasher  # type: ignore[attr-defined]
        stub.__version__ = "0-vibe-stub"  # type: ignore[attr-defined]
        sys.modules["xxhash"] = stub
        return "stub"

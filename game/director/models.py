"""Compatibility re-export for retained Director callers.

The generic Agent transport now lives in :mod:`agent.transport`. The Director
remains disabled, but its historical imports and tests keep the same symbols.
"""

from agent.transport import ChatClient, ModelError, load_profile

__all__ = ["ChatClient", "ModelError", "load_profile"]

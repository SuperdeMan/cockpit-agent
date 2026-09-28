"""Scope ancestry shared by authorization and context projection."""


def is_scope_covered(required: str, effective: set[str]) -> bool:
    """A parent scope covers descendants; writes never imply unrelated reads."""
    parts = required.split(".")
    return any(".".join(parts[:i]) in effective for i in range(len(parts), 0, -1))

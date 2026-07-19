"""Global repository registry for content providers."""
from __future__ import annotations

from typing import Optional

from .contracts import ContentRepository


class RegistrationError(Exception):
    """Raised when attempting to register a provider that already exists."""

    def __init__(self, provider_name: str, message: str) -> None:
        super().__init__(message)
        self.provider_name = provider_name
        self.message = message


class RepositoryRegistry:
    def __init__(self) -> None:
        self._repositories: dict[str, ContentRepository] = {}

    def register(self, provider_name: str, repository: ContentRepository) -> None:
        if provider_name in self._repositories:
            raise RegistrationError(
                provider_name=provider_name,
                message=f"Provider {provider_name!r} is already registered.",
            )
        self._repositories[provider_name] = repository

    def get(self, provider_name: str) -> Optional[ContentRepository]:
        return self._repositories.get(provider_name)

    def names(self) -> list[str]:
        return sorted(self._repositories.keys())

    def snapshot(self) -> dict[str, ContentRepository]:
        return dict(self._repositories)

    def reset(self) -> None:
        """For test isolation only."""
        self._repositories.clear()


# Process-wide singleton for convenience; tests may reset it.
registry = RepositoryRegistry()

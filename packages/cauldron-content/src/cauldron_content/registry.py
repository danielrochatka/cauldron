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
        self._owners: dict[str, str] = {}

    def register(
        self,
        provider_name: str,
        repository: ContentRepository,
        owning_module: str = "",
    ) -> None:
        if provider_name in self._repositories:
            existing = self._repositories[provider_name]
            existing_owner = self._owners.get(provider_name, "")
            if existing is repository and existing_owner == owning_module:
                return  # exact same registration — idempotent
            if existing is not repository:
                owner_hint = f" (registered by {existing_owner!r})" if existing_owner else ""
                attempted = f" Attempted re-registration by {owning_module!r}." if owning_module else ""
                raise RegistrationError(
                    provider_name=provider_name,
                    message=f"Provider {provider_name!r} is already registered{owner_hint}.{attempted}",
                )
            # same instance, different owner
            raise RegistrationError(
                provider_name=provider_name,
                message=(
                    f"Provider {provider_name!r} is already owned by "
                    f"{existing_owner!r}; cannot re-register with "
                    f"owning_module={owning_module!r}."
                ),
            )
        self._repositories[provider_name] = repository
        self._owners[provider_name] = owning_module

    def get(self, provider_name: str) -> Optional[ContentRepository]:
        return self._repositories.get(provider_name)

    def get_owning_module(self, provider_name: str) -> str:
        """Return the owning module slug for the named provider, or '' if unknown."""
        return self._owners.get(provider_name, "")

    def names(self) -> list[str]:
        return sorted(self._repositories.keys())

    def snapshot(self) -> dict[str, ContentRepository]:
        return dict(self._repositories)

    def reset(self) -> None:
        """For test isolation only."""
        self._repositories.clear()
        self._owners.clear()


# Process-wide singleton for convenience; tests may reset it.
registry = RepositoryRegistry()

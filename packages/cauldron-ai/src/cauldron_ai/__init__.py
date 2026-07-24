"""Provider-neutral AI contracts for Cauldron.

This package defines the abstract contracts used by every Cauldron AI
consumer (model messages, tool definitions, provider protocol). It has
no Django dependency and no model-vendor SDK dependency; concrete
provider implementations live in downstream packages.
"""
from .contracts import (
    AIModelMessage,
    AIModelRequest,
    AIModelResponse,
    AIModelToolCall,
    AIModelToolDefinition,
)
from .providers import (
    AIModelProvider,
    AIModelProviderDescriptor,
    AIModelProviderRegistry,
    descriptor_for,
    get_default_provider,
    get_provider,
    provider_names,
    register_provider,
    unregister_provider,
)

__all__ = [
    "AIModelMessage",
    "AIModelRequest",
    "AIModelResponse",
    "AIModelToolCall",
    "AIModelToolDefinition",
    "AIModelProvider",
    "AIModelProviderDescriptor",
    "AIModelProviderRegistry",
    "descriptor_for",
    "get_default_provider",
    "get_provider",
    "provider_names",
    "register_provider",
    "unregister_provider",
]

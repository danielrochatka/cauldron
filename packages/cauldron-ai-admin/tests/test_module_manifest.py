"""Verify the cauldron-ai-admin module manifest declares exactly the
capabilities and dependencies mandated by the trust contract."""
from cauldron_ai_admin.module import module


EXPECTED_PROVIDES = {
    "admin.ai",
    "admin.ai.orchestration",
    "admin.ai.tools",
    "admin.ai.audit",
    "admin.ai.health",
}

EXPECTED_REQUIRED_CAPABILITIES = {
    "ai.model.providers",
    "content.operations",
    "admin.interface",
    "django.state",
    "identity.authentication",
    "identity.permissions",
}


def test_module_provides_expected_capabilities():
    assert set(module.manifest.provides) == EXPECTED_PROVIDES


def test_module_requires_expected_capability_dependencies():
    capability_deps = {
        r.slug for r in module.manifest.requires if r.kind == "capability"
    }
    assert capability_deps == EXPECTED_REQUIRED_CAPABILITIES


def test_module_slug_and_apps():
    assert module.slug == "cauldron.ai.admin"
    assert tuple(module.manifest.django_apps) == ("cauldron_ai_admin",)

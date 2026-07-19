"""Cauldron fixture module: beta. Requires alpha; optional dep on test.capability.gamma."""

from cauldron.modules import BaseModule, ModuleManifest, ModuleRequirement

_manifest = ModuleManifest(
    slug="cauldron.fixture.beta",
    label="Cauldron Fixture Beta",
    version="1.0.0",
    cauldron_version=">=0.1.0",
    requires=(
        ModuleRequirement(slug="cauldron.fixture.alpha", version=">=1.0.0"),
    ),
    optional=(
        ModuleRequirement(slug="test.capability.gamma", kind="capability"),
    ),
)

module = BaseModule(_manifest)

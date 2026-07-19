"""Cauldron fixture module: alpha. Standalone; provides test.capability.alpha."""

from cauldron.modules import BaseModule, ModuleManifest

_manifest = ModuleManifest(
    slug="cauldron.fixture.alpha",
    label="Cauldron Fixture Alpha",
    version="1.0.0",
    cauldron_version=">=0.1.0",
    provides=("test.capability.alpha",),
)

module = BaseModule(_manifest)

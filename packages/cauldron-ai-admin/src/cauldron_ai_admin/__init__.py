"""Cauldron Admin AI — provider-neutral admin assistant scaffolding.

Public API:

* :class:`cauldron_ai_admin.service.AdminAIService` — the pipeline that
  turns a natural-language admin request into vetted tool invocations.
* :func:`cauldron_ai_admin.tools.register_tool` — extension point child
  modules use to expose new tools.
* :func:`cauldron_ai_admin.tools.get_tool_registry` — the singleton
  registry used by the service at request time.
"""

default_app_config = "cauldron_ai_admin.apps.CauldronAIAdminConfig"

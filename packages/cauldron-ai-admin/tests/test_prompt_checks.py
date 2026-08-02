"""Tests for prompt-template Django system checks (E017–E021, W007–W008)."""
import pytest
from unittest.mock import patch, MagicMock

from cauldron_ai.prompt_templates import (
    AIGlobalOperatingPrompt,
    AIToolPromptTemplate,
    AIPromptTemplateRegistry,
)
from cauldron_ai.testing import reset_prompt_registry_for_tests
from cauldron_ai_admin.tools import (
    AdminAIToolDefinition,
    AdminAIToolRegistry,
    RiskLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_def(
    name: str,
    permission: str = "auth.view_user",
) -> AdminAIToolDefinition:
    return AdminAIToolDefinition(
        name=name,
        version="1.0",
        description="test",
        argument_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        required_permission=permission,
        owning_module="test.module",
    )


def _make_template(
    tool_name: str,
    version: str = "v1",
    permission: str | None = "auth.view_user",
) -> AIToolPromptTemplate:
    return AIToolPromptTemplate(
        tool_name=tool_name,
        template_version=version,
        owning_module="test.module",
        purpose="Test purpose.",
        supported_tasks=("task",),
        required_permission=permission,
        risk_level="READ_ONLY",
        read_scope="scope.",
        write_scope="None.",
        preconditions=("pre",),
        input_expectations="none.",
        result_behavior="result.",
        approval_requirements="None; read-only.",
        clarification_behavior="clarify.",
        refusal_behavior="refuse.",
        error_guidance="report.",
        positive_examples=("ex.",),
        boundary_examples=("bound.",),
    )


def _noop_handler(context, **kwargs):
    pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_registry():
    reset_prompt_registry_for_tests()
    yield
    reset_prompt_registry_for_tests()


# ---------------------------------------------------------------------------
# Import the check functions
# ---------------------------------------------------------------------------

from cauldron_ai_admin.checks import (
    check_registered_tools_have_prompt_templates,
    check_no_orphan_prompt_templates,
    check_prompt_template_versions_valid,
    check_prompt_template_permission_alignment,
    check_global_operating_prompt_present,
    check_global_prompt_version_valid,
)


# ---------------------------------------------------------------------------
# Context manager helpers for patching both registries
# ---------------------------------------------------------------------------

def _patch_active():
    """Patch _is_admin_ai_active to return True."""
    return patch("cauldron_ai_admin.checks._is_admin_ai_active", return_value=True)


def _patch_tool_registry(tool_reg: AdminAIToolRegistry):
    """Patch get_tool_registry in cauldron_ai_admin.tools (imported locally by checks)."""
    return patch("cauldron_ai_admin.tools.get_tool_registry", return_value=tool_reg)


def _patch_prompt_registry(tmpl_reg: AIPromptTemplateRegistry):
    """Patch get_prompt_template_registry in cauldron_ai.prompt_templates (imported by checks)."""
    return patch(
        "cauldron_ai.prompt_templates.get_prompt_template_registry",
        return_value=tmpl_reg,
    )


# ---------------------------------------------------------------------------
# E017: every registered tool must have a prompt template
# ---------------------------------------------------------------------------


def test_e017_fires_when_tool_has_no_template():
    tool_reg = AdminAIToolRegistry()
    tool_reg.register(_make_tool_def("test.tool"), _noop_handler)
    tmpl_reg = AIPromptTemplateRegistry()
    # No templates registered.

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_registered_tools_have_prompt_templates(None)

    assert any(e.id == "admin_ai.E017" for e in errors)
    assert any("test.tool" in str(e.msg) for e in errors)


def test_e017_clear_when_all_tools_have_templates():
    tool_reg = AdminAIToolRegistry()
    tool_reg.register(_make_tool_def("test.tool"), _noop_handler)
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(_make_template("test.tool"))

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_registered_tools_have_prompt_templates(None)

    assert errors == []


# ---------------------------------------------------------------------------
# E018: every prompt template must match a registered tool
# ---------------------------------------------------------------------------


def test_e018_fires_when_template_for_unknown_tool():
    tool_reg = AdminAIToolRegistry()
    # No tools registered.
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(_make_template("orphan.tool"))

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_no_orphan_prompt_templates(None)

    assert any(e.id == "admin_ai.E018" for e in errors)
    assert any("orphan.tool" in str(e.msg) for e in errors)


def test_e018_clear_when_all_templates_match_tools():
    tool_reg = AdminAIToolRegistry()
    tool_reg.register(_make_tool_def("test.tool"), _noop_handler)
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(_make_template("test.tool"))

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_no_orphan_prompt_templates(None)

    assert errors == []


# ---------------------------------------------------------------------------
# E019: every template version must be non-empty and valid
# ---------------------------------------------------------------------------


def test_e019_fires_when_template_has_invalid_version():
    """Inject a template with an invalid version by bypassing frozen dataclass."""
    tmpl_reg = AIPromptTemplateRegistry()
    # Register a valid template.
    tmpl_reg.register_tool_template(_make_template("test.tool", version="v1"))
    # Inject a template with empty version directly.
    bad_tmpl = object.__new__(AIToolPromptTemplate)
    object.__setattr__(bad_tmpl, "tool_name", "bad.tool")
    object.__setattr__(bad_tmpl, "template_version", "")
    object.__setattr__(bad_tmpl, "owning_module", "m")
    object.__setattr__(bad_tmpl, "purpose", "p")
    object.__setattr__(bad_tmpl, "supported_tasks", ())
    object.__setattr__(bad_tmpl, "required_permission", None)
    object.__setattr__(bad_tmpl, "risk_level", "READ_ONLY")
    object.__setattr__(bad_tmpl, "read_scope", "s")
    object.__setattr__(bad_tmpl, "write_scope", "None.")
    object.__setattr__(bad_tmpl, "preconditions", ())
    object.__setattr__(bad_tmpl, "input_expectations", "i")
    object.__setattr__(bad_tmpl, "result_behavior", "r")
    object.__setattr__(bad_tmpl, "approval_requirements", "None; read-only.")
    object.__setattr__(bad_tmpl, "clarification_behavior", "c")
    object.__setattr__(bad_tmpl, "refusal_behavior", "ref")
    object.__setattr__(bad_tmpl, "error_guidance", "e")
    object.__setattr__(bad_tmpl, "positive_examples", ())
    object.__setattr__(bad_tmpl, "boundary_examples", ())
    with tmpl_reg._lock:
        tmpl_reg._tool_templates["bad.tool"] = bad_tmpl

    with _patch_active(), _patch_prompt_registry(tmpl_reg):
        errors = check_prompt_template_versions_valid(None)

    assert any(e.id == "admin_ai.E019" for e in errors)
    assert any("bad.tool" in str(e.msg) for e in errors)


def test_e019_clear_when_all_versions_valid():
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(_make_template("test.tool", version="v1"))
    tmpl_reg.register_tool_template(_make_template("other.tool", version="1.2.3"))

    with _patch_active(), _patch_prompt_registry(tmpl_reg):
        errors = check_prompt_template_versions_valid(None)

    assert errors == []


# ---------------------------------------------------------------------------
# E021: template required_permission must match tool definition
# ---------------------------------------------------------------------------


def test_e021_fires_when_permission_mismatch():
    tool_reg = AdminAIToolRegistry()
    tool_reg.register(
        _make_tool_def("test.tool", permission="auth.view_user"),
        _noop_handler,
    )
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(
        _make_template("test.tool", permission="auth.change_user")  # mismatch
    )

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_prompt_template_permission_alignment(None)

    assert any(e.id == "admin_ai.E021" for e in errors)
    assert any("test.tool" in str(e.msg) for e in errors)


def test_e021_clear_when_permissions_match():
    tool_reg = AdminAIToolRegistry()
    tool_reg.register(
        _make_tool_def("test.tool", permission="auth.view_user"),
        _noop_handler,
    )
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(
        _make_template("test.tool", permission="auth.view_user")
    )

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_prompt_template_permission_alignment(None)

    assert errors == []


def test_e021_clear_when_template_permission_is_none():
    """None required_permission in template → no mismatch check."""
    tool_reg = AdminAIToolRegistry()
    tool_reg.register(
        _make_tool_def("test.tool", permission="auth.view_user"),
        _noop_handler,
    )
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_tool_template(
        _make_template("test.tool", permission=None)
    )

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        errors = check_prompt_template_permission_alignment(None)

    assert errors == []


# ---------------------------------------------------------------------------
# W007: global operating prompt should be registered
# ---------------------------------------------------------------------------


def test_w007_fires_when_no_global_prompt():
    tmpl_reg = AIPromptTemplateRegistry()
    # No global prompt registered.

    with _patch_active(), _patch_prompt_registry(tmpl_reg):
        warnings = check_global_operating_prompt_present(None)

    assert any(w.id == "admin_ai.W007" for w in warnings)


def test_w007_clear_when_global_prompt_registered():
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Global body."
    ))

    with _patch_active(), _patch_prompt_registry(tmpl_reg):
        warnings = check_global_operating_prompt_present(None)

    assert warnings == []


# ---------------------------------------------------------------------------
# W008: global prompt version must be valid
# ---------------------------------------------------------------------------


def test_w008_fires_when_global_prompt_has_invalid_version():
    """Bypass frozen dataclass to inject invalid version."""
    tmpl_reg = AIPromptTemplateRegistry()
    bad_gp = object.__new__(AIGlobalOperatingPrompt)
    object.__setattr__(bad_gp, "version", "invalid!!!")
    object.__setattr__(bad_gp, "owning_module", "m")
    object.__setattr__(bad_gp, "body", "Body.")
    with tmpl_reg._lock:
        tmpl_reg._global_prompt = bad_gp

    with _patch_active(), _patch_prompt_registry(tmpl_reg):
        warnings = check_global_prompt_version_valid(None)

    assert any(w.id == "admin_ai.W008" for w in warnings)


def test_w008_clear_when_global_prompt_version_valid():
    tmpl_reg = AIPromptTemplateRegistry()
    tmpl_reg.register_global_prompt(AIGlobalOperatingPrompt(
        version="v1", owning_module="m", body="Body."
    ))

    with _patch_active(), _patch_prompt_registry(tmpl_reg):
        warnings = check_global_prompt_version_valid(None)

    assert warnings == []


# ---------------------------------------------------------------------------
# All checks return empty when admin AI is not active
# ---------------------------------------------------------------------------


def test_all_checks_return_empty_when_not_active():
    checks_to_test = [
        check_registered_tools_have_prompt_templates,
        check_no_orphan_prompt_templates,
        check_prompt_template_versions_valid,
        check_prompt_template_permission_alignment,
        check_global_operating_prompt_present,
        check_global_prompt_version_valid,
    ]
    with patch("cauldron_ai_admin.checks._is_admin_ai_active", return_value=False):
        for check_fn in checks_to_test:
            result = check_fn(None)
            assert result == [], f"{check_fn.__name__} should return [] when inactive"


# ---------------------------------------------------------------------------
# Checks do not build providers or read secrets
# ---------------------------------------------------------------------------


def test_checks_do_not_import_provider():
    """The check functions must not import or construct AI providers."""
    tmpl_reg = AIPromptTemplateRegistry()
    tool_reg = AdminAIToolRegistry()

    import sys
    before = set(sys.modules.keys())

    with _patch_active(), _patch_tool_registry(tool_reg), _patch_prompt_registry(tmpl_reg):
        check_registered_tools_have_prompt_templates(None)
        check_no_orphan_prompt_templates(None)
        check_global_operating_prompt_present(None)

    after = set(sys.modules.keys())
    new_modules = after - before
    provider_modules = {m for m in new_modules if "openai" in m or "anthropic" in m}
    assert provider_modules == set(), f"Provider modules imported: {provider_modules}"

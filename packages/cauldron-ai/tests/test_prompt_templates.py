"""Tests for cauldron_ai.prompt_templates — registry contracts and validation."""
import pytest

from cauldron_ai.prompt_templates import (
    AIGlobalOperatingPrompt,
    AIToolPromptTemplate,
    AIPromptTemplateRegistry,
    PromptTemplateRegistryError,
    _reset_prompt_registry_for_tests,
    get_prompt_template_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template(
    tool_name: str = "test.tool",
    version: str = "v1",
    risk_level: str = "READ_ONLY",
    permission: str | None = "test_app.view_something",
) -> AIToolPromptTemplate:
    return AIToolPromptTemplate(
        tool_name=tool_name,
        template_version=version,
        owning_module="test.module",
        purpose="A test purpose.",
        supported_tasks=("task one",),
        required_permission=permission,
        risk_level=risk_level,
        read_scope="Some read scope.",
        write_scope="None.",
        preconditions=("precondition one",),
        input_expectations="No required arguments.",
        result_behavior="Returns a result.",
        approval_requirements="None; read-only.",
        clarification_behavior="Clarify if needed.",
        refusal_behavior="Refuse if unavailable.",
        error_guidance="Report the error.",
        positive_examples=("Example one.",),
        boundary_examples=("Boundary one.",),
    )


def _make_global_prompt(version: str = "v1", body: str = "Global body text.") -> AIGlobalOperatingPrompt:
    return AIGlobalOperatingPrompt(
        version=version,
        owning_module="test.module",
        body=body,
    )


@pytest.fixture(autouse=True)
def reset_registry():
    """Ensure each test starts with a fresh singleton registry."""
    _reset_prompt_registry_for_tests()
    yield
    _reset_prompt_registry_for_tests()


# ---------------------------------------------------------------------------
# Validation: AIToolPromptTemplate
# ---------------------------------------------------------------------------


def test_valid_template_construction():
    tmpl = _make_template()
    assert tmpl.tool_name == "test.tool"
    assert tmpl.template_version == "v1"
    assert tmpl.risk_level == "READ_ONLY"


def test_invalid_tool_name_no_dots():
    with pytest.raises(ValueError, match="tool_name"):
        _make_template(tool_name="singlesegment")


def test_invalid_tool_name_uppercase():
    with pytest.raises(ValueError, match="tool_name"):
        _make_template(tool_name="Test.Tool")


def test_invalid_tool_name_empty():
    with pytest.raises(ValueError, match="tool_name"):
        _make_template(tool_name="")


def test_invalid_tool_name_too_long():
    # 129 bytes with dotted name
    long_name = "a" * 64 + "." + "b" * 64  # 129 chars
    with pytest.raises(ValueError, match="tool_name"):
        _make_template(tool_name=long_name)


def test_valid_tool_name_with_underscores():
    tmpl = _make_template(tool_name="content.list_items")
    assert tmpl.tool_name == "content.list_items"


def test_valid_tool_name_three_segments():
    tmpl = _make_template(tool_name="ui.styles.list_files")
    assert tmpl.tool_name == "ui.styles.list_files"


def test_invalid_template_version_empty():
    with pytest.raises(ValueError, match="template_version"):
        _make_template(version="")


def test_invalid_template_version_plain_word():
    with pytest.raises(ValueError, match="template_version"):
        _make_template(version="latest")


def test_invalid_template_version_too_long():
    long_version = "v" + "1" * 64  # 65 chars
    with pytest.raises(ValueError, match="template_version"):
        _make_template(version=long_version)


def test_valid_template_version_semver():
    tmpl = _make_template(version="1.0.0")
    assert tmpl.template_version == "1.0.0"


def test_valid_template_version_v_prefix():
    tmpl = _make_template(version="v42")
    assert tmpl.template_version == "v42"


def test_invalid_risk_level():
    with pytest.raises(ValueError, match="risk_level"):
        _make_template(risk_level="UNKNOWN")


def test_all_valid_risk_levels():
    for level in ("READ_ONLY", "PROPOSE", "MAINTENANCE", "PRIVILEGED"):
        tmpl = _make_template(risk_level=level)
        assert tmpl.risk_level == level


# ---------------------------------------------------------------------------
# Validation: AIGlobalOperatingPrompt
# ---------------------------------------------------------------------------


def test_valid_global_prompt():
    gp = _make_global_prompt()
    assert gp.version == "v1"
    assert gp.body == "Global body text."


def test_global_prompt_empty_body():
    with pytest.raises(ValueError, match="body"):
        AIGlobalOperatingPrompt(version="v1", owning_module="m", body="")


def test_global_prompt_whitespace_body():
    with pytest.raises(ValueError, match="body"):
        AIGlobalOperatingPrompt(version="v1", owning_module="m", body="   ")


def test_global_prompt_empty_version():
    with pytest.raises(ValueError, match="version"):
        AIGlobalOperatingPrompt(version="", owning_module="m", body="text")


def test_global_prompt_invalid_version():
    with pytest.raises(ValueError, match="version"):
        AIGlobalOperatingPrompt(version="latest", owning_module="m", body="text")


def test_global_prompt_version_too_long():
    long_version = "v" + "1" * 64  # 65 chars
    with pytest.raises(ValueError, match="version"):
        AIGlobalOperatingPrompt(version=long_version, owning_module="m", body="text")


# ---------------------------------------------------------------------------
# Registry: tool template registration
# ---------------------------------------------------------------------------


def test_register_tool_template_success():
    reg = get_prompt_template_registry()
    tmpl = _make_template()
    reg.register_tool_template(tmpl)
    assert reg.get_tool_template("test.tool") is tmpl


def test_register_tool_template_reregister_identical_is_noop():
    reg = get_prompt_template_registry()
    tmpl = _make_template()
    reg.register_tool_template(tmpl)
    # Should not raise
    reg.register_tool_template(tmpl)
    assert reg.get_tool_template("test.tool") is tmpl


def test_register_tool_template_duplicate_different_raises():
    reg = get_prompt_template_registry()
    tmpl1 = _make_template(tool_name="test.tool", version="v1")
    tmpl2 = _make_template(tool_name="test.tool", version="v2")
    reg.register_tool_template(tmpl1)
    with pytest.raises(PromptTemplateRegistryError, match="test.tool"):
        reg.register_tool_template(tmpl2)


def test_get_tool_template_returns_none_for_unknown():
    reg = get_prompt_template_registry()
    assert reg.get_tool_template("unknown.tool") is None


def test_all_tool_templates_returns_sorted_list():
    reg = get_prompt_template_registry()
    reg.register_tool_template(_make_template(tool_name="z.zz"))
    reg.register_tool_template(_make_template(tool_name="a.aa"))
    reg.register_tool_template(_make_template(tool_name="m.mm"))
    templates = reg.all_tool_templates()
    names = [t.tool_name for t in templates]
    assert names == sorted(names)
    assert names == ["a.aa", "m.mm", "z.zz"]


def test_all_tool_templates_empty():
    reg = get_prompt_template_registry()
    assert reg.all_tool_templates() == []


# ---------------------------------------------------------------------------
# Registry: global prompt registration
# ---------------------------------------------------------------------------


def test_register_global_prompt_success():
    reg = get_prompt_template_registry()
    gp = _make_global_prompt()
    reg.register_global_prompt(gp)
    assert reg.get_global_prompt() is gp


def test_register_global_prompt_reregister_identical_is_noop():
    reg = get_prompt_template_registry()
    gp = _make_global_prompt()
    reg.register_global_prompt(gp)
    # Should not raise
    reg.register_global_prompt(gp)
    assert reg.get_global_prompt() is gp


def test_register_global_prompt_duplicate_different_raises():
    reg = get_prompt_template_registry()
    gp1 = _make_global_prompt(version="v1", body="Body one.")
    gp2 = _make_global_prompt(version="v2", body="Body two.")
    reg.register_global_prompt(gp1)
    with pytest.raises(PromptTemplateRegistryError):
        reg.register_global_prompt(gp2)


def test_get_global_prompt_returns_none_when_not_registered():
    reg = get_prompt_template_registry()
    assert reg.get_global_prompt() is None


# ---------------------------------------------------------------------------
# Registry: clear / isolation
# ---------------------------------------------------------------------------


def test_registry_clear_removes_all():
    reg = get_prompt_template_registry()
    reg.register_tool_template(_make_template())
    reg.register_global_prompt(_make_global_prompt())
    reg.clear()
    assert reg.all_tool_templates() == []
    assert reg.get_global_prompt() is None


def test_reset_for_tests_gives_fresh_registry():
    reg1 = get_prompt_template_registry()
    reg1.register_tool_template(_make_template())
    _reset_prompt_registry_for_tests()
    reg2 = get_prompt_template_registry()
    # After reset, the registry is fresh and the old template is gone.
    assert reg2.all_tool_templates() == []


def test_reset_for_tests_gives_new_instance():
    reg1 = get_prompt_template_registry()
    _reset_prompt_registry_for_tests()
    reg2 = get_prompt_template_registry()
    # The reset creates a new object; they should not be the same instance.
    assert reg1 is not reg2


# ---------------------------------------------------------------------------
# Frozen dataclass invariants
# ---------------------------------------------------------------------------


def test_tool_template_is_frozen():
    tmpl = _make_template()
    with pytest.raises((AttributeError, TypeError)):
        tmpl.tool_name = "other.name"  # type: ignore[misc]


def test_global_prompt_is_frozen():
    gp = _make_global_prompt()
    with pytest.raises((AttributeError, TypeError)):
        gp.version = "v99"  # type: ignore[misc]

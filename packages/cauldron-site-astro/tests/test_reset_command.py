"""Tests for the cauldron_site_reset management command.

Covers: flag parsing (including combined --content --styles), confirmation
prompts (y/yes/Y/YES), content deletion with optimistic hashes, content
apply failure (hard fail, no false success), style snapshot/restore on
rebuild failure, scope-specific summaries, service resolution failure
before mutation, idempotency, and a real flat-file router integration test.
"""
from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_build_result(ok: bool = True, pages_built: int = 0, error: str = ""):
    from cauldron_site_astro.service import BuildResult
    return BuildResult(ok=ok, pages_built=pages_built, output_dir="/out", error=error)


def _make_content_item(
    item_id: str,
    collection: str = "pages",
    provider: str = "flatfile",
    hash_val: str = "hash-" + "x",
):
    from cauldron_content.contracts import ContentItem, ContentStatus
    return ContentItem(
        id=item_id,
        collection=collection,
        slug=item_id,
        status=ContentStatus.PUBLISHED,
        schema="page",
        data={},
        body="",
        hash=hash_val,
        provider=provider,
    )


def _make_apply_result(success: bool = True, conflicts=(), validation_errors=()):
    from cauldron_content.contracts import ApplyResult
    return ApplyResult(
        success=success,
        applied=(),
        conflicts=tuple(conflicts),
        validation_errors=tuple(validation_errors),
    )


def _make_collection_info(name: str = "pages", provider: str = "flatfile"):
    from cauldron_content.router import CollectionInfo
    return CollectionInfo(name=name, schema="page", provider=provider, item_count=None)


def _make_mock_router(collections=None, items_by_collection=None, apply_result=None):
    router = MagicMock()
    router.list_collections.return_value = collections or []
    items_by_collection = items_by_collection or {}

    def _list_items(collection, *, include_drafts=False):
        return items_by_collection.get(collection, [])

    router.list_items.side_effect = _list_items
    router.apply.return_value = apply_result or _make_apply_result()
    return router


def _make_mock_build_service(router=None, build_result=None, theme_root=""):
    svc = MagicMock()
    svc._router = router or _make_mock_router()
    svc._config = MagicMock()
    svc._config.theme_root = theme_root
    svc.build.return_value = build_result or _make_build_result(ok=True)
    return svc


def _run_reset(**kwargs) -> tuple[str, str]:
    stdout = StringIO()
    stderr = StringIO()
    call_command("cauldron_site_reset", stdout=stdout, stderr=stderr, **kwargs)
    return stdout.getvalue(), stderr.getvalue()


def _patch_build_service(svc):
    return patch("cauldron_site_astro.service.get_build_service", return_value=svc)


def _patch_theme_service(mock_theme):
    # Patch at source so the command's delayed import gets the mock.
    return patch("cauldron_site_astro.theme.SiteThemeService", return_value=mock_theme)


def _patch_site_config(theme_root=""):
    cfg = MagicMock()
    cfg.theme_root = theme_root
    return patch(
        "cauldron_site_astro.config.get_site_astro_config",
        return_value=cfg,
    )


# ---------------------------------------------------------------------------
# 1. Argument parsing
# ---------------------------------------------------------------------------

def test_cli_accepts_content_and_styles_combined():
    """The parser must accept --content --styles together (not mutually exclusive)."""
    import argparse
    from cauldron_site_astro.management.commands.cauldron_site_reset import Command

    cmd = Command()
    parser = argparse.ArgumentParser()
    cmd.add_arguments(parser)
    # Must not raise
    ns = parser.parse_args(["--content", "--styles"])
    assert ns.content is True
    assert ns.styles is True


def test_content_and_styles_flags_together_resets_both():
    """--content --styles is equivalent to --all."""
    items = [_make_content_item("page-1")]
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": items},
    )
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(router=router, theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        patch("builtins.input", return_value="y"),
    ):
        stdout, _ = _run_reset(content=True, styles=True)

    assert router.apply.called
    assert theme.set_active_css.called
    assert "content item(s) removed" in stdout
    assert "styles reset" in stdout


def test_no_flags_defaults_to_all():
    """Running with no flags resets both content and styles."""
    items = [_make_content_item("page-1")]
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": items},
    )
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(router=router, theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        patch("builtins.input", return_value="y"),
    ):
        stdout, _ = _run_reset()

    assert router.apply.called
    assert theme.set_active_css.called
    assert theme.discard_staged.called


def test_content_flag_resets_content_only():
    """--content resets content but does NOT touch styles."""
    items = [_make_content_item("page-1")]
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": items},
    )
    theme = MagicMock()
    svc = _make_mock_build_service(router=router, theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        patch("builtins.input", return_value="y"),
    ):
        _run_reset(content=True)

    assert router.apply.called
    assert not theme.set_active_css.called
    assert not theme.discard_staged.called


def test_styles_flag_resets_styles_only():
    """--styles clears styles but does NOT touch content."""
    router = _make_mock_router()
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(router=router, theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        patch("builtins.input", return_value="y"),
    ):
        _run_reset(styles=True)

    assert not router.apply.called
    assert theme.set_active_css.called
    assert theme.discard_staged.called


def test_all_flag_resets_both():
    """--all resets content and styles (explicit flag)."""
    items = [_make_content_item("page-1")]
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": items},
    )
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(router=router, theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        patch("builtins.input", return_value="y"),
    ):
        _run_reset(**{"all": True})

    assert router.apply.called
    assert theme.set_active_css.called
    assert theme.discard_staged.called


# ---------------------------------------------------------------------------
# 2. Confirmation prompt
# ---------------------------------------------------------------------------

def test_confirmation_prompt_displayed_without_yes():
    """Without --yes the command prints a warning and prompts for confirmation."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="y") as mock_input,
    ):
        stdout, _ = _run_reset()

    mock_input.assert_called_once()
    assert "permanently delete" in stdout


def test_yes_flag_skips_confirmation():
    """--yes bypasses the confirmation prompt entirely."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input") as mock_input,
    ):
        _run_reset(yes=True)

    mock_input.assert_not_called()


def test_lowercase_y_proceeds():
    """'y' at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="y"),
    ):
        stdout, _ = _run_reset()
    assert "Website reset complete" in stdout


def test_uppercase_Y_proceeds():
    """'Y' at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="Y"),
    ):
        stdout, _ = _run_reset()
    assert "Website reset complete" in stdout


def test_yes_word_lowercase_proceeds():
    """'yes' at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="yes"),
    ):
        stdout, _ = _run_reset()
    assert "Website reset complete" in stdout


def test_yes_word_uppercase_proceeds():
    """'YES' at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="YES"),
    ):
        stdout, _ = _run_reset()
    assert "Website reset complete" in stdout


def test_yes_word_mixed_case_proceeds():
    """'Yes' (mixed case) at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="Yes"),
    ):
        stdout, _ = _run_reset()
    assert "Website reset complete" in stdout


def test_lowercase_n_aborts():
    """'n' at the prompt aborts without any changes."""
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": [_make_content_item("p1")]},
    )
    svc = _make_mock_build_service(router=router)
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="n"),
    ):
        stdout, _ = _run_reset()

    assert "Aborted" in stdout
    router.apply.assert_not_called()
    svc.build.assert_not_called()


def test_uppercase_N_aborts():
    """'N' at the prompt aborts."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="N"),
    ):
        stdout, _ = _run_reset()
    assert "Aborted" in stdout


def test_empty_input_aborts():
    """Empty string at the prompt aborts — default is N."""
    router = _make_mock_router()
    svc = _make_mock_build_service(router=router)
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value=""),
    ):
        stdout, _ = _run_reset()

    assert "Aborted" in stdout
    router.apply.assert_not_called()


def test_abort_does_not_call_apply():
    """After abort, router.apply() is never called."""
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": [_make_content_item("p1")]},
    )
    svc = _make_mock_build_service(router=router)
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="n"),
    ):
        _run_reset()
    router.apply.assert_not_called()


def test_abort_does_not_call_set_active_css():
    """After abort, set_active_css() is never called."""
    theme = MagicMock()
    svc = _make_mock_build_service(theme_root="/theme")
    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        patch("builtins.input", return_value="n"),
    ):
        _run_reset()
    theme.set_active_css.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Content reset behaviour
# ---------------------------------------------------------------------------

def test_content_reset_deletes_all_items():
    """Every enumerated item produces a DELETE operation."""
    from cauldron_content.contracts import ContentOperationKind

    items = [_make_content_item("a"), _make_content_item("b"), _make_content_item("c")]
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": items},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True)

    router.apply.assert_called_once()
    changeset = router.apply.call_args[0][0]
    assert len(changeset.operations) == 3
    for op in changeset.operations:
        assert op.kind == ContentOperationKind.DELETE


def test_content_reset_uses_include_drafts():
    """list_items() is called with include_drafts=True."""
    router = _make_mock_router(collections=[_make_collection_info("pages")])
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    router.list_items.assert_called_with("pages", include_drafts=True)


def test_content_reset_with_no_items_is_zero_and_idempotent():
    """No items → zero reported, apply() never called, command succeeds."""
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": []},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        stdout, stderr = _run_reset(yes=True, content=True)

    router.apply.assert_not_called()
    assert "0 content item(s)" in stdout
    assert stderr == ""


def test_content_reset_spans_multiple_collections():
    """Items from every collection land in one changeset."""
    pages = [_make_content_item("p1", collection="pages")]
    posts = [
        _make_content_item("b1", collection="posts"),
        _make_content_item("b2", collection="posts"),
    ]
    router = _make_mock_router(
        collections=[_make_collection_info("pages"), _make_collection_info("posts")],
        items_by_collection={"pages": pages, "posts": posts},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    changeset = router.apply.call_args[0][0]
    assert len(changeset.operations) == 3
    assert {op.collection for op in changeset.operations} == {"pages", "posts"}


def test_content_reset_uses_item_hash_for_optimistic_concurrency():
    """Each DELETE operation carries the enumerated item hash, not an empty hash."""
    item = _make_content_item("x", hash_val="expected-hash-abc")
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": [item]},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    op = router.apply.call_args[0][0].operations[0]
    assert op.expected_hash == "expected-hash-abc"


def test_content_reset_force_is_false():
    """DELETE operations must not use force=True."""
    item = _make_content_item("x")
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": [item]},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    op = router.apply.call_args[0][0].operations[0]
    assert op.force is False


# ---------------------------------------------------------------------------
# 4. Content apply failure — hard fail
# ---------------------------------------------------------------------------

def test_failed_apply_exits_nonzero():
    """router.apply() returning success=False raises SystemExit(1)."""
    items = [_make_content_item("p1")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
        apply_result=_make_apply_result(success=False),
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        with pytest.raises(SystemExit) as exc_info:
            _run_reset(yes=True, content=True)

    assert exc_info.value.code == 1


def test_failed_apply_never_prints_success():
    """A failed apply must not print the success summary."""
    items = [_make_content_item("p1")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
        apply_result=_make_apply_result(success=False),
    )
    svc = _make_mock_build_service(router=router)
    stdout = StringIO()

    with _patch_build_service(svc), _patch_site_config():
        with pytest.raises(SystemExit):
            call_command("cauldron_site_reset", yes=True, content=True, stdout=stdout)

    assert "Website reset complete" not in stdout.getvalue()


def test_failed_apply_does_not_claim_items_removed():
    """A failed apply must not report any items as successfully removed."""
    items = [_make_content_item("p1"), _make_content_item("p2")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
        apply_result=_make_apply_result(success=False),
    )
    svc = _make_mock_build_service(router=router)
    stdout = StringIO()

    with _patch_build_service(svc), _patch_site_config():
        with pytest.raises(SystemExit):
            call_command("cauldron_site_reset", yes=True, content=True, stdout=stdout)

    out = stdout.getvalue()
    assert "2 content item(s) removed" not in out
    assert "1 content item(s) removed" not in out


def test_failed_apply_writes_error_to_stderr():
    """Apply failure details are written to stderr."""
    from cauldron_content.contracts import Conflict

    conflict = Conflict(
        item_id="p1",
        collection="pages",
        expected_hash="old",
        actual_hash="new",
        message="Hash mismatch",
    )
    items = [_make_content_item("p1")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
        apply_result=_make_apply_result(success=False, conflicts=[conflict]),
    )
    svc = _make_mock_build_service(router=router)
    stderr = StringIO()

    with _patch_build_service(svc), _patch_site_config():
        with pytest.raises(SystemExit):
            call_command("cauldron_site_reset", yes=True, content=True, stderr=stderr)

    assert "conflict" in stderr.getvalue().lower()


# ---------------------------------------------------------------------------
# 5. Style reset behaviour
# ---------------------------------------------------------------------------

def test_styles_reset_calls_set_active_css_empty():
    """set_active_css('') is called during a styles reset."""
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        _run_reset(yes=True, styles=True)

    theme.set_active_css.assert_any_call("")


def test_styles_reset_calls_discard_staged():
    """discard_staged() is called during a styles reset."""
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        _run_reset(yes=True, styles=True)

    theme.discard_staged.assert_called_once()


def test_styles_reset_skipped_when_no_theme_root():
    """When theme_root is not configured, style reset succeeds without error."""
    svc = _make_mock_build_service(theme_root="")

    with _patch_build_service(svc), _patch_site_config(theme_root=""):
        stdout, stderr = _run_reset(yes=True, styles=True)

    assert "Website reset complete" in stdout
    assert stderr == ""


def test_style_state_restored_after_rebuild_failure():
    """Prior active CSS is restored when rebuild fails after style reset."""
    theme = MagicMock()
    theme.get_active_css.return_value = "body { color: red; }"
    theme.get_staged_css.return_value = "body { color: blue; }"
    svc = _make_mock_build_service(
        build_result=_make_build_result(ok=False, error="Astro failed"),
        theme_root="/theme",
    )

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        pytest.raises(SystemExit),
    ):
        _run_reset(yes=True, styles=True)

    # Active CSS is restored to its prior value.
    theme.set_active_css.assert_any_call("body { color: red; }")
    # Staged CSS is re-staged.
    theme.stage_css.assert_called_once_with("body { color: blue; }")


def test_style_state_restored_when_nothing_was_staged():
    """When nothing was staged, only active CSS is restored; stage_css not called."""
    theme = MagicMock()
    theme.get_active_css.return_value = "body { color: red; }"
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(
        build_result=_make_build_result(ok=False, error="Astro failed"),
        theme_root="/theme",
    )

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
        pytest.raises(SystemExit),
    ):
        _run_reset(yes=True, styles=True)

    theme.set_active_css.assert_any_call("body { color: red; }")
    theme.stage_css.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Service resolution failure (before mutation)
# ---------------------------------------------------------------------------

def test_styles_config_failure_prevents_content_mutation():
    """If theme config raises during pre-resolution, content is not deleted."""
    items = [_make_content_item("p1")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
    )
    svc = _make_mock_build_service(router=router)

    with (
        _patch_build_service(svc),
        patch(
            "cauldron_site_astro.config.get_site_astro_config",
            side_effect=RuntimeError("config broken"),
        ),
        pytest.raises(RuntimeError),
    ):
        _run_reset(yes=True)  # --all by default

    # Service resolution failure happens before any router mutation.
    router.apply.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Rebuild behaviour
# ---------------------------------------------------------------------------

def test_build_called_after_reset():
    """build() is always called after a successful reset."""
    svc = _make_mock_build_service()

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True)

    svc.build.assert_called_once()


def test_build_failure_raises_system_exit_1():
    """If the rebuild fails, SystemExit(1) is raised."""
    svc = _make_mock_build_service(
        build_result=_make_build_result(ok=False, error="Astro failed")
    )

    with _patch_build_service(svc), _patch_site_config():
        with pytest.raises(SystemExit) as exc_info:
            _run_reset(yes=True)

    assert exc_info.value.code == 1


def test_build_failure_writes_error_to_stderr():
    """Build failure message is written to stderr."""
    svc = _make_mock_build_service(
        build_result=_make_build_result(ok=False, error="npm not found")
    )
    stderr = StringIO()

    with _patch_build_service(svc), _patch_site_config():
        with pytest.raises(SystemExit):
            call_command("cauldron_site_reset", yes=True, stderr=stderr)

    assert "npm not found" in stderr.getvalue()


# ---------------------------------------------------------------------------
# 8. Success summaries — only selected scopes
# ---------------------------------------------------------------------------

def test_success_summary_contains_item_count():
    """Summary reports the number of content items removed."""
    items = [_make_content_item("a"), _make_content_item("b")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        stdout, _ = _run_reset(yes=True)

    assert "2 content item(s) removed" in stdout


def test_success_summary_contains_styles_reset_when_styles_cleared():
    """Summary includes 'styles reset' when styles were selected."""
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        stdout, _ = _run_reset(yes=True)

    assert "styles reset" in stdout


def test_content_only_summary_omits_styles():
    """--content only: summary must not mention 'styles reset'."""
    router = _make_mock_router()
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        stdout, _ = _run_reset(yes=True, content=True)

    assert "styles reset" not in stdout


def test_styles_only_summary_omits_content_count():
    """--styles only: summary must not mention content item count."""
    theme = MagicMock()
    theme.get_active_css.return_value = ""
    theme.get_staged_css.return_value = None
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        stdout, _ = _run_reset(yes=True, styles=True)

    assert "content item(s)" not in stdout
    assert "styles reset" in stdout
    assert "public site rebuilt" in stdout


def test_success_summary_always_contains_public_site_rebuilt():
    """Summary always ends with 'public site rebuilt'."""
    svc = _make_mock_build_service()

    with _patch_build_service(svc), _patch_site_config():
        stdout, _ = _run_reset(yes=True)

    assert "public site rebuilt" in stdout


def test_success_exits_0():
    """A fully successful reset does not raise SystemExit."""
    svc = _make_mock_build_service()

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True)  # must not raise


# ---------------------------------------------------------------------------
# 9. Integration test — real flat-file ContentRouter
# ---------------------------------------------------------------------------

def test_integration_content_reset_with_real_flatfile_router(tmp_path):
    """Content reset removes both published and draft items through a real ContentRouter.

    This test uses genuine FlatFileRepository/ContentRouter infrastructure
    (already a test dependency of cauldron-site-astro) without any mocking
    of the content layer.  Only the Astro build subprocess is mocked.

    Proves:
    - Published items are removed.
    - Draft items are removed (include_drafts=True is respected).
    - The router reports zero items after reset.
    - The success summary mentions the correct item count.
    """
    import json

    from cauldron_cms_flatfile.config import FlatFileCMSConfig
    from cauldron_cms_flatfile.repository import FlatFileRepository
    from cauldron_content.registry import RepositoryRegistry
    from cauldron_content.router import ContentRouter, RouterConfig
    from cauldron_site_astro.service import BuildResult

    # ------------------------------------------------------------------
    # Build a minimal flat-file site with one published and one draft page.
    # ------------------------------------------------------------------
    site = tmp_path / "site"
    (site / "content" / "pages").mkdir(parents=True)
    (site / "schemas").mkdir(parents=True)
    (site / "schemas" / "page.schema.json").write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "page",
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        }),
        encoding="utf-8",
    )

    (site / "content" / "pages" / "pub.md").write_text(
        "---\nid: pub\nslug: pub\nschema: page\nstatus: published\ntitle: Published\n---\n\n# Body\n",
        encoding="utf-8",
    )
    (site / "content" / "pages" / "draft.md").write_text(
        "---\nid: draft\nslug: draft\nschema: page\nstatus: draft\ntitle: Draft\n---\n\n# Draft body\n",
        encoding="utf-8",
    )

    # ------------------------------------------------------------------
    # Wire a real ContentRouter.
    # ------------------------------------------------------------------
    repo = FlatFileRepository(FlatFileCMSConfig(site_root=site))
    registry = RepositoryRegistry()
    registry.register("flatfile", repo)
    router = ContentRouter(registry, RouterConfig(default_provider="flatfile"))

    # Confirm both items exist before reset.
    items_before = router.list_items("pages", include_drafts=True)
    assert len(items_before) == 2

    # ------------------------------------------------------------------
    # Mock only the build (no real Astro process).
    # ------------------------------------------------------------------
    mock_svc = MagicMock()
    mock_svc._router = router
    mock_svc.build.return_value = BuildResult(
        ok=True, pages_built=0, output_dir="/out"
    )

    with (
        _patch_build_service(mock_svc),
        _patch_site_config(theme_root=""),
    ):
        stdout, stderr = _run_reset(yes=True, content=True)

    # Both items must be gone.
    items_after = router.list_items("pages", include_drafts=True)
    assert items_after == [], (
        f"Expected 0 items after reset, got {[i.id for i in items_after]}"
    )

    assert "2 content item(s) removed" in stdout
    assert stderr == ""
    assert "Website reset complete" in stdout

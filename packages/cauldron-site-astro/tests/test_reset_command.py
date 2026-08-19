"""Tests for the cauldron_site_reset management command.

22 scenarios covering: flag defaults, mutual exclusion, confirmation
prompts, content deletion, style clearing, rebuild, summary wording,
and failure handling.
"""
from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, call, patch

import pytest
from django.core.management import call_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_build_result(ok: bool = True, pages_built: int = 0, error: str = ""):
    from cauldron_site_astro.service import BuildResult
    return BuildResult(ok=ok, pages_built=pages_built, output_dir="/out", error=error)


def _make_content_item(item_id: str, collection: str = "pages", provider: str = "flatfile"):
    from cauldron_content.contracts import ContentItem, ContentStatus
    return ContentItem(
        id=item_id,
        collection=collection,
        slug=item_id,
        status=ContentStatus.PUBLISHED,
        schema="page",
        data={},
        body="",
        hash="abc",
        provider=provider,
    )


def _make_apply_result(success: bool = True):
    from cauldron_content.contracts import ApplyResult
    return ApplyResult(success=success, applied=(), conflicts=(), validation_errors=())


def _make_collection_info(name: str = "pages", provider: str = "flatfile"):
    from cauldron_content.router import CollectionInfo
    return CollectionInfo(name=name, schema="page", provider=provider, item_count=None)


def _make_mock_router(collections=None, items_by_collection=None, apply_result=None):
    """Build a mock ContentRouter.

    collections: list[CollectionInfo]
    items_by_collection: dict[str, list[ContentItem]] keyed by collection name
    apply_result: ApplyResult (defaults to success)
    """
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
    """Invoke cauldron_site_reset and return (stdout, stderr)."""
    stdout = StringIO()
    stderr = StringIO()
    call_command("cauldron_site_reset", stdout=stdout, stderr=stderr, **kwargs)
    return stdout.getvalue(), stderr.getvalue()


def _patch_build_service(svc):
    return patch("cauldron_site_astro.service.get_build_service", return_value=svc)


def _patch_theme_service(mock_theme):
    # Patch at the source module because the command imports inside the method body.
    return patch(
        "cauldron_site_astro.theme.SiteThemeService",
        return_value=mock_theme,
    )


def _patch_site_config(theme_root=""):
    cfg = MagicMock()
    cfg.theme_root = theme_root
    return patch(
        "cauldron_site_astro.config.get_site_astro_config",
        return_value=cfg,
    )


# ---------------------------------------------------------------------------
# 1. Flag defaults
# ---------------------------------------------------------------------------

def test_no_flags_defaults_to_all():
    """Running with no flags resets both content and styles."""
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
        stdout, _ = _run_reset()

    # Content and styles both reset
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
# 2. Interactive confirmation
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
    # The destructive-action warning is printed before the prompt
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
    """Entering 'y' at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="y"),
    ):
        stdout, _ = _run_reset()

    assert "Website reset complete" in stdout


def test_uppercase_Y_proceeds():
    """Entering 'Y' at the prompt allows the reset to continue."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="Y"),
    ):
        stdout, _ = _run_reset()

    assert "Website reset complete" in stdout


def test_lowercase_n_aborts():
    """Entering 'n' at the prompt aborts without any changes."""
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
    """Entering 'N' at the prompt aborts."""
    svc = _make_mock_build_service()
    with (
        _patch_build_service(svc),
        _patch_site_config(),
        patch("builtins.input", return_value="N"),
    ):
        stdout, _ = _run_reset()

    assert "Aborted" in stdout


def test_empty_input_aborts():
    """Pressing Enter (empty string) at the prompt aborts — default is N."""
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
    """Every item returned by the router is included in a DELETE operation."""
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
    """list_items() is called with include_drafts=True to catch draft items."""
    router = _make_mock_router(collections=[_make_collection_info("pages")])
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    router.list_items.assert_called_with("pages", include_drafts=True)


def test_content_reset_with_no_items_returns_zero():
    """When there are no items, zero is reported and apply() is not called."""
    router = _make_mock_router(
        collections=[_make_collection_info("pages")],
        items_by_collection={"pages": []},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        stdout, _ = _run_reset(yes=True, content=True)

    router.apply.assert_not_called()
    assert "0 content item(s)" in stdout


def test_content_reset_spans_multiple_collections():
    """Items from every collection are gathered and deleted in one changeset."""
    from cauldron_content.contracts import ContentOperationKind

    pages = [_make_content_item("p1", collection="pages")]
    posts = [_make_content_item("b1", collection="posts"), _make_content_item("b2", collection="posts")]
    router = _make_mock_router(
        collections=[_make_collection_info("pages"), _make_collection_info("posts")],
        items_by_collection={"pages": pages, "posts": posts},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    changeset = router.apply.call_args[0][0]
    assert len(changeset.operations) == 3
    collections_in_ops = {op.collection for op in changeset.operations}
    assert collections_in_ops == {"pages", "posts"}


def test_content_reset_sets_force_true():
    """Each DELETE operation has force=True."""
    items = [_make_content_item("x")]
    router = _make_mock_router(
        collections=[_make_collection_info()],
        items_by_collection={"pages": items},
    )
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True, content=True)

    op = router.apply.call_args[0][0].operations[0]
    assert op.force is True


# ---------------------------------------------------------------------------
# 4. Style reset behaviour
# ---------------------------------------------------------------------------

def test_styles_reset_calls_set_active_css_empty():
    """set_active_css('') is called during a styles reset."""
    theme = MagicMock()
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        _run_reset(yes=True, styles=True)

    theme.set_active_css.assert_called_once_with("")


def test_styles_reset_calls_discard_staged():
    """discard_staged() is called during a styles reset."""
    theme = MagicMock()
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        _run_reset(yes=True, styles=True)

    theme.discard_staged.assert_called_once()


def test_styles_reset_skipped_when_no_theme_root():
    """When theme_root is not configured, style reset completes without error."""
    svc = _make_mock_build_service(theme_root="")

    with _patch_build_service(svc), _patch_site_config(theme_root=""):
        # Must not raise
        stdout, stderr = _run_reset(yes=True, styles=True)

    assert "Website reset complete" in stdout
    assert stderr == ""


# ---------------------------------------------------------------------------
# 5. Rebuild behaviour
# ---------------------------------------------------------------------------

def test_build_called_after_reset():
    """get_build_service().build() is always called after a successful reset."""
    svc = _make_mock_build_service()

    with _patch_build_service(svc), _patch_site_config():
        _run_reset(yes=True)

    svc.build.assert_called_once()


def test_build_failure_raises_system_exit_1():
    """If the rebuild fails, SystemExit(1) is raised."""
    svc = _make_mock_build_service(build_result=_make_build_result(ok=False, error="Astro failed"))

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
# 6. Summary message
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
    """Summary includes 'styles reset' when styles were cleared."""
    theme = MagicMock()
    svc = _make_mock_build_service(theme_root="/theme")

    with (
        _patch_build_service(svc),
        _patch_theme_service(theme),
        _patch_site_config(theme_root="/theme"),
    ):
        stdout, _ = _run_reset(yes=True)

    assert "styles reset" in stdout


def test_success_summary_omits_styles_reset_for_content_only():
    """When only --content is used, 'styles reset' does not appear in summary."""
    router = _make_mock_router()
    svc = _make_mock_build_service(router=router)

    with _patch_build_service(svc), _patch_site_config():
        stdout, _ = _run_reset(yes=True, content=True)

    assert "styles reset" not in stdout


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
        # Must not raise
        _run_reset(yes=True)

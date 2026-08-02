"""Built-in prompt templates for Cauldron Site Astro AI tools.

Registered in CauldronSiteAstroConfig.ready() alongside the tool definitions.
Idempotent: re-registering identical instances is a no-op.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cauldron_ai.prompt_templates import AIToolPromptTemplate

_OWNING_MODULE = "cauldron.site.astro"


def _get_builtin_templates() -> tuple:
    """Construct built-in prompt templates lazily.

    Deferred so this module can be safely imported without the optional
    ``cauldron-ai`` dependency. Callers must ensure ``cauldron_ai`` is
    installed (apps.py guards with ``import cauldron_ai`` before calling
    :func:`register_builtin_site_tool_prompts`).
    """
    try:
        from cauldron_ai.prompt_templates import AIToolPromptTemplate
    except ImportError:
        return ()
    return (
        AIToolPromptTemplate(
            tool_name="site.inspect",
            template_version="v1",
            owning_module=_OWNING_MODULE,
            purpose=(
                "Inspect the current public site build status: whether a live "
                "build exists and whether a staged theme CSS is pending."
            ),
            supported_tasks=("site status check", "pre-publish inspection"),
            required_permission="cauldron_content_operations.view_published_content",
            risk_level="READ_ONLY",
            read_scope=(
                "Whether a live build exists, whether a staged theme is pending, "
                "and high-level build metadata. No file contents returned."
            ),
            write_scope="None.",
            preconditions=("Actor has view_published_content permission.",),
            input_expectations="No arguments required.",
            result_behavior=(
                "Returns build_exists (boolean), staged_theme_pending (boolean), "
                "and optional metadata about the current live build."
            ),
            approval_requirements="None; read-only.",
            clarification_behavior=(
                "If the actor asks whether the site is live, use this tool first "
                "before making assumptions. A missing build means the site has "
                "never been published — not that it is down."
            ),
            refusal_behavior="Refuse if the actor lacks view_published_content permission.",
            error_guidance=(
                "On error, report the error code and suggest checking system logs."
            ),
            positive_examples=(
                "Check whether the public site is currently live.",
                "Inspect site status before recommending a publish.",
            ),
            boundary_examples=(
                "Do not infer page content from the build status.",
                "Do not assume a staged theme is live until publish is confirmed.",
            ),
        ),
        AIToolPromptTemplate(
            tool_name="site.stage_theme",
            template_version="v1",
            owning_module=_OWNING_MODULE,
            purpose=(
                "Stage a CSS stylesheet as the proposed public-site theme. "
                "The CSS is NOT applied to the live site until site.publish is called."
            ),
            supported_tasks=("theme proposal", "CSS staging"),
            required_permission="cauldron_content_operations.propose_content_changes",
            risk_level="PROPOSE",
            read_scope="None.",
            write_scope=(
                "Stores a proposed CSS stylesheet as the pending staged theme. "
                "Has no effect on the live site until site.publish succeeds."
            ),
            preconditions=(
                "Actor has propose_content_changes permission.",
                "The user has explicitly requested a theme change.",
            ),
            input_expectations=(
                "Requires 'css_content' (string, full CSS). "
                "Optional: 'description' (string)."
            ),
            result_behavior=(
                "Returns confirmation that the theme CSS has been staged. "
                "The staged CSS is only promoted on a successful site.publish."
            ),
            approval_requirements=(
                "The staged theme is held in a proposal state until site.publish "
                "succeeds. A failed build never promotes the staged CSS."
            ),
            clarification_behavior=(
                "Confirm the CSS content with the user before staging. "
                "If the request is ambiguous, ask for the full stylesheet."
            ),
            refusal_behavior=(
                "Refuse if the user has not explicitly requested a theme change. "
                "Refuse if css_content is empty or invalid."
            ),
            error_guidance=(
                "On validation error, report the specific CSS problem. "
                "Do not retry automatically without user confirmation."
            ),
            positive_examples=(
                "Stage a new primary-colour CSS override for the public site.",
            ),
            boundary_examples=(
                "Do not claim the theme is live until site.publish is confirmed.",
                "Do not stage themes the user has not explicitly requested.",
                "Do not pass css_content to site.prepare_change_set — the staged "
                "CSS is included automatically.",
            ),
        ),
        AIToolPromptTemplate(
            tool_name="site.prepare_change_set",
            template_version="v1",
            owning_module=_OWNING_MODULE,
            purpose=(
                "Create a SiteChangeSet from content change requests plus an "
                "optional staged theme CSS, and build a scoped preview. "
                "Does not touch the live site."
            ),
            supported_tasks=("preview build", "change-set preparation", "draft review"),
            required_permission="cauldron_content_operations.propose_content_changes",
            risk_level="PROPOSE",
            read_scope=(
                "Reads published content baseline and draft operations for the "
                "specified change requests."
            ),
            write_scope=(
                "Creates a SiteChangeSet record and writes a scoped preview build "
                "to disk. No effect on the live site or live theme."
            ),
            preconditions=(
                "Actor has propose_content_changes permission.",
                "All content_request_ids must exist and be in a proposed/validated state.",
            ),
            input_expectations=(
                "Requires 'content_request_ids' (non-empty list of strings). "
                "Optional: 'description' (string). "
                "Do NOT pass 'theme_css' — any CSS previously staged via "
                "site.stage_theme is loaded automatically."
            ),
            result_behavior=(
                "Returns change_set_id (UUID string), preview_url (Django URL path), "
                "and pages_built (integer). "
                "The preview shows the published baseline overlaid with only the "
                "specified drafts — unrelated in-flight drafts are excluded. "
                "The build runs synchronously; the result reflects the final "
                "draft_ready or preview_failed state. "
                "site.inspect_preview can be used to revisit the status of an "
                "existing change set."
            ),
            approval_requirements=(
                "Prepare is a propose-level operation. Publishing the change set "
                "always requires explicit user confirmation via site.publish."
            ),
            clarification_behavior=(
                "Always use this tool to create a preview before recommending publish. "
                "Report the preview_url to the user for their review. "
                "If the actor previously staged theme CSS, it will be included "
                "in the preview automatically — do not ask them to re-supply it."
            ),
            refusal_behavior=(
                "Refuse if content_request_ids is empty. "
                "Refuse if any request ID is not found or invalid."
            ),
            error_guidance=(
                "On build failure (success=false), report the error and do not proceed "
                "to publish. The actor may retry site.prepare_change_set after fixing "
                "the underlying content issue."
            ),
            positive_examples=(
                "Prepare a preview for change requests ['req-abc', 'req-def'].",
                "Bundle two content proposals into a change set — staged theme CSS "
                "is included automatically.",
            ),
            boundary_examples=(
                "Do not pass theme_css — staged CSS is auto-loaded.",
                "Do not proceed to site.publish without first confirming the preview "
                "with the user.",
                "Do not include change requests that have already been published.",
            ),
        ),
        AIToolPromptTemplate(
            tool_name="site.inspect_preview",
            template_version="v1",
            owning_module=_OWNING_MODULE,
            purpose=(
                "Inspect a previously prepared SiteChangeSet preview. "
                "Returns status, page count, and the Django preview URL."
            ),
            supported_tasks=("preview status check", "build result inspection"),
            required_permission="cauldron_content_operations.view_published_content",
            risk_level="READ_ONLY",
            read_scope=(
                "Status and metadata of the specified SiteChangeSet preview. "
                "No file contents or raw paths returned."
            ),
            write_scope="None.",
            preconditions=(
                "Actor has view_published_content permission.",
                "A change_set_id from site.prepare_change_set must be known.",
            ),
            input_expectations="Requires 'change_set_id' (UUID string).",
            result_behavior=(
                "Returns status ('building', 'draft_ready', 'preview_failed'), "
                "page_count (integer or null), and preview_url (Django URL path)."
            ),
            approval_requirements="None; read-only.",
            clarification_behavior=(
                "Use this tool to revisit a previously prepared change set or to "
                "re-read status after returning to a workflow. "
                "site.prepare_change_set already returns the final status synchronously, "
                "so polling is not needed immediately after preparation. "
                "Report the preview_url to the user when status is 'draft_ready'."
            ),
            refusal_behavior=(
                "Refuse if change_set_id is not found or does not belong to the actor."
            ),
            error_guidance=(
                "On 'preview_failed' status, report the failure to the user and "
                "do not proceed to publish. Suggest reviewing content and retrying "
                "site.prepare_change_set."
            ),
            positive_examples=(
                "Check the build status of change set 'cs-uuid-123'.",
                "Poll until the preview is ready before reporting the URL.",
            ),
            boundary_examples=(
                "Do not expose internal build paths or filesystem locations.",
                "Do not proceed to publish if status is not 'draft_ready'.",
            ),
        ),
        AIToolPromptTemplate(
            tool_name="site.publish",
            template_version="v1",
            owning_module=_OWNING_MODULE,
            purpose=(
                "Publish a draft-ready SiteChangeSet to the live site: apply "
                "its content change requests, rebuild the site with the staged "
                "theme, and promote both atomically. Requires confirm=true."
            ),
            supported_tasks=("live publish", "content promotion", "theme promotion"),
            required_permission="cauldron_content_operations.apply_content_changes",
            risk_level="PROPOSE",
            read_scope=(
                "Reads the SiteChangeSet record and its associated content requests."
            ),
            write_scope=(
                "Applies content change requests to the canonical store, "
                "rebuilds the live site, and atomically promotes the staged theme "
                "CSS if the build succeeds. A failed build leaves the live site "
                "and live theme untouched."
            ),
            preconditions=(
                "Actor has apply_content_changes permission.",
                "The SiteChangeSet must have status 'draft_ready'.",
                "The user must have explicitly confirmed the publish action.",
                "confirm=true must be set in the tool call.",
            ),
            input_expectations=(
                "Requires 'change_set_id' (UUID string) and 'confirm' (boolean, must be true)."
            ),
            result_behavior=(
                "On success: data includes published=true, live_url (the live site "
                "root, typically '/'), pages_built (integer), and change_set_id. "
                "On any failure: data includes published=false and change_set_id; "
                "the live site and live theme are left untouched. "
                "Staged theme CSS is only promoted on a successful build."
            ),
            approval_requirements=(
                "Publish is an irreversible action that affects the live site. "
                "Always require explicit user confirmation before calling this tool. "
                "Never call site.publish autonomously or without confirm=true."
            ),
            clarification_behavior=(
                "Always present the preview_url from site.inspect_preview to the "
                "user and ask for explicit confirmation before publishing. "
                "If the user has not reviewed the preview, do not publish."
            ),
            refusal_behavior=(
                "Refuse if confirm is not explicitly true. "
                "Refuse if the user has not confirmed the action. "
                "Refuse if the change set status is not 'draft_ready'."
            ),
            error_guidance=(
                "On build failure, report that the live site was not changed. "
                "Suggest reviewing content proposals and retrying site.prepare_change_set "
                "to get a new preview before attempting publish again."
            ),
            positive_examples=(
                "Publish change set 'cs-uuid-123' after the user confirms the preview.",
            ),
            boundary_examples=(
                "Never call site.publish without explicit user confirmation.",
                "Never claim the site is updated until published=true is returned.",
                "Do not publish if the preview build failed.",
            ),
        ),
    )


def register_builtin_site_tool_prompts() -> None:
    """Register built-in prompt templates for all site tools.

    Called from CauldronSiteAstroConfig.ready(). Idempotent: re-registering
    identical instances is a silent no-op.
    """
    try:
        from cauldron_ai.prompt_templates import register_tool_template
    except ImportError:
        return
    for tmpl in _get_builtin_templates():
        register_tool_template(tmpl)

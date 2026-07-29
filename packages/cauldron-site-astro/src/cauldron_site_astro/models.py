"""Django models for cauldron-site-astro."""
from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class SiteChangeSet(models.Model):
    """Aggregate that tracks a cohesive set of content + theme changes.

    A SiteChangeSet groups one or more content change-request IDs with an
    optional staged theme CSS snippet and links them to an originating Admin
    AI run so the authoring workflow can surface a single ``draft_ready`` or
    ``published`` status rather than requiring the user to track individual
    change requests.
    """

    PREPARING = "preparing"
    DRAFT_READY = "draft_ready"
    PREVIEW_FAILED = "preview_failed"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"

    STATUS_CHOICES = [
        (PREPARING, "Preparing"),
        (DRAFT_READY, "Draft Ready"),
        (PREVIEW_FAILED, "Preview Failed"),
        (PUBLISHING, "Publishing"),
        (PUBLISHED, "Published"),
        (PUBLISH_FAILED, "Publish Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default=PREPARING, db_index=True,
    )

    # Content change requests included in this change set
    content_request_ids = models.JSONField(default=list, blank=True)

    # Staged public-site theme CSS (empty means no theme change)
    staged_theme_css = models.TextField(blank=True)

    # Originating Admin AI run (optional — populated when created by AI)
    originating_run_id = models.UUIDField(null=True, blank=True, db_index=True)

    # Actor who created this change set
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_change_sets",
    )

    # Preview build location (relative path beneath the configured previews root)
    preview_dir = models.CharField(max_length=512, blank=True)

    # Affected page routes (informational — populated at prepare time)
    page_routes = models.JSONField(default=list, blank=True)

    # Content item ids affected by this change set (extracted from operations
    # of the referenced ContentChangeRequests). Used to scope preview builds
    # so drafts belonging to unrelated in-flight work are not surfaced.
    affected_item_ids = models.JSONField(default=list, blank=True)

    # Build result payloads
    publish_build_result = models.JSONField(default=dict, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    draft_ready_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "cauldron_site_astro"
        ordering = ["-created_at"]
        verbose_name = "Site Change Set"
        verbose_name_plural = "Site Change Sets"

    def __str__(self) -> str:
        return f"SiteChangeSet({self.id}, {self.status})"

    def get_preview_url(self) -> str:
        """Return the Django URL path where this change set's preview is served.

        The path is always a Django URL (starts with ``/``), never a filesystem
        path — this is the URL that gets exposed to admin tools and users.
        """
        from django.urls import reverse
        return reverse(
            "cauldron_site_astro:preview-home",
            kwargs={"change_set_id": str(self.id)},
        )

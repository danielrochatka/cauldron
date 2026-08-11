"""Django signals emitted by cauldron-site-astro."""
import django.dispatch

# Emitted by SiteChangeSetService.publish() after a changeset transitions to
# PUBLISHED.  Handlers that need to react to a successful publish (e.g.
# marking a UI style proposal as applied) connect here.
#
# Provided kwargs:
#   changeset_id   (str)  — UUID of the published SiteChangeSet
#   staged_theme_css (str) — the CSS that was promoted to active.css
#   style_request_id (str) — UIStyleChangeRequest.request_id associated with
#                            this changeset, or "" if this is a content-only
#                            publish
site_changeset_published = django.dispatch.Signal()

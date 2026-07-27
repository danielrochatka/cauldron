"""URL patterns for cauldron_admin_content."""
from django.urls import path
from . import views

app_name = "cauldron_admin_content"

urlpatterns = [
    # Content Browser
    path("content/", views.ContentBrowserView.as_view(), name="content-browser"),
    path("content-browser/", views.ContentBrowserRedirectView.as_view()),  # compat redirect

    # Homepage singleton — must be before generic page routes
    path("content/homepage/", views.HomepageView.as_view(), name="homepage"),

    # Page authoring (primary interface)
    path("content/pages/new/", views.PageCreateView.as_view(), name="page-create"),
    path("content/pages/<str:item_id>/", views.PageDetailView.as_view(), name="page-detail"),
    path("content/pages/<str:item_id>/edit/", views.PageEditView.as_view(), name="page-edit"),

    # Generic proposal form (advanced / technical interface)
    path("content-proposal/", views.ContentProposalView.as_view(), name="content-proposal"),

    # Change requests and audit
    path("content/change-requests/", views.ChangeRequestListView.as_view(), name="change-request-list"),
    path("content/change-requests/<str:request_id>/", views.ChangeRequestDetailView.as_view(), name="change-request-detail"),
    path("content/audit/", views.AuditListView.as_view(), name="audit-list"),
    path("content/audit/<str:event_id>/", views.AuditDetailView.as_view(), name="audit-detail"),
]

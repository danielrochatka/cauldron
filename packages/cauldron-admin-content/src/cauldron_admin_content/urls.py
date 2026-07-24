"""URL patterns for cauldron_admin_content."""
from django.urls import path
from . import views

app_name = "cauldron_admin_content"

urlpatterns = [
    path("content-browser/", views.ContentBrowserView.as_view(), name="content-browser"),
    path("content-proposal/", views.ContentProposalView.as_view(), name="content-proposal"),
    path("content/change-requests/", views.ChangeRequestListView.as_view(), name="change-request-list"),
    path("content/change-requests/<str:request_id>/", views.ChangeRequestDetailView.as_view(), name="change-request-detail"),
    path("content/audit/", views.AuditListView.as_view(), name="audit-list"),
    path("content/audit/<str:event_id>/", views.AuditDetailView.as_view(), name="audit-detail"),
]

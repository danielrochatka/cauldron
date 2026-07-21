"""URL patterns for cauldron_admin_content."""
from django.urls import path
from . import views

app_name = "cauldron_admin_content"

urlpatterns = [
    path("content-browser/", views.ContentBrowserView.as_view(), name="content-browser"),
    path("content-proposal/", views.ContentProposalView.as_view(), name="content-proposal"),
]

"""URL patterns for cauldron_ai_admin."""
from django.urls import path

from . import views

app_name = "cauldron_ai_admin"

urlpatterns = [
    path("admin/ai/", views.AdminAIPageView.as_view(), name="ai-page"),
    path("admin/ai/runs/", views.AdminAIRunListView.as_view(), name="run-list"),
    path("admin/ai/runs/<uuid:run_id>/", views.AdminAIRunDetailView.as_view(), name="run-detail"),
    path(
        "admin/ai/runs/<uuid:run_id>/invocations/<uuid:invocation_id>/",
        views.AdminAIInvocationDetailView.as_view(),
        name="invocation-detail",
    ),
    path("admin/ai/settings/", views.AdminAISettingsView.as_view(), name="settings"),
    path("ui/style-changes/", views.UIStyleChangeListView.as_view(), name="style-list"),
    path("ui/style-changes/<uuid:request_id>/", views.UIStyleChangeDetailView.as_view(), name="style-detail"),
]

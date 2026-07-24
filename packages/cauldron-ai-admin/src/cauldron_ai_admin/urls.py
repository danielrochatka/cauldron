"""URL patterns for cauldron_ai_admin."""
from django.urls import path

from . import views

app_name = "cauldron_ai_admin"

urlpatterns = [
    path("ai/", views.AdminAIPageView.as_view(), name="ai-page"),
]

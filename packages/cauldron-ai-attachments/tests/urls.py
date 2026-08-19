"""Minimal URL configuration for cauldron-ai-attachments tests."""
from django.urls import include, path

urlpatterns = [
    path(
        "cauldron/admin/ai/attachments/",
        include("cauldron_ai_attachments.urls", namespace="cauldron_ai_attachments"),
    ),
]

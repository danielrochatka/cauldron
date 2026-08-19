"""URL configuration for cauldron_ai_attachments."""
from django.urls import path

from . import views

app_name = "cauldron_ai_attachments"

urlpatterns = [
    path("upload/", views.AttachmentUploadView.as_view(), name="attachment-upload"),
]

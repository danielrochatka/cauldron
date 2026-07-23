"""URL patterns for cauldron_content_api."""
from django.urls import path

from . import views

app_name = "cauldron_content_api"

urlpatterns = [
    path("collections/", views.CollectionsView.as_view(), name="collections-list"),
    path("collections/<str:collection>/items/", views.CollectionItemsView.as_view(), name="collection-items"),
    path("collections/<str:collection>/items/<str:item_id>/", views.CollectionItemDetailView.as_view(), name="collection-item-detail"),
    path("change-requests/", views.ChangeRequestListView.as_view(), name="change-requests-list"),
    path("change-requests/<str:request_id>/", views.ChangeRequestDetailView.as_view(), name="change-request-detail"),
    path("change-requests/<str:request_id>/preview/", views.ChangeRequestPreviewView.as_view(), name="change-request-preview"),
    path("change-requests/<str:request_id>/audit/", views.ChangeRequestAuditView.as_view(), name="change-request-audit"),
    path("change-requests/<str:request_id>/validate/", views.ChangeRequestValidateView.as_view(), name="change-request-validate"),
    path("change-requests/<str:request_id>/approve/", views.ChangeRequestApproveView.as_view(), name="change-request-approve"),
    path("change-requests/<str:request_id>/reject/", views.ChangeRequestRejectView.as_view(), name="change-request-reject"),
    path("change-requests/<str:request_id>/apply/", views.ChangeRequestApplyView.as_view(), name="change-request-apply"),
    path("change-requests/<str:request_id>/rollback/", views.ChangeRequestRollbackView.as_view(), name="change-request-rollback"),
]

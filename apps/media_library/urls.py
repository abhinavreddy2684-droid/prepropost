from django.urls import path

from . import views

urlpatterns = [
    path("media/<uuid:pk>/confirm", views.MediaConfirmView.as_view(), name="media-confirm"),
    path("media/<uuid:pk>/cancel", views.MediaCancelView.as_view(), name="media-cancel"),
    path("media/<uuid:pk>/upload-url", views.MediaUploadUrlView.as_view(), name="media-upload-url"),
]

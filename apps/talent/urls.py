from django.urls import path

from . import views

urlpatterns = [
    path("talent/me", views.TalentMeView.as_view(), name="talent-me"),
    path("talent/me/crafts", views.TalentCraftsView.as_view(), name="talent-crafts"),
    path(
        "talent/me/onboarding/complete",
        views.CompleteOnboardingView.as_view(),
        name="talent-onboarding-complete",
    ),
    path("talent/me/publish", views.PublishView.as_view(), name="talent-publish"),
    path("talent/me/experiences", views.ExperienceListView.as_view(), name="talent-experiences"),
    path(
        "talent/me/experiences/<uuid:pk>",
        views.ExperienceDetailView.as_view(),
        name="talent-experience",
    ),
    path("talent/me/availability", views.AvailabilityView.as_view(), name="talent-availability"),
    path("talent/me/gallery", views.GalleryListView.as_view(), name="talent-gallery"),
    path(
        "talent/me/gallery/uploads",
        views.GalleryUploadView.as_view(),
        name="talent-gallery-uploads",
    ),
    path("talent/me/gallery/links", views.GalleryLinkView.as_view(), name="talent-gallery-links"),
    path(
        "talent/me/gallery/<uuid:pk>", views.GalleryItemView.as_view(), name="talent-gallery-item"
    ),
    path("talent/me/avatar", views.AvatarView.as_view(), name="talent-avatar"),
    path(
        "talent/me/avatar/uploads", views.AvatarUploadView.as_view(), name="talent-avatar-uploads"
    ),
    path(
        "talent/me/avatar/submission",
        views.AvatarSubmissionView.as_view(),
        name="talent-avatar-submission",
    ),
    path("talent/<uuid:pk>", views.PublicTalentView.as_view(), name="talent-public"),
    path(
        "talent/<uuid:pk>/gallery", views.PublicGalleryView.as_view(), name="talent-public-gallery"
    ),
    path(
        "gallery-items/<uuid:pk>/like",
        views.GalleryItemLikeView.as_view(),
        name="gallery-item-like",
    ),
]

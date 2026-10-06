from django.urls import path

from . import views

urlpatterns = [
    path("talent/me", views.TalentMeView.as_view(), name="talent-me"),
    path("talent/me/crafts", views.TalentCraftsView.as_view(), name="talent-crafts"),
    path("talent/me/avatar", views.TalentAvatarView.as_view(), name="talent-avatar"),
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
    path("talent/<uuid:pk>", views.PublicTalentView.as_view(), name="talent-public"),
]

from django.urls import path

from . import views

urlpatterns = [
    path("recruiters/me", views.RecruiterMeView.as_view(), name="recruiter-me"),
    path("recruiters/me/submit", views.RecruiterSubmitView.as_view(), name="recruiter-submit"),
    path("admin/recruiters", views.RecruiterReviewListView.as_view(), name="recruiter-review"),
    path(
        "admin/recruiters/<uuid:profile_id>/approve",
        views.RecruiterApproveView.as_view(),
        name="recruiter-approve",
    ),
    path(
        "admin/recruiters/<uuid:profile_id>/reject",
        views.RecruiterRejectView.as_view(),
        name="recruiter-reject",
    ),
]

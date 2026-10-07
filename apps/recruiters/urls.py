from django.urls import path

from . import views

urlpatterns = [
    path("recruiters/me", views.RecruiterMeView.as_view(), name="recruiter-me"),
    path("recruiters/me/submit", views.RecruiterSubmitView.as_view(), name="recruiter-submit"),
]

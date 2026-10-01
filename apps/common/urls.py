from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.liveness, name="liveness"),
    path("readyz", views.readiness, name="readiness"),
]

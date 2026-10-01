from django.urls import path

from .views import BootstrapView

urlpatterns = [path("bootstrap", BootstrapView.as_view(), name="bootstrap")]

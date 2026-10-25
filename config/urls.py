"""Top-level URL routing for HTML and versioned API transports."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("token_budget_platform.transports.html.urls")),
    path("api/v1/", include("token_budget_platform.transports.api.urls")),
]

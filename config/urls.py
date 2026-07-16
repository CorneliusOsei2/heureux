"""URL configuration for the flashcards project."""

from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView


def healthz(request):
    """Lightweight liveness probe for Render (verifies the DB responds)."""
    try:
        connection.ensure_connection()
    except Exception:  # pragma: no cover - only on a broken DB
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})


@never_cache
def service_worker(request):
    response = render(
        request,
        "sw.js",
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response["Service-Worker-Allowed"] = "/"
    return response


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path(
        "sw.js",
        service_worker,
        name="service_worker",
    ),
    path(
        "manifest.webmanifest",
        TemplateView.as_view(
            template_name="manifest.webmanifest",
            content_type="application/manifest+json",
        ),
        name="manifest",
    ),
    path(
        "offline/",
        TemplateView.as_view(template_name="offline.html"),
        name="offline",
    ),
    path("", include("study.urls")),
]

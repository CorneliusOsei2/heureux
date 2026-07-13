"""Custom middleware for the flashcards project."""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse
from django.contrib.auth.views import redirect_to_login
from django.utils.cache import patch_cache_control

HEALTH_CHECK_PATH = "/healthz"


class HealthCheckMiddleware:
    """Answer the platform liveness probe before any host/SSL processing.

    Render performs its health check over the private network with plain HTTP
    and a Host header we cannot predict (often an internal IP). Django's normal
    request path validates that Host — in ``SecurityMiddleware`` (HTTPS redirect)
    and unconditionally in ``CommonMiddleware`` (the ``PREPEND_WWW`` check) — and
    rejects an unknown Host with HTTP 400, so the probe never turns healthy.

    By sitting first in ``MIDDLEWARE`` and matching on ``request.path`` (which is
    derived from ``PATH_INFO`` and never touches the Host header), we short-
    circuit ``/healthz`` to a 200 without weakening ``ALLOWED_HOSTS`` for any real
    traffic.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == HEALTH_CHECK_PATH:
            try:
                connection.ensure_connection()
            except Exception:  # pragma: no cover - only on a broken DB
                return JsonResponse({"status": "error"}, status=503)
            return JsonResponse({"status": "ok"})
        return self.get_response(request)


class AuthenticationRequiredMiddleware:
    """Require an authenticated account for every private study surface."""

    public_paths = {
        "/login/",
        "/register/",
        "/healthz",
        "/manifest.webmanifest",
        "/sw.js",
        "/offline/",
    }
    public_prefixes = ("/admin/", "/static/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_public = request.path in self.public_paths or request.path.startswith(
            self.public_prefixes
        )
        if request.user.is_authenticated:
            response = self.get_response(request)
            patch_cache_control(response, private=True, no_store=True)
            return response
        if is_public:
            return self.get_response(request)
        login_target = (
            "/review/"
            if request.path.startswith("/review/")
            else request.get_full_path()
        )
        login_redirect = redirect_to_login(login_target)
        patch_cache_control(login_redirect, private=True, no_store=True)
        if request.path.startswith("/review/") and request.headers.get(
            "X-Requested-With"
        ):
            response = JsonResponse(
                {
                    "error": "Votre session a expiré. Reconnectez-vous.",
                    "login_url": login_redirect.url,
                },
                status=401,
            )
            patch_cache_control(response, private=True, no_store=True)
            return response
        return login_redirect

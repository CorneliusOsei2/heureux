"""Custom middleware for the flashcards project."""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse

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

"""Template context shared across every page (nav badges, app name)."""

from .models import Card, Settings
from .queue import queue_counts


def study_globals(request):
    counts = queue_counts()
    return {
        "app_name": "Réviser",
        "nav_due_total": counts["due_reviews"] + counts["new_available"],
        "nav_counts": counts,
        "study_settings": Settings.load(),
        "total_cards": Card.objects.count(),
    }

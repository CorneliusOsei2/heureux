"""Template context shared across the authenticated application shell."""

from .models import Response, ReviewSession, Task, Theme
from .queue import queue_counts, scoped_cards


COMPREHENSION_ROUTES = {
    "comprehension_hub",
    "comprehension_overview",
    "comprehension_group",
    "comprehension_test",
    "comprehension_question_study",
    "comprehension_start",
    "comprehension_question",
    "comprehension_results",
}
EXPRESSION_ROUTES = {
    "expression",
    "part_detail",
    "task_detail",
    "task_browse",
    "theme_detail",
    "family_detail",
    "task_family_detail",
    "response_detail",
    "edit_response",
    "task_review_hub",
}
VOCABULARY_ROUTES = {
    "vocabulary",
    "phrases",
    "task_phrases",
}
NOTES_ROUTES = {
    "notes_overview",
    "general_notes",
    "task_notes",
    "annotation_search",
    "annotation_study",
    "task_annotation_study",
}
STATS_ROUTES = {"stats", "task_stats", "stats_overview"}


def _empty_globals():
    return {
        "app_name": "Heureux",
        "annotation_task": None,
        "content_task": None,
        "active_nav_area": "",
        "nav_due_total": 0,
        "nav_counts": {},
        "nav_revisit_count": 0,
        "total_cards": 0,
    }


def _explicit_task(request):
    """Resolve only task scope explicitly encoded by the current page."""
    match = request.resolver_match
    kwargs = match.kwargs if match else {}
    part_slug = kwargs.get("part_slug")
    task_slug = kwargs.get("task_slug")

    data = request.POST if request.method == "POST" else request.GET
    part_slug = part_slug or (data.get("part") or "").strip()
    task_slug = task_slug or (data.get("task") or "").strip()

    if not task_slug and match and match.url_name == "review":
        saved_scope = ReviewSession.load(request.user).scope
        if isinstance(saved_scope, dict):
            part_slug = saved_scope.get("part")
            task_slug = saved_scope.get("task")

    if task_slug:
        tasks = Task.objects.select_related("part").filter(
            slug=task_slug,
            is_active=True,
            part__is_active=True,
        )
        if part_slug:
            tasks = tasks.filter(part__slug=part_slug)
        return tasks.first()

    if match and match.url_name == "theme_detail":
        return (
            Theme.objects.select_related("task__part")
            .filter(slug=kwargs.get("slug"), is_active=True)
            .values_list("task_id", flat=True)
            .first()
        )
    if match and match.url_name in {"response_detail", "edit_response"}:
        return (
            Response.objects.filter(pk=kwargs.get("pk"), is_active=True)
            .values_list("theme__task_id", flat=True)
            .first()
        )
    return None


def _active_nav_area(request):
    match = request.resolver_match
    route_name = match.url_name if match else ""
    if route_name == "dashboard":
        return "home"
    if route_name in COMPREHENSION_ROUTES:
        return "comprehension"
    if route_name in EXPRESSION_ROUTES:
        return "expression"
    if route_name in VOCABULARY_ROUTES:
        return "vocabulary"
    if route_name in NOTES_ROUTES:
        return "notes"
    if route_name in STATS_ROUTES:
        return "stats"
    if route_name in {"review", "revisit_list", "task_revisit_list"}:
        data = request.POST if request.method == "POST" else request.GET
        scope = {
            key: (data.get(key) or "").strip()
            for key in ("kind", "content")
        }
        if not any(scope.values()) and route_name == "review":
            saved_scope = ReviewSession.load(request.user).scope
            if isinstance(saved_scope, dict):
                scope.update(
                    {
                        key: (saved_scope.get(key) or "").strip()
                        for key in ("kind", "content")
                    }
                )
            if not any(scope.values()):
                return "expression"
        if scope["kind"] == "spine" or scope["content"] == "spine":
            return "expression"
        return "vocabulary"
    return ""


def study_globals(request):
    match = request.resolver_match
    if (
        not request.user.is_authenticated
        or not match
        or match.namespace != "study"
    ):
        return _empty_globals()

    task = _explicit_task(request)
    if isinstance(task, int):
        task = Task.objects.select_related("part").filter(
            pk=task,
            is_active=True,
        ).first()
    scope = (
        {"part": task.part.slug, "task": task.slug}
        if task is not None
        else {}
    )
    counts = queue_counts(scope, user=request.user)
    return {
        "app_name": "Heureux",
        "annotation_task": task,
        "content_task": task,
        "active_nav_area": _active_nav_area(request),
        "nav_due_total": counts["due_reviews"] + counts["new_available"],
        "nav_counts": counts,
        "nav_revisit_count": counts["revisit_total"],
        "total_cards": scoped_cards(scope, user=request.user).count(),
    }

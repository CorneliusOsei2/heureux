"""Template context shared across every page (nav badges, app name)."""

from .models import Response, ReviewSession, Settings, Task, Theme
from .queue import queue_counts, scoped_cards


def _empty_globals():
    return {
        "app_name": "Heureux",
        "content_task": None,
        "nav_due_total": 0,
        "nav_counts": {},
        "nav_revisit_count": 0,
        "study_settings": None,
        "total_cards": 0,
    }


def _request_task(request):
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
        tasks = Task.objects.select_related("part").filter(slug=task_slug)
        if part_slug:
            tasks = tasks.filter(part__slug=part_slug)
        task = tasks.first()
        if task:
            return task

    if match and match.url_name == "theme_detail":
        return (
            Theme.objects.filter(slug=kwargs.get("slug"))
            .values_list("task_id", flat=True)
            .first()
        )
    if match and match.url_name == "response_detail":
        return (
            Response.objects.filter(pk=kwargs.get("pk"))
            .values_list("theme__task_id", flat=True)
            .first()
        )
    if match and match.url_name == "family_detail":
        return (
            Task.objects.filter(
                themes__prompts__family__slug=kwargs.get("slug")
            )
            .values_list("pk", flat=True)
            .order_by("part__order", "order")
            .first()
        )
    if match and match.url_name == "part_detail":
        return (
            Task.objects.filter(
                part__slug=kwargs.get("part_slug"),
                available=True,
                themes__isnull=False,
            )
            .values_list("pk", flat=True)
            .order_by("order")
            .first()
            or False
        )
    return None


def study_globals(request):
    match = request.resolver_match
    if (
        not request.user.is_authenticated
        or not match
        or match.namespace != "study"
    ):
        return _empty_globals()
    content_task = _request_task(request)
    if content_task is False:
        content_task = None
    elif isinstance(content_task, int):
        content_task = Task.objects.select_related("part").filter(
            pk=content_task
        ).first()
    elif (
        content_task is None
        and request.resolver_match
        and request.resolver_match.url_name == "dashboard"
    ):
        content_task = (
            Task.objects.select_related("part")
            .filter(available=True, themes__isnull=False)
            .distinct()
            .order_by("part__order", "order")
            .first()
        )
    scope = {}
    if content_task:
        scope = {"part": content_task.part.slug, "task": content_task.slug}
    counts = queue_counts(scope, user=request.user)
    return {
        "app_name": "Heureux",
        "content_task": content_task,
        "nav_due_total": counts["due_reviews"] + counts["new_available"],
        "nav_counts": counts,
        "nav_revisit_count": counts["revisit_total"],
        "study_settings": Settings.load(request.user),
        "total_cards": scoped_cards(scope, user=request.user).count(),
    }

"""Annotations, notes, and highlight views."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from ..forms import (
    NoteForm,
)
from ..models import (
    Annotation,
    AnnotationKind,
    Task,
)
from ..progress import mark_annotation_subject_started

from .common import _route_task

MAX_ANNOTATION_QUOTE_LENGTH = 5000


MAX_ANNOTATION_BODY_LENGTH = 20000


ANNOTATION_SOURCE_KEY_RE = re.compile(r"^[A-Za-z0-9:._-]{0,200}$")


def _annotation_counts(user):
    rows = (
        Annotation.objects.filter(user=user)
        .values("task_id", "kind", "study_later")
        .annotate(total=Count("id"))
    )
    counts = {}
    for row in rows:
        task_counts = counts.setdefault(
            row["task_id"],
            {"notes": 0, "highlights": 0, "study": 0, "total": 0},
        )
        key = (
            "highlights"
            if row["kind"] == AnnotationKind.HIGHLIGHT
            else "notes"
        )
        task_counts[key] += row["total"]
        if row["study_later"]:
            task_counts["study"] += row["total"]
        task_counts["total"] += row["total"]
    return counts


@require_http_methods(["GET", "POST"])
def notes_overview(request):
    part_slug = (request.GET.get("part") or "").strip()
    task_slug = (request.GET.get("task") or "").strip()
    if bool(part_slug) != bool(task_slug):
        return HttpResponseBadRequest(
            "A notes task filter requires both part and task."
        )
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    general_only = request.GET.get("scope") == "general"
    return _notes_scope(
        request,
        task,
        aggregate=task is None and not general_only,
    )


def _annotation_scope_url(task=None):
    if task:
        return (
            reverse("study:notes_overview")
            + "?"
            + urlencode(
                {
                    "part": task.part.slug,
                    "task": task.slug,
                }
            )
        )
    return reverse("study:notes_overview") + "?scope=general"


def _annotation_tab_url(task, kind):
    tab = (
        "highlights"
        if kind == AnnotationKind.HIGHLIGHT
        else "notes"
    )
    return f"{_annotation_scope_url(task)}&tab={tab}"


_HIGHLIGHT_ORIGIN_LABELS = {
    "responses": "Réponse",
    "expressions": "Expression",
}


def _highlight_origin(highlight):
    """Classify a highlight by the kind of source it was captured from."""
    source_key = highlight.source_key or ""
    if source_key.startswith("phrase:"):
        return "expressions"
    if source_key.startswith("response:"):
        return "responses"
    source = urlsplit(highlight.source_path or "")
    source_query = parse_qs(source.query)
    is_expression = (
        source.path.endswith("/expressions/")
        or source.path == reverse("study:vocabulary")
        or source_query.get("kind") == ["phrase"]
    )
    return "expressions" if is_expression else "responses"


_ANNOTATION_DATE_BUCKETS = (
    ("today", "Aujourd’hui"),
    ("yesterday", "Hier"),
    ("week", "Cette semaine"),
    ("month", "Ce mois-ci"),
    ("earlier", "Plus tôt"),
)


def _annotation_date_bucket(local_date, today, yesterday, week_start):
    if local_date >= today:
        return "today"
    if local_date == yesterday:
        return "yesterday"
    if local_date >= week_start:
        return "week"
    if local_date.year == today.year and local_date.month == today.month:
        return "month"
    return "earlier"


def _annotation_date_sections(annotations):
    """Group annotations into ordered, non-empty relative-date sections.

    Sections are keyed on ``created_at`` (the stable capture date) so the
    learning timeline never reshuffles when a card's ``updated_at`` changes,
    e.g. when toggling "à étudier".
    """
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=6)
    items = {key: [] for key, _title in _ANNOTATION_DATE_BUCKETS}
    for annotation in annotations:
        local_date = timezone.localtime(annotation.created_at).date()
        bucket = _annotation_date_bucket(
            local_date, today, yesterday, week_start
        )
        items[bucket].append(annotation)
    return [
        {"key": key, "title": title, "items": items[key]}
        for key, title in _ANNOTATION_DATE_BUCKETS
        if items[key]
    ]


def _notes_scope(request, task=None, *, aggregate=False):
    annotations = Annotation.objects.filter(user=request.user).select_related(
        "task__part"
    )
    if not aggregate:
        annotations = annotations.filter(task=task)
    query = (request.GET.get("q") or "").strip()
    if query:
        annotations = annotations.filter(
            Q(title__icontains=query)
            | Q(body__icontains=query)
            | Q(quote__icontains=query)
            | Q(source_title__icontains=query)
        )
    active_tab = (
        request.GET.get("tab")
        if request.GET.get("tab") in {"notes", "highlights"}
        else "notes"
    )
    if request.method == "POST":
        active_tab = "notes"
        instance = Annotation(
            user=request.user,
            task=task,
            kind=AnnotationKind.NOTE,
        )
        form = NoteForm(request.POST, instance=instance)
        if form.is_valid():
            note = form.save()
            return redirect(
                _annotation_redirect(request, note)
                + f"#note-{note.id}"
            )
    else:
        form = NoteForm()
    notes = list(
        annotations.filter(kind=AnnotationKind.NOTE).order_by(
            "-created_at", "-id"
        )
    )
    highlights = list(
        annotations.filter(kind=AnnotationKind.HIGHLIGHT).order_by(
            "-created_at", "-id"
        )
    )
    for highlight in highlights:
        highlight.origin_label = _HIGHLIGHT_ORIGIN_LABELS[
            _highlight_origin(highlight)
        ]
    counts = _annotation_counts(request.user)
    task_filters = []
    for filter_task in Task.objects.select_related("part").filter(
        Q(is_active=True) | Q(annotations__user=request.user)
    ).distinct().order_by("part__order", "order"):
        task_filters.append(
            {
                "task": filter_task,
                "count": counts.get(filter_task.pk, {}).get("total", 0),
            }
        )
    if task:
        scope_query = urlencode(
            {"part": task.part.slug, "task": task.slug}
        )
    elif not aggregate:
        scope_query = "scope=general"
    else:
        scope_query = ""
    return render(
        request,
        "study/notes_list.html",
        {
            "part": task.part if task else None,
            "task": task,
            "scope_title": (
                task.name
                if task
                else (
                    "Toutes mes notes"
                    if aggregate
                    else "Notes générales"
                )
            ),
            "notes": notes,
            "highlights": highlights,
            "notes_sections": _annotation_date_sections(notes),
            "highlights_sections": _annotation_date_sections(highlights),
            "active_tab": active_tab,
            "study_count": annotations.filter(study_later=True).count(),
            "form": form,
            "aggregate": aggregate,
            "query": query,
            "task_filters": task_filters,
            "general_count": counts.get(None, {}).get("total", 0),
            "scope_query": scope_query,
            "tab_url_prefix": (
                f"?{scope_query}&" if scope_query else "?"
            ),
        },
    )


def task_notes(request, part_slug, task_slug):
    return _notes_scope(request, _route_task(part_slug, task_slug))


def general_notes(request):
    return _notes_scope(request)


def _annotation_anchor(annotation):
    prefix = (
        "highlight"
        if annotation.kind == AnnotationKind.HIGHLIGHT
        else "note"
    )
    return f"{prefix}-{annotation.id}"


@require_GET
def annotation_search(request):
    query = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    study_only = request.GET.get("study") == "1"
    task_id = (request.GET.get("task") or "").strip()

    annotations = Annotation.objects.filter(user=request.user).select_related(
        "task__part"
    )
    if query:
        annotations = annotations.filter(
            Q(title__icontains=query)
            | Q(body__icontains=query)
            | Q(quote__icontains=query)
            | Q(source_title__icontains=query)
        )
    if kind in AnnotationKind.values:
        annotations = annotations.filter(kind=kind)
    else:
        kind = ""
    if study_only:
        annotations = annotations.filter(study_later=True)
    if task_id.isdigit():
        task_id = int(task_id)
        annotations = annotations.filter(task_id=task_id)
    else:
        task_id = None

    result_count = annotations.count()
    results = list(annotations.order_by("-created_at", "-id")[:100])
    for annotation in results:
        annotation.notes_url = (
            _annotation_tab_url(annotation.task, annotation.kind)
            + "#"
            + _annotation_anchor(annotation)
        )
    task_options = (
        Task.objects.filter(annotations__user=request.user)
        .select_related("part")
        .distinct()
        .order_by("part__order", "order", "name")
    )
    return render(
        request,
        "study/annotation_search.html",
        {
            "query": query,
            "selected_kind": kind,
            "study_only": study_only,
            "selected_task_id": task_id,
            "task_options": task_options,
            "results": results,
            "result_count": result_count,
            "result_limit_reached": result_count > len(results),
        },
    )


@require_GET
def annotation_study(request, part_slug=None, task_slug=None):
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    general_only = request.GET.get("scope") == "general"
    annotations = Annotation.objects.filter(
        user=request.user,
        study_later=True,
    ).select_related("task__part")
    if task:
        annotations = annotations.filter(task=task)
    elif general_only:
        annotations = annotations.filter(task__isnull=True)
    items = list(annotations.order_by("-updated_at", "-id"))
    return render(
        request,
        "study/annotation_study.html",
        {
            "part": task.part if task else None,
            "task": task,
            "items": items,
            "scope_title": (
                task.name
                if task
                else (
                    "Notes générales"
                    if general_only
                    else "Toutes mes notes"
                )
            ),
            "back_url": (
                _annotation_scope_url(task)
                if task
                else (
                    reverse("study:notes_overview") + "?scope=general"
                    if general_only
                    else reverse("study:notes_overview")
                )
            ),
        },
    )


def _safe_source_path(value):
    value = (value or "").strip()
    parsed = urlsplit(value)
    if (
        not value
        or parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Invalid source path.")
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    return path[:500]


def _annotation_task(value):
    if not value:
        return None
    try:
        task_id = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid task.") from error
    return get_object_or_404(
        Task.objects.select_related("part"),
        pk=task_id,
        is_active=True,
    )


def _annotation_source_key(value):
    value = (value or "").strip()
    if not ANNOTATION_SOURCE_KEY_RE.fullmatch(value):
        raise ValueError("Invalid annotation source key.")
    return value


def _annotation_overlap_ids(value):
    if value is None:
        return None
    if not value:
        return []
    parts = value.split(",")
    if len(parts) > 100:
        raise ValueError("Too many overlapping highlights.")
    ids = [int(part) for part in parts]
    if any(pk <= 0 for pk in ids):
        raise ValueError("Invalid overlapping highlight.")
    return list(dict.fromkeys(ids))


def _annotation_overlap_revisions(value):
    if value is None:
        return None
    revisions = json.loads(value)
    if not isinstance(revisions, dict) or len(revisions) > 100:
        raise ValueError("Invalid highlight revisions.")
    parsed = {}
    for raw_id, revision in revisions.items():
        pk = int(raw_id)
        if pk <= 0 or not isinstance(revision, str) or len(revision) > 64:
            raise ValueError("Invalid highlight revision.")
        parsed[pk] = revision
    return parsed


def _annotation_source_scope(source_path):
    base_path = source_path.split("?", 1)[0]
    if re.fullmatch(r"/response/\d+/", base_path):
        return (
            base_path,
            Q(source_path=base_path)
            | Q(source_path__startswith=f"{base_path}?"),
        )
    return source_path, Q(source_path=source_path)


@require_GET
def annotations_for_source(request):
    try:
        source_path = _safe_source_path(request.GET.get("source_path"))
    except ValueError:
        return HttpResponseBadRequest("Invalid source path.")
    _, source_filter = _annotation_source_scope(source_path)
    highlights = list(
        Annotation.objects.filter(
            source_filter,
            user=request.user,
            kind=AnnotationKind.HIGHLIGHT,
        ).values(
            "id",
            "quote",
            "source_key",
            "start_offset",
            "end_offset",
            "prefix",
            "suffix",
            "updated_at",
        )
    )
    for highlight in highlights:
        highlight["revision"] = highlight.pop("updated_at").isoformat()
        highlight["delete_url"] = reverse(
            "study:annotation_delete",
            args=[highlight["id"]],
        )
    return JsonResponse({"highlights": highlights})


@require_POST
def annotation_create(request):
    kind = (request.POST.get("kind") or "").strip()
    if kind not in AnnotationKind.values:
        return HttpResponseBadRequest("Invalid annotation kind.")
    quote = request.POST.get("quote") or ""
    body = (request.POST.get("body") or "").strip()
    if not quote.strip():
        return JsonResponse(
            {"error": "Sélectionnez du texte avant de continuer."},
            status=400,
        )
    if len(quote) > MAX_ANNOTATION_QUOTE_LENGTH:
        return JsonResponse(
            {"error": "La sélection est trop longue."},
            status=400,
        )
    if len(body) > MAX_ANNOTATION_BODY_LENGTH:
        return JsonResponse(
            {"error": "La note est trop longue."},
            status=400,
        )
    try:
        task = _annotation_task(request.POST.get("task_id"))
        source_path = _safe_source_path(request.POST.get("source_path"))
        source_path, source_filter = _annotation_source_scope(source_path)
        source_key = _annotation_source_key(request.POST.get("source_key"))
        overlap_ids = _annotation_overlap_ids(request.POST.get("overlap_ids"))
        overlap_revisions = _annotation_overlap_revisions(
            request.POST.get("overlap_revisions")
        )
        start_offset = int(request.POST.get("start_offset", ""))
        end_offset = int(request.POST.get("end_offset", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid annotation data.")
    if start_offset < 0 or end_offset <= start_offset:
        return HttpResponseBadRequest("Invalid annotation offsets.")
    if overlap_ids and (
        overlap_revisions is None
        or set(overlap_ids) != set(overlap_revisions)
    ):
        return HttpResponseBadRequest("Invalid highlight revisions.")

    values = {
        "task": task,
        "quote": quote,
        "source_title": (request.POST.get("source_title") or "")[:300],
        "prefix": (request.POST.get("prefix") or "")[-160:],
        "suffix": (request.POST.get("suffix") or "")[:160],
        "body": body,
    }
    removed_ids = []
    try:
        if kind == AnnotationKind.HIGHLIGHT:
            with transaction.atomic():
                candidates = Annotation.objects.select_for_update().filter(
                    source_filter,
                    user=request.user,
                    kind=kind,
                    source_key=source_key,
                )
                if overlap_ids is None:
                    candidates = candidates.filter(
                        start_offset__lt=end_offset,
                        end_offset__gt=start_offset,
                    )
                    overlapping = list(
                        candidates.order_by("-updated_at", "-id")
                    )
                else:
                    overlapping = list(
                        candidates.filter(id__in=overlap_ids).order_by(
                            "-updated_at",
                            "-id",
                        )
                    )
                    if overlap_revisions is not None and (
                        len(overlapping) != len(overlap_ids)
                        or any(
                            item.updated_at.isoformat()
                            != overlap_revisions[item.id]
                            for item in overlapping
                        )
                    ):
                        return JsonResponse(
                            {
                                "error": (
                                    "Ce surlignage a changé dans un autre onglet. "
                                    "Réessayez avec la version actualisée."
                                )
                            },
                            status=409,
                        )
                    exact_retry = (
                        candidates.filter(
                            start_offset=start_offset,
                            end_offset=end_offset,
                        )
                        .exclude(id__in=overlap_ids)
                        .first()
                    )
                    if exact_retry:
                        if exact_retry.quote != quote:
                            return JsonResponse(
                                {
                                    "error": (
                                        "Les surlignages de cette page ont changé. "
                                        "Supprimez le passage en conflit avant de "
                                        "réessayer."
                                    )
                                },
                                status=409,
                            )
                        overlapping.append(exact_retry)
                annotation = next(
                    (
                        item
                        for item in overlapping
                        if item.start_offset == start_offset
                        and item.end_offset == end_offset
                    ),
                    overlapping[0] if overlapping else None,
                )
                created = annotation is None
                if created:
                    annotation = Annotation(
                        user=request.user,
                        kind=kind,
                        source_path=source_path,
                        source_key=source_key,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        **values,
                    )
                else:
                    annotation.task = task
                    annotation.quote = quote
                    annotation.source_title = values["source_title"]
                    annotation.prefix = values["prefix"]
                    annotation.suffix = values["suffix"]
                    annotation.body = body
                    annotation.start_offset = start_offset
                    annotation.end_offset = end_offset
                    annotation.study_later = any(
                        item.study_later for item in overlapping
                    )
                annotation.full_clean(validate_constraints=False)
                try:
                    with transaction.atomic():
                        annotation.save()
                except IntegrityError:
                    concurrent = candidates.filter(
                        start_offset=start_offset,
                        end_offset=end_offset,
                    ).first()
                    if (
                        not created
                        or overlapping
                        or concurrent is None
                        or concurrent.quote != quote
                    ):
                        return JsonResponse(
                            {
                                "error": (
                                    "Les surlignages de cette page ont changé. "
                                    "Actualisez la page puis réessayez."
                                )
                            },
                            status=409,
                        )
                    annotation = concurrent
                    created = False
                removed_ids = [
                    item.id for item in overlapping if item.id != annotation.id
                ]
                if removed_ids:
                    Annotation.objects.filter(
                        user=request.user,
                        id__in=removed_ids,
                    ).delete()
        else:
            annotation = Annotation(
                user=request.user,
                kind=kind,
                source_path=source_path,
                source_key=source_key,
                start_offset=start_offset,
                end_offset=end_offset,
                **values,
            )
            annotation.full_clean()
            annotation.save()
            created = True
    except ValidationError as error:
        return JsonResponse(
            {"error": " ".join(error.messages)},
            status=400,
        )
    mark_annotation_subject_started(
        request.user,
        annotation.source_path,
        annotation.source_key,
    )
    return JsonResponse(
        {
            "id": annotation.id,
            "created": created,
            "removed_ids": removed_ids,
            "revision": annotation.updated_at.isoformat(),
            "delete_url": reverse(
                "study:annotation_delete",
                args=[annotation.id],
            ),
            "notes_url": (
                _annotation_tab_url(task, annotation.kind)
                + "#"
                + _annotation_anchor(annotation)
            ),
        },
        status=201 if created else 200,
    )


def _annotation_redirect(request, annotation):
    candidate = request.POST.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return _annotation_tab_url(annotation.task, annotation.kind)


@require_POST
def annotation_update(request, pk):
    annotation = get_object_or_404(
        Annotation,
        pk=pk,
        user=request.user,
        kind=AnnotationKind.NOTE,
    )
    form = NoteForm(request.POST, instance=annotation)
    if not form.is_valid():
        return JsonResponse(
            {"error": "Corrigez la note avant de l'enregistrer."},
            status=400,
        )
    form.save()
    redirect_url = _annotation_redirect(request, annotation) + f"#note-{pk}"
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"redirect_url": redirect_url})
    return redirect(redirect_url)


@require_POST
def annotation_study_toggle(request, pk):
    annotation = get_object_or_404(
        Annotation,
        pk=pk,
        user=request.user,
    )
    value = request.POST.get("study_later")
    if value not in {"0", "1"}:
        return HttpResponseBadRequest("Invalid study status.")
    annotation.study_later = value == "1"
    annotation.save(update_fields=["study_later", "updated_at"])
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse(
            {
                "study_later": annotation.study_later,
                "id": annotation.pk,
            }
        )
    return redirect(_annotation_redirect(request, annotation))


@require_POST
def annotation_delete(request, pk):
    annotation = get_object_or_404(
        Annotation,
        pk=pk,
        user=request.user,
    )
    target = _annotation_redirect(request, annotation)
    annotation.delete()
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"deleted": True})
    return redirect(target)

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "study"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("reviser/", views.review_overview, name="review_overview"),
    path(
        "expressions/",
        views.expressions_overview,
        name="expressions_overview",
    ),
    path("progression/", views.stats_overview, name="stats_overview"),
    path("review/", views.review, name="review"),
    path("review/next/", views.review_next, name="review_next"),
    path("review/answer/", views.review_answer, name="review_answer"),
    path("review/undo/", views.review_undo, name="review_undo"),
    path("revisit/", views.revisit_list, name="revisit_list"),
    path("expression/<slug:part_slug>/", views.part_detail, name="part_detail"),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/",
        views.task_detail,
        name="task_detail",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/sujets/",
        views.browse,
        name="task_browse",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/expressions/",
        views.phrases,
        name="task_phrases",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/reviser/",
        views.review_hub,
        name="task_review_hub",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/a-revoir/",
        views.revisit_list,
        name="task_revisit_list",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/progression/",
        views.stats,
        name="task_stats",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/recherche/",
        views.search,
        name="task_search",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/famille/<slug:slug>/",
        views.family_detail,
        name="task_family_detail",
    ),
    path("browse/", views.browse, name="browse"),
    path("theme/<slug:slug>/", views.theme_detail, name="theme_detail"),
    path("family/<slug:slug>/", views.family_detail, name="family_detail"),
    path("response/<int:pk>/", views.response_detail, name="response_detail"),
    path("phrases/", views.phrases, name="phrases"),
    path("search/", views.search, name="search"),
    path("stats/", views.stats, name="stats"),
    path("settings/", views.settings_view, name="settings"),
    path(
        "epreuve/<path:remainder>",
        RedirectView.as_view(
            url="/expression/%(remainder)s",
            permanent=True,
            query_string=True,
        ),
    ),
]

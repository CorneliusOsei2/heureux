from django.urls import path

from . import views

app_name = "study"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("review/", views.review, name="review"),
    path("review/next/", views.review_next, name="review_next"),
    path("review/answer/", views.review_answer, name="review_answer"),
    path("review/undo/", views.review_undo, name="review_undo"),
    path("revisit/", views.revisit_list, name="revisit_list"),
    path("epreuve/<slug:part_slug>/", views.part_detail, name="part_detail"),
    path(
        "epreuve/<slug:part_slug>/<slug:task_slug>/",
        views.task_detail,
        name="task_detail",
    ),
    path("browse/", views.browse, name="browse"),
    path("theme/<slug:slug>/", views.theme_detail, name="theme_detail"),
    path("family/<slug:slug>/", views.family_detail, name="family_detail"),
    path("response/<int:pk>/", views.response_detail, name="response_detail"),
    path("phrases/", views.phrases, name="phrases"),
    path("search/", views.search, name="search"),
    path("stats/", views.stats, name="stats"),
    path("settings/", views.settings_view, name="settings"),
]

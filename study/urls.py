from django.urls import path

from . import views

app_name = "study"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("recover/", views.recover_account, name="recover_account"),
    path(
        "recovery-codes/",
        views.recovery_codes_view,
        name="recovery_codes",
    ),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path(
        "comprehension/",
        views.comprehension_hub,
        name="comprehension_hub",
    ),
    path(
        "comprehension/ce/",
        views.comprehension_overview,
        name="comprehension_overview",
    ),
    path(
        "comprehension/ce/groupe/<int:group_number>/",
        views.comprehension_group_detail,
        name="comprehension_group",
    ),
    path(
        "comprehension/ce/<slug:test_slug>/",
        views.comprehension_test_detail,
        {"mode": "ecrite"},
        name="comprehension_test",
    ),
    path(
        "comprehension/ce/<slug:test_slug>/question/<int:number>/",
        views.comprehension_question_study,
        {"mode": "ecrite"},
        name="comprehension_question_study",
    ),
    path(
        "comprehension/ce/<slug:test_slug>/commencer/",
        views.comprehension_start,
        {"mode": "ecrite"},
        name="comprehension_start",
    ),
    path(
        "comprehension/ce/<slug:test_slug>/"
        "tentative/<int:attempt_id>/question/<int:number>/",
        views.comprehension_question,
        {"mode": "ecrite"},
        name="comprehension_question",
    ),
    path(
        "comprehension/ce/<slug:test_slug>/"
        "tentative/<int:attempt_id>/resultats/",
        views.comprehension_results,
        {"mode": "ecrite"},
        name="comprehension_results",
    ),
    path(
        "comprehension/co/",
        views.comprehension_oral_overview,
        name="comprehension_oral_overview",
    ),
    path(
        "comprehension/co/<slug:test_slug>/",
        views.comprehension_test_detail,
        {"mode": "orale"},
        name="comprehension_oral_test",
    ),
    path(
        "comprehension/co/<slug:test_slug>/question/<int:number>/",
        views.comprehension_question_study,
        {"mode": "orale"},
        name="comprehension_oral_question_study",
    ),
    path(
        "comprehension/co/<slug:test_slug>/commencer/",
        views.comprehension_start,
        {"mode": "orale"},
        name="comprehension_oral_start",
    ),
    path(
        "comprehension/co/<slug:test_slug>/"
        "tentative/<int:attempt_id>/question/<int:number>/",
        views.comprehension_question,
        {"mode": "orale"},
        name="comprehension_oral_question",
    ),
    path(
        "comprehension/co/<slug:test_slug>/"
        "tentative/<int:attempt_id>/resultats/",
        views.comprehension_results,
        {"mode": "orale"},
        name="comprehension_oral_results",
    ),
    path("reviser/", views.redirect_home, name="review_overview"),
    path(
        "expressions/",
        views.redirect_vocabulary,
        name="expressions_overview",
    ),
    path("notes/", views.notes_overview, name="notes_overview"),
    path(
        "notes/recherche/",
        views.annotation_search,
        name="annotation_search",
    ),
    path(
        "notes/etudier/",
        views.annotation_study,
        name="annotation_study",
    ),
    path("notes/general/", views.redirect_notes, name="general_notes"),
    path(
        "notes/<slug:part_slug>/<slug:task_slug>/",
        views.redirect_notes,
        name="task_notes",
    ),
    path(
        "notes/<slug:part_slug>/<slug:task_slug>/etudier/",
        views.annotation_study,
        name="task_annotation_study",
    ),
    path(
        "annotations/source/",
        views.annotations_for_source,
        name="annotations_for_source",
    ),
    path(
        "annotations/create/",
        views.annotation_create,
        name="annotation_create",
    ),
    path(
        "annotations/<int:pk>/update/",
        views.annotation_update,
        name="annotation_update",
    ),
    path(
        "annotations/<int:pk>/study/",
        views.annotation_study_toggle,
        name="annotation_study_toggle",
    ),
    path(
        "annotations/<int:pk>/delete/",
        views.annotation_delete,
        name="annotation_delete",
    ),
    path("expression/", views.expression_hub, name="expression"),
    path("vocabulaire/", views.phrases, name="vocabulary"),
    path("recherche/", views.search, name="search"),
    path("progression/", views.stats, name="stats"),
    path("progression/", views.stats, name="stats_overview"),
    path("review/", views.review, name="review"),
    path("review/next/", views.review_next, name="review_next"),
    path("review/previous/", views.review_previous, name="review_previous"),
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
        views.redirect_vocabulary,
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
        views.redirect_stats,
        name="task_stats",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/recherche/",
        views.redirect_search,
        name="task_search",
    ),
    path(
        "expression/<slug:part_slug>/<slug:task_slug>/famille/<slug:slug>/",
        views.family_detail,
        name="task_family_detail",
    ),
    path("browse/", views.redirect_expression, name="browse"),
    path("theme/<slug:slug>/", views.theme_detail, name="theme_detail"),
    path("family/<slug:slug>/", views.family_detail, name="family_detail"),
    path("response/<int:pk>/", views.response_detail, name="response_detail"),
    path(
        "response/<int:pk>/edit/",
        views.edit_response,
        name="edit_response",
    ),
    path("phrases/", views.redirect_vocabulary, name="phrases"),
    path("search/", views.redirect_search, name="legacy_search"),
    path("stats/", views.redirect_stats, name="legacy_stats"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/pin/", views.change_pin, name="change_pin"),
    path(
        "settings/recovery-codes/",
        views.regenerate_recovery_codes,
        name="regenerate_recovery_codes",
    ),
    path(
        "settings/progress/reset/",
        views.reset_progress,
        name="reset_progress",
    ),
    path("settings/export/", views.export_account, name="export_account"),
    path(
        "settings/account/delete/",
        views.delete_account,
        name="delete_account",
    ),
]

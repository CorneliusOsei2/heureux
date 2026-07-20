from django.urls import path, register_converter

from . import views


class ExpressionPartConverter:
    regex = "eo|ee"

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(ExpressionPartConverter, "expression_part")

app_name = "study"

urlpatterns = [
    # Account
    path("compte/connexion/", views.login_view, name="login"),
    path("compte/inscription/", views.register_view, name="register"),
    path(
        "compte/recuperation/",
        views.recover_account,
        name="recover_account",
    ),
    path(
        "compte/codes-recuperation/",
        views.recovery_codes_view,
        name="recovery_codes",
    ),
    path("compte/deconnexion/", views.logout_view, name="logout"),
    path(
        "compte/parametres/",
        views.settings_view,
        name="settings",
    ),
    path(
        "compte/parametres/pin/",
        views.change_pin,
        name="change_pin",
    ),
    path(
        "compte/parametres/codes-recuperation/",
        views.regenerate_recovery_codes,
        name="regenerate_recovery_codes",
    ),
    path(
        "compte/parametres/progression/reinitialiser/",
        views.reset_progress,
        name="reset_progress",
    ),
    path(
        "compte/parametres/exporter/",
        views.export_account,
        name="export_account",
    ),
    path(
        "compte/parametres/supprimer/",
        views.delete_account,
        name="delete_account",
    ),
    # Main areas
    path("", views.dashboard, name="dashboard"),
    path("comprehension/", views.comprehension_hub, name="comprehension_hub"),
    path("expression/", views.expression_hub, name="expression"),
    # Compréhension écrite (CE)
    path("ce/", views.comprehension_overview, name="comprehension_overview"),
    path(
        "ce/groupes/<int:group_number>/",
        views.comprehension_group_detail,
        name="comprehension_group",
    ),
    path(
        "ce/vocabulaire/",
        views.phrases,
        {"comprehension_mode": "ecrite"},
        name="comprehension_vocabulary",
    ),
    path(
        "ce/tests/<slug:test_slug>/vocabulaire/",
        views.phrases,
        {"comprehension_mode": "ecrite"},
        name="comprehension_test_vocabulary",
    ),
    path(
        "ce/tests/<slug:test_slug>/vocabulaire/revision/",
        views.review,
        {"comprehension_mode": "ecrite"},
        name="comprehension_vocabulary_review",
    ),
    path(
        "ce/tests/<slug:test_slug>/questions/<int:number>/",
        views.comprehension_question_study,
        {"mode": "ecrite"},
        name="comprehension_question_study",
    ),
    path(
        "ce/tests/<slug:test_slug>/commencer/",
        views.comprehension_start,
        {"mode": "ecrite"},
        name="comprehension_start",
    ),
    path(
        "ce/tests/<slug:test_slug>/tentatives/<int:attempt_id>/"
        "questions/<int:number>/",
        views.comprehension_question,
        {"mode": "ecrite"},
        name="comprehension_question",
    ),
    path(
        "ce/tests/<slug:test_slug>/tentatives/<int:attempt_id>/resultats/",
        views.comprehension_results,
        {"mode": "ecrite"},
        name="comprehension_results",
    ),
    path(
        "ce/tests/<slug:test_slug>/",
        views.comprehension_test_detail,
        {"mode": "ecrite"},
        name="comprehension_test",
    ),
    # Compréhension orale (CO)
    path(
        "co/",
        views.comprehension_oral_overview,
        name="comprehension_oral_overview",
    ),
    path(
        "co/groupes/<int:group_number>/",
        views.comprehension_oral_group_detail,
        name="comprehension_oral_group",
    ),
    path(
        "co/vocabulaire/",
        views.phrases,
        {"comprehension_mode": "orale"},
        name="comprehension_oral_vocabulary",
    ),
    path(
        "co/tests/<slug:test_slug>/vocabulaire/",
        views.phrases,
        {"comprehension_mode": "orale"},
        name="comprehension_oral_test_vocabulary",
    ),
    path(
        "co/tests/<slug:test_slug>/vocabulaire/revision/",
        views.review,
        {"comprehension_mode": "orale"},
        name="comprehension_oral_vocabulary_review",
    ),
    path(
        "co/tests/<slug:test_slug>/questions/<int:number>/",
        views.comprehension_question_study,
        {"mode": "orale"},
        name="comprehension_oral_question_study",
    ),
    path(
        "co/tests/<slug:test_slug>/commencer/",
        views.comprehension_start,
        {"mode": "orale"},
        name="comprehension_oral_start",
    ),
    path(
        "co/tests/<slug:test_slug>/tentatives/<int:attempt_id>/"
        "questions/<int:number>/",
        views.comprehension_question,
        {"mode": "orale"},
        name="comprehension_oral_question",
    ),
    path(
        "co/tests/<slug:test_slug>/tentatives/<int:attempt_id>/resultats/",
        views.comprehension_results,
        {"mode": "orale"},
        name="comprehension_oral_results",
    ),
    path(
        "co/tests/<slug:test_slug>/",
        views.comprehension_test_detail,
        {"mode": "orale"},
        name="comprehension_oral_test",
    ),
    # Global vocabulary
    path("vocabulaire/", views.phrases, name="vocabulary"),
    path(
        "vocabulaire/categories/<slug:category_slug>/",
        views.phrases,
        name="vocabulary_category",
    ),
    # Notes and annotations
    path("notes/", views.notes_overview, name="notes_overview"),
    path("notes/generales/", views.general_notes, name="general_notes"),
    path(
        "notes/generales/etudier/",
        views.annotation_study,
        {"general_only": True},
        name="general_annotation_study",
    ),
    path("notes/recherche/", views.annotation_search, name="annotation_search"),
    path("notes/etudier/", views.annotation_study, name="annotation_study"),
    path(
        "notes/source/",
        views.annotations_for_source,
        name="annotations_for_source",
    ),
    path("notes/ajouter/", views.annotation_create, name="annotation_create"),
    path(
        "notes/<int:pk>/modifier/",
        views.annotation_update,
        name="annotation_update",
    ),
    path(
        "notes/<int:pk>/etudier/",
        views.annotation_study_toggle,
        name="annotation_study_toggle",
    ),
    path(
        "notes/<int:pk>/supprimer/",
        views.annotation_delete,
        name="annotation_delete",
    ),
    # Global study tools
    path("recherche/", views.search, name="search"),
    path("progression/", views.stats, name="stats"),
    path("revision/", views.review, name="review"),
    path("revision/suivante/", views.review_next, name="review_next"),
    path("revision/precedente/", views.review_previous, name="review_previous"),
    path("revision/repondre/", views.review_answer, name="review_answer"),
    path("revision/annuler/", views.review_undo, name="review_undo"),
    path("revision/a-revoir/", views.revisit_list, name="revisit_list"),
    # Expression écrite (EE) and expression orale (EO)
    path(
        "<expression_part:part_slug>/",
        views.part_detail,
        name="part_detail",
    ),
    path(
        "<expression_part:part_slug>/progression/",
        views.stats,
        name="part_stats",
    ),
    path(
        "<expression_part:part_slug>/revision/",
        views.review,
        name="part_review",
    ),
    path(
        "<expression_part:part_slug>/revision/a-revoir/",
        views.revisit_list,
        name="part_revisit_list",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/",
        views.task_detail,
        name="task_detail",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/sujets/",
        views.browse,
        name="task_browse",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/"
        "sujets/<int:prompt_id>/",
        views.response_detail,
        name="response_detail",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/"
        "sujets/<int:prompt_id>/personnaliser/",
        views.edit_response,
        name="edit_response",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/themes/<slug:slug>/",
        views.theme_detail,
        name="theme_detail",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/familles/<slug:slug>/",
        views.family_detail,
        name="task_family_detail",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/vocabulaire/",
        views.phrases,
        name="task_phrases",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/"
        "vocabulaire/categories/<slug:category_slug>/",
        views.phrases,
        name="task_vocabulary_category",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/notes/",
        views.task_notes,
        name="task_notes",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/notes/etudier/",
        views.annotation_study,
        name="task_annotation_study",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/recherche/",
        views.search,
        name="task_search",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/progression/",
        views.stats,
        name="task_stats",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/revision/",
        views.review_hub,
        name="task_review_hub",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/revision/cartes/",
        views.review,
        name="task_review",
    ),
    path(
        "<expression_part:part_slug>/<slug:task_slug>/revision/a-revoir/",
        views.revisit_list,
        name="task_revisit_list",
    ),
]

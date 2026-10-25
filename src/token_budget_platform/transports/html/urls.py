"""HTML transport URL routes for every core journey."""

from django.urls import path

from .views import (
    administration,
    browse,
    community_report,
    compare,
    home,
    live,
    manage_landing,
    manage_session,
    moderation_queue,
    organization,
    ready,
    record,
    staff_login,
    submission_confirm,
    submission_landing,
    submission_start,
)

urlpatterns = [
    path("", home, name="home"),
    # Public catalog
    path("browse/", browse, name="browse"),
    path("organizations/<slug:slug>/", organization, name="organization-profile"),
    path("records/<uuid:record_id>/", record, name="record-detail"),
    path("compare/", compare, name="compare"),
    # Contributor submission
    path("submit/", submission_start, name="submit"),
    path("submit/confirm/", submission_confirm, name="submit-confirm"),
    path("submit/manage/<str:token>/", submission_landing, name="submission-manage-link"),
    # Community report
    path("report/", community_report, name="community-report"),
    # Contributor management
    path("manage/", manage_landing, name="manage"),
    path("manage/session/", manage_session, name="manage-session"),
    # Staff
    path("staff/login/", staff_login, name="staff-login"),
    path("staff/moderation/", moderation_queue, name="moderation"),
    path("staff/administration/", administration, name="administration"),
    # Health
    path("health/live", live, name="health-live"),
    path("health/ready", ready, name="health-ready"),
]

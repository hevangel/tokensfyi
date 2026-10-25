from django.urls import path

from .backup_events import ingest_backup_event
from .catalog import api_browse, api_compare, api_record, api_report, api_submit
from .management_sessions import exchange_capability
from .staff_evidence import staff_evidence_download
from .views import health_live, health_ready

urlpatterns = [
    path("health/live", health_live, name="api-health-live"),
    path("health/ready", health_ready, name="api-health-ready"),
    # Public catalog (read-only)
    path("records", api_browse, name="api-records"),
    path("records/<uuid:record_id>", api_record, name="api-record-detail"),
    path("records:compare", api_compare, name="api-records-compare"),
    # Contributions
    path("submissions", api_submit, name="api-submissions"),
    path("reports", api_report, name="api-reports"),
    # Contributor management
    path("management-sessions", exchange_capability, name="api-management-sessions"),
    # Staff (role- and scan-gated)
    path("staff/evidence/<uuid:evidence_id>", staff_evidence_download, name="api-staff-evidence"),
    # Internal operations
    path("internal/backup-events", ingest_backup_event, name="api-backup-events"),
]

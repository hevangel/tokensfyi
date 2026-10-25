from django.apps import AppConfig


class ApiTransportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "token_budget_platform.transports.api"
    label = "token_budget_api"

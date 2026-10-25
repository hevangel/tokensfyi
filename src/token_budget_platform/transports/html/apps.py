from django.apps import AppConfig


class HtmlTransportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "token_budget_platform.transports.html"
    label = "token_budget_html"

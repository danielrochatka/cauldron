"""Extended URL conf that includes the cauldron shell namespace for shell tests."""
from django.urls import include, path


def _cauldron_stub_patterns():
    from django.http import HttpResponse

    def dashboard(request):
        return HttpResponse("dashboard")

    return [path("", dashboard, name="dashboard")]


def _cauldron_auth_stub_patterns():
    from django.http import HttpResponse

    def logout(request):
        return HttpResponse("logout")

    return [path("logout/", logout, name="logout")]


urlpatterns = [
    path("", include("cauldron_ai_admin.urls", namespace="cauldron_ai_admin")),
    path("cauldron/", include((_cauldron_stub_patterns(), "cauldron"))),
    path("auth/", include((_cauldron_auth_stub_patterns(), "cauldron_auth"))),
    path("site/", include("cauldron_site_astro.urls")),
]

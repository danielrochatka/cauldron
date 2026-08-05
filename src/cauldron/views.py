"""Small HTTP views exposed by the Cauldron foundation."""

from django.http import JsonResponse

from . import __version__


def health(request):
    """Return minimal runtime health information for integration tests."""

    return JsonResponse({"status": "ok", "package": "cauldron", "version": __version__})

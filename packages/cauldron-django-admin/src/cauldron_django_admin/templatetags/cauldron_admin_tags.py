"""Template tags for the Cauldron Admin Shell."""
from django import template

from ..navigation import get_navigation_registry

register = template.Library()


@register.simple_tag(takes_context=True)
def get_navigation(context, request):
    """Return grouped navigation for the current user."""
    registry = get_navigation_registry()
    user = getattr(request, "user", None)
    return registry.get_grouped_nav(user, request)

from django import template

register = template.Library()


@register.filter
def can_access(user, permission_key):
    if not permission_key:
        return False
    from users.permissions import user_has_permission

    return user_has_permission(user, str(permission_key))

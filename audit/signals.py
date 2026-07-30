"""Authentication events go into the audit trail automatically."""

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .models import AccessLog, AuditAction


def _ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    AccessLog.objects.create(
        user=user,
        username=user.get_username(),
        user_role=getattr(user, "role", ""),
        action=AuditAction.LOGIN,
        ip_address=_ip(request),
        path=request.path[:300] if request else "",
    )


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    if user is None:
        return
    AccessLog.objects.create(
        user=user,
        username=user.get_username(),
        user_role=getattr(user, "role", ""),
        action=AuditAction.LOGOUT,
        ip_address=_ip(request),
        path=request.path[:300] if request else "",
    )


@receiver(user_login_failed)
def log_login_failed(sender, credentials, request=None, **kwargs):
    # Only the attempted username is stored — never the submitted password.
    AccessLog.objects.create(
        username=(credentials or {}).get("username", "")[:150],
        action=AuditAction.LOGIN_FAILED,
        ip_address=_ip(request),
        description="Failed login attempt",
        path=request.path[:300] if request else "",
    )

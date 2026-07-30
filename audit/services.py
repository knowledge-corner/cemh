"""Helpers for writing audit entries from views."""

import logging

from .models import AccessLog, AuditAction

logger = logging.getLogger(__name__)


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def record(request, action, *, obj=None, patient=None, description=""):
    """
    Write one audit entry.

    Never raises: an audit failure must not take down a clinical screen. A
    failure is logged (without patient detail) so it can be investigated.
    """
    try:
        user = getattr(request, "user", None)
        authenticated = bool(user and user.is_authenticated)

        AccessLog.objects.create(
            user=user if authenticated else None,
            username=user.get_username() if authenticated else "",
            user_role=getattr(user, "role", "") if authenticated else "",
            action=action,
            object_type=obj.__class__.__name__ if obj is not None else "",
            object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
            patient_id_ref=patient.patient_id if patient is not None else "",
            description=description,
            ip_address=_client_ip(request),
            path=request.path[:300],
        )
    except Exception:
        # Deliberately no patient identifiers in this message.
        logger.exception("Failed to write audit entry for action=%s", action)


def record_patient_view(request, patient, description=""):
    """Shorthand for the commonest case: a staff member opening a patient file."""
    record(
        request,
        AuditAction.VIEW,
        obj=patient,
        patient=patient,
        description=description or "Viewed patient record",
    )

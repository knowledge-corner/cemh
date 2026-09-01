"""
Shared machinery for export_patient / import_patient.

Not a management command itself — Django only imports a module from this
folder when a command by that name is actually run, so a plain helper module
sitting alongside them is safe.

The transport format is one JSON file per patient: every field on every
related row, with any foreign key that would point at a numeric id in a
*different* database (a doctor, a lab test) replaced by its natural key
instead — a username, a lab test code. Rows are relinked to each other by
position (``visit_ref``: an index into the "visits" list) rather than by id,
since the id a row gets on the source side means nothing on the target side.

Two things this format explicitly does NOT carry over, on purpose:

* the source database's own numeric primary keys — the target database
  assigns its own, exactly as if a receptionist had typed all this in;
* the audit log (who-viewed-this-file trail) — that trail is about actions
  taken against a database, and the source machine's own test/local access
  history is not a fact about the *target* database's history.
"""

import base64
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import FileField

User = get_user_model()


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _from_json(field, value):
    """The inverse of ``_json_safe``, using the field's own type to decide how."""
    if value is None:
        return None
    internal = field.get_internal_type()
    if internal == "DecimalField":
        return Decimal(value)
    if internal == "DateTimeField":
        return datetime.fromisoformat(value)
    if internal == "DateField":
        return date.fromisoformat(value)
    return value


def serialize_instance(instance, *, fk_natural_keys=None):
    """
    Every concrete field on ``instance`` as a JSON-safe dict.

    The primary key is never included — the target database assigns its own.
    A field named in ``fk_natural_keys`` (``{field_name: callable(related) ->
    str}``) is replaced by its natural key. Every other relation (visit,
    patient, prescription, charge, payment — anything this export nests
    structurally instead) is silently skipped here; the caller relinks those
    by position when rebuilding the object graph on import.
    """
    fk_natural_keys = fk_natural_keys or {}
    data = {}
    for field in instance._meta.concrete_fields:
        name = field.name
        if name == "id":
            continue
        if isinstance(field, FileField):
            continue  # handled by the caller, alongside the file's own bytes
        if field.is_relation:
            if name in fk_natural_keys:
                related = getattr(instance, name)
                data[name] = fk_natural_keys[name](related) if related is not None else None
            continue
        data[name] = _json_safe(getattr(instance, name))
    return data


def apply_fields(instance, data, model, *, natural_key_lookups=None):
    """
    Set every field in ``data`` onto ``instance`` (not yet saved), converting
    JSON-safe values back to the type the field expects, and resolving a
    natural-key field (looked up via ``natural_key_lookups``) back to a real
    object in *this* database.
    """
    natural_key_lookups = natural_key_lookups or {}
    fields_by_name = {f.name: f for f in model._meta.concrete_fields}
    for name, value in data.items():
        if name in natural_key_lookups:
            setattr(instance, f"{name}_id" if fields_by_name[name].is_relation else name,
                    natural_key_lookups[name](value) if value is not None else None)
            continue
        field = fields_by_name[name]
        setattr(instance, name, _from_json(field, value))


def username_of(user):
    return user.username if user else None


def lookup_username(username):
    """Raises User.DoesNotExist — callers pre-flight-check every username first."""
    return User.objects.get(username=username).pk


def encode_file(field_file):
    """``(original_name, base64_bytes)``, or ``(None, None)`` if no file is set."""
    if not field_file:
        return None, None
    field_file.open("rb")
    try:
        content = field_file.read()
    finally:
        field_file.close()
    return field_file.name.rsplit("/", 1)[-1], base64.b64encode(content).decode("ascii")


def restore_file(field_file, name, b64_content):
    """Write a base64-encoded file back onto ``field_file`` (not yet saved)."""
    if not name:
        return
    from django.core.files.base import ContentFile
    field_file.save(name, ContentFile(base64.b64decode(b64_content)), save=False)


#: The fields every model here carries that Django recomputes on every plain
#: .save() regardless of what was assigned (auto_now / auto_now_add) — the
#: reason a historical value has to be forced back in with .update() instead.
FORCED_TIMESTAMP_FIELDS = {
    "created_at": "auto_now_add",
    "updated_at": "auto_now",
    "issued_at": "auto_now_add",
}


def force_timestamps(model, pk, data):
    """
    Re-apply any of ``FORCED_TIMESTAMP_FIELDS`` present in ``data``, bypassing
    auto_now/auto_now_add via a queryset .update() — the one thing that writes
    straight to SQL without Django's save() machinery touching them again.
    """
    fields_by_name = {f.name: f for f in model._meta.concrete_fields}
    updates = {}
    for name in FORCED_TIMESTAMP_FIELDS:
        if name in data and name in fields_by_name:
            updates[name] = _from_json(fields_by_name[name], data[name])
    if updates:
        model.objects.filter(pk=pk).update(**updates)

"""
The 500 error page, with a context Django's default one deliberately lacks.

Django's built-in ``server_error`` view renders with no context and no context
processors, precisely because the failure that triggered it might have been in
one of them — a context processor that queries the database is the last thing
you want run again while handling a 500 that started as a database problem.

This view keeps that caution: it builds the page's own context by hand from
``settings.CLINIC`` directly, and renders without a ``request`` argument, which
is what keeps Django's normal context processors from running at all.
"""

from django.conf import settings
from django.http import HttpResponseServerError
from django.template import loader


def server_error(request, template_name="500.html"):
    clinic = settings.CLINIC
    template = loader.get_template(template_name)
    html = template.render({
        "clinic_name": clinic.CLINIC_NAME,
        "clinic_phone": clinic.CLINIC_PHONE,
    })
    return HttpResponseServerError(html)

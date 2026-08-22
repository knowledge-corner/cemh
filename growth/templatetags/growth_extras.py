"""Template helpers for the growth chart tab."""

import json

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def chart_json(chart):
    """
    Serialise one chart's data for the ``data-chart`` attribute.

    HTML-escaped rather than marked safe as raw JSON: the values come from
    patient records, and an attribute is exactly where an unescaped quote would
    break out. The browser un-escapes it when reading the attribute.
    """
    payload = {
        "indicator": chart["indicator"],
        "label": chart["label"],
        "unit": chart["unit"],
        "points": chart["points"],
        "curves": chart["curves"],
        # Reference lines that are not centiles — the IAP BMI chart's
        # adult-equivalent overweight and obesity cut-offs. Empty elsewhere.
        "cutoffs": chart.get("cutoffs", []),
        # A second reading of the same height, positioned at the bone-age x
        # rather than the chronological one. Only the height chart ever has
        # any; empty elsewhere.
        "bone_age_points": chart.get("bone_age_points", []),
        # The mid-parental target height and its ±range, at the chart's right
        # edge. Only the height chart ever has one.
        "mid_parental": chart.get("mid_parental"),
    }
    return mark_safe(escape(json.dumps(payload, separators=(",", ":"))))

"""
Landing pages for roles whose screens arrive in a later phase.

They exist now so that role-based login routing is complete and testable from
day one: a receptionist who logs in lands somewhere sensible instead of an
error, and the access-control tests have a real URL to prove a doctor-only page
rejects them.
"""

from django.shortcuts import render

from accounts.models import Role
from accounts.permissions import role_required


@role_required(Role.RECEPTIONIST)
def reception_home(request):
    return render(
        request,
        "portal/placeholder.html",
        {
            "heading": "Reception",
            "message": "The daily queue, bookings and billing screens are being built.",
        },
    )


@role_required(Role.PATIENT)
def patient_home(request):
    return render(
        request,
        "portal/placeholder.html",
        {
            "heading": "Your appointments",
            "message": "Online booking and your visit history are being built.",
        },
    )

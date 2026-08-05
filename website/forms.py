"""The one form on the public page."""

from django import forms

from accounts.models import Role, User, phone_validator

from .models import CallbackRequest

INPUT = {"class": "wf__input"}


class CallbackForm(forms.ModelForm):
    """
    "Ring me back."

    Public and unauthenticated, so it asks for the least that makes a callback
    possible: a name and a number. Everything else is optional, because a
    required field on a public form is a person who gives up instead.
    """

    class Meta:
        model = CallbackRequest
        fields = ["name", "phone", "preferred_doctor", "concern"]
        widgets = {
            "name": forms.TextInput(attrs={**INPUT, "placeholder": "Patient name",
                                           "autocomplete": "name"}),
            "phone": forms.TextInput(attrs={**INPUT, "placeholder": "Phone number",
                                            "type": "tel", "autocomplete": "tel",
                                            "inputmode": "numeric"}),
            "concern": forms.Textarea(attrs={
                **INPUT, "rows": 3,
                "placeholder": "Tell us briefly about your concern (optional)",
            }),
        }
        labels = {
            "name": "Patient name",
            "phone": "Phone number",
            "preferred_doctor": "Preferred doctor",
            "concern": "Your concern",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["phone"].validators.append(phone_validator)
        for optional in ("preferred_doctor", "concern"):
            self.fields[optional].required = False

        # Named rather than linked, so a doctor who leaves does not orphan the
        # requests that asked for them. "No preference" comes first: most
        # people have none, and making them scroll past two names to say so is
        # asking a question nobody wanted.
        doctors = User.objects.filter(
            role=Role.DOCTOR, is_active=True
        ).select_related("doctor_profile", "doctor_profile__specialisation")

        choices = [("", "No preference")]
        for doctor in doctors:
            specialisation = getattr(
                getattr(doctor, "doctor_profile", None), "specialisation", None
            )
            label = doctor.display_name
            if specialisation is not None:
                label = f"{label} ({specialisation.name})"
            choices.append((doctor.display_name, label))

        self.fields["preferred_doctor"] = forms.ChoiceField(
            label="Preferred doctor", choices=choices, required=False,
            widget=forms.Select(attrs=INPUT),
        )

    def clean_name(self):
        return " ".join((self.cleaned_data.get("name") or "").split())

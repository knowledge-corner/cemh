import os, django, datetime
os.environ.setdefault("DJANGO_SETTINGS_MODULE","config.settings.dev")
django.setup()
from django.utils import timezone
from accounts.models import User
from appointments.models import Visit, VisitStatus
from patients.models import Patient
doc=User.objects.get(username="dr-adway"); rec=User.objects.get(username="browsercheck")
y = timezone.localdate() - datetime.timedelta(days=1)
t = timezone.localdate()
def at(d,h):
    return timezone.make_aware(datetime.datetime.combine(d, datetime.time(h,0)),
                               timezone.get_current_timezone())
pats=list(Patient.objects.all()[:3])
# Yesterday: one never-confirmed, one confirmed-but-missed.
a=Visit.objects.create(patient=pats[0],doctor=doc,scheduled_start=at(y,9),scheduled_end=at(y,10),booked_by=rec)
b=Visit.objects.create(patient=pats[1],doctor=doc,scheduled_start=at(y,11),scheduled_end=at(y,12),booked_by=rec)
b.transition_to(VisitStatus.CONFIRMED, by_user=rec)
# Today: a booking that must survive.
Visit.objects.create(patient=pats[2],doctor=doc,scheduled_start=at(t,15),scheduled_end=at(t,16),booked_by=rec)
print("before:", a.status, b.status)

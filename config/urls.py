from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

handler500 = "config.views.server_error"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    # /admin/ is a decoy: it reveals nothing about where the real admin lives.
    path("admin/", lambda r: redirect("/")),
    # The public page owns "/". Everything else needs a login.
    path("", include("website.urls")),
    path("", include("accounts.urls")),
    path("doctor/", include("portal.urls_doctor")),
    path("", include("portal.urls")),
]

# Not gated on DEBUG. This is a single small-clinic deployment with one
# gunicorn process behind Caddy and no separate file host — WhiteNoise already
# serves /static/ the same way, straight out of the app process. Without this,
# nothing serves an uploaded doctor photo or prescription scan in production at
# all: the file saves, and every ``.url`` pointing at it 404s.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    # /admin/ is a decoy: it reveals nothing about where the real admin lives.
    path("admin/", lambda r: redirect("/")),
    path("", include("accounts.urls")),
    path("doctor/", include("portal.urls_doctor")),
    path("", include("portal.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

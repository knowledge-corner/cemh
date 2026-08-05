from django.urls import path

from . import views, views_seo

urlpatterns = [
    path("", views.home, name="website_home"),

    # Both must sit at the very root to be found: a crawler looks for
    # /robots.txt and nowhere else.
    path("robots.txt", views_seo.robots_txt, name="robots_txt"),
    path("sitemap.xml", views_seo.sitemap_xml, name="sitemap_xml"),
]

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ClothRollViewSet,
    DipRunViewSet,
    LoftViewSet,
    dashboard_stats,
    weight_diff,
)

router = DefaultRouter()
router.register("lofts", LoftViewSet, basename="loft")
router.register("rolls", ClothRollViewSet, basename="roll")
router.register("dips", DipRunViewSet, basename="dip")

urlpatterns = [
    path("dashboard/", dashboard_stats, name="dashboard"),
    path("weight-diff/", weight_diff, name="weight-diff"),
    path("", include(router.urls)),
]

from django.db.models import Count
from django.db import transaction
from rest_framework import serializers, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ClothRoll, DipRun, Loft
from .rules import MAX_GSM_DELTA_DIPPING, gsm_blocker
from .serializers import ClothRollSerializer, DipRunSerializer, LoftSerializer


class LoftViewSet(viewsets.ModelViewSet):
    queryset = Loft.objects.annotate(roll_count=Count("rolls")).all()
    serializer_class = LoftSerializer


class ClothRollViewSet(viewsets.ModelViewSet):
    serializer_class = ClothRollSerializer

    def get_queryset(self):
        qs = ClothRoll.objects.select_related("loft").all()
        loft_id = self.request.query_params.get("loftId")
        status = self.request.query_params.get("status")
        if loft_id:
            qs = qs.filter(loft_id=loft_id)
        if status:
            qs = qs.filter(status=status)
        return qs

    def perform_create(self, serializer):
        new_status = serializer.validated_data.get("status")
        if new_status != ClothRoll.STATUS_DIPPING:
            serializer.save()
            return
        loft = serializer.validated_data.get("loft")
        weight = serializer.validated_data.get("fabric_weight_gsm")
        with transaction.atomic():
            list(
                ClothRoll.objects.select_for_update()
                .filter(loft_id=loft.id)
                .order_by("id")
            )
            probe = ClothRoll(
                loft=loft,
                fabric_weight_gsm=weight if weight is not None else 380,
            )
            msg = gsm_blocker(probe)
            if msg:
                raise serializers.ValidationError({"status": msg})
            serializer.save()

    def perform_update(self, serializer):
        instance = serializer.instance
        new_status = serializer.validated_data.get("status")
        entering_dipping = (
            new_status == ClothRoll.STATUS_DIPPING
            and instance.status != ClothRoll.STATUS_DIPPING
        )
        if not entering_dipping:
            serializer.save()
            return

        # 串行化同一帆布间内「转入浸渍中」：两名浸胶工几乎同时改同一卷或
        # 同间另一卷时，拿锁后再读一次最新邻卷，保证只有一笔能成功。
        loft_id = instance.loft_id
        weight = serializer.validated_data.get("fabric_weight_gsm")
        with transaction.atomic():
            list(
                ClothRoll.objects.select_for_update()
                .filter(loft_id=loft_id)
                .order_by("id")
            )
            fresh = ClothRoll.objects.get(pk=instance.pk)
            if fresh.status == ClothRoll.STATUS_DIPPING:
                raise serializers.ValidationError(
                    {"status": "该卷已处于浸渍中，请勿重复提交"}
                )
            msg = gsm_blocker(fresh, weight)
            if msg:
                raise serializers.ValidationError({"status": msg})
            serializer.save()

    @action(detail=False, methods=["get"], url_path="weight-diffs")
    def weight_diffs(self, request):
        """只读：列出各卷克重与同间浸渍中邻卷的当前克重差，可按间筛选。"""
        qs = ClothRoll.objects.select_related("loft").order_by("loft_id", "roll_code", "id")
        loft_id = request.query_params.get("loftId")
        if loft_id:
            qs = qs.filter(loft_id=loft_id)

        rolls = list(qs)
        dipping = {}
        for roll in rolls:
            if roll.status == ClothRoll.STATUS_DIPPING:
                dipping.setdefault(roll.loft_id, []).append(roll)

        results = []
        for roll in rolls:
            neighbors = [
                n for n in dipping.get(roll.loft_id, []) if n.id != roll.id
            ]
            neighbor_diffs = [
                {
                    "rollCode": n.roll_code,
                    "fabricWeightGsm": n.fabric_weight_gsm,
                    "diff": abs(roll.fabric_weight_gsm - n.fabric_weight_gsm),
                }
                for n in neighbors
            ]
            diffs = [item["diff"] for item in neighbor_diffs]
            max_diff = max(diffs) if diffs else None
            results.append(
                {
                    "id": roll.id,
                    "loftId": roll.loft_id,
                    "loftName": roll.loft.name,
                    "rollCode": roll.roll_code,
                    "status": roll.status,
                    "fabricWeightGsm": roll.fabric_weight_gsm,
                    "dippingNeighbors": neighbor_diffs,
                    "maxDiff": max_diff,
                    "withinLimit": max_diff is None or max_diff <= MAX_GSM_DELTA_DIPPING,
                }
            )
        return Response({"results": results})


class DipRunViewSet(viewsets.ModelViewSet):
    serializer_class = DipRunSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = DipRun.objects.select_related("roll", "roll__loft").all()
        roll_id = self.request.query_params.get("rollId")
        if roll_id:
            qs = qs.filter(roll_id=roll_id)
        return qs


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dashboard_stats(request):
    data = {
        "loftCount": Loft.objects.count(),
        "rawRollCount": ClothRoll.objects.filter(status=ClothRoll.STATUS_RAW).count(),
        "dippingRollCount": ClothRoll.objects.filter(
            status=ClothRoll.STATUS_DIPPING
        ).count(),
        "curedRollCount": ClothRoll.objects.filter(status=ClothRoll.STATUS_CURED).count(),
        "dipRunCount": DipRun.objects.count(),
    }
    return Response(data)

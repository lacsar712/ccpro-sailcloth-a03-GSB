from django.db import transaction
from django.db.models import Count
from rest_framework import status as http_status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ClothRoll, DipRun, Loft
from .rules import can_start_dipping
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

    def update(self, request, *args, **kwargs):
        # 锁住所属帆布间行：同间并发的状态改单在此串行，
        # 后到者一定能看到先到者已落入的「浸渍中」，从而被克重差规则挡住。
        # 锁顺序固定为先帆布间、后布卷，避免死锁。PUT/PATCH 都经此入口
        #（DRF 的 partial_update 最终也回调 update）。
        with transaction.atomic():
            instance = self.get_object()
            Loft.objects.select_for_update().get(pk=instance.loft_id)
            ClothRoll.objects.select_for_update().get(pk=instance.pk)
            return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        # 新建即标「浸渍中」同样按帆布间串行，避免并发漏比克重差。
        with transaction.atomic():
            loft = serializer.validated_data.get("loft")
            if loft is not None:
                Loft.objects.select_for_update().get(pk=loft.pk)
            serializer.save()

    @action(detail=True, methods=["post"], url_path="start-dipping")
    def start_dipping(self, request, pk=None):
        """
        一次性「原布 → 浸渍中」转换（浸胶工点标浸渍中）。
        - 在帆布间行锁 + 布卷行锁内串行：两名浸胶工几乎同时点同一卷，
          只有一笔成功，落空的一笔返回 409，不重复计入；
        - 同间已有浸渍中卷且克重差超 40：返回 400 中文挡住；
        - 已固化卷不允许改回浸渍中。
        """
        with transaction.atomic():
            roll = self.get_object()
            # 锁顺序固定为先帆布间、后布卷。
            Loft.objects.select_for_update().get(pk=roll.loft_id)
            roll = (
                ClothRoll.objects.select_for_update()
                .select_related("loft")
                .get(pk=roll.pk)
            )

            if roll.status == ClothRoll.STATUS_DIPPING:
                return Response(
                    {
                        "detail": (
                            f"布卷 {roll.roll_code} 已是浸渍中，该转换已由另一名浸胶工完成，"
                            "本次请求未重复计入"
                        )
                    },
                    status=http_status.HTTP_409_CONFLICT,
                )
            if roll.status == ClothRoll.STATUS_CURED:
                return Response(
                    {"detail": f"布卷 {roll.roll_code} 已固化，不能改回浸渍中"},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            ok, msg = can_start_dipping(roll)
            if not ok:
                return Response(
                    {"status": [msg]},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            roll.status = ClothRoll.STATUS_DIPPING
            roll.save(update_fields=["status", "updated_at"])

        serializer = self.get_serializer(roll)
        return Response(serializer.data, status=http_status.HTTP_200_OK)


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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def weight_diff(request):
    """
    只读：克重差对照。按帆布间列出每卷克重、状态，以及与同间
    「浸渍中」邻卷的当前最大克重差与是否超限（阈值 40 gsm）。
    支持 ?loftId= 按间筛选。
    """
    from .rules import MAX_DIPPING_WEIGHT_DIFF

    qs = ClothRoll.objects.select_related("loft").all()
    loft_id = request.query_params.get("loftId")
    if loft_id:
        qs = qs.filter(loft_id=loft_id)
    rolls = list(qs.order_by("loft_id", "id"))

    # 始终以全库浸渍中卷为基准（筛选单间时该集合本就都在结果内）。
    all_dipping = list(
        ClothRoll.objects.filter(status=ClothRoll.STATUS_DIPPING).values(
            "id", "loft_id", "roll_code", "fabric_weight_gsm"
        )
    )
    dipping_map: dict[int, list[dict]] = {}
    for row in all_dipping:
        dipping_map.setdefault(row["loft_id"], []).append(row)

    results = []
    for roll in rolls:
        neighbors = [
            n for n in dipping_map.get(roll.loft_id, []) if n["id"] != roll.id
        ]
        if neighbors:
            diffs = [
                abs(roll.fabric_weight_gsm - n["fabric_weight_gsm"]) for n in neighbors
            ]
            current_diff = max(diffs)
            neighbor_codes = [
                f'{n["roll_code"]}({n["fabric_weight_gsm"]})' for n in neighbors
            ]
        else:
            current_diff = None
            neighbor_codes = []
        results.append(
            {
                "loftId": roll.loft_id,
                "loftName": roll.loft.name,
                "rollId": roll.id,
                "rollCode": roll.roll_code,
                "status": roll.status,
                "fabricWeightGsm": roll.fabric_weight_gsm,
                "dippingNeighbors": neighbor_codes,
                "currentDiff": current_diff,
                "limit": MAX_DIPPING_WEIGHT_DIFF,
                "withinLimit": current_diff is None
                or current_diff <= MAX_DIPPING_WEIGHT_DIFF,
            }
        )
    return Response({"results": results})

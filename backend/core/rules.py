"""帆布浸渍防水台业务规则。"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Q

from .models import ClothRoll, DipRun

MIN_CURE_HOURS_FOR_CURED = Decimal("12")
MAX_DIPPING_WEIGHT_DIFF = 40


def latest_dip_run(roll: ClothRoll) -> DipRun | None:
    return roll.dip_runs.order_by("-started_at", "-id").first()


def dipping_neighbors(roll: ClothRoll):
    """同帆布间其它「浸渍中」邻卷。须在持有帆布间行锁的事务内调用。"""
    return list(
        ClothRoll.objects.filter(loft_id=roll.loft_id, status=ClothRoll.STATUS_DIPPING)
        .exclude(pk=roll.pk)
        .order_by("id")
    )


def can_start_dipping(roll: ClothRoll) -> tuple[bool, str]:
    """
    布卷从「原布」转为「浸渍中」(dipping) 的前提：
    同一帆布间若已有浸渍中卷，本卷与每一卷浸渍中邻卷的克重差绝对值
    都不得超过 40；同间没有浸渍中邻卷则不比较。
    """
    neighbors = dipping_neighbors(roll)
    if not neighbors:
        return True, ""
    for neighbor in neighbors:
        diff = abs(roll.fabric_weight_gsm - neighbor.fabric_weight_gsm)
        if diff > MAX_DIPPING_WEIGHT_DIFF:
            return (
                False,
                f"克重差过大：本卷 {roll.fabric_weight_gsm} gsm 与同帆布间浸渍中卷 "
                f"{neighbor.roll_code}（{neighbor.fabric_weight_gsm} gsm）相差 {diff} gsm，"
                f"超过 {MAX_DIPPING_WEIGHT_DIFF} gsm，不能改为浸渍中",
            )
    return True, ""


def can_mark_roll_cured(roll: ClothRoll) -> tuple[bool, str]:
    """
    布卷转为「已固化」(cured) 的前提：
    最近一条浸渍记录的固化时长已记录，且 >= 12 小时。
    """
    latest = latest_dip_run(roll)
    if latest is None:
        return False, "该布卷尚无浸渍记录，不能标记为已固化"
    if latest.cure_hours is None:
        return False, "最近浸渍记录尚未填写固化时长，不能标记为已固化"
    if latest.cure_hours < MIN_CURE_HOURS_FOR_CURED:
        return (
            False,
            f"最近浸渍固化时长 {latest.cure_hours} 小时低于 {MIN_CURE_HOURS_FOR_CURED} 小时，不能标记为已固化",
        )
    return True, ""

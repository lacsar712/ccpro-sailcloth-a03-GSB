"""帆布浸渍防水台业务规则。"""

from __future__ import annotations

from decimal import Decimal

from .models import ClothRoll, DipRun

MIN_CURE_HOURS_FOR_CURED = Decimal("12")

# 同间已有「浸渍中」邻卷时，新转入浸渍中的布卷与邻卷克重差上限（含 40）
MAX_GSM_DELTA_DIPPING = 40


def latest_dip_run(roll: ClothRoll) -> DipRun | None:
    return roll.dip_runs.order_by("-started_at", "-id").first()


def dipping_neighbors(roll: ClothRoll) -> list[ClothRoll]:
    """同一帆布间内除自身外的「浸渍中」布卷。"""
    return list(
        ClothRoll.objects.filter(
            loft_id=roll.loft_id, status=ClothRoll.STATUS_DIPPING
        ).exclude(pk=roll.pk)
    )


def weight_diff_against_dipping(
    roll: ClothRoll, weight: int | None = None
) -> tuple[int, ClothRoll | None]:
    """
    与同间浸渍中邻卷的最大克重差（绝对值）。
    同间没有浸渍中邻卷时返回 (0, None)，表示无需比较。
    """
    gsm = roll.fabric_weight_gsm if weight is None else weight
    max_diff = 0
    nearest: ClothRoll | None = None
    for neighbor in dipping_neighbors(roll):
        diff = abs(gsm - neighbor.fabric_weight_gsm)
        if diff >= max_diff:
            max_diff = diff
            nearest = neighbor
    return max_diff, nearest


def gsm_blocker(roll: ClothRoll, weight: int | None = None) -> str:
    """
    原布转「浸渍中」时的克重差拦截信息；通过（或无需比较）时返回空串。
    同间没有浸渍中邻卷时不比较、不拦截。
    """
    diff, neighbor = weight_diff_against_dipping(roll, weight)
    if neighbor is None:
        return ""
    if diff > MAX_GSM_DELTA_DIPPING:
        return (
            f"同帆布间已有浸渍中邻卷 {neighbor.roll_code}"
            f"（{neighbor.fabric_weight_gsm}gsm），与本卷克重差 {diff}，"
            f"超过允许上限 {MAX_GSM_DELTA_DIPPING}，不能转入浸渍中"
        )
    return ""


def can_mark_roll_cured(roll: ClothRoll) -> tuple[bool, str]:
    """
    布卷转为「已固化」(cured) 的前提：
    最近一条浸渍记录的固化时长已记录，且 >= 12 小时。
    只认最近浸渍时长，克重差不得掺入固化判断。
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

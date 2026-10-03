import threading
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from .models import ClothRoll, DipRun, Loft


class WeightDiffBase(APITransactionTestCase):
    def setUp(self):
        self.user = self.get_user()
        self.client.force_authenticate(user=self.user)
        self.loft = Loft.objects.create(name="北岸帆布间", location="港区二号库")
        # 同间基准：浸渍中 420
        self.r1 = ClothRoll.objects.create(
            loft=self.loft, roll_code="R-01",
            status=ClothRoll.STATUS_DIPPING, fabric_weight_gsm=420,
        )
        # 原布甲 380：与浸渍中差 40，刚好允许
        self.jia = ClothRoll.objects.create(
            loft=self.loft, roll_code="R-02",
            status=ClothRoll.STATUS_RAW, fabric_weight_gsm=380,
        )
        # 原布乙 500：与浸渍中差 80，应挡住
        self.yi = ClothRoll.objects.create(
            loft=self.loft, roll_code="R-04",
            status=ClothRoll.STATUS_RAW, fabric_weight_gsm=500,
        )

    def get_user(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(username="tester", password="x")

    def start_dipping(self, roll_id):
        return self.client.post(f"/api/rolls/{roll_id}/start-dipping/")


class DippingWeightRuleTests(WeightDiffBase):
    def test_diff_40_exactly_allowed(self):
        res = self.start_dipping(self.jia.id)
        self.assertEqual(res.status_code, 200, res.content)
        self.jia.refresh_from_db()
        self.assertEqual(self.jia.status, ClothRoll.STATUS_DIPPING)

    def test_diff_80_blocked_with_chinese_message(self):
        res = self.start_dipping(self.yi.id)
        self.assertEqual(res.status_code, 400)
        msg = res.data["status"][0]
        self.assertIn("克重差", msg)
        self.assertIn("80", msg)
        self.yi.refresh_from_db()
        self.assertEqual(self.yi.status, ClothRoll.STATUS_RAW)

    def test_general_patch_also_blocks_overweight(self):
        # 通用 PATCH 路径同样受克重差约束，不能从缝里挤进浸渍中。
        res = self.client.patch(f"/api/rolls/{self.yi.id}/", {"status": "dipping"})
        self.assertEqual(res.status_code, 400)
        self.yi.refresh_from_db()
        self.assertEqual(self.yi.status, ClothRoll.STATUS_RAW)

    def test_no_dipping_neighbor_skips_comparison(self):
        other = Loft.objects.create(name="空帆布间")
        solo = ClothRoll.objects.create(
            loft=other, roll_code="S-1",
            status=ClothRoll.STATUS_RAW, fabric_weight_gsm=200,
        )
        res = self.start_dipping(solo.id)
        self.assertEqual(res.status_code, 200, res.content)
        solo.refresh_from_db()
        self.assertEqual(solo.status, ClothRoll.STATUS_DIPPING)

    def test_yi_blocked_even_after_jia_succeeds(self):
        # 甲先进；乙对两卷浸渍中邻卷的差（80/120）仍超限。
        self.assertEqual(self.start_dipping(self.jia.id).status_code, 200)
        res = self.start_dipping(self.yi.id)
        self.assertEqual(res.status_code, 400)
        self.yi.refresh_from_db()
        self.assertEqual(self.yi.status, ClothRoll.STATUS_RAW)


class CureRuleIgnoresWeightTests(WeightDiffBase):
    def _set_cure_hours(self, roll, hours):
        DipRun.objects.create(
            roll=roll,
            started_at=timezone.now() - timedelta(hours=10),
            resin_pct=Decimal("28.0"),
            cure_hours=Decimal(str(hours)),
        )

    def test_cure_requires_12_hours(self):
        self._set_cure_hours(self.jia, 4)
        self.jia.status = ClothRoll.STATUS_DIPPING
        self.jia.save()
        res = self.client.patch(f"/api/rolls/{self.jia.id}/", {"status": "cured"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("12", res.data["status"][0])

    def test_weight_diff_never_enters_cure_decision(self):
        # 固化只认最近浸渍满 12 小时：即便与同间浸渍中卷差极大，也允许固化。
        heavy = ClothRoll.objects.create(
            loft=self.loft, roll_code="R-9",
            status=ClothRoll.STATUS_DIPPING, fabric_weight_gsm=900,
        )
        self._set_cure_hours(heavy, 14)
        res = self.client.patch(f"/api/rolls/{heavy.id}/", {"status": "cured"})
        self.assertEqual(res.status_code, 200, res.content)
        heavy.refresh_from_db()
        self.assertEqual(heavy.status, ClothRoll.STATUS_CURED)


class WeightDiffEndpointTests(WeightDiffBase):
    def test_endpoint_lists_diffs(self):
        res = self.client.get("/api/weight-diff/")
        self.assertEqual(res.status_code, 200)
        by_code = {r["rollCode"]: r for r in res.data["results"]}
        self.assertIsNone(by_code["R-01"]["currentDiff"])  # 自身即唯一浸渍中
        self.assertEqual(by_code["R-02"]["currentDiff"], 40)
        self.assertTrue(by_code["R-02"]["withinLimit"])
        self.assertEqual(by_code["R-04"]["currentDiff"], 80)
        self.assertFalse(by_code["R-04"]["withinLimit"])
        self.assertEqual(by_code["R-04"]["dippingNeighbors"], ["R-01(420)"])
        self.assertEqual(by_code["R-04"]["limit"], 40)

    def test_endpoint_filter_by_loft(self):
        other = Loft.objects.create(name="另一间")
        ClothRoll.objects.create(
            loft=other, roll_code="X-1",
            status=ClothRoll.STATUS_RAW, fabric_weight_gsm=300,
        )
        res = self.client.get(f"/api/weight-diff/?loftId={other.id}")
        codes = {r["rollCode"] for r in res.data["results"]}
        self.assertEqual(codes, {"X-1"})

    def test_endpoint_read_only(self):
        for method in ("post", "put", "patch", "delete"):
            res = getattr(self.client, method)("/api/weight-diff/")
            self.assertEqual(res.status_code, 405)


class ConcurrentDippingTests(WeightDiffBase):
    def _worker(self, roll_id, barrier, outcomes):
        from rest_framework.test import APIClient
        from django.contrib.auth import get_user_model
        from django.db import connection
        client = APIClient()
        client.force_authenticate(user=get_user_model().objects.get(username="tester"))
        barrier.wait()
        try:
            outcomes.append(client.post(f"/api/rolls/{roll_id}/start-dipping/").status_code)
        finally:
            # 该线程持有独立的数据库连接，用完即关，避免测试库销毁时残留会话。
            connection.close()

    def test_two_workers_one_success(self):
        barrier = threading.Barrier(2)
        outcomes = []
        threads = [
            threading.Thread(target=self._worker, args=(self.jia.id, barrier, outcomes))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(outcomes), [200, 409], outcomes)
        self.jia.refresh_from_db()
        self.assertEqual(self.jia.status, ClothRoll.STATUS_DIPPING)

    def test_yi_never_squeezes_in_under_concurrency(self):
        # 甲的两笔并发 + 乙的两笔并发同时发出。
        barrier = threading.Barrier(4)
        outcomes_jia, outcomes_yi = [], []
        threads = (
            [threading.Thread(target=self._worker, args=(self.jia.id, barrier, outcomes_jia)) for _ in range(2)]
            + [threading.Thread(target=self._worker, args=(self.yi.id, barrier, outcomes_yi)) for _ in range(2)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(sorted(outcomes_jia), [200, 409], outcomes_jia)
        self.assertTrue(all(code == 400 for code in outcomes_yi), outcomes_yi)
        self.jia.refresh_from_db()
        self.yi.refresh_from_db()
        self.assertEqual(self.jia.status, ClothRoll.STATUS_DIPPING)
        self.assertEqual(self.yi.status, ClothRoll.STATUS_RAW)

    def test_rack_still_opens_after_rejection(self):
        self.assertEqual(self.start_dipping(self.yi.id).status_code, 400)
        res = self.client.get("/api/rolls/")
        self.assertEqual(res.status_code, 200)


class SeedCommandTests(APITransactionTestCase):
    def test_seed_idempotent_and_has_yi(self):
        call_command("seed_data")
        call_command("seed_data")
        self.assertEqual(ClothRoll.objects.filter(roll_code="R-04").count(), 1)
        yi = ClothRoll.objects.get(roll_code="R-04")
        self.assertEqual(yi.fabric_weight_gsm, 500)
        self.assertEqual(yi.status, ClothRoll.STATUS_RAW)
        jia = ClothRoll.objects.get(roll_code="R-02")
        self.assertEqual(jia.fabric_weight_gsm, 380)
        base = ClothRoll.objects.get(roll_code="R-01")
        self.assertEqual(base.fabric_weight_gsm, 420)
        self.assertEqual(base.status, ClothRoll.STATUS_DIPPING)
        # 重跑不产生重复浸渍记录
        self.assertEqual(DipRun.objects.filter(roll=base).count(), 1)

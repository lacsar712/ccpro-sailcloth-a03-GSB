from decimal import Decimal
from threading import Barrier, Thread

from django.core.management import call_command
from django.db import connection
from rest_framework.test import APIClient, APITransactionTestCase

from .models import ClothRoll, DipRun, Loft


class GsmRuleTests(APITransactionTestCase):
    def setUp(self):
        call_command("seed_data", verbosity=0)
        self.client = APIClient()
        from django.contrib.auth import get_user_model

        self.admin = get_user_model().objects.get(username="admin")
        self.client.force_authenticate(self.admin)
        self.r1 = ClothRoll.objects.get(roll_code="R-01")  # dipping 420
        self.r2 = ClothRoll.objects.get(roll_code="R-02")  # raw 380 原布甲
        self.r3 = ClothRoll.objects.get(roll_code="R-03")  # raw 500 原布乙

    def patch_status(self, roll_id, status):
        return self.client.patch(f"/api/rolls/{roll_id}/", {"status": status}, format="json")

    def test_seed_weights(self):
        self.assertEqual(self.r1.status, ClothRoll.STATUS_DIPPING)
        self.assertEqual(self.r1.fabric_weight_gsm, 420)
        self.assertEqual(self.r2.status, ClothRoll.STATUS_RAW)
        self.assertEqual(self.r2.fabric_weight_gsm, 380)
        self.assertEqual(self.r3.status, ClothRoll.STATUS_RAW)
        self.assertEqual(self.r3.fabric_weight_gsm, 500)

    def test_diff_40_boundary_allowed(self):
        resp = self.patch_status(self.r2.id, "dipping")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.r2.refresh_from_db()
        self.assertEqual(self.r2.status, ClothRoll.STATUS_DIPPING)

    def test_diff_80_blocked_chinese(self):
        resp = self.patch_status(self.r3.id, "dipping")
        self.assertEqual(resp.status_code, 400)
        msg = resp.data["status"][0]
        self.assertIn("克重差", msg)
        self.assertIn("80", msg)
        self.r3.refresh_from_db()
        self.assertEqual(self.r3.status, ClothRoll.STATUS_RAW)

    def test_乙_always_blocked_even_after_甲_enters(self):
        # 甲（差 40）先进浸渍中
        self.assertEqual(self.patch_status(self.r2.id, "dipping").status_code, 200)
        # 乙此时与浸渍中邻卷（420/380）最大差 120，仍不得挤进
        resp = self.patch_status(self.r3.id, "dipping")
        self.assertEqual(resp.status_code, 400)
        self.r3.refresh_from_db()
        self.assertEqual(self.r3.status, ClothRoll.STATUS_RAW)

    def test_no_dipping_neighbor_no_compare(self):
        loft = Loft.objects.create(name="空帆布间")
        solo = ClothRoll.objects.create(
            loft=loft, roll_code="S-1", status=ClothRoll.STATUS_RAW,
            fabric_weight_gsm=999,
        )
        resp = self.patch_status(solo.id, "dipping")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_cure_only_checks_12h_not_weight(self):
        # 克重差 699（远超 40），但最近浸渍 13 小时：固化只认时长，应成功
        loft = Loft.objects.create(name="固化对照间")
        ClothRoll.objects.create(
            loft=loft, roll_code="D-0", status=ClothRoll.STATUS_DIPPING,
            fabric_weight_gsm=300,
        )
        heavy = ClothRoll.objects.create(
            loft=loft, roll_code="D-1", status=ClothRoll.STATUS_RAW,
            fabric_weight_gsm=999,
        )
        DipRun.objects.create(
            roll=heavy, started_at=_now(), resin_pct=Decimal("28"),
            cure_hours=Decimal("13.0"),
        )
        resp = self.patch_status(heavy.id, "cured")
        self.assertEqual(resp.status_code, 200, resp.content)
        heavy.refresh_from_db()
        self.assertEqual(heavy.status, ClothRoll.STATUS_CURED)

    def test_cure_under_12h_still_blocked(self):
        # R-02 最近浸渍 4 小时；即便克重差刚好 40，也不能固化
        resp = self.patch_status(self.r2.id, "cured")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("12", resp.data["status"][0])

    def test_weight_diff_endpoint(self):
        resp = self.client.get("/api/rolls/weight-diffs/")
        by_code = {r["rollCode"]: r for r in resp.data["results"]}
        self.assertIsNone(by_code["R-01"]["maxDiff"])
        self.assertEqual(by_code["R-02"]["maxDiff"], 40)
        self.assertTrue(by_code["R-02"]["withinLimit"])
        self.assertEqual(by_code["R-03"]["maxDiff"], 80)
        self.assertFalse(by_code["R-03"]["withinLimit"])
        nbr = by_code["R-03"]["dippingNeighbors"][0]
        self.assertEqual(nbr["rollCode"], "R-01")
        self.assertEqual(nbr["diff"], 80)

        # 按间筛选
        loft = Loft.objects.create(name="别的间")
        ClothRoll.objects.create(loft=loft, roll_code="X-1", fabric_weight_gsm=100)
        resp2 = self.client.get(f"/api/rolls/weight-diffs/?loftId={loft.id}")
        codes = {r["rollCode"] for r in resp2.data["results"]}
        self.assertEqual(codes, {"X-1"})

    def test_concurrent_double_submit_only_one_succeeds(self):
        """两名浸胶工几乎同时把原布甲改成浸渍中：只许一笔成功。"""
        import core.serializers as ser

        barrier = Barrier(2)
        original = ser.gsm_blocker

        def synced_blocker(roll, weight=None):
            # 让两个请求都走到序列化校验之后再一起放行，制造真实竞态
            barrier.wait(timeout=10)
            return original(roll, weight)

        ser.gsm_blocker = synced_blocker
        results = []

        def worker():
            client = APIClient()
            client.force_authenticate(self.admin)
            try:
                r = client.patch(
                    f"/api/rolls/{self.r2.id}/", {"status": "dipping"}, format="json"
                )
                results.append(r.status_code)
            finally:
                connection.close()

        try:
            t1 = Thread(target=worker)
            t2 = Thread(target=worker)
            t1.start(); t2.start()
            t1.join(15); t2.join(15)
        finally:
            ser.gsm_blocker = original

        self.assertEqual(sorted(results), [200, 400], results)
        self.r2.refresh_from_db()
        self.assertEqual(self.r2.status, ClothRoll.STATUS_DIPPING)
        # 乙在甲落库后更不能挤进
        resp = self.patch_status(self.r3.id, "dipping")
        self.assertEqual(resp.status_code, 400)
        self.r3.refresh_from_db()
        self.assertEqual(self.r3.status, ClothRoll.STATUS_RAW)


def _now():
    from django.utils import timezone
    return timezone.now()

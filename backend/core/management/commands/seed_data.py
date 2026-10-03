from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ClothRoll, DipRun, Loft

User = get_user_model()


class Command(BaseCommand):
    help = "初始化演示账号与帆布浸渍种子数据"

    def handle(self, *args, **options):
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@sailcloth.local",
                "role": User.ROLE_ADMIN,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        admin.set_password("123456")
        admin.role = User.ROLE_ADMIN
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()
        self.stdout.write(self.style.SUCCESS(f"admin {'created' if created else 'updated'}"))

        worker, created = User.objects.get_or_create(
            username="worker",
            defaults={
                "email": "worker@sailcloth.local",
                "role": User.ROLE_WORKER,
            },
        )
        worker.set_password("123456")
        worker.role = User.ROLE_WORKER
        worker.save()
        self.stdout.write(self.style.SUCCESS(f"worker {'created' if created else 'updated'}"))

        loft, loft_created = Loft.objects.get_or_create(
            name="北岸帆布间",
            defaults={
                "location": "港区二号库",
                "notes": "浸渍防水台示范 loft",
            },
        )
        # 幂等：按卷号 upsert，已存在的演示库重跑也能补齐原布乙并复位状态。
        r1, _ = ClothRoll.objects.update_or_create(
            loft=loft,
            roll_code="R-01",
            defaults={
                "status": ClothRoll.STATUS_DIPPING,
                "fabric_weight_gsm": 420,
                "notes": "同间基准浸渍中卷 420 gsm",
            },
        )
        r2, _ = ClothRoll.objects.update_or_create(
            loft=loft,
            roll_code="R-02",
            defaults={
                "status": ClothRoll.STATUS_RAW,
                "fabric_weight_gsm": 380,
                "notes": "原布甲：与浸渍中卷差 40 gsm，刚好允许",
            },
        )
        r4, _ = ClothRoll.objects.update_or_create(
            loft=loft,
            roll_code="R-04",
            defaults={
                "status": ClothRoll.STATUS_RAW,
                "fabric_weight_gsm": 500,
                "notes": "原布乙：与浸渍中卷差 80 gsm，应被挡住",
            },
        )
        r3, _ = ClothRoll.objects.update_or_create(
            loft=loft,
            roll_code="R-03",
            defaults={
                "status": ClothRoll.STATUS_CURED,
                "fabric_weight_gsm": 450,
                "notes": "已固化示例卷",
            },
        )

        now = timezone.now()

        def ensure_dip(roll, started_at, resin_pct, cure_hours, notes):
            # 以 (卷, 备注) 为稳定键，重跑不产生重复浸渍记录。
            DipRun.objects.update_or_create(
                roll=roll,
                notes=notes,
                defaults={
                    "started_at": started_at,
                    "resin_pct": resin_pct,
                    "cure_hours": cure_hours,
                },
            )

        ensure_dip(
            r1,
            now - timedelta(hours=8),
            Decimal("28.50"),
            None,
            "固化计时中",
        )
        ensure_dip(
            r2,
            now - timedelta(hours=1),
            Decimal("26.00"),
            Decimal("4.00"),
            "时长不足，不可标 cured",
        )
        ensure_dip(
            r3,
            now - timedelta(days=2),
            Decimal("30.00"),
            Decimal("14.50"),
            "已完成固化",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"种子完成：帆布间 {Loft.objects.count()}，布卷 {ClothRoll.objects.count()}，"
                f"浸渍 {DipRun.objects.count()}（北岸帆布间 {'新建' if loft_created else '已存在，已校验'}）"
            )
        )

from datetime import date
from decimal import Decimal
from itertools import count

from documents.models import PDFTemplate
from finance.models import (
    CommissionRole,
    PaymentMethod,
    PaymentReceipt,
    SaleCommissionScale,
)
from inventory.models import HouseType, Project
from sales.models import Sale
from users.models import RoleCode, User


class Factory:
    _seq = count(1)

    @classmethod
    def _n(cls):
        return next(cls._seq)

    @classmethod
    def user(cls, *, role=RoleCode.ADMIN, password="pass1234", **kwargs):
        n = cls._n()
        defaults = {
            "username": f"user{n}",
            "email": f"user{n}@example.com",
            "is_active": True,
            "role": role,
        }
        defaults.update(kwargs)
        return User.objects.create_user(password=password, **defaults)

    @classmethod
    def project(cls, **kwargs):
        n = cls._n()
        defaults = {
            "name": f"Proyecto {n}",
            "city": "Monteria",
            "finance_rate_monthly": Decimal("1.5000"),
            "mora_rate_monthly": Decimal("2.0000"),
        }
        defaults.update(kwargs)
        return Project.objects.create(**defaults)

    @classmethod
    def house_type(cls, *, project, **kwargs):
        n = cls._n()
        defaults = {
            "project": project,
            "name": f"Tipo {n}",
            "base_price": Decimal("390000000.00"),
        }
        defaults.update(kwargs)
        return HouseType.objects.create(**defaults)

    @classmethod
    def sale(cls, *, project, house_type, status=Sale.State.PENDING, **kwargs):
        n = cls._n()
        defaults = {
            "project": project,
            "house_type": house_type,
            "status": status,
            "contract_number": n,
            "final_price": Decimal("390000000.00"),
            "lot_metadata": {"id_inmueble": f"INM-{n}"},
        }
        defaults.update(kwargs)
        return Sale.objects.create(**defaults)

    @classmethod
    def commission_role(cls, **kwargs):
        n = cls._n()
        defaults = {"name": f"Rol Comision {n}"}
        defaults.update(kwargs)
        return CommissionRole.objects.create(**defaults)

    @classmethod
    def commission_scale(cls, *, sale, user, role, percentage="3.00"):
        return SaleCommissionScale.objects.create(
            sale=sale,
            user=user,
            role=role,
            percentage=Decimal(str(percentage)),
        )

    @classmethod
    def payment_method(cls, *, project, **kwargs):
        n = cls._n()
        defaults = {"project": project, "name": f"Metodo {n}"}
        defaults.update(kwargs)
        return PaymentMethod.objects.create(**defaults)

    @classmethod
    def receipt(
        cls,
        *,
        sale,
        created_by,
        payment_method=None,
        amount="5000000.00",
        date_paid_value=None,
        **kwargs,
    ):
        if payment_method is None:
            payment_method = cls.payment_method(project=sale.project)
        if date_paid_value is None:
            date_paid_value = date(2026, 2, 11)
        defaults = {
            "sale": sale,
            "amount": Decimal(str(amount)),
            "date_paid": date_paid_value,
            "payment_method": payment_method,
            "created_by": created_by,
            "file_hash": f"receipt-{cls._n()}",
        }
        defaults.update(kwargs)
        return PaymentReceipt.objects.create(**defaults)

    @classmethod
    def pdf_template(cls, *, created_by, **kwargs):
        n = cls._n()
        defaults = {
            "name": f"Template {n}",
            "slug": f"template-{n}",
            "target_path": f"contracts/template-{n}.html",
            "created_by": created_by,
        }
        defaults.update(kwargs)
        return PDFTemplate.objects.create(**defaults)

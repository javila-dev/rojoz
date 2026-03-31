from django.core.management.base import BaseCommand

from users.models import RoleCode, RolePermission
from users.permissions import list_permission_candidates, PERMISSION_LABELS


def _grant(role_code, key, label, path):
    RolePermission.objects.update_or_create(
        role_code=role_code,
        permission_key=key,
        defaults={"allowed": True, "label": label, "path": path},
    )


def _is_users_read_action(key: str) -> bool:
    return key in {
        "users:dashboard",
        "users:profile",
        "users:integrations",
    }


class Command(BaseCommand):
    help = "Carga una matriz inicial de permisos por rol (fail-closed safe defaults)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Elimina permisos existentes antes de cargar la matriz.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            RolePermission.objects.all().delete()
            self.stdout.write(self.style.WARNING("Permisos existentes eliminados."))

        candidates = list_permission_candidates()
        by_key = {c.key: c for c in candidates}

        for key, candidate in by_key.items():
            label = PERMISSION_LABELS.get(key, candidate.label)
            path = candidate.path

            # ADMIN y GERENTE: acceso total funcional (no superuser)
            _grant(RoleCode.ADMIN, key, label, path)
            _grant(RoleCode.GERENTE, key, label, path)

            # DIRECTOR: foco comercial
            if key.startswith("sales:") or key in {
                "users:dashboard",
                "users:profile",
                "finance:sale_commission_scale_list",
                "finance:sale_commission_scale_create",
                "finance:sale_commission_scale_edit",
                "finance:sale_commission_scale_delete",
                "finance:sale_commission_scale_generate",
                "finance:project_commission_role_list",
                "finance:project_commission_role_create",
                "finance:project_commission_role_edit",
                "finance:project_commission_role_delete",
                "finance:commission_role_list",
                "finance:commission_liquidation_queue",
                "sales:contract_party_list",
            }:
                _grant(RoleCode.DIRECTOR, key, label, path)

            # TESORERIA: foco financiero
            if key.startswith("finance:") or key in {
                "users:dashboard",
                "users:profile",
                "sales:contract_detail",
                "sales:sale_document_view",
                "sales:contract_party_list",
                "sales:contract_list_approved",
                "sales:contract_status_select",
                "sales:contract_project_select",
            }:
                _grant(RoleCode.TESORERIA, key, label, path)

            # SUPERVISOR: lectura operativa
            if key.startswith("inventory:") or key in {
                "users:dashboard",
                "users:profile",
                "sales:contract_party_list",
                "sales:contract_project_select",
                "sales:contract_status_select",
                "sales:contract_list_pending",
                "sales:contract_list_approved",
                "sales:contract_detail",
                "sales:sale_document_view",
            }:
                _grant(RoleCode.SUPERVISOR, key, label, path)

            # ASESOR: flujo comercial básico
            if key in {
                "users:dashboard",
                "users:profile",
                "sales:sale_flow_project",
                "sales:sale_flow_lots",
                "sales:sale_flow_finishes",
                "sales:sale_flow_payment",
                "sales:sale_flow_payment_preview",
                "sales:sale_flow_payment_confirm",
                "sales:contract_project_select",
                "sales:contract_status_select",
                "sales:contract_list_pending",
                "sales:contract_list_approved",
                "sales:contract_detail",
                "sales:contract_party_list",
                "sales:sale_document_view",
                "sales:contract_pdf",
                "sales:pagare_pdf",
                "sales:contract_schedule_pdf",
                "finance:sale_commission_scale_list",
                "finance:receipt_request_list",
                "finance:receipt_request_create",
                "finance:receipt_request_detail",
            } or _is_users_read_action(key):
                _grant(RoleCode.ASESOR, key, label, path)

        total = RolePermission.objects.filter(allowed=True).count()
        self.stdout.write(self.style.SUCCESS(f"Permisos cargados: {total}"))

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token
from roster.models import Membership, Office, Tenant


class Command(BaseCommand):
    help = "Create a local demo tenant, office and operator; prints its API token."

    def handle(self, *args, **options):
        tenant, _ = Tenant.objects.get_or_create(name="Demo Company")
        Office.objects.get_or_create(tenant=tenant, code="BLR", defaults={"name": "Bengaluru"})
        user, _ = get_user_model().objects.get_or_create(username="demo-operator")
        Membership.objects.update_or_create(user=user, defaults={"tenant": tenant, "role": "operator"})
        token, _ = Token.objects.get_or_create(user=user)
        self.stdout.write(f"Demo tenant ID: {tenant.id}")
        self.stdout.write(f"Operator API token: {token.key}")

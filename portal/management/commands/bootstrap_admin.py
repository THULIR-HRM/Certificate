import os

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import AdminProfile


class Command(BaseCommand):
    help = "Create the single portal administrator from environment configuration."

    @transaction.atomic
    def handle(self, *args, **options):
        username = settings.ADMIN_USERNAME
        existing_admins = User.objects.filter(is_staff=True)
        if existing_admins.exclude(username=username).exists():
            raise CommandError(
                "A different staff account already exists; refusing to create a second administrator."
            )

        user = User.objects.filter(username=username).first()
        if user is None:
            password = os.environ.get("ADMIN_INITIAL_PASSWORD", "")
            if not password:
                raise CommandError("Set ADMIN_INITIAL_PASSWORD before creating the administrator.")
            if User.objects.filter(is_staff=True).exists():
                raise CommandError("Only one staff account is allowed.")
            user = User(username=username, email=username, is_staff=True, is_superuser=False)
            user.set_password(password)
            user.save()
            AdminProfile.objects.create(user=user, force_password_change=True)
            self.stdout.write(self.style.SUCCESS("The single administrator was created."))
            return

        if existing_admins.exclude(pk=user.pk).exists():
            raise CommandError("Only one staff account is allowed.")
        if not user.is_staff:
            user.is_staff = True
            user.is_superuser = False
            user.save(update_fields=["is_staff", "is_superuser"])
        AdminProfile.objects.get_or_create(user=user, defaults={"force_password_change": True})
        self.stdout.write(self.style.SUCCESS("The administrator account is ready."))

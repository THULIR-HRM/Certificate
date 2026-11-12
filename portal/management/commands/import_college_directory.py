import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import CollegeCategory, CollegeDistrict, CollegeOption


DATASET = Path("portal/data/ugc_tamil_nadu_colleges.csv")
REQUIRED_COLUMNS = {
    "name",
    "location",
    "address",
    "state",
    "district",
    "category",
    "affiliation",
    "aliases",
    "source_url",
}


class Command(BaseCommand):
    help = "Import the verified Tamil Nadu UGC college directory snapshot."

    def handle(self, *args, **options):
        source = settings.BASE_DIR / DATASET
        if not source.is_file():
            raise CommandError(f"College directory dataset not found: {source}")

        with source.open(encoding="utf-8", newline="") as dataset:
            reader = csv.DictReader(dataset)
            if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                raise CommandError("College directory dataset has missing required columns.")
            rows = list(reader)

        created_count = 0
        existing_count = 0
        with transaction.atomic():
            for row in rows:
                district, _ = CollegeDistrict.objects.get_or_create(name=row["district"])
                category, _ = CollegeCategory.objects.get_or_create(name=row["category"])
                existing = CollegeOption.objects.filter(
                    district=district,
                    name__iexact=row["name"],
                    location__iexact=row["location"],
                ).first()
                if existing:
                    existing_count += 1
                    continue
                aliases = [
                    alias.strip()
                    for alias in row["aliases"].split("|")
                    if alias.strip()
                ]
                CollegeOption.objects.create(
                    name=row["name"],
                    location=row["location"],
                    address=row["address"],
                    state=row["state"],
                    district=district,
                    category=category,
                    affiliation=row["affiliation"],
                    aliases=aliases,
                    source_url=row["source_url"],
                    active=True,
                )
                created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"College directory import complete: {created_count} added, "
                f"{existing_count} already present."
            )
        )

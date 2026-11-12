import zipfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import CertificateTemplate


SOURCES = {
    CertificateTemplate.Category.OFFER: (
        "Vaishnavi N - Offer Letter (1).docx",
        "Vaishnavi N - Offer Letter (1).pdf",
    ),
    CertificateTemplate.Category.EXPERIENCE: (
        "Mr. Kishore Kumar L -  EC.docx",
        "Mr. Kishore Kumar L -  EC.pdf",
    ),
    CertificateTemplate.Category.PROJECT: (
        "Ms. Renuka B M - PC.docx",
        "Ms. Renuka B M - PC.pdf",
    ),
    CertificateTemplate.Category.LOR: (
        "Mr. Kishore Kumar L - LOR .docx",
        "Mr. Kishore Kumar L - LOR .pdf",
    ),
}


def validate_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            total_size = sum(info.file_size for info in archive.infolist())
            if "word/document.xml" not in names or total_size > 50 * 1024 * 1024:
                raise CommandError(f"{path.name} is not a valid or reasonably sized DOCX.")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise CommandError(f"Macro-enabled document is not allowed: {path.name}")
    except zipfile.BadZipFile as error:
        raise CommandError(f"{path.name} is not a valid DOCX archive.") from error


class Command(BaseCommand):
    help = "Import the selected source DOCX/PDF files as private, versioned templates."

    @transaction.atomic
    def handle(self, *args, **options):
        source_dir = settings.TEMPLATE_SOURCE_DIR
        if not source_dir.is_dir():
            raise CommandError(f"Template source directory does not exist: {source_dir}")

        missing = [
            str(source_dir / filename)
            for docx_name, pdf_name in SOURCES.values()
            for filename in (docx_name, pdf_name)
            if not (source_dir / filename).is_file()
        ]
        if missing:
            raise CommandError("Missing selected template files:\n" + "\n".join(missing))

        for category, (docx_name, pdf_name) in SOURCES.items():
            source_docx = source_dir / docx_name
            source_pdf = source_dir / pdf_name
            validate_docx(source_docx)
            if source_pdf.read_bytes()[:5] != b"%PDF-":
                raise CommandError(f"{source_pdf.name} is not a valid PDF.")

            current = (
                CertificateTemplate.objects.filter(category=category, active=True)
                .order_by("-version")
                .first()
            )
            if current and current.original_filename == docx_name:
                self.stdout.write(f"Already imported: {docx_name}")
                continue

            version = (
                CertificateTemplate.objects.filter(category=category)
                .order_by("-version")
                .values_list("version", flat=True)
                .first()
                or 0
            ) + 1
            CertificateTemplate.objects.filter(category=category, active=True).update(active=False)
            template = CertificateTemplate(
                category=category,
                version=version,
                original_filename=docx_name,
                active=True,
            )
            with source_docx.open("rb") as document_file:
                template.document.save(docx_name, File(document_file), save=False)
            with source_pdf.open("rb") as pdf_file:
                template.pdf_source.save(pdf_name, File(pdf_file), save=False)
            template.save()
            self.stdout.write(self.style.SUCCESS(f"Imported {category} template v{version}."))

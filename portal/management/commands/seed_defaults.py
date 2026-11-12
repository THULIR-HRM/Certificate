from django.core.management.base import BaseCommand

from portal.catalog_data import (
    COLLEGE_CATEGORIES,
    DEGREES,
    SPECIALIZATIONS,
    TAMIL_NADU_DISTRICTS,
)
from portal.models import (
    CollegeCategory,
    CollegeDistrict,
    CollegeOption,
    DegreeOption,
    Domain,
    FormFieldSetting,
    OfferProgramSettings,
    SpecializationOption,
)


DOMAINS = [
    # (name, is_technical)
    ("Data Science", True),
    ("Data Analytics", True),
    ("Machine Learning", True),
    ("FSD Java", True),
    ("FSD Python", True),
    ("HRM", False),
    ("Digital Marketing", False),
    ("Medical Coding", False),
]

COMMON_FIELDS = [
    ("title", "Title", "Select title", True),
    ("name", "Name", "Enter your full name", True),
    ("gender", "Gender", "", True),
    ("mobile", "Mobile Number", "10-digit mobile number", True),
    ("registration_number", "University Registration Number", "Enter your registration number", True),
    ("registered_email", "Registered Gmail", "name@gmail.com", True),
    ("alternative_email", "Alternative Gmail (optional)", "name@gmail.com", False),
    ("degree_department", "Degree and Department", "e.g. B.E (Computer Science and Engineering)", True),
    ("college", "College Name and Location", "College name, City", True),
    ("domain", "Domain", "", True),
]


class Command(BaseCommand):
    help = "Create the initial domains and student form settings without overwriting admin changes."

    def handle(self, *args, **options):
        district_renames = {
            "Kancheepuram": "Kanchipuram",
            "Kanniyakumari": "Kanyakumari",
            "Tirupathur": "Tirupattur",
            "Viluppuram": "Villupuram",
        }
        for old_name, new_name in district_renames.items():
            district = CollegeDistrict.objects.filter(name=old_name).first()
            if district and not CollegeDistrict.objects.filter(name=new_name).exists():
                district.name = new_name
                district.save(update_fields=["name"])

        for abbreviation, full_name, aliases in DEGREES:
            degree, _ = DegreeOption.objects.get_or_create(
                abbreviation=abbreviation,
                defaults={
                    "full_name": full_name,
                    "aliases": aliases,
                },
            )
            for name in SPECIALIZATIONS[abbreviation]:
                if name.casefold() == "other":
                    continue
                SpecializationOption.objects.get_or_create(degree=degree, name=name)

        districts = {
            name: CollegeDistrict.objects.get_or_create(name=name)[0]
            for name in TAMIL_NADU_DISTRICTS
        }
        categories = {
            name: CollegeCategory.objects.get_or_create(name=name)[0]
            for name in COLLEGE_CATEGORIES
        }
        CollegeOption.objects.get_or_create(
            name="Dr. Mahalingam College of Engineering and Technology",
            location="Pollachi",
            district=districts["Coimbatore"],
            defaults={
                "category": categories["Engineering"],
                "affiliation": "Anna University",
                "source_url": "https://drmcet.ac.in/",
            },
        )

        for name, is_technical in DOMAINS:
            Domain.objects.get_or_create(name=name, defaults={"active": True, "is_technical": is_technical})

        for form_type in [FormFieldSetting.FormType.OFFER, FormFieldSetting.FormType.INTERNSHIP]:
            for position, (key, label, placeholder, required) in enumerate(COMMON_FIELDS):
                FormFieldSetting.objects.get_or_create(
                    form_type=form_type,
                    key=key,
                    defaults={
                        "label": label,
                        "placeholder": placeholder,
                        "required": required,
                        "display_order": position,
                    },
                )

        extra_fields = [
            ("from_month", "From Month and Year", "", True),
            ("to_month", "To Month and Year", "", True),
            ("project_title", "Project Title", "Enter your project title", True),
            ("feedback", "How was your internship experience?", "Share your feedback", True),
        ]
        for position, (key, label, placeholder, required) in enumerate(extra_fields, len(COMMON_FIELDS)):
            FormFieldSetting.objects.get_or_create(
                form_type=FormFieldSetting.FormType.INTERNSHIP,
                key=key,
                defaults={
                    "label": label,
                    "placeholder": placeholder,
                    "required": required,
                    "display_order": position,
                },
            )

        OfferProgramSettings.objects.get_or_create(singleton=True)
        self.stdout.write(self.style.SUCCESS("Default domains and form settings are ready."))

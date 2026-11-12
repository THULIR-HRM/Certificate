import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .catalog_data import normalize_degree_label


class AdminProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    force_password_change = models.BooleanField(default=True)


class AdminLoginThrottle(models.Model):
    address = models.GenericIPAddressField(unique=True)
    failures = models.PositiveSmallIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)


class Domain(models.Model):
    name = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)
    is_technical = models.BooleanField(
        default=True,
        verbose_name="Technical domain",
        help_text=(
            "Check if this is a technical domain (e.g. ML, Data Science, FSD). "
            "Technical domains print 'Technical Internship' on the offer letter. "
            "Non-technical domains print '[Domain Name] Internship'."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DegreeOption(models.Model):
    abbreviation = models.CharField(max_length=30, unique=True)
    full_name = models.CharField(max_length=120)
    aliases = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["abbreviation"]

    def clean(self):
        labels = [self.abbreviation, self.full_name, *self.aliases]
        normalized = [normalize_degree_label(label) for label in labels if label]
        if len(normalized) != len(set(normalized)):
            raise ValidationError("A degree abbreviation, full name, and aliases must be distinct.")
        if self.aliases and not all(isinstance(alias, str) and alias.strip() for alias in self.aliases):
            raise ValidationError("Degree aliases must be non-empty text values.")
        for other in DegreeOption.objects.exclude(pk=self.pk):
            other_labels = [other.abbreviation, other.full_name, *other.aliases]
            other_normalized = {normalize_degree_label(label) for label in other_labels if label}
            if other_normalized.intersection(normalized):
                raise ValidationError("This degree name or alias is already assigned to another degree.")

    def __str__(self):
        return f"{self.abbreviation} – {self.full_name}"


class SpecializationOption(models.Model):
    degree = models.ForeignKey(
        DegreeOption, on_delete=models.PROTECT, related_name="specializations"
    )
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["degree__abbreviation", "name"]
        constraints = [
            models.UniqueConstraint(fields=["degree", "name"], name="unique_degree_specialization")
        ]

    def __str__(self):
        return f"{self.degree.abbreviation} - {self.name}"


class CollegeDistrict(models.Model):
    name = models.CharField(max_length=80, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CollegeCategory(models.Model):
    name = models.CharField(max_length=60, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CollegeOption(models.Model):
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=120)
    address = models.CharField(max_length=500, blank=True)
    state = models.CharField(max_length=80, default="Tamil Nadu")
    district = models.ForeignKey(
        CollegeDistrict, on_delete=models.PROTECT, related_name="colleges"
    )
    category = models.ForeignKey(
        CollegeCategory, on_delete=models.PROTECT, related_name="colleges"
    )
    affiliation = models.CharField(max_length=160, blank=True)
    aliases = models.JSONField(default=list, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "location"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "location", "district"], name="unique_college_location_district"
            )
        ]

    def __str__(self):
        return f"{self.name}, {self.location}"


class StudentSubmission(models.Model):
    class Source(models.TextChoices):
        OFFER = "offer", "Offer Letter"
        INTERNSHIP = "internship", "Other Certificates"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    submission_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    submission_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.CharField(max_length=12, choices=Source.choices)
    title = models.CharField(max_length=4)
    name = models.CharField(max_length=150)
    gender = models.CharField(max_length=8, choices=Gender.choices)
    mobile = models.CharField(max_length=10)
    registration_number = models.CharField(max_length=100)
    registered_email = models.EmailField()
    alternative_email = models.EmailField(blank=True)
    degree_department = models.CharField(max_length=160)
    college_name = models.CharField(max_length=200)
    college_location = models.CharField(max_length=120)
    domain = models.ForeignKey(Domain, on_delete=models.PROTECT, related_name="submissions")
    internship_start = models.DateField(null=True, blank=True)
    internship_end = models.DateField(null=True, blank=True)
    project_title = models.CharField(max_length=200, blank=True)
    feedback = models.CharField(max_length=2000, blank=True)
    custom_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source", "created_at"]),
            models.Index(fields=["registration_number"]),
            models.Index(fields=["name"]),
        ]

    @property
    def duration_label(self):
        if not self.internship_start or not self.internship_end:
            return ""
        return (
            f"{self.internship_start.strftime('%B %Y')} to "
            f"{self.internship_end.strftime('%B %Y')}"
        )

    def __str__(self):
        return f"{self.name} ({self.submission_id})"


class CertificateRequest(models.Model):
    class Category(models.TextChoices):
        OFFER = "offer", "Offer Letter"
        EXPERIENCE = "experience", "Experience Certificate"
        PROJECT = "project", "Project Completion Certificate"
        LOR = "lor", "Letter of Recommendation"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        GENERATED = "generated", "Generated"

    submission = models.ForeignKey(
        StudentSubmission, on_delete=models.CASCADE, related_name="certificate_requests"
    )
    category = models.CharField(max_length=12, choices=Category.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.CharField(max_length=500, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_requests"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "category"], name="unique_certificate_per_submission"
            )
        ]
        indexes = [
            models.Index(fields=["category", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} for {self.submission.name}"


class FormFieldSetting(models.Model):
    class FormType(models.TextChoices):
        OFFER = "offer", "Offer Letter Form"
        INTERNSHIP = "internship", "Other Certificates Form"

    class CharacterPolicy(models.TextChoices):
        DEFAULT = "default", "Field-specific validation"
        LETTERS_SPACES = "letters_spaces", "Letters and spaces"
        DIGITS = "digits", "Digits only"
        REGISTRATION = "registration", "Registration separators"
        FREE_TEXT = "free_text", "Free text"

    form_type = models.CharField(max_length=12, choices=FormType.choices)
    key = models.SlugField(max_length=40)
    is_custom = models.BooleanField(default=False)
    label = models.CharField(max_length=100)
    placeholder = models.CharField(max_length=160, blank=True)
    enabled = models.BooleanField(default=True)
    required = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    min_length = models.PositiveSmallIntegerField(null=True, blank=True)
    max_length = models.PositiveSmallIntegerField(null=True, blank=True)
    character_policy = models.CharField(
        max_length=20, choices=CharacterPolicy.choices, default=CharacterPolicy.DEFAULT
    )
    validation_message = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["form_type", "display_order", "key"]
        constraints = [
            models.UniqueConstraint(fields=["form_type", "key"], name="unique_form_field_setting")
        ]


class PortalSetting(models.Model):
    key = models.SlugField(max_length=60, unique=True)
    value = models.CharField(max_length=250, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CertificateTemplate(models.Model):
    class Category(models.TextChoices):
        OFFER = "offer", "Offer Letter"
        EXPERIENCE = "experience", "Experience Certificate"
        PROJECT = "project", "Project Completion Certificate"
        LOR = "lor", "Letter of Recommendation"

    category = models.CharField(max_length=12, choices=Category.choices)
    version = models.PositiveIntegerField()
    original_filename = models.CharField(max_length=255)
    document = models.FileField(upload_to="templates/docx/")
    pdf_source = models.FileField(upload_to="templates/pdf/", blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="uploaded_templates"
    )

    class Meta:
        ordering = ["category", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["category", "version"], name="unique_template_version")
        ]

    def __str__(self):
        return f"{self.get_category_display()} v{self.version}"


class GeneratedCertificate(models.Model):
    class Mode(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    class Format(models.TextChoices):
        DOCX = "docx", "Word"
        PDF = "pdf", "PDF"

    request = models.ForeignKey(
        CertificateRequest, on_delete=models.PROTECT, related_name="generated_files"
    )
    mode = models.CharField(max_length=8, choices=Mode.choices)
    file_format = models.CharField(max_length=4, choices=Format.choices)
    file = models.FileField(upload_to="generated/%Y/%m/")
    file_name = models.CharField(max_length=255)
    content_hash = models.CharField(max_length=64)
    generated_at = models.DateTimeField(auto_now_add=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-generated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "mode", "file_format", "content_hash"],
                name="unique_generated_certificate_content",
            )
        ]
        indexes = [models.Index(fields=["generated_at"])]


class CertificateDownload(models.Model):
    certificate = models.ForeignKey(
        GeneratedCertificate, on_delete=models.CASCADE, related_name="downloads"
    )
    downloaded_at = models.DateTimeField(auto_now_add=True)
    downloaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)


class AuditLog(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    submission = models.ForeignKey(
        StudentSubmission, on_delete=models.SET_NULL, null=True, blank=True
    )
    certificate_request = models.ForeignKey(
        CertificateRequest, on_delete=models.SET_NULL, null=True, blank=True
    )
    action = models.CharField(max_length=40)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["action", "created_at"])]


class OfferProgramSettings(models.Model):
    singleton = models.BooleanField(default=True, unique=True)
    start_month = models.DateField(null=True, blank=True)
    end_month = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.start_month and self.end_month and self.end_month < self.start_month:
            raise ValidationError("The program end month cannot be before its start month.")

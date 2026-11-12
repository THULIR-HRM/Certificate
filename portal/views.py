import io
import re
import uuid
from datetime import timedelta
from functools import wraps
from zipfile import BadZipFile, ZipFile

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AdminLoginForm,
    CertificateDecisionForm,
    CertificateTemplateUploadForm,
    CollegeCategoryForm,
    CollegeDistrictForm,
    CollegeOptionForm,
    CustomFieldForm,
    DegreeOptionForm,
    DomainForm,
    FormFieldSettingForm,
    OfferProgramSettingsForm,
    SpecializationOptionForm,
    StudentSubmissionEditForm,
    StudentSubmissionForm,
)
from .models import (
    AdminLoginThrottle,
    AdminProfile,
    AuditLog,
    CollegeCategory,
    CollegeDistrict,
    CollegeOption,
    CertificateDownload,
    CertificateRequest,
    CertificateTemplate,
    DegreeOption,
    Domain,
    FormFieldSetting,
    GeneratedCertificate,
    OfferProgramSettings,
    SpecializationOption,
    StudentSubmission,
)
from .services.certificates import (
    DocumentGenerationError,
    certificate_content_hash,
    certificate_filename,
    generate_certificate_bytes,
)


def _is_portal_admin(user):
    return (
        user.is_authenticated
        and user.is_staff
        and user.username == settings.ADMIN_USERNAME
        and AdminProfile.objects.filter(user=user).exists()
    )


def portal_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('admin_login')}?next={request.path}")
        if not _is_portal_admin(request.user):
            return HttpResponseForbidden("Administrator access is required.")
        profile = AdminProfile.objects.get(user=request.user)
        if profile.force_password_change and request.resolver_match.url_name != "admin_password_change":
            return redirect("admin_password_change")
        return view_func(request, *args, **kwargs)

    return wrapped


def _audit(request, action, submission=None, certificate_request=None, details=None):
    return AuditLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        submission=submission,
        certificate_request=certificate_request,
        action=action,
        details=details or {},
    )


def landing(request):
    return render(request, "portal/landing.html")


def _domain_for_submission(name):
    if not name:
        return None
    domain = Domain.objects.filter(name__iexact=name).first()
    if domain:
        if not domain.active:
            domain.active = True
            domain.save(update_fields=["active"])
        return domain
    try:
        with transaction.atomic():
            return Domain.objects.create(name=name, active=True)
    except IntegrityError:
        domain = Domain.objects.filter(name__iexact=name).first()
        if domain:
            return domain
        raise


def _registration_catalog_context(form):
    return {
        "degree_catalog": [
            {
                "id": str(item.pk),
                "abbreviation": item.abbreviation,
                "full_name": item.full_name,
                "aliases": item.aliases,
            }
            for item in form.degree_options
        ],
        "specialization_catalog": [
            {
                "id": str(item.pk),
                "degree_id": str(item.degree_id),
                "name": item.name,
            }
            for item in form.specialization_options
            if item.name.casefold() != "other"
        ],
        "college_catalog": [
            {
                "id": str(item.pk),
                "name": item.name,
                "location": item.location,
                "address": item.address,
                "state": item.state,
                "district_id": str(item.district_id),
                "district": item.district.name,
                "category_id": str(item.category_id),
                "category": item.category.name,
                "affiliation": item.affiliation,
                "aliases": item.aliases,
            }
            for item in form.college_options
        ],
    }


def student_register(request, form_type):
    if form_type not in {
        FormFieldSetting.FormType.OFFER,
        FormFieldSetting.FormType.INTERNSHIP,
    }:
        return HttpResponseNotFound("Registration form not found.")

    source = (
        StudentSubmission.Source.OFFER
        if form_type == FormFieldSetting.FormType.OFFER
        else StudentSubmission.Source.INTERNSHIP
    )
    initial = {"submission_token": uuid.uuid4()}
    form = StudentSubmissionForm(request.POST or None, form_type=form_type, initial=initial)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["submission_token"]
        existing = StudentSubmission.objects.filter(submission_token=token).first()
        if existing:
            return render(request, "portal/success.html", {"submission": existing})

        domain_name = form.cleaned_data["new_domain"]
        domain = _domain_for_submission(domain_name) if domain_name else form.cleaned_data["domain"]
        college_name = form.cleaned_data.get("college_name")
        college_location = form.cleaned_data.get("college_location")
        if college_name is None or college_location is None:
            college_name, college_location = form.cleaned_data["college"].split(",", 1)
        defaults = {
            "source": source,
            "title": form.cleaned_data["title"],
            "name": form.cleaned_data["name"],
            "gender": form.cleaned_data["gender"],
            "mobile": form.cleaned_data["mobile"],
            "registration_number": form.cleaned_data["registration_number"],
            "registered_email": form.cleaned_data["registered_email"],
            "alternative_email": form.cleaned_data["alternative_email"],
            "degree_department": form.cleaned_data["degree_department"],
            "college_name": college_name.strip(),
            "college_location": college_location.strip(),
            "domain": domain,
            "internship_start": form.cleaned_data.get("from_month"),
            "internship_end": form.cleaned_data.get("to_month"),
            "project_title": form.cleaned_data.get("project_title", ""),
            "feedback": form.cleaned_data.get("feedback", ""),
            "custom_data": {
                key: form.cleaned_data.get(key, "")
                for key, setting in form.field_settings.items()
                if setting.is_custom and key in form.fields
            },
        }
        with transaction.atomic():
            submission, created = StudentSubmission.objects.get_or_create(
                submission_token=token,
                defaults=defaults,
            )
            if created:
                categories = (
                    [CertificateRequest.Category.OFFER]
                    if source == StudentSubmission.Source.OFFER
                    else [
                        CertificateRequest.Category.EXPERIENCE,
                        CertificateRequest.Category.PROJECT,
                        CertificateRequest.Category.LOR,
                    ]
                )
                CertificateRequest.objects.bulk_create(
                    [CertificateRequest(submission=submission, category=category) for category in categories]
                )
                _audit(
                    request,
                    "student_submission_created",
                    submission=submission,
                    details={"source": source},
                )
        return render(request, "portal/success.html", {"submission": submission})

    return render(
        request,
        "portal/register.html",
        {
            "form": form,
            "form_type": form_type,
            "form_title": (
                "Offer Letter Registration"
                if form_type == FormFieldSetting.FormType.OFFER
                else "Internship Certificate Registration"
            ),
            **_registration_catalog_context(form),
        },
    )


def admin_login(request):
    if _is_portal_admin(request.user):
        profile = AdminProfile.objects.get(user=request.user)
        return redirect(
            "admin_password_change" if profile.force_password_change else "admin_dashboard"
        )

    form = AdminLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        address = request.META.get("REMOTE_ADDR", "127.0.0.1")
        try:
            throttle, _ = AdminLoginThrottle.objects.get_or_create(address=address)
        except ValidationError:
            address = "127.0.0.1"
            throttle, _ = AdminLoginThrottle.objects.get_or_create(address=address)

        now = timezone.now()
        if throttle.locked_until and throttle.locked_until > now:
            form.add_error(None, "Too many unsuccessful attempts. Try again after the lockout period.")
        else:
            if now - throttle.window_started_at > timedelta(minutes=15):
                throttle.failures = 0
                throttle.window_started_at = now
                throttle.locked_until = None

            username = form.cleaned_data["username"].lower()
            user = authenticate(
                request,
                username=username,
                password=form.cleaned_data["password"],
            )
            if (
                user
                and username == settings.ADMIN_USERNAME
                and user.is_staff
                and AdminProfile.objects.filter(user=user).exists()
            ):
                throttle.failures = 0
                throttle.locked_until = None
                throttle.save(update_fields=["failures", "locked_until", "window_started_at"])
                login(request, user)
                profile = AdminProfile.objects.get(user=user)
                return redirect(
                    "admin_password_change" if profile.force_password_change else "admin_dashboard"
                )

            throttle.failures += 1
            if throttle.failures >= 5:
                throttle.locked_until = now + timedelta(minutes=15)
            throttle.save(update_fields=["failures", "locked_until", "window_started_at"])
            form.add_error(None, "The email or password is incorrect.")

    return render(request, "portal/login.html", {"form": form})


@portal_admin_required
def admin_password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        profile = AdminProfile.objects.get(user=user)
        profile.force_password_change = False
        profile.save(update_fields=["force_password_change"])
        _audit(request, "admin_password_changed")
        messages.success(request, "Your password was changed.")
        return redirect("admin_dashboard")
    return render(request, "portal/password_change.html", {"form": form})


@require_POST
@portal_admin_required
def admin_logout(request):
    logout(request)
    return redirect("admin_login")


@portal_admin_required
def admin_dashboard(request):
    certificate_counts = []
    for category, label in CertificateRequest.Category.choices:
        certificate_counts.append(
            {
                "category": category,
                "label": label,
                "count": CertificateRequest.objects.filter(category=category).count(),
                "generated": CertificateRequest.objects.filter(
                    category=category, status=CertificateRequest.Status.GENERATED
                ).count(),
            }
        )
    context = {
        "total_submissions": StudentSubmission.objects.count(),
        "offer_requests": CertificateRequest.objects.filter(
            category=CertificateRequest.Category.OFFER
        ).count(),
        "pending": CertificateRequest.objects.filter(status=CertificateRequest.Status.PENDING).count(),
        "approved": CertificateRequest.objects.filter(status=CertificateRequest.Status.APPROVED).count(),
        "generated": CertificateRequest.objects.filter(status=CertificateRequest.Status.GENERATED).count(),
        "certificate_counts": certificate_counts,
        "recent_submissions": StudentSubmission.objects.select_related("domain")[:8],
    }
    return render(request, "portal/dashboard.html", context)


@portal_admin_required
def admin_qr_management(request):
    qr_items = [
        {
            "title": "Offer Letter",
            "url": request.build_absolute_uri(reverse("register_offer")),
            "image": reverse("admin_qr_image", kwargs={"form_type": "offer"}),
        },
        {
            "title": "Experience, Project Completion, and LOR",
            "url": request.build_absolute_uri(reverse("register_internship")),
            "image": reverse("admin_qr_image", kwargs={"form_type": "internship"}),
        },
    ]
    return render(request, "portal/qr_management.html", {"qr_items": qr_items})


@require_GET
@portal_admin_required
def admin_qr_image(request, form_type):
    routes = {
        "offer": "register_offer",
        "internship": "register_internship",
    }
    route = routes.get(form_type)
    if not route:
        return HttpResponseNotFound("QR code not found.")
    target = request.build_absolute_uri(reverse(route))
    image = qrcode.make(target)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return HttpResponse(output.getvalue(), content_type="image/png")


@portal_admin_required
def admin_submission_list(request, category=None):
    submissions = StudentSubmission.objects.select_related("domain").prefetch_related(
        "certificate_requests"
    )
    if category:
        submissions = submissions.filter(certificate_requests__category=category).distinct()
    query = request.GET.get("q", "").strip()
    if query:
        submissions = submissions.filter(
            Q(name__icontains=query) | Q(registration_number__icontains=query)
        )
    domain_id = request.GET.get("domain", "")
    if domain_id.isdigit():
        submissions = submissions.filter(domain_id=int(domain_id))
    request_status = request.GET.get("status", "")
    if request_status in CertificateRequest.Status.values:
        submissions = submissions.filter(
            certificate_requests__status=request_status
        ).distinct()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if date_from and not parsed_from:
        messages.error(request, "Enter a valid start date filter.")
    if date_to and not parsed_to:
        messages.error(request, "Enter a valid end date filter.")
    if parsed_from:
        submissions = submissions.filter(created_at__date__gte=parsed_from)
    if parsed_to:
        submissions = submissions.filter(created_at__date__lte=parsed_to)
    sort_options = {
        "name": "name",
        "-name": "-name",
        "created_at": "created_at",
        "-created_at": "-created_at",
        "registration_number": "registration_number",
        "-registration_number": "-registration_number",
    }
    sort = request.GET.get("sort", "-created_at")
    submissions = submissions.order_by(sort_options.get(sort, "-created_at"))
    page_obj = Paginator(submissions, 20).get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj,
        "domains": Domain.objects.all(),
        "statuses": CertificateRequest.Status.choices,
        "category": category,
        "category_label": dict(CertificateRequest.Category.choices).get(category),
        "filters": {
            "q": query,
            "domain": domain_id,
            "status": request_status,
            "date_from": date_from,
            "date_to": date_to,
            "sort": sort,
        },
    }
    return render(request, "portal/submission_list.html", context)


@portal_admin_required
def admin_submission_detail(request, submission_id):
    submission = get_object_or_404(
        StudentSubmission.objects.select_related("domain").prefetch_related(
            "certificate_requests__generated_files"
        ),
        submission_id=submission_id,
    )
    form = StudentSubmissionEditForm(request.POST or None, instance=submission)
    if request.method == "POST" and form.is_valid():
        before = {
            field: getattr(submission, field)
            for field in (
                "title",
                "name",
                "gender",
                "mobile",
                "registration_number",
                "registered_email",
                "alternative_email",
                "degree_department",
                "college_name",
                "college_location",
                "domain_id",
                "internship_start",
                "internship_end",
                "project_title",
                "feedback",
            )
        }
        form.save()
        changed = [
            field for field, value in before.items() if getattr(submission, field) != value
        ]
        if changed:
            _audit(request, "submission_edited", submission=submission, details={"fields": changed})
            messages.success(request, "Student details were updated.")
        return redirect("admin_submission_detail", submission_id=submission.submission_id)
    return render(
        request,
        "portal/submission_detail.html",
        {
            "submission": submission,
            "form": form,
            "decision_forms": {
                item.pk: CertificateDecisionForm()
                for item in submission.certificate_requests.all()
            },
        },
    )


@require_POST
@portal_admin_required
def admin_certificate_decision(request, request_id):
    certificate_request = get_object_or_404(
        CertificateRequest.objects.select_related("submission"),
        pk=request_id,
    )
    form = CertificateDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect(
            "admin_submission_detail",
            submission_id=certificate_request.submission.submission_id,
        )
    if certificate_request.status != CertificateRequest.Status.PENDING:
        messages.error(request, "Only pending requests can be approved or rejected.")
        return redirect(
            "admin_submission_detail",
            submission_id=certificate_request.submission.submission_id,
        )

    decision = form.cleaned_data["decision"]
    certificate_request.status = decision
    certificate_request.rejection_reason = (
        form.cleaned_data["rejection_reason"].strip()
        if decision == CertificateRequest.Status.REJECTED
        else ""
    )
    certificate_request.reviewed_by = request.user
    certificate_request.reviewed_at = timezone.now()
    certificate_request.save(
        update_fields=["status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"]
    )
    _audit(
        request,
        f"certificate_{decision}",
        submission=certificate_request.submission,
        certificate_request=certificate_request,
        details={"reason": certificate_request.rejection_reason},
    )
    messages.success(request, f"Certificate request {decision}.")
    return redirect(
        "admin_submission_detail",
        submission_id=certificate_request.submission.submission_id,
    )


@portal_admin_required
def admin_submission_delete(request, submission_id):
    submission = get_object_or_404(StudentSubmission, submission_id=submission_id)
    generated_exists = GeneratedCertificate.objects.filter(
        request__submission=submission
    ).exists()
    if request.method == "POST":
        if generated_exists:
            messages.error(
                request,
                "This submission has generated certificate backups and cannot be deleted.",
            )
            return redirect("admin_submission_detail", submission_id=submission.submission_id)
        _audit(
            request,
            "submission_deleted",
            details={"submission_id": str(submission.submission_id)},
        )
        submission.delete()
        messages.success(request, "The submission was deleted.")
        return redirect("admin_submission_list")
    return render(
        request,
        "portal/confirm_delete.html",
        {"submission": submission, "generated_exists": generated_exists},
    )


@require_POST
@portal_admin_required
def admin_generate_certificate(request, request_id):
    requested = get_object_or_404(
        CertificateRequest.objects.select_related("submission", "submission__domain"),
        pk=request_id,
    )
    mode = request.POST.get("mode", "")
    file_format = request.POST.get("format", "")
    template = CertificateTemplate.objects.filter(
        category=requested.category, active=True
    ).order_by("-version").first()
    if not template:
        messages.error(request, "No active template is available for this certificate category.")
        return redirect("admin_submission_detail", submission_id=requested.submission.submission_id)

    try:
        content = generate_certificate_bytes(requested, template, mode, file_format)
    except DocumentGenerationError as error:
        messages.error(request, str(error))
        return redirect("admin_submission_detail", submission_id=requested.submission.submission_id)

    digest = certificate_content_hash(content)
    name = certificate_filename(requested, mode, file_format)
    with transaction.atomic():
        locked_request = CertificateRequest.objects.select_for_update().get(pk=requested.pk)
        certificate = GeneratedCertificate.objects.filter(
            request=locked_request,
            mode=mode,
            file_format=file_format,
            content_hash=digest,
        ).first()
        if certificate is None:
            certificate = GeneratedCertificate(
                request=locked_request,
                mode=mode,
                file_format=file_format,
                file_name=name,
                content_hash=digest,
            )
            certificate.file.save(name, ContentFile(content), save=False)
            certificate.save()
            if locked_request.status != CertificateRequest.Status.GENERATED:
                locked_request.status = CertificateRequest.Status.GENERATED
                locked_request.save(update_fields=["status", "updated_at"])
            _audit(
                request,
                "certificate_generated",
                submission=locked_request.submission,
                certificate_request=locked_request,
                details={"mode": mode, "format": file_format, "file_name": name},
            )
        else:
            messages.info(request, "This exact certificate version is already backed up.")
    return redirect("admin_certificate_download", certificate_id=certificate.pk)


@portal_admin_required
def admin_certificate_download(request, certificate_id):
    certificate = get_object_or_404(
        GeneratedCertificate.objects.select_related("request__submission"),
        pk=certificate_id,
    )
    download_name = certificate_filename(
        certificate.request,
        certificate.mode,
        certificate.file_format,
    )
    CertificateDownload.objects.create(certificate=certificate, downloaded_by=request.user)
    certificate.downloaded_at = timezone.now()
    certificate.save(update_fields=["downloaded_at"])
    _audit(
        request,
        "certificate_downloaded",
        submission=certificate.request.submission,
        certificate_request=certificate.request,
        details={"file_name": download_name},
    )
    return FileResponse(
        certificate.file.open("rb"),
        as_attachment=True,
        filename=download_name,
    )


@portal_admin_required
def admin_backups(request, category=None):
    certificates = GeneratedCertificate.objects.select_related(
        "request__submission",
        "request__submission__domain",
    )
    if category:
        certificates = certificates.filter(request__category=category)
    query = request.GET.get("q", "").strip()
    if query:
        certificates = certificates.filter(
            Q(request__submission__name__icontains=query)
            | Q(request__submission__registration_number__icontains=query)
        )
    mode = request.GET.get("mode", "")
    if mode in GeneratedCertificate.Mode.values:
        certificates = certificates.filter(mode=mode)
    file_format = request.GET.get("format", "")
    if file_format in GeneratedCertificate.Format.values:
        certificates = certificates.filter(file_format=file_format)
    page_obj = Paginator(certificates, 20).get_page(request.GET.get("page"))
    for certificate in page_obj:
        certificate.display_filename = certificate_filename(
            certificate.request,
            certificate.mode,
            certificate.file_format,
        )
    return render(
        request,
        "portal/backups.html",
        {
            "page_obj": page_obj,
            "category": category,
            "category_label": dict(CertificateRequest.Category.choices).get(category),
            "filters": {"q": query, "mode": mode, "format": file_format},
        },
    )


@portal_admin_required
def admin_domains(request):
    form = DomainForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        domain = form.save()
        _audit(request, "domain_created", details={"domain_id": domain.pk})
        messages.success(request, "Domain added.")
        return redirect("admin_domains")
    return render(
        request,
        "portal/domains.html",
        {"form": form, "domains": Domain.objects.all()},
    )


@portal_admin_required
def admin_catalog(request):
    form_classes = {
        "degree": DegreeOptionForm,
        "specialization": SpecializationOptionForm,
        "district": CollegeDistrictForm,
        "category": CollegeCategoryForm,
        "college": CollegeOptionForm,
    }
    forms_by_kind = {kind: form_class() for kind, form_class in form_classes.items()}
    if request.method == "POST":
        kind = request.POST.get("kind", "")
        form_class = form_classes.get(kind)
        if form_class is None:
            return HttpResponseNotFound("Catalog item type not found.")
        form = form_class(request.POST)
        if form.is_valid():
            item = form.save()
            _audit(request, "academic_catalog_item_created", details={"kind": kind, "item_id": item.pk})
            messages.success(request, f"{kind.capitalize()} added to the catalog.")
            return redirect("admin_catalog")
        forms_by_kind[kind] = form

    return render(
        request,
        "portal/catalog.html",
        {
            "forms_by_kind": forms_by_kind,
            "degrees": DegreeOption.objects.all(),
            "specializations": SpecializationOption.objects.select_related("degree"),
            "districts": CollegeDistrict.objects.all(),
            "categories": CollegeCategory.objects.all(),
            "colleges": CollegeOption.objects.select_related("district", "category"),
        },
    )


@portal_admin_required
def admin_catalog_edit(request, kind, item_id):
    catalog = {
        "degree": (DegreeOption, DegreeOptionForm),
        "specialization": (SpecializationOption, SpecializationOptionForm),
        "district": (CollegeDistrict, CollegeDistrictForm),
        "category": (CollegeCategory, CollegeCategoryForm),
        "college": (CollegeOption, CollegeOptionForm),
    }
    entry = catalog.get(kind)
    if entry is None:
        return HttpResponseNotFound("Catalog item type not found.")
    model, form_class = entry
    item = get_object_or_404(model, pk=item_id)
    form = form_class(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        _audit(request, "academic_catalog_item_updated", details={"kind": kind, "item_id": item.pk})
        messages.success(request, f"{kind.capitalize()} updated.")
        return redirect("admin_catalog")
    return render(
        request,
        "portal/catalog_edit.html",
        {"form": form, "kind": kind.capitalize(), "item": item},
    )


@portal_admin_required
def admin_domain_edit(request, domain_id):
    domain = get_object_or_404(Domain, pk=domain_id)
    form = DomainForm(request.POST or None, instance=domain)
    if request.method == "POST" and form.is_valid():
        form.save()
        _audit(request, "domain_updated", details={"domain_id": domain.pk})
        messages.success(request, "Domain updated.")
        return redirect("admin_domains")
    return render(
        request,
        "portal/domain_edit.html",
        {"form": form, "domain": domain},
    )


@require_POST
@portal_admin_required
def admin_domain_delete(request, domain_id):
    domain = get_object_or_404(Domain, pk=domain_id)
    if domain.submissions.exists():
        messages.error(request, "This domain is used by submissions; deactivate it instead.")
    else:
        _audit(request, "domain_deleted", details={"domain_id": domain.pk, "name": domain.name})
        domain.delete()
        messages.success(request, "Domain deleted.")
    return redirect("admin_domains")


@portal_admin_required
def admin_form_settings(request):
    form_fields = FormFieldSetting.objects.filter(is_custom=False)
    custom_fields = FormFieldSetting.objects.filter(is_custom=True)
    return render(
        request,
        "portal/form_settings.html",
        {"form_fields": form_fields, "custom_fields": custom_fields},
    )


@portal_admin_required
def admin_form_field_edit(request, field_id):
    setting = get_object_or_404(FormFieldSetting, pk=field_id)
    form = FormFieldSettingForm(request.POST or None, instance=setting)
    if request.method == "POST" and form.is_valid():
        form.save()
        _audit(request, "form_field_updated", details={"field": setting.key})
        messages.success(request, "Form field settings saved.")
        return redirect("admin_form_settings")
    return render(
        request,
        "portal/form_field_edit.html",
        {"form": form, "setting": setting},
    )


@portal_admin_required
def admin_custom_field_add(request):
    form = CustomFieldForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        setting = form.save()
        _audit(request, "custom_form_field_added", details={"field": setting.key})
        messages.success(request, "Custom form field added.")
        return redirect("admin_form_settings")
    return render(request, "portal/custom_field_form.html", {"form": form})


@require_POST
@portal_admin_required
def admin_custom_field_delete(request, field_id):
    setting = get_object_or_404(FormFieldSetting, pk=field_id, is_custom=True)
    _audit(request, "custom_form_field_removed", details={"field": setting.key})
    setting.delete()
    messages.success(
        request,
        "Custom field removed. Existing submitted values remain in student records.",
    )
    return redirect("admin_form_settings")


@portal_admin_required
def admin_settings(request):
    offer_program, _ = OfferProgramSettings.objects.get_or_create(singleton=True)
    form = OfferProgramSettingsForm(request.POST or None, instance=offer_program)
    if request.method == "POST" and form.is_valid():
        form.save()
        _audit(request, "offer_program_settings_updated")
        messages.success(request, "Offer Letter program period saved.")
        return redirect("admin_settings")
    return render(request, "portal/admin_settings.html", {"form": form})


def _validate_docx_upload(upload):
    try:
        with ZipFile(upload) as archive:
            names = set(archive.namelist())
            total_size = sum(info.file_size for info in archive.infolist())
            if "word/document.xml" not in names or total_size > 50 * 1024 * 1024:
                raise ValidationError("The uploaded file is not a valid DOCX document.")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise ValidationError("Macro-enabled Word documents are not allowed.")
    except BadZipFile as error:
        raise ValidationError("The uploaded file is not a valid DOCX document.") from error
    finally:
        upload.seek(0)


@portal_admin_required
def admin_templates(request):
    form = CertificateTemplateUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        document = form.cleaned_data["document"]
        try:
            _validate_docx_upload(document)
        except ValidationError as error:
            form.add_error("document", error)
            return render(
                request,
                "portal/templates.html",
                {"form": form, "templates": CertificateTemplate.objects.all()},
            )
        pdf_source = form.cleaned_data.get("pdf_source")
        if pdf_source:
            if pdf_source.read(5) != b"%PDF-":
                form.add_error("pdf_source", "The uploaded file is not a valid PDF.")
                pdf_source.seek(0)
                return render(
                    request,
                    "portal/templates.html",
                    {"form": form, "templates": CertificateTemplate.objects.all()},
                )
            pdf_source.seek(0)
        category = form.cleaned_data["category"]
        version = (
            CertificateTemplate.objects.filter(category=category)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
            or 0
        ) + 1
        with transaction.atomic():
            CertificateTemplate.objects.filter(category=category, active=True).update(active=False)
            template = form.save(commit=False)
            template.version = version
            template.original_filename = document.name
            template.active = True
            template.created_by = request.user
            template.save()
            _audit(
                request,
                "template_uploaded",
                details={"category": category, "version": version},
            )
        messages.success(request, f"Template v{version} uploaded and activated.")
        return redirect("admin_templates")
    return render(
        request,
        "portal/templates.html",
        {"form": form, "templates": CertificateTemplate.objects.all()},
    )


@portal_admin_required
def admin_template_preview(request, template_id):
    template = get_object_or_404(CertificateTemplate, pk=template_id)
    if not template.pdf_source:
        messages.error(request, "This template version has no PDF preview.")
        return redirect("admin_templates")
    return FileResponse(
        template.pdf_source.open("rb"),
        content_type="application/pdf",
        filename=template.pdf_source.name.rsplit("/", 1)[-1],
    )


@portal_admin_required
def admin_audit_log(request):
    events = AuditLog.objects.select_related("actor", "submission", "certificate_request")[:100]
    return render(request, "portal/audit_log.html", {"events": events})

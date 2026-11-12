import hashlib
import tempfile
import uuid
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pymupdf as fitz
from docx import Document
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import StudentSubmissionForm
from .models import (
    AdminLoginThrottle,
    AdminProfile,
    AuditLog,
    CollegeCategory,
    CollegeDistrict,
    CollegeOption,
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
    SOURCE_FILES,
    _field_values,
    _paragraphs,
    _replace_in_paragraph,
    certificate_filename,
    generate_certificate_bytes,
    _docx_bytes_for_pdf,
)
from docx.enum.text import WD_ALIGN_PARAGRAPH


class PortalTestCase(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.addCleanup(self.media_directory.cleanup)
        call_command("seed_defaults", verbosity=0)

    def create_admin(self, force_password_change=False):
        user = User.objects.create_user(
            username=settings.ADMIN_USERNAME,
            password="OriginalPassword!872",
            is_staff=True,
        )
        AdminProfile.objects.create(user=user, force_password_change=force_password_change)
        return user

    def create_submission(self, source=StudentSubmission.Source.INTERNSHIP, gender="female"):
        domain, _ = Domain.objects.get_or_create(name="Data Science")
        return StudentSubmission.objects.create(
            source=source,
            title="Ms." if gender == "female" else "Mr.",
            name="Asha Rao",
            gender=gender,
            mobile="9876543210",
            registration_number="GT2026/001",
            registered_email="asha.rao@gmail.com",
            alternative_email="asha.alt@gmail.com",
            degree_department="B.E (Computer Science and Engineering)",
            college_name="National Engineering College",
            college_location="Coimbatore",
            domain=domain,
            internship_start=date(2026, 5, 1),
            internship_end=date(2026, 7, 1),
            project_title="Campus Analytics Dashboard",
            feedback="The internship was useful and informative.",
        )

    def create_source_template(self, category):
        filename = SOURCE_FILES[category]
        source_dir = settings.TEMPLATE_SOURCE_DIR
        pdf_name = filename.removesuffix(".docx") + ".pdf"
        template = CertificateTemplate(
            category=category,
            version=1,
            original_filename=filename,
            active=True,
        )
        template.document.save(filename, ContentFile((source_dir / filename).read_bytes()), save=False)
        template.pdf_source.save(
            pdf_name,
            ContentFile((source_dir / pdf_name).read_bytes()),
            save=False,
        )
        template.save()
        return template

    def create_request(self, category, gender="female"):
        submission = self.create_submission(
            source=(
                StudentSubmission.Source.OFFER
                if category == CertificateRequest.Category.OFFER
                else StudentSubmission.Source.INTERNSHIP
            ),
            gender=gender,
        )
        return CertificateRequest.objects.create(
            submission=submission,
            category=category,
            status=CertificateRequest.Status.APPROVED,
        )


class RegistrationTests(PortalTestCase):
    def registration_data(self, form_type, token=None):
        data = {
            "title": "Ms.",
            "name": "Asha Rao",
            "gender": "female",
            "mobile": "9876543210",
            "registration_number": "GT2026/001",
            "registered_email": "asha.rao@gmail.com",
            "alternative_email": "asha.alt@gmail.com",
            "degree_department": "B.E (Computer Science and Engineering)",
            "college": "National Engineering College, Coimbatore",
            "domain": Domain.objects.get(name="Data Science").pk,
            "new_domain": "",
            "submission_token": str(token or uuid.uuid4()),
        }
        if form_type == FormFieldSetting.FormType.INTERNSHIP:
            data.update(
                {
                    "from_month": "2026-05",
                    "to_month": "2026-07",
                    "project_title": "Campus Analytics Dashboard",
                    "feedback": "The internship was useful and informative.",
                }
            )
        return data

    def test_public_pages_render_both_registration_forms(self):
        self.assertEqual(self.client.get(reverse("landing")).status_code, 200)
        for route in ("register_offer", "register_internship"):
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200)

    def test_offer_registration_creates_one_request_and_is_idempotent(self):
        token = uuid.uuid4()
        route = reverse("register_offer")
        data = self.registration_data(FormFieldSetting.FormType.OFFER, token)

        first = self.client.post(route, data)
        second = self.client.post(route, {**data, "name": "Maya Rao"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(StudentSubmission.objects.count(), 1)
        submission = StudentSubmission.objects.get()
        self.assertEqual(submission.name, "Asha Rao")
        self.assertEqual(submission.certificate_requests.count(), 1)
        self.assertEqual(submission.certificate_requests.get().category, "offer")
        self.assertTrue(AuditLog.objects.filter(action="student_submission_created").exists())

    def test_internship_registration_creates_three_certificate_requests(self):
        response = self.client.post(
            reverse("register_internship"),
            self.registration_data(FormFieldSetting.FormType.INTERNSHIP),
        )

        self.assertEqual(response.status_code, 200)
        submission = StudentSubmission.objects.get()
        self.assertEqual(submission.internship_start, date(2026, 5, 1))
        self.assertEqual(
            set(submission.certificate_requests.values_list("category", flat=True)),
            {"experience", "project", "lor"},
        )

    def test_degree_full_names_and_common_variations_map_to_standard_abbreviations(self):
        examples = [
            ("Master of Computer Applications", "MCA"),
            ("Master of Computer Application", "MCA"),
            ("Master of Business Administration", "MBA"),
            ("Master in Business Administration", "MBA"),
            ("Bachelor of Engineering", "B.E."),
            ("Bachelor of Technology", "B.Tech"),
        ]
        for entered, expected in examples:
            with self.subTest(entered=entered):
                data = self.registration_data(FormFieldSetting.FormType.OFFER)
                data["degree_department"] = entered
                form = StudentSubmissionForm(data, form_type=FormFieldSetting.FormType.OFFER)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["degree_department"], expected)

    def test_degree_catalog_selection_formats_specialization_without_assuming_general(self):
        cases = [
            ("MCA", "Computer Application", "MCA (Computer Application)"),
            ("MCA", "General", "MCA (General)"),
            ("MBA", "Human Resource Management", "MBA (Human Resource Management)"),
            ("B.E.", "Computer Science and Engineering", "B.E. (Computer Science and Engineering)"),
            ("MBBS", "", "MBBS"),
        ]
        for abbreviation, specialization_name, expected in cases:
            with self.subTest(degree=abbreviation, specialization=specialization_name):
                degree = DegreeOption.objects.get(abbreviation=abbreviation)
                specialization = (
                    SpecializationOption.objects.get(
                        degree=degree,
                        name=specialization_name,
                    )
                    if specialization_name
                    else None
                )
                data = self.registration_data(FormFieldSetting.FormType.OFFER)
                data.update(
                    {
                        "degree_department": "",
                        "degree_option": str(degree.pk),
                        "specialization_option": str(specialization.pk) if specialization else "",
                    }
                )
                form = StudentSubmissionForm(data, form_type=FormFieldSetting.FormType.OFFER)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["degree_department"], expected)

    def test_search_catalog_data_is_available_on_both_registration_forms(self):
        for route in ("register_offer", "register_internship"):
            response = self.client.get(reverse(route))
            self.assertContains(response, "Master of Computer Applications")
            self.assertContains(response, "Other state / college not listed")
            self.assertContains(response, "Dr. Mahalingam College of Engineering and Technology")
            self.assertContains(response, "college_state_filter")

    def test_college_directory_import_is_idempotent_and_preserves_submissions(self):
        submission = self.create_submission()
        original_submission = (
            submission.college_name,
            submission.college_location,
            submission.registration_number,
        )

        call_command("import_college_directory", verbosity=0)
        first_count = CollegeOption.objects.count()
        call_command("import_college_directory", verbosity=0)

        self.assertGreaterEqual(first_count, 900)
        self.assertEqual(CollegeOption.objects.count(), first_count)
        imported_districts = set(
            CollegeOption.objects.filter(
                source_url="https://www.ugc.gov.in/colleges"
            ).values_list("district__name", flat=True)
        )
        self.assertEqual(len(imported_districts), 38)
        submission.refresh_from_db()
        self.assertEqual(
            (
                submission.college_name,
                submission.college_location,
                submission.registration_number,
            ),
            original_submission,
        )

        for route in ("register_offer", "register_internship"):
            response = self.client.get(reverse(route))
            colleges = response.context["college_catalog"]
            self.assertEqual(len(colleges), first_count)
            self.assertTrue(
                any(
                    college["name"] == "A.J.K. College of Arts & Science"
                    and college["district"] == "Coimbatore"
                    for college in colleges
                )
            )
            self.assertTrue(
                all({"address", "aliases", "state"} <= college.keys() for college in colleges)
            )

        form = StudentSubmissionForm(form_type=FormFieldSetting.FormType.OFFER)
        self.assertEqual(form.fields["college_state_filter"].choices[1][1], "Tamil Nadu")
        self.assertEqual(len(form.fields["college_district_filter"].choices), 39)

    def test_selected_college_is_authoritative_and_feeds_certificate_values(self):
        college = CollegeOption.objects.get(
            name="Dr. Mahalingam College of Engineering and Technology"
        )
        OfferProgramSettings.objects.update_or_create(
            singleton=True,
            defaults={
                "start_month": date(2026, 11, 1),
                "end_month": date(2027, 1, 1),
            },
        )
        data = self.registration_data(FormFieldSetting.FormType.OFFER)
        data.update(
            {
                "college": "Manually Typed Incorrect College, Incorrect City",
                "college_option": str(college.pk),
            }
        )

        response = self.client.post(reverse("register_offer"), data)

        self.assertEqual(response.status_code, 200)
        submission = StudentSubmission.objects.get()
        self.assertEqual(submission.college_name, college.name)
        self.assertEqual(submission.college_location, college.location)
        request = submission.certificate_requests.get(category=CertificateRequest.Category.OFFER)
        certificate_values = _field_values(request, date(2026, 11, 4))
        self.assertEqual(certificate_values["COLLEGE_NAME"], college.name)
        self.assertEqual(certificate_values["COLLEGE_LOCATION"], college.location)

    def test_admin_added_college_is_searchable_from_both_student_forms(self):
        district = CollegeDistrict.objects.get(name="Coimbatore")
        category = CollegeCategory.objects.get(name="Arts and Science")
        self.client.force_login(self.create_admin())

        response = self.client.post(
            reverse("admin_catalog"),
            {
                "kind": "college",
                "name": "Admin Added Search College",
                "location": "Testville",
                "address": "12 College Road, Testville",
                "state": "Tamil Nadu",
                "district": district.pk,
                "category": category.pk,
                "affiliation": "Test University",
                "aliases": "Common College Name; Test Search Name",
                "source_url": "https://example.edu/",
                "active": "on",
            },
        )

        self.assertRedirects(response, reverse("admin_catalog"))
        college = CollegeOption.objects.get(name="Admin Added Search College")
        self.assertEqual(college.aliases, ["Common College Name", "Test Search Name"])
        for route in ("register_offer", "register_internship"):
            response = self.client.get(reverse(route))
            entry = next(
                item
                for item in response.context["college_catalog"]
                if item["id"] == str(college.pk)
            )
            self.assertEqual(entry["aliases"], college.aliases)
            self.assertEqual(entry["address"], college.address)

    def test_other_state_college_is_validated_and_keeps_state_in_saved_location(self):
        data = self.registration_data(FormFieldSetting.FormType.OFFER)
        data.update(
            {
                "college": "",
                "college_option": "__other_state__",
                "college_state": "Andhra Pradesh",
                "college_other_name": "Sri Venkateswara University",
                "college_other_location": "Tirupati",
            }
        )
        response = self.client.post(reverse("register_offer"), data)

        self.assertEqual(response.status_code, 200)
        submission = StudentSubmission.objects.get()
        self.assertEqual(submission.college_name, "Sri Venkateswara University")
        self.assertEqual(submission.college_location, "Tirupati, Andhra Pradesh")

    def test_other_tamil_nadu_college_can_be_entered_manually(self):
        data = self.registration_data(FormFieldSetting.FormType.OFFER)
        data.update(
            {
                "college": "",
                "college_option": "__other__",
                "college_state": "Tamil Nadu",
                "college_other_name": "Unlisted Technical Institute",
                "college_other_location": "Thanjavur",
            }
        )

        response = self.client.post(reverse("register_offer"), data)

        self.assertEqual(response.status_code, 200)
        submission = StudentSubmission.objects.get()
        self.assertEqual(submission.college_name, "Unlisted Technical Institute")
        self.assertEqual(submission.college_location, "Thanjavur")

    def test_invalid_registration_is_rejected_server_side(self):
        data = self.registration_data(FormFieldSetting.FormType.OFFER)
        data["mobile"] = "12345"

        response = self.client.post(reverse("register_offer"), data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(StudentSubmission.objects.count(), 0)
        self.assertContains(response, "Enter exactly 10 digits")

    def test_degree_aliases_and_case_variations_are_standardized(self):
        examples = [
            ("mca (computer application)", "MCA (Computer Application)"),
            (
                "b.e (computer science and engineering)",
                "B.E. (Computer Science and Engineering)",
            ),
            ("b.sc., information technology", "B.Sc., Information Technology"),
            ("mba (general)", "MBA (General)"),
            (
                "m.tech (artificial intelligence and data science)",
                "M.Tech (Artificial Intelligence and Data Science)",
            ),
            ("M.C.A.", "MCA"),
            ("M.B.A. (General)", "MBA (General)"),
        ]
        for entered, suggestion in examples:
            with self.subTest(entered=entered):
                data = self.registration_data(FormFieldSetting.FormType.OFFER)
                data["degree_department"] = entered
                form = StudentSubmissionForm(data, form_type=FormFieldSetting.FormType.OFFER)

                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["degree_department"], suggestion)

    def test_degree_suggestion_is_shown_without_overwriting_input_and_can_be_revalidated(self):
        data = self.registration_data(FormFieldSetting.FormType.OFFER)
        data["degree_department"] = "B.Plan (urban planning)"

        response = self.client.post(reverse("register_offer"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "specialization must use standard capitalization")
        self.assertContains(response, "B.Plan (Urban Planning)")
        self.assertContains(response, "Apply suggestion")
        self.assertContains(response, 'value="B.Plan (urban planning)"')
        self.assertEqual(StudentSubmission.objects.count(), 0)

        data["degree_department"] = "B.Plan (Urban Planning)"
        form = StudentSubmissionForm(data, form_type=FormFieldSetting.FormType.OFFER)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["degree_department"],
            "B.Plan (Urban Planning)",
        )


class AdminWorkflowTests(PortalTestCase):
    def test_application_uses_gt_certify_branding(self):
        for route in ("landing", "register_offer", "register_internship", "admin_login"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, "GT Certify")
                self.assertContains(
                    response,
                    "Smart Certificate Generation &amp; Management System",
                )
                self.assertIn(b"GT Certify</title>", response.content)

    def test_admin_pages_require_authentication(self):
        response = self.client.get(reverse("admin_dashboard"))
        self.assertRedirects(response, f"{reverse('admin_login')}?next={reverse('admin_dashboard')}")

    def test_first_login_requires_password_change(self):
        self.create_admin(force_password_change=True)

        response = self.client.post(
            reverse("admin_login"),
            {"username": settings.ADMIN_USERNAME, "password": "OriginalPassword!872"},
        )

        self.assertRedirects(response, reverse("admin_password_change"))
        self.assertEqual(
            self.client.get(reverse("admin_dashboard")).url,
            reverse("admin_password_change"),
        )

    def test_password_change_clears_first_login_requirement(self):
        user = self.create_admin(force_password_change=True)
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin_password_change"),
            {
                "old_password": "OriginalPassword!872",
                "new_password1": "A substantially different password!928",
                "new_password2": "A substantially different password!928",
            },
        )

        self.assertRedirects(response, reverse("admin_dashboard"))
        user.refresh_from_db()
        profile = AdminProfile.objects.get(user=user)
        self.assertTrue(user.check_password("A substantially different password!928"))
        self.assertFalse(profile.force_password_change)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)

    def test_login_throttle_locks_after_five_failures(self):
        self.create_admin()

        for _ in range(5):
            self.client.post(
                reverse("admin_login"),
                {"username": settings.ADMIN_USERNAME, "password": "incorrect"},
            )

        throttle = AdminLoginThrottle.objects.get(address="127.0.0.1")
        self.assertEqual(throttle.failures, 5)
        self.assertIsNotNone(throttle.locked_until)
        response = self.client.post(
            reverse("admin_login"),
            {"username": settings.ADMIN_USERNAME, "password": "OriginalPassword!872"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_approval_is_required_before_generation_and_approved_request_is_backed_up(self):
        user = self.create_admin()
        self.client.force_login(user)
        submission = self.create_submission(source=StudentSubmission.Source.OFFER)
        certificate_request = CertificateRequest.objects.create(
            submission=submission,
            category=CertificateRequest.Category.OFFER,
        )
        template = CertificateTemplate(
            category=CertificateTemplate.Category.OFFER,
            version=1,
            original_filename="custom-offer.docx",
            active=True,
        )
        document = Document()
        document.add_paragraph("{{STUDENT_NAME}} - {{UNIVERSITY_REGISTRATION_NUMBER}}")
        output = BytesIO()
        document.save(output)
        template.document.save("custom-offer.docx", ContentFile(output.getvalue()), save=False)
        template.save()

        generate_url = reverse("admin_generate_certificate", args=[certificate_request.pk])
        rejected = self.client.post(generate_url, {"mode": "online", "format": "docx"})
        self.assertRedirects(
            rejected,
            reverse("admin_submission_detail", args=[submission.submission_id]),
        )
        self.assertEqual(GeneratedCertificate.objects.count(), 0)

        decision = self.client.post(
            reverse("admin_certificate_decision", args=[certificate_request.pk]),
            {"decision": CertificateRequest.Status.APPROVED, "rejection_reason": ""},
        )
        self.assertEqual(decision.status_code, 302)
        certificate_request.refresh_from_db()
        self.assertEqual(certificate_request.status, CertificateRequest.Status.APPROVED)
        self.assertEqual(certificate_request.reviewed_by, user)
        OfferProgramSettings.objects.update_or_create(
            singleton=True,
            defaults={"start_month": date(2027, 1, 1), "end_month": date(2027, 3, 1)},
        )
        response = self.client.post(generate_url, {"mode": "online", "format": "docx"})

        certificate = GeneratedCertificate.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("admin_certificate_download", args=[certificate.pk]),
        )
        self.assertEqual(certificate_request.__class__.objects.get(pk=certificate_request.pk).status, "generated")
        download = self.client.get(reverse("admin_certificate_download", args=[certificate.pk]))
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertEqual(certificate.downloads.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="certificate_generated").exists())
        self.assertTrue(AuditLog.objects.filter(action="certificate_downloaded").exists())

    def test_rejection_requires_a_reason_and_records_decision(self):
        user = self.create_admin()
        self.client.force_login(user)
        certificate_request = CertificateRequest.objects.create(
            submission=self.create_submission(),
            category=CertificateRequest.Category.EXPERIENCE,
        )
        url = reverse("admin_certificate_decision", args=[certificate_request.pk])

        invalid = self.client.post(
            url,
            {"decision": CertificateRequest.Status.REJECTED, "rejection_reason": ""},
        )
        self.assertEqual(invalid.status_code, 302)
        certificate_request.refresh_from_db()
        self.assertEqual(certificate_request.status, CertificateRequest.Status.PENDING)

        response = self.client.post(
            url,
            {
                "decision": CertificateRequest.Status.REJECTED,
                "rejection_reason": "Student details need correction.",
            },
        )
        self.assertEqual(response.status_code, 302)
        certificate_request.refresh_from_db()
        self.assertEqual(certificate_request.status, CertificateRequest.Status.REJECTED)
        self.assertEqual(certificate_request.rejection_reason, "Student details need correction.")
        self.assertTrue(AuditLog.objects.filter(action="certificate_rejected").exists())

    def test_qr_endpoints_return_png_for_both_registration_workflows(self):
        self.client.force_login(self.create_admin())

        for form_type in ("offer", "internship"):
            with self.subTest(form_type=form_type):
                response = self.client.get(
                    reverse("admin_qr_image", kwargs={"form_type": form_type})
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/png")
                self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_authenticated_admin_pages_render(self):
        self.client.force_login(self.create_admin())
        submission = self.create_submission(source=StudentSubmission.Source.OFFER)
        certificate_request = CertificateRequest.objects.create(
            submission=submission,
            category=CertificateRequest.Category.OFFER,
        )
        setting = FormFieldSetting.objects.get(
            form_type=FormFieldSetting.FormType.OFFER,
            key="name",
        )
        template = self.create_source_template(CertificateTemplate.Category.OFFER)
        paths = [
            reverse("admin_dashboard"),
            reverse("admin_qr_management"),
            reverse("admin_submission_list"),
            reverse("admin_offer_submissions"),
            reverse("admin_experience_submissions"),
            reverse("admin_project_submissions"),
            reverse("admin_lor_submissions"),
            reverse("admin_backups"),
            reverse("admin_offer_backups"),
            reverse("admin_experience_backups"),
            reverse("admin_project_backups"),
            reverse("admin_lor_backups"),
            reverse("admin_domains"),
            reverse("admin_domain_edit", args=[submission.domain_id]),
            reverse("admin_form_settings"),
            reverse("admin_catalog"),
            reverse("admin_form_field_edit", args=[setting.pk]),
            reverse("admin_custom_field_add"),
            reverse("admin_settings"),
            reverse("admin_templates"),
            reverse("admin_template_preview", args=[template.pk]),
            reverse("admin_audit_log"),
            reverse("admin_submission_detail", args=[submission.submission_id]),
            reverse("admin_submission_delete", args=[submission.submission_id]),
        ]

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                if response["Content-Type"].startswith("text/html"):
                    self.assertIn(b"GT Certify</title>", response.content)
                response.close()
        self.assertEqual(certificate_request.status, CertificateRequest.Status.PENDING)

    def test_admin_can_add_and_deactivate_degree_aliases(self):
        self.client.force_login(self.create_admin())
        response = self.client.post(
            reverse("admin_catalog"),
            {
                "kind": "degree",
                "abbreviation": "B.Plan",
                "full_name": "Bachelor of Planning",
                "aliases_text": "Bachelor in Planning\nB.Plan.",
                "active": "on",
            },
        )

        self.assertRedirects(response, reverse("admin_catalog"))
        degree = DegreeOption.objects.get(abbreviation="B.Plan")
        self.assertEqual(degree.aliases, ["Bachelor in Planning"])
        response = self.client.post(
            reverse("admin_catalog_edit", args=["degree", degree.pk]),
            {
                "abbreviation": "B.Plan",
                "full_name": "Bachelor of Planning",
                "aliases_text": "Bachelor in Planning",
            },
        )

        self.assertRedirects(response, reverse("admin_catalog"))
        degree.refresh_from_db()
        self.assertFalse(degree.active)

    def test_invalid_docx_upload_is_reported_as_a_form_error(self):
        self.client.force_login(self.create_admin())

        response = self.client.post(
            reverse("admin_templates"),
            {
                "category": CertificateTemplate.Category.OFFER,
                "document": SimpleUploadedFile("invalid.docx", b"not a zip archive"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not a valid DOCX document")
        self.assertEqual(CertificateTemplate.objects.count(), 0)


class DocumentReplacementTests(TestCase):
    def test_replacement_across_runs_keeps_anchor_and_surrounding_formatting(self):
        document = Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Before ")
        anchor_start = paragraph.add_run("Original")
        anchor_start.bold = True
        anchor_end = paragraph.add_run(" value")
        anchor_end.italic = True
        following = paragraph.add_run(" after")
        following.add_break()
        following.add_text("the break")

        count = _replace_in_paragraph(paragraph, "Original value", "Replacement")

        self.assertEqual(count, 1)
        self.assertEqual(paragraph.text, "Before Replacement after\nthe break")
        self.assertTrue(anchor_start.bold)
        self.assertEqual(anchor_start.text, "Replacement")
        self.assertTrue(anchor_end.italic)
        self.assertEqual(anchor_end.text, "")
        self.assertEqual(following.text, " after\nthe break")


class CertificateGenerationTests(PortalTestCase):
    def setUp(self):
        super().setUp()
        OfferProgramSettings.objects.update_or_create(
            singleton=True,
            defaults={"start_month": date(2027, 1, 1), "end_month": date(2027, 3, 1)},
        )

    def assert_source_docx_generated(self, category, gender="female"):
        request = self.create_request(category, gender=gender)
        template = self.create_source_template(category)
        source_path = Path(settings.TEMPLATE_SOURCE_DIR) / SOURCE_FILES[category]
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        source_document = Document(source_path)
        source_anchor = {
            CertificateRequest.Category.OFFER: "Ms. Vaishnavi N",
            CertificateRequest.Category.EXPERIENCE: "Mr. Kishore Kumar L",
            CertificateRequest.Category.PROJECT: "Ms. Renuka B M",
            CertificateRequest.Category.LOR: "Mr. Kishore Kumar L",
        }[category]
        source_paragraph = next(
            paragraph
            for paragraph in _paragraphs(source_document)
            if source_anchor in paragraph.text
        )
        source_run = next(run for run in source_paragraph.runs if source_anchor in run.text)

        for mode in ("online", "offline"):
            with self.subTest(mode=mode):
                content = generate_certificate_bytes(request, template, mode, "docx")
                document = Document(BytesIO(content))
                text = "\n".join(paragraph.text for paragraph in _paragraphs(document))
                generated_paragraph = next(
                    paragraph
                    for paragraph in _paragraphs(document)
                    if "Asha Rao" in paragraph.text
                )
                generated_run = next(
                    run for run in generated_paragraph.runs if "Asha Rao" in run.text
                )
                self.assertIn("Asha Rao", text)
                self.assertEqual(generated_paragraph.alignment, source_paragraph.alignment)
                for attribute in ("name", "size", "bold", "italic"):
                    self.assertEqual(
                        getattr(generated_run.font, attribute),
                        getattr(source_run.font, attribute),
                        attribute,
                    )
                self.assertNotIn("Kishore Kumar", text)
                self.assertNotIn("Vaishnavi", text)
                self.assertNotIn("Renuka", text)
                self.assertNotIn("05th October 2026", text)
                if category == CertificateRequest.Category.OFFER:
                    self.assertIn("3 months from January 2027 to March 2027", text)
                if category == CertificateRequest.Category.EXPERIENCE:
                    self.assertIn("her performance", text)
                if category == CertificateRequest.Category.PROJECT:
                    self.assertIn("his contributions", text)
                if category == CertificateRequest.Category.LOR:
                    self.assertIn("her behalf", text)
                if mode == "offline":
                    self.assertNotIn("Kanishrajiah Durairaju", text)
                    self.assertNotIn("Manager - Human Resource", text)
                    with ZipFile(BytesIO(content)) as archive:
                        self.assertFalse(
                            any(name.startswith("word/media/") for name in archive.namelist())
                        )
                else:
                    with ZipFile(source_path) as source_archive, ZipFile(
                        BytesIO(content)
                    ) as generated_archive:
                        source_assets = {
                            name
                            for name in source_archive.namelist()
                            if name.startswith(("word/header", "word/footer", "word/media/"))
                        }
                        generated_assets = {
                            name
                            for name in generated_archive.namelist()
                            if name.startswith(("word/header", "word/footer", "word/media/"))
                        }
                    self.assertTrue(source_assets)
                    self.assertLessEqual(source_assets, generated_assets)

        self.assertEqual(hashlib.sha256(source_path.read_bytes()).hexdigest(), source_hash)

    def test_offer_docx_generation(self):
        self.assert_source_docx_generated(CertificateRequest.Category.OFFER)

    def test_offer_bullets_are_left_aligned_in_word_and_pdf_conversion_documents(self):
        request = self.create_request(CertificateRequest.Category.OFFER)
        template = self.create_source_template(CertificateTemplate.Category.OFFER)
        source_document = Document(template.document.path)
        source_bullets = (
            "Certificate of Completion",
            "Letter of Recommendation",
            "Recognition & Rewards for Top Performers",
        )
        source_paragraphs = {
            text: next(paragraph for paragraph in source_document.paragraphs if text in paragraph.text)
            for text in source_bullets
        }

        for content in (
            generate_certificate_bytes(request, template, "online", "docx"),
            _docx_bytes_for_pdf(template, request, "online"),
        ):
            generated = Document(BytesIO(content))
            for text in source_bullets:
                paragraph = next(item for item in generated.paragraphs if text in item.text)
                source = source_paragraphs[text]
                self.assertEqual(paragraph.alignment, WD_ALIGN_PARAGRAPH.LEFT)
                self.assertEqual(
                    paragraph.paragraph_format.left_indent,
                    source.paragraph_format.left_indent,
                )
                self.assertEqual(
                    paragraph.paragraph_format.first_line_indent,
                    source.paragraph_format.first_line_indent,
                )
                self.assertEqual(
                    [
                        (run.font.name, run.font.size, run.bold, run.italic)
                        for run in paragraph.runs
                        if run.text
                    ],
                    [
                        (run.font.name, run.font.size, run.bold, run.italic)
                        for run in source.runs
                        if run.text
                    ],
                )
            following = next(
                item for item in generated.paragraphs if "We are delighted to have you join" in item.text
            )
            source_following = next(
                item for item in source_document.paragraphs if "We are delighted to have you join" in item.text
            )
            self.assertEqual(following.alignment, source_following.alignment)

    def test_experience_docx_generation(self):
        self.assert_source_docx_generated(CertificateRequest.Category.EXPERIENCE)

    def test_project_docx_generation(self):
        self.assert_source_docx_generated(CertificateRequest.Category.PROJECT, gender="male")

    def test_lor_docx_generation(self):
        self.assert_source_docx_generated(CertificateRequest.Category.LOR)

    def test_long_student_names_keep_anchor_format_in_all_word_certificates(self):
        long_name = "Janavi Lakshmi Chandeswari"
        source_anchors = {
            CertificateRequest.Category.OFFER: "Ms. Vaishnavi N",
            CertificateRequest.Category.EXPERIENCE: "Mr. Kishore Kumar L",
            CertificateRequest.Category.PROJECT: "Ms. Renuka B M",
            CertificateRequest.Category.LOR: "Mr. Kishore Kumar L",
        }

        for category, source_anchor in source_anchors.items():
            with self.subTest(category=category):
                request = self.create_request(category)
                request.submission.name = long_name
                request.submission.save(update_fields=["name"])
                template = self.create_source_template(category)
                source_document = Document(
                    Path(settings.TEMPLATE_SOURCE_DIR) / SOURCE_FILES[category]
                )
                source_paragraph = next(
                    paragraph
                    for paragraph in _paragraphs(source_document)
                    if source_anchor in paragraph.text
                )
                source_run = next(
                    run for run in source_paragraph.runs if source_anchor in run.text
                )

                for mode in ("online", "offline"):
                    with self.subTest(mode=mode):
                        content = generate_certificate_bytes(
                            request, template, mode, "docx"
                        )
                        document = Document(BytesIO(content))
                        generated_paragraph = next(
                            paragraph
                            for paragraph in _paragraphs(document)
                            if f"Ms. {long_name}" in paragraph.text
                        )
                        generated_run = next(
                            run
                            for run in generated_paragraph.runs
                            if long_name in run.text
                        )
                        self.assertEqual(
                            generated_paragraph.alignment,
                            source_paragraph.alignment,
                        )
                        for attribute in ("name", "size", "bold", "italic"):
                            self.assertEqual(
                                getattr(generated_run.font, attribute),
                                getattr(source_run.font, attribute),
                                attribute,
                            )

    def assert_source_pdf_generated(self, category, gender="female"):
        request = self.create_request(category, gender=gender)
        pdf_source_values = {
            CertificateRequest.Category.EXPERIENCE: {
                "gender": "male",
                "registration_number": "44734015",
                "degree_department": "M.Sc., (Computer Science)",
                "college_name": "Sathyabama University",
                "college_location": "Chennai",
            },
            CertificateRequest.Category.PROJECT: {
                "gender": "female",
                "registration_number": "122311510105",
                "degree_department": "B.Tech (Computer Science and Engineering)",
                "college_name": "The Apollo University",
                "college_location": "Chittoor",
                "project_title": "AI Supply Chain Disruption Predictor using News NLP Weather Data",
                "domain": Domain.objects.get(name="Machine Learning"),
            },
            CertificateRequest.Category.LOR: {"gender": "male"},
        }.get(category, {})
        for field, value in pdf_source_values.items():
            setattr(request.submission, field, value)
        if pdf_source_values:
            request.submission.save(update_fields=list(pdf_source_values))
        template = self.create_source_template(category)
        source_anchor = {
            CertificateRequest.Category.OFFER: "Ms. Vaishnavi N",
            CertificateRequest.Category.EXPERIENCE: "Mr. Kishore Kumar L",
            CertificateRequest.Category.PROJECT: "Ms. Renuka B M",
            CertificateRequest.Category.LOR: "Mr. Kishore Kumar L",
        }[category]
        source_pdf = fitz.open(template.pdf_source.path)
        source_image_count = len(source_pdf[0].get_images(full=True))
        source_pdf.close()

        for mode in ("online", "offline"):
            with self.subTest(mode=mode):
                content = generate_certificate_bytes(request, template, mode, "pdf")
                document = fitz.open(stream=content, filetype="pdf")
                text = "\n".join(page.get_text() for page in document)
                image_count = len(document[0].get_images(full=True))
                generated_spans = [
                    span
                    for block in document[0].get_text("dict")["blocks"]
                    for line in block.get("lines", [])
                    for span in line["spans"]
                ]
                generated_name_span = next(
                    span for span in generated_spans if "Asha Rao" in span["text"]
                )
                source_document = fitz.open(template.pdf_source.path)
                source_spans = [
                    span
                    for block in source_document[0].get_text("dict")["blocks"]
                    for line in block.get("lines", [])
                    for span in line["spans"]
                ]
                source_name_span = next(
                    span for span in source_spans if source_anchor in span["text"]
                )
                self.assertEqual(generated_name_span["size"], source_name_span["size"])
                source_document.close()
                document.close()
                self.assertIn("Asha Rao", text)
                if mode == "online":
                    self.assertIn("Kanishrajiah Durairaju", text)
                    self.assertIn("Manager - Human Resource", text)
                    self.assertEqual(image_count, source_image_count)
                else:
                    self.assertEqual(image_count, 0)
                self.assertNotIn("Kishore Kumar", text)
                self.assertNotIn("Vaishnavi", text)
                self.assertNotIn("Renuka", text)
                self.assertNotIn("05th October 2026", text)
                if category == CertificateRequest.Category.OFFER:
                    self.assertIn("3 months from January 2027 to March 2027", text)
                if mode == "offline":
                    self.assertNotIn("Kanishrajiah Durairaju", text)
                    self.assertNotIn("Manager - Human Resource", text)

    def test_offer_pdf_generation(self):
        self.assert_source_pdf_generated(CertificateRequest.Category.OFFER)

    def test_experience_pdf_generation(self):
        self.assert_source_pdf_generated(CertificateRequest.Category.EXPERIENCE)

    def test_project_pdf_generation(self):
        self.assert_source_pdf_generated(CertificateRequest.Category.PROJECT, gender="male")

    def test_lor_pdf_generation(self):
        self.assert_source_pdf_generated(CertificateRequest.Category.LOR)

    def test_long_values_wrap_in_docx_and_pdf(self):
        long_college_name = "Dr. Mahalingam College of Engineering and Technology"
        long_project_title = "AI Supply Chain Disruption Predictor using News NLP"
        project_college_name = "National Engineering College of Engineering"

        for category, field_name, value in (
            (CertificateRequest.Category.OFFER, "college_name", long_college_name),
            (CertificateRequest.Category.EXPERIENCE, "college_name", long_college_name),
            (CertificateRequest.Category.PROJECT, "project_title", long_project_title),
        ):
            with self.subTest(category=category):
                request = self.create_request(category)
                setattr(request.submission, field_name, value)
                if category == CertificateRequest.Category.PROJECT:
                    request.submission.college_name = project_college_name
                    request.submission.college_location = "Pollachi"
                request.submission.save(update_fields=[field_name])
                if category == CertificateRequest.Category.PROJECT:
                    request.submission.save(update_fields=["college_name", "college_location"])
                template = self.create_source_template(category)

                docx_content = generate_certificate_bytes(request, template, "online", "docx")
                document = Document(BytesIO(docx_content))
                generated_text = "\n".join(
                    paragraph.text for paragraph in _paragraphs(document)
                )
                self.assertIn(value, generated_text)
                if category == CertificateRequest.Category.PROJECT:
                    self.assertIn(
                        f"{project_college_name}, Pollachi",
                        generated_text,
                    )

                if category in {
                    CertificateRequest.Category.EXPERIENCE,
                    CertificateRequest.Category.PROJECT,
                }:
                    pdf_content = generate_certificate_bytes(
                        request,
                        template,
                        "online",
                        "pdf",
                    )
                    pdf_document = fitz.open(
                        stream=pdf_content,
                        filetype="pdf",
                    )
                    pdf_text = " ".join(
                        " ".join(page.get_text().split()) for page in pdf_document
                    )
                    pdf_document.close()
                    self.assertIn(value, pdf_text)
                    if category == CertificateRequest.Category.PROJECT:
                        self.assertIn(
                            f"{project_college_name}, Pollachi",
                            pdf_text,
                        )

    def test_long_dynamic_pdf_values_reflow_for_all_certificate_categories(self):
        long_name = "Amrita Zen A"
        long_registration_number = "123456789012345"
        long_college_name = "Dr. Mahalingam College of Engineering and Technology"
        long_degree = "M.Tech (Artificial Intelligence and Data Science)"
        long_project_title = (
            "AI Supply Chain Disruption Predictor using News NLP and Weather Data"
        )
        values = {
            CertificateRequest.Category.OFFER: {
                "name": long_name,
                "registration_number": long_registration_number,
                "college_name": long_college_name,
                "college_location": "Pollachi",
            },
            CertificateRequest.Category.EXPERIENCE: {
                "name": long_name,
                "registration_number": long_registration_number,
                "degree_department": long_degree,
                "college_name": long_college_name,
                "college_location": "Pollachi",
                "domain": Domain.objects.get(name="Machine Learning"),
            },
            CertificateRequest.Category.PROJECT: {
                "name": long_name,
                "registration_number": long_registration_number,
                "degree_department": long_degree,
                "college_name": long_college_name,
                "college_location": "Pollachi",
                "project_title": long_project_title,
                "domain": Domain.objects.get(name="Machine Learning"),
            },
            CertificateRequest.Category.LOR: {
                "name": long_name,
            },
        }

        for category, submission_values in values.items():
            with self.subTest(category=category):
                request = self.create_request(category)
                for field, value in submission_values.items():
                    setattr(request.submission, field, value)
                request.submission.save(update_fields=list(submission_values))
                template = self.create_source_template(category)

                for mode in ("online", "offline"):
                    with self.subTest(mode=mode):
                        content = generate_certificate_bytes(
                            request, template, mode, "pdf"
                        )
                        document = fitz.open(stream=content, filetype="pdf")
                        text = " ".join(
                            " ".join(page.get_text().split()) for page in document
                        )
                        for page in document:
                            spans = [
                                span
                                for block in page.get_text("dict")["blocks"]
                                for line in block.get("lines", [])
                                for span in line["spans"]
                            ]
                            for span in spans:
                                if not span["text"].strip():
                                    continue
                                rectangle = fitz.Rect(span["bbox"])
                                self.assertTrue(
                                    rectangle.x0 >= -1
                                    and rectangle.y0 >= -1
                                    and rectangle.x1 <= page.rect.width + 1
                                    and rectangle.y1 <= page.rect.height + 1,
                                    f"PDF text is outside the page: {span['text']!r} "
                                    f"{rectangle} / {page.rect}",
                                )
                        document.close()
                        self.assertIn(long_name, text)
                        if category != CertificateRequest.Category.LOR:
                            self.assertIn(long_registration_number, text)
                        if category in {
                            CertificateRequest.Category.EXPERIENCE,
                            CertificateRequest.Category.PROJECT,
                        }:
                            self.assertIn(long_degree, text)
                            self.assertIn(long_college_name, text)
                            self.assertIn("Pollachi", text)
                        if category == CertificateRequest.Category.PROJECT:
                            self.assertIn(long_project_title, text)
                            self.assertIn("Machine Learning", text)

    def test_generation_requires_approval_and_offer_program_configuration(self):
        request = self.create_request(CertificateRequest.Category.OFFER)
        template = self.create_source_template(CertificateTemplate.Category.OFFER)
        OfferProgramSettings.objects.update(start_month=None, end_month=None)

        with self.assertRaisesMessage(DocumentGenerationError, "Approve this certificate request"):
            request.status = CertificateRequest.Status.PENDING
            generate_certificate_bytes(request, template, "online", "docx")

        request.status = CertificateRequest.Status.APPROVED
        with self.assertRaisesMessage(DocumentGenerationError, "Set the Offer Letter program"):
            generate_certificate_bytes(request, template, "online", "docx")


class CertificateFilenameTests(PortalTestCase):
    def test_filename_uses_student_name_and_certificate_abbreviation_for_all_types(self):
        expected = {
            CertificateRequest.Category.OFFER: "Janavi T_OL",
            CertificateRequest.Category.EXPERIENCE: "Janavi T_EC",
            CertificateRequest.Category.PROJECT: "Janavi T_PCC",
            CertificateRequest.Category.LOR: "Janavi T_LOR",
        }
        for category, stem in expected.items():
            request = self.create_request(category)
            request.submission.name = "Janavi T"
            for mode in ("online", "offline"):
                for file_format in ("pdf", "docx"):
                    with self.subTest(category=category, mode=mode, format=file_format):
                        self.assertEqual(
                            certificate_filename(request, mode, file_format),
                            f"{stem}.{file_format}",
                        )

    def test_backup_download_uses_current_dynamic_filename_not_stored_template_name(self):
        user = self.create_admin()
        self.client.force_login(user)
        request = self.create_request(CertificateRequest.Category.OFFER)
        request.submission.name = "Janavi T"
        request.submission.save(update_fields=["name"])
        certificate = GeneratedCertificate(
            request=request,
            mode=GeneratedCertificate.Mode.OFFLINE,
            file_format=GeneratedCertificate.Format.PDF,
            file_name="Offer Letter Template.pdf",
            content_hash=hashlib.sha256(b"previous certificate").hexdigest(),
        )
        certificate.file.save(
            "Offer Letter Template.pdf",
            ContentFile(b"previous certificate"),
            save=False,
        )
        certificate.save()

        response = self.client.get(
            reverse("admin_certificate_download", args=[certificate.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="Janavi T_OL.pdf"', response["Content-Disposition"])
        self.assertNotIn("Template", response["Content-Disposition"])
        response.close()

        archive = self.client.get(reverse("admin_backups"))
        self.assertContains(archive, "Janavi T_OL.pdf")
        self.assertNotContains(archive, "Offer Letter Template.pdf")

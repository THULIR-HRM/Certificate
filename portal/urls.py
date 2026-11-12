from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("register/offer/", views.student_register, {"form_type": "offer"}, name="register_offer"),
    path(
        "register/internship/",
        views.student_register,
        {"form_type": "internship"},
        name="register_internship",
    ),
    path("admin/login/", views.admin_login, name="admin_login"),
    path("admin/change-password/", views.admin_password_change, name="admin_password_change"),
    path("admin/logout/", views.admin_logout, name="admin_logout"),
    path("admin/dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("admin/qr-codes/", views.admin_qr_management, name="admin_qr_management"),
    path("admin/qr-codes/<slug:form_type>.png", views.admin_qr_image, name="admin_qr_image"),
    path("admin/submissions/", views.admin_submission_list, name="admin_submission_list"),
    path(
        "admin/submissions/offer/",
        views.admin_submission_list,
        {"category": "offer"},
        name="admin_offer_submissions",
    ),
    path(
        "admin/submissions/experience/",
        views.admin_submission_list,
        {"category": "experience"},
        name="admin_experience_submissions",
    ),
    path(
        "admin/submissions/project/",
        views.admin_submission_list,
        {"category": "project"},
        name="admin_project_submissions",
    ),
    path(
        "admin/submissions/lor/",
        views.admin_submission_list,
        {"category": "lor"},
        name="admin_lor_submissions",
    ),
    path(
        "admin/submissions/<uuid:submission_id>/",
        views.admin_submission_detail,
        name="admin_submission_detail",
    ),
    path(
        "admin/submissions/<uuid:submission_id>/delete/",
        views.admin_submission_delete,
        name="admin_submission_delete",
    ),
    path(
        "admin/certificate-requests/<int:request_id>/decision/",
        views.admin_certificate_decision,
        name="admin_certificate_decision",
    ),
    path(
        "admin/certificate-requests/<int:request_id>/generate/",
        views.admin_generate_certificate,
        name="admin_generate_certificate",
    ),
    path(
        "admin/certificates/<int:certificate_id>/download/",
        views.admin_certificate_download,
        name="admin_certificate_download",
    ),
    path("admin/backups/", views.admin_backups, name="admin_backups"),
    path(
        "admin/backups/offer/",
        views.admin_backups,
        {"category": "offer"},
        name="admin_offer_backups",
    ),
    path(
        "admin/backups/experience/",
        views.admin_backups,
        {"category": "experience"},
        name="admin_experience_backups",
    ),
    path(
        "admin/backups/project/",
        views.admin_backups,
        {"category": "project"},
        name="admin_project_backups",
    ),
    path(
        "admin/backups/lor/",
        views.admin_backups,
        {"category": "lor"},
        name="admin_lor_backups",
    ),
    path("admin/domains/", views.admin_domains, name="admin_domains"),
    path("admin/catalog/", views.admin_catalog, name="admin_catalog"),
    path(
        "admin/catalog/<slug:kind>/<int:item_id>/",
        views.admin_catalog_edit,
        name="admin_catalog_edit",
    ),
    path("admin/domains/<int:domain_id>/", views.admin_domain_edit, name="admin_domain_edit"),
    path(
        "admin/domains/<int:domain_id>/delete/",
        views.admin_domain_delete,
        name="admin_domain_delete",
    ),
    path("admin/form-settings/", views.admin_form_settings, name="admin_form_settings"),
    path(
        "admin/form-settings/<int:field_id>/",
        views.admin_form_field_edit,
        name="admin_form_field_edit",
    ),
    path(
        "admin/form-settings/custom/add/",
        views.admin_custom_field_add,
        name="admin_custom_field_add",
    ),
    path(
        "admin/form-settings/custom/<int:field_id>/delete/",
        views.admin_custom_field_delete,
        name="admin_custom_field_delete",
    ),
    path("admin/settings/", views.admin_settings, name="admin_settings"),
    path("admin/templates/", views.admin_templates, name="admin_templates"),
    path(
        "admin/templates/<int:template_id>/preview/",
        views.admin_template_preview,
        name="admin_template_preview",
    ),
    path("admin/audit-log/", views.admin_audit_log, name="admin_audit_log"),
]

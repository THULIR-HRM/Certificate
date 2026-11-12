import re

from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError

from .models import (
    CollegeCategory,
    CollegeDistrict,
    CollegeOption,
    CertificateRequest,
    CertificateTemplate,
    DegreeOption,
    Domain,
    FormFieldSetting,
    OfferProgramSettings,
    SpecializationOption,
    StudentSubmission,
)
from .catalog_data import INDIAN_STATES_AND_UTS, normalize_degree_label


NAME_TOKEN = re.compile(r"(?:[A-Z][a-z]+|[A-Z])\Z")
REGISTRATION_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ./_-]*\Z")
DEGREE_TOKEN_PATTERN = re.compile(
    r"^(?P<degree>[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)*\.?)(?P<suffix>.*)$"
)
CANONICAL_DEGREES = {
    "b.e": "B.E",
    "m.e": "M.E",
    "b.tech": "B.Tech",
    "m.tech": "M.Tech",
    "b.sc": "B.Sc.",
    "m.sc": "M.Sc.",
    "b.com": "B.Com.",
    "m.com": "M.Com.",
    "bca": "BCA",
    "mca": "MCA",
    "mba": "MBA",
    "bba": "BBA",
    "b.arch": "B.Arch",
    "m.arch": "M.Arch",
    "b.pharm": "B.Pharm",
    "m.pharm": "M.Pharm",
    "b.des": "B.Des",
    "m.des": "M.Des",
    "bfa": "BFA",
    "mfa": "MFA",
    "bpt": "BPT",
    "bds": "BDS",
    "mbbs": "MBBS",
    "llb": "LLB",
    "llm": "LLM",
    "phd": "PhD",
    "bsw": "BSW",
    "b.hm": "B.HM",
}
CONNECTING_WORDS = {"and", "or", "of", "in", "the", "for", "at"}
KNOWN_ACRONYMS = {"AI", "ML", "OCR", "IoT", "HRM", "FSD", "IT", "NLP", "B.E", "M.E"}
SPECIALIZATION_ACRONYMS = {
    "ai": "AI",
    "api": "API",
    "bca": "BCA",
    "bds": "BDS",
    "cse": "CSE",
    "ece": "ECE",
    "fsd": "FSD",
    "hrm": "HRM",
    "iot": "IoT",
    "it": "IT",
    "mba": "MBA",
    "mca": "MCA",
    "ml": "ML",
    "nlp": "NLP",
    "ocr": "OCR",
    "phd": "PhD",
    "sql": "SQL",
}
ESSENTIAL_FIELDS = {
    "title",
    "name",
    "gender",
    "mobile",
    "registration_number",
    "registered_email",
    "degree_department",
    "college",
    "domain",
    "from_month",
    "to_month",
    "project_title",
    "feedback",
}


def validate_title_case(value, field_name):
    value = value.strip()
    if not value or value != " ".join(value.split()):
        raise ValidationError(f"{field_name} must not start/end with spaces or contain repeated spaces.")

    words = value.split(" ")
    for index, word in enumerate(words):
        pieces = re.split(r"([-&])", word)
        after_hyphen = False
        for piece in pieces:
            if not piece:
                continue
            if piece == "-":
                after_hyphen = True
                continue
            if piece == "&":
                continue
            if piece in KNOWN_ACRONYMS:
                after_hyphen = False
                continue
            if piece.endswith(".") and piece[:-1].isalpha() and piece[:-1][0].isupper():
                if piece[:-1][1:].islower() or len(piece[:-1]) == 1:
                    after_hyphen = False
                    continue
            if piece.lower() in CONNECTING_WORDS:
                if index == 0 or piece != piece.lower():
                    if index == 0 and piece == piece[0].upper() + piece[1:].lower():
                        after_hyphen = False
                        continue
                    raise ValidationError(
                        f"Use lowercase for connecting words such as '{piece.lower()}' within {field_name}."
                    )
                after_hyphen = False
                continue
            if after_hyphen and piece.islower():
                after_hyphen = False
                continue
            if not re.fullmatch(r"[A-Z][a-z]+|[A-Z]{2,}", piece):
                raise ValidationError(
                    f"'{piece}' in {field_name} must use Title Case; preserve acronyms such as AI or ML."
                )
            after_hyphen = False


def validate_name(value):
    if value != value.strip() or "  " in value:
        raise ValidationError("Enter the name without leading, trailing, or repeated spaces.")
    if not all(NAME_TOKEN.fullmatch(token) for token in value.split(" ")):
        raise ValidationError(
            "Invalid name format. Each name must start with a capital letter, followed by lowercase "
            "letters. Initials must be uppercase."
        )


def _canonical_degree_token(value):
    normalized = value.rstrip(".").casefold()
    if normalized in CANONICAL_DEGREES:
        return CANONICAL_DEGREES[normalized]

    parts = value.rstrip(".").split(".")
    if len(parts) > 1:
        canonical_parts = []
        for index, part in enumerate(parts):
            if len(part) == 1 or part.isupper():
                canonical_parts.append(part.upper())
            elif index == 0 and part.casefold() in {"b", "m"}:
                canonical_parts.append(part.upper())
            else:
                canonical_parts.append(part.capitalize())
        return ".".join(canonical_parts) + ("." if value.endswith(".") else "")

    if len(value) <= 4 or value.isupper():
        return value.upper()
    if value[0].casefold() in {"b", "m"} and value[1:].isalpha():
        return value[0].upper() + value[1].capitalize()
    return value.capitalize()


def _canonical_specialization(value):
    normalized = " ".join(value.split())
    first_word = True

    def format_word(match):
        nonlocal first_word
        word = match.group(0)
        key = word.casefold()
        if key in SPECIALIZATION_ACRONYMS:
            result = SPECIALIZATION_ACRONYMS[key]
        elif key in CONNECTING_WORDS and not first_word:
            result = key
        elif word.isupper() and len(word) <= 6:
            result = word
        else:
            result = word[0].upper() + word[1:].lower()
        first_word = False
        return result

    return re.sub(r"[A-Za-z][A-Za-z0-9]*", format_word, normalized)


def _degree_parts(value):
    match = DEGREE_TOKEN_PATTERN.fullmatch(value)
    if not match:
        return None
    degree = match.group("degree")
    suffix = match.group("suffix")
    stripped_suffix = suffix.strip()
    if not stripped_suffix:
        return degree, "", "", ""

    if stripped_suffix.startswith("("):
        specialization_match = re.fullmatch(r"\(\s*([^()]*)\s*\)", stripped_suffix)
        if not specialization_match or not specialization_match.group(1).strip():
            return None
        return degree, specialization_match.group(1).strip(), "parentheses", suffix

    if stripped_suffix.startswith(","):
        remainder = stripped_suffix[1:].strip()
        if remainder.startswith("("):
            specialization_match = re.fullmatch(r"\(\s*([^()]*)\s*\)", remainder)
            if not specialization_match or not specialization_match.group(1).strip():
                return None
            return degree, specialization_match.group(1).strip(), "comma_parentheses", suffix
        if not remainder or any(character in remainder for character in "()"):
            return None
        return degree, remainder, "comma", suffix

    if suffix[:1].isspace() and not any(character in stripped_suffix for character in "(),"):
        return degree, stripped_suffix, "unbracketed", suffix
    return None


def suggest_degree_format(value):
    parts = _degree_parts(value)
    if parts is None:
        return None

    degree, specialization, suffix_style, _ = parts
    formatted_degree = _canonical_degree_token(degree)
    if not specialization:
        return formatted_degree

    formatted_specialization = _canonical_specialization(specialization)
    if not formatted_specialization:
        return None
    if formatted_specialization.casefold() == "general":
        return f"{formatted_degree} (General)"
    if suffix_style == "comma":
        return f"{formatted_degree}, {formatted_specialization}"
    if suffix_style == "comma_parentheses":
        return f"{formatted_degree}, ({formatted_specialization})"
    return f"{formatted_degree} ({formatted_specialization})"


def validate_degree(value):
    suggestion = suggest_degree_format(value)
    if suggestion is None:
        raise ValidationError(
            "Enter a degree abbreviation followed by a specialization in parentheses or after a comma."
        )
    if suggestion != value:
        degree, specialization, suffix_style, _ = _degree_parts(value)
        original_degree = _canonical_degree_token(degree)
        reasons = []
        if original_degree != degree:
            reasons.append("The degree abbreviation must use its standard capitalization and punctuation.")
        if specialization and specialization.casefold() == "general":
            if suffix_style not in {"parentheses", "comma_parentheses"}:
                reasons.append("General degrees must use the format (General).")
            elif suggestion.endswith("(General)") and "(General)" not in value:
                reasons.append("Write General with an uppercase G.")
        elif specialization and _canonical_specialization(specialization) != specialization:
            reasons.append("The specialization must use standard capitalization and spacing.")
        if suffix_style == "unbracketed":
            reasons.append("Enclose the specialization in parentheses or follow it with a comma.")
        if not reasons:
            reasons.append("Remove extra spaces and use the suggested degree format.")
        raise ValidationError(" ".join(reasons))


def canonicalize_degree_entry(value):
    value = " ".join(value.split())
    degrees = list(DegreeOption.objects.filter(active=True))
    normalized_value = normalize_degree_label(value)
    for degree in degrees:
        labels = [degree.abbreviation, degree.full_name, *degree.aliases]
        if normalized_value in {normalize_degree_label(label) for label in labels}:
            return degree.abbreviation

    parenthesized = re.fullmatch(r"(.+?)\s*\(\s*([^()]*)\s*\)", value)
    comma_separated = re.fullmatch(r"(.+?),\s*(?:\(\s*([^()]*)\s*\)|([^()]+))", value)
    if parenthesized:
        base, specialization = parenthesized.group(1).strip(), parenthesized.group(2).strip()
    elif comma_separated:
        base = comma_separated.group(1).strip()
        specialization = (comma_separated.group(2) or comma_separated.group(3)).strip()
    else:
        parsed = _degree_parts(value)
        if parsed:
            base, specialization, _, _ = parsed
        else:
            base, specialization = value, ""

    normalized_base = normalize_degree_label(base)
    for degree in degrees:
        labels = [degree.abbreviation, degree.full_name, *degree.aliases]
        if normalized_base not in {normalize_degree_label(label) for label in labels}:
            continue
        formatted_specialization = _canonical_specialization(specialization)
        if not specialization:
            return degree.abbreviation
        if degree.abbreviation in {"B.Sc.", "M.Sc."}:
            return f"{degree.abbreviation}, {formatted_specialization}"
        return f"{degree.abbreviation} ({formatted_specialization})"

    return None


def validate_mobile(value):
    if not re.fullmatch(r"[0-9]{10}", value):
        raise ValidationError("Enter exactly 10 digits for the mobile number.")


def validate_gmail(value):
    if not value.lower().endswith("@gmail.com"):
        raise ValidationError("Enter a valid Gmail address ending in @gmail.com.")


class StudentSubmissionForm(forms.Form):
    TITLE_CHOICES = [("", "Select title"), ("Mr.", "Mr."), ("Ms.", "Ms."), ("Mrs.", "Mrs.")]
    title = forms.ChoiceField(choices=TITLE_CHOICES)
    name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    gender = forms.ChoiceField(
        choices=StudentSubmission.Gender.choices,
        widget=forms.RadioSelect,
    )
    mobile = forms.CharField(max_length=10, widget=forms.TextInput(attrs={"inputmode": "numeric"}))
    registration_number = forms.CharField(max_length=100)
    registered_email = forms.EmailField(max_length=254)
    alternative_email = forms.EmailField(max_length=254, required=False)
    degree_department = forms.CharField(
        max_length=160, required=False, strip=False, widget=forms.HiddenInput()
    )
    degree_search = forms.CharField(
        max_length=120,
        required=False,
        label="Search degree",
        widget=forms.TextInput(attrs={"type": "search", "autocomplete": "off"}),
    )
    degree_option = forms.ChoiceField(
        required=False,
        choices=[("", "Select a degree"), ("__other__", "Other degree (enter manually)")],
        label="Degree",
    )
    degree_other_name = forms.CharField(max_length=100, required=False, label="Enter degree name")
    specialization_option = forms.ChoiceField(
        required=False,
        choices=[("", "No specialization selected"), ("__other__", "Other specialization")],
        label="Department / Specialization",
    )
    specialization_other = forms.CharField(
        max_length=100, required=False, label="Enter department / specialization"
    )
    college = forms.CharField(max_length=320, required=False, widget=forms.HiddenInput())
    college_search = forms.CharField(
        max_length=160,
        required=False,
        label="Search college",
        widget=forms.TextInput(
            attrs={
                "type": "search",
                "autocomplete": "off",
                "placeholder": "Search by college name, city, or district",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-controls": "college-option-list",
                "aria-expanded": "false",
            }
        ),
    )
    college_district_filter = forms.ChoiceField(
        required=False, choices=[("", "All districts")], label="Filter by district"
    )
    college_state_filter = forms.ChoiceField(
        required=False, choices=[("", "All states")], label="Filter by state"
    )
    college_category_filter = forms.ChoiceField(
        required=False, choices=[("", "All categories")], label="Filter by category"
    )
    college_affiliation_filter = forms.CharField(
        max_length=160,
        required=False,
        label="Filter by university / affiliation",
        widget=forms.TextInput(attrs={"type": "search", "autocomplete": "off"}),
    )
    college_option = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Select a college"),
            ("__other__", "Other College / College Not Listed (Tamil Nadu)"),
            ("__other_state__", "Other state / college not listed"),
        ],
        label="College",
    )
    college_state = forms.ChoiceField(
        required=False,
        choices=[("", "Select state or union territory")]
        + [(state, state) for state in INDIAN_STATES_AND_UTS],
        label="State / Union territory",
    )
    college_other_name = forms.CharField(max_length=200, required=False, label="Enter college name")
    college_other_location = forms.CharField(
        max_length=120, required=False, label="Enter college location / city"
    )
    domain = forms.ModelChoiceField(
        queryset=Domain.objects.none(),
        required=False,
        widget=forms.RadioSelect,
    )
    new_domain = forms.CharField(max_length=100, required=False, label="Add a new domain")
    submission_token = forms.UUIDField(widget=forms.HiddenInput)
    from_month = forms.DateField(
        required=False,
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )
    to_month = forms.DateField(
        required=False,
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )
    project_title = forms.CharField(max_length=200, required=False)
    feedback = forms.CharField(
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 5, "maxlength": 2000}),
    )

    def __init__(self, *args, form_type, **kwargs):
        self.form_type = form_type
        self.degree_suggestion = None
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update(
            {
                "pattern": r"(?:[A-Z][a-z]+|[A-Z])(?: (?:[A-Z][a-z]+|[A-Z]))*",
                "title": "Each word starts with a capital; initials are uppercase single letters.",
            }
        )
        self.fields["mobile"].widget.attrs.update(
            {"pattern": "[0-9]{10}", "maxlength": "10", "title": "Enter exactly 10 digits."}
        )
        self.fields["registration_number"].widget.attrs.update(
            {
                "pattern": r"[A-Za-z0-9][A-Za-z0-9 ./_-]*",
                "title": "Use letters, numbers, spaces, and common separators.",
            }
        )
        self.fields["domain"].queryset = Domain.objects.filter(active=True)
        degrees = list(DegreeOption.objects.filter(active=True))
        specializations = list(
            SpecializationOption.objects.filter(active=True)
            .exclude(name__iexact="Other")
            .select_related("degree")
        )
        colleges = list(
            CollegeOption.objects.filter(
                active=True, district__active=True, category__active=True
            ).select_related("district", "category")
        )
        self.fields["degree_option"].choices = [
            ("", "Select a degree"),
            *((str(degree.pk), str(degree)) for degree in degrees),
            ("__other__", "Other degree (enter manually)"),
        ]
        self.fields["specialization_option"].choices = [
            ("", "No specialization selected"),
            *((str(item.pk), item.name) for item in specializations),
            ("__other__", "Other specialization"),
        ]
        self.fields["college_option"].choices = [
            ("", "Select a college"),
            *(
                (
                    str(item.pk),
                    f"{item.name}, {item.location} - {item.district.name} ({item.category.name})",
                )
                for item in colleges
            ),
            ("__other__", "Other College / College Not Listed (Tamil Nadu)"),
            ("__other_state__", "Other state / college not listed"),
        ]
        self.degree_options = degrees
        self.specialization_options = specializations
        self.college_options = colleges
        self.district_options = CollegeDistrict.objects.filter(active=True)
        self.category_options = CollegeCategory.objects.filter(active=True)
        self.college_state_options = sorted({item.state for item in colleges})
        self.fields["college_state_filter"].choices = [
            ("", "All states"),
            *((state, state) for state in self.college_state_options),
        ]
        self.fields["college_district_filter"].choices = [
            ("", "All districts"),
            *((str(item.pk), item.name) for item in self.district_options),
        ]
        self.fields["college_category_filter"].choices = [
            ("", "All categories"),
            *((str(item.pk), item.name) for item in self.category_options),
        ]
        self.state_options = INDIAN_STATES_AND_UTS
        self.field_settings = {
            setting.key: setting
            for setting in FormFieldSetting.objects.filter(form_type=form_type)
        }
        if form_type == FormFieldSetting.FormType.OFFER:
            for key in ("from_month", "to_month", "project_title", "feedback"):
                self.fields.pop(key, None)
        for key, setting in self.field_settings.items():
            if key not in self.fields:
                if setting.is_custom and setting.enabled:
                    custom_field = forms.CharField(
                        label=setting.label,
                        required=setting.required,
                        max_length=setting.max_length or 200,
                        widget=forms.Textarea(attrs={"rows": 3})
                        if setting.character_policy == FormFieldSetting.CharacterPolicy.FREE_TEXT
                        else forms.TextInput(),
                    )
                    custom_field.widget.attrs["placeholder"] = setting.placeholder
                    if setting.min_length:
                        custom_field.min_length = setting.min_length
                    self.fields[key] = custom_field
                continue
            if not setting.enabled and key not in ESSENTIAL_FIELDS:
                self.fields.pop(key)
                continue
            field = self.fields[key]
            field.label = setting.label
            field.required = setting.required if key not in ESSENTIAL_FIELDS else True
            field.widget.attrs["placeholder"] = setting.placeholder
            if setting.min_length is not None and hasattr(field, "min_length"):
                field.min_length = setting.min_length
            if setting.max_length is not None and hasattr(field, "max_length"):
                field.max_length = setting.max_length
        for key in ("degree_department", "college"):
            self.fields[key].required = False
        order = sorted(
            self.fields,
            key=lambda key: self.field_settings[key].display_order
            if key in self.field_settings
            else 1000,
        )
        self.order_fields(order)

    def clean_title(self):
        value = self.cleaned_data["title"]
        if not value:
            raise ValidationError("Select a title.")
        return value

    def clean_name(self):
        value = " ".join(self.cleaned_data["name"].split())
        validate_name(value)
        return value

    def clean_mobile(self):
        value = self.cleaned_data["mobile"].strip()
        validate_mobile(value)
        return value

    def clean_registration_number(self):
        value = self.cleaned_data["registration_number"].strip()
        if not value or not REGISTRATION_NUMBER.fullmatch(value):
            raise ValidationError(
                "Use letters, numbers, spaces, and common separators such as /, - or _."
            )
        return value

    def clean_registered_email(self):
        value = self.cleaned_data["registered_email"].strip().lower()
        validate_gmail(value)
        return value

    def clean_alternative_email(self):
        value = self.cleaned_data.get("alternative_email", "").strip().lower()
        if value:
            validate_gmail(value)
        return value

    def clean_degree_department(self):
        return self.cleaned_data["degree_department"].strip()

    def clean_college(self):
        return self.cleaned_data["college"].strip()

    def clean_new_domain(self):
        value = " ".join(self.cleaned_data.get("new_domain", "").split())
        if value:
            validate_title_case(value, "domain")
        return value

    def clean_project_title(self):
        value = " ".join(self.cleaned_data.get("project_title", "").split())
        if value:
            if not any(character.isalpha() for character in value):
                raise ValidationError("Project title must contain words, not only numbers.")
            validate_title_case(value, "project title")
        return value

    def clean_feedback(self):
        return self.cleaned_data.get("feedback", "").strip()

    def clean(self):
        cleaned = super().clean()
        self._clean_degree_department(cleaned)
        self._clean_college(cleaned)

        email = cleaned.get("registered_email")
        alternative = cleaned.get("alternative_email")
        if email and alternative and email.casefold() == alternative.casefold():
            self.add_error("alternative_email", "Alternative Gmail must differ from registered Gmail.")

        title = cleaned.get("title")
        gender = cleaned.get("gender")
        if title and gender:
            if gender == StudentSubmission.Gender.MALE and title != "Mr.":
                self.add_error("title", "Select Mr. for Male.")
            if gender == StudentSubmission.Gender.FEMALE and title not in {"Ms.", "Mrs."}:
                self.add_error("title", "Select Ms. or Mrs. for Female.")

        selected_domain = cleaned.get("domain")
        new_domain = cleaned.get("new_domain", "")
        if selected_domain and new_domain:
            self.add_error("new_domain", "Select an existing domain or add a new one, not both.")
        elif not selected_domain and not new_domain:
            self.add_error("domain", "Select a domain or add a new domain.")

        if self.form_type == FormFieldSetting.FormType.INTERNSHIP:
            start = cleaned.get("from_month")
            end = cleaned.get("to_month")
            if not start:
                self.add_error("from_month", "Select the starting month and year.")
            if not end:
                self.add_error("to_month", "Select the ending month and year.")
            if start and end and end < start:
                self.add_error("to_month", "Ending month must not be earlier than starting month.")
            if not cleaned.get("project_title"):
                self.add_error("project_title", "Enter the project title.")
            if not cleaned.get("feedback"):
                self.add_error("feedback", "Enter your internship experience feedback.")

        for key, setting in self.field_settings.items():
            value = cleaned.get(key)
            if value is None or not isinstance(value, str):
                continue
            if setting.min_length and len(value) < setting.min_length:
                self.add_error(
                    key,
                    setting.validation_message
                    or f"Enter at least {setting.min_length} characters.",
                )
            if setting.max_length and len(value) > setting.max_length:
                self.add_error(
                    key,
                    setting.validation_message
                    or f"Enter no more than {setting.max_length} characters.",
                )
            policy = setting.character_policy
            if policy == FormFieldSetting.CharacterPolicy.LETTERS_SPACES and not re.fullmatch(
                r"[A-Za-z ]+", value
            ):
                self.add_error(key, setting.validation_message or "Use letters and spaces only.")
            if policy == FormFieldSetting.CharacterPolicy.DIGITS and not value.isdigit():
                self.add_error(key, setting.validation_message or "Use digits only.")
            if policy == FormFieldSetting.CharacterPolicy.REGISTRATION and not REGISTRATION_NUMBER.fullmatch(
                value
            ):
                self.add_error(
                    key, setting.validation_message or "Use only letters, numbers, and separators."
                )
        return cleaned

    def _clean_degree_department(self, cleaned):
        selected_degree = cleaned.get("degree_option", "")
        degree_name = ""
        degree = None
        if selected_degree == "__other__":
            entered_degree = cleaned.get("degree_other_name", "").strip()
            if not entered_degree:
                self.add_error("degree_other_name", "Enter the degree name.")
                return
            canonical = canonicalize_degree_entry(entered_degree)
            if canonical:
                parsed = _degree_parts(canonical)
                if parsed:
                    degree_name = _canonical_degree_token(parsed[0])
                else:
                    degree_name = canonical
            else:
                degree_name = _canonical_specialization(entered_degree)
        elif selected_degree:
            degree = next(
                (item for item in self.degree_options if str(item.pk) == selected_degree),
                None,
            )
            if degree is None:
                self.add_error("degree_option", "Select an active degree option.")
                return
            degree_name = degree.abbreviation
        else:
            legacy_value = cleaned.get("degree_department", "")
            if not legacy_value:
                self.add_error("degree_option", "Select a degree or choose Other.")
                return
            canonical = canonicalize_degree_entry(legacy_value)
            if canonical:
                cleaned["degree_department"] = canonical
                return
            try:
                validate_degree(legacy_value)
            except ValidationError as error:
                self.degree_suggestion = suggest_degree_format(legacy_value)
                self.add_error("degree_department", error)
                return
            cleaned["degree_department"] = legacy_value
            return

        selected_specialization = cleaned.get("specialization_option", "")
        specialization = ""
        if selected_specialization == "__other__":
            specialization = cleaned.get("specialization_other", "").strip()
            if not specialization:
                self.add_error(
                    "specialization_other",
                    "Enter the department or specialization.",
                )
                return
            specialization = _canonical_specialization(specialization)
        elif selected_specialization:
            item = next(
                (
                    option
                    for option in self.specialization_options
                    if str(option.pk) == selected_specialization
                ),
                None,
            )
            if item is None or degree is None or item.degree_id != degree.pk:
                self.add_error(
                    "specialization_option",
                    "Choose a specialization belonging to the selected degree.",
                )
                return
            if normalize_degree_label(item.name) == "other":
                specialization = cleaned.get("specialization_other", "").strip()
                if not specialization:
                    self.add_error(
                        "specialization_other",
                        "Enter the department or specialization.",
                    )
                    return
                specialization = _canonical_specialization(specialization)
            else:
                specialization = item.name

        if specialization:
            if degree_name in {"B.Sc.", "M.Sc."}:
                output = f"{degree_name}, {specialization}"
            else:
                output = f"{degree_name} ({specialization})"
        else:
            output = degree_name
        if not output or len(output) > 160:
            self.add_error("degree_option", "The degree and specialization are too long.")
            return
        cleaned["degree_department"] = output

    def _clean_college(self, cleaned):
        selected_college = cleaned.get("college_option", "")
        if selected_college and selected_college not in {"__other__", "__other_state__"}:
            college = next(
                (item for item in self.college_options if str(item.pk) == selected_college),
                None,
            )
            if college is None:
                self.add_error("college_option", "Select an active college option.")
                return
            cleaned["college"] = f"{college.name}, {college.location}"
            cleaned["college_name"] = college.name
            cleaned["college_location"] = college.location
            return

        if selected_college in {"__other__", "__other_state__"}:
            name = " ".join(cleaned.get("college_other_name", "").split())
            location = " ".join(cleaned.get("college_other_location", "").split())
            state = cleaned.get("college_state", "")
            if not name:
                self.add_error("college_other_name", "Enter the college name.")
            if not location:
                self.add_error("college_other_location", "Enter the college location or city.")
            if not state:
                self.add_error("college_state", "Select a state or union territory.")
            elif selected_college == "__other__" and state != "Tamil Nadu":
                self.add_error(
                    "college_state",
                    "Choose Other state / college not listed for a college outside Tamil Nadu.",
                )
            elif selected_college == "__other_state__" and state == "Tamil Nadu":
                self.add_error(
                    "college_state",
                    "Choose Other Tamil Nadu college for an institution in Tamil Nadu.",
                )
            if name and location:
                try:
                    validate_title_case(name, "college name")
                    validate_title_case(location, "college location")
                except ValidationError as error:
                    self.add_error("college_other_name", error)
                    return
                if state and state != "Tamil Nadu":
                    location = f"{location}, {state}"
                cleaned["college"] = f"{name}, {location}"
                cleaned["college_name"] = name
                cleaned["college_location"] = location
            return

        legacy_value = cleaned.get("college", "")
        if not legacy_value:
            self.add_error("college_option", "Select a college or choose an Other option.")
            return
        value = " ".join(legacy_value.split())
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 2 or not all(parts):
            self.add_error("college", "Enter the college name and location separated by one comma.")
            return
        try:
            validate_title_case(parts[0], "college name")
            validate_title_case(parts[1], "college location")
        except ValidationError as error:
            self.add_error("college", error)
            return
        cleaned["college"] = value
        cleaned["college_name"] = parts[0]
        cleaned["college_location"] = parts[1]


class AdminLoginForm(forms.Form):
    username = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


class AdminPasswordChangeForm(PasswordChangeForm):
    pass


class StudentSubmissionEditForm(forms.ModelForm):
    class Meta:
        model = StudentSubmission
        exclude = [
            "submission_id",
            "submission_token",
            "source",
            "custom_data",
            "created_at",
            "updated_at",
        ]
        widgets = {
            "internship_start": forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
            "internship_end": forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
            "feedback": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_name(self):
        value = " ".join(self.cleaned_data["name"].split())
        validate_name(value)
        return value


class CertificateDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            (CertificateRequest.Status.APPROVED, "Approve"),
            (CertificateRequest.Status.REJECTED, "Reject"),
        ]
    )
    rejection_reason = forms.CharField(max_length=500, required=False)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("decision") == CertificateRequest.Status.REJECTED
            and not cleaned.get("rejection_reason", "").strip()
        ):
            self.add_error("rejection_reason", "Provide a reason when rejecting a request.")
        return cleaned


class DomainForm(forms.ModelForm):
    class Meta:
        model = Domain
        fields = ["name", "is_technical", "active"]

    def clean_name(self):
        value = " ".join(self.cleaned_data["name"].split())
        validate_title_case(value, "domain")
        return value


class DegreeOptionForm(forms.ModelForm):
    aliases_text = forms.CharField(
        required=False,
        label="Search aliases",
        help_text="Enter one abbreviation or equivalent name per line.",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    class Meta:
        model = DegreeOption
        fields = ["abbreviation", "full_name", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["aliases_text"].initial = "\n".join(self.instance.aliases)

    def clean_aliases_text(self):
        entered_aliases = [
            " ".join(alias.split())
            for alias in self.cleaned_data["aliases_text"].splitlines()
            if alias.strip()
        ]
        seen = {
            normalize_degree_label(self.cleaned_data.get("abbreviation", "")),
            normalize_degree_label(self.cleaned_data.get("full_name", "")),
        }
        aliases = []
        for alias in entered_aliases:
            normalized = normalize_degree_label(alias)
            if normalized and normalized not in seen:
                aliases.append(alias)
                seen.add(normalized)
        return aliases

    def clean(self):
        cleaned = super().clean()
        if {"abbreviation", "full_name", "aliases_text"}.issubset(cleaned):
            self.instance.abbreviation = cleaned["abbreviation"]
            self.instance.full_name = cleaned["full_name"]
            self.instance.aliases = cleaned["aliases_text"]
            try:
                self.instance.clean()
            except ValidationError as error:
                self.add_error(None, error)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.aliases = self.cleaned_data["aliases_text"]
        instance.full_clean()
        if commit:
            instance.save()
        return instance


class SpecializationOptionForm(forms.ModelForm):
    class Meta:
        model = SpecializationOption
        fields = ["degree", "name", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["degree"].queryset = DegreeOption.objects.all()


class CollegeDistrictForm(forms.ModelForm):
    class Meta:
        model = CollegeDistrict
        fields = ["name", "active"]


class CollegeCategoryForm(forms.ModelForm):
    class Meta:
        model = CollegeCategory
        fields = ["name", "active"]


class CollegeOptionForm(forms.ModelForm):
    aliases = forms.CharField(
        required=False,
        label="Common search names (comma-separated)",
        help_text="Optional names students may commonly use.",
    )

    class Meta:
        model = CollegeOption
        fields = [
            "name",
            "location",
            "address",
            "state",
            "district",
            "category",
            "affiliation",
            "aliases",
            "source_url",
            "active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["district"].queryset = CollegeDistrict.objects.all()
        self.fields["category"].queryset = CollegeCategory.objects.all()
        if not self.is_bound:
            self.initial["aliases"] = ", ".join(self.instance.aliases or [])

    def clean_aliases(self):
        aliases = self.cleaned_data["aliases"]
        unique_aliases = {}
        for alias in re.split(r"[,;\n]", aliases):
            alias = " ".join(alias.split())
            if alias:
                unique_aliases.setdefault(alias.casefold(), alias)
        return list(unique_aliases.values())


class OfferProgramSettingsForm(forms.ModelForm):
    start_month = forms.DateField(
        required=False,
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "month"}),
    )
    end_month = forms.DateField(
        required=False,
        input_formats=["%Y-%m", "%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "month"}),
    )

    class Meta:
        model = OfferProgramSettings
        fields = ["start_month", "end_month"]

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_month")
        end = cleaned.get("end_month")
        if bool(start) != bool(end):
            raise ValidationError("Set both program start and end months.")
        if start and end and end < start:
            raise ValidationError("Program end month must not be earlier than its start month.")
        return cleaned


class FormFieldSettingForm(forms.ModelForm):
    class Meta:
        model = FormFieldSetting
        exclude = ["form_type", "key"]

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("enabled") and self.instance.key in ESSENTIAL_FIELDS:
            raise ValidationError("This required system field cannot be disabled.")
        if not cleaned.get("required") and self.instance.key in ESSENTIAL_FIELDS:
            raise ValidationError("This required system field cannot be made optional.")
        minimum = cleaned.get("min_length")
        maximum = cleaned.get("max_length")
        if minimum and maximum and maximum < minimum:
            raise ValidationError("Maximum length must be at least the minimum length.")
        return cleaned


class CustomFieldForm(forms.ModelForm):
    class Meta:
        model = FormFieldSetting
        fields = [
            "form_type",
            "key",
            "label",
            "placeholder",
            "enabled",
            "required",
            "display_order",
            "min_length",
            "max_length",
            "character_policy",
            "validation_message",
        ]

    def clean_key(self):
        key = self.cleaned_data["key"].lower()
        if not key.startswith("custom_"):
            key = "custom_" + key
        return key

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_custom = True
        if commit:
            instance.save()
        return instance


class CertificateTemplateUploadForm(forms.ModelForm):
    class Meta:
        model = CertificateTemplate
        fields = ["category", "document", "pdf_source"]

    def clean_document(self):
        upload = self.cleaned_data["document"]
        if upload.size > 10 * 1024 * 1024:
            raise ValidationError("DOCX templates must be 10 MB or smaller.")
        if not upload.name.lower().endswith(".docx"):
            raise ValidationError("Upload an editable DOCX template.")
        return upload

    def clean_pdf_source(self):
        upload = self.cleaned_data.get("pdf_source")
        if upload:
            if upload.size > 10 * 1024 * 1024:
                raise ValidationError("PDF templates must be 10 MB or smaller.")
            if not upload.name.lower().endswith(".pdf"):
                raise ValidationError("Only PDF source files are allowed.")
        return upload

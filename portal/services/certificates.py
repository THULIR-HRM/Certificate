import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf as fitz
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from django.utils import timezone

from portal.models import CertificateRequest, CertificateTemplate, OfferProgramSettings


SOURCE_FILES = {
    CertificateTemplate.Category.OFFER: "Vaishnavi N - Offer Letter (1).docx",
    CertificateTemplate.Category.EXPERIENCE: "Mr. Kishore Kumar L -  EC.docx",
    CertificateTemplate.Category.PROJECT: "Ms. Renuka B M - PC.docx",
    CertificateTemplate.Category.LOR: "Mr. Kishore Kumar L - LOR .docx",
}

SAMPLE_DATES = {
    CertificateTemplate.Category.OFFER: ("05", "th", "October", "2026"),
    CertificateTemplate.Category.EXPERIENCE: ("05", "th", "October", "2026"),
    CertificateTemplate.Category.PROJECT: ("30", "th", "September", "2026"),
    CertificateTemplate.Category.LOR: ("05", "th", "October", "2026"),
}


class DocumentGenerationError(Exception):
    pass


def _ordinal_suffix(day):
    if 11 <= day % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _date_parts(value):
    return str(value.day), _ordinal_suffix(value.day), value.strftime("%B"), str(value.year)


def _short_name(name):
    tokens = name.split()
    if len(tokens) > 1 and len(tokens[1]) > 1:
        return " ".join(tokens[:2])
    return tokens[0]


def _certificate_title(submission):
    return "Mr." if submission.gender == "male" else "Ms."


def _pronouns(submission):
    if submission.gender == "male":
        return {
            "subject": "He",
            "subject_lower": "he",
            "object": "him",
            "possessive": "His",
            "possessive_lower": "his",
            "hers": "his",
        }
    return {
        "subject": "She",
        "subject_lower": "she",
        "object": "her",
        "possessive": "Her",
        "possessive_lower": "her",
        "hers": "hers",
    }


def _month_label(value):
    return value.strftime("%B %Y")


def _offer_program_range():
    configuration = OfferProgramSettings.objects.filter(singleton=True).first()
    if not configuration or not configuration.start_month or not configuration.end_month:
        raise DocumentGenerationError(
            "Set the Offer Letter program start and end months in Admin Settings before generation."
        )
    start = configuration.start_month
    end = configuration.end_month
    if end < start:
        raise DocumentGenerationError("The configured Offer Letter program period is invalid.")
    count = (end.year - start.year) * 12 + end.month - start.month + 1
    unit = "month" if count == 1 else "months"
    return f"{count} {unit} from {_month_label(start)} to {_month_label(end)}"


NON_TECHNICAL_EXPANSIONS = {
    # Human Resources
    "HRM": "Human Resource Management",
    "HR": "Human Resource Management",
    "HRD": "Human Resource Development",
    "TA": "Talent Acquisition",
    # Marketing & PR
    "DM": "Digital Marketing",
    "SMM": "Social Media Marketing",
    "SMO": "Social Media Optimization",
    "SEO": "Search Engine Optimization",
    "SEM": "Search Engine Marketing",
    "PR": "Public Relations",
    "CM": "Content Marketing",
    "EM": "Email Marketing",
    "IM": "Inbound Marketing",
    "PMM": "Product Marketing Management",
    # Business Development & Sales
    "BD": "Business Development",
    "BDE": "Business Development",
    "BDA": "Business Development",
    "B2B": "Business to Business",
    "B2C": "Business to Consumer",
    "AM": "Account Management",
    "KAM": "Key Account Management",
    "CRM": "Customer Relationship Management",
    # Content, Media & Design
    "CW": "Content Writing",
    "GD": "Graphic Designing",
    "GDD": "Graphic Design",
    "CD": "Creative Design",
    "VD": "Video Editing",
    "MG": "Motion Graphics",
    "TW": "Technical Writing",
    "UI/UX": "UI/UX Design",
    "UI": "User Interface Design",
    "UX": "User Experience Design",
    # Finance, Banking & Accounting
    "FA": "Financial Analysis",
    "FM": "Financial Management",
    "IB": "Investment Banking",
    "CF": "Corporate Finance",
    "WM": "Wealth Management",
    "AF": "Accounting and Finance",
    "CA": "Chartered Accountancy",
    # Operations & Management
    "SCM": "Supply Chain Management",
    "OM": "Operations Management",
    "LM": "Logistics Management",
    "PM": "Project Management",
    "PO": "Product Operations",
    "BPO": "Business Process Outsourcing",
    "KPO": "Knowledge Process Outsourcing",
    "BA": "Business Analysis",
    "BBA": "Business Administration",
    "MBA": "Business Administration",
    # Healthcare & Support
    "MC": "Medical Coding",
    "MT": "Medical Transcription",
    "HA": "Hospital Administration",
    "HM": "Healthcare Management",
    "CS": "Customer Support",
    "CX": "Customer Experience",
}

TECHNICAL_EXPANSIONS = {
    "ML": "Machine Learning",
    "DS": "Data Science",
    "DA": "Data Analytics",
    "AI": "Artificial Intelligence",
    "GENAI": "Generative AI",
    "GEN AI": "Generative AI",
    "DL": "Deep Learning",
    "NLP": "Natural Language Processing",
    "CV": "Computer Vision",
    "LLM": "Large Language Models",
    "FSD": "Full Stack Development",
    "FSD JAVA": "Full Stack Development – Java",
    "FSD PYTHON": "Full Stack Development – Python",
}


def _clean_domain_name(name):
    if not name:
        return ""
    cleaned = " ".join(str(name).strip().split())
    match = re.match(r"^(.*?)\s*\((.*?)\)$", cleaned)
    if match:
        part1 = match.group(1).strip()
        part2 = match.group(2).strip()
        if len(part1.split()) > 1 and len(part2.split()) == 1:
            return part1
        elif len(part2.split()) > 1 and len(part1.split()) == 1:
            return part2
        return part1
    return cleaned


def _is_technical_domain(domain_obj_or_name):
    if hasattr(domain_obj_or_name, "name"):
        raw_name = domain_obj_or_name.name
        is_tech_attr = getattr(domain_obj_or_name, "is_technical", None)
    else:
        raw_name = str(domain_obj_or_name)
        is_tech_attr = None

    name = _clean_domain_name(raw_name).upper()
    name_lower = name.lower()

    if "medical coding" in name_lower or "medical transcription" in name_lower:
        return False

    if name in NON_TECHNICAL_EXPANSIONS:
        return False

    non_tech_keywords = [
        "human resource", "human resources", "talent acquisition", "recruitment", "staffing",
        "digital marketing", "social media", "marketing", "advertising", "branding", "public relations",
        "business development", "sales", "client relations", "account management",
        "content writing", "copywriting", "creative writing", "blogging", "technical writing",
        "graphic design", "graphic designing", "visual design", "video editing", "motion graphics",
        "finance", "financial", "accounting", "auditing", "taxation", "banking",
        "supply chain", "logistics", "operations management",
        "hospital administration", "healthcare management", "hospitality",
        "customer support", "customer service", "customer success",
    ]
    for kw in non_tech_keywords:
        if kw in name_lower:
            return False

    if name in TECHNICAL_EXPANSIONS:
        return True

    tech_keywords = [
        "machine learning", "data science", "data analytics", "data analyst", "data engineer",
        "artificial intelligence", "generative ai", "deep learning", "nlp", "computer vision",
        "software", "developer", "development", "programming", "programmer",
        "full stack", "fullstack", "frontend", "backend", "fsd",
        "java", "python", "c++", "c#", ".net", "php", "golang", "rust", "ruby", "javascript", "typescript", "node", "react", "angular",
        "web dev", "app dev", "mobile app", "android", "ios", "flutter",
        "cyber security", "cybersecurity", "infosec", "cloud", "devops", "docker", "kubernetes",
        "blockchain", "web3", "robotics", "embedded", "iot", "testing", "quality assurance", "database", "sql",
    ]
    for kw in tech_keywords:
        if kw in name_lower:
            return True

    if is_tech_attr is not None:
        return bool(is_tech_attr)
    return True


def _expand_non_technical_domain(domain_name):
    cleaned = _clean_domain_name(domain_name)
    cleaned = re.sub(r"\s+internship\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    upper = cleaned.upper()
    if upper in NON_TECHNICAL_EXPANSIONS:
        return NON_TECHNICAL_EXPANSIONS[upper]
    for abbr, full in NON_TECHNICAL_EXPANSIONS.items():
        pattern = r"\b" + re.escape(abbr) + r"\b"
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            return re.sub(pattern, full, cleaned, flags=re.IGNORECASE)
    return cleaned


def _display_domain_name(submission):
    domain = submission.domain
    raw_name = domain.name if hasattr(domain, "name") else str(domain)
    if _is_technical_domain(domain):
        cleaned = _clean_domain_name(raw_name)
        return TECHNICAL_EXPANSIONS.get(cleaned.upper(), cleaned)
    return _expand_non_technical_domain(raw_name)


def _offer_internship_designation(submission):
    """Return the correct internship designation for the offer letter and certificates.

    Technical domain  → "Technical Internship"
    Non-technical     → "<Expanded Domain Name> Internship"
    """
    if _is_technical_domain(submission.domain):
        return "Technical Internship"
    expanded = _expand_non_technical_domain(submission.domain.name)
    return f"{expanded} Internship"


def _field_values(request, issue_date):
    submission = request.submission
    pronouns = _pronouns(submission)
    designation = _offer_internship_designation(submission)
    return {
        "STUDENT_TITLE": _certificate_title(submission),
        "STUDENT_NAME": submission.name,
        "STUDENT_FIRST_NAME": submission.name.split()[0],
        "GENDER": submission.get_gender_display(),
        "PRONOUN_SUBJECT": pronouns["subject"],
        "PRONOUN_OBJECT": pronouns["object"],
        "PRONOUN_POSSESSIVE": pronouns["possessive"],
        "PRONOUN_POSSESSIVE_DETERMINER": pronouns["possessive_lower"],
        "MOBILE_NUMBER": submission.mobile,
        "UNIVERSITY_REGISTRATION_NUMBER": submission.registration_number,
        "REGISTERED_GMAIL": submission.registered_email,
        "ALTERNATIVE_GMAIL": submission.alternative_email,
        "DEGREE_DEPARTMENT": submission.degree_department,
        "COLLEGE_NAME": submission.college_name,
        "COLLEGE_LOCATION": submission.college_location,
        "DOMAIN": _display_domain_name(submission),
        "INTERNSHIP_DESIGNATION": designation,
        "INTERNSHIP_TITLE": designation,
        "DESIGNATION": designation,
        "INTERNSHIP_START_DATE": _month_label(submission.internship_start)
        if submission.internship_start
        else "",
        "INTERNSHIP_END_DATE": _month_label(submission.internship_end)
        if submission.internship_end
        else "",
        "INTERNSHIP_DURATION": submission.duration_label,
        "PROJECT_TITLE": submission.project_title,
        "STUDENT_FEEDBACK": submission.feedback,
        "CERTIFICATE_DATE": issue_date.strftime("%d %B %Y"),
        "OFFER_PROGRAM_DURATION": _offer_program_range()
        if request.category == CertificateRequest.Category.OFFER
        else "",
    }


def _paragraphs(document):
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    yield paragraph
    for section in document.sections:
        for story in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            for paragraph in story.paragraphs:
                yield paragraph
            for table in story.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            yield paragraph


def _replace_in_paragraph(paragraph, old, new, limit=None):
    if not old:
        return 0
    text_nodes = [
        node
        for run in paragraph.runs
        for node in run._r.findall(qn("w:t"))
    ]
    full_text = "".join(node.text or "" for node in text_nodes)
    matches = list(re.finditer(re.escape(old), full_text))
    if limit is not None:
        matches = matches[:limit]
    for match in reversed(matches):
        offset = 0
        overlaps = []
        for node in text_nodes:
            text = node.text or ""
            node_end = offset + len(text)
            overlap_start = max(match.start(), offset)
            overlap_end = min(match.end(), node_end)
            if overlap_start < overlap_end:
                overlaps.append(
                    (node, overlap_start - offset, overlap_end - offset)
                )
            offset = node_end

        for index, (node, start, end) in enumerate(overlaps):
            text = node.text or ""
            before = text[:start] if index == 0 else ""
            after = text[end:] if index == len(overlaps) - 1 else ""
            replacement = new if index == 0 else ""
            updated = before + replacement + after
            node.text = updated
            if updated.startswith(" ") or updated.endswith(" "):
                node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return len(matches)


def _replace_document_text(document, old, new, minimum=1):
    if old == new:
        return 0
    count = sum(_replace_in_paragraph(paragraph, old, new) for paragraph in _paragraphs(document))
    if count < minimum:
        raise DocumentGenerationError(
            f"Template content could not be matched for a required field ({old!r})."
        )
    return count


def _replace_date(document, category, issue_date):
    old_day, old_suffix, old_month, old_year = SAMPLE_DATES[category]
    new_day, new_suffix, new_month, new_year = _date_parts(issue_date)
    target = f"{old_month} {old_year}"
    for paragraph in _paragraphs(document):
        if target not in paragraph.text or old_day not in paragraph.text:
            continue
        for old, new in (
            (old_day, new_day),
            (old_suffix, new_suffix),
            (old_month, new_month),
            (old_year, new_year),
        ):
            if _replace_in_paragraph(paragraph, old, new, limit=1) != 1:
                raise DocumentGenerationError("The certificate date could not be updated safely.")
        return
    raise DocumentGenerationError("The certificate date was not found in the selected template.")


def _source_replacements(request, issue_date):
    submission = request.submission
    title = _certificate_title(submission)
    name = submission.name
    values = []

    if request.category == CertificateRequest.Category.OFFER:
        values = [
            ("Ms. Vaishnavi N", f"{title} {name}"),
            ("113824243010", submission.registration_number),
            ("Sri Jayaram Institute of Engineering and Technology", submission.college_name),
            ("Elavur", submission.college_location),
            ("Vaishnavi", submission.name.split()[0]),
            (
                "3 months from October 2026 to December 2026",
                _offer_program_range(),
            ),
            ("Technical Internship", _offer_internship_designation(submission)),
        ]
    elif request.category == CertificateRequest.Category.EXPERIENCE:
        values = [
            ("Mr. Kishore Kumar L", f"{title} {name}"),
            ("44734015", submission.registration_number),
            ("M.Sc., (Computer Science)", submission.degree_department),
            ("Sathyabama University", submission.college_name),
            ("Chennai", submission.college_location),
            ("May 2026 to July 2026", submission.duration_label),
            ("Data Science", _display_domain_name(submission)),
        ]
    elif request.category == CertificateRequest.Category.PROJECT:
        values = [
            ("Ms. Renuka B M", f"{title} {name}"),
            ("122311510105", submission.registration_number),
            ("B.Tech (Computer Science and Engineering)", submission.degree_department),
            (
                "The Apollo University, Chittoor",
                f"{submission.college_name}, {submission.college_location}",
            ),
            (
                "AI Supply Chain  Disruption Predictor using News NLP Weather Data",
                submission.project_title,
            ),
            ("May 2026 to July 2026", submission.duration_label),
            ("Machine Learning", _display_domain_name(submission)),
        ]
    elif request.category == CertificateRequest.Category.LOR:
        values = [
            ("Mr. Kishore Kumar L", f"{title} {name}"),
            ("Kishore Kumar", _short_name(name)),
            ("May 2026 to July 2026", submission.duration_label),
        ]
    else:
        raise DocumentGenerationError("Unsupported certificate category.")

    return values


def _gender_replacements(category, gender):
    if gender == "male":
        if category == CertificateRequest.Category.PROJECT:
            return [
                ("her contributions", "his contributions"),
                ("she demonstrated", "he demonstrated"),
                ("wish her continued", "wish him continued"),
                ("wish her the best", "wish him the best"),
            ]
        return []

    if category == CertificateRequest.Category.EXPERIENCE:
        return [
            ("found him to be", "found her to be"),
            ("Hard-working. He", "Hard-working. She"),
            ("on his assignments", "on her assignments"),
            ("his performance", "her performance"),
            ("him continued", "her continued"),
            ("him the best", "her the best"),
        ]
    if category == CertificateRequest.Category.LOR:
        return [
            ("his behalf", "her behalf"),
            ("his decision", "her decision"),
            ("he has consistently", "she has consistently"),
            ("He is an excellent", "She is an excellent"),
            ("He has been exposed", "She has been exposed"),
            ("his technical skills", "her technical skills"),
            ("his recommendation", "her recommendation"),
        ]
    return []


def _replace_source_template(document, request, issue_date):
    _replace_date(document, request.category, issue_date)
    for old, new in _source_replacements(request, issue_date):
        _replace_document_text(document, old, new)
    for old, new in _gender_replacements(request.category, request.submission.gender):
        if old != new:
            _replace_document_text(document, old, new)
    if request.category == CertificateRequest.Category.OFFER:
        _left_align_offer_bullets(document)


def _left_align_offer_bullets(document):
    bullet_text = (
        "Certificate of Completion",
        "Letter of Recommendation",
        "Recognition & Rewards for Top Performers",
    )
    paragraphs = list(_paragraphs(document))
    for text in bullet_text:
        matches = [paragraph for paragraph in paragraphs if text in paragraph.text]
        if len(matches) != 1:
            raise DocumentGenerationError(
                f"The Offer Letter bullet paragraph could not be matched uniquely for {text!r}."
            )
        matches[0].alignment = WD_ALIGN_PARAGRAPH.LEFT


def _replace_placeholders(document, request, issue_date):
    values = _field_values(request, issue_date)
    found = False
    for key, value in values.items():
        token = "{{" + key + "}}"
        if any(token in paragraph.text for paragraph in _paragraphs(document)):
            found = True
            _replace_document_text(document, token, str(value))
    unresolved = [
        match
        for paragraph in _paragraphs(document)
        for match in re.findall(r"\{\{[^}]+\}\}", paragraph.text)
    ]
    if unresolved:
        raise DocumentGenerationError(
            "The template contains unsupported placeholders: " + ", ".join(sorted(set(unresolved)))
        )
    if not found:
        raise DocumentGenerationError(
            "The active template is not one of the imported source templates and has no supported placeholders."
        )


def _remove_element(element):
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _clear_story(story):
    for paragraph in story.paragraphs:
        paragraph.clear()
    for table in story.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.clear()
    for element in list(story._element.xpath(".//w:drawing | .//w:pict")):
        _remove_element(element)


def _make_docx_offline(document):
    for section in document.sections:
        for story in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            _clear_story(story)

    paragraphs = list(_paragraphs(document))
    for paragraph in paragraphs:
        normalized = paragraph.text.strip()
        if normalized in {"Kanishrajiah Durairaju", "Manager - Human Resource"}:
            _remove_element(paragraph._element)
            continue
        for element in list(paragraph._p.xpath(".//w:drawing | .//w:pict")):
            _remove_element(element)

    for element in list(document.part.element.body.xpath(".//w:drawing | .//w:pict")):
        _remove_element(element)
    _remove_image_relationships(document.part)


def _remove_image_relationships(part, visited=None):
    visited = visited or set()
    part_name = str(part.partname)
    if part_name in visited:
        return
    visited.add(part_name)
    for relationship_id, relationship in list(part.rels.items()):
        if relationship.reltype == RT.IMAGE:
            del part.rels[relationship_id]
        elif not relationship.is_external:
            _remove_image_relationships(relationship.target_part, visited)


def _docx_bytes(template, request, mode):
    if template.original_filename == SOURCE_FILES.get(request.category):
        document = Document(template.document.path)
        issue_date = timezone.localdate()
        _replace_source_template(document, request, issue_date)
    else:
        document = Document(template.document.path)
        issue_date = timezone.localdate()
        _replace_placeholders(document, request, issue_date)
    if mode == "offline":
        _make_docx_offline(document)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _replace_source_template_for_pdf(document, request, issue_date):
    replacements = _source_replacements(request, issue_date)
    replacements.extend(
        _gender_replacements(request.category, request.submission.gender)
    )
    tagged_replacements = [
        (old, f"__GT_PDF_{hashlib.sha256(f'{index}:{old}'.encode()).hexdigest()}__", new)
        for index, (old, new) in enumerate(replacements)
    ]
    _replace_date(document, request.category, issue_date)
    for old, token, _ in tagged_replacements:
        _replace_document_text(document, old, token)
    for _, token, new in tagged_replacements:
        _replace_document_text(document, token, new)
    if request.category == CertificateRequest.Category.OFFER:
        _left_align_offer_bullets(document)


def _docx_bytes_for_pdf(template, request, mode):
    document = Document(template.document.path)
    issue_date = timezone.localdate()
    if template.original_filename == SOURCE_FILES.get(request.category):
        _replace_source_template_for_pdf(document, request, issue_date)
    else:
        _replace_placeholders(document, request, issue_date)
    if mode == "offline":
        _make_docx_offline(document)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _pdf_font(span):
    name = span.get("font", "").lower()
    bold = "bold" in name
    italic = "italic" in name or "oblique" in name
    if "arial" in name:
        return "hebi" if bold and italic else "hebo" if bold else "heit" if italic else "helv"
    return "tibi" if bold and italic else "tibo" if bold else "tiit" if italic else "tiro"


def _text_spans(page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"]:
                    yield span


def _preserved_pdf_font_size(text, fontname, font_size, width, anchor):
    if fitz.get_text_length(text, fontname=fontname, fontsize=font_size) <= width + 0.1:
        return font_size
    raise DocumentGenerationError(
        f"The replacement for {anchor!r} does not fit the fixed PDF template field "
        "at its original font size. "
        "Choose Word format, or configure LibreOffice to generate the PDF from the "
        "Word document so the text can wrap naturally."
    )


def _line_groups(rectangles):
    groups = []
    for rectangle in sorted(rectangles, key=lambda rect: (rect.y0, rect.x0)):
        group = next(
            (candidate for candidate in groups if abs(candidate[0].y0 - rectangle.y0) <= 3),
            None,
        )
        if group is None:
            groups.append([rectangle])
        else:
            group.append(rectangle)
    return groups


def _matching_spans(page, rectangle):
    return [
        span
        for span in _text_spans(page)
        if fitz.Rect(span["bbox"]).intersects(rectangle)
    ]


def _pdf_text_lines(page):
    return [
        line
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        if line.get("spans")
    ]


def _pdf_phrase_pattern(phrase):
    return re.compile(r"\s+".join(re.escape(part) for part in phrase.split()))


def _line_spans_for_rectangles(page, rectangles):
    return [
        line
        for line in _pdf_text_lines(page)
        if any(fitz.Rect(line["bbox"]).intersects(rectangle) for rectangle in rectangles)
    ]


def _reflow_pdf_lines(page, lines, replacements):
    lines = sorted(lines, key=lambda line: line["bbox"][1])
    line_keys = {
        tuple(round(value, 1) for value in line["bbox"])
        for line in lines
    }
    spans = [
        span
        for line in lines
        for span in sorted(line["spans"], key=lambda item: item["bbox"][0])
        if span["text"]
    ]
    if not spans:
        return set(), None

    text = ""
    styles = []
    previous_span = None
    for span in spans:
        span_text = span["text"]
        new_line = (
            previous_span is not None
            and abs(span["bbox"][1] - previous_span["bbox"][1]) > 3
        )
        if (
            previous_span
            and not text[-1:].isspace()
            and not span_text[:1].isspace()
            and (
                new_line
                or span["bbox"][0] - previous_span["bbox"][2] > 1.5
            )
        ):
            text += " "
            styles.append(previous_span)
        text += span_text
        styles.extend([span] * len(span_text))
        previous_span = span

    candidates = []
    for order, (old, new) in enumerate(replacements):
        if old == new:
            continue
        candidates.extend(
            (match.start(), match.end(), old, new, order)
            for match in _pdf_phrase_pattern(old).finditer(text)
        )
    selected = []
    for candidate in sorted(candidates, key=lambda item: (item[0], item[4])):
        if selected and candidate[0] < selected[-1][1]:
            continue
        selected.append(candidate)
    if not selected:
        return set(), None
    handled = {(old, new) for _, _, old, new, _ in selected}
    for start, end, old, new, _ in reversed(selected):
        style = styles[start]
        text = text[:start] + new + text[end:]
        styles[start:end] = [style] * len(new)

    words = []
    for match in re.finditer(r"\S+", text):
        word = match.group()
        style = styles[match.start()]
        fontname = _pdf_font(style)
        words.append(
            (
                word,
                style,
                fontname,
                style["size"],
                fitz.get_text_length(word, fontname=fontname, fontsize=style["size"]),
            )
        )
    if not words:
        return set(), None

    line_start = min(span["bbox"][0] for span in spans)
    right_edge = page.rect.width - 50
    available_width = right_edge - line_start
    wrapped_lines = []
    current_line = []
    current_width = 0.0
    for item in words:
        word, style, fontname, size, width = item
        space_width = (
            fitz.get_text_length(" ", fontname=fontname, fontsize=size)
            if current_line
            else 0
        )
        if current_line and current_width + space_width + width > available_width:
            wrapped_lines.append(current_line)
            current_line = []
            current_width = 0.0
            space_width = 0
        if width > available_width:
            anchor = selected[0][2]
            raise DocumentGenerationError(
                f"The replacement for {anchor!r} contains a word wider than the PDF text area."
            )
        current_line.append((item, space_width))
        current_width += space_width + width
    if current_line:
        wrapped_lines.append(current_line)

    first_line_y = min(line["bbox"][1] for line in lines)
    baseline = min(
        span.get("origin", (span["bbox"][0], span["bbox"][3] - 2))[1]
        for span in spans
    )
    line_advance = max(span["size"] for span in spans) * 1.1
    line_bottom = max(span["bbox"][3] - span["origin"][1] for span in spans)
    next_lines = [
        line
        for line in _pdf_text_lines(page)
        if tuple(round(value, 1) for value in line["bbox"]) not in line_keys
        and line["bbox"][1] >= first_line_y + 3
    ]
    if next_lines and len(wrapped_lines) > 1:
        next_top = min(line["bbox"][1] for line in next_lines)
        last_bottom = baseline + (len(wrapped_lines) - 1) * line_advance + line_bottom
        if last_bottom >= next_top:
            anchor = selected[0][2]
            raise DocumentGenerationError(
                f"The replacement for {anchor!r} needs more vertical room than the "
                "original PDF layout provides. Use a matching PDF template with "
                "more space or generate the PDF from the Word document."
            )

    redactions = [fitz.Rect(span["bbox"]) for span in spans]
    for rectangle in redactions:
        page.add_redact_annot(rectangle, fill=(1, 1, 1), cross_out=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )

    justified = max(span["bbox"][2] for span in spans) >= page.rect.width - 80
    for line_index, line_words in enumerate(wrapped_lines):
        natural_width = sum(item[0][4] + item[1] for item in line_words)
        distribute = (
            max(0, len(line_words) - 1)
            if justified and line_index < len(wrapped_lines) - 1
            else 0
        )
        extra_space = (
            max(0, (available_width - natural_width) / distribute)
            if distribute
            else 0
        )
        x = line_start
        y = baseline + line_index * line_advance
        for word_index, (item, space_width) in enumerate(line_words):
            word, style, fontname, size, width = item
            if word_index:
                x += space_width + extra_space
            color_value = style.get("color", 0)
            color = (
                ((color_value >> 16) & 255) / 255,
                ((color_value >> 8) & 255) / 255,
                (color_value & 255) / 255,
            )
            page.insert_text((x, y), word, fontname=fontname, fontsize=size, color=color)
            x += width
    reflow_area = fitz.Rect(
        0,
        min(line["bbox"][1] for line in lines),
        page.rect.width,
        max(line["bbox"][3] for line in lines),
    )
    return handled, reflow_area


def _reflow_pdf_overflowing_fields(page, replacements):
    components = []
    for old, new in replacements:
        if old == new:
            continue
        rectangles = page.search_for(old)
        for group in _line_groups(rectangles):
            anchor = fitz.Rect(group[0])
            for rectangle in group[1:]:
                anchor |= rectangle
            spans = _matching_spans(page, anchor)
            if not spans:
                continue
            span = min(spans, key=lambda item: item["bbox"][0])
            if fitz.get_text_length(
                new,
                fontname=_pdf_font(span),
                fontsize=span["size"],
            ) <= _pdf_field_width(page, anchor, span, old) + 0.1:
                continue
            lines = _line_spans_for_rectangles(page, rectangles)
            if not lines:
                continue
            keys = {tuple(round(value, 1) for value in line["bbox"]) for line in lines}
            overlapping = [item for item in components if item["keys"] & keys]
            component = {
                "keys": set(keys),
                "lines": {
                    tuple(round(value, 1) for value in line["bbox"]): line
                    for line in lines
                },
            }
            for item in overlapping:
                component["keys"].update(item["keys"])
                component["lines"].update(item["lines"])
                components.remove(item)
            components.append(component)

    handled = set()
    reflow_areas = []
    for component in components:
        lines_by_key = {
            tuple(round(value, 1) for value in line["bbox"]): line
            for line in _pdf_text_lines(page)
        }
        lines = [
            lines_by_key[key]
            for key in component["keys"]
            if key in lines_by_key
        ]
        line_rectangles = [fitz.Rect(line["bbox"]) for line in lines]
        line_replacements = [
            (old, new)
            for old, new in replacements
            if old != new
            and any(
                rectangle.intersects(line_rectangle)
                for rectangle in page.search_for(old)
                for line_rectangle in line_rectangles
            )
        ]
        component_handled, reflow_area = _reflow_pdf_lines(
            page,
            lines,
            line_replacements,
        )
        handled.update(component_handled)
        if reflow_area is not None:
            reflow_areas.append(reflow_area)
    return handled, reflow_areas


def _pdf_field_width(page, rectangle, span, old):
    span_rectangle = fitz.Rect(span["bbox"])
    if rectangle.x1 < span_rectangle.x1 - 1 and not span["text"].rstrip().endswith(old):
        return rectangle.width

    baseline = span.get("origin", (rectangle.x0, rectangle.y1 - 2))[1]
    next_spans = [
        candidate
        for candidate in _text_spans(page)
        if abs(candidate.get("origin", (0, 0))[1] - baseline) <= 3
        and fitz.Rect(candidate["bbox"]).x0 >= rectangle.x1 - 0.5
    ]
    right_edge = (
        min(fitz.Rect(candidate["bbox"]).x0 for candidate in next_spans) - 2
        if next_spans
        else page.rect.width - 50
    )
    return max(rectangle.width, right_edge - rectangle.x0)


def _replace_pdf_phrase(page, old, new, max_y=700, exclude_rectangles=()):
    if old == new:
        return 0
    found_rectangles = [
        rectangle
        for rectangle in page.search_for(old)
        if rectangle.y0 < max_y
    ]
    if not found_rectangles:
        raise DocumentGenerationError(f"PDF template text could not be matched for {old!r}.")
    rectangles = [
        rectangle
        for rectangle in found_rectangles
        if not any(rectangle.intersects(area) for area in exclude_rectangles)
    ]
    if not rectangles:
        return 0

    line_groups = _line_groups(rectangles)
    clusters = []
    for group in line_groups:
        if not clusters or group[0].y0 - clusters[-1][-1][0].y0 > 40:
            clusters.append([group])
        else:
            clusters[-1].append(group)

    replacements = []
    redactions = []
    for cluster in clusters:
        group_rectangles = []
        for group in cluster:
            rectangle = fitz.Rect(group[0])
            for part in group[1:]:
                rectangle |= part
            group_rectangles.append(rectangle)
        anchor = group_rectangles[0]
        spans = _matching_spans(page, anchor)
        if not spans:
            raise DocumentGenerationError("The PDF field font could not be identified.")
        span = min(spans, key=lambda item: item["bbox"][0])
        fontname = _pdf_font(span)
        field_width = _pdf_field_width(page, anchor, span, old)
        if fitz.get_text_length(new, fontname=fontname, fontsize=span["size"]) > field_width + 0.1:
            lines = _line_spans_for_rectangles(page, group_rectangles)
            if lines:
                handled, reflow_area = _reflow_pdf_lines(
                    page,
                    lines,
                    [(old, new)],
                )
                if handled:
                    if isinstance(exclude_rectangles, list) and reflow_area is not None:
                        exclude_rectangles.append(reflow_area)
                    continue
        size = _preserved_pdf_font_size(
            new,
            fontname,
            span["size"],
            field_width,
            old,
        )
        baseline = span.get("origin", (anchor.x0, anchor.y1 - 2))[1]
        replacements.append((new, fontname, size, (anchor.x0, baseline)))
        redactions.extend(group_rectangles)

    for rectangle in redactions:
        page.add_redact_annot(rectangle, fill=(1, 1, 1), cross_out=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )
    for text, fontname, size, origin in replacements:
        page.insert_text(origin, text, fontname=fontname, fontsize=size, color=(0, 0, 0))
    return len(replacements)


def _replace_pdf_issue_date(page, category, issue_date):
    old_day, old_suffix, old_month, old_year = SAMPLE_DATES[category]
    old_date = f"{old_day}{old_suffix} {old_month} {old_year}"
    rectangles = page.search_for(old_date)
    if not rectangles:
        raise DocumentGenerationError("The issue date was not found in the PDF template.")

    rectangle = fitz.Rect(rectangles[0])
    for part in rectangles[1:]:
        rectangle |= part
    spans = _matching_spans(page, rectangle)
    if not spans:
        raise DocumentGenerationError("The issue date font could not be identified.")
    normal_span = min(spans, key=lambda item: item["bbox"][0])
    bold = any("bold" in span.get("font", "").lower() for span in spans)
    fontname = "tibo" if bold else "tiro"
    small_font = "tibo" if bold else "tiro"
    size = normal_span["size"]
    day, suffix, month, year = _date_parts(issue_date)
    month_year = f"{month} {year}"
    day_width = fitz.get_text_length(day, fontname=fontname, fontsize=size)
    suffix_size = max(6.5, size * 0.6)
    suffix_width = fitz.get_text_length(suffix, fontname=small_font, fontsize=suffix_size)
    month_width = fitz.get_text_length(month_year, fontname=fontname, fontsize=size)
    right_edge = max(part.x1 for part in rectangles)
    x = right_edge - day_width - suffix_width - month_width
    baseline = normal_span.get("origin", (rectangle.x0, rectangle.y1 - 2))[1]

    page.add_redact_annot(rectangle, fill=(1, 1, 1), cross_out=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )
    page.insert_text((x, baseline), day, fontname=fontname, fontsize=size, color=(0, 0, 0))
    page.insert_text(
        (x + day_width, baseline - size * 0.42),
        suffix,
        fontname=small_font,
        fontsize=suffix_size,
        color=(0, 0, 0),
    )
    page.insert_text(
        (x + day_width + suffix_width, baseline),
        month_year,
        fontname=fontname,
        fontsize=size,
        color=(0, 0, 0),
    )


def _replace_pdf_placeholders(page, request, issue_date):
    values = _field_values(request, issue_date)
    found = False
    replacements = []
    for key, value in values.items():
        token = "{{" + key + "}}"
        if page.search_for(token):
            found = True
            replacements.append((token, str(value)))
    if not found:
        raise DocumentGenerationError(
            "The PDF source is not one of the imported templates and has no supported placeholders."
        )
    handled, reflow_areas = _reflow_pdf_overflowing_fields(page, replacements)
    for old, new in replacements:
        if (old, new) in handled and not page.search_for(old):
            continue
        _replace_pdf_phrase(page, old, new, exclude_rectangles=reflow_areas)


def _replace_pdf_source_fields(page, request, issue_date):
    category = request.category
    submission = request.submission
    title = _certificate_title(submission)

    _replace_pdf_issue_date(page, category, issue_date)
    if category == CertificateRequest.Category.OFFER:
        changes = [
            ("Ms. Vaishnavi N", f"{title} {submission.name}"),
            ("113824243010", submission.registration_number),
            ("Sri Jayaram Institute of Engineering and Technology", submission.college_name),
            ("Elavur.", f"{submission.college_location}."),
            ("Vaishnavi,", f"{submission.name.split()[0]},"),
            ("3 months from October 2026 to December 2026", _offer_program_range()),
            ("Technical Internship", _offer_internship_designation(submission)),
        ]
    elif category == CertificateRequest.Category.EXPERIENCE:
        changes = [
            ("Mr. Kishore Kumar L", f"{title} {submission.name}"),
            ("44734015", submission.registration_number),
            ("M.Sc., (Computer Science)", submission.degree_department),
            ("Sathyabama University", submission.college_name),
            ("Chennai", submission.college_location),
            ("May 2026 to July 2026", submission.duration_label),
            ("Data Science", _display_domain_name(submission)),
        ]
    elif category == CertificateRequest.Category.PROJECT:
        changes = [
            ("Ms. Renuka B M", f"{title} {submission.name}"),
            ("122311510105", submission.registration_number),
            ("B.Tech (Computer Science and Engineering)", submission.degree_department),
            (
                "The Apollo University, Chittoor",
                f"{submission.college_name}, {submission.college_location}",
            ),
            (
                "AI Supply Chain Disruption Predictor using News NLP Weather Data",
                submission.project_title,
            ),
            ("May 2026 to July 2026", submission.duration_label),
            ("Machine Learning", _display_domain_name(submission)),
        ]
    elif category == CertificateRequest.Category.LOR:
        changes = [
            ("Mr. Kishore Kumar L", f"{title} {submission.name}"),
            ("Kishore Kumar", _short_name(submission.name)),
            ("May 2026 to July 2026", submission.duration_label),
        ]
    else:
        raise DocumentGenerationError("Unsupported certificate category.")

    changes.extend(_gender_replacements(category, submission.gender))
    handled, reflow_areas = _reflow_pdf_overflowing_fields(page, changes)
    for old, new in changes:
        if (old, new) in handled and not page.search_for(old):
            continue
        _replace_pdf_phrase(page, old, new, exclude_rectangles=reflow_areas)


def _offline_pdf(online_pdf):
    source_document = fitz.open(stream=online_pdf, filetype="pdf")
    output_document = fitz.open()
    for source_page in source_document:
        output_page = output_document.new_page(
            width=source_page.rect.width,
            height=source_page.rect.height,
        )
        for span in _text_spans(source_page):
            text = span["text"]
            box = fitz.Rect(span["bbox"])
            if (
                box.y1 >= 700
                or box.x0 >= 535
                or "Kanishrajiah Durairaju" in text
                or "Manager - Human Resource" in text
                or "For Gradtwin Services" in text
            ):
                continue
            fontname = _pdf_font(span)
            origin = span.get("origin", (box.x0, box.y1 - 2))
            color_value = span.get("color", 0)
            color = (
                ((color_value >> 16) & 255) / 255,
                ((color_value >> 8) & 255) / 255,
                (color_value & 255) / 255,
            )
            output_page.insert_text(
                origin,
                text,
                fontname=fontname,
                fontsize=span["size"],
                color=color,
            )
    data = output_document.tobytes(garbage=4, deflate=True)
    output_document.close()
    source_document.close()
    return data


def _convert_docx_with_libreoffice(docx_data):
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if not executable and os.name == "nt":
        install_directories = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
        ]
        executable = next(
            (
                str(candidate)
                for directory in install_directories
                if directory
                for name in ("soffice.com", "soffice.exe")
                if (candidate := Path(directory) / "LibreOffice" / "program" / name).is_file()
            ),
            None,
        )
    if not executable:
        return None
    with tempfile.TemporaryDirectory(prefix="gradtwin-docx-") as temp_dir:
        temp_path = Path(temp_dir)
        source = temp_path / "certificate.docx"
        source.write_bytes(docx_data)
        result = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temp_path),
                str(source),
            ],
            capture_output=True,
            timeout=45,
            check=False,
        )
        output = temp_path / "certificate.pdf"
        if result.returncode != 0 or not output.is_file():
            diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
            raise DocumentGenerationError(
                "DOCX-to-PDF conversion failed"
                + (f": {diagnostic}" if diagnostic else ".")
            )
        return output.read_bytes()


def generate_certificate_bytes(request, template, mode, file_format):
    if request.status not in {
        CertificateRequest.Status.APPROVED,
        CertificateRequest.Status.GENERATED,
    }:
        raise DocumentGenerationError("Approve this certificate request before generating a file.")
    if mode not in {"online", "offline"}:
        raise DocumentGenerationError("Choose Online or Offline certificate mode.")
    if file_format not in {"docx", "pdf"}:
        raise DocumentGenerationError("Choose Word or PDF format.")

    if file_format == "docx":
        return _docx_bytes(template, request, mode)

    pdf_docx_data = _docx_bytes_for_pdf(template, request, mode)
    converted_pdf = _convert_docx_with_libreoffice(pdf_docx_data)
    if converted_pdf is not None:
        return converted_pdf

    if not template.pdf_source:
        raise DocumentGenerationError(
            "PDF generation requires LibreOffice or a matching PDF source template."
        )
    try:
        source_document = fitz.open(template.pdf_source.path)
    except (fitz.FileDataError, OSError) as error:
        raise DocumentGenerationError("The PDF source template could not be opened.") from error
    try:
        if source_document.page_count != 1:
            raise DocumentGenerationError("Certificate PDF templates must contain exactly one page.")

        issue_date = timezone.localdate()
        page = source_document[0]
        if template.original_filename == SOURCE_FILES.get(request.category):
            _replace_pdf_source_fields(page, request, issue_date)
        else:
            _replace_pdf_placeholders(page, request, issue_date)
        online_pdf = source_document.tobytes(garbage=4, deflate=True)
    finally:
        source_document.close()
    return _offline_pdf(online_pdf) if mode == "offline" else online_pdf


def certificate_filename(request, mode, file_format):
    safe_name = re.sub(r"[^A-Za-z0-9 ._-]+", "", request.submission.name).strip()
    safe_name = re.sub(r"\s+", " ", safe_name)
    safe_name = safe_name.strip(" ._-")
    abbreviations = {
        CertificateRequest.Category.OFFER: "OL",
        CertificateRequest.Category.EXPERIENCE: "EC",
        CertificateRequest.Category.PROJECT: "PCC",
        CertificateRequest.Category.LOR: "LOR",
    }
    abbreviation = abbreviations.get(request.category)
    if not safe_name or abbreviation is None:
        raise DocumentGenerationError("A valid student name and certificate category are required.")
    return f"{safe_name}_{abbreviation}.{file_format}"


def certificate_content_hash(content):
    return hashlib.sha256(content).hexdigest()

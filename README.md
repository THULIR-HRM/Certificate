# GT Certify

A single-admin certificate request and generation portal. Student submissions are public; student records, templates, approvals, and generated files are restricted to the authenticated administrator.

## Local setup

1. Use Python 3.11 or newer.
2. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`. Set a new `SECRET_KEY` and `ADMIN_INITIAL_PASSWORD` before starting the application. The initial password is required only when bootstrapping the administrator; it is hashed and the administrator must change it at first login.
4. Apply migrations, initialize the default forms and academic catalog, and bootstrap the single administrator:

   ```powershell
   python manage.py migrate
   python manage.py seed_defaults
   python manage.py import_college_directory
   python manage.py bootstrap_admin
   ```

5. Start the server with `python manage.py runserver`.

The application uses SQLite and local persistent media storage by default. Set `DATABASE_URL` to a PostgreSQL connection string and configure persistent storage for `MEDIA_ROOT` in production. Do not expose the media directory as a public static path.

## Student formats and certificate downloads

Students can search degree options by abbreviation, full name, or configured alias. The selected degree and specialization are stored in the certificate format; no specialization is assumed when none is selected. Unlisted qualifications, specializations, and colleges can be entered manually. The single administrator can add, edit, and deactivate these options under **Academic catalog**.

The bundled Tamil Nadu college snapshot contains UGC-listed colleges that could be assigned to a district from the UGC directory's district field or address. It has 905 deduplicated entries across all 38 districts and retains each source address, affiliation, and UGC source URL. The UGC list does not provide a reliable category for every institution, so category is inferred only when the name explicitly identifies one; otherwise it is **Other**. This snapshot is not a complete list of all AICTE, medical, pharmacy, polytechnic, or other standalone institutions. Add or correct entries, aliases, and categories with an official source URL in **Academic catalog**. Students can always choose Other College or Other state / college not listed and enter the name and city manually.

Downloaded certificates use the student's name and certificate abbreviation, for example `Janavi T_OL.pdf`, `Janavi T_EC.docx`, `Janavi T_PCC.pdf`, or `Janavi T_LOR.docx`. Backup downloads use the same naming convention.

DOCX replacements retain the formatting of the original anchor and naturally wrap within the existing Word paragraph. The paired-PDF fallback cannot reflow fixed PDF text fields; it preserves the original font size and reports when a replacement does not fit. For PDFs with long replacement values, configure LibreOffice so the application can convert the generated DOCX and lay out the text naturally.

## Certificate templates

The supplied DOCX files are treated as source assets and are never edited in place. Configure `TEMPLATE_SOURCE_DIR` to the directory containing the originals, then run:

```powershell
python manage.py import_templates
```

The portal stores versioned private copies. Offer Letter generation also requires an administrator-configured program period; it will not reuse the sample student's dates.

The selected source DOCX files are:

- Offer Letter: `Certifiactes Template\Vaishnavi N - Offer Letter (1).docx`
- Experience Certificate: `Certifiactes Template\Mr. Kishore Kumar L -  EC.docx`
- Project Completion Certificate: `Certifiactes Template\Ms. Renuka B M - PC.docx`
- Letter of Recommendation: `Certifiactes Template\Mr. Kishore Kumar L - LOR .docx`

To correct a source layout without overwriting the original, edit a copy and upload it with its matching PDF through Admin > Certificate templates. Keep the selected source filename so the source-text substitutions continue to match; the importer skips an already-imported active source with the same filename.

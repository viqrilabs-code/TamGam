from app.api.v1.endpoints import tutor


def test_contains_pattern():
    assert tutor._contains_pattern("hello class 5", [r"\bclass\s*5\b"]) is True
    assert tutor._contains_pattern("hello world", [r"\bmath\b"]) is False


def test_extract_sources_dedupes_and_keeps_order():
    chunks = [
        {"source": "NCERT Class 5", "content_type": "ncert_book"},
        {"source": "NCERT Class 5", "content_type": "ncert_book"},
        {"source": "Class Notes", "content_type": "note_section"},
    ]
    sources = tutor._extract_sources(chunks)
    assert sources == [
        {"label": "NCERT Class 5", "type": "ncert_book"},
        {"label": "Class Notes", "type": "note_section"},
    ]


def test_is_allowed_upload_accepts_images_pdf_docx():
    assert tutor._is_allowed_upload("sheet.pdf", "application/pdf") is True
    assert tutor._is_allowed_upload("notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") is True
    assert tutor._is_allowed_upload("photo.png", "image/png") is True
    assert tutor._is_allowed_upload("scan.jpg", "") is True


def test_is_allowed_upload_rejects_other_types():
    assert tutor._is_allowed_upload("archive.zip", "application/zip") is False
    assert tutor._is_allowed_upload("script.exe", "application/octet-stream") is False

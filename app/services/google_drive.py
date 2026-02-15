# app/services/google_drive.py
# Google Drive API wrapper
# Downloads .docx transcripts and extracts raw text
#
# Auth: Service account JSON key (GOOGLE_SERVICE_ACCOUNT_KEY_PATH in .env)
# In production: key stored in GCP Secret Manager, mounted at runtime

import io
import os
from typing import Optional

from app.core.config import settings


def _get_drive_service():
    """
    Build authenticated Google Drive service.
    Uses service account key file.
    Returns None if credentials not configured (dev mode).
    """
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        key_path = settings.google_service_account_key_path
        if not os.path.exists(key_path):
            return None

        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=credentials)
    except Exception:
        return None


def get_file_metadata(file_id: str) -> Optional[dict]:
    """
    Fetch metadata for a Drive file.
    Returns dict with 'name', 'mimeType', 'size' or None if not accessible.
    """
    service = _get_drive_service()
    if not service:
        return {"name": f"mock_transcript_{file_id}.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

    try:
        return service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime",
        ).execute()
    except Exception:
        return None


def download_docx_as_text(file_id: str) -> Optional[str]:
    """
    Download a .docx file from Google Drive and extract plain text.
    Handles both native .docx and Google Docs (exported as .docx).

    Returns extracted text string or None if download fails.
    """
    service = _get_drive_service()
    if not service:
        # Dev mode mock -- return sample transcript text
        return _mock_transcript_text()

    try:
        # Get file metadata to check mime type
        metadata = service.files().get(fileId=file_id, fields="mimeType").execute()
        mime_type = metadata.get("mimeType", "")

        if mime_type == "application/vnd.google-apps.document":
            # Google Doc -- export as docx
            response = service.files().export(
                fileId=file_id,
                mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ).execute()
            file_bytes = response
        else:
            # Native .docx -- download directly
            from googleapiclient.http import MediaIoBaseDownload
            request = service.files().get_media(fileId=file_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            file_bytes = buffer.getvalue()

        return _extract_text_from_docx(file_bytes)

    except Exception as e:
        print(f"Google Drive download failed for {file_id}: {e}")
        return None


def _extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from .docx bytes using python-docx."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n\n".join(paragraphs)
    except Exception as e:
        print(f"docx text extraction failed: {e}")
        return ""


def _mock_transcript_text() -> str:
    """
    Mock transcript text for development.
    Returned when Google Drive credentials are not configured.
    """
    return """Class Transcript - Introduction to Algebra

Teacher: Good morning everyone. Today we are going to learn about algebraic expressions.

An algebraic expression is a mathematical phrase that can contain numbers, variables, and operators.

For example, 2x + 3 is an algebraic expression where x is a variable.

Key concepts covered today:
1. Variables and constants
2. Coefficients
3. Like terms and unlike terms
4. Simplification of expressions

Teacher: Can anyone tell me what a variable is?

Student: A variable is a letter that represents an unknown number.

Teacher: Excellent! That is correct. Variables are usually represented by letters like x, y, or z.

Let us look at some examples.
If x = 5, then 2x + 3 = 2(5) + 3 = 13.

Practice problems for homework:
- Simplify: 3x + 2x + 5
- Evaluate: 4y - 7 when y = 3
- Identify like terms in: 5a + 3b + 2a + b

See you all next class!"""
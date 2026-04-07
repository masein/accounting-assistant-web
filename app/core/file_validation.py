"""Validate file uploads by checking magic bytes (file signatures)."""
from __future__ import annotations

from fastapi import HTTPException

# Map MIME types to their expected magic byte signatures
_MAGIC_SIGNATURES: dict[str, list[tuple[bytes, int]]] = {
    # (signature_bytes, offset)
    "image/jpeg": [(b"\xff\xd8\xff", 0)],
    "image/png": [(b"\x89PNG\r\n\x1a\n", 0)],
    "image/webp": [(b"RIFF", 0), (b"WEBP", 8)],  # Must match BOTH
    "application/pdf": [(b"%PDF", 0)],
}

ALLOWED_TYPES = set(_MAGIC_SIGNATURES.keys())


def validate_file_magic(data: bytes, claimed_content_type: str) -> None:
    """
    Validate that file content matches its claimed MIME type.
    Raises HTTPException 400 if the content type is unsupported or
    the file signature doesn't match.
    """
    ct = claimed_content_type.strip().lower()
    if ct not in _MAGIC_SIGNATURES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ct}")

    sigs = _MAGIC_SIGNATURES[ct]
    for sig_bytes, offset in sigs:
        if len(data) < offset + len(sig_bytes):
            raise HTTPException(
                status_code=400,
                detail="File too small to validate signature",
            )
        if data[offset : offset + len(sig_bytes)] != sig_bytes:
            raise HTTPException(
                status_code=400,
                detail="File content does not match declared type (possible MIME spoofing)",
            )

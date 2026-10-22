import base64
import binascii


def decode_dash_upload(content: str, filename: str, required_suffix: str = ".raw") -> tuple[str, bytes, str]:
    if not filename:
        raise ValueError("Missing file name")
    if required_suffix and not filename.lower().endswith(required_suffix):
        raise ValueError(f"File '{filename}' must end with {required_suffix}")
    if not content:
        raise ValueError("Missing uploaded file content")

    try:
        header, encoded = content.split(",", maxsplit=1)
        media_type = "application/octet-stream"
        if header.startswith("data:"):
            media_type = header[5:].split(";", maxsplit=1)[0] or media_type
        return filename, base64.b64decode(encoded), media_type
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"Could not decode uploaded file '{filename}': {exc}") from exc

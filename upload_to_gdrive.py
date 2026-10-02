"""
Google Drive Uploader Utility for Linux Terminal Agent.
Uploads linux-terminal-agent.zip to Google Drive using Google Drive API v3
via OAuth 2.0 Access Token or Service Account.
"""

import os
import sys
import json
import urllib.request
import urllib.error

ZIP_FILE = "/linux-terminal-agent.zip"

def upload_with_access_token(access_token: str, folder_id: str = None) -> bool:
    """Uploads file using an OAuth2 Bearer Access Token."""
    if not os.path.exists(ZIP_FILE):
        print(f"Error: {ZIP_FILE} not found.")
        return False

    metadata = {
        "name": "linux-terminal-agent.zip",
        "mimeType": "application/zip"
    }
    if folder_id:
        metadata["parents"] = [folder_id]

    boundary = "-------314159265358979323846"
    delimiter = f"\r\n--{boundary}\r\n"
    close_delimiter = f"\r\n--{boundary}--\r\n"

    with open(ZIP_FILE, "rb") as f:
        file_bytes = f.read()

    body = (
        delimiter
        + "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        + json.dumps(metadata)
        + delimiter
        + "Content-Type: application/zip\r\n"
        + "Content-Transfer-Encoding: base64\r\n\r\n"
    ).encode("utf-8")

    import base64
    body += base64.b64encode(file_bytes) + close_delimiter.encode("utf-8")

    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token.strip()}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            file_id = data.get("id")
            print(f"✔ Uploaded successfully to Google Drive! File ID: {file_id}")
            return True
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"✖ Google Drive API Error (HTTP {e.code}): {err}")
        return False
    except Exception as e:
        print(f"✖ Upload failed: {str(e)}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 upload_to_gdrive.py <GOOGLE_DRIVE_OAUTH_TOKEN> [FOLDER_ID]")
        sys.exit(1)

    token = sys.argv[1]
    folder = sys.argv[2] if len(sys.argv) > 2 else None
    upload_with_access_token(token, folder)

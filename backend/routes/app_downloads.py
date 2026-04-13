"""
App Downloads — Platform-wide APK distribution.
Super admin uploads APK files; any authenticated user can view/download.
Files stored in Cloudflare R2 under _platform/apk-downloads/{slug}/
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import Optional
import os
import boto3
from botocore.config import Config
from config import _raw_db
from utils import get_current_user, now_iso, new_id

router = APIRouter(prefix="/app-downloads", tags=["App Downloads"])

ALLOWED_CONTENT_TYPES = {
    "application/vnd.android.package-archive",
    "application/octet-stream",
    "application/zip",
}
MAX_APK_SIZE = 200 * 1024 * 1024  # 200 MB

# Pre-defined app slots (slug → display metadata defaults)
APP_SLOTS = {
    "agrisms-gateway": {
        "slug": "agrisms-gateway",
        "name": "AgriSMS Gateway 2.0",
        "tagline": "Turn a dedicated Android phone into a secure SMS bridge between your SIM and AgriBooks.",
        "package": "com.agrism.gateway",
        "min_android": "8.0 (API 26)",
        "icon_color": "#16a34a",
    },
    "agrismart-terminal": {
        "slug": "agrismart-terminal",
        "name": "AgriSmart Terminal",
        "tagline": "The official AgriBooks Android app for in-store terminals with native thermal printing and scanner support.",
        "package": "com.agribooks.terminal",
        "min_android": "Android 8.0+",
        "icon_color": "#1d4ed8",
    },
}


def require_super_admin(user=Depends(get_current_user)):
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user


def _get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("R2_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def _r2_key(slug: str, filename: str) -> str:
    return f"_platform/apk-downloads/{slug}/{filename}"


async def _upload_to_r2(key: str, data: bytes, content_type: str):
    bucket = os.environ.get("R2_FILES_BUCKET")
    client = _get_r2_client()
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


async def _delete_from_r2(key: str):
    bucket = os.environ.get("R2_FILES_BUCKET")
    client = _get_r2_client()
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass


async def _get_presigned_url(key: str, filename: str, expires_in: int = 3600) -> str:
    bucket = os.environ.get("R2_FILES_BUCKET")
    client = _get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expires_in,
    )


@router.get("")
async def list_apps(user=Depends(get_current_user)):
    """List all app download entries. Returns pre-defined slots merged with DB data."""
    db_apps = await _raw_db.app_downloads.find(
        {"active": True}, {"_id": 0}
    ).to_list(length=None)
    db_map = {a["slug"]: a for a in db_apps}

    result = []
    for slug, defaults in APP_SLOTS.items():
        app = {**defaults}
        if slug in db_map:
            db = db_map[slug]
            app.update({
                "id": db.get("id"),
                "version": db.get("version", ""),
                "filename": db.get("filename", ""),
                "file_size": db.get("file_size", 0),
                "upload_date": db.get("upload_date", ""),
                "download_count": db.get("download_count", 0),
                "changelog": db.get("changelog", ""),
                "uploaded_by_email": db.get("uploaded_by_email", ""),
                "has_apk": True,
            })
        else:
            app["has_apk"] = False
        result.append(app)

    return result


@router.get("/{slug}/download-url")
async def get_download_url(slug: str, user=Depends(get_current_user)):
    """Get a pre-signed download URL for an APK. Increments download_count."""
    app = await _raw_db.app_downloads.find_one({"slug": slug, "active": True}, {"_id": 0})
    if not app or not app.get("r2_key"):
        raise HTTPException(status_code=404, detail="APK not available yet")

    url = await _get_presigned_url(app["r2_key"], app.get("filename", f"{slug}.apk"), expires_in=300)
    # Increment counter (fire-and-forget, don't block response)
    await _raw_db.app_downloads.update_one(
        {"slug": slug},
        {"$inc": {"download_count": 1}}
    )
    return {"url": url, "filename": app.get("filename"), "version": app.get("version")}


@router.post("/{slug}/upload")
async def upload_apk(
    slug: str,
    file: UploadFile = File(...),
    version: str = Form(...),
    changelog: str = Form(""),
    user=Depends(require_super_admin),
):
    """Upload or replace an APK for the given app slug."""
    if slug not in APP_SLOTS:
        raise HTTPException(status_code=400, detail=f"Unknown app slug: {slug}")

    content = await file.read()
    if len(content) > MAX_APK_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 200 MB)")

    content_type = file.content_type or "application/octet-stream"
    # Be permissive — APKs sometimes come with wrong content type
    original_filename = file.filename or f"{slug}.apk"
    safe_filename = original_filename.replace(" ", "_")
    r2_key = _r2_key(slug, safe_filename)

    # Delete old APK from R2 if replacing
    existing = await _raw_db.app_downloads.find_one({"slug": slug}, {"_id": 0})
    if existing and existing.get("r2_key") and existing["r2_key"] != r2_key:
        await _delete_from_r2(existing["r2_key"])

    await _upload_to_r2(r2_key, content, content_type)

    doc = {
        "id": existing["id"] if existing else new_id(),
        "slug": slug,
        "name": APP_SLOTS[slug]["name"],
        "version": version.strip(),
        "filename": safe_filename,
        "file_size": len(content),
        "content_type": content_type,
        "r2_key": r2_key,
        "changelog": changelog.strip(),
        "upload_date": now_iso(),
        "uploaded_by_email": user.get("email", ""),
        "download_count": existing.get("download_count", 0) if existing else 0,
        "active": True,
    }

    await _raw_db.app_downloads.update_one(
        {"slug": slug},
        {"$set": doc},
        upsert=True,
    )
    return {"message": f"{APP_SLOTS[slug]['name']} v{version} uploaded successfully", "slug": slug, "version": version}


@router.delete("/{slug}")
async def delete_apk(slug: str, user=Depends(require_super_admin)):
    """Remove the APK for a given app slot."""
    app = await _raw_db.app_downloads.find_one({"slug": slug}, {"_id": 0})
    if app and app.get("r2_key"):
        await _delete_from_r2(app["r2_key"])
    await _raw_db.app_downloads.update_one(
        {"slug": slug},
        {"$set": {"active": False, "r2_key": "", "filename": "", "has_apk": False}}
    )
    return {"message": "APK removed"}

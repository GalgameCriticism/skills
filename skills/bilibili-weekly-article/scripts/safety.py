# Created by Miyin
"""Local input, account and receipt guards for Bilibili writes."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preview_directory(root):
    root = Path(root).resolve(strict=True)
    preview = root / "_preview"
    if preview.is_symlink() or not preview.resolve().is_relative_to(root):
        raise ValueError("_preview 不能指向素材目录之外")
    preview.mkdir(exist_ok=True)
    return preview


def require_review(article):
    if article.get("title_status") != "manual" or not article.get("title", "").strip():
        raise ValueError("标题尚未人工定稿")
    if article.get("warnings"):
        raise ValueError("请处理 article.json 中的 warnings 后再上传或发布")
    if not article.get("credits"):
        raise ValueError("署名为空，请补全后再上传或发布")


def image_path(root, value):
    from PIL import Image
    root = Path(root).resolve(strict=True)
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("图片路径必须位于素材目录内")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("图片路径越出素材目录或不是文件")
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise ValueError("不支持的图片扩展名")
    with Image.open(path) as image:
        if image.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            raise ValueError("不支持的图片格式")
        image.verify()
    return path


def selected_images(root, article):
    names = []
    if article.get("source", {}).get("cover"):
        names.append(article["source"]["cover"])
    for section in article.get("sections", []):
        for item in section.get("items", []):
            selected = item.get("selected", [])
            if len(selected) > 2 or len(set(selected)) != len(selected):
                raise ValueError("每条新闻最多选择两张不同图片")
            for index in selected:
                if type(index) is not int or not 0 <= index < len(item["candidates"]):
                    raise ValueError("配图索引无效")
                names.append(item["candidates"][index])
    # Validate the whole batch before the first upload.
    return [image_path(root, name) for name in names]


def require_account(session, account_id):
    if not account_id or not str(account_id).isdigit():
        raise ValueError("请显式指定 --account-id <目标B站UID>")
    response = session.get("https://api.bilibili.com/x/web-interface/nav", timeout=15,
                           allow_redirects=False)
    response.raise_for_status()
    result = response.json()
    data = result.get("data") or {}
    if result.get("code") != 0 or not data.get("isLogin") or str(data.get("mid")) != str(account_id):
        raise ValueError("登录账号与目标 UID 不一致，已停止写入")
    return str(data["mid"])


def write_private(path, value):
    """Publish a complete mode-600 file, without following a destination symlink."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("拒绝写入符号链接")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value.encode("utf-8"))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_receipt(path, receipt):
    write_private(path, json.dumps(receipt, ensure_ascii=False, indent=2))


def claim_receipt(path, receipt):
    """A durable exclusive claim survives ambiguous failures and blocks retries."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)


def payload_metadata(article, account_id, paths):
    return {"article_sha256": digest(article), "account_id": str(account_id),
            "images": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}


def validate_payload(payload, article, account_id, paths):
    require_review(article)
    expected = payload_metadata(article, account_id, paths)
    expected["payload_sha256"] = digest({k: v for k, v in payload.items() if k != "_local"})
    if payload.get("_local") != expected:
        raise ValueError("素材、定稿或目标账号已变化，请重新 build_opus.py")
    if payload["opus_req"]["opus"]["title"] != article["title"]:
        raise ValueError("payload 标题与定稿不一致")


def post_draft(session, url, **kwargs):
    # Requests exception strings can expose the csrf query parameter.
    try:
        response = session.post(url, allow_redirects=False, **kwargs)
        response.raise_for_status()
        return response.json()
    except Exception:
        raise RuntimeError("草稿提交未确认，请先核对创作中心；已保留回执") from None

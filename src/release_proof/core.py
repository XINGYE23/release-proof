"""Manifest creation and verification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CHUNK_SIZE = 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ManifestError(ValueError):
    """The manifest or a command argument is invalid."""


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _artifact_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ManifestError("artifact path must be a relative POSIX path")
    if value.startswith("/") or "//" in value or any(part in ("", ".", "..") for part in value.split("/")):
        raise ManifestError(f"unsafe artifact path: {value}")
    if PurePosixPath(value).as_posix() != value:
        raise ManifestError(f"invalid artifact path: {value}")
    return value


def _https_url(value: object) -> str:
    if not isinstance(value, str) or any(character.isspace() or ord(character) < 32 for character in value):
        raise ManifestError("URL must be a string without whitespace or control characters")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ManifestError(f"invalid URL: {value}") from exc
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ManifestError(f"URL must be an HTTPS URL without credentials or fragment: {value}")
    return value


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            manifest = json.load(stream, object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "release", "artifacts"}:
        raise ManifestError("manifest must contain only schema_version, release and artifacts")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ManifestError("unsupported schema_version; expected 1")
    if not isinstance(manifest["release"], str) or not manifest["release"].strip():
        raise ManifestError("release must be a nonempty string")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ManifestError("artifacts must be a nonempty list")
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or not {"path", "size", "sha256"} <= set(item) or set(item) - {"path", "size", "sha256", "url"}:
            raise ManifestError("each artifact needs path, size and sha256; url is optional")
        name = _artifact_path(item["path"])
        if name.casefold() in seen:
            raise ManifestError(f"duplicate artifact path: {name}")
        seen.add(name.casefold())
        if type(item["size"]) is not int or item["size"] < 0:
            raise ManifestError(f"invalid size for {name}")
        if not isinstance(item["sha256"], str) or not SHA256.fullmatch(item["sha256"]):
            raise ManifestError(f"invalid SHA-256 for {name}")
        if "url" in item:
            _https_url(item["url"])
    return manifest


class _HTTPSOnlyRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _hash_url(url: str, expected_size: int, timeout: float) -> tuple[int, str]:
    opener = build_opener(_HTTPSOnlyRedirects)
    request = Request(url, headers={"User-Agent": "release-proof/0.1", "Accept-Encoding": "identity"})
    digest = hashlib.sha256()
    size = 0
    with opener.open(request, timeout=timeout) as response:
        _https_url(response.geturl())
        for chunk in iter(lambda: response.read(CHUNK_SIZE), b""):
            size += len(chunk)
            if size > expected_size:
                return size, "exceeds declared size"
            digest.update(chunk)
    return size, digest.hexdigest()


def create_manifest(output: Path, release: str, files: list[Path], url_base: str | None = None, force: bool = False) -> dict[str, object]:
    if not release.strip():
        raise ManifestError("release must be nonempty")
    if not files:
        raise ManifestError("provide at least one artifact")
    if url_base is not None:
        _https_url(url_base)
        parts = urlsplit(url_base)
        if parts.query:
            raise ManifestError("URL base cannot contain a query")
    output = output.resolve()
    base = output.parent
    if not base.is_dir():
        raise ManifestError(f"output directory does not exist: {base}")
    if output.exists() and not force:
        raise ManifestError(f"manifest exists; use --force to replace it: {output}")
    artifacts: list[dict[str, object]] = []
    seen: set[str] = set()
    for file in files:
        try:
            resolved = file.resolve(strict=True)
            name = resolved.relative_to(base).as_posix()
        except (OSError, ValueError) as exc:
            raise ManifestError(f"artifact must exist inside the output directory: {file}") from exc
        name = _artifact_path(name)
        if name.casefold() in seen:
            raise ManifestError(f"duplicate artifact: {name}")
        if resolved == output:
            raise ManifestError("manifest cannot also be an artifact")
        seen.add(name.casefold())
        if not resolved.is_file():
            raise ManifestError(f"artifact is not a regular file: {file}")
        try:
            size, sha256 = _hash_file(resolved)
        except OSError as exc:
            raise ManifestError(f"cannot read artifact {file}: {exc}") from exc
        item: dict[str, object] = {"path": name, "size": size, "sha256": sha256}
        if url_base is not None:
            item["url"] = url_base.rstrip("/") + "/" + "/".join(quote(part) for part in name.split("/"))
        artifacts.append(item)
    manifest: dict[str, object] = {"schema_version": 1, "release": release, "artifacts": artifacts}
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=base, prefix=".release-proof-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return manifest


def verify_manifest(path: Path, remote: bool = False, timeout: float = 30) -> tuple[dict[str, object], list[dict[str, str]]]:
    manifest = _load_manifest(path)
    base = path.resolve().parent
    results: list[dict[str, str]] = []
    for item in manifest["artifacts"]:
        name = item["path"]
        expected_size = item["size"]
        expected_hash = item["sha256"]
        candidate = base.joinpath(*name.split("/"))
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(base)
            if not resolved.is_file():
                raise OSError("not a regular file")
            size, sha256 = _hash_file(resolved)
            if size != expected_size:
                raise ValueError(f"size {size} != expected {expected_size}")
            if sha256 != expected_hash:
                raise ValueError(f"SHA-256 {sha256} != expected {expected_hash}")
            results.append({"path": name, "scope": "local", "status": "pass"})
        except (OSError, ValueError) as exc:
            results.append({"path": name, "scope": "local", "status": "fail", "reason": str(exc)})
        if remote:
            if "url" not in item:
                results.append({"path": name, "scope": "remote", "status": "fail", "reason": "no URL in manifest"})
                continue
            try:
                size, sha256 = _hash_url(item["url"], expected_size, timeout)
                if size != expected_size:
                    raise ValueError(f"size {size} != expected {expected_size}")
                if sha256 != expected_hash:
                    raise ValueError(f"SHA-256 {sha256} != expected {expected_hash}")
                results.append({"path": name, "scope": "remote", "status": "pass"})
            except (OSError, URLError, ValueError) as exc:
                results.append({"path": name, "scope": "remote", "status": "fail", "reason": str(exc)})
    return manifest, results

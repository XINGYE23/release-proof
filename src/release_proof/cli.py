"""Command line interface for Release Proof."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import __version__
from .core import ManifestError, create_manifest, verify_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release-proof", description="Verify release artifacts against a JSON manifest")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a manifest from local artifacts")
    create.add_argument("--release", required=True, help="release identifier, such as 1.2.0")
    create.add_argument("--output", required=True, type=Path, help="manifest JSON path")
    create.add_argument("--url-base", help="HTTPS directory containing the same artifact paths")
    create.add_argument("--force", action="store_true", help="replace an existing manifest")
    create.add_argument("files", nargs="+", type=Path)
    verify = commands.add_parser("verify", help="verify local artifacts and optionally public URLs")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--remote", action="store_true", help="download and hash each HTTPS artifact URL")
    verify.add_argument("--timeout", type=float, default=30, help="per-request network timeout in seconds")
    verify.add_argument("--json", action="store_true", help="print machine-readable results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_manifest(args.output, args.release, args.files, args.url_base, args.force)
            print(f"Created {args.output} for {len(manifest['artifacts'])} artifact(s)")
            return 0
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ManifestError("timeout must be a positive finite number")
        manifest, results = verify_manifest(args.manifest, args.remote, args.timeout)
        if args.json:
            print(json.dumps({"release": manifest["release"], "results": results}, ensure_ascii=False, indent=2))
        else:
            for result in results:
                detail = f": {result['reason']}" if "reason" in result else ""
                print(f"{result['status'].upper()} {result['scope']} {result['path']}{detail}")
        return 0 if all(result["status"] == "pass" for result in results) else 1
    except ManifestError as exc:
        print(f"release-proof: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

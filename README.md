# Release Proof

A small, dependency-free CLI for checking that published release files are the
same files you built. It records each artifact's byte size and SHA-256 hash in
a JSON manifest, then checks local copies and, on request, public HTTPS
downloads. It works with APKs, ZIPs, binaries, or any other file type.

Release Proof is an independent open-source project. It does not require a
GitHub account, an OpenAI account, or any hosted service.

## Requirements

Python 3.10 or newer. There are no runtime dependencies.

## Quick start

From this repository:

```sh
python -m pip install -e .
mkdir -p dist
printf 'example release file\n' > dist/example.txt
release-proof create --release 1.0.0 --output release.json dist/example.txt
release-proof verify release.json
```

On Windows PowerShell, create a sample file with
`New-Item -ItemType Directory dist` and
`Set-Content -NoNewline dist/example.txt 'example release file'`.

To record public download URLs, add an HTTPS directory when creating the
manifest:

```sh
release-proof create --release 1.0.0 --output release.json \
  --url-base https://downloads.example.org/releases/1.0.0 \
  dist/example.txt
release-proof verify release.json --remote
```

`--remote` downloads each listed URL and checks its bytes against the same
size and SHA-256 as the local artifact. Remote downloads are **off by
default**. Redirects to HTTP are rejected. Use `--json` for CI output:

```sh
release-proof verify release.json --remote --json
```

Exit status is `0` when every check passes, `1` when a file or download fails
verification, and `2` for an invalid manifest or command input. `--force`
allows `create` to replace an existing manifest. `--timeout` sets a positive
per-request network timeout in seconds (default: 30).

## Manifest format

```json
{
  "schema_version": 1,
  "release": "1.0.0",
  "artifacts": [
    {
      "path": "dist/example.txt",
      "size": 21,
      "sha256": "82fe2aeb7a93235ae392774a054d2631d8e67c556000144da28bf35e34bb583b",
      "url": "https://downloads.example.org/releases/1.0.0/dist/example.txt"
    }
  ]
}
```

The example digest matches `example release file` followed by a newline.
Run `create` to generate a manifest for your actual files. Paths are relative
to the manifest's directory and use `/` on every
platform. The URL is optional unless `--remote` is used. The CLI rejects
duplicate artifact paths and paths that escape the manifest directory.

## CI

Store the manifest beside the artifacts in your release job, then run
`release-proof verify release.json`. After uploading, run the same command
with `--remote` to catch stale CDN files, wrong redirects, or mismatched
uploads. This verifies file identity; it does not validate a package's code,
signature, or provenance.

## Contributing

Issues and pull requests are welcome after the repository is published. Run
the tests with:

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

On PowerShell: `$env:PYTHONPATH='src'; python -m unittest discover -s tests -v`.

## License

MIT. See [LICENSE](LICENSE).


# File hash development export

A minimal Python standard-library utility and isolated tests for a reviewed
public-source acquisition pilot. This is not a full application distribution.

Run from this directory with Python 3.10 or newer:

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v

`get_file_sha256(Path)` returns SHA-256 and caches by resolved path,
mtime nanoseconds, and size. A stat failure returns an empty string; read
errors after a successful stat may propagate. `clear_sha_cache()` resets it.
The cache is not safe against same-size, same-mtime content replacement or
concurrent writers. No network, credentials, real user data or third-party
packages are required for these tests.

Only the files in the export manifest are part of this pilot. Runtime state,
private source history, workflows and application acceptance are excluded.
No open-source license is added by this export; license selection remains a
separate owner decision.

## Additional reviewed input: SVG Converter

See [SVG offline input](SVG_INPUT_README.md) and its [manifest](SVG_INPUT_MANIFEST.json) for the separately scoped logging/paste development input. The original file-hash pilot and its tests remain included.

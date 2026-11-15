"""HTTP 200 alone is insufficient: Strata also reports whether the model is loaded."""
import json
import os
import sys
import urllib.error
import urllib.request


def is_ready(health):
    return (
        isinstance(health, dict)
        and health.get("service") == "strata"
        and health.get("status") == "ok"
        and health.get("loaded") is True
        and health.get("api_key") is True
    )


def main():
    url = f"http://127.0.0.1:{os.environ.get('PORT', '8080')}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            healthy = response.status == 200 and is_ready(json.load(response))
    except (OSError, ValueError, urllib.error.URLError):
        healthy = False
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())

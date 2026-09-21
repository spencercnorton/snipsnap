#!/usr/bin/env python3
"""The version the binary reports must be the version the package is, and the
git tag must be that same number.

They drifted once already: the binary said v1.0.0 while apt said 1.0.1-1kleos1,
and 1.0.1 existed solely to fix the dead capture shortcut. Anyone checking
`snipsnap --version` or About to find out whether they had that fix got a
number that could not tell them -- which is the one moment the version string
has a job to do.

The tag was a second, separate counter until 2026-07-29: `v1.1.4-kleos1` was
the tag on the commit that produced package 1.0.3. Two numbers for one release
means neither answers "what is running", and the tag is what publishes, so it
is the one that has to be right. One counter now: CMakeLists.txt, the changelog
and the tag all say the same thing.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cmake_version():
    text = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    match = re.search(r"^set\(SNIPSNAP_VERSION\s+([0-9]+\.[0-9]+\.[0-9]+)\)", text, re.M)
    assert match, "CMakeLists.txt has no set(SNIPSNAP_VERSION x.y.z)"
    return match.group(1)


def changelog_version():
    first = (ROOT / "packaging" / "debian" / "changelog").read_text(
        encoding="utf-8"
    ).splitlines()[0]
    match = re.match(r"^snipsnap \(([^)]+)\)", first)
    assert match, f"unparsable changelog header: {first!r}"
    full = match.group(1)
    # An epoch would also break the .deb filename the CI job asserts, and is
    # how this package could outrank a distribution's own flameshot, which the
    # compatibility symlink and Provides: flameshot make a real possibility.
    assert ":" not in full, f"changelog version carries an epoch: {full!r}"
    return full.split("-")[0], full


def main():
    cmake = cmake_version()
    upstream, full = changelog_version()
    if cmake != upstream:
        print(
            f"version mismatch: CMakeLists.txt says {cmake}, "
            f"packaging/debian/changelog says {full} (upstream {upstream}).\n"
            f"`snipsnap --version` would report {cmake} while apt reports {full}.",
            file=sys.stderr,
        )
        return 1

    # Set by the release pipeline when it builds a tag (GitLab's CI_COMMIT_TAG,
    # or GitHub's GITHUB_REF_NAME when GITHUB_REF_TYPE is "tag").
    tag = os.environ.get("CI_COMMIT_TAG")
    if not tag and os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME")
    if tag and tag != f"v{cmake}":
        print(
            f"tag mismatch: tagged {tag}, but this tree builds {cmake}.\n"
            f"Tags are the package version prefixed with v -- tag v{cmake}, or\n"
            f"bump CMakeLists.txt and packaging/debian/changelog to match {tag}.",
            file=sys.stderr,
        )
        return 1

    print(f"ok: {cmake} == {full}" + (f" == {tag}" if tag else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Everything snipsnap-annotator runs must be a declared dependency.

It shipped needing python3 and gsettings with neither in Depends. CI could not
catch it: the package build installs libglib2.0-bin as a *build*
dependency and then runs `snipsnap-annotator status` in that same container,
so the helper always found what it needed. On a machine with only the declared
dependencies it would have died on its shebang.

This checks the script against the control file directly, so it does not
matter what happens to be installed wherever it runs.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "snipsnap-annotator"
CONTROL = ROOT / "packaging" / "debian" / "control"

# Which package provides each external command the script may invoke. Adding a
# new command to the script without adding it here fails this test, which is
# the point -- the mapping is the thing a person forgets.
PROVIDED_BY = {
    "python3": "python3",
    "gsettings": "libglib2.0-bin",
}


def declared_depends():
    text = CONTROL.read_text(encoding="utf-8")
    block = re.search(r"\nDepends:\n(.*?)\n(?=[A-Z][a-zA-Z-]*:)", text, re.S)
    assert block, "could not find the binary package's Depends block"
    names = set()
    for line in block.group(1).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("#") or line.startswith("${"):
            continue
        # Take the first alternative and drop any version constraint.
        names.add(line.split("|")[0].split()[0])
    return names


def required_commands():
    source = SCRIPT.read_text(encoding="utf-8")
    needed = set()

    shebang = source.splitlines()[0]
    match = re.match(r"#!/usr/bin/(?:env\s+)?(\S+)", shebang)
    assert match, f"unrecognised shebang: {shebang!r}"
    needed.add(Path(match.group(1)).name)

    # Every subprocess invocation's argv[0], however it is spelled.
    for call in re.findall(r"subprocess\.run\(\s*\[\s*([^\]]+)\]", source, re.S):
        first = call.split(",")[0].strip()
        literal = re.match(r'^[\'"]([^\'"]+)[\'"]$', first)
        if literal:
            needed.add(literal.group(1))
        else:
            # A variable argv[0] -- e.g. a constant defined above.
            name = first.strip()
            const = re.search(rf'^{re.escape(name)}\s*=\s*[\'"]([^\'"]+)[\'"]',
                              source, re.M)
            if const:
                needed.add(const.group(1))
    # shutil.which() targets are optional at runtime by design (satty), so they
    # are deliberately not required here.
    return needed


def main():
    depends = declared_depends()
    problems = []
    for command in sorted(required_commands()):
        package = PROVIDED_BY.get(command)
        if package is None:
            problems.append(
                f"snipsnap-annotator runs {command!r}, which is not in this "
                f"test's PROVIDED_BY map -- add it there and to Depends."
            )
        elif package not in depends:
            problems.append(
                f"snipsnap-annotator runs {command!r} from {package}, which is "
                f"not in Depends. Declared: {sorted(depends)}"
            )
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"ok: {sorted(required_commands())} all covered by Depends")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Canonicalize the ordering of Project U-Ray text database files.

Lines use the natural ordering established by Project U-Ray/Project X-Ray, so
numbered names such as ``FEATURE[2]`` precede ``FEATURE[10]``.  Within a
segbits row, bit tokens are ordered numerically by frame and bit.  A leading
``!`` describes the polarity of a bit and is deliberately ignored when
ordering its address.
"""

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


BIT_RE = re.compile(r"^(!?)([0-9]+)_([0-9]+)$")
TAG_SEPARATOR_RE = re.compile(r"[_.\[\]]")
TRAILING_NUMBER_RE = re.compile(r"^(.*?)([0-9]+)$")


def bit_sort_key(token):
    """Return the numeric address of a bit, followed by a stable polarity key."""
    match = BIT_RE.fullmatch(token)
    if match is None:
        raise ValueError("not a bit token: {!r}".format(token))

    # Polarity is only a tie breaker.  Match the established Project U-Ray
    # convention by placing a negated token first if an invalid/unfinished
    # database happens to contain both forms of the same address.
    return (int(match.group(2)), int(match.group(3)), match.group(1) != "!")


def natural_component_sort_key(component):
    """Normalize one tag component into a totally ordered value.

    The leading discriminator reproduces the mixed-type ordering used by the
    canonicalizer in prjuray/utils/sort_db.py: integers, strings, and then
    string/integer pairs.
    """
    match = TRAILING_NUMBER_RE.fullmatch(component)
    if match is None:
        return (1, component.encode("ascii"))
    if not match.group(1):
        return (0, int(match.group(2)))
    return (2, match.group(1).encode("ascii"), int(match.group(2)))


def natural_tag_sort_key(tag):
    """Return the established natural sort key for a database feature tag."""
    return tuple(
        natural_component_sort_key(component)
        for component in TAG_SEPARATOR_RE.split(tag)
        if component
    )


def canonicalize_line(line, source="<input>", line_number=0):
    """Canonicalize bit order in one database line.

    Non-segbits rows, such as unresolved ``<0 candidates>`` rows, are retained
    verbatim apart from whitespace normalization.  Mixing bit and non-bit
    values in one row is rejected because guessing could change its meaning.
    """
    fields = line.split()
    if not fields:
        return None

    value_start = 1
    if len(fields) > 1 and fields[1].startswith("origin:"):
        value_start = 2

    values = fields[value_start:]
    bit_values = [BIT_RE.fullmatch(value) is not None for value in values]

    if values and all(bit_values):
        fields[value_start:] = sorted(values, key=bit_sort_key)
    elif any(bit_values):
        raise ValueError(
            "{}:{}: row mixes bit and non-bit values: {}".format(
                source, line_number, line.rstrip()
            )
        )

    return " ".join(fields)


def canonicalize_text(text, source="<input>", line_order="natural"):
    """Return canonical database text."""
    lines = []
    for line_number, line in enumerate(text.splitlines(), 1):
        canonical = canonicalize_line(line, source, line_number)
        if canonical is not None:
            lines.append(canonical)

    try:
        if line_order == "natural":
            lines.sort(
                key=lambda line: (
                    natural_tag_sort_key(line.split(None, 1)[0]),
                    line.encode("ascii"),
                )
            )
        elif line_order == "ascii":
            lines.sort(key=lambda line: line.encode("ascii"))
        else:
            raise ValueError("unknown line ordering: {}".format(line_order))
    except UnicodeEncodeError as error:
        raise ValueError("{}: database lines must be ASCII: {}".format(source, error))

    return "".join(line + "\n" for line in lines)


def find_database_files(paths):
    """Expand files and directories into a deterministic list of .db files."""
    files = set()
    for path in paths:
        if path.is_dir():
            files.update(candidate for candidate in path.rglob("*.db") if candidate.is_file())
        elif path.is_file():
            if path.suffix != ".db":
                raise ValueError("not a .db file: {}".format(path))
            files.add(path)
        else:
            raise ValueError("path does not exist: {}".format(path))

    return sorted(files, key=lambda path: os.fsencode(str(path)))


def replace_file(path, contents):
    """Atomically replace path while retaining its permission bits."""
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", newline="\n", dir=str(path.parent),
            prefix="." + path.name + ".", delete=False
        ) as temporary:
            temporary.write(contents)
            temporary_name = temporary.name
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, str(path))
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def process_file(path, check, line_order):
    original = path.read_text(encoding="ascii")
    canonical = canonicalize_text(original, str(path), line_order)
    if original == canonical:
        return False
    if not check:
        replace_file(path, canonical)
    return True


def main(argv=None):
    repository = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="*", type=Path, default=[repository / "zynqusp"],
        help=".db files or directories to process (default: zynqusp)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="report non-canonical files and exit nonzero without changing them",
    )
    parser.add_argument(
        "--line-order", choices=("natural", "ascii"), default="natural",
        help="line ordering to use (default: natural, matching Project U-Ray)",
    )
    args = parser.parse_args(argv)

    try:
        files = find_database_files(args.paths)
        changed = [
            path for path in files
            if process_file(path, args.check, args.line_order)
        ]
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))

    action = "would sort" if args.check else "sorted"
    for path in changed:
        try:
            display_path = path.relative_to(repository)
        except ValueError:
            display_path = path
        print("{} {}".format(action, display_path))

    if args.check and changed:
        print("{} database file(s) are not canonical".format(len(changed)), file=sys.stderr)
        return 1

    print("{} database file(s); {} already canonical".format(
        len(changed), len(files) - len(changed)
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())

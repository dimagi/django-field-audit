#!/usr/bin/env python3
import re
import sys


def main():
    lib_version = get_lib_version("./field_audit/__init__.py")
    chlog_version = get_latest_changelog_version("./CHANGELOG.md")
    if lib_version != chlog_version:
        raise Fail(
            "Library and latest changelog versions do not match: "
            f"{lib_version!r} != {chlog_version!r}"
        )
    print(f"Library and latest changelog versions match: {lib_version}")


def get_lib_version(filepath):
    with open(filepath, "r") as file:
        return re.search(r'__version__ = "([^"]+)"', file.read()).group(1)


def get_latest_changelog_version(filepath):
    expected_msg = "(expected format: '## vN.N.N - <date>')"
    with open(filepath, "r") as file:
        for line_num, line in enumerate(file, start=1):
            if line.startswith("##"):
                # the first non-H1 header must be the latest version
                try:
                    # second field, drop the leading "v"
                    version_string = line.split()[1][1:]
                except IndexError:
                    version_string = None
                if not version_string:
                    raise InvalidChangelog(
                        f"Invalid changelog header on line {line_num}: "
                        f"{line!r} {expected_msg}"
                    )
                return version_string
    raise InvalidChangelog(f"No changlog entries found {expected_msg}")


class Fail(Exception):
    pass


class InvalidChangelog(Fail):
    pass


if __name__ == "__main__":
    try:
        main()
    except Fail as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""SPOT test: every `hv` subcommand resolves to a real handler. Stdlib only, no daemon.

WHY IT EXISTS. Merging the mac and PC lines took one side of a conflict in cli.py and, with it,
silently deleted a handler that was still registered a few hundred lines below. Neither
`ast.parse` nor `import highvisor.cli` catches that — the name is only resolved when
build_parser() runs — so the merge passed every gate I had and then `hv state` died with
`NameError: name '_cmd_mouse' is not defined` on the next call.

A parser that builds is a weak claim; a parser whose every subcommand has a callable handler is
the claim worth making.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from highvisor import cli  # noqa: E402


def main():
    parser = cli.build_parser()
    subs = parser._subparsers._group_actions[0].choices
    bad = []
    for name, sub in subs.items():
        fn = sub.get_default("fn")
        if fn is None or not callable(fn):
            bad.append(name)
    for name in sorted(subs):
        pass
    if bad:
        print("FAILED — %d subcommand(s) with no callable handler: %s" % (len(bad), ", ".join(bad)))
        return 1
    print("ok — %d subcommands, every one resolves to a callable handler" % len(subs))
    return 0


if __name__ == "__main__":
    sys.exit(main())

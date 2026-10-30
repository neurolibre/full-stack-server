"""Commands an editor runs on the server for a paper's description of its data, outside the review issue.

    python api/weft_admin.py reset <doi>
    python api/weft_admin.py publish

reset starts the paper's description over (weft_steps.reset): the confirmed description is moved aside as
<stem>.weft.yaml.previous, so the next confirmation measures its version against it; its profiles and the working
copy go; Evidentron's site is published again without its documents; and the CDN is rebuilt. The paper's rows stay
in the store until the next load of the paper replaces them, since Evidentron has no command to remove them. Then
`@roboneuro weft draft` drafts it again.

publish publishes Evidentron's site again from the store (its terms, documents, metadata and index.json, with each
paper's kin) and rebuilds the CDN: after a new Evidentron is installed, so what it publishes follows its new rules.
Sakiori's walks read the index at their next build (POST /api/sakiori/build).

Each command waits for any task that holds the store, as the tasks wait for each other, and says each thing it did in
a sentence. The places are weft_steps' own.
"""

import argparse
import contextlib
import sys

import myst_cdn
import weft_messages as say
import weft_steps


def _one_line(e: BaseException) -> str:
    """An exception's message on one line, without a final full stop."""
    lines = str(e).strip().splitlines()
    return (lines[0] if lines else "no detail").rstrip(".")


def reset(doi: str) -> int:
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(weft_steps.store_lock())
        except TimeoutError:
            print(say.store_busy(myst_cdn.duration(weft_steps.STORE_LOCK_WAIT)))
            return 1
        result = weft_steps.reset(doi, weft_steps.WEFTS_PATH, weft_steps.GRAPH_DB_PATH, weft_steps.SITE_PATH)
        for sentence in result.said:
            print(sentence)
        if result.stopped:
            return 1
        if not result.removed:
            return 0
        try:
            myst_cdn.rebuild()
        except Exception as e:
            print(f"The CDN could not be rebuilt ({e.__class__.__name__}: {_one_line(e)}); the documents leave it at "
                  "the next rebuild.")
            return 1
        print("The CDN was rebuilt without them.")
    return 0


def publish() -> int:
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(weft_steps.store_lock())
        except TimeoutError:
            print(say.store_busy(myst_cdn.duration(weft_steps.STORE_LOCK_WAIT)))
            return 1
        result = weft_steps.publish(weft_steps.SITE_PATH, weft_steps.GRAPH_DB_PATH)
        if not result.get("ok"):
            print("Evidentron's site was not published again: " + "; ".join(str(e) for e in result.get("errors") or ["it said nothing"]))
            return 1
        print("Evidentron's site was published again.")
        try:
            myst_cdn.rebuild()
        except Exception as e:
            print(f"The CDN could not be rebuilt ({e.__class__.__name__}: {_one_line(e)}); the site joins it at the next rebuild.")
            return 1
        print("The CDN was rebuilt with it. Sakiori's walks read it at their next build (POST /api/sakiori/build).")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python api/weft_admin.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start_over = commands.add_parser("reset", help="start a paper's description over (see the module's text)")
    start_over.add_argument("doi", help="the paper's DOI, for example 10.55458/neurolibre.00041")
    commands.add_parser("publish", help="publish Evidentron's site again and rebuild the CDN (see the module's text)")
    args = parser.parse_args(argv)
    return reset(args.doi) if args.command == "reset" else publish()


if __name__ == "__main__":
    sys.exit(main())

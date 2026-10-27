"""Commands an editor runs on the server for a paper's description of its data, outside the review issue.

    python api/weft_admin.py reset <doi>

reset starts the paper's description over: the confirmed description and its profiles leave the server's folder
of descriptions (the description is kept beside it as <stem>.weft.yaml.previous, so the next confirmation measures
its version against it), its FAIR² and Croissant documents leave Evidentron's site, and the CDN is rebuilt without
them. The paper's rows stay in the store until the next load of the paper replaces them, since Evidentron has no
command to remove them. Then `@roboneuro weft draft` drafts it again. The command waits for any task that holds the
store, as the tasks wait for each other, and says each thing it did in a sentence.
"""

import argparse
import sys
from pathlib import Path

import myst_cdn
import weft_steps

EVIDENTRON_PATH = myst_cdn.EVIDENTRON_SITE.parent
WEFTS_PATH = EVIDENTRON_PATH / "wefts"
GRAPH_DB_PATH = EVIDENTRON_PATH / "graph.db"
SITE_PATH = myst_cdn.EVIDENTRON_SITE
STORE_LOCK_PATH = EVIDENTRON_PATH / "store.lock"       # the lock the tasks hold while they write (weft_tasks)
STORE_LOCK_WAIT = 1800


def reset(doi: str) -> int:
    try:
        with myst_cdn.held(Path(STORE_LOCK_PATH), STORE_LOCK_WAIT, "busy"):
            for sentence in weft_steps.reset(doi, Path(WEFTS_PATH), Path(GRAPH_DB_PATH), Path(SITE_PATH)):
                print(sentence)
            try:
                myst_cdn.rebuild()
            except Exception as e:
                print(f"The CDN could not be rebuilt ({e.__class__.__name__}: {_one_line(e)}); the documents "
                      "leave it at the next rebuild.")
                return 1
            print("The CDN was rebuilt without them.")
    except TimeoutError:
        print(f"Another task kept the paper store busy for more than {myst_cdn._duration(STORE_LOCK_WAIT)}, so "
              "nothing was reset. Try again in a while.")
        return 1
    return 0


def _one_line(e: BaseException) -> str:
    """An exception's message on one line, without a final full stop."""
    lines = str(e).strip().splitlines()
    return (lines[0] if lines else "no detail").rstrip(".")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python api/weft_admin.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start_over = commands.add_parser("reset", help="start a paper's description over (see the module's text)")
    start_over.add_argument("doi", help="the paper's DOI, for example 10.55458/neurolibre.00041")
    args = parser.parse_args(argv)
    return reset(args.doi)


if __name__ == "__main__":
    sys.exit(main())

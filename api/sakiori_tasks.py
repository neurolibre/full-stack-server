"""Sakiori's build (sakiori.py) as celery tasks: the build of one paper's walk that reports on its review issue
(queued after the paper is published to the CDN, and by POST /api/sakiori/walk, which `@roboneuro sakiori` sends), and
the build of one paper or the whole collection by POST /api/sakiori/build, which reports nowhere.

The celery module imports this one at its end, which registers the tasks on its app; the publication step calls
queue() for the line it adds to its comment.
"""

import sakiori
from neurolibre_celery_tasks import BaseNeuroLibreTask, celery_app
from task_outcome import TaskFailed

# What the authors read when their paper's walk is served: the walk, why it is there, and what it shows, in the order
# a walk visits its stops (woven's src/stops.ts).
WALK_READY = """🧵 **Your paper's walk through the collection is ready:** {address}

You put in the hard work to do more than a PDF, so we have a surprise for you. Sakiori follows your paper through the
NeuroLibre collection, one stop at a time: who wrote it and the works it builds on, its data and what each of its
columns measures, the archives and the repository that make it reproducible, its dashboard when it has one, the papers
whose work is closest to yours and why, a question an AI agent answered after reading your paper, and the
machine-readable descriptions (FAIR² and Croissant) that let others find and reuse your data."""


def walk_ready(number: str, said) -> str:
    """The comment for a paper whose walk is served, with what did not go as it should folded underneath."""
    text = WALK_READY.format(address=sakiori.address(number))
    if said:
        text += ("\n\n<details><summary>Not everything was written this time</summary>\n\n"
                 + "\n".join(f"- {s}" for s in said) + "\n</details>")
    return text


@celery_app.task(bind=True, throws=(TaskFailed,), soft_time_limit=sakiori.BUILD_SECONDS,
                 time_limit=sakiori.BUILD_SECONDS + 600)
def sakiori_walk_task(self, screening_dict):
    """Build Sakiori with this paper's notes and answer written again, and say on its review issue that its walk is
    served, or why not."""
    task = BaseNeuroLibreTask(self, screening_dict)
    number = f"{task.screening.issue_id:05d}"
    task.start("🧵 Weaving this paper's walk through the collection.")
    got = sakiori.build([number])
    if not got["ok"]:
        task.fail(" ".join(got["said"]))
    task.succeed(walk_ready(number, got["said"]), collapsable=False)
    return f"The walk of {number} is served."


@celery_app.task(bind=True, throws=(TaskFailed,), soft_time_limit=sakiori.BUILD_SECONDS,
                 time_limit=sakiori.BUILD_SECONDS + 600)
def sakiori_build_task(self, papers):
    """Build Sakiori's walks, with notes and answers written for `papers` (numbers; none: those still missing)."""
    got = sakiori.build(list(papers or []))
    if not got["ok"]:
        raise TaskFailed(" ".join(got["said"]))
    return " ".join(["Sakiori's walks are served.", *got["said"]])


def queue(base_doi: str, screening=None) -> str:
    """Queue the walk of a paper just published (base_doi like neurolibre.00052), and say so in a line for the
    publication's comment; a walk that cannot be queued is said there, and the publication goes on. With the
    publication's screening, the walk reports on the review issue in a comment of its own when it is served."""
    number = base_doi.rsplit(".", 1)[-1]
    try:
        if screening is not None:
            sakiori_walk_task.apply_async(args=[dict(screening.to_dict(), comment_id=None, task_name="SAKIORI WALK")])
        else:
            sakiori_build_task.delay([number])
    except Exception as e:
        return f"⚠️ Sakiori's walk of this paper was not queued: {e}"
    return f"🧵 Sakiori is weaving this paper's walk through the collection: {sakiori.address(number)}"

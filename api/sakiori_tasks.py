"""Sakiori's build (sakiori.py) as a celery task: queued after a paper is published to the CDN, for that paper, and by
POST /api/sakiori/build, for one paper or the whole collection.

The celery module imports this one at its end, which registers the task on its app; the publication step calls
queue() for the line it adds to its comment.
"""

import sakiori
from neurolibre_celery_tasks import celery_app
from task_outcome import TaskFailed


@celery_app.task(bind=True, throws=(TaskFailed,), soft_time_limit=sakiori.BUILD_SECONDS,
                 time_limit=sakiori.BUILD_SECONDS + 600)
def sakiori_build_task(self, papers):
    """Build Sakiori's walks, with notes and answers written for `papers` (numbers; none: those still missing)."""
    got = sakiori.build(list(papers or []))
    if not got["ok"]:
        raise TaskFailed(" ".join(got["said"]))
    return " ".join(["Sakiori's walks are served.", *got["said"]])


def queue(base_doi: str) -> str:
    """Queue the walk of a paper just published (base_doi like neurolibre.00052), and say so in a line for the
    publication's comment; a walk that cannot be queued is said there, and the publication goes on."""
    number = base_doi.rsplit(".", 1)[-1]
    try:
        sakiori_build_task.delay([number])
    except Exception as e:
        return f"⚠️ Sakiori's walk of this paper was not queued: {e}"
    return f"🧵 Sakiori is weaving this paper's walk through the collection: {sakiori.address(number)}"

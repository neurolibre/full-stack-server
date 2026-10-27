"""The texts the weft tasks post on a review issue, in one place so they are easy to edit.

A comment is read by the paper's authors, who may never have heard of a weft: it says what the description of
the paper's data is, why they are asked, and what to do. "weft" appears only in the commands the bot answers to
(`@roboneuro weft check`) and in file names. Failures are one or two short sentences. No em dashes.
"""

import re
from typing import List, Optional

from weft_steps import lower_first

DESCRIBING_URL = "https://cdn.evidencepub.io/terms/describing/"
TERMS_URL = "https://cdn.evidencepub.io/terms/"


def heading(step: str) -> str:
    return f"### Description of this paper's data: {step}"


INTRO = ("NeuroLibre publishes, with each paper, a description of its data: what each file holds, what one row "
         "stands for, what every column means and in which unit, and which shared term it corresponds to. The "
         "description makes the data findable and reusable, and it is published next to the paper as "
         "[FAIR²](https://fair2.ai) and [Croissant](https://mlcommons.org/croissant/) documents.")

PROPOSAL = ("A first draft was made with a language model from the files themselves. It is a proposal: nothing is "
            "published until the authors have reviewed it and an editor or reviewer has confirmed it.")

REPLACED = "It replaces the earlier draft on the server."

EDITOR = ("Write `@roboneuro weft check at <comment id>` to check the authors' reply, and "
          "`@roboneuro weft confirm at <comment id>` to confirm it; the id is the number at the end of the reply's "
          "link. The confirmation is recorded under the name of the person who wrote that reply. An editor can write "
          "`@roboneuro weft confirm` without `at` to confirm the draft as it stands. To confirm only some columns, "
          "add `fields <name>, <name>`.")


# Sentences that come from the steps and the commands.

# "weft" as a word, but not in a command (`@roboneuro weft check`, evidentron.weft) or a file name (x.weft.yaml).
_WEFT = re.compile(r"(?<!roboneuro )(?<![\w.`/-])weft(s?)(?=['\s,;:)!?]|\.(?!\w)|$)")


def plain(text: str) -> str:
    """A sentence from a step or a command, with "weft" written as "description", since the comment explains
    the description and never the word."""
    return _WEFT.sub(r"description\1", str(text))


def end(text: str) -> str:
    """The sentence without its final full stop, so it can be placed inside another one."""
    return str(text).strip().rstrip(".").rstrip()


def inner(text: str) -> str:
    """A sentence from a step, to follow a colon: plain words, first letter lower-cased, no final full stop."""
    return lower_first(end(plain(text)))


def bullets(items: List[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def details(summary: str, body: str) -> str:
    return f"<details><summary>{summary}</summary>\n\n{body}\n\n</details>"


def listed(lead: str, items: List[str], after: str = "") -> str:
    """One sentence when there is one item ("lead: item."), else the lead and the items as a list."""
    items = [plain(i) for i in items]
    if len(items) == 1:
        text = f"{lead}: {inner(items[0])}."
    else:
        text = f"{lead}:\n" + bullets(items)
        after = "\n\n" + after if after else ""
        return text + after
    return text + (" " + after if after else "")


# The draft.

def terms_sentence(count: Optional[int], labels: Optional[List[str]], failure: Optional[str]) -> Optional[str]:
    """What the term linking did: the labels of the terms when they could be read, the count when they could
    not, the reason when it failed, and nothing when no term was linked."""
    if count is None:
        return f"The paper's text could not be linked to shared terms: {inner(failure or 'no reason was given')}."
    if labels:
        count = len(labels)
    if not count:
        return None
    noun = "shared term" if count == 1 else "shared terms"
    if labels:
        return f"The paper's text was linked to {count} {noun}: {', '.join(labels)}."
    return f"The paper's text was linked to {count} {noun}."


def draft_comment(summary: List[str], terms: Optional[str], example: Optional[str], draft: str,
                  problems: List[str], warnings: List[str], replaced: bool = False) -> str:
    """The comment for a draft. `example` is a yaml block of one real entry of this draft (without its fences),
    `draft` the inside of the collapsed block that shows the draft."""
    parts = [heading("draft"), INTRO, PROPOSAL + (" " + REPLACED if replaced else "")]
    if summary:
        parts.append("**What the draft found**\n" + bullets(summary))
    if terms:
        parts.append(terms)
    first = "Fix the problems listed below, then put" if problems else "If anything is wrong or missing, put"
    step2 = (f"2. Reply to this issue. {first} the entries to change inside a `yaml` code block (only the entries "
             "that change; an entry with `remove: true` is dropped)")
    if example:
        block = "\n".join(("   " + line) if line else "" for line in ["```yaml", *example.rstrip("\n").split("\n"), "```"])
        step2 += ", for example:\n\n" + block + "\n\n   "
    else:
        step2 += ". "
    step2 += (f"To change a column's term, pick an id from the [list of terms]({TERMS_URL}); if none fits, propose "
              "one under `new_terms` as the draft does, and the check will say if a close one already exists.")
    parts.append("\n".join([
        "**What we ask of the authors**",
        "1. Open \"The draft\" below and read each file's and column's description, type, unit and term. The "
        f"allowed types and the list of terms are on the [Describing data]({DESCRIBING_URL}) page.",
        step2,
        "3. If the draft is right as it is, reply saying so.",
    ]))
    parts.append("**What the editor or reviewer does**\n" + EDITOR)
    parts.append(details("The draft", draft))
    if problems:
        parts.append(details("Problems the check found", bullets([plain(p) for p in problems])))
    if warnings:
        parts.append(details("Warnings from the check", bullets([plain(w) for w in warnings])))
    return "\n\n".join(parts)


def too_long(file_name: str) -> str:
    return f"The draft is too long for a comment; ask an editor for the file `{file_name}` on the server."


# The check.

def applied(comment_id) -> str:
    if comment_id is None:
        return "No corrections were given, so the description was checked as it is."
    return f"The corrections from comment {comment_id} were applied."


NOT_LISTED_AFTER_CORRECTIONS = ("The check's warnings are not listed after corrections; the draft comment "
                                "lists them.")


def check_comment(corrections_id, errors: List[str], warnings: List[str], confirm_at=None) -> str:
    """The comment for a check: whether it passed, and the errors or warnings. `corrections_id` is the comment
    whose corrections were applied, `confirm_at` the comment a confirmation would name. After corrections, the
    check gives only the notes on applying them, not its own warnings, so the comment says so and lists the
    notes under their own title."""
    parts = [heading("check"), applied(corrections_id)]
    warnings = [plain(w) for w in warnings]
    title = "Notes on the corrections" if corrections_id is not None else "Warnings"
    if errors:
        parts.append("The description does not pass the check yet. Fix these, then ask for `@roboneuro weft check` "
                     "again:\n" + bullets([plain(e) for e in errors]))
        if warnings:
            parts.append(details(title, bullets(warnings)))
        return "\n\n".join(parts)
    if corrections_id is not None:
        noted = NOT_LISTED_AFTER_CORRECTIONS
    elif not warnings:
        noted = "There are no warnings."
    elif len(warnings) == 1:
        noted = "There is 1 warning, listed below; it does not block confirmation."
    else:
        noted = f"There are {len(warnings)} warnings, listed below; they do not block confirmation."
    parts.append(f"The description passes the check. {noted}")
    command = f"`@roboneuro weft confirm at {confirm_at}`" if confirm_at is not None else "`@roboneuro weft confirm`"
    parts.append(f"An editor or reviewer confirms it with {command}.")
    if warnings:
        parts.append(details(title, bullets(warnings)))
    return "\n\n".join(parts)


def check_unreadable(corrections_id, sentence: str) -> str:
    return "\n\n".join([heading("check"), applied(corrections_id), plain(sentence)])


# The confirmation.

def confirm_comment(login: str, reply_id, version: str, addresses: dict, term_links: List[str],
                    note: Optional[str] = None) -> str:
    how = f" (reply in comment {reply_id})" if reply_id is not None else " as the draft stood"
    lines = [f"{label}: {addresses[key]}" for label, key in
             (("FAIR²", "fair2"), ("Croissant", "croissant"), ("Metadata", "metadata")) if addresses.get(key)]
    if term_links:
        lines.append("Terms it uses: " + ", ".join(term_links))
    text = (f"Confirmed by @{login}{how}. The description is now version {version} and is published with the "
            "paper:\n\n" + bullets(lines))
    parts = [heading("confirmed"), text]
    if note:
        parts.append(note)
    return "\n\n".join(parts)


def cdn_not_rebuilt(reason: str) -> str:
    return f"The CDN could not be rebuilt ({reason}); the documents appear after the next rebuild."


# Failures: the first sentence states the fact, the second what to do.

def data_missing(project_name: str) -> str:
    return (f"The paper's data is not at its place on the server ({project_name}), so there is nothing to describe. "
            "Ask an editor to check the data transfer.")


NO_PUBLISHED_BUILD = ("The paper has no published build yet, so its code cannot be read. Run "
                      "`@roboneuro production sync myst` first.")

ALREADY_CONFIRMED = ("This paper's data description is already confirmed. To change it, reply with the "
                     "entries to change in a `yaml` code block and ask an editor or reviewer for "
                     "`@roboneuro weft confirm at <comment id>`; to start over, an editor removes the confirmed "
                     "description on the server.")

NO_DRAFT = ("There is no draft of this paper's data description on the server yet. Ask for "
            "`@roboneuro weft draft` first.")

CONFIRMED_WITHOUT_PROFILES = ("The confirmed description of this paper's data is on the server without its "
                              "profiles, so it cannot be checked. An editor restores the profiles beside it, or "
                              "removes the confirmed description and asks for `@roboneuro weft draft` again.")

NO_ARCHIVE_DOI = ("The data archive has no DOI yet, so the description cannot be confirmed. Confirm it after the "
                  "data has been deposited (`@roboneuro zenodo publish`, or set the archive DOI in the issue).")

NO_COMMAND_COMMENT = ("The description cannot be confirmed without the comment that asked for it, since that "
                      "comment says who confirms it.")


def not_drafted(problems: List[str]) -> str:
    reasons = [inner(p) for p in problems if str(p).strip()] or ["no draft was written"]
    return f"The description could not be drafted: {'; '.join(reasons)}."


def unreadable_block(comment_id, where: str) -> str:
    return (f"The `yaml` block in comment {comment_id} could not be read ({where}), so nothing was applied. Fix the "
            "block and ask again.")


def comment_not_found(comment_id) -> str:
    return f"Comment {comment_id} is not on this review issue, so its corrections cannot be read."


def not_allowed(author: Optional[str], editor: Optional[str], comment_id, login: str, corrections: bool) -> str:
    what = f"the corrections in comment {comment_id} are" if corrections else f"comment {comment_id} is"
    return (f"Only the paper's author ({'@' + author if author else 'not named on the issue'}) or the editor "
            f"({'@' + editor if editor else 'not named on the issue'}) can confirm; {what} by @{login}.")


def github_unreachable(what: str, reason: str) -> str:
    return f"GitHub could not be reached for {what} ({reason}). Ask again in a minute."


def store_busy(minutes: str) -> str:
    return (f"Another task kept the paper store busy for more than {minutes} minutes, so nothing was done. Ask "
            "again in a while.")


def confirmed_not_published(version: str, errors: List[str]) -> str:
    return listed(f"The description was confirmed at version {version} but not published", errors,
                  "Asking for `@roboneuro weft confirm` again is safe once this is fixed.")


def not_confirmed(errors: List[str]) -> str:
    return listed("The description was not confirmed", errors)

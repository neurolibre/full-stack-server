"""The texts the weft tasks post on a review issue, in one place so they are easy to edit.

A comment is read by the paper's authors, who may never have heard of a weft: it says what the description of
the paper's data is, why they are asked, and what to do. "weft" appears only in the commands the bot answers to
(`@roboneuro weft check`) and in file names. Failures are one or two short sentences. No em dashes.
"""

import re
from typing import List, Optional

import yaml

from weft_steps import CDN_URL

DESCRIBING_URL = f"{CDN_URL}/terms/describing/"
TERMS_URL = f"{CDN_URL}/terms/"


def heading(step: str) -> str:
    return f"### Description of this paper's data: {step}"


INTRO = ("NeuroLibre publishes, with each paper, a description of its data: what each file holds, what one row "
         "stands for, what every column means and in which unit, and which shared term it corresponds to. The "
         "description makes the data findable and reusable, and it is published next to the paper as "
         "[FAIR²](https://fair2.ai) and [Croissant](https://mlcommons.org/croissant/) documents.")

PROPOSAL = ("A first draft was made with a language model from the files themselves. It is a proposal: nothing is "
            "published until it is confirmed.")

REPLACED = "It replaces the earlier draft on the server."

# Who confirms, said the same way everywhere. The code's rule: the confirmer is the author of the comment the
# corrections come from (the comment `at` names, else the command's own), who must be the paper's author or the
# issue's editor.
WHO_CONFIRMS = ("The paper's author or the issue's editor confirms, by replying; an editor or reviewer records an "
                "author's reply with `@roboneuro weft confirm at <comment id>`.")

CHECKING = ("Anyone can write `@roboneuro weft check at <comment id>` to check a reply with its corrections applied; "
            "the id is the number at the end of the reply's link. " + WHO_CONFIRMS + " To confirm only some columns, "
            "add `fields <name>, <name>`.")

RESET = "an editor runs the reset command on the server"


# Sentences that come from the steps and the commands.

# "weft" as a word, but not in a command (`@roboneuro weft check`, evidentron.weft) or a file name (x.weft.yaml).
_WEFT = re.compile(r"(?<!roboneuro )(?<![\w.`/-])weft(s?)(?=['\s,;:)!?]|\.(?!\w)|$)")


def plain(text: str) -> str:
    """A sentence from a step or a command, with "weft" written as "description", since the comment explains
    the description and never the word."""
    return _WEFT.sub(r"description\1", str(text))


def lower_first(text: str) -> str:
    """The text with its first letter lower-cased, unless its first word is an acronym or a name written with
    capitals inside it (NOAA, WIPO, iPhone): only when the rest of the first word has no capital letter."""
    words = text.split(maxsplit=1)
    if not words or any(c.isupper() for c in words[0][1:]):
        return text
    return text[:1].lower() + text[1:]


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

def _yaml_scalar(value) -> str:
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10 ** 9)
    return text.split("\n...")[0].strip()


def example(weft_text: str) -> Optional[str]:
    """A yaml block that changes one real entry of this draft: the first table's first field, else the first
    collection's first variable, with the keys an author reviews; None when the draft has neither. The weft's
    shape is Evidentron's published format, so reading its first field here is reading a public document."""
    try:
        weft = yaml.safe_load(weft_text)
    except yaml.YAMLError:
        return None
    entries = [e for e in (weft.get("files") or [] if isinstance(weft, dict) else []) if isinstance(e, dict)]
    for owner_keys, items_key in ((("file", "file_pattern"), "fields"), (("collection",), "variables")):
        for entry in entries:
            owner = next((k for k in owner_keys if entry.get(k)), None)
            items = [i for i in entry.get(items_key) or [] if isinstance(i, dict)]
            if owner is None or "collection" in entry and owner != "collection" or not items:
                continue
            item = items[0]
            name_key = "name" if item.get("name") is not None else "pattern"
            if item.get(name_key) is None:
                continue
            lines = ["files:", f"  - {owner}: {_yaml_scalar(entry[owner])}", f"    {items_key}:",
                     f"      - {name_key}: {_yaml_scalar(item[name_key])}"]
            term = item.get("term", item.get("concept"))
            for key, value in (("description", item.get("description")), ("datatype", item.get("datatype")),
                               ("evidence_type", item.get("evidence_type")), ("term", term)):
                if value is not None:
                    lines.append(f"        {key}: {_yaml_scalar(value)}")
            return "\n".join(lines)
    return None


def draft_block(weft_text: str) -> str:
    """The draft as a fenced yaml block, with a fence longer than any run of backticks in it."""
    fence = "```"
    while fence in weft_text:
        fence += "`"
    return f"{fence}yaml\n" + weft_text.rstrip("\n") + f"\n{fence}"


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
    parts.append("**Checking and confirming**\n" + CHECKING)
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


def check_comment(corrections_id, errors: List[str], warnings: List[str], confirm_at=None,
                  notes: Optional[List[str]] = None) -> str:
    """The comment for a check: whether it passed, then its errors, its warnings and its notes (with corrections,
    the notes say how they were applied). `corrections_id` is the comment whose corrections were applied,
    `confirm_at` the comment a confirmation would name."""
    parts = [heading("check"), applied(corrections_id)]
    warnings = [plain(w) for w in warnings]
    notes = [plain(n) for n in notes or []]
    lists = []
    if warnings:
        lists.append(details("Warnings", bullets(warnings)))
    if notes:
        lists.append(details("Notes on the corrections" if corrections_id is not None else "Notes", bullets(notes)))
    if errors:
        parts.append("The description does not pass the check yet. Fix these, then ask for `@roboneuro weft check` "
                     "again:\n" + bullets([plain(e) for e in errors]))
        return "\n\n".join(parts + lists)
    if not warnings:
        noted = "There are no warnings."
    elif len(warnings) == 1:
        noted = "There is 1 warning, listed below; it does not block confirmation."
    else:
        noted = f"There are {len(warnings)} warnings, listed below; they do not block confirmation."
    parts.append(f"The description passes the check. {noted}")
    parts.append(WHO_CONFIRMS + (f" For this reply, that is `@roboneuro weft confirm at {confirm_at}`."
                                 if confirm_at is not None else ""))
    return "\n\n".join(parts + lists)


def check_unreadable(corrections_id, sentence: str) -> str:
    return "\n\n".join([heading("check"), applied(corrections_id), plain(sentence)])


# The confirmation.

def term_links(term_ids: List[str], terms_url: Optional[str]) -> List[str]:
    """Each term as a link to its page, under the address publish gave for the terms; the ids alone when it gave
    none."""
    if not terms_url:
        return [f"`{t}`" for t in term_ids]
    base = terms_url if terms_url.endswith("/") else terms_url + "/"
    return [f"[`{t}`]({base}{t}/)" for t in term_ids]


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


def data_folder_unknown(reason: Optional[str]) -> str:
    return "The paper's data folder could not be found on the server" + (f": {end(reason)}." if reason else ".")


def no_backend(key: str) -> str:
    return (f"The server's Evidentron config names no `{key}` backend under `weft: backend:`, so nothing was done. "
            "Ask an editor to set it.")


def cdn_not_rebuilt(reason: str) -> str:
    return f"The CDN could not be rebuilt ({reason}); the documents appear after the next rebuild."


# Failures: the first sentence states the fact, the second what to do.

def data_missing(project_name: str) -> str:
    return (f"The paper's data is not at its place on the server ({project_name}), so there is nothing to describe. "
            "Ask an editor to check the data transfer.")


NO_PUBLISHED_BUILD = ("The paper has no published build yet, so its code cannot be read. Run "
                      "`@roboneuro production sync myst` first.")

ALREADY_CONFIRMED = ("This paper's data description is already confirmed. To change it, reply with the "
                     "entries to change in a `yaml` code block. " + WHO_CONFIRMS + " To start over, " + RESET + ".")

NO_DRAFT = ("There is no draft of this paper's data description on the server yet. Ask for "
            "`@roboneuro weft draft` first.")

CONFIRMED_WITHOUT_PROFILES = ("The confirmed description of this paper's data is on the server without its "
                              "profiles, so it cannot be checked. An editor restores the profiles beside it, or "
                              "runs the reset command on the server and asks for `@roboneuro weft draft` again.")

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

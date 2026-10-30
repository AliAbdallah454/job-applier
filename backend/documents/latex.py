"""LaTeX CV adapter: parse a Jake-style resume and apply structured edits in place.

Only the recognized macros are read and rewritten, by their exact location in the source;
everything else (preamble, layout, custom commands, comments) is kept byte for byte.
"""

import re
from dataclasses import dataclass, field

from backend.documents.errors import DocumentError
from backend.documents.models import (
    NEW_BULLET,
    CvBullet,
    CvDocument,
    CvEntry,
    CvSection,
    CvSkillGroup,
    TailoredCv,
)

HEADINGS = {"resumeSubheading": 4, "resumeSubSubheading": 2, "resumeProjectHeading": 2}
MACRO = re.compile(
    r"\\(resumeSubheading|resumeSubSubheading|resumeProjectHeading|resumeItem)(?![A-Za-z])"
)
SECTION = re.compile(r"\\section\*?\s*\{")
SKILL_GROUP = re.compile(r"\\textbf\s*\{")


def escape(value):
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in str(value)).replace(
        "\n", " "
    )


def plain(latex):
    """Readable text of a LaTeX fragment, as shown to the model."""
    text = latex.replace(r"\$", "\0")
    text = re.sub(r"\\href\s*\{[^{}]*\}", "", text)
    text = text.replace("$|$", "|").replace("$", "").replace("\0", "$")
    text = text.replace(r"\textasciitilde{}", "~").replace(r"\textasciicircum{}", "^")
    text = text.replace(r"\textbackslash{}", "\\").replace("---", "—").replace("--", "–")
    text = re.sub(r"\\([%&#_{}])", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?\s*", "", text)
    text = re.sub(r"(?<!\\)[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def mask_comments(source):
    """Blank out comments (same length) so macros inside them are not parsed."""
    return re.sub(r"(?<!\\)%[^\n]*", lambda match: " " * len(match[0]), source)


def read_group(masked, position):
    """Return the span inside the brace group starting at or after `position`."""
    while position < len(masked) and masked[position].isspace():
        position += 1
    if position >= len(masked) or masked[position] != "{":
        raise DocumentError("CV has a resume macro with missing arguments")
    depth = 0
    for index in range(position, len(masked)):
        character = masked[index]
        if character == "{" and masked[index - 1] != "\\":
            depth += 1
        elif character == "}" and masked[index - 1] != "\\":
            depth -= 1
            if depth == 0:
                return position + 1, index
    raise DocumentError("CV has unbalanced braces")


@dataclass
class Span:
    start: int
    end: int


@dataclass
class Bullet(Span):
    id: str
    text: str


@dataclass
class Entry(Span):
    id: str
    title: str
    bullets: list[Bullet] = field(default_factory=list)


@dataclass
class SkillGroup:
    id: str
    name: str
    start: int
    end: int
    skills: list[tuple[str, str]]  # (plain text, original LaTeX)


@dataclass
class Section:
    id: str
    name: str
    entries: list[Entry] = field(default_factory=list)
    skill_groups: list[SkillGroup] = field(default_factory=list)


class LatexCv:
    def __init__(self, source):
        if "\\documentclass" not in source or "\\end{document}" not in source:
            raise DocumentError("Provide a complete LaTeX CV document")
        self.source = source
        masked = mask_comments(source)
        starts = [match.start() for match in SECTION.finditer(masked)]
        ends = [*starts[1:], masked.rindex("\\end{document}")]
        self.sections = [
            self._section(masked, f"s{number}", start, end)
            for number, (start, end) in enumerate(zip(starts, ends, strict=False), 1)
        ]
        if not any(section.entries for section in self.sections):
            raise DocumentError(
                "CV has no recognized entries; use \\resumeSubheading or "
                "\\resumeProjectHeading with \\resumeItem bullets"
            )

    def _section(self, masked, section_id, start, end):
        name_start, name_end = read_group(masked, masked.index("{", start))
        section = Section(section_id, plain(self.source[name_start:name_end]))
        position = name_end
        for match in MACRO.finditer(masked, position, end):
            if match.start() < position:
                continue
            macro = match[1]
            spans = []
            position = match.end()
            for _ in range(HEADINGS.get(macro, 1)):
                spans.append(read_group(masked, position))
                position = spans[-1][1] + 1
            if macro in HEADINGS:
                title = " | ".join(filter(None, (plain(self.source[a:b]) for a, b in spans)))
                entry_id = f"{section_id}.e{len(section.entries) + 1}"
                section.entries.append(Entry(match.start(), position, entry_id, title))
            elif section.entries:
                entry = section.entries[-1]
                bullet_id = f"{entry.id}.b{len(entry.bullets) + 1}"
                text = plain(self.source[spans[0][0] : spans[0][1]])
                entry.bullets.append(Bullet(match.start(), position, bullet_id, text))
                entry.end = position
        for entry in section.entries:
            close = re.compile(r"\s*\\resumeItemListEnd(?![A-Za-z])").match(masked, entry.end)
            if entry.bullets and close:
                entry.end = close.end()
        if not section.entries:
            section.skill_groups = self._skill_groups(masked, section_id, name_end, end)
        return section

    def _skill_groups(self, masked, section_id, start, end):
        groups = []
        for match in SKILL_GROUP.finditer(masked, start, end):
            name_start, name_end = read_group(masked, match.end() - 1)
            following = re.compile(r"\s*\{\s*:\s*").match(masked, name_end + 1)
            if not following:
                continue
            list_start = following.end()
            list_end = read_group(masked, following.start() + following[0].index("{"))[1]
            skills, depth, item_start = [], 0, list_start
            for index in range(list_start, list_end + 1):
                character = masked[index] if index < list_end else ","
                depth += {"{": 1, "}": -1}.get(character, 0)
                if character == "," and depth == 0:
                    raw = self.source[item_start:index].strip()
                    if raw:
                        skills.append((plain(raw), raw))
                    item_start = index + 1
            stripped_end = list_start + len(self.source[list_start:list_end].rstrip())
            if skills:
                group_id = f"{section_id}.g{len(groups) + 1}"
                name = plain(self.source[name_start:name_end])
                groups.append(SkillGroup(group_id, name, list_start, stripped_end, skills))
        return groups

    def document(self):
        return CvDocument(
            sections=[
                CvSection(
                    id=section.id,
                    name=section.name,
                    entries=[
                        CvEntry(
                            id=entry.id,
                            title=entry.title,
                            bullets=[CvBullet(id=b.id, text=b.text) for b in entry.bullets],
                        )
                        for entry in section.entries
                    ],
                    skill_groups=[
                        CvSkillGroup(id=g.id, name=g.name, skills=[s for s, _ in g.skills])
                        for g in section.skill_groups
                    ],
                )
                for section in self.sections
                if section.entries or section.skill_groups
            ]
        )

    def apply(self, edits: TailoredCv):
        """Return the edited source; validate the edits against this CV first."""
        replacements = []
        sections = {section.id: section for section in self.sections}
        groups = {g.id: g for section in self.sections for g in section.skill_groups}

        for edit in edits.sections:
            entries = sections[edit.section_id].entries
            by_id = {entry.id: entry for entry in entries}
            rendered = [self._entry(by_id[e.entry_id], e.bullets) for e in edit.entries]
            replacements.append((entries[0].start, entries[-1].end, self._join(entries, rendered)))

        for edit in edits.skill_groups:
            group = groups[edit.group_id]
            originals = {text.casefold(): raw for text, raw in group.skills}
            items = [originals.get(skill.casefold(), escape(skill)) for skill in edit.skills]
            replacements.append((group.start, group.end, ", ".join(items)))

        source = self.source
        for start, end, text in sorted(replacements, reverse=True):
            source = source[:start] + text + source[end:]
        return source

    def _entry(self, entry, bullet_edits):
        if not entry.bullets:
            return self.source[entry.start : entry.end]
        originals = {bullet.id: bullet for bullet in entry.bullets}
        rendered = []
        for edit in bullet_edits:
            original = originals.get(edit.source_id)
            if original and edit.text == original.text:
                rendered.append(self.source[original.start : original.end])
            else:
                rendered.append(r"\resumeItem{" + escape(edit.text) + "}")
        first, last = entry.bullets[0], entry.bullets[-1]
        return (
            self.source[entry.start : first.start]
            + self._join(entry.bullets, rendered)
            + self.source[last.end : entry.end]
        )

    def _join(self, slots, rendered):
        """Join new items with the original whitespace between consecutive slots."""
        separators = [self.source[a.end : b.start] for a, b in zip(slots, slots[1:], strict=False)]
        if not separators:
            line_start = self.source.rfind("\n", 0, slots[0].start) + 1
            indent = self.source[line_start : slots[0].start]
            separators = ["\n" + (indent if not indent.strip() else "")]
        return "".join(
            item
            + (separators[min(index, len(separators) - 1)] if index < len(rendered) - 1 else "")
            for index, item in enumerate(rendered)
        )

    def changes(self, edits: TailoredCv):
        """A reviewable summary of what the edits change, stored next to the PDF."""
        entries = {e.id: e for section in self.sections for e in section.entries}
        summary = {"rewritten": [], "added": [], "dropped_entries": [], "dropped_bullets": []}
        for edit in edits.sections:
            kept = {e.entry_id for e in edit.entries}
            for entry in (e for e in entries.values() if e.id.startswith(edit.section_id + ".")):
                if entry.id not in kept:
                    summary["dropped_entries"].append(entry.title)
            for entry_edit in edit.entries:
                entry = entries[entry_edit.entry_id]
                originals = {b.id: b for b in entry.bullets}
                used = set()
                for bullet in entry_edit.bullets:
                    original = originals.get(bullet.source_id)
                    if bullet.source_id == NEW_BULLET:
                        summary["added"].append({"entry": entry.title, "after": bullet.text})
                    elif original.text != bullet.text:
                        summary["rewritten"].append(
                            {"entry": entry.title, "before": original.text, "after": bullet.text}
                        )
                    used.add(bullet.source_id)
                summary["dropped_bullets"].extend(
                    {"entry": entry.title, "before": b.text}
                    for b in entry.bullets
                    if b.id not in used
                )
        return summary

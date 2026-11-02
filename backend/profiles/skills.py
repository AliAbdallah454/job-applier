"""Build the skill enums and the Skills model from the catalogue; add skills in skill_catalogue.py.

The web app shows the catalogue through /api/options and gets its types from the OpenAPI schema,
so it never holds its own copy.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from backend.profiles.skill_catalogue import CATEGORIES, OTHER_NAMES


class SkillGroups(BaseModel):
    """The user's skills by category; the field order is the order the web app shows."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def no_repeats(self):
        for key in type(self).model_fields:
            skills = getattr(self, key)
            if len(skills) != len(set(skills)):
                raise ValueError(f"{key} lists the same skill twice")
        return self


def skill_enum(category):
    """One enum per category, so a skill is only valid in its own category."""
    name = category.key.title().replace("_", "") + "Skill"
    return StrEnum(name, {skill: skill for skill in category.skills})


Skills = create_model(
    "Skills",
    __base__=SkillGroups,
    **{
        category.key: (
            list[skill_enum(category)],
            Field(default_factory=list, title=category.label),
        )
        for category in CATEGORIES
    },
)


class SkillCategory(BaseModel):
    """One group of the catalogue, as offered to the web app."""

    key: str
    label: str
    skills: list[str]


SKILL_CATEGORIES = [
    SkillCategory(key=category.key, label=category.label, skills=category.skills)
    for category in CATEGORIES
]


def known_names():
    """Each spelling a saved profile may use, in lower case, with its (category, skill) pair."""
    names = {
        skill.casefold(): (category.key, skill)
        for category in CATEGORIES
        for skill in category.skills
    }
    for key, skill in list(names.values()):
        if skill.startswith("AWS "):
            service = skill.removeprefix("AWS ").casefold()
            names |= {service: (key, skill), f"amazon {service}": (key, skill)}
    return names | {other.casefold(): names[name.casefold()] for other, name in OTHER_NAMES.items()}


KNOWN_NAMES = known_names()


def catalogue_entries(name):
    """The (category, skill) pairs for a saved name; "C/C++" is two, and an unknown one none."""
    for parts in ([name], name.split("/")):
        entries = [KNOWN_NAMES.get(part.strip().casefold()) for part in parts]
        if all(entries):
            return entries
    return []

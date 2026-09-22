"""Conventional Commit parser with patch releases for dependency chores."""

from semantic_release.commit_parser.conventional.parser import ConventionalCommitParser
from semantic_release.enums import LevelBump


class DependencyReleaseParser(ConventionalCommitParser):
    """Keep standard release levels, adding only chore(deps) as a patch."""

    def create_parsed_message_result(self, match):
        result = super().create_parsed_message_result(match)
        if result.type == "chore" and result.scope == "deps" and result.bump == LevelBump.NO_RELEASE:
            return result._replace(bump=LevelBump.PATCH)
        return result

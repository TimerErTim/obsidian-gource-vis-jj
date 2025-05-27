from __future__ import annotations

from dataclasses import dataclass, field
import subprocess
import sys
import os
import re
from typing import Callable
from collections import defaultdict
import frontmatter
from textwrap import dedent
import argparse
from enum import Enum


class PathStrategy(Enum):
    """Strategy for generating paths from Obsidian notes in visualization"""
    TAGS_AND_FILENAME = "both"
    TAGS_ONLY = "tags"
    FILEPATH_ONLY = "file"
    CONFLICT_FREE = "conflict-free"

    def get_change_path_set(self, change: ChangeDescription) -> ChangePathSet:
        fn_map = {
            self.TAGS_AND_FILENAME: PathStrategy.tags_and_filename_paths,
            self.TAGS_ONLY: PathStrategy.tags_only_paths,
            self.FILEPATH_ONLY: PathStrategy.filepath_only_paths,
            self.CONFLICT_FREE: PathStrategy.conflict_free_paths,
        }
        convert_fn = fn_map[self]

        old_paths = convert_fn([(file_change.old_path, file_change.old_tags) for file_change in change.file_changes])
        new_paths = convert_fn([(file_change.new_path, file_change.new_tags) for file_change in change.file_changes])

        return ChangePathSet(old_paths, new_paths)

    @staticmethod
    def tags_and_filename_paths(pairs: list[tuple[str | None, list[str] | None]]) -> set[str]:
        paths = set()
        for path, tags in pairs:
            if tags is not None and len(tags) > 0:
                paths.update({
                    f"{tag}/{path.split('/')[-1]}" for tag in tags
                })
            elif path is not None:
                paths.add(path)
        return paths

    @staticmethod
    def tags_only_paths(pairs: list[tuple[str | None, list[str] | None]]) -> set[str]:
        paths = set()
        for path, tags in pairs:
            if tags is not None and len(tags) > 0:
                paths.update({
                    f"{tag}.md" for tag in tags
                })
            elif path is not None:
                paths.add(path)
        return paths

    @staticmethod
    def filepath_only_paths(pairs: list[tuple[str | None, list[str] | None]]) -> set[str]:
        paths = set()
        for path, tags in pairs:
            if path is not None:
                paths.add(path)
        return paths

    @staticmethod
    def conflict_free_paths(pairs: list[tuple[str | None, list[str] | None]]) -> set[str]:
        tag_to_with_path_alternative = dict()
        duplicates = set()
        paths = set()

        def insert_without_alternative(path: str):
            if path in paths:
                # Resolve the previous duplicate (which has to be a tag) with its alternative path
                paths.add(tag_to_with_path_alternative[path])
            else:
                paths.add(path)

        def insert_with_alternative(path: str, path_alternative: str):
            if path in paths:
                duplicates.add(path)
                insert_without_alternative(path_alternative)
            else:
                paths.add(path)
                tag_to_with_path_alternative[path] = path_alternative

        for path, tags in pairs:
            if tags is not None and len(tags) > 0 and path is not None:
                for tag in tags:
                    insert_with_alternative(f"{tag}.md", f"{tag}/{path.split('/')[-1]}")
            elif path is not None:
                insert_without_alternative(path)

        for duplicate in duplicates:
            paths.remove(duplicate)
            paths.add(tag_to_with_path_alternative[duplicate])

        return paths


@dataclass
class ChangePathSet:
    old_paths: set[str] = field(default_factory=lambda: set())
    new_paths: set[str] = field(default_factory=lambda: set())


@dataclass
class FileChange:
    old_path: str | None
    new_path: str | None
    old_tags: list[str] | None = None
    new_tags: list[str] | None = None


@dataclass
class ChangeDescription:
    change_id: str
    author: str
    timestamp: str
    file_changes: list[FileChange] = field(default_factory=lambda: [])


def get_jj_commits_and_file_path_changes(revset: str, ignore_working_copy: bool) -> list[ChangeDescription]:
    cmd_submission = ["jj", "log", "-r", revset, "-T", "builtin_log_oneline",
                      "--summary", "--no-graph"]
    if ignore_working_copy:
        cmd_submission.append("--ignore-working-copy")

    cmdpipe = subprocess.Popen(
        cmd_submission, stdout=subprocess.PIPE, text=True, bufsize=1)
    # output = subprocess.check_output(cmd_submission).decode()

    changes = []
    current_change: ChangeDescription | None = None

    # Regex for commit header
    change_header_re = re.compile(
        r'^([a-z]+)\s+(\w+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
    )
    change_filepath_re = re.compile(
        r'(.*)\{(.+) => (.+)\}(.*)'
    )

    for row in cmdpipe.stdout:
        row: str = row.strip()
        if not row:
            continue

        match = change_header_re.match(row)
        if match:
            if current_change:
                changes.append(current_change)

            change_id, author, timestamp = match.groups()
            date, time = timestamp.split(" ")
            current_change = ChangeDescription(
                change_id=change_id,
                author=author,
                timestamp=f"{date}T{time}Z"
            )
        elif current_change:
            op = row[0]
            path = row[2:]

            file_change: FileChange | None = None

            if op == 'A':
                file_change = FileChange(old_path=None, new_path=path)
            elif op == 'M':
                file_change = FileChange(old_path=path, new_path=path)
            elif op == 'D':
                file_change = FileChange(old_path=path, new_path=None)
            elif op == 'C':
                match = change_filepath_re.match(path)
                prefix, old_part, new_part, suffix = match.groups()
                file_change = FileChange(
                    old_path=None, new_path=prefix+new_part+suffix)
            elif op == 'R':
                match = change_filepath_re.match(path)
                prefix, old_part, new_part, suffix = match.groups()
                file_change = FileChange(
                    old_path=prefix+old_part+suffix,
                    new_path=prefix+new_part+suffix
                )

            if file_change:
                current_change.file_changes.append(file_change)

    cmdpipe.stdout.close()
    return changes


tags_at_revision_cache: dict[str, dict[str, list[str] | None]] = defaultdict(dict)

def get_tags_at_jj_revision(filepath: str, change_id: str) -> list[str] | None:
    if not filepath.endswith(".md"):
        return None

    if change_id in tags_at_revision_cache[filepath]:
        return tags_at_revision_cache[filepath][change_id]

    cmd_submission = ["jj", "file", "show", "-r",
                      change_id, f'"{filepath}"', "--ignore-working-copy"]
    file_content = subprocess.check_output(cmd_submission).decode()
    try:
        fm, content = frontmatter.parse(file_content)
        tags = fm.get("tags", None)
    except Exception as e:
        print("WARN:", filepath, "could not read tags due to ->", e, file=sys.stderr)
        return None
        
    tags_at_revision_cache[filepath][change_id] = tags
    return tags


def fill_changes_with_tags(
    changes: list[ChangeDescription],
    processed_clb: Callable[[ChangeDescription], None] | None = None
):
    prev_change_id: str | None = None
    for change in reversed(changes):
        change_id = change.change_id
        for file_change in change.file_changes:
            if file_change.old_path and prev_change_id:
                file_change.old_tags = get_tags_at_jj_revision(
                    file_change.old_path, prev_change_id)
            if file_change.new_path:
                file_change.new_tags = get_tags_at_jj_revision(
                    file_change.new_path, change_id)
            # print(f"Found tags for file '{file_change.new_path}' at rev '{change_id}':", file=sys.stderr)
            # print(file_change.new_tags, file=sys.stderr)

        if processed_clb is not None:
            processed_clb(change)

        prev_change_id = change_id


def print_gource_logs_for_change(change: ChangeDescription, path_strategy: PathStrategy):
    paths = path_strategy.get_change_path_set(change)

    modified = paths.new_paths.intersection(paths.old_paths)
    added = paths.new_paths.difference(paths.old_paths)
    deleted = paths.old_paths.difference(paths.new_paths)

    def make_line(change_type: str, change_path: str) -> str:
        return f"{change.timestamp}|{change.author}|{change_type}|{change_path}"

    for path in modified:
        print(make_line('M', path), flush=True)
    for path in added:
        print(make_line('A', path), flush=True)
    for path in deleted:
        print(make_line('D', path), flush=True)


def print_gource_custom_logs(changes: list[ChangeDescription], path_strategy: PathStrategy):
    for change in changes:
        print_gource_logs_for_change(change, path_strategy)

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate Gource visualization logs from Jujutsu repository with Obsidian vault support.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
                obsidian-gource-vis-jj /path/to/obsidian/vault
                obsidian-gource-vis-jj /vault -r 'main..@'
                obsidian-gource-vis-jj /vault -ps tags_only
        """),
    )
    
    parser.add_argument(
        "path",
        help="Path to the working directory (Obsidian vault with Jujutsu repository)"
    )
    
    parser.add_argument(
        "--revset",
        "-r",
        default="..@-",
        help="JJ revset to process (default: %(default)s)"
    )

    parser.add_argument(
        "--ignore-working-copy",
        action="store_true",
        default=False,
        help="Ignore working copy when reading JJ log"
    )
    
    parser.add_argument(
        "--path-strategy",
        "-ps",
        type=lambda x: PathStrategy(x).value,
        choices=[strategy.value for strategy in PathStrategy],
        default=PathStrategy.TAGS_AND_FILENAME.value,
        help=f"{PathStrategy.__doc__} (default: %(default)s; choices: %(choices)s)",
        metavar="PATH_STRATEGY"
    )
    
    args = parser.parse_args()
    args.__setattr__("path_strategy", PathStrategy(args.path_strategy))

    return args

def main():
    args = parse_arguments()
    
    # Change working directory to the specified path
    os.chdir(args.path)
    
    print(f"Processing revsets '{args.revset}' of vault at:", os.getcwd(), file=sys.stderr)
    print(f"Using path generation strategy: {args.path_strategy.value}", file=sys.stderr)

    raw_changes = get_jj_commits_and_file_path_changes(args.revset, args.ignore_working_copy)
    print(f"Found {len(raw_changes)} revisions...", file=sys.stderr)
    
    fill_changes_with_tags(raw_changes, processed_clb=lambda c: print_gource_logs_for_change(c, args.path_strategy))


if __name__ == "__main__":
    main()

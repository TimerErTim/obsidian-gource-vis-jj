import obsidiantools as otools
from dataclasses import dataclass, field
import subprocess
import sys
import os
import re
from typing import Callable
import frontmatter
from textwrap import dedent
import argparse
from enum import Enum


class PathGenPolicy(Enum):
    """Policy for generating file paths in gource visualization."""
    TAGS_AND_FILENAME = "tags_and_filename"
    TAGS_ONLY = "tags_only"
    FILEPATH_ONLY = "filepath_only"
    MIXTURE = "mixture"


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


def get_tags_at_jj_revision(filepath: str, change_id: str) -> list[str] | None:
    if not filepath.endswith(".md"):
        return None

    cmd_submission = ["jj", "file", "show", "-r",
                      change_id, f'"{filepath}"', "--ignore-working-copy"]
    file_content = subprocess.check_output(cmd_submission).decode()
    # print("Revision", change_id, "File:", filepath)
    # print(file_content, "\n")
    try:
        fm, content = frontmatter.parse(file_content)
        tags = fm.get("tags", None)
        return tags
    except Exception as e:
        print("WARN:", filepath, "could not read tags due to ->", e, file=sys.stderr)
        return None


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

        if processed_clb:
            processed_clb(change)

        prev_change_id = change_id


def print_gource_logs_for_change(change: ChangeDescription):
    for file_change in change.file_changes:
        old_paths = set()
        if file_change.old_tags:
            old_paths = {
                f"{x}/{file_change.old_path.split('/')[-1]}" for x in file_change.old_tags}
        elif file_change.old_path:
            old_paths = {file_change.old_path}

        new_paths = set()
        if file_change.new_tags:
            new_paths = {
                f"{x}/{file_change.new_path.split('/')[-1]}" for x in file_change.new_tags}
        elif file_change.new_path:
            new_paths = {file_change.new_path}

        modified = new_paths.intersection(old_paths)
        added = new_paths.difference(old_paths)
        deleted = old_paths.difference(new_paths)

        def make_line(change_type: str, change_path: str) -> str:
            return f"{change.timestamp}|{change.author}|{change_type}|{change_path}"

        for path in modified:
            print(make_line('M', path), flush=True)
        for path in added:
            print(make_line('A', path), flush=True)
        for path in deleted:
            print(make_line('D', path), flush=True)


def print_gource_custom_logs(changes: list[ChangeDescription]):
    for change in changes:
        print_gource_logs_for_change(change)

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate Gource visualization logs from Jujutsu repository with Obsidian vault support.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent("""\
            Examples:
                obsidian-gource-vis-jj /path/to/obsidian/vault
                obsidian-gource-vis-jj /vault -r 'main..@'
                obsidian-gource-vis-jj /vault --path-gen-policy tags_only
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
        "--path-gen-policy",
        type=lambda x: PathGenPolicy(x),
        choices=[policy.value for policy in PathGenPolicy],
        default=PathGenPolicy.TAGS_AND_FILENAME.value,
        help="Policy for generating file paths in visualization (default: %(default)s, choices: %(choices)s)",
        metavar="PATH_GEN_POLICY"
    )
    
    return parser.parse_args()

def main():
    args = parse_arguments()
    
    # Change working directory to the specified path
    os.chdir(args.path)
    
    print(f"Processing revsets '{args.revset}' of vault at:", os.getcwd(), file=sys.stderr)
    print(f"Using path generation policy: {args.path_gen_policy.value}", file=sys.stderr)

    raw_changes = get_jj_commits_and_file_path_changes(args.revset, args.ignore_working_copy)
    print(f"Found {len(raw_changes)} revisions...", file=sys.stderr)
    
    fill_changes_with_tags(raw_changes, processed_clb=lambda c: print_gource_logs_for_change(c))


if __name__ == "__main__":
    main()

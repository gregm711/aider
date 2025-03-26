# aider/cli/operator.py
import os
import sys
import argparse
import traceback
from pathlib import Path

# Use absolute imports from the package root
from aider.models import (
    Model,
    register_models,
    register_litellm_models,
)
from aider.repo import GitRepo, ANY_GIT_ERROR
from aider.main import (
    load_dotenv_files,
    generate_search_path_list,
)  # Leverage existing env loading and path searching

# Use relative import within the package
from ..operator.core import OperatorIO, run_aider_operator


def main():
    parser = argparse.ArgumentParser(
        description="Aider Operator: Automates Aider tasks via CLI."
    )
    parser.add_argument(
        "--request", required=True, help="The user's request/prompt for Aider."
    )
    parser.add_argument(
        "--dir", required=True, help="Path to the target project directory."
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Optional: Specific files to initially add to the chat (relative to --dir)."
        " If not provided, LCR determines files.",
    )
    parser.add_argument(
        "--lcr-model",
        default="claude-3-5-sonnet-20240620",
        help="Model name for Large Context Retrieval.",
    )
    parser.add_argument(
        "--architect-model",
        default="claude-3-5-sonnet-20240620",
        help="Model name for the Architect phase.",
    )
    parser.add_argument(
        "--editor-model",
        default="claude-3-haiku-20240307",
        help="Model name for the Editor phase.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output from Aider and the operator.",
    )
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="Disable git features (repo map, commits).",
        default=False,
    )
    parser.add_argument(
        "--no-auto-commits",
        action="store_true",
        help="Disable automatic commits by Aider.",
        default=False,
    )
    # Simplified config/env options for now
    parser.add_argument(
        "--env-file",
        help="Specify a .env file to load API keys and other settings from.",
    )
    parser.add_argument(
        "--model-settings-file",
        help="Specify a file with aider model settings for unknown models.",
    )
    parser.add_argument(
        "--model-metadata-file",
        help="Specify a file with context window and costs for unknown models.",
    )

    args = parser.parse_args()

    # Use stderr for CLI setup messages
    print("--- Starting Aider Operator CLI ---", file=sys.stderr)

    # --- Setup ---
    operator_io = OperatorIO(verbose=args.verbose)
    use_git = not args.no_git
    auto_commits = not args.no_auto_commits
    git_root = None
    repo = None
    coder_root = None
    initial_abs_files = []

    target_dir_path = Path(args.dir).resolve()
    if not target_dir_path.is_dir():
        operator_io.tool_error(f"Target directory not found: {args.dir}")
        return 1

    # Determine git root (if applicable) and coder root
    if use_git:
        # Attempt to find git repo at or above the target directory
        # Let potential errors (ANY_GIT_ERROR, FileNotFoundError) propagate
        repo_check_dummy = GitRepo(
            operator_io, git_root=str(target_dir_path), models=[]
        )
        git_root = repo_check_dummy.root
        operator_io.tool_output(f"Identified Git root: {git_root}")
        coder_root = git_root
    else:
        # If git is explicitly disabled or not found implicitly
        coder_root = str(target_dir_path)  # Fallback to target dir
        git_root = coder_root  # Use coder_root for consistency in file searching below
        operator_io.tool_output(f"Proceeding without git features.")

    operator_io.tool_output(f"Using coder root: {coder_root}")

    # Load .env files (using git_root hint)
    loaded_dotenvs = load_dotenv_files(git_root, args.env_file, operator_io.encoding)
    if args.verbose:
        for fname in loaded_dotenvs:
            operator_io.tool_output(f"Loaded {fname}")

    # Register models from standard locations + command line args
    # Let potential errors propagate
    register_models(
        git_root, args.model_settings_file, operator_io, verbose=args.verbose
    )
    register_litellm_models(
        git_root, args.model_metadata_file, operator_io, verbose=args.verbose
    )

    # Resolve initial files relative to coder_root
    for fname_rel in args.files:
        abs_path = Path(coder_root) / fname_rel
        # Check if it exists and is a file
        # Let potential errors (like permission errors) propagate
        if abs_path.is_file():
            initial_abs_files.append(str(abs_path.resolve()))
        else:
            operator_io.tool_warning(
                f"Initial path is not a file or does not exist: {abs_path}, skipping."
            )

    if initial_abs_files:
        operator_io.tool_output(
            f"Using specified initial files (resolved): {initial_abs_files}"
        )
    else:
        operator_io.tool_output(
            "No initial files specified or found, LCR will determine context."
        )

    # Initialize GitRepo if use_git is still True
    if use_git:
        # Let potential ANY_GIT_ERROR propagate
        repo = GitRepo(operator_io, git_root=coder_root, models=[])

    # Initialize Models
    # Let potential errors propagate
    lcr_model = Model(args.lcr_model)
    architect_model = Model(args.architect_model)
    editor_model = Model(args.editor_model)

    # --- Execute ---
    # Let potential errors propagate
    exit_code = run_aider_operator(
        user_prompt=args.request,
        initial_files=initial_abs_files,
        lcr_model=lcr_model,
        architect_model=architect_model,
        editor_model=editor_model,
        operator_io=operator_io,
        repo=repo,
        coder_root=coder_root,
        use_git=use_git,
        auto_commits=auto_commits,
        verbose=args.verbose,
    )
    return exit_code


# This guard is technically not needed for entry points but good practice
if __name__ == "__main__":
    # No top-level try/except, let errors exit the script
    sys.exit(main())

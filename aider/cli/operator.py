#!/usr/bin/env python
import os
import sys
import argparse
from pathlib import Path
import importlib_resources
import threading
import traceback

try:
    import git  # Try importing git
except ImportError:
    git = None

# Use absolute imports from the package root
from aider.models import (
    Model,
    register_models,
    register_litellm_models,
    print_matching_models,  # Added for potential future use/debugging
    ModelSettings,  # Added for map_tokens default logic
)
from aider.repo import GitRepo, ANY_GIT_ERROR
from aider.main import (
    load_dotenv_files,
    get_git_root,
    load_slow_imports,
    generate_search_path_list,
    register_models as main_register_models,  # Keep distinct name if needed later
    register_litellm_models as main_register_litellm_models,  # Keep distinct name
    make_new_repo,  # Added
    check_gitignore,  # Added
    setup_git,  # Added
    guessed_wrong_repo,  # Added
    sanity_check_repo,  # Added
)
from aider.args import default_env_file
from aider.io import InputOutput  # For compatibility if OperatorIO needs methods
from aider.utils import check_pip_install_extra  # For potential future checks

# Use relative import within the package
from ..operator.core import OperatorIO, run_aider_operator

# --- Constants ---
AIDER_CONF_FNAME = ".aider.conf.yml"  # Mirroring main.py
MODEL_SETTINGS_FNAME = ".aider.model.settings.yml"
MODEL_METADATA_FNAME = ".aider.model.metadata.json"
AIDERIGNORE_FNAME = ".aiderignore"


def main():
    # --- Initial Git Root Discovery (for config defaults) ---
    # Try to find git root early for default file paths, mirroring aider.main behavior
    try:
        initial_git_root_guess = get_git_root()
    except ImportError:
        initial_git_root_guess = None
        if git is not None:  # If the module object exists but failed internally
            print(
                "Warning: GitPython is installed but failed to find git root.",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"Warning: Error during initial git root discovery: {e}", file=sys.stderr)
        initial_git_root_guess = None

    parser = argparse.ArgumentParser(
        description="Aider Operator: Automates Aider tasks via CLI."
    )

    parser.add_argument(
        "--request", required=True, help="The user's request/prompt for Aider."
    )
    parser.add_argument(
        "files",
        metavar="FILE",
        nargs="*",
        default=[],
        help="Optional: Specific files to initially add to the chat (relative to determined root)."
        " If not provided, LCR determines files.",
    )
    parser.add_argument(  # Added from main.py relevant args
        "--read",
        metavar="READ_FILE",
        action="append",
        default=[],
        help="Add files or directories in read-only mode. Aider won't edit them.",
    )
    parser.add_argument(
        "--env-file",
        metavar="ENV_FILE",
        default=default_env_file(initial_git_root_guess),
        help=f"Specify the .env file to load (default: .env in git root or cwd, searched like {MODEL_SETTINGS_FNAME}).",
    )
    # Model selection
    parser.add_argument(
        "--lcr-model",
        # Default might depend on other models or keys, set later if None
        default=None,
        help="Model name for Large Context Retrieval (default: fastest suitable).",
    )
    parser.add_argument(
        "--architect-model",
        # Default might depend on other models or keys, set later if None
        default=None,
        help="Model name for the Architect phase (default: best suitable, e.g. gpt-4o or claude-3.5-sonnet).",
    )
    parser.add_argument(
        "--editor-model",
        # Default might depend on other models or keys, set later if None
        default=None,
        help="Model name for the Editor phase (default: same as architect).",
    )
    parser.add_argument(
        "--weak-model",  # Added from main.py
        metavar="MODEL",
        help=(
            "Specify a weaker/cheaper model to use for commit messages and chat history summarization."
            " Defaults to a model suitable for the main model."
        ),
        default=None,
    )
    # Git related args (mirroring main.py where relevant)
    parser.add_argument(
        "--git",
        "--yes",
        action="store_true",
        dest="git",
        default=True,  # Default to using git if available
        help="Use git features (repo map, commits) (default: True).",
    )
    parser.add_argument(
        "--no-git",
        action="store_false",
        dest="git",
        help="Disable git features.",
        default=False,
    )
    parser.add_argument(  # Renamed from --no-auto-commits for clarity and mirroring
        "--auto-commits",
        action="store_true",
        dest="auto_commits",
        default=True,  # Default to auto-commit
        help="Enable automatic commits after successful changes (default: True).",
    )
    parser.add_argument(  # Added from main.py
        "--dirty-commits",
        action="store_true",
        help="Allow adding files with unstaged changes to the chat",
        default=True,  # Consistent with aider default
    )
    parser.add_argument(
        "--attribute-author",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Attribute aider code changes in the git author name (default: True)",
    )
    parser.add_argument(
        "--attribute-committer",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Attribute aider commits in the git committer name (default: True)",
    )
    parser.add_argument(
        "--attribute-commit-message-author",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Prefix commit messages with 'aider: ' if aider authored the changes (default: False)",
    )
    parser.add_argument(
        "--attribute-commit-message-committer",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Prefix all commit messages with 'aider: ' (default: False)",
    )
    parser.add_argument(
        "--git-commit-verify",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable/disable git pre-commit hooks with --no-verify (default: False)",
    )
    parser.add_argument(  # Added from main.py
        "--aiderignore",
        metavar="AIDERIGNORE_FILE",
        default=AIDERIGNORE_FNAME,
        help=f"Specify the aider ignore file (default: {AIDERIGNORE_FNAME})",
    )
    parser.add_argument(  # Added from main.py
        "--gitignore",
        action="store_true",
        dest="gitignore",
        default=True,
        help="Check and update .gitignore (default: True)",
    )
    # Repo Map args (mirroring main.py)
    parser.add_argument(
        "--map-tokens",
        type=int,
        metavar="TOKENS",
        help="Max tokens for the repo map, use 0 to disable (default depends on main model).",
        default=None,
    )
    parser.add_argument(
        "--map-refresh",
        choices=["auto", "manual", "files"],
        help=(
            "Repo-map refresh strategy: auto (refresh if repo changed), manual (only with"
            " /reindex), files (refresh if files changed)"
        ),
        default="auto",
    )
    parser.add_argument(
        "--map-multiplier-no-files",
        type=float,
        default=8,
        help="Repo map context multiplier when no files are specified (default: 8).",
    )
    # Other args
    parser.add_argument(
        "--api-key",
        action="append",
        metavar="PROVIDER=KEY",
        help=(
            "Set an API key for a provider (eg: --api-key provider=<key> sets"
            " PROVIDER_API_KEY=<key>)"
        ),
        default=[],
    )
    parser.add_argument(
        "--commit-prompt",
        metavar="PROMPT",
        help="Specify a custom prompt for generating commit messages",
    )
    parser.add_argument(  # Added from main.py
        "--encoding",
        default="utf-8",
        help="Specify the encoding for reading/writing files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output from Aider and the operator.",
    )
    parser.add_argument(
        "--subtree-only",
        action="store_true",
        help="Only consider files in the current subtree of the git repository",
        default=False,
    )
    parser.add_argument(
        "--skip-sanity-check-repo",
        action="store_true",
        help="Skip the sanity check for the git repository (default: False)",
        default=False,
    )
    # Removed model settings/metadata file args - use standard search path

    args = parser.parse_args()

    # Use stderr for CLI setup messages
    print("--- Starting Aider Operator CLI ---", file=sys.stderr)

    # --- Setup IO ---
    # OperatorIO is simple, but use InputOutput for compatibility with helper functions
    operator_io = InputOutput(
        pretty=True,  # Operator is non-interactive
        yes=True,  # Assume yes for any confirmations needed by helpers
        encoding=args.encoding,  # Use the specified encoding
    )
    operator_io.verbose = args.verbose

    # --- Load slow imports synchronously ---
    # Operator is non-interactive, load everything upfront
    try:
        load_slow_imports(swallow=False)
        if args.verbose:
            operator_io.tool_output("Slow imports loaded.")
    except Exception as e:
        operator_io.tool_error(f"Error loading required imports: {e}")
        operator_io.tool_output(
            "This might indicate an incomplete installation. Try reinstalling aider."
        )
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        return 1

    # --- Handle API Keys from args ---
    # Do this *before* loading .env, so args can override .env
    if args.api_key:
        for api_setting in args.api_key:
            try:
                provider, key = api_setting.split("=", 1)
                env_var = f"{provider.strip().upper()}_API_KEY"
                os.environ[env_var] = key.strip()
                if args.verbose:
                    operator_io.tool_output(
                        f"Set environment variable from --api-key: {env_var}"
                    )
            except ValueError:
                operator_io.tool_error(f"Invalid --api-key format: {api_setting}")
                operator_io.tool_output("Format should be: provider=key")
                return 1

    # --- Determine Effective Git Root and Coder Root ---
    # Re-evaluate git availability and root based on args and environment
    use_git = args.git and git is not None
    git_root = None
    repo = None

    if use_git:
        try:
            # Let setup_git handle finding/creating repo, messages, etc.
            # Pass the initial guess for efficiency if available.
            git_root_found = setup_git(initial_git_root_guess, operator_io)

            if git_root_found:
                # Now verify it's the correct one based on args.files if provided
                # (mimics main.py's check after parsing args)
                potential_fnames = [str(Path(f).resolve()) for f in args.files]
                right_repo_root = guessed_wrong_repo(
                    operator_io, git_root_found, potential_fnames, None
                )  # No explicit git_dname from args

                if (
                    right_repo_root
                    and Path(right_repo_root).resolve()
                    != Path(git_root_found).resolve()
                ):
                    operator_io.tool_warning(
                        f"Initial git root guess {git_root_found} might be incorrect based on files."
                    )
                    operator_io.tool_warning(
                        f"Adjusting git root to: {right_repo_root}"
                    )
                    git_root = right_repo_root
                    # Re-run setup_git with the corrected root? No, just use the path.
                    # Check gitignore again for the *correct* root
                    if args.gitignore:
                        check_gitignore(
                            git_root, operator_io, ask=False
                        )  # Never ask in operator
                else:
                    git_root = git_root_found
                    # Check gitignore for the found root
                    if args.gitignore and git_root:  # Ensure git_root is not None
                        check_gitignore(
                            git_root, operator_io, ask=False
                        )  # Never ask in operator

            else:
                # setup_git determined no repo / user declined creation
                operator_io.tool_warning(
                    "Proceeding without git features (no repo found or configured)."
                )
                use_git = False

        except ImportError:
            operator_io.tool_warning(
                "GitPython not installed. Proceeding without git features."
            )
            use_git = False
        except Exception as e:
            operator_io.tool_error(f"Error setting up git repository: {e}")
            if args.verbose:
                traceback.print_exc(file=sys.stderr)
            operator_io.tool_warning("Proceeding without git features due to error.")
            use_git = False
    else:
        # Git explicitly disabled or git module unavailable
        operator_io.tool_output(
            f"Proceeding without git features ({'--no-git specified' if not args.git else 'git module not available'})."
        )
        use_git = False

    # Coder root is git root if git is used, otherwise CWD
    coder_root = git_root if use_git and git_root else str(Path.cwd())
    operator_io.tool_output(f"Using coder root: {coder_root}")

    # --- Load .env Files ---
    # Use the *final* determined git_root for search path
    # Re-evaluate env_file default based on final git_root
    if args.env_file == default_env_file(initial_git_root_guess):
        args.env_file = default_env_file(git_root)  # Use final git_root

    loaded_dotenvs = load_dotenv_files(git_root, args.env_file, operator_io.encoding)
    if args.verbose:
        if loaded_dotenvs:
            operator_io.tool_output("Loaded .env files:")
            for fname in loaded_dotenvs:
                operator_io.tool_output(f"  - {fname}")
        else:
            operator_io.tool_output("No .env files loaded.")

    # --- Register Models ---
    # Use standard search paths based on final git_root
    try:
        # Register model settings (YAML)
        model_settings_files = generate_search_path_list(
            MODEL_SETTINGS_FNAME,
            git_root,
            None,  # No explicit CLI arg for settings file in operator
        )
        # Use the imported register_models directly
        files_loaded_settings = register_models(model_settings_files)
        if args.verbose:
            if files_loaded_settings:
                operator_io.tool_output("Loaded model settings from:")
                for file_loaded in files_loaded_settings:
                    operator_io.tool_output(f"  - {file_loaded}")
            # else: operator_io.tool_output("No custom model settings files found or loaded.")
            # operator_io.tool_output("Searched for model settings files:")
            # for file in model_settings_files: operator_io.tool_output(f"  - {file}")

        # Register model metadata (JSON) - including resource file
        model_metadata_files = []
        try:
            resource_metadata_path = importlib_resources.files(
                "aider.resources"
            ).joinpath("model-metadata.json")
            # Use as_file context manager to handle potential zip/non-fs paths
            with importlib_resources.as_file(resource_metadata_path) as resource_file:
                model_metadata_files.append(str(resource_file))
        except FileNotFoundError:
            operator_io.tool_warning("Could not find built-in model metadata resource.")
        except Exception as e:
            operator_io.tool_warning(
                f"Could not load built-in model metadata resource: {e}"
            )

        model_metadata_files += generate_search_path_list(
            MODEL_METADATA_FNAME,
            git_root,
            None,  # No explicit CLI arg for metadata file in operator
        )
        # Use the imported register_litellm_models directly
        files_loaded_metadata = register_litellm_models(model_metadata_files)
        if args.verbose:
            if files_loaded_metadata:
                operator_io.tool_output("Loaded model metadata from:")
                for file_loaded in files_loaded_metadata:
                    operator_io.tool_output(f"  - {file_loaded}")
            # else: operator_io.tool_output("No custom model metadata files found or loaded.")
            # operator_io.tool_output("Searched for model metadata files:")
            # for file in model_metadata_files: operator_io.tool_output(f"  - {file}")

    except Exception as e:
        operator_io.tool_error(f"Error loading model definitions: {e}")
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        return 1

    # --- Determine and Initialize Models ---
    # Select defaults if not specified, similar to main.py logic if possible
    if not args.architect_model:
        # Basic fallback logic, main.py's key-based detection is more complex
        if os.environ.get("OPENAI_API_KEY"):
            args.architect_model = "gpt-4o"
        elif os.environ.get("ANTHROPIC_API_KEY"):
            args.architect_model = "claude-3.5-sonnet"  # Or sonnet as fallback
        elif os.environ.get("OPENROUTER_API_KEY"):
            args.architect_model = "openrouter/anthropic/claude-3.5-sonnet"
        else:
            operator_io.tool_error(
                "Could not determine default architect model. Please specify --architect-model or set an API key (e.g., OPENAI_API_KEY)."
            )
            return 1
        operator_io.tool_output(
            f"Using default architect model: {args.architect_model}"
        )

    if not args.editor_model:
        args.editor_model = args.architect_model  # Default editor to architect
        operator_io.tool_output(f"Using default editor model: {args.editor_model}")

    if not args.lcr_model:
        # Basic fallback logic
        if os.environ.get("GEMINI_API_KEY"):
            args.lcr_model = "flash"  # google/gemini-flash
        elif os.environ.get("OPENROUTER_API_KEY"):
            args.lcr_model = "openrouter/google/gemini-flash"
        elif os.environ.get("OPENAI_API_KEY"):
            args.lcr_model = "gpt-3.5-turbo"  # Cheap OpenAI fallback
        else:
            # Fallback to a generally available cheap model if possible
            args.lcr_model = "gpt-3.5-turbo"  # Or another common one
            operator_io.tool_warning(
                f"Could not determine specific LCR model, falling back to {args.lcr_model}. Specify --lcr-model or relevant API key (GEMINI_API_KEY, OPENROUTER_API_KEY) for better results."
            )
        operator_io.tool_output(f"Using default LCR model: {args.lcr_model}")

    try:
        # Initialize architect model first, its properties might influence others
        architect_model_obj = Model(
            args.architect_model,
            weak_model=args.weak_model,
            editor_model=args.editor_model,  # Pass editor name for potential internal use
        )
        operator_io.tool_output(
            f"Architect model ({architect_model_obj.name}) initialized."
        )

        # Determine default weak model if needed, based on architect model
        # This mirrors logic within Model.__init__ implicitly
        effective_weak_model_name = architect_model_obj.weak_model_name
        if effective_weak_model_name and args.verbose:
            operator_io.tool_output(
                f"Effective weak model for commits/summaries: {effective_weak_model_name}"
            )

        # Initialize editor model (often the same object if names match, handled by Model cache)
        editor_model_obj = Model(args.editor_model)  # Use resolved editor name
        operator_io.tool_output(f"Editor model ({editor_model_obj.name}) initialized.")

        # Initialize LCR model
        lcr_model_obj = Model(args.lcr_model)
        operator_io.tool_output(f"LCR model ({lcr_model_obj.name}) initialized.")

    except Exception as e:
        operator_io.tool_error(f"Error initializing models: {e}")
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        # Check if specific keys are missing
        if "api key" in str(e).lower():
            operator_io.tool_error(
                "Please ensure the required API key is set via --api-key, .env file, or environment variable."
            )
        return 1

    # --- Determine map_tokens Default ---
    # Based on the *architect* model, mirroring main.py's logic
    map_tokens = args.map_tokens
    if map_tokens is None:
        map_tokens = architect_model_obj.get_repo_map_tokens()
        if args.verbose:
            operator_io.tool_output(
                f"Using default repo map tokens based on architect model: {map_tokens}"
            )

    # --- Resolve Initial File Paths ---
    initial_abs_files = []
    read_only_abs_files = []

    # Process positional files (editable)
    for fname_rel in args.files:
        path_obj = Path(fname_rel)
        abs_path = path_obj if path_obj.is_absolute() else Path(coder_root) / fname_rel
        try:
            resolved_path = abs_path.resolve()
            if resolved_path.is_file():
                initial_abs_files.append(str(resolved_path))
            elif resolved_path.is_dir():
                operator_io.tool_warning(
                    f"Specified path is a directory, not adding to initial files: {resolved_path}"
                )
                operator_io.tool_output(
                    "Specify individual files or rely on LCR for directories."
                )
            else:
                operator_io.tool_warning(
                    f"Specified path not found or not a file: {resolved_path}, skipping."
                )
        except OSError as e:
            operator_io.tool_warning(
                f"Error resolving or checking file {abs_path}: {e}, skipping."
            )

    # Process --read files (read-only) - mirroring main.py logic
    for fn in args.read:
        path = Path(fn).expanduser()
        abs_path = path if path.is_absolute() else Path(coder_root) / path
        try:
            resolved_path = abs_path.resolve()
            if resolved_path.is_dir():
                # Recursively add all files in the directory
                added_count = 0
                for f in resolved_path.rglob("*"):
                    if f.is_file():
                        read_only_abs_files.append(str(f))
                        added_count += 1
                if args.verbose:
                    operator_io.tool_output(
                        f"Added {added_count} read-only files from directory: {resolved_path}"
                    )
            elif resolved_path.is_file():
                read_only_abs_files.append(str(resolved_path))
            else:
                operator_io.tool_warning(
                    f"Read-only path not found: {resolved_path}, skipping."
                )
        except OSError as e:
            operator_io.tool_warning(
                f"Error resolving or checking read-only path {abs_path}: {e}, skipping."
            )

    # Remove duplicates and ensure read-only files aren't in editable list
    read_only_abs_files = sorted(list(set(read_only_abs_files)))
    initial_abs_files = sorted(list(set(initial_abs_files) - set(read_only_abs_files)))

    if initial_abs_files:
        operator_io.tool_output(
            f"Using specified initial files (resolved): {initial_abs_files}"
        )
    if read_only_abs_files:
        operator_io.tool_output(
            f"Using specified read-only files (resolved): {read_only_abs_files}"
        )
    if not initial_abs_files and not read_only_abs_files:
        operator_io.tool_output(
            "No initial files specified or found, LCR will determine context."
        )

    if args.git:
        git_root = setup_git(git_root, operator_io)
        if args.gitignore:
            check_gitignore(git_root, operator_io)

    # --- Initialize GitRepo ---
    if use_git and git_root:
        try:
            # Combine editable and read-only for GitRepo context finding, but it mainly uses git status/ls-files
            all_initial_context_files = initial_abs_files + read_only_abs_files

            # Pass models needed for commit messages *if* auto_commits is on
            commit_models = []
            if args.auto_commits:
                # Use the effective weak model and the architect model
                commit_models = [architect_model_obj.weak_model, architect_model_obj]

            repo = GitRepo(
                io=operator_io,
                fnames=all_initial_context_files,
                git_dname=git_root,
                aider_ignore_file=args.aiderignore,
                models=commit_models,
                attribute_author=args.attribute_author,
                attribute_committer=args.attribute_committer,
                attribute_commit_message_author=args.attribute_commit_message_author,
                attribute_commit_message_committer=args.attribute_commit_message_committer,
                commit_prompt=args.commit_prompt,
                subtree_only=args.subtree_only,
                git_commit_verify=args.git_commit_verify,
            )
            operator_io.tool_output(f"Git repository initialized for {git_root}")

            # Sanity check the repo unless skipped
            if not args.skip_sanity_check_repo:
                if not sanity_check_repo(repo, operator_io):
                    operator_io.tool_error(
                        "Git repository sanity check failed. Operator might malfunction."
                    )
                    operator_io.tool_warning(
                        "Proceeding, but results may be unpredictable. Try fixing the repo or run with --no-git."
                    )
                    # Don't disable git entirely here, let the operator try
                    # use_git = False
                    # repo = None

        except ANY_GIT_ERROR as e:
            operator_io.tool_error(f"Error initializing git repository object: {e}")
            operator_io.tool_warning("Proceeding without git features.")
            use_git = False
            repo = None
        except FileNotFoundError:
            # This shouldn't happen if git_root was confirmed, but handle defensively
            operator_io.tool_error(
                f"Git root directory not found during GitRepo init: {git_root}"
            )
            operator_io.tool_warning("Proceeding without git features.")
            use_git = False
            repo = None
        except Exception as e:
            operator_io.tool_error(f"Unexpected error initializing GitRepo: {e}")
            if args.verbose:
                traceback.print_exc(file=sys.stderr)
            operator_io.tool_warning("Proceeding without git features.")
            use_git = False
            repo = None

    # --- Execute Operator Core Logic ---
    operator_io.tool_output("--- Starting Aider Operator Execution ---")
    try:
        exit_code = run_aider_operator(
            user_prompt=args.request,
            initial_files=initial_abs_files,
            read_only_files=read_only_abs_files,
            lcr_model=lcr_model_obj,
            architect_model=architect_model_obj,
            operator_io=operator_io,
            repo=repo,
            coder_root=coder_root,
            use_git=use_git,
            auto_commits=args.auto_commits,
            dirty_commits=args.dirty_commits,
            verbose=args.verbose,
            map_tokens=map_tokens,
            map_refresh=args.map_refresh,
            map_mul_no_files=args.map_multiplier_no_files,
        )
        operator_io.tool_output(
            f"--- Aider Operator Finished (Exit Code: {exit_code}) ---"
        )
        return exit_code
    except Exception as e:
        operator_io.tool_error(f"An error occurred during operator execution: {e}")
        if args.verbose:
            traceback.print_exc(file=sys.stderr)
        operator_io.tool_output(f"--- Aider Operator Failed ---")
        return 1


def setup_git(git_root, io):
    if git is None:
        return

    try:
        cwd = Path.cwd()
    except OSError:
        cwd = None

    repo = None

    if git_root:
        try:
            repo = git.Repo(git_root)
        except ANY_GIT_ERROR:
            pass
    elif cwd == Path.home():
        io.tool_warning(
            "You should probably run aider in your project's directory, not your home dir."
        )
        return
    elif cwd and io.confirm_ask(
        "No git repo found, create one to track aider's changes (recommended)?"
    ):
        git_root = str(cwd.resolve())
        repo = make_new_repo(git_root, io)

    if not repo:
        return

    try:
        user_name = repo.git.config("--get", "user.name") or None
    except git.exc.GitCommandError:
        user_name = None

    try:
        user_email = repo.git.config("--get", "user.email") or None
    except git.exc.GitCommandError:
        user_email = None

    if user_name and user_email:
        return repo.working_tree_dir

    with repo.config_writer() as git_config:
        if not user_name:
            git_config.set_value("user", "name", "Your Name")
            io.tool_warning('Update git name with: git config user.name "Your Name"')
        if not user_email:
            git_config.set_value("user", "email", "you@example.com")
            io.tool_warning(
                'Update git email with: git config user.email "you@example.com"'
            )

    return repo.working_tree_dir


# Standard entry point guard
if __name__ == "__main__":
    sys.exit(main())

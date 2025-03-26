import os
import sys
import traceback
from pathlib import Path

# --- Aider Imports ---
# Use absolute imports from the package root
from aider.coders import Coder
from aider.models import Model
from aider.io import InputOutput
from aider.repo import GitRepo, ANY_GIT_ERROR

# --- Operator Specific IO Handler ---


class OperatorIO(InputOutput):
    """
    A non-interactive IO handler for the Aider operator.
    Captures output and automatically confirms actions.
    Simplified for basic operation, less state tracking.
    """

    def __init__(self, encoding="utf-8", verbose=False, **kwargs):
        # Core settings for non-interactive operation
        self.pretty = False
        self.yes_always = True
        self.verbose = verbose
        self.encoding = encoding

        # Unused properties for non-interactive mode, kept for compatibility
        self.input_history_file = None
        self.chat_history_file = None
        self._input = None
        self._output = None
        self.user_input_color = None
        self.tool_output_color = None
        self.tool_warning_color = None
        self.tool_error_color = None
        self.assistant_output_color = None
        self.code_theme = "default"
        self.dry_run = False  # Can be set externally if needed
        self.line_endings = "platform"
        self.llm_history_file = None
        self.multiline_mode = False
        self.notifications = False
        self.notifications_command = None
        self.last_prompt = None  # Could be useful for debugging

        # Simplified captures - just track last assistant output
        self.last_assistant_output = None

    def get_input(self, *args, **kwargs):
        # This should never be called in operator mode
        print("FATAL: OperatorIO.get_input called unexpectedly!", file=sys.stderr)
        raise RuntimeError("OperatorIO does not support interactive input.")

    def confirm_ask(self, *args, **kwargs):
        # Always confirm 'yes'
        # Optional verbose logging removed for simplicity
        return True

    def tool_output(self, *args, **kwargs):
        message = " ".join(map(str, args))
        log_only = kwargs.get("log_only", False)
        if not log_only and self.verbose:
            print(f"OPERATOR_IO_TOOL: {message}", file=sys.stderr)

    def tool_warning(self, *args, **kwargs):
        message = " ".join(map(str, args))
        # Always print warnings to stderr
        print(f"OPERATOR_IO_WARN: {message}", file=sys.stderr)

    def tool_error(self, *args, **kwargs):
        message = " ".join(map(str, args))
        # Always print errors to stderr
        print(f"OPERATOR_IO_ERR: {message}", file=sys.stderr)

    def assistant_output(self, content, **kwargs):
        # Store the final assistant output
        self.last_assistant_output = content
        if self.verbose:
            print(
                f"OPERATOR_IO_ASSISTANT (Final):\n---\n{content}\n---", file=sys.stderr
            )

    def ai_output(self, content, **kwargs):
        # ai_output is mainly for intermediate steps, ignore in simplified version
        pass

    def llm_started(self):
        if self.verbose:
            print("OPERATOR_IO: LLM Processing Started...", file=sys.stderr)

    def rule(self, *args, **kwargs):
        pass  # No visual rules needed

    def get_last_assistant_output(self):
        return self.last_assistant_output

    def add_to_input_history(self, line):
        pass  # No history file

    def user_input(self, text):
        if self.verbose:
            print(f"OPERATOR_IO_USER_INPUT: {text}", file=sys.stderr)

    def get_assistant_mdstream(self):
        # Simplified dummy stream for non-interactive processing
        class DummyStream:
            def __init__(self, verbose):
                self.verbose = verbose

            def update(self, content, final=False):
                # Only print intermediate stream output if verbose
                if self.verbose and content:
                    print(content, end="", flush=True, file=sys.stderr)
                if self.verbose and final:
                    print(file=sys.stderr)  # Newline at the end

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return DummyStream(self.verbose)

    # Methods made into simple no-ops
    def set_last_prompt(self, prompt):
        self.last_prompt = prompt

    def set_placeholder(self, text):
        pass

    def push_input_onto_queue(self, text):
        pass

    def toggle_multiline_mode(self):
        pass

    def offer_url(self, *args, **kwargs):
        return False


# --- Operator Execution Logic ---


def run_aider_operator(
    user_prompt: str,
    initial_files: list,  # Should be absolute paths
    lcr_model: Model,
    architect_model: Model,
    editor_model: Model,
    operator_io: OperatorIO,
    repo: GitRepo | None,
    coder_root: str,
    use_git: bool,
    auto_commits: bool,
    verbose: bool,
):
    """
    Runs the core Aider operator process using pre-configured components.
    Returns 0 on success, raises exceptions on error.
    Simplified version without internal try/except.
    """
    # --- Phase 1: Large Context Retriever ---
    operator_io.tool_output("\n--- Phase 1: Large Context Retriever ---")
    lcr_coder = Coder.create(
        main_model=lcr_model,
        edit_format="large_context_retriever",
        io=operator_io,
        repo=repo,
        fnames=initial_files,
        verbose=verbose,
        use_git=use_git,
        root=coder_root,
        stream=False,  # LCR usually doesn't stream well
    )

    # Run LCR - will raise exception on failure
    lcr_coder.run_one(user_prompt, preproc=False)

    identified_files_rel = lcr_coder.get_inchat_relative_files()
    if not identified_files_rel:
        operator_io.tool_warning(
            "LCR phase did not identify any files. Proceeding, but might lack context."
        )
    else:
        operator_io.tool_output(f"LCR identified files: {identified_files_rel}")

    # --- Phase 2: Architect / Editor ---
    operator_io.tool_output("\n--- Phase 2: Architect/Editor ---")

    architect_coder = Coder.create(
        main_model=architect_model,
        editor_model=editor_model,
        edit_format="architect",
        from_coder=lcr_coder,  # Inherit state (files identified by LCR)
        io=operator_io,
        repo=repo,
        root=coder_root,
        auto_accept_architect=True,  # Essential for operator
        auto_commits=auto_commits,
        dirty_commits=auto_commits,  # Usually same as auto_commits
        show_diffs=verbose,
        verbose=verbose,
        use_git=use_git,
        stream=True,  # Allow streaming for this phase
    )

    architect_prompt = (
        f"Based on the file context provided (determined relevant by a previous step), "
        f"please address the following request:\n\n{user_prompt}\n\n"
        f"Design the changes and then implement them using the editor."
    )
    if verbose:
        operator_io.tool_output(f"\nPrompting Architect:\n{architect_prompt}\n")

    # Run the architect coder (triggers architect -> editor flow)
    # Will raise exception on failure
    architect_coder.run_one(architect_prompt, preproc=False)

    # --- Completion ---
    operator_io.tool_output("\n--- Aider Operator Finished ---")

    if architect_coder.aider_edited_files:
        operator_io.tool_output(
            f"Files potentially edited: {architect_coder.aider_edited_files}"
        )
        # Check commit status if applicable
        if repo and architect_coder.last_aider_commit_hash and auto_commits:
            operator_io.tool_output(
                f"Last Aider commit: {architect_coder.last_aider_commit_hash} - {architect_coder.last_aider_commit_message}"
            )
        elif repo and auto_commits:
            operator_io.tool_output(
                "Changes applied, but auto-commit status uncertain (might not have run/succeeded)."
            )
        # Other status messages omitted for brevity in this simplified version
    else:
        # Simplified check for no edits
        operator_io.tool_output(
            "No files appear to have been edited by the architect/editor phase."
        )

    return 0  # Success

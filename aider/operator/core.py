# aider/operator/core.py
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
    """

    def __init__(self, encoding="utf-8", verbose=False, **kwargs):
        # Core settings for non-interactive operation
        # Operator output isn't meant for rich terminal display, so pretty=False
        self.pretty = False
        self.yes_always = True  # Assume 'yes' to confirmations
        self.verbose = verbose
        self.encoding = encoding

        # Properties for compatibility with InputOutput interface expected by Coder
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
        self.last_prompt = None
        self.placeholder = None

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
        # Print tool output to stderr if verbose and not log_only
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

    def assistant_output(self, content, final=True, **kwargs):
        # Store the final assistant output
        # Check 'final' flag which indicates the complete response in streaming
        if final:
            self.last_assistant_output = content
            if self.verbose:
                print(
                    f"OPERATOR_IO_ASSISTANT (Final):\n---\n{content}\n---",
                    file=sys.stderr,
                )
        elif self.verbose:
            # Optionally print intermediate streaming chunks if verbose
            # print(f"OPERATOR_IO_ASSISTANT (Chunk): {content}", file=sys.stderr)
            pass  # Usually too noisy

    def ai_output(self, content, **kwargs):
        # ai_output is mainly for intermediate steps (e.g., thoughts), ignore for clarity
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
        # Log the effective user input (prompt) if verbose
        if self.verbose:
            print(f"OPERATOR_IO_USER_INPUT: {text}", file=sys.stderr)

    def get_assistant_mdstream(self):
        # Simplified dummy stream for non-interactive processing
        class DummyStream:
            def __init__(self, io_instance):
                self.io = io_instance
                self.content_buffer = ""

            def update(self, content, final=False):
                # Accumulate content
                self.content_buffer += content
                # Pass intermediate/final content to the main assistant_output method
                self.io.assistant_output(self.content_buffer, final=final)
                # Print intermediate stream output to stderr if verbose
                if self.io.verbose and content:
                    # Print only the new chunk to stderr for streaming effect
                    print(content, end="", flush=True, file=sys.stderr)
                if self.io.verbose and final:
                    print(file=sys.stderr)  # Newline at the end of stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                # Ensure final state is captured even if not explicitly finalized
                # self.io.assistant_output(self.content_buffer, final=True)
                pass  # `update(final=True)` should handle this

        return DummyStream(self)

    # Methods made into simple no-ops or basic logging
    def set_last_prompt(self, prompt):
        self.last_prompt = prompt
        if self.verbose:
            print(f"OPERATOR_IO: Last prompt set.", file=sys.stderr)

    def set_placeholder(self, text):
        pass

    def push_input_onto_queue(self, text):
        pass

    def toggle_multiline_mode(self):
        pass

    def offer_url(self, *args, **kwargs):
        # Non-interactive, always decline to open URL
        return False


# --- Operator Execution Logic ---


def run_aider_operator(
    user_prompt: str,
    initial_files: list,  # Absolute paths for editable files
    read_only_files: list,  # Absolute paths for read-only files
    lcr_model: Model,
    architect_model: Model,
    operator_io: OperatorIO,
    repo: GitRepo | None,
    coder_root: str,  # Determined root for relative path resolution if needed
    use_git: bool,
    auto_commits: bool,
    dirty_commits: bool,  # Added
    verbose: bool,
    map_tokens: int,
    map_refresh: str,
    map_mul_no_files: float,
) -> int:
    """
    Runs the core Aider operator process using pre-configured components.
    Returns 0 on success, 1 on failure.
    """
    try:
        # --- Phase 1: Large Context Retriever ---
        operator_io.tool_output("\n--- Phase 1: Large Context Retriever ---")
        lcr_coder = Coder.create(
            main_model=lcr_model,
            edit_format="large_context_retriever",
            io=operator_io,
            repo=repo,
            fnames=initial_files,
            read_only_fnames=read_only_files,  # Pass read-only files
            verbose=verbose,
            use_git=use_git,  # Use passed parameter
            stream=False,  # LCR usually doesn't stream well
            map_tokens=map_tokens,  # Use passed parameter
            map_refresh=map_refresh,  # Use passed parameter
            map_mul_no_files=map_mul_no_files,  # Use passed parameter
            # Other relevant defaults from Coder.create will apply
        )

        # Run LCR - use coder.run()
        lcr_coder.run(with_message=user_prompt)

        # Get files identified by LCR to potentially pass or log
        # Note: LCR edit format adds files directly to the chat context
        identified_files_rel = lcr_coder.get_inchat_relative_files()
        if not identified_files_rel:
            operator_io.tool_warning(
                "LCR phase did not explicitly add files to chat. Proceeding with initial/repo context."
            )
        else:
            operator_io.tool_output(
                f"LCR added files to context: {identified_files_rel}"
            )

        # Optional: Output the repo map after LCR run (might be updated)
        if verbose and repo:
            repo_map_content = lcr_coder.get_repo_map()
            if repo_map_content:
                operator_io.tool_output(f"Repo map after LCR:\n{repo_map_content}")
            else:
                operator_io.tool_output("Repo map is disabled or empty after LCR.")

        # --- Phase 2: Architect / Editor ---
        operator_io.tool_output("\n--- Phase 2: Architect/Editor ---")

        architect_coder = Coder.create(
            main_model=architect_model,
            edit_format="architect",
            from_coder=lcr_coder,  # Inherit state (files, repo, io, encoding etc.)
            # Explicitly override specific settings needed for this phase:
            io=operator_io,  # Ensure it uses the operator IO
            repo=repo,  # Pass repo again (though from_coder should handle)
            auto_accept_architect=True,  # Essential for operator
            auto_commits=auto_commits,  # Use passed parameter
            dirty_commits=dirty_commits,  # Use passed parameter
            show_diffs=verbose,  # Show diffs only if verbose
            verbose=verbose,
            use_git=use_git,  # Use passed parameter
            stream=True,  # Allow streaming for this phase (handled by OperatorIO)
            map_tokens=map_tokens,  # Use passed parameter
            map_refresh=map_refresh,  # Use passed parameter
            map_mul_no_files=map_mul_no_files,  # Use passed parameter
        )

        # Construct the prompt for the architect phase
        # No need to explicitly mention LCR files, they are in the context
        architect_prompt = (
            f"Based on the provided file context, please address the following request:\n\n"
            f"{user_prompt}\n\n"
            f"Design the changes if necessary, then implement them using the editor format."
        )
        if verbose:
            operator_io.tool_output(f"\nPrompting Architect:\n{architect_prompt}\n")

        architect_coder.run_one(user_message=architect_prompt, preproc=True)

        # --- Completion ---
        operator_io.tool_output("\n--- Operator Execution Summary ---")

        return 0  # Indicate success

    except Exception as e:
        operator_io.tool_error(f"An error occurred during operator execution: {e}")
        if verbose:
            traceback.print_exc(file=sys.stderr)
        return 1  # Indicate failure

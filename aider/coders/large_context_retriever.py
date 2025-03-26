import re
import xml.etree.ElementTree as ET
import fnmatch
from pathlib import Path
from .base_coder import Coder
from .large_context_retriever_prompts import LargeContextRetrieverPrompts
from gitingest import ingest


class LargeContextRetriever(Coder):
    edit_format = "large_context_retriever"
    gpt_prompts = LargeContextRetrieverPrompts()

    DEFAULT_EXCLUDE_FOLDERS = [
        "node_modules",
        ".git",
        "dist",
        "build",
        ".next",
        "venv",
        "__pycache__",
        "out",
        ".cache",
        "coverage",
    ]
    DEFAULT_EXCLUDE_FILES = ["package-lock.json", "yarn.lock"]
    DEFAULT_EXCLUDE_EXTENSIONS = [".min.js"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repo_map = None

    def extract_xml_from_response(self, response_text):
        result = {"thinking": [], "include_patterns": [], "exclude_patterns": []}
        xml_match = re.search(r"<response>(.*?)</response>", response_text, re.DOTALL)
        if not xml_match:
            self.io.tool_warning("No XML block found in response.")
            return result
        try:
            root = ET.fromstring(f"<response>{xml_match.group(1)}</response>")
            thinking = root.find("thinking")
            if thinking is not None and thinking.text:
                result["thinking"] = [thinking.text.strip()]
            result["include_patterns"] = [
                p.text.strip()
                for p in root.findall(".//include_patterns/pattern")
                if p.text
            ]
            result["exclude_patterns"] = [
                p.text.strip()
                for p in root.findall(".//exclude_patterns/pattern")
                if p.text
            ]
        except ET.ParseError as e:
            self.io.tool_warning(f"XML parsing failed: {e}")
        return result

    def get_default_exclude_patterns(self):
        return (
            [f"**/{folder}/**" for folder in self.DEFAULT_EXCLUDE_FOLDERS]
            + [f"**/{file}" for file in self.DEFAULT_EXCLUDE_FILES]
            + [f"**/*{ext}" for ext in self.DEFAULT_EXCLUDE_EXTENSIONS]
        )

    def match_files_with_patterns(self, include_patterns, exclude_patterns):
        all_files = set(self.get_all_relative_files())
        combined_excludes = set(exclude_patterns + self.get_default_exclude_patterns())

        if not include_patterns:
            included_files = all_files
        else:
            included_files = set()
            for pattern in include_patterns:
                matched_files = fnmatch.filter(all_files, pattern)
                included_files.update(matched_files)

        final_files = {
            file
            for file in included_files
            if not any(fnmatch.fnmatch(file, pat) for pat in combined_excludes)
        }
        return final_files

    def reply_completed(self):
        content = self.partial_response_content
        xml_data = self.extract_xml_from_response(content)

        if xml_data["thinking"]:
            self.io.tool_output(f"LLM Reasoning:\n{xml_data['thinking'][0]}")

        matched_files = self.match_files_with_patterns(
            xml_data["include_patterns"], xml_data["exclude_patterns"]
        )

        if matched_files:
            self.abs_fnames = {self.abs_root_path(f) for f in matched_files}
            file_list = ", ".join(sorted(matched_files))
            self.io.tool_output(
                f"Added {len(matched_files)} files to chat: {file_list}"
            )
        else:
            self.io.tool_warning(
                "No files matched the patterns provided; clearing file selection."
            )
            self.abs_fnames.clear()

        return True

    def check_for_file_mentions(self, content):
        pass

    # Required base class overrides (even minimal):
    def get_edits(self, mode="update"):
        # No actual edits, just file selection
        return []

    def apply_edits(self, edits):
        pass

    def apply_edits_dry_run(self, edits):
        return edits

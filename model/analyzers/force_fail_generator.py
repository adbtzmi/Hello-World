#!/usr/bin/env python3
"""
model/analyzers/force_fail_generator.py
=========================================
Force Fail Generator — AI-powered force-fail code change engine.

Responsibilities:
  1. Calls AI to generate structured force-fail code diffs based on
     JIRA analysis, test scenarios, and repository contents.
  2. Validates that AI-generated patches can be applied cleanly.
  3. Creates a temporary copy of the TP repository.
  4. Applies patches to the copy to produce a force-fail variant.
  5. Cleans up the temporary copy after compilation.

Data flow:
  ForceFailController → ForceFailGenerator.generate_force_fail_diffs()
                       → ForceFailGenerator.validate_patches()
                       → ForceFailGenerator.create_patched_repo()
                       → CompileController.start_compile(patched_repo)
                       → ForceFailGenerator.cleanup_patched_repo()
"""

import os
import re
import json
import shutil
import logging
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("bento_app")

# ── Default configuration ─────────────────────────────────────────────────────
DEFAULT_TEMP_PREFIX = "bento_ff_"
DEFAULT_MAX_PATCHES = 10


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class FilePatch:
    """A single file-level patch within a force-fail case."""
    file: str                    # Relative path within the repo
    original_lines: List[str]    # Lines to find (for validation)
    modified_lines: List[str]    # Lines to replace with
    line_number: int = 0         # Approximate line number (hint for search)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FilePatch":
        return cls(
            file=d.get("file", ""),
            original_lines=d.get("original_lines", []),
            modified_lines=d.get("modified_lines", []),
            line_number=d.get("line_number", 0),
        )


@dataclass
class ForceFailCase:
    """One force-fail test case with its associated code patches."""
    test_id: str
    description: str
    rationale: str
    patches: List[FilePatch] = field(default_factory=list)
    enabled: bool = True         # User can toggle individual cases on/off

    def to_dict(self) -> dict:
        d = asdict(self)
        d["patches"] = [p.to_dict() for p in self.patches]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ForceFailCase":
        return cls(
            test_id=d.get("test_id", ""),
            description=d.get("description", ""),
            rationale=d.get("rationale", ""),
            patches=[FilePatch.from_dict(p) for p in d.get("patches", [])],
            enabled=d.get("enabled", True),
        )


@dataclass
class PatchValidationResult:
    """Result of validating patches against the actual repo."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ── Output Schema for AI ──────────────────────────────────────────────────────

FORCE_FAIL_OUTPUT_SCHEMA = """{
  "force_fail_cases": [
    {
      "test_id": "FF-001",
      "description": "Human-readable description of what this force-fail tests",
      "rationale": "Why this change will cause a detectable failure",
      "patches": [
        {
          "file": "relative/path/to/file.py",
          "original_lines": ["line1_as_it_exists_now", "line2_as_it_exists_now"],
          "modified_lines": ["line1_modified_to_cause_failure", "line2_modified"],
          "line_number": 42
        }
      ]
    }
  ]
}"""


# ── Force Fail Generator ─────────────────────────────────────────────────────

class ForceFailGenerator:
    """
    Core engine for AI-generated force-fail code changes.

    Usage:
        generator = ForceFailGenerator(analyzer)
        cases = generator.generate_force_fail_diffs(jira_analysis, test_scenarios, repo_path)
        validation = generator.validate_patches(repo_path, cases)
        patched_path = generator.create_patched_repo(repo_path, cases, jira_key)
        # ... compile patched_path ...
        generator.cleanup_patched_repo(patched_path)
    """

    def __init__(self, analyzer, config: Optional[dict] = None):
        """
        Args:
            analyzer: JIRAAnalyzer instance (has .ai_client for chat_completion)
            config:   Optional settings dict (force_fail section from settings.json)
        """
        self.analyzer = analyzer
        self._config = config or {}
        self._temp_prefix = self._config.get("temp_repo_prefix", DEFAULT_TEMP_PREFIX)
        self._temp_dir = self._config.get("temp_repo_dir", "")
        self._auto_cleanup = self._config.get("auto_cleanup", True)
        self._max_patches = self._config.get("max_patches_per_case", DEFAULT_MAX_PATCHES)
        self._log_callback = None

    def set_log_callback(self, callback):
        """Set a log callback for GUI integration."""
        self._log_callback = callback

    def _log(self, msg: str, level: str = "info"):
        """Log to both Python logger and optional GUI callback."""
        getattr(logger, level)(msg)
        if self._log_callback:
            self._log_callback(msg)

    # ──────────────────────────────────────────────────────────────────────
    # STEP 1: AI GENERATION
    # ──────────────────────────────────────────────────────────────────────

    def generate_force_fail_diffs(
        self,
        jira_analysis: str,
        test_scenarios: str,
        repo_path: str,
        repo_index: Optional[Dict] = None,
    ) -> List[ForceFailCase]:
        """
        Call the AI to generate force-fail code changes.

        Args:
            jira_analysis:   Text of the JIRA analysis from workflow
            test_scenarios:  Text of the test scenarios (includes force-fail cases)
            repo_path:       Path to the TP repository
            repo_index:      Optional repo index dict from analyzer.index_repository()

        Returns:
            List of ForceFailCase objects with patches
        """
        self._log("\n[Generating Force Fail Code Changes with AI]")

        # Build repo index if not provided
        if repo_index is None and self.analyzer:
            self._log("Indexing repository for force-fail generation...")
            repo_index = self.analyzer.index_repository(repo_path)

        # Extract force-fail specific test cases from the scenarios text
        ff_scenarios = self._extract_force_fail_scenarios(test_scenarios)
        if not ff_scenarios:
            ff_scenarios = test_scenarios  # Use full scenarios if extraction fails

        # Read contents of potentially affected files
        affected_files_content = self._read_affected_files(repo_path, repo_index)

        # Build explicit file listing to ground the AI (RAG approach)
        repo_file_listing = self._get_repo_file_listing(repo_path)

        # Build the prompt
        prompt = self._build_generation_prompt(
            jira_analysis=jira_analysis,
            force_fail_scenarios=ff_scenarios,
            repo_index=repo_index,
            affected_files_content=affected_files_content,
            repo_file_listing=repo_file_listing,
        )

        # Call AI
        messages = [{"role": "user", "content": prompt}]

        if not self.analyzer or not hasattr(self.analyzer, 'ai_client'):
            self._log("✗ AI client not available — cannot generate force-fail diffs", "error")
            return []

        self._log("Calling AI for force-fail code generation...")
        result = self.analyzer.ai_client.chat_completion(
            messages,
            task_type="code_generation",
            mode="force_fail_generation",
        )

        if not result.get("success"):
            self._log(f"✗ AI generation failed: {result.get('error', 'Unknown error')}", "error")
            return []

        # Parse the response
        response_content = result["response"]["choices"][0]["message"]["content"]
        cases = self._parse_ai_response(response_content)

        if cases:
            self._log(f"✓ AI generated {len(cases)} force-fail case(s)")
            for case in cases:
                self._log(f"  • {case.test_id}: {case.description} ({len(case.patches)} patch(es))")

            # Post-generation validation: auto-disable cases with non-existent files
            cases = self._auto_disable_invalid_cases(cases, repo_path)
        else:
            self._log("⚠ AI response did not contain parseable force-fail cases")

        return cases

    def _extract_force_fail_scenarios(self, test_scenarios: str) -> str:
        """
        Extract force-fail specific sections from the full test scenarios text.
        Looks for sections labeled 'Force Fail', 'Negative Test', etc.
        """
        lines = test_scenarios.split("\n")
        ff_lines = []
        in_ff_section = False

        for line in lines:
            lower = line.lower().strip()
            # Detect force-fail section headers
            if any(kw in lower for kw in [
                "force fail", "force_fail", "negative test",
                "failure scenario", "failing case", "fail case"
            ]):
                in_ff_section = True
                ff_lines.append(line)
            elif in_ff_section:
                # End section on next major header (## or **Section**)
                if (line.startswith("## ") or line.startswith("**")) and \
                   not any(kw in lower for kw in ["force", "fail", "negative"]):
                    in_ff_section = False
                else:
                    ff_lines.append(line)

        return "\n".join(ff_lines) if ff_lines else ""

    def _get_repo_file_listing(self, repo_path: str, max_files: int = 500) -> str:
        """
        Build an explicit listing of all files in the repository.
        This grounds the AI so it can only reference files that actually exist,
        eliminating hallucinated file paths (RAG approach).
        """
        if not os.path.isdir(repo_path):
            return "(Repository path not accessible)"

        all_files = []
        excluded_dirs = {".git", "__pycache__", "node_modules", "release",
                         ".venv", "build", "dist", ".tox"}
        excluded_exts = {".pyc", ".pyo", ".o", ".d", ".a", ".map", ".elf",
                         ".tgz", ".gz", ".zip", ".tar", ".bin", ".dat"}

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in excluded_exts:
                    continue
                rel_path = os.path.relpath(
                    os.path.join(root, fname), repo_path
                ).replace("\\", "/")
                all_files.append(rel_path)

        all_files.sort()
        if len(all_files) > max_files:
            all_files = all_files[:max_files]
            all_files.append(f"... ({len(all_files)} files shown, more exist)")

        return "\n".join(all_files) if all_files else "(Empty repository)"

    def _auto_disable_invalid_cases(
        self, cases: List[ForceFailCase], repo_path: str
    ) -> List[ForceFailCase]:
        """
        Post-generation validation: auto-disable cases that reference
        files not found in the repository. Logs warnings for transparency.
        """
        disabled_count = 0
        for case in cases:
            has_invalid = False
            for patch in case.patches:
                file_path = os.path.join(repo_path, patch.file)
                if not os.path.isfile(file_path):
                    self._log(
                        f"  ⚠ [{case.test_id}] File not found: {patch.file} "
                        f"— auto-disabling case", "warning"
                    )
                    has_invalid = True
                    break
            if has_invalid and case.enabled:
                case.enabled = False
                disabled_count += 1

        if disabled_count:
            self._log(
                f"⚠ Auto-disabled {disabled_count} case(s) with invalid file paths"
            )
        return cases

    def _read_affected_files(
        self, repo_path: str, repo_index: Optional[Dict]
    ) -> str:
        """
        Read contents of files likely to be affected by force-fail changes.
        Focuses on rule files, limit files, config files, and test classes.
        """
        target_patterns = [
            r".*rule.*\.py$",
            r".*limit.*\.py$",
            r".*config.*\.(py|json|yaml|yml)$",
            r".*test.*\.py$",
            r".*param.*\.py$",
            r".*threshold.*\.py$",
            r".*check.*\.py$",
            r".*validate.*\.py$",
        ]

        files_content = []
        max_files = 15
        max_file_size = 5000  # chars per file

        if not os.path.isdir(repo_path):
            return "(Repository path not accessible)"

        found_files = []
        for root, dirs, files in os.walk(repo_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in {
                ".git", "__pycache__", "node_modules", "release", ".venv"
            }]
            for fname in files:
                rel_path = os.path.relpath(os.path.join(root, fname), repo_path)
                for pattern in target_patterns:
                    if re.match(pattern, rel_path.lower()):
                        found_files.append(rel_path)
                        break

        # Limit number of files
        found_files = found_files[:max_files]

        for rel_path in found_files:
            full_path = os.path.join(repo_path, rel_path)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if len(content) > max_file_size:
                    content = content[:max_file_size] + "\n... (truncated)"
                files_content.append(f"--- {rel_path} ---\n{content}")
            except Exception:
                pass

        if not files_content:
            return "(No matching files found in repository)"

        return "\n\n".join(files_content)

    def _build_generation_prompt(
        self,
        jira_analysis: str,
        force_fail_scenarios: str,
        repo_index: Optional[Dict],
        affected_files_content: str,
        repo_file_listing: str = "",
    ) -> str:
        """Build the prompt for AI force-fail generation."""
        # Build repo summary
        repo_summary = "Not available"
        if repo_index and "stats" in repo_index:
            stats = repo_index["stats"]
            repo_summary = (
                f"Total Files: {stats.get('total_files', 'N/A')}\n"
                f"File Types: {', '.join(f'{ext}({count})' for ext, count in sorted(stats.get('by_extension', {}).items(), key=lambda x: x[1], reverse=True)[:8])}"
            )

        prompt = f"""You are generating force-fail code changes for SSD test program validation.

## ⚠️ MANDATORY FILE CONSTRAINT — READ THIS FIRST ⚠️
The "file" field in every patch MUST use an EXACT path from the file listing below.
Files mentioned in the JIRA analysis (e.g. SendGearmanRequest.py) may NOT exist in this local repository clone.
**If a file mentioned in the JIRA analysis is NOT in the listing below, you MUST choose a different file from the listing that is functionally related.**
Any patch referencing a file not in this listing will be REJECTED and the case will be disabled.

### Complete Repository File Listing (ONLY these files exist):
```
{repo_file_listing}
```

### Affected File Contents (use these for original_lines matching):
{affected_files_content}

---

**JIRA Change Request Analysis:**
{jira_analysis}

**Test Scenarios (Force Fail Cases):**
{force_fail_scenarios if force_fail_scenarios else "(No specific force-fail scenarios extracted — analyze the JIRA change and generate appropriate force-fail cases)"}

**Repository Structure:**
{repo_summary}

**Common Force-Fail Patterns for SSD Test Programs:**
- Limit violations: Change threshold values to trigger out-of-range failures
- Rule inversions: Flip pass/fail conditions in rule files
- Config overrides: Set invalid configuration values
- Disabled checks: Comment out or bypass validation logic
- Wrong references: Point to non-existent FIDs, wrong product codes
- Boundary violations: Set values just outside acceptable ranges
- Return code changes: Change pass return codes to fail codes

**CRITICAL RULES:**
1. For each force-fail test case, generate the MINIMAL code changes needed to cause a specific, detectable failure
2. Each change must be reversible and clearly documented
3. Only modify files directly related to the test scenario — NEVER modify framework/infrastructure code
4. Ensure changes will cause a DETECTABLE failure (not a silent one)
5. The original_lines MUST exactly match what exists in the "Affected File Contents" section above (including whitespace)
6. Keep modifications small and targeted — prefer changing values over restructuring code
7. Maximum {self._max_patches} patches per force-fail case
8. **ONLY use files from the "Complete Repository File Listing". Do NOT use file names from the JIRA analysis if they are not in the listing. Cross-check EVERY "file" field against the listing before outputting.**
9. The "file" field MUST be a verbatim path from the listing — no guessing, no inventing
10. If the JIRA mentions a file that doesn't exist in the listing, find the closest matching file in the listing and use that instead

**Output Format:**
Return ONLY a valid JSON object with this exact structure (no markdown fences, no extra text):
{FORCE_FAIL_OUTPUT_SCHEMA}
"""
        return prompt

    def _parse_ai_response(self, response: str) -> List[ForceFailCase]:
        """
        Parse the AI response into ForceFailCase objects.
        Handles both clean JSON and JSON embedded in markdown code fences.
        """
        # Try to extract JSON from the response
        json_str = response.strip()

        # Remove markdown code fences if present
        if "```json" in json_str:
            match = re.search(r"```json\s*(.*?)\s*```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1)
        elif "```" in json_str:
            match = re.search(r"```\s*(.*?)\s*```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            self._log(f"⚠ Failed to parse AI response as JSON: {e}", "warning")
            self._log(f"  Response preview: {response[:200]}...", "debug")
            return []

        cases = []
        for case_data in data.get("force_fail_cases", []):
            try:
                case = ForceFailCase.from_dict(case_data)
                if case.test_id and case.patches:
                    cases.append(case)
                else:
                    self._log(f"⚠ Skipping case with missing test_id or patches", "warning")
            except Exception as e:
                self._log(f"⚠ Failed to parse force-fail case: {e}", "warning")

        return cases

    # ──────────────────────────────────────────────────────────────────────
    # STEP 2: VALIDATION
    # ──────────────────────────────────────────────────────────────────────

    def validate_patches(
        self, repo_path: str, cases: List[ForceFailCase]
    ) -> PatchValidationResult:
        """
        Validate that all patches can be applied cleanly to the repo.

        Checks:
          - All target files exist
          - original_lines match actual file content (fuzzy whitespace match)
          - modified_lines are non-empty

        Returns:
            PatchValidationResult with valid=True/False and error details
        """
        self._log("Validating force-fail patches...")
        errors = []
        warnings = []

        enabled_cases = [c for c in cases if c.enabled]
        if not enabled_cases:
            errors.append("No enabled force-fail cases to validate")
            return PatchValidationResult(valid=False, errors=errors)

        for case in enabled_cases:
            for patch in case.patches:
                file_path = os.path.join(repo_path, patch.file)

                # Check file exists
                if not os.path.isfile(file_path):
                    errors.append(
                        f"[{case.test_id}] File not found: {patch.file}"
                    )
                    continue

                # Read file content
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception as e:
                    errors.append(
                        f"[{case.test_id}] Cannot read {patch.file}: {e}"
                    )
                    continue

                # Check original_lines exist in the file
                if not patch.original_lines:
                    warnings.append(
                        f"[{case.test_id}] Empty original_lines for {patch.file}"
                    )
                    continue

                original_block = "\n".join(patch.original_lines)
                if not self._fuzzy_find(content, original_block):
                    errors.append(
                        f"[{case.test_id}] Original lines not found in {patch.file}:\n"
                        f"  Expected: {original_block[:100]}..."
                    )

                # Check modified_lines are non-empty
                if not patch.modified_lines:
                    warnings.append(
                        f"[{case.test_id}] Empty modified_lines for {patch.file} — "
                        "this will delete the original lines"
                    )

        valid = len(errors) == 0
        if valid:
            self._log(f"✓ All patches validated successfully ({len(warnings)} warning(s))")
        else:
            self._log(f"✗ Patch validation failed: {len(errors)} error(s)", "error")
            for err in errors:
                self._log(f"  ✗ {err}", "error")

        for warn in warnings:
            self._log(f"  ⚠ {warn}", "warning")

        return PatchValidationResult(valid=valid, errors=errors, warnings=warnings)

    def _fuzzy_find(self, content: str, search_block: str) -> bool:
        """
        Check if search_block exists in content with whitespace tolerance.
        Normalizes leading/trailing whitespace per line for comparison.
        """
        def normalize(text: str) -> str:
            return "\n".join(line.strip() for line in text.strip().split("\n"))

        return normalize(search_block) in normalize(content)

    # ──────────────────────────────────────────────────────────────────────
    # STEP 3: PATCHED REPO CREATION
    # ──────────────────────────────────────────────────────────────────────

    def create_patched_repo(
        self,
        repo_path: str,
        cases: List[ForceFailCase],
        jira_key: str = "",
    ) -> Optional[str]:
        """
        Create a temporary copy of the repo and apply force-fail patches.

        Args:
            repo_path: Path to the original TP repository
            cases:     List of ForceFailCase objects (only enabled ones are applied)
            jira_key:  JIRA key for naming the temp directory

        Returns:
            Path to the patched repo copy, or None on failure
        """
        enabled_cases = [c for c in cases if c.enabled]
        if not enabled_cases:
            self._log("✗ No enabled force-fail cases to apply", "error")
            return None

        # Determine temp directory location
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_name = f"{self._temp_prefix}{jira_key}_{timestamp}"

        if self._temp_dir:
            temp_base = self._temp_dir
        else:
            temp_base = tempfile.gettempdir()

        patched_path = os.path.join(temp_base, dir_name)

        # Copy the repo
        self._log(f"Creating patched repo copy: {patched_path}")
        try:
            shutil.copytree(
                repo_path,
                patched_path,
                ignore=shutil.ignore_patterns(
                    ".git", "__pycache__", "node_modules", "release",
                    "*.pyc", "*.pyo", ".bento_lock", ".bento_status",
                    "*.tgz", "*.o", "*.d", "*.a", "*.map", "*.elf",
                ),
            )
            self._log("✓ Repository copied successfully")
        except Exception as e:
            self._log(f"✗ Failed to copy repository: {e}", "error")
            return None

        # Apply patches
        applied_count = 0
        failed_count = 0

        for case in enabled_cases:
            self._log(f"Applying patches for {case.test_id}: {case.description}")
            for patch in case.patches:
                success = self._apply_patch(patched_path, patch, case.test_id)
                if success:
                    applied_count += 1
                else:
                    failed_count += 1

        if failed_count > 0:
            self._log(
                f"⚠ {failed_count} patch(es) failed to apply — "
                "aborting and cleaning up",
                "error",
            )
            self.cleanup_patched_repo(patched_path)
            return None

        self._log(f"✓ All {applied_count} patch(es) applied successfully")

        # Write a manifest of what was changed
        self._write_patch_manifest(patched_path, enabled_cases, jira_key)

        return patched_path

    def _apply_patch(
        self, repo_path: str, patch: FilePatch, case_id: str
    ) -> bool:
        """
        Apply a single FilePatch to the patched repo.
        Uses fuzzy matching to find the original lines and replace them.
        """
        file_path = os.path.join(repo_path, patch.file)

        if not os.path.isfile(file_path):
            self._log(f"  ✗ [{case_id}] File not found: {patch.file}", "error")
            return False

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            self._log(f"  ✗ [{case_id}] Cannot read {patch.file}: {e}", "error")
            return False

        original_block = "\n".join(patch.original_lines)
        modified_block = "\n".join(patch.modified_lines)

        # Try exact match first
        if original_block in content:
            new_content = content.replace(original_block, modified_block, 1)
        else:
            # Try whitespace-normalized match
            new_content = self._fuzzy_replace(content, original_block, modified_block)
            if new_content is None:
                self._log(
                    f"  ✗ [{case_id}] Original lines not found in {patch.file}",
                    "error",
                )
                return False

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            self._log(f"  ✓ [{case_id}] Patched {patch.file}")
            return True
        except Exception as e:
            self._log(f"  ✗ [{case_id}] Cannot write {patch.file}: {e}", "error")
            return False

    def _fuzzy_replace(
        self, content: str, original: str, replacement: str
    ) -> Optional[str]:
        """
        Replace original with replacement using whitespace-tolerant matching.
        Returns the modified content, or None if original was not found.
        """
        content_lines = content.split("\n")
        original_lines = original.split("\n")
        orig_stripped = [line.strip() for line in original_lines]

        # Sliding window search
        for i in range(len(content_lines) - len(original_lines) + 1):
            window = [content_lines[i + j].strip() for j in range(len(original_lines))]
            if window == orig_stripped:
                # Found match — preserve indentation of first line
                indent = ""
                first_line = content_lines[i]
                stripped_first = first_line.lstrip()
                if stripped_first:
                    indent = first_line[: len(first_line) - len(stripped_first)]

                # Build replacement with original indentation
                replacement_lines = replacement.split("\n")
                indented_replacement = []
                for j, rline in enumerate(replacement_lines):
                    if j == 0:
                        indented_replacement.append(indent + rline.lstrip())
                    else:
                        # Preserve relative indentation from the replacement
                        indented_replacement.append(indent + rline.lstrip())

                new_lines = (
                    content_lines[:i]
                    + indented_replacement
                    + content_lines[i + len(original_lines):]
                )
                return "\n".join(new_lines)

        return None

    def _write_patch_manifest(
        self, patched_path: str, cases: List[ForceFailCase], jira_key: str
    ):
        """Write a JSON manifest of applied patches for audit trail."""
        manifest = {
            "jira_key": jira_key,
            "generated_at": datetime.now().isoformat(),
            "generator": "BENTO ForceFailGenerator",
            "cases": [case.to_dict() for case in cases],
        }
        manifest_path = os.path.join(patched_path, ".bento_force_fail_manifest.json")
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            self._log(f"✓ Patch manifest written: {manifest_path}")
        except Exception as e:
            self._log(f"⚠ Could not write manifest: {e}", "warning")

    # ──────────────────────────────────────────────────────────────────────
    # STEP 4: CLEANUP
    # ──────────────────────────────────────────────────────────────────────

    def cleanup_patched_repo(self, patched_path: str):
        """Remove the temporary patched repo copy."""
        if not patched_path or not os.path.isdir(patched_path):
            return

        self._log(f"Cleaning up patched repo: {patched_path}")
        try:
            shutil.rmtree(patched_path, ignore_errors=True)
            self._log("✓ Patched repo cleaned up")
        except Exception as e:
            self._log(f"⚠ Cleanup failed: {e}", "warning")

    # ──────────────────────────────────────────────────────────────────────
    # SERIALIZATION (for workflow file storage)
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def cases_to_json(cases: List[ForceFailCase]) -> str:
        """Serialize force-fail cases to JSON string for workflow storage."""
        return json.dumps(
            [case.to_dict() for case in cases],
            indent=2,
        )

    @staticmethod
    def cases_from_json(json_str: str) -> List[ForceFailCase]:
        """Deserialize force-fail cases from JSON string."""
        try:
            data = json.loads(json_str)
            return [ForceFailCase.from_dict(d) for d in data]
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def cases_to_display(cases: List[ForceFailCase]) -> str:
        """
        Format force-fail cases for human-readable display in the GUI.
        Shows each case with its patches in a diff-like format.
        """
        lines = []
        for case in cases:
            status = "✓" if case.enabled else "○"
            lines.append(f"{status} [{case.test_id}] {case.description}")
            lines.append(f"  Rationale: {case.rationale}")
            for patch in case.patches:
                lines.append(f"  File: {patch.file} (line ~{patch.line_number})")
                lines.append(f"  --- Original ---")
                for ol in patch.original_lines:
                    lines.append(f"  - {ol}")
                lines.append(f"  +++ Modified +++")
                for ml in patch.modified_lines:
                    lines.append(f"  + {ml}")
                lines.append("")
            lines.append("")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # DISK SPACE CHECK
    # ──────────────────────────────────────────────────────────────────────

    def check_disk_space(self, repo_path: str) -> Tuple[bool, str]:
        """
        Check if there is enough disk space for a repo copy.
        Returns (ok, message).
        """
        try:
            # Estimate repo size (excluding ignored dirs)
            total_size = 0
            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if d not in {
                    ".git", "__pycache__", "node_modules", "release"
                }]
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        total_size += os.path.getsize(fpath)
                    except OSError:
                        pass

            # Check free space on temp drive
            if self._temp_dir:
                target = self._temp_dir
            else:
                target = tempfile.gettempdir()

            free_space = shutil.disk_usage(target).free
            needed = total_size * 1.2  # 20% buffer

            size_mb = total_size / (1024 * 1024)
            free_mb = free_space / (1024 * 1024)

            if free_space > needed:
                return True, f"OK — repo ~{size_mb:.0f} MB, free ~{free_mb:.0f} MB"
            else:
                return False, (
                    f"Insufficient disk space: repo ~{size_mb:.0f} MB, "
                    f"free ~{free_mb:.0f} MB on {target}"
                )
        except Exception as e:
            return True, f"Could not check disk space: {e} (proceeding anyway)"

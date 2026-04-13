#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fault-tolerant wrapper for recipe_selection.py.

This script monkey-patches the rule engine's __getitem__() method to
gracefully handle non-critical rule failures (e.g., BOISE_PROGRAM_RECIPE
failing when SITE_NAME=PENANG) instead of crashing the entire process.

The upstream recipe_selection.py rule engine (attributes.py Solutions class)
eagerly resolves ALL rules via calc_all_rules(), including site-specific
rules that don't apply to the current site.  When a site-specific rule
table has no matching row for the current product configuration, it raises:

    ValueError: Hit the end of the table when looking up the value for rule <RULE>

The problem is that rules form a dependency tree.  For example:

    TEST_PROGRAM_PATH  depends on  PROGRAM_RECIPE  depends on  SITE_NAME
    When SITE_NAME=PENANG, PROGRAM_RECIPE resolves to PENANG_PROGRAM_RECIPE
    PENANG_PROGRAM_RECIPE is then resolved as a separate rule table

If PENANG_PROGRAM_RECIPE fails, the error propagates UP through
TEST_PROGRAM_PATH (a non-skippable rule), crashing the entire process.

This wrapper patches __getitem__() to intercept the error at the SOURCE
(the skippable rule itself), returning a placeholder value 'N/A' so that
parent rules can continue resolving.

Usage:
    python recipe_fault_tolerant.py <recipe_selection_dir> <tmptravl_path> [--tt_format dat]

The wrapper:
1. Adds <recipe_selection_dir> to sys.path
2. Changes cwd to <recipe_selection_dir>
3. Patches Solutions.__getitem__() to be fault-tolerant for skippable rules
4. Runs the real recipe_selection.py with the remaining arguments

NOTE: This script must be run with the SAME Python interpreter that
recipe_selection.py expects (typically Python 2.7).
"""

import sys
import os
import traceback

# ---------------------------------------------------------------------------
# Rule name patterns that can safely fail.
#
# Two categories:
# 1. Site-specific rules — recipe/jobpath rules for OTHER sites that don't
#    affect the current site's output.  The current site's rules are NOT
#    skipped so they can produce the actual recipe/test-program output.
# 2. Platform-specific checks — validation rules that require tester
#    equipment data (software versions, machine model, etc.) which BENTO
#    does not have.  These are defined in the .rul file as
#    PLATFORM_SPECIFIC_CHECKS and run AFTER recipe selection in production
#    CAT.  They don't affect the recipe/test-program output.
# ---------------------------------------------------------------------------

# Map of SITE_NAME values to their rule name prefixes.
# Used to determine which site-specific rules belong to the CURRENT site
# (and therefore should NOT be skipped).
_SITE_RULE_PREFIXES = {
    'BOISE':    ['BOISE_'],
    'SINGAPORE':['SINGAPORE_'],
    'PENANG':   ['PENANG_'],
    'SANAND':   ['SANAND_', 'ATMES_SANAND_'],
    'XIAN':     ['XIAN_'],
}

# All known site-specific rule names (for all sites)
_ALL_SITE_RULES = [
    'BOISE_PROGRAM_RECIPE', 'BOISE_JOBPATH',
    'SINGAPORE_PROGRAM_RECIPE', 'SINGAPORE_JOBPATH',
    'PENANG_PROGRAM_RECIPE', 'PENANG_JOBPATH',
    'SANAND_PROGRAM_RECIPE', 'SANAND_JOBPATH',
    'ATMES_SANAND_PROGRAM_RECIPE', 'ATMES_SANAND_JOBPATH',
]

# Platform-specific checks — always skippable (require tester equipment data)
_PLATFORM_CHECK_RULES = [
    'VERSION_CHECK',
    'MACHINE_CHECKING',
    'PASS_MFG_STATUS_CHECK',
    'PROG_RECIPE_CHECK',
    'STYLUS_CHECK',
]

# Suffix patterns for platform-specific check rules.
# Any rule ending with one of these suffixes is treated as a platform check
# and can be safely skipped.  This future-proofs against new *_CHECK rules
# being added to the rule tables that BENTO doesn't have data for.
_PLATFORM_CHECK_SUFFIXES = ['_CHECK', '_CHECKING']

# Broader pattern fragments for future-proofing site rules
_SITE_RULE_FRAGMENTS = ['_PROGRAM_RECIPE', '_JOBPATH']

# Track skipped rules for diagnostics (populated during __getitem__ calls)
_skipped_rules = []

# Current site name — set by main() after reading tmptravl
_current_site = None


def _read_site_from_tmptravl(tmptravl_path):
    """Read SITE_NAME from the tmptravl file."""
    try:
        with open(tmptravl_path, 'r') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('SITE_NAME:'):
                    return stripped.split(':', 1)[1].strip().upper()
    except (IOError, OSError):
        pass
    return None


def _is_skippable_rule(rule_name):
    """Check if a rule failure can be safely ignored.

    A rule is skippable if:
    - It's a platform-specific check (VERSION_CHECK, etc.)
    - It's a site-specific rule for a DIFFERENT site than the current one
    """
    # Platform checks are always skippable (explicit list)
    if rule_name in _PLATFORM_CHECK_RULES:
        return True

    # Platform checks by suffix pattern (future-proofing)
    for suffix in _PLATFORM_CHECK_SUFFIXES:
        if rule_name.endswith(suffix):
            return True

    # Check if it's a site-specific rule
    is_site_rule = rule_name in _ALL_SITE_RULES
    if not is_site_rule:
        for fragment in _SITE_RULE_FRAGMENTS:
            if fragment in rule_name:
                is_site_rule = True
                break

    if not is_site_rule:
        return False

    # It's a site-specific rule — only skip if it's NOT for the current site
    if _current_site and _current_site in _SITE_RULE_PREFIXES:
        current_prefixes = _SITE_RULE_PREFIXES[_current_site]
        for prefix in current_prefixes:
            if rule_name.startswith(prefix):
                # This rule belongs to the CURRENT site — do NOT skip
                return False

    # It's a site rule for a different site — safe to skip
    return True


def _patch_getitem(solutions_class):
    """Monkey-patch Solutions.__getitem__ to be fault-tolerant.

    This patches the rule resolution at the lowest level — inside
    __getitem__ itself.  When a skippable rule fails (ValueError from
    "Hit the end of the table"), we catch it RIGHT THERE and return
    a placeholder 'N/A' value.  This prevents the error from
    propagating up through parent rules like TEST_PROGRAM_PATH.

    The original __getitem__ flow:
    1. Look up rule definition
    2. Recursively resolve dependency columns via self[dep]
    3. Iterate table rows to find a match
    4. eval() the matched value
    5. Replace rule definition with resolved value

    Our patch wraps step 3-4: if the table lookup fails for a
    skippable rule, we return 'N/A' instead of raising.
    """
    _original_getitem = solutions_class.__getitem__

    def patched_getitem(self, key):
        try:
            return _original_getitem(self, key)
        except (ValueError, KeyError) as e:
            error_msg = str(e)
            # Check if this specific rule (key) is skippable
            if _is_skippable_rule(key):
                # Set a resolved placeholder so future lookups don't retry
                self[key] = ((), [((), repr('N/A'))])
                if key not in _skipped_rules:
                    _skipped_rules.append(key)
                    sys.stderr.write(
                        "RECIPE_WRAPPER_INFO: Skipped rule %s: %s\n"
                        % (key, error_msg)
                    )
                return 'N/A'
            # For non-skippable rules, check if the error mentions a
            # skippable rule (recursive resolution failure).
            # Extract the failing rule name from the error message and
            # check if THAT rule is skippable (not just a string match).
            # This prevents false positives where the current site's
            # rule name appears in the error message.
            import re as _re
            match = _re.search(
                r'rule\s+(\w+)', error_msg
            )
            if match:
                failing_rule = match.group(1)
                if failing_rule != key and _is_skippable_rule(failing_rule):
                    sys.stderr.write(
                        "RECIPE_WRAPPER_INFO: Rule %s failed due to "
                        "skippable sub-rule %s: %s\n"
                        % (key, failing_rule, error_msg)
                    )
                    return 'N/A'
            # Truly non-skippable error — re-raise
            raise

    solutions_class.__getitem__ = patched_getitem


def main():
    if len(sys.argv) < 3:
        sys.stderr.write(
            "Usage: recipe_fault_tolerant.py <recipe_selection_dir> "
            "<tmptravl_path> [--tt_format dat]\n"
        )
        sys.exit(1)

    recipe_dir = sys.argv[1]
    tmptravl_path = sys.argv[2]
    extra_args = sys.argv[3:]

    # Validate paths
    if not os.path.isdir(recipe_dir):
        sys.stderr.write("ERROR: Recipe directory not found: %s\n" % recipe_dir)
        sys.exit(1)

    # Read SITE_NAME from tmptravl to determine which site-specific rules
    # belong to the current site (and should NOT be skipped).
    global _current_site
    _current_site = _read_site_from_tmptravl(tmptravl_path)
    if _current_site:
        sys.stderr.write(
            "RECIPE_WRAPPER_INFO: Site=%s (will preserve %s_* rules)\n"
            % (_current_site, _current_site)
        )
    else:
        sys.stderr.write(
            "RECIPE_WRAPPER_WARNING: Could not read SITE_NAME from tmptravl "
            "— all site-specific rules will be skippable\n"
        )

    # Determine the versioned recipe directory
    # The root recipe_selection.py reads RELEASE_VERSION.txt and delegates
    # to the versioned subdirectory.  We need to find that version.
    release_version_file = os.path.join(recipe_dir, "RELEASE_VERSION.txt")
    versioned_dir = recipe_dir  # default: assume recipe_dir IS the versioned dir

    if os.path.isfile(release_version_file):
        with open(release_version_file, 'r') as f:
            for line in f:
                if "REL_VERSION:" in line:
                    version = line.split(":")[1].strip()
                    candidate = os.path.join(recipe_dir, version)
                    if os.path.isdir(candidate):
                        versioned_dir = candidate
                    break

    # Add versioned dir to path so we can import the rule engine
    if versioned_dir not in sys.path:
        sys.path.insert(0, versioned_dir)

    # Change to versioned dir (recipe_selection.py expects this)
    os.chdir(versioned_dir)

    # Import and patch the rule engine BEFORE recipe_selection.py uses it.
    #
    # CRITICAL: SSDrules_loader.py imports `attributes` as a BARE module
    # (not `rules_mgr.attributes`) by inserting the `rules_mgr/` directory
    # into sys.path and doing `__import__("attributes")`.  We MUST import
    # it the same way so we patch the SAME class that gets used at runtime.
    # If we used `from rules_mgr.attributes import Solutions`, Python would
    # cache it as a separate module object in sys.modules and our patch
    # would be applied to a different Solutions class.
    try:
        rules_mgr_path = os.path.join(versioned_dir, 'rules_mgr')
        if os.path.isdir(rules_mgr_path) and rules_mgr_path not in sys.path:
            sys.path.insert(0, rules_mgr_path)

        # Import as bare "attributes" — same as SSDrules_loader does
        import attributes as attr_mod
        Solutions = attr_mod.Solutions
        _patch_getitem(Solutions)
        sys.stderr.write("RECIPE_WRAPPER_INFO: Patched __getitem__ for fault tolerance\n")
    except ImportError as e:
        sys.stderr.write(
            "RECIPE_WRAPPER_WARNING: Could not import attributes "
            "to patch __getitem__: %s\n" % str(e)
        )
        # Continue anyway — the real recipe_selection.py will import it

    # Build the command line for the real recipe_selection.py
    real_script = os.path.join(versioned_dir, 'recipe_selection.py')
    if not os.path.isfile(real_script):
        sys.stderr.write("ERROR: recipe_selection.py not found at: %s\n" % real_script)
        sys.exit(1)

    # Set up sys.argv as if recipe_selection.py was called directly
    sys.argv = [real_script, tmptravl_path] + extra_args

    # Execute the real recipe_selection.py in this process
    # (so our monkey-patch is active).
    #
    # IMPORTANT: We must provide a proper global namespace dict that
    # simulates __main__ module scope.  Without this, execfile() runs
    # in the caller's local scope, and module-level constants like
    # TT_OPTION_PY / TT_OPTION_DAT won't be visible to functions
    # defined in the script (their __globals__ would be wrong).
    script_globals = {
        '__name__': '__main__',
        '__file__': real_script,
        '__builtins__': __builtins__,
    }
    try:
        # Python 2 has execfile; Python 3 uses exec+open
        if hasattr(__builtins__, 'execfile') or (
            isinstance(__builtins__, dict) and 'execfile' in __builtins__
        ):
            execfile(real_script, script_globals)
        else:
            with open(real_script, 'r') as f:
                exec(compile(f.read(), real_script, 'exec'), script_globals)
    except SystemExit as e:
        # Emit summary of skipped rules before exiting
        if _skipped_rules:
            sys.stderr.write(
                "RECIPE_WRAPPER_INFO: Skipped %d non-critical rule(s): %s\n"
                % (len(_skipped_rules), ", ".join(sorted(_skipped_rules)))
            )
        # recipe_selection.py may call sys.exit — propagate it
        sys.exit(e.code if hasattr(e, 'code') else 0)
    except Exception as e:
        # Emit summary of skipped rules before reporting error
        if _skipped_rules:
            sys.stderr.write(
                "RECIPE_WRAPPER_INFO: Skipped %d non-critical rule(s): %s\n"
                % (len(_skipped_rules), ", ".join(sorted(_skipped_rules)))
            )
        sys.stderr.write("RECIPE_WRAPPER_ERROR: %s\n" % str(e))
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

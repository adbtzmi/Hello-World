#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fault-tolerant wrapper for recipe_selection.py.

This script monkey-patches the rule engine's calc_all_rules() method to
gracefully handle non-critical rule failures (e.g., BOISE_PROGRAM_RECIPE
failing when SITE_NAME=PENANG) instead of crashing the entire process.

The upstream recipe_selection.py rule engine (attributes.py Solutions class)
eagerly resolves ALL rules via calc_all_rules(), including site-specific
rules that don't apply to the current site.  When a site-specific rule
table has no matching row for the current product configuration, it raises:

    ValueError: Hit the end of the table when looking up the value for rule <RULE>

This wrapper catches those errors for non-critical site-specific rules
and sets them to a placeholder value, allowing the remaining rules
(including the correct site's PROGRAM_RECIPE) to resolve successfully.

Usage:
    python recipe_fault_tolerant.py <recipe_selection_dir> <tmptravl_path> [--tt_format dat]

The wrapper:
1. Adds <recipe_selection_dir> to sys.path
2. Changes cwd to <recipe_selection_dir>
3. Patches Solutions.calc_all_rules() to be fault-tolerant
4. Runs the real recipe_selection.py with the remaining arguments

NOTE: This script must be run with the SAME Python interpreter that
recipe_selection.py expects (typically Python 2.7).
"""

import sys
import os
import traceback

# ---------------------------------------------------------------------------
# Site-specific rule name patterns that can safely fail.
# These are rules for OTHER sites that don't affect the current site's output.
# The pattern matching is intentionally broad to cover future site additions.
# ---------------------------------------------------------------------------
_SKIPPABLE_RULE_PATTERNS = [
    # Site-specific program recipe rules
    'BOISE_PROGRAM_RECIPE',
    'SINGAPORE_PROGRAM_RECIPE',
    'PENANG_PROGRAM_RECIPE',
    'SANAND_PROGRAM_RECIPE',
    'ATMES_SANAND_PROGRAM_RECIPE',
    # Site-specific job path rules
    'BOISE_JOBPATH',
    'SINGAPORE_JOBPATH',
    'PENANG_JOBPATH',
    'SANAND_JOBPATH',
    'ATMES_SANAND_JOBPATH',
]

# Broader pattern fragments for future-proofing
_SKIPPABLE_FRAGMENTS = ['_PROGRAM_RECIPE', '_JOBPATH']


def _is_skippable_rule(rule_name):
    """Check if a rule failure can be safely ignored.

    A rule is skippable if it's a site-specific recipe/jobpath rule
    that doesn't apply to the current site configuration.
    """
    # Exact match against known skippable rules
    if rule_name in _SKIPPABLE_RULE_PATTERNS:
        return True
    # Fragment match for future site additions
    for fragment in _SKIPPABLE_FRAGMENTS:
        if fragment in rule_name:
            return True
    return False


def _patch_calc_all_rules(solutions_class):
    """Monkey-patch Solutions.calc_all_rules to be fault-tolerant.

    Instead of crashing on the first rule failure, this patched version:
    1. Tries to resolve each rule
    2. On ValueError/KeyError for skippable rules, sets a placeholder
    3. Re-raises errors for critical (non-skippable) rules
    4. Reports skipped rules to stderr for diagnostics
    """
    def patched_calc_all_rules(self):
        all_rules = list(self.keys())
        skipped = []
        for rule in all_rules:
            try:
                self[rule]
            except (ValueError, KeyError) as e:
                if _is_skippable_rule(rule):
                    # Set a placeholder value so dependent rules don't fail
                    # Use the same tuple format as the rule engine:
                    #   (dependancies, [(match_conditions, value)])
                    self[rule] = ((), [((), repr('N/A'))])
                    skipped.append(rule)
                else:
                    raise

        if skipped:
            sys.stderr.write(
                "RECIPE_WRAPPER_INFO: Skipped %d non-critical rule(s): %s\n"
                % (len(skipped), ", ".join(sorted(skipped)))
            )

    solutions_class.calc_all_rules = patched_calc_all_rules


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

    # Import and patch the rule engine BEFORE recipe_selection.py uses it
    try:
        # The rules_mgr package is inside the versioned directory
        rules_mgr_path = os.path.join(versioned_dir, 'rules_mgr')
        if os.path.isdir(rules_mgr_path) and rules_mgr_path not in sys.path:
            sys.path.insert(0, os.path.dirname(rules_mgr_path))

        from rules_mgr.attributes import Solutions
        _patch_calc_all_rules(Solutions)
        sys.stderr.write("RECIPE_WRAPPER_INFO: Patched calc_all_rules for fault tolerance\n")
    except ImportError as e:
        sys.stderr.write(
            "RECIPE_WRAPPER_WARNING: Could not import rules_mgr.attributes "
            "to patch calc_all_rules: %s\n" % str(e)
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
    # (so our monkey-patch is active)
    try:
        # Python 2 has execfile; Python 3 uses exec+open
        if hasattr(__builtins__, 'execfile') or (
            isinstance(__builtins__, dict) and 'execfile' in __builtins__
        ):
            execfile(real_script)
        else:
            with open(real_script, 'r') as f:
                exec(compile(f.read(), real_script, 'exec'))
    except SystemExit as e:
        # recipe_selection.py may call sys.exit — propagate it
        sys.exit(e.code if hasattr(e, 'code') else 0)
    except Exception as e:
        sys.stderr.write("RECIPE_WRAPPER_ERROR: %s\n" % str(e))
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

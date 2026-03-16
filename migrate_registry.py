#!/usr/bin/env python3
"""
BENTO Registry Migration Script
================================
Migrates existing bento_testers.json to the new unified format.

Run this once after upgrading to the unified registry system.
"""

import os
import json

def migrate():
    local_file = "bento_testers.json"
    shared_file = r"P:\temp\BENTO\bento_testers.json"
    
    print("BENTO Registry Migration")
    print("=" * 60)
    
    # Check if local file exists
    if not os.path.exists(local_file):
        print(f"✗ Local registry not found: {local_file}")
        print("  Nothing to migrate. New installations will use defaults.")
        return
    
    # Load local registry
    with open(local_file, "r") as f:
        local_data = json.load(f)
    
    print(f"✓ Found {len(local_data)} testers in local registry")
    
    # Convert to new format
    new_format = {}
    for key, val in local_data.items():
        if isinstance(val, list):
            if len(val) == 4:
                # Already new format
                hostname, env, repo_dir, build_cmd = val
            elif len(val) == 2:
                # Old format - add defaults
                hostname, env = val
                repo_dir = r"C:\xi\adv_ibir_master"
                # CNFG uses a different build target
                build_cmd = "make release_supermicro" if env.upper() == "CNFG" else "make release"
                print(f"  Migrating {key}: added default repo_dir and build_cmd ({build_cmd})")
            else:
                print(f"  ⚠ Skipping invalid entry: {key}")
                continue
            
            new_format[key] = {
                "hostname":  hostname,
                "env":       env,
                "repo_dir":  repo_dir,
                "build_cmd": build_cmd,
            }
        elif isinstance(val, dict):
            # Already new dict format - pass through directly
            hostname  = val.get("hostname", "")
            env       = val.get("env", "")
            repo_dir  = val.get("repo_dir", r"C:\xi\adv_ibir_master")
            build_cmd = val.get("build_cmd", "make release")
            print(f"  {key}: already in new format")
            new_format[key] = {
                "hostname":  hostname,
                "env":       env,
                "repo_dir":  repo_dir,
                "build_cmd": build_cmd,
            }
    
    # Write to shared folder
    try:
        os.makedirs(os.path.dirname(shared_file), exist_ok=True)
        with open(shared_file, "w") as f:
            json.dump(new_format, f, indent=2)
        print(f"✓ Migrated registry to {shared_file}")
    except Exception as e:
        print(f"✗ Failed to write shared registry: {e}")
        return
    
    # Update local file to new format
    local_new = {k: [v["hostname"], v["env"], v["repo_dir"], v["build_cmd"]] 
                 for k, v in new_format.items()}
    with open(local_file, "w") as f:
        json.dump(local_new, f, indent=2)
    print(f"✓ Updated local registry to new format")
    
    print("\n" + "=" * 60)
    print("Migration complete!")
    print("\nNext steps:")
    print("1. Verify testers in GUI (Home tab)")
    print("2. Edit any tester to update repo_dir or build_cmd if needed")
    print("3. Watchers will auto-load from shared registry on next run")

if __name__ == "__main__":
    migrate()

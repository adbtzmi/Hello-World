#!/usr/bin/env python3
"""
controller/repo_controller.py
==============================
Repository Controller - Phase 2

Handles repository operations.
Extracted from gui/app.py lines 1477-1615, 3838-3873.
"""

import os
import logging
import threading

logger = logging.getLogger("bento_app")


class RepoController:
    """
    Manages repository operations including cloning and branch creation.
    
    Supports:
    - Fetching repository list from Bitbucket
    - Cloning repositories
    - Creating feature branches
    - Repository/branch filtering
    """
    
    def __init__(self, context, workflow_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.analyzer = context.analyzer
        self._running = False
        logger.info("RepoController initialized.")
    
    def is_running(self):
        return self._running
    
    def fetch_repos(self, callback=None):
        """
        Fetch repository list from Bitbucket.
        
        Args:
            callback: Optional callback(repos) to call on completion
        """
        def _fetch():
            self._running = True
            try:
                self.context.log("\n[Fetching Repositories from Bitbucket]")
                repos = self.analyzer.fetch_repositories()
                
                if repos:
                    self.context.repos = repos
                    self.context.log(f"✓ Fetched {len(repos)} repositories")
                    if callback:
                        self.context.root.after(0, lambda: callback(repos))
                else:
                    self.context.log("✗ Failed to fetch repositories")
                    if callback:
                        self.context.root.after(0, lambda: callback([]))
            
            except Exception as e:
                logger.error(f"fetch_repos error: {e}")
                self.context.log(f"✗ Error fetching repositories: {e}")
                if callback:
                    self.context.root.after(0, lambda: callback([]))
            finally:
                self._running = False
        
        threading.Thread(target=_fetch, daemon=True).start()
    
    def filter_repos(self, query):
        """
        Filter repositories by query string.
        
        Args:
            query: Search query
        
        Returns:
            List of matching repository names
        """
        if not query:
            return self.context.repos
        
        query_lower = query.lower()
        return [repo for repo in self.context.repos if query_lower in repo.lower()]
    
    def filter_branches(self, query):
        """
        Filter branches by query string.
        
        Args:
            query: Search query
        
        Returns:
            List of matching branch names
        """
        if not query:
            return self.context.branches
        
        query_lower = query.lower()
        return [branch for branch in self.context.branches if query_lower in branch.lower()]
    
    def clone_repo(self, repo, branch, issue_key, callback=None):
        """
        Clone repository only.
        
        Args:
            repo: Repository name
            branch: Base branch name
            issue_key: JIRA issue key
            callback: Optional callback(result) to call on completion
        """
        def _clone():
            self._running = True
            try:
                # Initialize workflow file
                self.workflow.init_workflow_file(issue_key)
                
                # Extract repo slug if needed
                if ' - ' in repo:
                    repo_slug = repo.split(' - ')[0].strip()
                else:
                    repo_slug = repo
                
                # Check if local repository already exists
                repos_dir = "Repos"
                expected_repo_path = os.path.join(repos_dir, f"{issue_key}_{repo_slug}")
                
                if os.path.exists(expected_repo_path):
                    # Check if it's a git repository
                    if os.path.exists(os.path.join(expected_repo_path, '.git')):
                        warning_msg = (
                            f"⚠️ WARNING: Local repository already exists!\n\n"
                            f"Path: {expected_repo_path}\n"
                            "This may indicate:\n"
                            "• You've already cloned this repository for this issue\n"
                            "• There may be uncommitted changes\n"
                            "• Previous work may be overwritten\n\n"
                            "Please check the existing repository before proceeding.\n"
                            "Consider using 'Load Workflow' if you want to continue previous work."
                        )
                        
                        self.context.log(f"⚠️ Local repository already exists: {expected_repo_path}")
                        if callback:
                            self.context.root.after(0, lambda: callback({
                                'success': False,
                                'error': 'Repository already exists',
                                'warning': warning_msg
                            }))
                        return
                    else:
                        # Directory exists but not a git repo
                        warning_msg = (
                            f"⚠️ WARNING: Directory already exists but is not a git repository!\n\n"
                            f"Path: {expected_repo_path}\n\n"
                            "Please remove or rename this directory before cloning."
                        )
                        
                        self.context.log(f"⚠️ Directory exists but not a git repo: {expected_repo_path}")
                        if callback:
                            self.context.root.after(0, lambda: callback({
                                'success': False,
                                'error': 'Directory exists but not a git repo',
                                'warning': warning_msg
                            }))
                        return
                
                self.context.log("\n=========start clone===========")
                self.context.log(f"[Cloning Repository: {repo_slug}]")
                repo_path = self.analyzer.clone_repository(repo_slug, branch, issue_key)
                self.context.log("============end clone==========")
                
                if repo_path:
                    result = (
                        f"Repository cloned successfully!\n\n"
                        f"Local path: {repo_path}\n"
                        f"Repository: {repo_slug}\n"
                        f"Base branch: {branch}\n"
                    )
                    
                    # Save to workflow file
                    self.workflow.save_workflow_step("REPOSITORY_PATH", repo_path)
                    self.workflow.save_workflow_step("REPOSITORY_INFO", result)
                    
                    self.context.log(f"✓ Repository cloned and saved to workflow")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': True,
                            'result': result,
                            'repo_path': repo_path
                        }))
                else:
                    self.context.log(f"✗ Failed to clone repository")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': False,
                            'error': 'Failed to clone repository'
                        }))
            
            except Exception as e:
                error_msg = str(e)
                logger.error(f"clone_repo error: {error_msg}")
                self.context.log(f"✗ Error cloning repository: {error_msg}")
                if callback:
                    self.context.root.after(0, lambda _m=error_msg: callback({
                        'success': False,
                        'error': _m
                    }))
            finally:
                self._running = False
        
        threading.Thread(target=_clone, daemon=True).start()
    
    def create_feature_branch(self, repo, branch, issue_key, feature_branch_input, callback=None):
        """
        Create feature branch only.
        
        Args:
            repo: Repository name
            branch: Base branch name
            issue_key: JIRA issue key
            feature_branch_input: Custom feature branch name (or empty for auto)
            callback: Optional callback(result) to call on completion
        """
        def _create():
            self._running = True
            try:
                # Extract repo slug if needed
                if ' - ' in repo:
                    repo_slug = repo.split(' - ')[0].strip()
                else:
                    repo_slug = repo
                
                # Get repo path from workflow or construct it
                repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
                if not repo_path:
                    repo_path = os.path.join("Repos", f"{issue_key}_{repo_slug}")
                
                if not os.path.exists(repo_path):
                    error_msg = f"Repository path not found: {repo_path}\nPlease clone the repository first."
                    self.context.log(f"✗ {error_msg}")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': False,
                            'error': error_msg
                        }))
                    return
                
                # Determine feature branch name
                if feature_branch_input and "if empty," not in feature_branch_input.lower() and feature_branch_input.lower() != "auto populate":
                    feature_branch_name = feature_branch_input
                else:
                    feature_branch_name = f"feature/{issue_key}"
                
                if self.analyzer.create_feature_branch(repo_path, issue_key, branch, feature_branch_input):
                    result = (
                        f"Feature branch created successfully!\n\n"
                        f"Local path: {repo_path}\n"
                        f"Feature branch: {feature_branch_name}\n"
                        f"Repository: {repo_slug}\n"
                        f"Base branch: {branch}\n"
                    )
                    
                    # Save to workflow file
                    self.workflow.save_workflow_step("REPOSITORY_PATH", repo_path)
                    self.workflow.save_workflow_step("REPOSITORY_INFO", result)
                    
                    self.context.log(f"✓ Feature branch created and saved to workflow")
                    self.context.log(f"  Feature branch: {feature_branch_name}")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': True,
                            'result': result,
                            'feature_branch': feature_branch_name
                        }))
                else:
                    self.context.log(f"✗ Failed to create feature branch")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': False,
                            'error': 'Failed to create feature branch'
                        }))
            
            except Exception as e:
                error_msg = str(e)
                logger.error(f"create_feature_branch error: {error_msg}")
                self.context.log(f"✗ Error creating feature branch: {error_msg}")
                if callback:
                    self.context.root.after(0, lambda _m=error_msg: callback({
                        'success': False,
                        'error': _m
                    }))
            finally:
                self._running = False
        
        threading.Thread(target=_create, daemon=True).start()

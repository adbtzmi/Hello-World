#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bento_controller.py
===================
BENTO Master Controller — mirrors CAT.py from C.A.T. application

MVC Architecture:
    View       : gui/app.py (SimpleGUI class)
    Controller : This file (bridges View ↔ Model)
    Model      : orchestrators/, analyzers/, watcher/

Follows EXACT same pattern as CAT.py [17]:
    - CatSAP, CatDB, CatGUI, CATFWMig, CatProfileGenerate
    - BentoCompile, BentoCheckout, BentoGUI, BentoJIRA

Controllers act as intermediaries:
    - Receive user actions from GUI
    - Call backend functions and process results
    - Update GUI with new data or status
"""

import os
import sys
import json
import logging
import threading
from typing import Dict, List, Optional, Tuple

# Import backend models
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from model.orchestrators import compilation_orchestrator
from model.orchestrators import checkout_orchestrator
from model.analyzers import jira_analyzer

# Reuse the already-configured logger
logger = logging.getLogger("bento_logger")


###########################################################################
# JIRA Communication Controller Functions
###########################################################################
class BentoJIRA:
    """
    Handles all JIRA-related operations.
    Mirrors CatSAP from CAT.py [17].
    """
    
    def __init__(self, settings_path="settings.json"):
        self.settings_path = settings_path
        self.settings = self._load_settings()
        self.analyzer = None  # Initialized after credentials loaded
    
    def _load_settings(self) -> Dict:
        """Load settings from settings.json"""
        try:
            if os.path.exists(self.settings_path):
                with open(self.settings_path, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load settings: {e}")
        return {}
    
    def initialize_analyzer(self, email, jira_token, bitbucket_token, model_api_key,
                           jira_url, bitbucket_url, log_callback=None):
        """Initialize JIRAAnalyzer with credentials"""
        self.analyzer = jira_analyzer.JIRAAnalyzer(log_callback=log_callback)
        self.analyzer.email = email
        self.analyzer.bitbucket_username = email.split("@")[0] if "@" in email else email
        self.analyzer.jira_token = jira_token
        self.analyzer.bitbucket_token = bitbucket_token
        self.analyzer.model_api_key = model_api_key
        self.analyzer.jira_base_url = jira_url
        self.analyzer.bitbucket_base_url = bitbucket_url
        self.analyzer.ai_client = jira_analyzer.AIGatewayClient(model_api_key)
        logger.info("JIRA Analyzer initialized")
    
    def fetch_jira_issue(self, issue_key: str) -> Optional[Dict]:
        """Fetch JIRA issue data"""
        if not self.analyzer:
            logger.error("Analyzer not initialized")
            return None
        return self.analyzer.fetch_jira_issue(issue_key)
    
    def analyze_jira_request(self, issue_data: Dict) -> Dict:
        """Analyze JIRA request with AI"""
        if not self.analyzer:
            logger.error("Analyzer not initialized")
            return {"success": False, "error": "Analyzer not initialized"}
        return self.analyzer.analyze_jira_request(issue_data)
    
    def list_project_repositories(self, project_key: str) -> List[Dict]:
        """List all repositories in Bitbucket project"""
        if not self.analyzer:
            logger.error("Analyzer not initialized")
            return []
        return self.analyzer.list_project_repositories(project_key)
    
    def list_repository_branches(self, project_key: str, repo_slug: str) -> List[str]:
        """List all branches in a repository"""
        if not self.analyzer:
            logger.error("Analyzer not initialized")
            return []
        return self.analyzer.list_repository_branches(project_key, repo_slug)


###########################################################################
# Compilation Controller Functions
###########################################################################
class BentoCompile:
    """
    Handles TP package compilation orchestration.
    Mirrors CATFWMig from CAT.py [17].
    """
    
    def __init__(self, GUI_ctrl):
        self.GUI_ctrl = GUI_ctrl
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict:
        """Load settings from settings.json"""
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load settings: {e}")
        return {}
    
    def compile_single(self, source_dir, env, jira_key, hostname, label="",
                      log_callback=None, raw_zip_folder=None, release_tgz_folder=None):
        """
        Compile for a single tester.
        Mirrors run_fw_mig_gui() from CAT.py [17].
        """
        logger.info(f"Starting compilation for {hostname} ({env})")
        
        result = compilation_orchestrator.compile_tp_package(
            source_dir=source_dir,
            env=env,
            jira_key=jira_key,
            hostname=hostname,
            label=label,
            log_callback=log_callback,
            raw_zip_folder=raw_zip_folder,
            release_tgz_folder=release_tgz_folder,
        )
        
        return result
    
    def compile_multi(self, source_dir, targets, jira_key, label="",
                     log_callback=None, raw_zip_folder=None, release_tgz_folder=None):
        """
        Compile for multiple testers in parallel.
        Mirrors run_fw_mig_cronjob() from CAT.py [17].
        
        Args:
            targets: List of (hostname, env) tuples
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        logger.info(f"Starting parallel compilation for {len(targets)} testers")
        
        def _one(hostname, env):
            result = self.compile_single(
                source_dir=source_dir,
                env=env,
                jira_key=jira_key,
                hostname=hostname,
                label=label,
                log_callback=log_callback,
                raw_zip_folder=raw_zip_folder,
                release_tgz_folder=release_tgz_folder,
            )
            result["hostname"] = hostname
            result["env"] = env
            return result
        
        results = []
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {pool.submit(_one, h, e): (h, e) for h, e in targets}
            for future in as_completed(futures):
                results.append(future.result())
        
        return results


###########################################################################
# Checkout Controller Functions
###########################################################################
class BentoCheckout:
    """
    Handles checkout orchestration.
    Mirrors CatProfileGenerate from CAT.py [17].
    """
    
    def __init__(self, GUI_ctrl):
        self.GUI_ctrl = GUI_ctrl
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict:
        """Load settings from settings.json"""
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load settings: {e}")
        return {}
    
    def run_checkout(self, tgz_path, mids, lot_prefix, dut_locations, env,
                    jira_key, log_callback=None):
        """
        Run checkout orchestration.
        Mirrors ProfileDump() from CAT.py [17].
        
        Returns result dict with status, detail, elapsed
        """
        logger.info(f"Starting checkout for {len(mids)} DUTs on {env}")
        
        # Notify GUI that checkout started
        self.GUI_ctrl.checkout_started()
        
        # Run in background thread
        thread = threading.Thread(
            target=self._run_checkout_thread,
            args=(tgz_path, mids, lot_prefix, dut_locations, env, jira_key, log_callback),
            daemon=True
        )
        thread.start()
    
    def _run_checkout_thread(self, tgz_path, mids, lot_prefix, dut_locations,
                            env, jira_key, log_callback):
        """Background thread for checkout execution"""
        try:
            result = checkout_orchestrator.run_checkout(
                tgz_path=tgz_path,
                mids=mids,
                lot_prefix=lot_prefix,
                dut_locations=dut_locations,
                env=env,
                jira_key=jira_key,
                log_callback=log_callback,
            )
            
            # Notify GUI of completion
            self.GUI_ctrl.checkout_completed(result)
            
        except Exception as e:
            logger.error(f"Checkout thread error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.GUI_ctrl.checkout_failed(str(e))


###########################################################################
# GUI Controller Functions
###########################################################################
class BentoGUI:
    """
    Bridges GUI (View) and backend (Model).
    Mirrors CatGUI from CAT.py [17].
    """
    
    def __init__(self, jira_ctrl, compile_ctrl, checkout_ctrl):
        self.jira_ctrl = jira_ctrl
        self.compile_ctrl = compile_ctrl
        self.checkout_ctrl = checkout_ctrl
        
        # Background task tracking (mirrors CAT.py [17])
        self._background_tasks = {
            "Compilation": 0,
            "Checkout": 0,
        }
    
    def set_controllers(self, jira_ctrl, compile_ctrl, checkout_ctrl):
        """Set controller references after initialization"""
        self.jira_ctrl = jira_ctrl
        self.compile_ctrl = compile_ctrl
        self.checkout_ctrl = checkout_ctrl
    
    # ────────────────────────────────────────────────────────────
    # Background Task Management (mirrors CAT.py [17])
    # ────────────────────────────────────────────────────────────
    
    def get_background_tasks(self) -> Dict:
        """Get list of tasks running in background"""
        return self._background_tasks
    
    def add_background_task(self, task: str):
        """Add a task to background task list"""
        if task in self._background_tasks:
            self._background_tasks[task] = 1
    
    def remove_background_task(self, task: str):
        """Remove a task from background task list"""
        if task in self._background_tasks:
            self._background_tasks[task] = 0
    
    # ────────────────────────────────────────────────────────────
    # Compilation Callbacks (mirrors fw_mig_started/completed)
    # ────────────────────────────────────────────────────────────
    
    def compile_started(self):
        """Called when compilation starts"""
        logger.info("Compilation started")
        self.add_background_task("Compilation")
    
    def compile_completed(self, result: Dict):
        """Called when compilation completes"""
        logger.info(f"Compilation completed: {result.get('status')}")
        self.remove_background_task("Compilation")
    
    def compile_failed(self, error_msg: str):
        """Called when compilation fails"""
        logger.error(f"Compilation failed: {error_msg}")
        self.remove_background_task("Compilation")
    
    # ────────────────────────────────────────────────────────────
    # Checkout Callbacks (mirrors profile_gen_started/completed)
    # ────────────────────────────────────────────────────────────
    
    def checkout_started(self):
        """Called when checkout starts"""
        logger.info("Checkout started")
        self.add_background_task("Checkout")
    
    def checkout_completed(self, result: Dict):
        """Called when checkout completes"""
        logger.info(f"Checkout completed: {result.get('status')}")
        self.remove_background_task("Checkout")
    
    def checkout_failed(self, error_msg: str):
        """Called when checkout fails"""
        logger.error(f"Checkout failed: {error_msg}")
        self.remove_background_task("Checkout")
    
    # ────────────────────────────────────────────────────────────
    # JIRA Operations (mirrors query_incoming_crt_from_sap)
    # ────────────────────────────────────────────────────────────
    
    def fetch_jira_issue(self, issue_key: str) -> Optional[Dict]:
        """Fetch JIRA issue"""
        logger.info(f"Fetching JIRA issue: {issue_key}")
        return self.jira_ctrl.fetch_jira_issue(issue_key)
    
    def analyze_jira_request(self, issue_data: Dict) -> Dict:
        """Analyze JIRA request with AI"""
        logger.info("Analyzing JIRA request with AI")
        return self.jira_ctrl.analyze_jira_request(issue_data)
    
    # ────────────────────────────────────────────────────────────
    # Compilation Operations (mirrors call_fw_mig)
    # ────────────────────────────────────────────────────────────
    
    def call_compile(self, source_dir, env, jira_key, hostname, label="",
                    log_callback=None, raw_zip_folder=None, release_tgz_folder=None):
        """Call compilation for single tester"""
        return self.compile_ctrl.compile_single(
            source_dir=source_dir,
            env=env,
            jira_key=jira_key,
            hostname=hostname,
            label=label,
            log_callback=log_callback,
            raw_zip_folder=raw_zip_folder,
            release_tgz_folder=release_tgz_folder,
        )
    
    def call_compile_multi(self, source_dir, targets, jira_key, label="",
                          log_callback=None, raw_zip_folder=None, release_tgz_folder=None):
        """Call compilation for multiple testers"""
        return self.compile_ctrl.compile_multi(
            source_dir=source_dir,
            targets=targets,
            jira_key=jira_key,
            label=label,
            log_callback=log_callback,
            raw_zip_folder=raw_zip_folder,
            release_tgz_folder=release_tgz_folder,
        )
    
    # ────────────────────────────────────────────────────────────
    # Checkout Operations (mirrors profile_dump)
    # ────────────────────────────────────────────────────────────
    
    def call_checkout(self, tgz_path, mids, lot_prefix, dut_locations, env,
                     jira_key, log_callback=None):
        """Call checkout orchestration"""
        return self.checkout_ctrl.run_checkout(
            tgz_path=tgz_path,
            mids=mids,
            lot_prefix=lot_prefix,
            dut_locations=dut_locations,
            env=env,
            jira_key=jira_key,
            log_callback=log_callback,
        )

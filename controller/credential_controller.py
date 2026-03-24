# -*- coding: utf-8 -*-
"""
controller/credential_controller.py
====================================
Credential Controller - Phase 3

Handles credential loading, saving, and debug mode toggling.
"""

import logging
import tkinter as tk
from tkinter import simpledialog

logger = logging.getLogger("bento_app")


class CredentialController:
    """
    Handles credential management and debug mode.
    """

    def __init__(self, context):
        self._context = context
        logger.info("CredentialController initialised.")

    def load_credentials(self, callback):
        """
        Load credentials from encrypted file.
        
        Args:
            callback: Function to call with credentials dict (thread-safe)
        """
        try:
            from jira_analyzer import CredentialManager
            
            # Prompt for password
            password = self._prompt_password("Enter password to decrypt credentials:")
            if not password:
                if callback:
                    self._context.root.after(0, lambda: callback(None))
                return
            
            credentials = CredentialManager.load_credentials(password)
            
            if callback:
                self._context.root.after(0, lambda: callback(credentials))
                
        except Exception as e:
            logger.error(f"CredentialController.load_credentials: {e}")
            if callback:
                self._context.root.after(0, lambda: callback(None))

    def save_credentials(self, email, jira_token, bb_token, model_key, callback):
        """
        Save credentials to encrypted file.
        
        Args:
            email: User email
            jira_token: JIRA API token
            bb_token: Bitbucket API token
            model_key: Model Gateway API key
            callback: Function to call with success boolean (thread-safe)
        """
        try:
            from jira_analyzer import CredentialManager
            
            # Extract Bitbucket username from email
            bb_username = email.split('@')[0] if '@' in email else email
            
            # Get URLs from config
            jira_url = self._context.config.get('jira', {}).get('base_url', '')
            bb_url = self._context.config.get('bitbucket', {}).get('base_url', '')
            
            # Prompt for password
            password = self._prompt_password("Enter password for encryption:")
            if not password:
                if callback:
                    self._context.root.after(0, lambda: callback(False))
                return
            
            confirm = self._prompt_password("Confirm password:")
            if password != confirm:
                logger.warning("Passwords don't match")
                if callback:
                    self._context.root.after(0, lambda: callback(False))
                return
            
            success = CredentialManager.save_credentials(
                email, bb_username, jira_token, bb_token,
                jira_url, bb_url, model_key, password
            )
            
            if callback:
                self._context.root.after(0, lambda: callback(success))
                
        except Exception as e:
            logger.error(f"CredentialController.save_credentials: {e}")
            if callback:
                self._context.root.after(0, lambda: callback(False))

    def toggle_debug_mode(self, enabled):
        """
        Toggle debug mode on the analyzer's AI client.
        
        Args:
            enabled: Boolean indicating if debug mode should be enabled
        """
        try:
            if self._context.analyzer and self._context.analyzer.ai_client:
                self._context.analyzer.ai_client.debug = enabled
                logger.info(f"Debug mode {'enabled' if enabled else 'disabled'}")
        except Exception as e:
            logger.error(f"CredentialController.toggle_debug_mode: {e}")

    def _prompt_password(self, prompt):
        """Prompt for password using a GUI dialog"""
        try:
            # Use the root window from context as parent for the modal dialog
            return simpledialog.askstring("Credentials", prompt, 
                                        parent=self._context.root, 
                                        show='*')
        except Exception as e:
            logger.error(f"CredentialController._prompt_password: {e}")
            return None

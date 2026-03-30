#!/usr/bin/env python3
"""
JIRA Request Analyzer & Implementer
Analyzes JIRA change requests, clones repositories, and generates comprehensive analysis using AI
"""

import os
import sys
import json
import subprocess
import ssl
import urllib.request
import urllib.error
import getpass
import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple, Union
from cryptography.fernet import Fernet
import base64
import hashlib


class CredentialManager:
    """Manages encrypted credential storage"""
    
    CREDENTIAL_FILE = "credential"
    
    @staticmethod
    def _get_key_from_password(password: str) -> bytes:
        """Derive encryption key from password"""
        # Use password-based key derivation
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), b'salt_', 100000)
        return base64.urlsafe_b64encode(key)
    
    @staticmethod
    def save_credentials(email: str, bitbucket_username: str, jira_token: str, bitbucket_token: str,
                        jira_url: str, bitbucket_url: str, model_api_key: str, password: str):
        """Save encrypted credentials to file"""
        try:
            # Create encryption key from password
            key = CredentialManager._get_key_from_password(password)
            cipher = Fernet(key)
            
            # Prepare credentials dictionary
            credentials = {
                'email': email,
                'bitbucket_username': bitbucket_username,
                'jira_token': jira_token,
                'bitbucket_token': bitbucket_token,
                'jira_url': jira_url,
                'bitbucket_url': bitbucket_url,
                'model_api_key': model_api_key
            }
            
            # Encrypt and save
            encrypted_data = cipher.encrypt(json.dumps(credentials).encode())
            with open(CredentialManager.CREDENTIAL_FILE, 'wb') as f:
                f.write(encrypted_data)
            
            print(f"✓ Credentials saved to encrypted file: {CredentialManager.CREDENTIAL_FILE}")
            return True
        except Exception as e:
            print(f"✗ Error saving credentials: {str(e)}")
            return False
    
    @staticmethod
    def load_credentials(password: str) -> Optional[Dict]:
        """Load and decrypt credentials from file"""
        try:
            if not os.path.exists(CredentialManager.CREDENTIAL_FILE):
                return None
            
            # Create decryption key from password
            key = CredentialManager._get_key_from_password(password)
            cipher = Fernet(key)
            
            # Read and decrypt
            with open(CredentialManager.CREDENTIAL_FILE, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = cipher.decrypt(encrypted_data)
            credentials = json.loads(decrypted_data.decode())
            
            print(f"✓ Credentials loaded from: {CredentialManager.CREDENTIAL_FILE}")
            return credentials
        except Exception as e:
            print(f"✗ Error loading credentials: {str(e)}")
            print("  (This may be due to incorrect password or corrupted file)")
            return None


class AIGatewayClient:
    """Client for interacting with Model Gateway API"""
    
    def __init__(self, api_key: str, config_file: str = "settings.json", modes_file: str = "ai_modes.json"):
        self.api_key = api_key
        self.config = self._load_config(config_file)
        self.modes_config = self._load_modes(modes_file)
        self.base_url = self.config.get('model_gateway', {}).get('base_url',
                                                                  "https://model-gateway.gcldgenaigw.gc.micron.com/api/v1")
        self.timeout = self.config.get('settings', {}).get('timeout', 300)
        self.debug = self.config.get('settings', {}).get('debug', False)
        
        # Create SSL context that doesn't verify certificates (for internal servers)
        self.ssl_context = ssl._create_unverified_context()
    
    def _load_config(self, config_file: str) -> Dict:
        """Load configuration from JSON file"""
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")
        return {}
    
    def _load_modes(self, modes_file: str) -> Dict:
        """Load AI mode definitions from ai_modes.json"""
        try:
            if os.path.exists(modes_file):
                with open(modes_file, 'r') as f:
                    self._modes_data = json.load(f)
                    return self._modes_data.get('modes', {})
            else:
                print(f"Info: AI modes file '{modes_file}' not found. Using default behavior (no system prompts).")
        except Exception as e:
            print(f"Warning: Could not load AI modes file: {e}")
        self._modes_data = {}
        return {}
    
    def get_mode_config(self, mode_name: str) -> Optional[Dict]:
        """
        Get AI mode configuration by name.
        
        Args:
            mode_name: Mode key (e.g., 'jira_analysis', 'code_implementation', 'test_scenarios')
        
        Returns:
            Dict with 'name', 'system_prompt', 'temperature', 'task_type' or None if not found
        """
        return self.modes_config.get(mode_name)
    
    def get_model_config(self, task_type: str) -> Dict:
        """Get model configuration for specific task type from ai_modes.json"""
        models = self._modes_data.get('models', {})
        if task_type in models:
            return models[task_type]
        # Fallback to code_generation model
        return models.get('code_generation', {
            'name': 'claude-sonnet-4-5'
        })
    
    def get_prompt_template(self, prompt_type: str) -> str:
        """Get prompt template from ai_modes.json modes section.
        Looks for 'prompt_template' field within the mode definition."""
        # First check in modes (new consolidated structure)
        mode_config = self.modes_config.get(prompt_type, {})
        if isinstance(mode_config, dict) and 'prompt_template' in mode_config:
            return mode_config['prompt_template']
        # Fallback to legacy 'prompts' section for backward compatibility
        prompts = self._modes_data.get('prompts', {})
        return prompts.get(prompt_type, '')
    
    def chat_completion(self, messages: List[Dict], model: Optional[str] = None, task_type: str = "code_generation",
                        mode: Optional[str] = None) -> Dict:
        """
        Send chat completion request to Model Gateway
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use (if None, uses task_type to determine model)
            task_type: Type of task ('analysis' or 'code_generation')
            mode: AI mode name from ai_modes.json (e.g., 'jira_analysis', 'test_scenarios').
                  When specified, prepends a system message with the mode's system_prompt
                  and uses the mode's task_type if task_type is not explicitly provided.
        
        Returns:
            Dict with 'success' and either 'response' or 'error'
        """
        try:
            # Apply AI mode if specified
            mode_config = None
            if mode:
                mode_config = self.get_mode_config(mode)
                if mode_config:
                    # Use mode's task_type if not explicitly overridden
                    if task_type == "code_generation" and mode_config.get('task_type'):
                        task_type = mode_config['task_type']
                    
                    if self.debug:
                        print(f"  [DEBUG] AI Mode: {mode_config.get('name', mode)} | Task Type: {task_type}")
                elif self.debug:
                    print(f"  [DEBUG] AI Mode '{mode}' not found in ai_modes.json, proceeding without system prompt")
            
            # Get model config based on task type if model not specified
            if model is None:
                model_config = self.get_model_config(task_type)
                model = model_config['name']
            
            # Build final messages list with system prompt from mode
            final_messages = []
            
            if mode_config and mode_config.get('system_prompt'):
                # Check if messages already have a system message
                has_system = any(m.get('role') == 'system' for m in messages)
                if not has_system:
                    final_messages.append({
                        "role": "system",
                        "content": mode_config['system_prompt']
                    })
                    if self.debug:
                        print(f"  [DEBUG] System prompt injected from mode '{mode}' ({len(mode_config['system_prompt'])} chars)")
            
            final_messages.extend(messages)
            
            payload = {
                "model": model,
                "messages": final_messages
            }
            
            # Add temperature from mode config if available
            if mode_config and 'temperature' in mode_config:
                payload["temperature"] = mode_config['temperature']
            
            # Prepare request
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = json.dumps(payload).encode('utf-8')
            request = urllib.request.Request(url, data, headers)
            
            # Log AI request to file logger
            self._log_ai_request(model, mode, final_messages, payload)
            
            # Make request
            response = urllib.request.urlopen(request, context=self.ssl_context, timeout=self.timeout)
            response_data = json.loads(response.read().decode('utf-8'))
            
            # Log AI response to file logger
            self._log_ai_response(model, mode, response_data)
            
            return {
                "success": True,
                "model_used": model,
                "mode_used": mode_config.get('name', mode) if mode_config else None,
                "response": response_data
            }
            
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            error_msg = f"Model {model} failed with status {e.code}: {error_body}"
            self._log_ai_error(model, mode, error_msg)
            return {"success": False, "error": error_msg}
        except Exception as e:
            self._log_ai_error(model, mode, str(e))
            return {"success": False, "error": str(e)}
    
    def _log_ai_request(self, model, mode, messages, payload):
        """Log AI request details to file logger"""
        try:
            # Get the bento file logger (created by main.py)
            loggers = [logging.getLogger(name) for name in logging.Logger.manager.loggerDict
                      if name.startswith('bento_')]
            if not loggers:
                return
            logger = loggers[-1]  # Use the most recent bento logger
            
            logger.info("-" * 60)
            logger.info("AI REQUEST")
            logger.info("-" * 60)
            logger.info(f"  Model: {model}")
            logger.info(f"  Mode: {mode or 'default'}")
            logger.info(f"  Messages count: {len(messages)}")
            if 'temperature' in payload:
                logger.info(f"  Temperature: {payload['temperature']}")
            
            # Log message summaries (truncate long content)
            for i, msg in enumerate(messages):
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                content_preview = content[:500] + '...' if len(content) > 500 else content
                logger.info(f"  Message[{i}] ({role}): {content_preview}")
            logger.info("-" * 60)
        except Exception:
            pass  # Don't let logging errors break the application
    
    def _log_ai_response(self, model, mode, response_data):
        """Log AI response details to file logger"""
        try:
            loggers = [logging.getLogger(name) for name in logging.Logger.manager.loggerDict
                      if name.startswith('bento_')]
            if not loggers:
                return
            logger = loggers[-1]
            
            logger.info("-" * 60)
            logger.info("AI RESPONSE")
            logger.info("-" * 60)
            logger.info(f"  Model: {model}")
            logger.info(f"  Mode: {mode or 'default'}")
            
            # Extract response content
            choices = response_data.get('choices', [])
            if choices:
                content = choices[0].get('message', {}).get('content', '')
                content_preview = content[:1000] + '...' if len(content) > 1000 else content
                logger.info(f"  Response length: {len(content)} chars")
                logger.info(f"  Response preview: {content_preview}")
            
            # Log usage info if available
            usage = response_data.get('usage', {})
            if usage:
                logger.info(f"  Tokens - Prompt: {usage.get('prompt_tokens', 'N/A')}, "
                          f"Completion: {usage.get('completion_tokens', 'N/A')}, "
                          f"Total: {usage.get('total_tokens', 'N/A')}")
            logger.info("-" * 60)
        except Exception:
            pass
    
    def _log_ai_error(self, model, mode, error_msg):
        """Log AI error details to file logger"""
        try:
            loggers = [logging.getLogger(name) for name in logging.Logger.manager.loggerDict
                      if name.startswith('bento_')]
            if not loggers:
                return
            logger = loggers[-1]
            
            logger.error("-" * 60)
            logger.error("AI ERROR")
            logger.error("-" * 60)
            logger.error(f"  Model: {model}")
            logger.error(f"  Mode: {mode or 'default'}")
            logger.error(f"  Error: {error_msg}")
            logger.error("-" * 60)
        except Exception:
            pass


class JIRAAnalyzer:
    """Main class for JIRA request analysis and implementation"""
    
    def __init__(self, log_callback: Optional[Callable] = None, gui_only_log_callback: Optional[Callable] = None):
        self.email: Optional[str] = None  # Email for JIRA (Atlassian Cloud)
        self.bitbucket_username: Optional[str] = None  # Username for Bitbucket (part before @)
        self.jira_token: Optional[str] = None
        self.bitbucket_token: Optional[str] = None
        self.jira_base_url: Optional[str] = None
        self.bitbucket_base_url: Optional[str] = None
        self.model_api_key: Optional[str] = None
        self.ai_client: Optional[AIGatewayClient] = None
        self.log_callback = log_callback  # Optional callback for logging to GUI
        self.gui_only_log_callback = gui_only_log_callback  # Optional callback for GUI-only logging (no file log)
    
    def _log(self, message):
        """Log message to callback if available, otherwise print"""
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)
    
    def _log_gui_only(self, message):
        """Log message to GUI only (no file log). Used for verbose output like git clone progress."""
        if self.gui_only_log_callback:
            self.gui_only_log_callback(message)
        elif self.log_callback:
            # Fallback to regular log if gui_only not available
            self.log_callback(message)
        else:
            print(message)
        
    def get_credentials(self):
        """Get credentials from user or load from encrypted file"""
        self._log("=" * 80)
        self._log("BENTO - CREDENTIAL SETUP")
        self._log("=" * 80)
        
        # Check if encrypted credential file exists
        if os.path.exists(CredentialManager.CREDENTIAL_FILE):
            self._log(f"\n✓ Found encrypted credential file: {CredentialManager.CREDENTIAL_FILE}")
            use_saved = input("Load saved credentials? (y/n): ").strip().lower()
            
            if use_saved == 'y':
                password = getpass.getpass("Enter password to decrypt credentials: ").strip()
                credentials = CredentialManager.load_credentials(password)
                
                if credentials:
                    self.email = credentials['email']
                    self.bitbucket_username = credentials['bitbucket_username']
                    self.jira_token = credentials['jira_token']
                    self.bitbucket_token = credentials['bitbucket_token']
                    self.jira_base_url = credentials['jira_url']
                    self.bitbucket_base_url = credentials['bitbucket_url']
                    self.model_api_key = credentials['model_api_key']
                    self.ai_client = AIGatewayClient(str(self.model_api_key))
                    self._log("✓ Credentials loaded successfully\n")
                    return
                else:
                    self._log("✗ Failed to load credentials. Please enter manually.\n")
        
        # Manual credential entry
        self._log("\n[Credentials Setup]")
        
        self.email = input("Micron Email (e.g., john.doe@micron.com): ").strip()
        
        # Extract Bitbucket username from email (part before @)
        if '@' in self.email:
            self.bitbucket_username = self.email.split('@')[0]
            self._log(f"  Bitbucket username: {self.bitbucket_username}")
        else:
            self._log("  Warning: Email should contain @micron.com")
            self.bitbucket_username = self.email
        
        self.jira_token = getpass.getpass("JIRA API Token: ").strip()
        self.bitbucket_token = getpass.getpass("Bitbucket API Token: ").strip()
        
        # Use default URLs with option to override
        default_jira = "https://micron.atlassian.net"
        default_bitbucket = "https://bitbucket.micron.com"
        
        use_defaults = input(f"Use default URLs? (JIRA: {default_jira}, Bitbucket: {default_bitbucket}) (y/n): ").strip().lower()
        
        if use_defaults == 'y':
            self.jira_base_url = default_jira
            self.bitbucket_base_url = default_bitbucket
            self._log(f"  Using JIRA: {self.jira_base_url}")
            self._log(f"  Using Bitbucket: {self.bitbucket_base_url}")
        else:
            self.jira_base_url = input("JIRA Base URL: ").strip()
            self.bitbucket_base_url = input("Bitbucket Base URL: ").strip()
        
        # Model Gateway API Key is required
        self.model_api_key = getpass.getpass("Model Gateway API Key: ").strip()
        
        if not self.model_api_key:
            self._log("✗ Model Gateway API Key is required!")
            sys.exit(1)
        
        self.ai_client = AIGatewayClient(str(self.model_api_key))
        
        # Ask if user wants to save credentials
        self._log("\n[Save Credentials]")
        save_creds = input("Save credentials to encrypted file? (y/n): ").strip().lower()
        
        if save_creds == 'y':
            password = getpass.getpass("Enter password for encryption: ").strip()
            confirm_password = getpass.getpass("Confirm password: ").strip()
            
            if password == confirm_password:
                CredentialManager.save_credentials(
                    self.email,
                    self.bitbucket_username,
                    self.jira_token,
                    self.bitbucket_token,
                    self.jira_base_url,
                    self.bitbucket_base_url,
                    self.model_api_key,
                    password
                )
            else:
                self._log("✗ Passwords don't match. Credentials not saved.")
        
        self._log("\n✓ Credentials configured successfully\n")
    
    def list_repository_branches(self, project_key: str, repo_slug: str) -> List[str]:
        """List all branches in a repository"""
        try:
            url = f"{self.bitbucket_base_url}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/branches?limit=1000"
            
            # Create request with authentication
            auth_string = f"{self.bitbucket_username}:{self.bitbucket_token}"
            auth_bytes = auth_string.encode('utf-8')
            auth_header = base64.b64encode(auth_bytes).decode('utf-8')
            
            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/json"
            }
            
            request = urllib.request.Request(url, headers=headers)
            ssl_context = ssl._create_unverified_context()
            
            response = urllib.request.urlopen(request, context=ssl_context, timeout=30)
            data = json.loads(response.read().decode('utf-8'))
            
            branches = []
            for branch in data.get('values', []):
                branches.append(branch['displayId'])
            
            return branches
            
        except Exception as e:
            self._log(f"  Error listing branches: {str(e)}")
            return []
    
    def list_project_repositories(self, project_key: str = "TESTSSD") -> List[Dict]:
        """List all repositories in a Bitbucket project"""
        try:
            url = f"{self.bitbucket_base_url}/rest/api/1.0/projects/{project_key}/repos"
            
            # Create request with authentication
            auth_string = f"{self.bitbucket_username}:{self.bitbucket_token}"
            auth_bytes = auth_string.encode('utf-8')
            auth_header = base64.b64encode(auth_bytes).decode('utf-8')
            
            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/json"
            }
            
            request = urllib.request.Request(url, headers=headers)
            ssl_context = ssl._create_unverified_context()
            
            response = urllib.request.urlopen(request, context=ssl_context, timeout=30)
            data = json.loads(response.read().decode('utf-8'))
            
            repos = []
            for repo in data.get('values', []):
                repos.append({
                    'slug': repo['slug'],
                    'name': repo['name'],
                    'project': repo['project']['key']
                })
            
            return repos
            
        except Exception as e:
            self._log(f"  Error listing repositories: {str(e)}")
            return []
    
    def get_task_details(self) -> Tuple[str, str, str]:
        """Get issue key, repo, and branch from user"""
        self._log("=" * 80)
        self._log("TASK DETAILS")
        self._log("=" * 80)
        
        issue_key = input("\nJIRA Issue Key (e.g., PROJ-1234): ").strip().upper()
        
        # List available repositories from TESTSSD project
        self._log(f"\n[Fetching repositories from TESTSSD project...]")
        repos = self.list_project_repositories("TESTSSD")
        
        if repos:
            self._log(f"\n✓ Found {len(repos)} repositories in TESTSSD project:")
            self._log("\n" + "=" * 80)
            self._log(f"{'#':<5} {'Repository Slug':<40} {'Name':<30}")
            self._log("=" * 80)
            
            for idx, repo in enumerate(repos, 1):
                self._log(f"{idx:<5} {repo['slug']:<40} {repo['name']:<30}")
            
            self._log("=" * 80)
            
            # Let user choose
            choice = input("\nEnter repository number (or type custom name): ").strip()
            
            if choice.isdigit() and 1 <= int(choice) <= len(repos):
                repo_name = repos[int(choice) - 1]['slug']
                self._log(f"  Selected: {repo_name}")
            else:
                repo_name = choice
                self._log(f"  Using custom repository: {repo_name}")
        else:
            self._log("  Could not fetch repositories. Please enter manually.")
            repo_name = input("Repository Name: ").strip()
        
        # Fetch and display branches for selected repository
        self._log(f"\n[Fetching branches for {repo_name}...]")
        branches = self.list_repository_branches("TESTSSD", repo_name)
        
        if branches:
            self._log(f"\n✓ Found {len(branches)} branches:")
            self._log("\n" + "=" * 80)
            self._log(f"{'#':<5} {'Branch Name':<70}")
            self._log("=" * 80)
            
            for idx, branch in enumerate(branches, 1):
                self._log(f"{idx:<5} {branch:<70}")
            
            self._log("=" * 80)
            
            # Let user choose branch
            choice = input("\nEnter branch number (or type custom name): ").strip()
            
            if choice.isdigit() and 1 <= int(choice) <= len(branches):
                branch_name = branches[int(choice) - 1]
                self._log(f"  Selected: {branch_name}")
            else:
                branch_name = choice
                self._log(f"  Using custom branch: {branch_name}")
        else:
            self._log("  Could not fetch branches. Please enter manually.")
            branch_name = input("Branch Name (e.g., develop, master): ").strip()
        
        return issue_key, repo_name, branch_name
    
    def _extract_text_from_adf(self, content) -> str:
        """Extract plain text from Atlassian Document Format (ADF)"""
        if not content:
            return ""
            
        if isinstance(content, str):
            try:
                # Try to parse string as JSON if it looks like ADF
                if content.strip().startswith('{') and '"type": "doc"' in content:
                    content = json.loads(content)
                else:
                    return content
            except:
                return content
        
        if not isinstance(content, dict):
            return str(content)
            
        text_parts = []
        _seen = set()  # Guard against circular references
        
        def traverse(node, depth=0):
            if depth > 100:  # Prevent infinite recursion on deeply nested ADF
                return
            node_id = id(node)
            if node_id in _seen:
                return  # Circular reference detected
            _seen.add(node_id)
            
            if isinstance(node, dict):
                if node.get('type') == 'text':
                    text_parts.append(node.get('text', ''))
                elif node.get('type') == 'hardBreak':
                    text_parts.append('\n')
                elif node.get('type') == 'paragraph':
                    text_parts.append('\n')
                
                # Recursively check content
                if 'content' in node:
                    for child in node['content']:
                        traverse(child, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    traverse(item, depth + 1)
                    
        traverse(content)
        return ''.join(text_parts).strip()
    
    def extract_jira_fields(self, issue_data: Dict) -> Dict:
        """Extract and structure JIRA fields from issue data"""
        fields = issue_data.get('fields', {})
        names = issue_data.get('names', {})
        
        # Debug: Show all available fields if debug mode is enabled
        if self.ai_client and self.ai_client.debug:
            self._log("\n[DEBUG] Available JIRA Fields:")
            self._log("-" * 80)
            self._log(f"Total fields in response: {len(fields)}")
            self._log(f"Total named fields: {len(names)}")
            self._log("\nField Names Mapping:")
            for field_id, field_name in sorted(names.items()):
                field_value = fields.get(field_id)
                has_value = "✓" if field_value else "✗"
                self._log(f"  {has_value} {field_id}: {field_name}")
            self._log("-" * 80)
        
        # Extract fields
        # 1. Work Type (Issue Type)
        work_type = fields.get('issuetype', {}).get('name', 'N/A')
        if self.ai_client and self.ai_client.debug:
            self._log(f"\n[DEBUG] Work Type: {work_type}")
        
        # 2. Description
        description = fields.get('description', 'N/A')
        if description:
            description_text = self._extract_text_from_adf(description)[:2000]
        else:
            description_text = 'N/A'
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Description: {'Found' if description != 'N/A' else 'Not Found'} ({len(description_text)} chars)")
        
        # 3. Components
        components = [c.get('name') for c in fields.get('components', [])]
        components_str = ', '.join(components) if components else 'None'
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Components: {components_str}")
        
        # 4. Acceptance Criteria
        acceptance_criteria = "N/A"
        acceptance_criteria_field_id = None
        for field_id, field_name in names.items():
            if 'acceptance criteria' in field_name.lower():
                acceptance_criteria_field_id = field_id
                ac_val = fields.get(field_id)
                if ac_val:
                    acceptance_criteria = self._extract_text_from_adf(ac_val)
                    if self.ai_client and self.ai_client.debug:
                        self._log(f"[DEBUG] Acceptance Criteria: ✓ Found in field '{field_name}' ({field_id})")
                        self._log(f"        Length: {len(acceptance_criteria)} chars")
                    break
                else:
                    if self.ai_client and self.ai_client.debug:
                        self._log(f"[DEBUG] Acceptance Criteria: ✗ Field '{field_name}' ({field_id}) exists but is empty")
        
        if acceptance_criteria == "N/A" and self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Acceptance Criteria: ✗ Not found in any field")
            self._log(f"        Searched for fields containing 'acceptance criteria'")
        
        # 5. Change Category
        change_category = "N/A"
        change_category_field_id = None
        for field_id, field_name in names.items():
            if 'change category' in field_name.lower():
                change_category_field_id = field_id
                cat_val = fields.get(field_id)
                if cat_val:
                    # Handle different field types
                    if isinstance(cat_val, dict):
                        change_category = cat_val.get('value', cat_val.get('name', str(cat_val)))
                    elif isinstance(cat_val, list) and cat_val:
                        change_category = ', '.join([str(c.get('value', c.get('name', c))) if isinstance(c, dict) else str(c) for c in cat_val])
                    else:
                        change_category = str(cat_val)
                    if self.ai_client and self.ai_client.debug:
                        self._log(f"[DEBUG] Change Category: ✓ Found in field '{field_name}' ({field_id})")
                        self._log(f"        Value: {change_category}")
                    break
                else:
                    if self.ai_client and self.ai_client.debug:
                        self._log(f"[DEBUG] Change Category: ✗ Field '{field_name}' ({field_id}) exists but is empty")
        
        if change_category == "N/A" and self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Change Category: ✗ Not found in any field")
            self._log(f"        Searched for fields containing 'change category' or 'category'")
        
        # 6. Reporter and Assignee
        reporter = fields.get('reporter', {})
        reporter_name = reporter.get('displayName', 'N/A') if reporter else 'N/A'
        
        assignee = fields.get('assignee', {})
        assignee_name = assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned'
        
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Reporter: {reporter_name}")
            self._log(f"[DEBUG] Assignee: {assignee_name}")
        
        # 7. Issue Links
        issue_links = fields.get('issuelinks', [])
        links_count = len(issue_links) if issue_links else 0
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Issue Links: {links_count} linked issues")
        
        # 8. Attachments
        attachments = fields.get('attachment', [])
        attachments_count = len(attachments) if attachments else 0
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Attachments: {attachments_count} files")
        
        # 9. Comments (limit to most recent 5) with role identification
        comments = []
        comments_text = ""
        comment_data = fields.get('comment', {}).get('comments', [])
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Comments: Found {len(comment_data)} total comments")
        
        for comment in comment_data[-5:]:  # Only last 5 comments
            author = comment.get('author', {}).get('displayName', 'Unknown')
            author_email = comment.get('author', {}).get('emailAddress', '')
            
            # Identify role
            role = ""
            if reporter and author == reporter_name:
                role = " [Reporter]"
            elif assignee and author == assignee_name:
                role = " [Assignee]"
            
            body = comment.get('body', '')
            body_text = self._extract_text_from_adf(body)[:500]
            comments_text += f"- **{author}{role}**: {body_text}\n"
            
            comments.append({
                'author': author,
                'role': role.strip('[]') if role else 'Other',
                'body': body_text
            })
        
        if not comments_text:
            comments_text = "None"
        
        if self.ai_client and self.ai_client.debug:
            self._log(f"[DEBUG] Comments: Using last {len(comments)} comments for analysis")
            self._log("-" * 80)
            self._log("\n[DEBUG] Field Extraction Summary:")
            self._log(f"  ✓ Work Type: {work_type}")
            self._log(f"  {'✓' if description != 'N/A' else '✗'} Description: {'Found' if description != 'N/A' else 'Not Found'}")
            self._log(f"  {'✓' if components else '✗'} Components: {components_str}")
            self._log(f"  {'✓' if acceptance_criteria != 'N/A' else '✗'} Acceptance Criteria: {'Found' if acceptance_criteria != 'N/A' else 'Not Found'}")
            self._log(f"  {'✓' if change_category != 'N/A' else '✗'} Change Category: {'Found' if change_category != 'N/A' else 'Not Found'}")
            self._log(f"  {'✓' if comments else '✗'} Comments: {len(comments)} comments")
            self._log(f"  ✓ Reporter: {reporter_name}")
            self._log(f"  ✓ Assignee: {assignee_name}")
            self._log(f"  ✓ Issue Links: {links_count}")
            self._log(f"  ✓ Attachments: {attachments_count}")
            self._log("-" * 80)
        
        return {
            'work_type': work_type,
            'description': description_text,
            'components': components,
            'components_str': components_str,
            'acceptance_criteria': acceptance_criteria,
            'change_category': change_category,
            'comments': comments,
            'comments_text': comments_text,
            'reporter': reporter_name,
            'assignee': assignee_name,
            'issue_links': issue_links,
            'issue_links_count': links_count,
            'attachments': attachments,
            'attachments_count': attachments_count
        }

    def fetch_jira_issue(self, issue_key: str) -> Optional[Dict]:
        """Fetch JIRA issue details via REST API"""
        self._log(f"\n[Fetching JIRA Issue: {issue_key}]")
        
        # Fallback: load jira_base_url from settings.json if not set
        if not self.jira_base_url:
            try:
                with open('settings.json', 'r') as f:
                    settings = json.load(f)
                self.jira_base_url = settings.get('jira', {}).get('base_url', '')
                if self.jira_base_url:
                    self._log(f"  ℹ Loaded JIRA base URL from settings.json: {self.jira_base_url}")
            except Exception:
                pass
        
        if not self.jira_base_url:
            self._log("✗ JIRA base URL is not configured.")
            self._log("  Please load credentials or set the JIRA URL in Settings.")
            return None
        
        url = ""
        try:
            # For Atlassian Cloud, use /rest/api/3/ (latest) or /rest/api/2/
            # Try API v3 first for Atlassian Cloud
            if self.jira_base_url and 'atlassian.net' in self.jira_base_url:
                api_version = '3'
            else:
                api_version = '2'
            
            url = f"{self.jira_base_url}/rest/api/{api_version}/issue/{issue_key}"
            # Fetch all fields (including custom fields) and expand names
            # to identify custom field labels like acceptance criteria
            url += "?expand=names"
            
            self._log(f"  API URL: {url}")
            self._log(f"  Using API version: {api_version}")
            self._log(f"  Email: {self.email}")
            
            # Create request with authentication
            auth_string = f"{self.email}:{self.jira_token}"
            auth_bytes = auth_string.encode('utf-8')
            import base64
            auth_header = base64.b64encode(auth_bytes).decode('utf-8')
            
            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            request = urllib.request.Request(url, headers=headers)
            
            # Create SSL context
            ssl_context = ssl._create_unverified_context()
            
            response = urllib.request.urlopen(request, context=ssl_context, timeout=30)
            issue_data = json.loads(response.read().decode('utf-8'))
            
            self._log(f"✓ Successfully fetched issue: {issue_data['fields']['summary']}")
            
            # Extract fields immediately after fetching
            extracted_fields = self.extract_jira_fields(issue_data)
            
            # Add extracted fields to issue_data for easy access
            issue_data['extracted_fields'] = extracted_fields
            
            return issue_data
            
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            self._log(f"\n✗ HTTP Error {e.code}")
            self._log(f"  URL: {url}")
            self._log(f"  Response: {error_body}")
            
            # If API v3 returned 404, retry with API v2
            if e.code == 404 and api_version == '3':
                self._log("\n  ℹ Retrying with API v2...")
                try:
                    url_v2 = f"{self.jira_base_url}/rest/api/2/issue/{issue_key}?expand=names"
                    self._log(f"  API URL: {url_v2}")
                    request_v2 = urllib.request.Request(url_v2, headers=headers)
                    response_v2 = urllib.request.urlopen(request_v2, context=ssl_context, timeout=30)
                    issue_data = json.loads(response_v2.read().decode('utf-8'))
                    self._log(f"✓ Successfully fetched issue (API v2): {issue_data['fields']['summary']}")
                    extracted_fields = self.extract_jira_fields(issue_data)
                    issue_data['extracted_fields'] = extracted_fields
                    return issue_data
                except urllib.error.HTTPError as e2:
                    error_body_v2 = e2.read().decode('utf-8')
                    self._log(f"\n✗ HTTP Error {e2.code} (API v2 retry)")
                    self._log(f"  Response: {error_body_v2}")
                except Exception as e2:
                    self._log(f"✗ API v2 retry also failed: {str(e2)}")
            
            # Provide helpful debugging info
            if e.code == 404:
                self._log("\n  Possible causes:")
                self._log("  1. Issue key doesn't exist or is incorrect")
                self._log("  2. You don't have permission to view this issue")
                self._log("  3. Issue is in a different JIRA instance")
                self._log(f"  4. Check if issue exists at: {self.jira_base_url}/browse/{issue_key}")
            elif e.code == 401:
                self._log("\n  Authentication failed:")
                self._log("  1. Check your username is correct")
                self._log("  2. Verify your API token is valid")
                self._log("  3. For Atlassian Cloud, use your email as username")
                self._log("  4. Regenerate API token if needed")
            
            return None
        except Exception as e:
            self._log(f"✗ Error fetching JIRA issue: {str(e)}")
            import traceback
            traceback.print_exc()
            return None
    
    def fetch_linked_issues(self, issue_links: List[Dict]) -> str:
        """Fetch details of linked JIRA issues for additional context"""
        if not issue_links:
            return "No linked issues"
        
        linked_context = []
        self._log(f"\n[Fetching {len(issue_links)} linked issues for context...]")
        
        for link in issue_links[:5]:  # Limit to 5 linked issues
            linked_key: Optional[str] = None
            link_type: str = 'relates to'
            try:
                # Get the linked issue key
                if 'outwardIssue' in link:
                    linked_key = link['outwardIssue'].get('key')
                    link_type = link.get('type', {}).get('outward', 'relates to')
                elif 'inwardIssue' in link:
                    linked_key = link['inwardIssue'].get('key')
                    link_type = link.get('type', {}).get('inward', 'relates to')
                
                if not linked_key:
                    continue
                
                # Fetch the linked issue
                linked_issue = self.fetch_jira_issue(linked_key)
                if linked_issue:
                    fields = linked_issue.get('fields', {})
                    summary = fields.get('summary', 'N/A')
                    status = fields.get('status', {}).get('name', 'N/A')
                    description = self._extract_text_from_adf(fields.get('description', ''))[:300]
                    
                    linked_context.append(
                        f"- **{linked_key}** ({link_type}): {summary}\n"
                        f"  Status: {status}\n"
                        f"  Description: {description}..."
                    )
            except Exception as e:
                self._log(f"  Warning: Could not fetch {linked_key or 'unknown'}: {str(e)}")
                continue
        
        if linked_context:
            return "\n".join(linked_context)
        return "No linked issues could be fetched"
    
    def process_attachments(self, attachments: List[Dict]) -> str:
        """Process attachment metadata for AI analysis"""
        if not attachments:
            return "No attachments"
        
        attachment_info = []
        self._log(f"\n[Processing {len(attachments)} attachments...]")
        
        for att in attachments[:10]:  # Limit to 10 attachments
            filename = att.get('filename', 'unknown')
            size = att.get('size', 0)
            mime_type = att.get('mimeType', 'unknown')
            author = att.get('author', {}).get('displayName', 'Unknown')
            created = att.get('created', 'Unknown')
            
            # Determine if it's a readable document
            readable_types = ['text/', 'application/pdf', 'application/json', 'application/xml']
            is_readable = any(mime_type.startswith(t) for t in readable_types)
            
            attachment_info.append(
                f"- **{filename}** ({size} bytes, {mime_type})\n"
                f"  Uploaded by: {author} on {created}\n"
                f"  {'[Text/Document - content could be analyzed]' if is_readable else '[Binary file]'}"
            )
        
        return "\n".join(attachment_info)
    
    def analyze_jira_request(self, issue_data: Dict) -> Dict:
        """Analyze JIRA request using AI to understand change requirements"""
        assert self.ai_client is not None, "AI client not initialized. Call get_credentials() first."
        self._log("\n[Analyzing JIRA Request with AI]")
        
        # Use pre-extracted fields from fetch_jira_issue()
        # If not available (backward compatibility), extract them now
        if 'extracted_fields' in issue_data:
            extracted = issue_data['extracted_fields']
        else:
            # Backward compatibility: extract fields if not already done
            extracted = self.extract_jira_fields(issue_data)
        
        # Extract all fields
        work_type = extracted['work_type']
        description_text = extracted['description']
        components = extracted['components']
        components_str = extracted['components_str']
        acceptance_criteria = extracted['acceptance_criteria']
        change_category = extracted['change_category']
        comments = extracted['comments']
        comments_text = extracted['comments_text']
        issue_links = extracted.get('issue_links', [])
        attachments = extracted.get('attachments', [])
        
        # Fetch linked issues for additional context
        linked_issues_context = ""
        if issue_links:
            linked_issues_context = self.fetch_linked_issues(issue_links)
        
        # Process attachments
        attachments_context = ""
        if attachments:
            attachments_context = self.process_attachments(attachments)
        
        # Get prompt template from ai_modes.json (unified 'jira_analysis' for all work types)
        prompt_template = self.ai_client.get_prompt_template('jira_analysis')
        
        # Ultimate fallback if config doesn't have any templates
        if not prompt_template:
            prompt_template = """Analyze this JIRA request:

**Work Type:** {work_type}
**Description:** {description_text}
**Components:** {components_str}
**Acceptance Criteria:** {acceptance_criteria}
**Change Category:** {change_category}
**Comments:** {comments_text}
**Linked Issues:** {linked_issues}
**Attachments:** {attachments_info}

Provide comprehensive analysis including objectives, requirements, and implementation approach."""
        
        # Format the prompt with actual values
        analysis_prompt = prompt_template.format(
            work_type=work_type,
            description_text=description_text,
            components_str=components_str,
            acceptance_criteria=acceptance_criteria,
            change_category=change_category,
            comments_text=comments_text,
            linked_issues=linked_issues_context,
            attachments_info=attachments_context
        )
        
        if self.ai_client.debug:
            self._log("\n[DEBUG] Analysis Prompt:")
            self._log("-" * 40)
            self._log(analysis_prompt)
            self._log("-" * 40)

        messages = [
            {"role": "user", "content": analysis_prompt}
        ]
        
        # Use analysis model (gemini-3.0-pro-preview with 900k tokens)
        result = self.ai_client.chat_completion(messages, task_type="analysis", mode="jira_analysis")
        
        if result['success']:
            response_content = result['response']['choices'][0]['message']['content']
            self._log("✓ AI Analysis completed")
            return {
                'success': True,
                'analysis': response_content,
                'raw_data': {
                    'work_type': work_type,
                    'description': description_text,
                    'components': components,
                    'acceptance_criteria': acceptance_criteria,
                    'change_category': change_category,
                    'comments': comments
                }
            }
        else:
            self._log(f"✗ AI Analysis failed: {result['error']}")
            return {'success': False, 'error': result['error']}
    
    def clone_repository(self, repo_name: str, branch_name: str, issue_key: str) -> Optional[str]:
        """Clone Bitbucket repository to local directory and switch to branch"""
        self._log(f"\n[Cloning Repository: {repo_name}]")
        
        # Extract project key from repo name if format is PROJECT/repo
        if '/' in repo_name:
            project_key, repo_slug = repo_name.split('/', 1)
            self._log(f"  Using provided project: {project_key}")
        else:
            # Default to TESTSSD project
            repo_slug = repo_name
            project_key = "TESTSSD"
            self._log(f"  Using default project: {project_key}")
        
        # Construct clone URL with credentials (use bitbucket_username)
        clone_url = f"https://{self.bitbucket_username}:{self.bitbucket_token}@"
        clone_url += (self.bitbucket_base_url or '').replace('https://', '')
        clone_url += f"/scm/{project_key}/{repo_slug}.git"
        
        # Create Repos directory if not exists
        repos_dir = "Repos"
        if not os.path.exists(repos_dir):
            os.makedirs(repos_dir)
            self._log(f"  Created directory: {repos_dir}")

        # Create local directory name with issue key first, then repo slug
        local_dir = os.path.join(repos_dir, f"{issue_key}_{repo_slug}")
        
        try:
            # Clone repository
            self._log(f"  Cloning from: {project_key}/{repo_slug}")
            self._log(f"  Local directory: {local_dir}")
            
            # Run git clone with live output streaming
            process = subprocess.Popen(
                ['git', 'clone', '--progress', clone_url, local_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Stream output in real-time to GUI only (not to log file)
            for line in (process.stdout or []):
                line = line.strip()
                if line:
                    self._log_gui_only(f"  {line}")
            
            process.wait()
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, 'git clone')
            
            self._log(f"✓ Repository cloned successfully")
            
            # Switch to specified branch
            self._log(f"  Switching to branch: {branch_name}")
            
            process = subprocess.Popen(
                ['git', 'checkout', branch_name],
                cwd=local_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Stream checkout output to GUI only (not to log file)
            for line in (process.stdout or []):
                line = line.strip()
                if line:
                    self._log_gui_only(f"  {line}")
            
            process.wait()
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, 'git checkout')
            
            self._log(f"✓ Switched to branch: {branch_name}")
            
            return local_dir
            
        except subprocess.CalledProcessError as e:
            # Handle error message - stderr might be None
            error_msg = ""
            if hasattr(e, 'stderr') and e.stderr is not None:
                error_msg = e.stderr.decode('utf-8') if isinstance(e.stderr, bytes) else str(e.stderr)
            elif hasattr(e, 'output') and e.output is not None:
                error_msg = e.output.decode('utf-8') if isinstance(e.output, bytes) else str(e.output)
            else:
                error_msg = str(e)
            
            self._log(f"✗ Git operation failed: {error_msg}")
            
            # Check for specific error conditions
            if 'already exists' in error_msg.lower() or os.path.exists(local_dir):
                self._log(f"\n  Error: Directory '{local_dir}' already exists!")
                self._log(f"  Please remove or rename the existing directory before cloning.")
                self._log(f"  Or use 'Load Workflow' to continue with existing repository.")
            elif 'not found' in error_msg.lower():
                self._log(f"\n  Troubleshooting:")
                self._log(f"  1. Verify repository exists: {self.bitbucket_base_url}/projects/{project_key}/repos/{repo_slug}")
                self._log(f"  2. Check you have read access to the repository")
                self._log(f"  3. Verify project key is correct: {project_key}")
            
            return None
        except Exception as e:
            self._log(f"✗ Error cloning repository: {str(e)}")
            return None
            
    
    def create_feature_branch(self, repo_path: str, issue_key: str, base_branch: str, custom_branch_name: Optional[str] = None) -> bool:
        """Create a feature branch for the JIRA issue"""
        self._log(f"\n[Creating Feature Branch]")
        
        if custom_branch_name and custom_branch_name.strip() and custom_branch_name.lower() != "auto populate" and "if empty," not in custom_branch_name.lower():
            feature_branch = custom_branch_name.strip()
        else:
            feature_branch = f"feature/{issue_key}"
        
        try:
            # Check if branch already exists
            result = subprocess.run(
                ['git', 'branch', '--list', feature_branch],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            
            if result.stdout.strip():
                self._log(f"  Branch {feature_branch} already exists, checking it out...")
                subprocess.run(['git', 'checkout', feature_branch], cwd=repo_path, check=True)
            else:
                # Create and checkout new branch from base branch
                self._log(f"  Creating new branch: {feature_branch} from {base_branch}")
                subprocess.run(['git', 'checkout', '-b', feature_branch], cwd=repo_path, check=True)
            
            self._log(f"✓ Feature branch ready: {feature_branch}")
            return True
            
        except subprocess.CalledProcessError as e:
            self._log(f"✗ Failed to create feature branch: {e}")
            return False
    
    def implement_code_changes(self, repo_path: str, jira_analysis: Dict, impact_analysis: Dict, repo_index: Dict) -> bool:
        """Use AI to implement code changes based on JIRA analysis"""
        assert self.ai_client is not None, "AI client not initialized. Call get_credentials() first."
        self._log(f"\n[Implementing Code Changes with AI]")
        
        # Get list of key files to analyze
        key_files = []
        for file_info in repo_index['file_index'][:20]:  # Top 20 files
            file_path = os.path.join(repo_path, file_info['path'])
            if os.path.isfile(file_path) and file_info['size'] < 100000:  # Skip large files
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        key_files.append({
                            'path': file_info['path'],
                            'content': content[:2000]  # First 2000 chars
                        })
                except:
                    pass
        
        # Get prompt template from config
        prompt_template = self.ai_client.get_prompt_template('code_implementation')
        if not prompt_template:
            prompt_template = """Based on the JIRA analysis and code impact assessment, provide specific code changes:

**JIRA Analysis:**
{jira_analysis}

**Impact Analysis:**
{impact_analysis}

**Sample Code Files:**
{sample_files}

Provide implementation in this EXACT format:

## FILES_TO_MODIFY
- path/to/file1.py: Brief description of changes
- path/to/file2.js: Brief description of changes

## CODE_CHANGES
### path/to/file1.py
```python
# Complete updated code for this file
```

### path/to/file2.js
```javascript
// Complete updated code for this file
```

## NEW_FILES
### path/to/newfile.py
```python
# Complete code for new file
```

## IMPLEMENTATION_GUIDE
Step-by-step guide for manual review and additional changes.
"""
        
        impl_prompt = prompt_template.format(
            jira_analysis=jira_analysis.get('analysis', 'N/A'),
            impact_analysis=impact_analysis.get('impact_analysis', 'N/A'),
            sample_files=json.dumps(key_files[:5], indent=2)
        )
        
        if self.ai_client.debug:
            self._log("\n[DEBUG] Implementation Prompt:")
            self._log("-" * 40)
            self._log(impl_prompt[:2000] + "... (truncated)" if len(impl_prompt) > 2000 else impl_prompt)
            self._log("-" * 40)

        messages = [
            {"role": "user", "content": impl_prompt}
        ]
        
        # Use code generation model (claude-sonnet-4-5 with 180k tokens)
        result = self.ai_client.chat_completion(messages, task_type="code_generation", mode="code_implementation")
        
        if result['success']:
            implementation_plan = result['response']['choices'][0]['message']['content']
            self._log("✓ AI generated implementation plan")
            
            # Save implementation plan
            plan_file = os.path.join(repo_path, "IMPLEMENTATION_PLAN.md")
            with open(plan_file, 'w', encoding='utf-8') as f:
                f.write(f"# Implementation Plan\n\n")
                f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(implementation_plan)
            
            self._log(f"  Implementation plan saved to: IMPLEMENTATION_PLAN.md")
            
            # Ask if user wants to apply changes automatically
            self._log("\n" + "=" * 80)
            apply_auto = input("Apply code changes automatically? (y/n): ").strip().lower()
            
            if apply_auto == 'y':
                changes_applied = self._apply_code_changes(repo_path, implementation_plan)
                if changes_applied > 0:
                    self._log(f"\n✓ Applied {changes_applied} code changes")
                    self._log("  Review changes with: git diff")
                else:
                    self._log("\n  No automatic changes applied. Review IMPLEMENTATION_PLAN.md for manual changes.")
            else:
                self._log("\n  Review IMPLEMENTATION_PLAN.md and apply changes manually.")
            
            return True
        else:
            self._log(f"✗ Failed to generate implementation plan: {result['error']}")
            return False
    
    def _apply_code_changes(self, repo_path: str, implementation_plan: str) -> int:
        """Parse and apply code changes from implementation plan"""
        changes_applied = 0
        
        try:
            # Simple parser to extract code blocks
            lines = implementation_plan.split('\n')
            current_file = None
            current_code = []
            in_code_block = False
            
            for line in lines:
                # Detect file headers like "### path/to/file.py"
                if line.startswith('###') and not in_code_block:
                    # Save previous file if any
                    if current_file and current_code:
                        self._write_code_to_file(repo_path, current_file, '\n'.join(current_code))
                        changes_applied += 1
                        current_code = []
                    
                    # Extract new file path
                    current_file = line.replace('###', '').strip()
                    continue
                
                # Detect code block start/end
                if line.strip().startswith('```'):
                    in_code_block = not in_code_block
                    continue
                
                # Collect code lines
                if in_code_block and current_file:
                    current_code.append(line)
            
            # Save last file
            if current_file and current_code:
                self._write_code_to_file(repo_path, current_file, '\n'.join(current_code))
                changes_applied += 1
            
        except Exception as e:
            self._log(f"  Warning: Error applying some changes: {str(e)}")
        
        return changes_applied
    
    def _write_code_to_file(self, repo_path: str, file_path: str, content: str):
        """Write code content to a file"""
        full_path = os.path.join(repo_path, file_path)
        
        # Create directories if needed
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # Write content
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        self._log(f"  ✓ Updated: {file_path}")
    
    def stage_changes(self, repo_path: str) -> bool:
        """Stage all changes for commit"""
        self._log(f"\n[Staging Changes]")
        
        try:
            # Add all changes
            subprocess.run(['git', 'add', '.'], cwd=repo_path, check=True)
            
            # Show status
            result = subprocess.run(
                ['git', 'status', '--short'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            if result.stdout.strip():
                self._log("✓ Changes staged:")
                self._log(result.stdout)
                return True
            else:
                self._log("  No changes to stage")
                return False
                
        except subprocess.CalledProcessError as e:
            self._log(f"✗ Failed to stage changes: {e}")
            return False
    
    def index_repository(self, repo_path: str) -> Dict:
        """Index repository and identify code structure"""
        self._log(f"\n[Indexing Repository: {repo_path}]")
        
        file_index = []
        code_stats = {
            'total_files': 0,
            'by_extension': {},
            'directories': set()
        }
        
        # Walk through repository
        for root, dirs, files in os.walk(repo_path):
            # Skip .git directory
            if '.git' in root:
                continue
            
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, repo_path)
                
                # Get file extension
                _, ext = os.path.splitext(file)
                
                file_index.append({
                    'path': rel_path,
                    'extension': ext,
                    'size': os.path.getsize(file_path)
                })
                
                code_stats['total_files'] += 1
                code_stats['by_extension'][ext] = code_stats['by_extension'].get(ext, 0) + 1
                code_stats['directories'].add(os.path.dirname(rel_path))
        
        code_stats['directories'] = list(code_stats['directories'])
        
        self._log(f"✓ Indexed {code_stats['total_files']} files")
        self._log(f"  File types: {len(code_stats['by_extension'])}")
        self._log(f"  Directories: {len(code_stats['directories'])}")
        
        return {
            'file_index': file_index,
            'stats': code_stats
        }
    
    def analyze_code_impact(self, repo_path: str, repo_index: Dict, jira_analysis: Dict) -> Dict:
        """Analyze potential code changes and dependencies using AI"""
        assert self.ai_client is not None, "AI client not initialized. Call get_credentials() first."
        self._log("\n[Analyzing Code Impact with AI]")
        
        # Prepare code context
        stats = repo_index['stats']
        file_types = ', '.join([f"{ext}({count})" for ext, count in sorted(stats['by_extension'].items(), key=lambda x: x[1], reverse=True)[:10]])
        
        # Get sample of key files
        key_files = []
        for file_info in repo_index['file_index'][:50]:  # Limit to first 50 files
            key_files.append(file_info['path'])
        
        # Get prompt template from config
        prompt_template = self.ai_client.get_prompt_template('code_impact')
        if not prompt_template:
            prompt_template = """Based on the JIRA analysis and repository structure, identify potential code changes and impacts:

**JIRA Analysis:**
{jira_analysis}

**Repository Statistics:**
- Total Files: {total_files}
- File Types: {file_types}
- Key Directories: {key_directories}

**Sample Files:**
{sample_files}

Please provide:
1. **Affected Components**: Which parts of the codebase will likely need changes
2. **File Patterns**: What types of files should be modified (e.g., *.py, *.java, config files)
3. **Cross-Product Dependencies**: Any dependencies on other modules or services
4. **Integration Points**: APIs, databases, or external systems that may be affected
5. **Recommended Changes**: Specific files or modules to focus on
"""
        
        impact_prompt = prompt_template.format(
            jira_analysis=jira_analysis.get('analysis', 'N/A'),
            total_files=stats['total_files'],
            file_types=file_types,
            key_directories=', '.join(stats['directories'][:20]),
            sample_files=chr(10).join(key_files[:30])
        )
        
        if self.ai_client.debug:
            self._log("\n[DEBUG] Impact Analysis Prompt:")
            self._log("-" * 40)
            self._log(impact_prompt[:2000] + "... (truncated)" if len(impact_prompt) > 2000 else impact_prompt)
            self._log("-" * 40)

        messages = [
            {"role": "user", "content": impact_prompt}
        ]
        
        # Use analysis model for impact analysis
        result = self.ai_client.chat_completion(messages, task_type="analysis", mode="code_impact")
        
        if result['success']:
            response_content = result['response']['choices'][0]['message']['content']
            self._log("✓ Code impact analysis completed")
            return {
                'success': True,
                'impact_analysis': response_content
            }
        else:
            self._log(f"✗ Code impact analysis failed: {result['error']}")
            return {'success': False, 'error': result['error']}
    
    def generate_test_scenarios(self, jira_analysis: Dict, repo_index: Dict) -> Dict:
        """Generate comprehensive test scenarios using AI"""
        assert self.ai_client is not None, "AI client not initialized. Call get_credentials() first."
        self._log("\n[Generating Test Scenarios with AI]")
        
        stats = repo_index['stats']
        
        # Get prompt template from config
        prompt_template = self.ai_client.get_prompt_template('test_scenarios')
        if not prompt_template:
            prompt_template = """Based on the JIRA requirements and codebase, create comprehensive test scenarios:

**JIRA Analysis:**
{jira_analysis}

**Repository Context:**
- Total Files: {total_files}
- Main File Types: {main_file_types}

Please provide:
1. **Functional Test Cases**: Test scenarios to validate the change request requirements
2. **Edge Cases**: Boundary conditions and error scenarios to test
3. **Integration Tests**: Tests for interactions with other components
4. **Regression Tests**: Areas that should be tested to ensure no breaking changes
5. **Expected Outputs**: For each test case, what the expected result should be
6. **Test Data Requirements**: What test data is needed for validation

Format each test case with:
- Test ID
- Description
- Preconditions
- Steps
- Expected Result
"""
        
        test_prompt = prompt_template.format(
            jira_analysis=jira_analysis.get('analysis', 'N/A'),
            total_files=stats['total_files'],
            main_file_types=', '.join([f"{ext}({count})" for ext, count in sorted(stats['by_extension'].items(), key=lambda x: x[1], reverse=True)[:5]])
        )
        
        if self.ai_client.debug:
            self._log("\n[DEBUG] Test Scenarios Prompt:")
            self._log("-" * 40)
            self._log(test_prompt[:2000] + "... (truncated)" if len(test_prompt) > 2000 else test_prompt)
            self._log("-" * 40)

        messages = [
            {"role": "user", "content": test_prompt}
        ]
        
        # Use code generation model for test scenarios
        result = self.ai_client.chat_completion(messages, task_type="code_generation", mode="test_scenarios")
        
        if result['success']:
            response_content = result['response']['choices'][0]['message']['content']
            self._log("✓ Test scenarios generated")
            return {
                'success': True,
                'test_scenarios': response_content
            }
        else:
            self._log(f"✗ Test scenario generation failed: {result['error']}")
            return {'success': False, 'error': result['error']}
    
    def assess_risks(self, jira_analysis: Dict, impact_analysis: Dict) -> Dict:
        """Evaluate change complexity, risks, and mitigation strategies using AI"""
        assert self.ai_client is not None, "AI client not initialized. Call get_credentials() first."
        self._log("\n[Assessing Risks with AI]")
        
        # Get prompt template from config
        prompt_template = self.ai_client.get_prompt_template('risk_assessment')
        if not prompt_template:
            prompt_template = """Perform a comprehensive risk assessment for this change request:

**JIRA Analysis:**
{jira_analysis}

**Impact Analysis:**
{impact_analysis}

Please provide:
1. **Change Complexity**: Rate complexity (Low/Medium/High) and explain why
2. **Impact Scope**: How widespread are the changes (localized/moderate/extensive)
3. **Dependency Risks**: Risks related to dependencies on other systems/components
4. **Technical Risks**: Potential technical challenges or pitfalls
5. **Timeline Risks**: Factors that could affect delivery timeline
6. **Mitigation Strategies**: Specific actions to reduce identified risks
7. **Rollback Plan**: Steps to revert changes if issues arise
8. **Monitoring Requirements**: What should be monitored post-deployment
"""
        
        risk_prompt = prompt_template.format(
            jira_analysis=jira_analysis.get('analysis', 'N/A'),
            impact_analysis=impact_analysis.get('impact_analysis', 'N/A')
        )
        
        if self.ai_client.debug:
            self._log("\n[DEBUG] Risk Assessment Prompt:")
            self._log("-" * 40)
            self._log(risk_prompt[:2000] + "... (truncated)" if len(risk_prompt) > 2000 else risk_prompt)
            self._log("-" * 40)

        messages = [
            {"role": "user", "content": risk_prompt}
        ]
        
        # Use analysis model for risk assessment
        result = self.ai_client.chat_completion(messages, task_type="analysis", mode="risk_assessment")
        
        if result['success']:
            response_content = result['response']['choices'][0]['message']['content']
            self._log("✓ Risk assessment completed")
            return {
                'success': True,
                'risk_assessment': response_content
            }
        else:
            self._log(f"✗ Risk assessment failed: {result['error']}")
            return {'success': False, 'error': result['error']}
    
    def save_analysis_report(self, issue_key: str, all_results: Dict):
        """Save comprehensive analysis report to HTML file"""
        self._log("\n[Saving Analysis Report]")
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"JIRA_Analysis_{issue_key}_{timestamp}.html"
        
        # Convert markdown-style content to HTML
        def md_to_html(text):
            """Simple markdown to HTML converter"""
            if not text or text == 'N/A':
                return '<p>N/A</p>'
            # Convert headers
            text = text.replace('# ', '<h1>').replace('\n## ', '</h1>\n<h2>').replace('\n### ', '</h2>\n<h3>')
            # Convert bold
            import re
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            # Convert lists
            text = re.sub(r'\n- ', '\n<li>', text)
            text = re.sub(r'(<li>.+?)(\n(?!<li>))', r'<ul>\1</ul>\2', text, flags=re.DOTALL)
            # Convert code blocks
            text = re.sub(r'```(\w+)?\n(.+?)\n```', r'<pre><code>\2</code></pre>', text, flags=re.DOTALL)
            # Convert paragraphs
            text = re.sub(r'\n\n', '</p><p>', text)
            return f'<p>{text}</p>'
        
        report = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JIRA Analysis Report - {issue_key}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin-bottom: 30px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        .header h1 {{
            margin: 0;
            font-size: 2.5em;
        }}
        .metadata {{
            margin-top: 10px;
            opacity: 0.9;
        }}
        .section {{
            background: white;
            padding: 25px;
            margin-bottom: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .section h2 {{
            color: #667eea;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
            margin-top: 0;
        }}
        .section h3 {{
            color: #764ba2;
            margin-top: 20px;
        }}
        pre {{
            background-color: #f4f4f4;
            border-left: 4px solid #667eea;
            padding: 15px;
            overflow-x: auto;
            border-radius: 4px;
        }}
        code {{
            font-family: 'Courier New', monospace;
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
        }}
        ul {{
            padding-left: 20px;
        }}
        li {{
            margin: 8px 0;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }}
        .stat-box {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #667eea;
        }}
        .stat-label {{
            font-weight: bold;
            color: #666;
            font-size: 0.9em;
        }}
        .stat-value {{
            font-size: 1.3em;
            color: #333;
            margin-top: 5px;
        }}
        strong {{
            color: #764ba2;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>JIRA Request Analysis Report</h1>
        <div class="metadata">
            <strong>Issue:</strong> {issue_key}<br>
            <strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>

    <div class="section">
        <h2>1. JIRA Request Analysis</h2>
        {md_to_html(all_results.get('jira_analysis', {}).get('analysis', 'N/A'))}
    </div>

    <div class="section">
        <h2>2. Code Impact Analysis</h2>
        {md_to_html(all_results.get('impact_analysis', {}).get('impact_analysis', 'N/A'))}
    </div>

    <div class="section">
        <h2>3. Test Scenarios</h2>
        {md_to_html(all_results.get('test_scenarios', {}).get('test_scenarios', 'N/A'))}
    </div>

    <div class="section">
        <h2>4. Risk Assessment</h2>
        {md_to_html(all_results.get('risk_assessment', {}).get('risk_assessment', 'N/A'))}
    </div>

    <div class="section">
        <h2>5. Repository Information</h2>
        <div class="stats">
            <div class="stat-box">
                <div class="stat-label">Local Path</div>
                <div class="stat-value">{all_results.get('repo_path', 'N/A')}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Total Files</div>
                <div class="stat-value">{all_results.get('repo_stats', {}).get('total_files', 'N/A')}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">File Types</div>
                <div class="stat-value">{', '.join([f"{ext}({count})" for ext, count in sorted(all_results.get('repo_stats', {}).get('by_extension', {}).items(), key=lambda x: x[1], reverse=True)[:5]])}</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Summary</h2>
        <p>This analysis was generated using AI-powered analysis of the JIRA request, repository structure, and best practices for change implementation.</p>
        <h3>Next Steps:</h3>
        <ul>
            <li>Review the analysis and test scenarios</li>
            <li>Implement changes in the identified components</li>
            <li>Execute the test scenarios</li>
            <li>Monitor the identified risk areas</li>
            <li>Follow the mitigation strategies</li>
        </ul>
    </div>
</body>
</html>
"""
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(report)
            self._log(f"✓ Analysis report saved to: {filename}")
            return filename
        except Exception as e:
            self._log(f"✗ Error saving report: {str(e)}")
            return None
    
    def run(self):
        """Main execution flow"""
        self._log("\n" + "=" * 80)
        self._log("JIRA REQUEST ANALYZER & IMPLEMENTER")
        self._log("=" * 80)
        
        try:
            # Step 1: Get credentials
            self.get_credentials()
            
            # Step 2: Get task details
            issue_key, repo_name, branch_name = self.get_task_details()
            
            # Step 3: Fetch JIRA issue
            issue_data = self.fetch_jira_issue(issue_key)
            if not issue_data:
                self._log("\n✗ Failed to fetch JIRA issue. Exiting.")
                return
            
            # Step 4: Analyze JIRA request
            jira_analysis = self.analyze_jira_request(issue_data)
            if not jira_analysis.get('success'):
                self._log("\n✗ Failed to analyze JIRA request. Exiting.")
                return
            
            # Step 5: Clone repository
            repo_path = self.clone_repository(repo_name, branch_name, issue_key)
            if not repo_path:
                self._log("\n✗ Failed to clone repository. Exiting.")
                return
            
            # Step 6: Create feature branch
            if not self.create_feature_branch(repo_path, issue_key, branch_name):
                self._log("\n✗ Failed to create feature branch. Exiting.")
                return
            
            # Step 7: Index repository
            repo_index = self.index_repository(repo_path)
            
            # Step 8: Analyze code impact
            impact_analysis = self.analyze_code_impact(repo_path, repo_index, jira_analysis)
            
            # Step 9: Generate test scenarios
            test_scenarios = self.generate_test_scenarios(jira_analysis, repo_index)
            
            # Step 10: Assess risks
            risk_assessment = self.assess_risks(jira_analysis, impact_analysis)
            
            # Step 11: Implement code changes (AI-guided)
            self._log("\n" + "=" * 80)
            implement = input("Generate AI implementation plan? (y/n): ").strip().lower()
            if implement == 'y':
                self.implement_code_changes(repo_path, jira_analysis, impact_analysis, repo_index)
                
                # Ask if user wants to stage changes
                stage = input("\nStage changes for commit? (y/n): ").strip().lower()
                if stage == 'y':
                    self.stage_changes(repo_path)
            
            # Step 12: Save comprehensive report
            all_results = {
                'jira_analysis': jira_analysis,
                'impact_analysis': impact_analysis,
                'test_scenarios': test_scenarios,
                'risk_assessment': risk_assessment,
                'repo_path': repo_path,
                'repo_stats': repo_index['stats']
            }
            
            report_file = self.save_analysis_report(issue_key, all_results)
            
            # Final summary
            self._log("\n" + "=" * 80)
            self._log("ANALYSIS COMPLETE")
            self._log("=" * 80)
            self._log(f"\n✓ JIRA Issue: {issue_key}")
            self._log(f"✓ Repository: {repo_name} (branch: {branch_name})")
            self._log(f"✓ Local Path: {repo_path}")
            self._log(f"✓ Report: {report_file}")
            self._log("\nAll analysis steps completed successfully!")
            self._log("Review the generated report for detailed findings and recommendations.")
            self._log("=" * 80 + "\n")
            
        except KeyboardInterrupt:
            self._log("\n\n✗ Operation cancelled by user.")
            sys.exit(1)
        except Exception as e:
            self._log(f"\n✗ Unexpected error: {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


def main():
    """Entry point for the script"""
    analyzer = JIRAAnalyzer()
    analyzer.run()


if __name__ == "__main__":
    main()
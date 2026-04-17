# -*- coding: utf-8 -*-
"""
controller/config_controller.py
================================
Config Controller - Phase 3

Handles configuration testing and validation.
"""

import logging
import threading
import urllib.request
import urllib.error
import ssl
import json

logger = logging.getLogger("bento_app")


class ConfigController:
    """
    Handles configuration testing and validation.
    """

    def __init__(self, context):
        self._context = context
        self._running = False
        logger.info("ConfigController initialised.")

    def is_running(self):
        return self._running

    def test_config(self, callback):
        """
        Test configuration by attempting to connect to JIRA, Bitbucket, and Model Gateway.
        
        Args:
            callback: Function to call with results (thread-safe)
        """
        def _test():
            self._running = True
            try:
                results = {
                    'jira': self._test_jira(),
                    'bitbucket': self._test_bitbucket(),
                    'model_gateway': self._test_model_gateway()
                }
                
                # Thread-safe callback
                if callback:
                    self._context.root.after(0, lambda: callback(results))
                    
            except Exception as e:
                error_msg = str(e)
                logger.error(f"ConfigController.test_config: {error_msg}")
                if callback:
                    self._context.root.after(0, lambda _m=error_msg: callback({
                        'error': _m
                    }))
            finally:
                self._running = False

        threading.Thread(target=_test, daemon=True, name="bento-config-test").start()

    def _test_jira(self):
        """Test JIRA connection"""
        try:
            jira_url = self._context.config.get('jira', {}).get('base_url', '')
            if not jira_url:
                return {'success': False, 'error': 'JIRA URL not configured'}
            
            # Simple connectivity test
            ssl_context = ssl._create_unverified_context()
            request = urllib.request.Request(jira_url)
            urllib.request.urlopen(request, context=ssl_context, timeout=10)
            return {'success': True, 'message': 'JIRA reachable'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _test_bitbucket(self):
        """Test Bitbucket connection"""
        try:
            bb_url = self._context.config.get('bitbucket', {}).get('base_url', '')
            if not bb_url:
                return {'success': False, 'error': 'Bitbucket URL not configured'}
            
            # Simple connectivity test
            ssl_context = ssl._create_unverified_context()
            request = urllib.request.Request(bb_url)
            urllib.request.urlopen(request, context=ssl_context, timeout=10)
            return {'success': True, 'message': 'Bitbucket reachable'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _test_model_gateway(self):
        """Test Model Gateway connection"""
        try:
            model_url = self._context.config.get('model_gateway', {}).get('base_url', '')
            if not model_url:
                return {'success': False, 'error': 'Model Gateway URL not configured'}
            
            # Simple connectivity test
            ssl_context = ssl._create_unverified_context()
            request = urllib.request.Request(model_url)
            urllib.request.urlopen(request, context=ssl_context, timeout=10)
            return {'success': True, 'message': 'Model Gateway reachable'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

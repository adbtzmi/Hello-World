import urllib.request
import ssl
import base64
import json

class ConnectivityService:
    """Service to check JIRA, Bitbucket, and AI Model API connectivity."""
    
    @staticmethod
    def check_jira(email, token, base_url, log_callback):
        if not token or not email:
            log_callback("⚠ Cannot check JIRA - credentials not loaded")
            return False
            
        log_callback("\n[Checking JIRA Connectivity...]")
        try:
            url = f"{base_url}/rest/api/2/myself"
            auth_string = f"{email}:{token}"
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
            
            username = data.get('displayName', data.get('name', 'Unknown'))
            log_callback(f"  ✓ JIRA Connection: SUCCESS")
            log_callback(f"  ✓ Logged in as: {username}")
            log_callback(f"  ✓ URL: {base_url}")
            return True
        except urllib.error.HTTPError as e:
            log_callback(f"  ✗ JIRA Connection: FAILED (HTTP {e.code})")
            log_callback(f"  ✗ Error: {e.read().decode('utf-8')[:200]}")
            return False
        except Exception as e:
            log_callback(f"  ✗ JIRA Connection: FAILED")
            log_callback(f"  ✗ Error: {str(e)}")
            return False

    @staticmethod
    def check_bitbucket(username, token, base_url, log_callback):
        if not token or not username:
            log_callback("⚠ Cannot check Bitbucket - credentials not loaded")
            return False
            
        log_callback("\n[Checking Bitbucket Connectivity...]")
        try:
            url = f"{base_url}/rest/api/1.0/projects?limit=1"
            auth_string = f"{username}:{token}"
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
            
            project_count = data.get('size', 0)
            log_callback(f"  ✓ Bitbucket Connection: SUCCESS")
            log_callback(f"  ✓ Username: {username}")
            log_callback(f"  ✓ URL: {base_url}")
            log_callback(f"  ✓ Accessible projects: {project_count}+")
            return True
        except urllib.error.HTTPError as e:
            log_callback(f"  ✗ Bitbucket Connection: FAILED (HTTP {e.code})")
            log_callback(f"  ✗ Error: {e.read().decode('utf-8')[:200]}")
            return False
        except Exception as e:
            log_callback(f"  ✗ Bitbucket Connection: FAILED")
            log_callback(f"  ✗ Error: {str(e)}")
            return False

    @staticmethod
    def test_model_prompt(model_name, model_type, api_key, gateway_url, log_callback):
        try:
            url = f"{gateway_url}/chat/completions"
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": "Say 'OK' if you can read this."}]
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            data = json.dumps(payload).encode('utf-8')
            request = urllib.request.Request(url, data, headers)
            ssl_context = ssl._create_unverified_context()
            response = urllib.request.urlopen(request, context=ssl_context, timeout=30)
            response_data = json.loads(response.read().decode('utf-8'))
            
            if 'choices' in response_data and len(response_data['choices']) > 0:
                log_callback(f"    ✓ {model_type} Model Test: PASSED (model responded)")
                return True
            else:
                log_callback(f"    ✗ {model_type} Model Test: FAILED (unexpected response)")
                return False
        except urllib.error.HTTPError as e:
            log_callback(f"    ✗ {model_type} Model Test: FAILED (HTTP {e.code})")
            return False
        except Exception as e:
            log_callback(f"    ✗ {model_type} Model Test: FAILED ({str(e)[:100]})")
            return False

    @staticmethod
    def check_models(api_key, gateway_url, analysis_model, code_model, log_callback):
        if not api_key:
            log_callback("⚠ Cannot check models - no API key loaded")
            return False
            
        log_callback("\n[Checking Model Availability...]")
        try:
            url = f"{gateway_url}/models"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            request = urllib.request.Request(url, headers=headers)
            ssl_context = ssl._create_unverified_context()
            response = urllib.request.urlopen(request, context=ssl_context, timeout=30)
            data = json.loads(response.read().decode('utf-8'))
            
            if 'data' in data:
                available_models = [m.get('id') for m in data['data']]
                log_callback(f"  Gateway: {gateway_url}")
                
                models_ok = True
                
                if analysis_model in available_models:
                    log_callback(f"  ✓ Analysis Model: {analysis_model} - LISTED")
                    if not ConnectivityService.test_model_prompt(analysis_model, "Analysis", api_key, gateway_url, log_callback):
                        models_ok = False
                else:
                    log_callback(f"  ✗ Analysis Model: {analysis_model} - NOT FOUND")
                    models_ok = False
                
                if code_model in available_models:
                    log_callback(f"  ✓ Code Model: {code_model} - LISTED")
                    if not ConnectivityService.test_model_prompt(code_model, "Code", api_key, gateway_url, log_callback):
                        models_ok = False
                else:
                    log_callback(f"  ✗ Code Model: {code_model} - NOT FOUND")
                    models_ok = False
                
                return models_ok
            else:
                log_callback(f"  ⚠ Unexpected response format from Model Gateway")
                return False
                
        except Exception as e:
            log_callback(f"  ✗ Error checking models: {str(e)}")
            return False
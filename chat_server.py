#!/usr/bin/env python3
"""
Web-based Chat Server for BENTO AI Interactive Chat
Uses Python's built-in http.server - no external dependencies needed.
"""

import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class ChatState:
    """Shared state between the chat server and the main GUI"""
    
    def __init__(self):
        self.issue_key = ""
        self.step_name = ""
        self.instructions = ""
        self.model_used = ""
        self.messages = []  # List of {"role": str, "content": str, "model": str}
        self.chat_messages_history = []  # For AI context (role/content only)
        self.result = None  # "approved", "cancelled", "force_closed"
        self.result_event = threading.Event()  # Signaled when user acts
        self.send_callback = None  # Function to call when user sends a message
        self.log_callback = None  # Function to log to GUI
        self.ai_client = None  # Reference to AI client
        self.task_type = "analysis"  # Task type for AI model selection
        self.debug = False


class ChatRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the chat web interface"""
    
    chat_state = None  # Will be set by the server
    
    def log_message(self, format, *args):
        """Suppress default HTTP logging"""
        pass
    
    def _send_json(self, data, status=200):
        """Send JSON response"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def _send_html(self, html_content):
        """Send HTML response"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
    
    def _read_body(self):
        """Read request body"""
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            return self.rfile.read(content_length).decode('utf-8')
        return ''
    
    def do_GET(self):
        """Handle GET requests"""
        path = urlparse(self.path).path
        
        if path == '/' or path == '/chat':
            # Serve the chat HTML page
            template_path = os.path.join(os.path.dirname(__file__), 'templates', 'chat.html')
            try:
                with open(template_path, 'r', encoding='utf-8') as f:
                    html = f.read()
                self._send_html(html)
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Chat template not found')
        
        elif path == '/api/config':
            # Return chat configuration
            state = self.__class__.chat_state
            self._send_json({
                'issueKey': state.issue_key,
                'stepName': state.step_name,
                'instructions': state.instructions,
                'modelUsed': state.model_used
            })
        
        elif path == '/api/messages':
            # Return all messages
            state = self.__class__.chat_state
            self._send_json(state.messages)
        
        elif path == '/api/status':
            # Return current status
            state = self.__class__.chat_state
            self._send_json({
                'result': state.result,
                'messageCount': len(state.messages)
            })
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_POST(self):
        """Handle POST requests"""
        path = urlparse(self.path).path
        state = self.__class__.chat_state
        
        if path == '/api/send':
            # User sent a message
            body = self._read_body()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}
            
            message = data.get('message', '').strip()
            if not message:
                self._send_json({'success': False, 'error': 'Empty message'})
                return
            
            # Add user message to state
            state.messages.append({
                'role': 'user',
                'content': message,
                'model': None
            })
            state.chat_messages_history.append({
                'role': 'user',
                'content': message
            })
            
            if state.log_callback:
                state.log_callback(f"  💬 User: {message[:100]}{'...' if len(message) > 100 else ''}")
            
            # Get AI response
            try:
                if state.ai_client:
                    result = state.ai_client.chat_completion(
                        state.chat_messages_history,
                        task_type=state.task_type,
                        mode="chat_continuation"
                    )
                    
                    if result['success']:
                        response_text = result['response']['choices'][0]['message']['content']
                        model_used = result.get('model_used', state.model_used)
                        
                        # Add AI response to state
                        state.messages.append({
                            'role': 'assistant',
                            'content': response_text,
                            'model': model_used
                        })
                        state.chat_messages_history.append({
                            'role': 'assistant',
                            'content': response_text
                        })
                        
                        if state.log_callback:
                            state.log_callback(f"  🤖 AI responded ({len(response_text)} chars)")
                        
                        self._send_json({
                            'success': True,
                            'response': response_text,
                            'model': model_used
                        })
                    else:
                        error_msg = result.get('error', 'Unknown error')
                        if state.log_callback:
                            state.log_callback(f"  ✗ AI error: {error_msg}")
                        self._send_json({
                            'success': False,
                            'error': error_msg
                        })
                else:
                    self._send_json({
                        'success': False,
                        'error': 'AI client not available'
                    })
            except Exception as e:
                if state.log_callback:
                    state.log_callback(f"  ✗ Chat error: {str(e)}")
                self._send_json({
                    'success': False,
                    'error': str(e)
                })
        
        elif path == '/api/approve':
            # User approved the analysis
            state.result = "approved"
            state.result_event.set()
            if state.log_callback:
                state.log_callback("✓ User approved analysis via web chat")
            self._send_json({'success': True})
        
        elif path == '/api/cancel':
            # User cancelled the analysis
            state.result = "cancelled"
            state.result_event.set()
            if state.log_callback:
                state.log_callback("✗ User cancelled analysis via web chat")
            self._send_json({'success': True})
        
        elif path == '/api/force_close':
            # Browser was force-closed
            if state.result is None:
                state.result = "force_closed"
                state.result_event.set()
                if state.log_callback:
                    state.log_callback("⚠ Web chat window was closed without approval")
            self._send_json({'success': True})
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_OPTIONS(self):
        """Handle CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()


def find_free_port():
    """Find a free port to use"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def start_chat_server(chat_state):
    """Start the chat HTTP server in a background thread.
    Returns (server, port) tuple."""
    port = find_free_port()
    
    # Create handler class with state reference
    handler = type('Handler', (ChatRequestHandler,), {'chat_state': chat_state})
    
    server = ThreadingHTTPServer(('127.0.0.1', port), handler)
    
    # Run server in background thread
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    return server, port


def stop_chat_server(server):
    """Stop the chat server"""
    if server:
        server.shutdown()

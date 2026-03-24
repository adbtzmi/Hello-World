#!/usr/bin/env python3
"""
controller/chat_controller.py
==============================
Chat Controller - Phase 2

Handles interactive chat windows for AI refinement.
Extracted from gui/app.py lines 1745-1837.
"""

import logging
import tkinter as tk
from tkinter import ttk, scrolledtext

logger = logging.getLogger("bento_app")


class ChatController:
    """
    Manages interactive chat windows for AI-powered refinement.
    
    Supports:
    - Creating chat windows for JIRA analysis, test scenarios, etc.
    - Sending messages to AI for refinement
    - Finalizing content after user approval
    
    NOTE: self.analyzer is accessed only in send_chat_message(),
    not during __init__. This avoids the chicken-and-egg problem
    where context.analyzer might be None at construction time.
    """
    
    def __init__(self, context):
        self.context = context
        # DO NOT access context.analyzer here — it may be None
        # We'll access it in send_chat_message() when it's guaranteed to exist
        self._running = False
        logger.info("ChatController initialized.")
    
    def is_running(self):
        return self._running
    
    def create_interactive_chat(self, issue_key, title, initial_content, finalize_callback):
        """
        Create an interactive chat window for content refinement.
        
        Args:
            issue_key: JIRA issue key
            title: Window title
            initial_content: Initial AI-generated content
            finalize_callback: Callback to call when user approves content
        """
        # Close existing chat window if any
        if self.context.chat_window and self.context.chat_window.winfo_exists():
            self.context.chat_window.destroy()
        
        # Create new chat window
        chat_window = tk.Toplevel(self.context.root)
        chat_window.title(f"{title} - {issue_key}")
        chat_window.geometry("900x700")
        
        self.context.chat_window = chat_window
        self.context.current_chat_messages = [
            {"role": "assistant", "content": initial_content}
        ]
        
        # Chat display area
        chat_frame = ttk.Frame(chat_window)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        chat_display = scrolledtext.ScrolledText(
            chat_frame,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            state=tk.DISABLED
        )
        chat_display.pack(fill=tk.BOTH, expand=True)
        
        # Configure text tags for styling
        chat_display.tag_config("user", foreground="blue", font=("Segoe UI", 10, "bold"))
        chat_display.tag_config("assistant", foreground="green", font=("Segoe UI", 10))
        
        # Display initial content
        chat_display.configure(state=tk.NORMAL)
        chat_display.insert(tk.END, "AI Assistant:\n", "assistant")
        chat_display.insert(tk.END, f"{initial_content}\n\n")
        chat_display.configure(state=tk.DISABLED)
        chat_display.see(tk.END)
        
        # Input area
        input_frame = ttk.Frame(chat_window)
        input_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        ttk.Label(input_frame, text="Your message:").pack(anchor=tk.W)
        
        input_text = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Segoe UI", 10))
        input_text.pack(fill=tk.X, pady=(5, 5))
        
        # Button frame
        button_frame = ttk.Frame(input_frame)
        button_frame.pack(fill=tk.X)
        
        def send_message():
            user_message = input_text.get("1.0", tk.END).strip()
            if not user_message:
                return
            
            # Display user message
            chat_display.configure(state=tk.NORMAL)
            chat_display.insert(tk.END, "You:\n", "user")
            chat_display.insert(tk.END, f"{user_message}\n\n")
            chat_display.configure(state=tk.DISABLED)
            chat_display.see(tk.END)
            
            # Clear input
            input_text.delete("1.0", tk.END)
            
            # Send to AI
            self.send_chat_message(user_message, chat_display)
        
        def approve_content():
            # Get final content (last assistant message)
            final_content = self.context.current_chat_messages[-1]["content"]
            
            # Call finalize callback
            finalize_callback()
            
            # Close chat window
            chat_window.destroy()
            self.context.chat_window = None
        
        ttk.Button(button_frame, text="Send", command=send_message).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Approve & Save", command=approve_content, style="Accent.TButton").pack(side=tk.LEFT)
        
        # Bind Enter key to send
        input_text.bind("<Control-Return>", lambda e: send_message())
        
        logger.info(f"Created interactive chat window: {title}")
    
    def send_chat_message(self, user_message, chat_display):
        """
        Send a message to AI for refinement.
        
        Args:
            user_message: User's message
            chat_display: Chat display widget
        """
        import threading
        
        def _send():
            self._running = True
            try:
                # Add user message to history
                self.context.current_chat_messages.append({
                    "role": "user",
                    "content": user_message
                })
                
                # ✅ FIXED: Access analyzer here (not in __init__)
                # At this point, context.analyzer is guaranteed to exist
                analyzer = self.context.analyzer
                if not analyzer:
                    error_msg = "Analyzer not available"
                    logger.error(error_msg)
                    self.context.log(f"✗ {error_msg}")
                    return
                
                # Send to AI
                response = analyzer.send_chat_message(self.context.current_chat_messages)
                
                if response.get('success'):
                    ai_response = response.get('response', '')
                    
                    # Add AI response to history
                    self.context.current_chat_messages.append({
                        "role": "assistant",
                        "content": ai_response
                    })
                    
                    # Display AI response
                    def _display():
                        chat_display.configure(state=tk.NORMAL)
                        chat_display.insert(tk.END, "AI Assistant:\n", "assistant")
                        chat_display.insert(tk.END, f"{ai_response}\n\n")
                        chat_display.configure(state=tk.DISABLED)
                        chat_display.see(tk.END)
                    
                    self.context.root.after(0, _display)
                else:
                    error_msg = f"Chat error: {response.get('error')}"
                    self.context.log(f"✗ {error_msg}")
            
            except Exception as e:
                logger.error(f"send_chat_message error: {e}")
                self.context.log(f"✗ Error sending chat message: {e}")
            finally:
                self._running = False
        
        threading.Thread(target=_send, daemon=True).start()

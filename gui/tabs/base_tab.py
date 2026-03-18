import tkinter as tk
from tkinter import ttk

class BaseTab(ttk.Frame):
    """Base class for all tabs in the main notebook."""
    def __init__(self, notebook, context, title):
        super().__init__(notebook, padding="10")
        self.context = context
        notebook.add(self, text=title)
        
    def log(self, message):
        """Helper to send logs through context"""
        self.context.log(message)
        
    def show_error(self, title, message):
        from tkinter import messagebox
        messagebox.showerror(title, message)
        
    def show_info(self, title, message):
        from tkinter import messagebox
        messagebox.showinfo(title, message)
        
    def lock_gui(self):
        self.context.gui_locked = True
        # Logic to lock GUI should be handled at the main window level
        pass

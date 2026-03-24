import tkinter as tk
from tkinter import ttk

class BaseTab(ttk.Frame):
    """Base class for all tabs in the main BENTO notebook."""
    def __init__(self, notebook, context, title):
        super().__init__(notebook, padding="10")
        self.context = context
        self.root = notebook.winfo_toplevel()
        notebook.add(self, text=title)

    def log(self, message):
        """Helper to send log messages through the shared AppContext."""
        self.context.log(message)

    def show_error(self, title, message):
        from tkinter import messagebox
        messagebox.showerror(title, message)

    def show_info(self, title, message):
        from tkinter import messagebox
        messagebox.showinfo(title, message)

    def lock_gui(self):
        self.context.gui_locked = True
        if hasattr(self.context, "lockable_buttons"):
            for btn in self.context.lockable_buttons:
                try:
                    btn.config(state=tk.DISABLED)
                except Exception:
                    pass

    def unlock_gui(self):
        self.context.gui_locked = False
        if hasattr(self.context, "lockable_buttons"):
            for btn in self.context.lockable_buttons:
                try:
                    btn.config(state=tk.NORMAL)
                except Exception:
                    pass

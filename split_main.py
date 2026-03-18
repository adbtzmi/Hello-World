import os
import re

def extract_tabs():
    with open('main_backup.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    os.makedirs('gui/tabs', exist_ok=True)
    os.makedirs('gui/dialogs', exist_ok=True)
    
    # We will identify the start of each tab creation method
    tabs_to_extract = [
        ('create_home_tab', 'home_tab.py', 'HomeTab'),
        ('create_fetch_issue_tab', 'fetch_issue_tab.py', 'FetchIssueTab'),
        ('create_analyze_jira_tab', 'analyze_jira_tab.py', 'AnalyzeJiraTab'),
        ('create_repo_tab', 'repo_tab.py', 'RepoTab'),
        ('create_impact_tab', 'impact_tab.py', 'ImpactTab'),
        ('create_test_tab', 'test_tab.py', 'TestTab'),
        ('create_risk_tab', 'risk_tab.py', 'RiskTab'),
        ('create_implementation_tab', 'implementation_tab.py', 'ImplementationTab'),
    ]
    
    # Let's extract the method bodies
    for func_name, filename, class_name in tabs_to_extract:
        start_idx = -1
        end_idx = -1
        for i, line in enumerate(lines):
            if line.startswith(f"    def {func_name}(self):") or line.startswith(f"    def {func_name}(self,"):
                start_idx = i
                break
                
        if start_idx != -1:
            # find end_idx
            for i in range(start_idx + 1, len(lines)):
                if lines[i].startswith("    def ") and not lines[i].startswith("        def "):
                    end_idx = i
                    break
            if end_idx == -1:
                end_idx = len(lines)
                
            method_lines = lines[start_idx:end_idx]
            
            # create the file
            filepath = os.path.join('gui', 'tabs', filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("import tkinter as tk\n")
                f.write("from tkinter import ttk, messagebox, scrolledtext, simpledialog\n")
                f.write("from gui.tabs.base_tab import BaseTab\n\n")
                f.write(f"class {class_name}(BaseTab):\n")
                f.write(f"    def __init__(self, notebook, context):\n")
                # Need to determine the title based on the first line inside the original method that adds it to notebook
                title = class_name.replace("Tab", " Tab")
                for ml in method_lines:
                    if ".add(" in ml and "text=" in ml:
                        m = re.search(r'text="(.*?)"', ml)
                        if m:
                            title = m.group(1)
                            break
                f.write(f"        super().__init__(notebook, context, \"{title}\")\n")
                f.write(f"        self.build_ui()\n\n")
                f.write(f"    def build_ui(self):\n")
                
                # Copy the rest of the lines, but replacing `tab = ttk.Frame...` with `tab = self`
                # and `self.` with `self.context.` where appropriate
                for ml in method_lines[2:]:
                    # basic indent adjustment
                    adjusted = ml[4:] 
                    f.write(adjusted)
                    
            print(f"Extracted {class_name} to {filepath}")

if __name__ == "__main__":
    extract_tabs()

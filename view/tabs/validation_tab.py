import tkinter as tk
from tkinter import ttk, scrolledtext
from view.tabs.base_tab import BaseTab


class ValidationTab(BaseTab):
    """
    Validation & Risk Tab (View) — Phase 3B
    ========================================
    Generates validation document and risk assessment.
    
    Layout:
      1. JIRA Issue Key input (auto-populated from Home tab)
      2. Info label
      3. Generate button
      4. Results display (ScrolledText)
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📋 Validation & Risk")
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        # ── Title ──────────────────────────────────────────────────────────
        ttk.Label(self, text="Generate Validation Document & Risk Assessment",
                  font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=3, pady=10)

        # ── JIRA Issue Key ─────────────────────────────────────────────────
        ttk.Label(self, text="JIRA Issue Key:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        
        # Get JIRA project prefix from config
        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')
        self.context.set_var('validation_issue_var', tk.StringVar(value=f"{jira_project}-"))
        
        ttk.Entry(self, textvariable=self.context.get_var('validation_issue_var'),
                  width=50).grid(row=1, column=1, pady=5, sticky=(tk.W, tk.E))
        
        ttk.Label(self, text="(Auto-populated from Home tab)",
                  font=('Arial', 8), foreground='gray').grid(
            row=1, column=2, sticky=tk.W, padx=5)

        # ── Info Label ─────────────────────────────────────────────────────
        ttk.Label(self, text="Risk assessment will be generated from workflow data",
                  font=('Arial', 9), foreground='gray').grid(
            row=2, column=0, columnspan=3, pady=5)

        # ── Generate Button ────────────────────────────────────────────────
        self.generate_btn = ttk.Button(
            self, text="Generate Validation & Risk Assessment",
            command=self._generate_validation)
        self.generate_btn.grid(row=3, column=0, columnspan=3, pady=10)

        # ── Results Display ────────────────────────────────────────────────
        result_frame = ttk.LabelFrame(self, text="Validation Document & Risk Assessment",
                                       padding="10")
        result_frame.grid(row=4, column=0, columnspan=3,
                         sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)

        self.result_text = scrolledtext.ScrolledText(
            result_frame, height=22, width=70, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True)

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _generate_validation(self):
        """Generate validation document and risk assessment"""
        issue_key = self.context.get_var('validation_issue_var').get().strip().upper()
        
        # Validate issue key
        jira_project = self.context.config.get('jira', {}).get('project_key', 'TSESSD')
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Input Error", "Please enter JIRA issue key")
            return
        
        # Disable button during generation
        self.generate_btn.configure(state=tk.DISABLED)
        
        self.log(f"\n[Generating Validation Document for {issue_key}]")
        
        # Call controller
        if not self.context.controller.validation_controller:
            self.show_error("Error", "Validation controller not initialized")
            self.generate_btn.configure(state=tk.NORMAL)
            return
        
        self.context.controller.validation_controller.generate_validation(
            issue_key, self._on_validation_generated)

    def _on_validation_generated(self, result):
        """Callback when validation generation completes"""
        # Re-enable button
        self.generate_btn.configure(state=tk.NORMAL)
        
        if not result.get('success'):
            error = result.get('error', 'Unknown error')
            self.show_error("Generation Failed", error)
            self.log(f"✗ Validation generation failed: {error}")
            return
        
        # Get results
        risk_assessment = result.get('risk_assessment', '')
        template_file = result.get('template_file', '')
        
        # Open interactive chat for review
        self.log(f"✓ Risk assessment generated - opening interactive chat...")
        
        # Get issue key
        issue_key = self.context.get_var('validation_issue_var').get().strip().upper()
        
        # Open chat via chat controller
        if self.context.controller.chat_controller:
            self.context.controller.chat_controller.open_interactive_chat(
                issue_key=issue_key,
                step_name="Validation & Risk Assessment",
                initial_content=risk_assessment,
                finalize_callback=lambda: self._finalize_assessment(issue_key, risk_assessment)
            )
        else:
            # Fallback: just display results
            self._display_results(risk_assessment, template_file)

    def _finalize_assessment(self, issue_key, risk_assessment_text):
        """Finalize assessment after user approval"""
        if self.context.controller.validation_controller:
            self.context.controller.validation_controller.finalize_assessment(
                issue_key, risk_assessment_text)
        
        # Display results
        template_file = f"{issue_key}_validation.docx"
        self._display_results(risk_assessment_text, template_file)

    def _display_results(self, risk_assessment, template_file):
        """Display results in the text widget"""
        # Build display text
        display_text = "Validation document generated successfully!\n\n"
        
        if template_file:
            display_text += f"Output file: {template_file}\n\n"
            display_text += "Document populated with:\n"
            display_text += "- JIRA Analysis\n"
            display_text += "- Impact Analysis\n"
            display_text += "- Test Scenarios\n"
            display_text += "- Risk Assessment\n"
            display_text += "- Repository Information\n\n"
        
        display_text += "="*60 + "\n"
        display_text += "RISK ASSESSMENT\n"
        display_text += "="*60 + "\n\n"
        display_text += risk_assessment
        
        # Update text widget
        self.result_text.delete('1.0', tk.END)
        self.result_text.insert('1.0', display_text)
        
        # Show success message
        if template_file:
            self.show_info("Success",
                          f"Validation document generated!\n\n"
                          f"Saved as: {template_file}")
        
        self.log(f"✓ Validation & Risk Assessment complete")

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_validation_completed(self, result: dict):
        """Called by controller when validation completes"""
        issue_key = result.get("issue_key", "")
        self.log(f"✓ Validation complete: {issue_key}")

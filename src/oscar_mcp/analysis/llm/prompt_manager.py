"""
Jinja2-based prompt template system for LLM analysis.

Provides version-controlled, file-based prompt templates with:
- Template inheritance and composition
- Secure template rendering with sandboxing
- Convenient helper methods for common analysis tasks
- Automatic inclusion of medical knowledge from Python constants
"""

from pathlib import Path
from typing import Any

from jinja2 import FileSystemLoader, TemplateNotFound, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from oscar_mcp.knowledge import patterns, thresholds


class PromptManager:
    """Manages Jinja2 prompt templates for medical analysis."""

    def __init__(self, templates_dir: Path = None):
        """
        Initialize prompt manager.

        Args:
            templates_dir: Path to templates directory.
                          Defaults to prompts/ in this package.
        """
        if templates_dir is None:
            templates_dir = Path(__file__).parent / "prompts"

        self.templates_dir = Path(templates_dir)

        # Sandboxed environment prevents code execution in templates
        # (defense-in-depth, in case templates are ever loaded from external sources)
        self.env = SandboxedEnvironment(
            loader=FileSystemLoader(self.templates_dir),
            autoescape=select_autoescape(
                enabled_extensions=("html", "xml", "jinja2"),
                default_for_string=True,
            ),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render_prompt(self, template_name: str, **context: Any) -> str:
        """
        Render a prompt template with context variables.

        Args:
            template_name: Template filename (e.g., "flow_limitation/analysis.jinja2")
            **context: Template variables to render

        Returns:
            Rendered prompt string

        Raises:
            TemplateNotFound: If the template file doesn't exist
            ValueError: If template rendering fails

        Example:
            >>> pm = PromptManager()
            >>> prompt = pm.render_prompt(
            ...     "flow_limitation/analysis.jinja2",
            ...     breath_descriptions=[...],
            ...     reference_patterns={...}
            ... )
        """
        try:
            template = self.env.get_template(template_name)
            return template.render(**context)
        except TemplateNotFound as e:
            raise TemplateNotFound(
                f"Template '{template_name}' not found in {self.templates_dir}"
            ) from e

    def render_flow_limitation_analysis(self, breath_descriptions: list) -> str:
        """
        Convenience method for flow limitation analysis.

        Automatically includes flow limitation class definitions from
        oscar_mcp.knowledge.patterns.FLOW_LIMITATION_CLASSES.

        Args:
            breath_descriptions: List of breath description dicts with visual
                               descriptions, metrics, and timestamps

        Returns:
            Rendered analysis prompt
        """
        return self.render_prompt(
            "flow_limitation/analysis.jinja2",
            breath_descriptions=breath_descriptions,
            flow_classes=patterns.FLOW_LIMITATION_CLASSES,
            thresholds=thresholds.AHI_SEVERITY,
        )

    def render_event_detection(self, breathing_data: list) -> str:
        """
        Convenience method for respiratory event detection.

        Automatically includes event definitions and thresholds.

        Args:
            breathing_data: List of breath/flow data for analysis

        Returns:
            Rendered event detection prompt
        """
        return self.render_prompt(
            "events/detection.jinja2",
            breathing_data=breathing_data,
            event_types=patterns.RESPIRATORY_EVENTS,
            spo2_ranges=thresholds.SPO2_RANGES,
        )

    def render_pattern_detection(self, session_data: dict, pattern_type: str = "csr") -> str:
        """
        Convenience method for complex pattern detection (CSR, periodic breathing, etc.).

        Args:
            session_data: Session waveform and metrics data
            pattern_type: Type of pattern to detect

        Returns:
            Rendered pattern detection prompt
        """
        return self.render_prompt(
            f"patterns/{pattern_type}_detection.jinja2",
            session_data=session_data,
            complex_patterns=patterns.COMPLEX_PATTERNS,
            relationships=patterns.PATTERN_RELATIONSHIPS,
        )

    def get_medical_knowledge_context(self) -> dict:
        """
        Get all medical knowledge as a dictionary for template context.

        Returns:
            Dictionary with all medical knowledge constants
        """
        return {
            "flow_classes": patterns.FLOW_LIMITATION_CLASSES,
            "respiratory_events": patterns.RESPIRATORY_EVENTS,
            "complex_patterns": patterns.COMPLEX_PATTERNS,
            "pattern_relationships": patterns.PATTERN_RELATIONSHIPS,
            "ahi_severity": thresholds.AHI_SEVERITY,
            "spo2_ranges": thresholds.SPO2_RANGES,
            "leak_thresholds": thresholds.LEAK_THRESHOLDS,
            "compliance_criteria": thresholds.COMPLIANCE_CRITERIA,
            "pressure_ranges": thresholds.PRESSURE_RANGES,
            "respiratory_rate": thresholds.RESPIRATORY_RATE,
        }

    def list_templates(self) -> list[str]:
        """
        List all available templates.

        Returns:
            List of template paths relative to templates_dir
        """
        templates = []
        for template_file in self.templates_dir.rglob("*.jinja2"):
            relative_path = template_file.relative_to(self.templates_dir)
            templates.append(str(relative_path))
        return sorted(templates)

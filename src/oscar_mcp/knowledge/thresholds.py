"""
Clinical Thresholds and Severity Classifications

AHI severity, SpO2 ranges, leak thresholds, and compliance criteria
from AASM guidelines and clinical practice standards.
"""

# AHI (Apnea-Hypopnea Index) Severity Classification
# Events per hour of sleep
AHI_SEVERITY = {
    "normal": {
        "range": (0, 5),
        "description": "Normal (AHI < 5)",
        "clinical_action": "No OSA diagnosis",
    },
    "mild": {
        "range": (5, 15),
        "description": "Mild OSA (AHI 5-15)",
        "clinical_action": "Consider CPAP therapy, lifestyle modifications",
    },
    "moderate": {
        "range": (15, 30),
        "description": "Moderate OSA (AHI 15-30)",
        "clinical_action": "CPAP therapy recommended",
    },
    "severe": {
        "range": (30, float("inf")),
        "description": "Severe OSA (AHI ≥ 30)",
        "clinical_action": "CPAP therapy strongly indicated, consider BiPAP",
    },
}


# SpO2 (Oxygen Saturation) Ranges
# Percentage values
SPO2_RANGES = {
    "normal": {
        "range": (95, 100),
        "description": "Normal oxygen saturation (≥95%)",
        "clinical_significance": "Adequate oxygenation",
    },
    "mild_hypoxemia": {
        "range": (90, 95),
        "description": "Mild hypoxemia (90-94%)",
        "clinical_significance": "Mild oxygen desaturation, monitor trends",
    },
    "moderate_hypoxemia": {
        "range": (85, 90),
        "description": "Moderate hypoxemia (85-89%)",
        "clinical_significance": "Concerning oxygen desaturation, therapy adjustment needed",
    },
    "severe_hypoxemia": {
        "range": (0, 85),
        "description": "Severe hypoxemia (<85%)",
        "clinical_significance": "Dangerous oxygen levels, urgent intervention needed",
    },
}

# Key SpO2 Thresholds
SPO2_CRITICAL_THRESHOLD = 88  # Below this is critical
SPO2_NORMAL_THRESHOLD = 95  # Above this is normal


# Leak Rate Thresholds
# Liters per minute (L/min)
LEAK_THRESHOLDS = {
    "acceptable": {
        "range": (0, 24),
        "description": "Acceptable leak (<24 L/min)",
        "clinical_significance": "Normal intentional leak from mask",
    },
    "large": {
        "range": (24, 30),
        "description": "Large leak (24-30 L/min)",
        "clinical_significance": "Unintentional leak, may affect therapy efficacy",
    },
    "excessive": {
        "range": (30, float("inf")),
        "description": "Excessive leak (>30 L/min)",
        "clinical_significance": "Significant leak, therapy likely ineffective, mask adjustment needed",
    },
}


# CPAP Compliance Criteria
# Medicare and clinical guidelines
COMPLIANCE_CRITERIA = {
    "medicare_hours_per_night": {
        "threshold": 4.0,
        "description": "Minimum 4 hours of usage per night",
        "requirement": "Must meet for compliance",
    },
    "medicare_days_per_month": {
        "threshold": 21,
        "percentage": 70,
        "description": "At least 21 days per month (70% of days)",
        "requirement": "Must meet for compliance",
    },
    "optimal_hours": {
        "threshold": 7.0,
        "description": "Optimal usage ≥7 hours per night",
        "requirement": "Recommended for best outcomes",
    },
    "minimum_effective": {
        "threshold": 6.0,
        "description": "Minimum for clinical effectiveness",
        "requirement": "Target for symptom improvement",
    },
}


# Pressure Thresholds
# cm H2O
PRESSURE_RANGES = {
    "cpap_typical": {
        "min": 4,
        "max": 20,
        "description": "Typical CPAP pressure range",
    },
    "cpap_common": {
        "min": 6,
        "max": 14,
        "description": "Most common therapeutic pressure range",
    },
    "low_pressure": {
        "threshold": 6,
        "description": "Below 6 cm H2O considered low",
        "clinical_significance": "May be insufficient for moderate-severe OSA",
    },
    "high_pressure": {
        "threshold": 15,
        "description": "Above 15 cm H2O considered high",
        "clinical_significance": "May indicate severe obstruction or positional issues",
    },
}


# Respiratory Rate Ranges
# Breaths per minute
RESPIRATORY_RATE = {
    "normal_adult_awake": {
        "min": 12,
        "max": 20,
        "description": "Normal adult respiratory rate while awake",
    },
    "normal_adult_sleep": {
        "min": 12,
        "max": 25,
        "description": "Normal adult respiratory rate during sleep",
    },
    "bradypnea": {
        "threshold": 10,
        "description": "Abnormally slow breathing (<10 breaths/min)",
        "clinical_significance": "May indicate central respiratory depression",
    },
    "tachypnea": {
        "threshold": 25,
        "description": "Abnormally fast breathing (>25 breaths/min)",
        "clinical_significance": "May indicate respiratory distress or anxiety",
    },
}


# Helper function for AHI classification
def classify_ahi(ahi_value: float) -> dict:
    """
    Classify AHI value into severity category.

    Args:
        ahi_value: AHI events per hour

    Returns:
        Dictionary with severity level and details
    """
    for severity, data in AHI_SEVERITY.items():
        min_val, max_val = data["range"]
        if min_val <= ahi_value < max_val:
            return {"severity": severity, "value": ahi_value, **data}
    return {
        "severity": "unknown",
        "value": ahi_value,
        "description": f"AHI {ahi_value} - classification error",
    }


# Helper function for SpO2 classification
def classify_spo2(spo2_value: float) -> dict:
    """
    Classify SpO2 value into range category.

    Args:
        spo2_value: SpO2 percentage (0-100)

    Returns:
        Dictionary with range category and details
    """
    for category, data in SPO2_RANGES.items():
        min_val, max_val = data["range"]
        if min_val <= spo2_value <= max_val:
            return {"category": category, "value": spo2_value, **data}
    return {
        "category": "unknown",
        "value": spo2_value,
        "description": f"SpO2 {spo2_value}% - classification error",
    }

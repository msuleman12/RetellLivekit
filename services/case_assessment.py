"""The "3 I's" (liability, insurance, injuries) and red flags for the priority
PDF and email, ported from the old ai-receptionist build.

Each dimension starts at 5 and moves with what the caller said: 7+ is Strong,
4-6 Partial, below 4 Weak. A field the caller was never asked about is listed
as missing rather than counted against the case, and a dimension with nothing
known at all is Unknown.
"""

from dataclasses import dataclass
from typing import Any

_SEVERE_TERMS = ("severe", "brain", "fracture", "broken", "critical", "skull", "spine", "spinal", "surgery", "paraly")
_NEGATIVE_TEXT = ("no ", "none", "nobody", "no one", "not ")


@dataclass(frozen=True)
class Rule:
    field: str
    strength: str
    concern: str = ""
    missing: str = ""
    yes: int = 2
    no: int = 0
    # Free-text field: any answer counts as a yes, and the answer is shown.
    text: bool = False
    # Injury text: severe wording adds this on top of `yes`.
    severity_bonus: int = 0


R = Rule

RULES: dict[str, dict[str, tuple[Rule, ...]]] = {
    "accident": {
        "liability": (
            R("police_report", "Police report filed", "No police report filed", "Whether a police report exists", yes=2, no=-1),
            R("liability_status", "Fault / contact", missing="Fault and contact from the other side", yes=1, text=True),
            R("witnesses", "Witnesses to the accident", "No witnesses", "Witnesses", yes=1),
            R("accident_description", "Incident described", missing="Description of the accident", yes=1, text=True),
        ),
        "insurance": (
            R("other_party_insured", "Other party is insured", "Other party is uninsured", "Other party's insurance", yes=3, no=-2),
            R("insurance_claim_opened", "Insurance claim already opened", "No insurance claim opened", "Insurance claim status", yes=2),
            R("um_coverage", "Caller has UM/UIM coverage", "No UM/UIM coverage", "UM/UIM coverage", yes=1),
        ),
        "injuries": (
            R("accident_injuries", "Injuries", "No injuries reported", "Injury details", yes=2, no=-2, text=True, severity_bonus=2),
            R("accident_treatment", "Medical treatment received", "No medical treatment", "Medical treatment", yes=2, no=-1),
            R("accident_missed_work", "Missed work / lost income", missing="Lost work", yes=1),
        ),
    },
    "premises": {
        "liability": (
            R("premises_incident_reported", "Reported to property staff", "Not reported to the property", "Whether it was reported", yes=2, no=-1),
            R("premises_owner_responsibility", "Caller believes the owner is at fault", "Owner fault unclear", "Owner responsibility", yes=3, no=-1),
            R("premises_hazard_condition", "Hazard", missing="Hazard condition", yes=1, text=True),
            R("premises_witnesses", "Witnesses", "No witnesses", "Witnesses", yes=1, text=True),
            R("premises_photos_available", "Photos taken", "No photos", "Photos", yes=1),
        ),
        "insurance": (
            R("premises_property_owner_insurance", "Property owner is insured", "Owner insurance unknown", "Property owner's insurance", yes=4, no=-2),
            R("premises_caller_insurance", "Caller's insurance", missing="Caller's own insurance", yes=1, text=True),
        ),
        "injuries": (
            R("premises_physical_injuries", "Injuries", "No injuries reported", "Injury details", yes=2, no=-2, text=True, severity_bonus=2),
            R("premises_medical_treatment", "Medical treatment received", "No medical treatment", "Medical treatment", yes=2, no=-1),
            R("premises_missed_work", "Missed work", missing="Lost work", yes=1),
        ),
    },
    "employment": {
        "liability": (
            R("employment_reported_internally", "Reported internally", "Not reported internally", "Whether it was reported", yes=2, no=-1),
            R("employment_documentation_available", "Documentation available", "No documentation", "Supporting documentation", yes=2, no=-1),
            R("employment_issue_description", "Issue", missing="Description of the issue", yes=1, text=True),
            R("employment_retaliation", "Retaliation after complaining", yes=1),
        ),
        "insurance": (
            R("other_party_name", "Employer", missing="Employer's name", yes=1, text=True),
            R("employer_size", "Employer size", missing="Employer size", yes=1, text=True),
        ),
        "injuries": (
            R("employment_effects_financial", "Financial harm", missing="Financial impact", yes=3, text=True),
            R("employment_effects_emotional", "Emotional harm", missing="Emotional impact", yes=2, text=True),
        ),
    },
    "harassment": {
        "liability": (
            R("sh_reported_to_hr", "Reported to HR or a supervisor", "Not reported to HR", "Whether it was reported", yes=3, no=-2),
            R("sh_filed_with_agency", "Agency complaint filed", missing="Agency complaint", yes=2),
            R("sh_evidence_details", "Evidence", "No evidence", "Evidence", yes=2, no=-1, text=True),
            R("sh_nature_of_incidents", "Nature of incidents", missing="Nature of the incidents", yes=1, text=True),
            R("sh_witnesses", "Witnesses", "No witnesses", "Witnesses", yes=1, text=True),
        ),
        "insurance": (
            R("other_party_name", "Employer / other party", missing="Employer or other party", yes=1, text=True),
        ),
        "injuries": (
            R("sh_missed_work", "Missed work", missing="Lost work", yes=3),
            R("sh_lost_wages", "Lost wages", missing="Lost wages", yes=2, text=True),
            R("sh_career_impact", "Career impact", missing="Career impact", yes=2, text=True),
            R("sh_retaliation", "Retaliation after reporting", yes=1),
        ),
    },
    "malpractice": {
        "liability": (
            R("mm_complaint_filed", "Complaint filed", "No complaint filed", "Whether a complaint was filed", yes=3, no=-1),
            R("mm_records_available", "Medical records available", "Medical records not available", "Medical records", yes=2, no=-2),
            R("mm_incident_description", "Incident", missing="Description of what happened", yes=2, text=True),
            R("mm_witnesses", "Witnesses", "No witnesses", "Witnesses", yes=1, text=True),
        ),
        "insurance": (
            R("other_party_name", "Provider", missing="Doctor or facility name", yes=1, text=True),
        ),
        "injuries": (
            R("mm_injuries", "Injuries", "No injuries reported", "Injury details", yes=3, no=-2, text=True, severity_bonus=2),
            R("mm_additional_treatment", "Additional treatment needed", "No additional treatment", "Additional treatment", yes=2, no=-1),
            R("mm_additional_hospitalization", "Additional hospitalization", missing="Hospitalization", yes=2),
        ),
    },
}

# Said "no" to one of these: flagged for the attorney.
RED_FLAG_FIELDS: dict[str, dict[str, str]] = {
    "accident": {
        "police_report": "No police report filed",
        "accident_treatment": "No medical treatment",
        "other_party_insured": "Other party is uninsured",
    },
    "premises": {
        "premises_incident_reported": "Incident not reported to the property",
        "premises_medical_treatment": "No medical treatment",
        "premises_property_owner_insurance": "Property owner insurance unknown",
    },
    "employment": {
        "employment_reported_internally": "Issue not reported internally",
        "employment_documentation_available": "No documentation available",
    },
    "harassment": {"sh_reported_to_hr": "Not reported to HR"},
    "malpractice": {
        "mm_complaint_filed": "No formal complaint filed",
        "mm_records_available": "Medical records not available",
    },
}


def _answer(value: Any, rule: Rule) -> bool | None:
    """True, False, or None when the caller was never asked."""
    if value is None or value == "":
        return None
    if rule.text:
        return not str(value).strip().lower().startswith(_NEGATIVE_TEXT)
    return bool(value)


def _dimension(rules: tuple[Rule, ...], custom: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "Unknown", "strengths": [], "concerns": [], "missing": []}
    score, known = 5, False
    for rule in rules:
        value = custom.get(rule.field)
        answer = _answer(value, rule)
        if answer is None:
            if rule.missing:
                out["missing"].append(rule.missing)
            continue
        known = True
        if answer:
            score += rule.yes
            out["strengths"].append(f"{rule.strength}: {value}" if rule.text else rule.strength)
            if rule.severity_bonus and any(t in str(value).lower() for t in _SEVERE_TERMS):
                score += rule.severity_bonus
                out["strengths"].append("Severe injuries described")
        else:
            score += rule.no
            if rule.concern:
                out["concerns"].append(rule.concern)
    if known:
        out["status"] = "Strong" if score >= 7 else "Partial" if score >= 4 else "Weak"
    return out


def assess(case_type: str, custom: dict[str, Any]) -> dict[str, Any] | None:
    """{liability, insurance, injuries, red_flags}, or None without a practice area."""
    rules = RULES.get(case_type)
    if not rules:
        return None
    result: dict[str, Any] = {name: _dimension(dim, custom) for name, dim in rules.items()}
    if case_type == "malpractice":
        result["insurance"]["strengths"].append("Medical providers typically carry malpractice insurance")

    red_flags = [text for field, text in RED_FLAG_FIELDS[case_type].items() if custom.get(field) is False]
    if custom.get("has_other_attorney"):
        red_flags.append("Caller already has another attorney for this matter")
    result["red_flags"] = red_flags
    return result

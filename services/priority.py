"""Case priority (HIGH / MEDIUM / LOW) from keywords, ported from the old
ai-receptionist build.

A single HIGH_ABSOLUTE match makes a case HIGH; HIGH_SCORED needs
HIGH_SCORED_THRESHOLD matches. Otherwise the first bucket with a match wins.

Two deliberate changes from the old build: only the caller's words and the
extracted fields are searched, never Claire's (her questions name "witnesses",
"supervisor", "police report" on every call and inflated the old scores), and
keywords match whole words ("fire" no longer matches "fired").
"""

import json
import re
from typing import Any

HIGH_SCORED_THRESHOLD = 2

ACCIDENT = {
    "HIGH_ABSOLUTE": [
        "death", "fatality", "fatal", "died", "killed", "coma", "paralysis", "paralyzed",
        "traumatic brain injury", "TBI", "spinal cord injury", "internal bleeding",
        "lost consciousness", "fled the scene", "drunk driver", "DUI", "DWI",
        "intoxicated driver", "miscarriage", "emergency surgery", "permanent disability",
        "permanent damage",
    ],
    "HIGH_SCORED": [
        "severe injury", "critical", "life-threatening", "brain", "brain injury", "head trauma",
        "spine", "back injury", "multiple injuries", "compound fracture", "hospitalized", "ICU",
        "intensive care", "child injured", "minor injured", "infant", "burn",
        "commercial vehicle", "truck accident", "18-wheeler", "multiple vehicles", "pile-up",
        "chain reaction", "broken bones", "concussion", "surgery",
    ],
    "MEDIUM": [
        "fracture", "hit and run", "hospital admission", "overnight stay", "ambulance",
        "emergency room", "ER visit", "blacked out", "airbag deployed", "total loss vehicle",
        "uninsured driver", "pregnant", "pregnancy complications",
    ],
    "LOW": [
        "minor injuries", "bruises", "soreness", "property damage only", "no injuries",
        "declined medical treatment", "whiplash", "no insurance", "fender bender",
        "minor collision", "anxiety", "stress", "depression",
    ],
}

EMPLOYMENT = {
    "HIGH_ABSOLUTE": [
        "right to sue letter", "right to sue", "sexual assault", "rape", "FMLA violation",
        "wage theft",
    ],
    "HIGH_SCORED": ["family leave denied", "unpaid wages", "overtime not paid", "sexual harassment"],
    "MEDIUM": [
        "demotion", "pay cut", "hours reduced", "EEOC", "assault", "ADA violation",
        "denied promotion", "passed over", "maternity leave denied", "constructive discharge",
        "forced to quit", "medical leave issues", "reasonable accommodation denied",
        "unpaid overtime", "misclassified", "breach of contract", "terminated",
        "wrongful termination", "fired illegally", "discrimination", "discriminated",
        "protected class", "race", "safety violations", "OSHA", "unsafe conditions",
        "whistleblower", "reported illegal activity", "class action potential",
        "multiple employees affected", "documented evidence", "written proof", "emails",
    ],
    "LOW": [
        "general workplace complaint", "personality conflict", "no documentation",
        "incident over 2 years ago", "harassment", "hostile work environment", "retaliation",
        "retaliated against", "pregnancy discrimination",
    ],
}

MALPRACTICE = {
    "HIGH_ABSOLUTE": ["death", "died", "fatal", "wrongful death", "gross negligence"],
    "HIGH_SCORED": [
        "surgical error", "wrong site surgery", "wrong patient", "birth injury", "infant death",
        "maternal death", "brain damage", "stroke", "heart attack", "amputation", "loss of limb",
        "organ damage", "organ failure", "paralysis", "loss of function", "anesthesia error",
        "awareness during surgery", "sepsis", "MRSA", "misdiagnosis", "delayed diagnosis",
        "cancer", "medication error", "wrong medication", "overdose", "permanent injury",
        "permanent disability",
    ],
    "MEDIUM": [
        "delayed treatment", "treatment delay", "failed procedure", "complications",
        "hospital readmission", "bedsores", "pressure ulcers", "fall in hospital",
        "patient fall", "lack of informed consent", "unnecessary surgery",
        "emergency room error", "ER negligence", "nursing home abuse", "neglect", "infection",
    ],
    "LOW": ["minor complication", "cosmetic concern", "no lasting injury", "incident over 2 years ago"],
}

PREMISES = {
    "HIGH_ABSOLUTE": [
        "brain injury", "skull fracture", "spinal injury", "permanent disability", "drowning",
        "negligent security", "fire", "explosion", "toxic exposure", "child injured",
        "minor injured",
    ],
    "HIGH_SCORED": [
        "business property", "severe injury", "serious injury", "hospitalized", "head injury",
        "back injury", "neck injury", "multiple fractures", "permanent injury", "trip and fall",
        "assault on premises", "attacked", "near drowning", "elevator accident",
        "escalator accident", "burn injury", "chemical exposure", "dog bite", "animal attack",
        "elderly injured", "senior citizen", "swimming pool accident", "gross negligence",
    ],
    "MEDIUM": [
        "fall", "fell", "slipped", "tripped", "wet floor", "no warning sign", "uneven surface",
        "broken stairs", "inadequate lighting", "poor lighting", "parking lot accident",
        "shopping cart accident", "broken glass", "sharp object", "required stitches",
        "emergency room visit",
    ],
    "LOW": [
        "minor bruising", "minor scrape", "no medical treatment", "trespassing",
        "no permission to be there", "obvious hazard",
    ],
}

HARASSMENT = {
    "HIGH_ABSOLUTE": [
        "rape", "sexual assault", "assaulted", "physical contact", "touched", "groped",
        "grabbed", "quid pro quo", "threatened termination", "minor involved", "underage",
        "EEOC filing",
    ],
    "HIGH_SCORED": [
        "supervisor", "manager", "boss involved", "multiple incidents", "ongoing", "pattern",
        "retaliation", "fired after reporting", "demoted", "reported to HR", "HR ignored",
        "no action taken", "witnesses", "evidence", "documentation", "agency complaint filed",
        "multiple victims", "other employees affected",
    ],
    "MEDIUM": [
        "unwanted advances", "inappropriate comments", "sexual jokes", "sexual remarks",
        "inappropriate messages", "texts", "emails", "stalking behavior", "following",
        "gender discrimination", "pregnancy harassment", "creating hostile environment",
    ],
    "LOW": ["single minor comment", "no documentation", "incident over 1 year ago", "no reporting to employer"],
}

KEYWORDS_BY_CASE_TYPE: dict[str, dict[str, list[str]]] = {
    "accident": ACCIDENT,
    "employment": EMPLOYMENT,
    "premises": PREMISES,
    "harassment": HARASSMENT,
    "malpractice": MALPRACTICE,
}


def _matches(text: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if re.search(rf"(?<!\w){re.escape(k.lower())}(?!\w)", text)]


def calculate(case_type: str, custom: dict[str, Any], caller_text: str) -> dict[str, Any]:
    """{priority_level, matched_keywords, reasoning}. UNKNOWN when there is no practice area."""
    buckets = KEYWORDS_BY_CASE_TYPE.get(case_type)
    if not buckets:
        return {"priority_level": "UNKNOWN", "matched_keywords": [], "reasoning": "No practice area was identified."}

    text = f"{caller_text} {json.dumps(custom, ensure_ascii=False)}".lower()
    absolute = _matches(text, buckets["HIGH_ABSOLUTE"])
    scored = _matches(text, buckets["HIGH_SCORED"])
    medium = _matches(text, buckets["MEDIUM"])
    low = _matches(text, buckets["LOW"])

    if absolute:
        return {
            "priority_level": "HIGH",
            "matched_keywords": absolute,
            "reasoning": f"HIGH: critical keyword(s) mentioned ({', '.join(absolute)}).",
        }
    if len(scored) >= HIGH_SCORED_THRESHOLD:
        return {
            "priority_level": "HIGH",
            "matched_keywords": scored,
            "reasoning": f"HIGH: {len(scored)} high-severity keywords mentioned ({', '.join(scored)}).",
        }
    if medium:
        return {
            "priority_level": "MEDIUM",
            "matched_keywords": medium + scored,
            "reasoning": f"MEDIUM: {len(medium)} medium-priority keyword(s) mentioned.",
        }
    return {
        "priority_level": "LOW",
        "matched_keywords": low + scored,
        "reasoning": "LOW: " + (f"{len(low)} low-priority keyword(s) mentioned." if low else "no priority keywords mentioned."),
    }

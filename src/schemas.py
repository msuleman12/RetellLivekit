"""Post-call analysis fields, copied from each Retell agent's
`post_call_analysis_data`.

Retell ran these after the call with `post_call_analysis_model` and delivered
them on the `call_analyzed` webhook.  `src/postcall.py` reproduces that: it
turns these definitions into a JSON schema, asks the model to fill it from the
transcript, and POSTs the result in the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class AnalysisField:
    name: str
    description: str
    type: Literal["string", "boolean", "number", "enum"] = "string"
    required: bool = False
    examples: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    conditional_prompt: str = ""

    def prompt_line(self) -> str:
        bits = [f"- {self.name}: {self.description}"]
        if self.choices:
            bits.append(f"  Allowed values: {', '.join(self.choices)}.")
        if self.examples:
            bits.append(f"  Examples: {'; '.join(self.examples)}.")
        if self.conditional_prompt:
            bits.append(f"  {self.conditional_prompt}")
        if self.required:
            bits.append("  This field is REQUIRED.")
        return "\n".join(bits)


F = AnalysisField

# ---------------------------------------------------------------------------
# Accident / general intake  (Retell agent_c6413efd5341ccd76adff20485)
# ---------------------------------------------------------------------------
ACCIDENT_FIELDS: tuple[AnalysisField, ...] = (
    F("user_fname", "The caller's first name, exactly as they gave or spelled it. Leave empty if you are not confident what was said.", required=True, examples=("Ahmed", "Sarah")),
    F("user_lname", "The caller's last name or surname, exactly as they gave or spelled it. Leave empty if you are not confident what was said.", required=True, examples=("Khan", "Nguyen")),
    F("user_phone", "The caller's callback phone number, digits only. Must be a valid 10-digit US number. If the caller gave fewer than 10 digits, leave this empty rather than guessing.", required=True, examples=("5551234567",)),
    F("other_party_name", "The name of the other party the caller is in dispute with - a person or a business. Used for the firm's conflict check. Record the OTHER side, never the caller's own name.", required=True, examples=("Bilal Ahmad", "State Farm", "ABC Trucking LLC")),
    F("user_email", "The caller's email address, if they gave one.", examples=("ahmed.khan@gmail.com",), conditional_prompt="Only populate if the caller actually gave an email address."),
    F("preferred_contact", "How the caller said they prefer to be contacted back.", type="enum", choices=("phone", "email", "text"), conditional_prompt="Only populate if the caller expressed a preference."),
    F("best_contact_time", "When the caller said is the best time to reach them.", examples=("Weekday afternoons", "After 5pm"), conditional_prompt="Only populate if the caller mentioned a preferred time."),
    F("referral_source", "How the caller heard about the firm - a person, a website, an ad, a previous client.", examples=("Google search", "Referred by her cousin", "Saw a billboard on I-35"), conditional_prompt="Only populate if the caller said how they found the firm."),
    F("client_goal", "What the caller said they are hoping to achieve. Record it in their own terms, not as a legal assessment.", examples=("Wants medical bills covered and time off work paid", "Just wants the harassment to stop", "Wants to know if the contract is enforceable"), conditional_prompt="Only populate if the caller expressed what they want out of this."),
    F("accident_date", "The date the incident happened, in YYYY-MM-DD format if determinable. If the caller was vague, record what they said.", examples=("2026-07-15", "about three weeks ago"), conditional_prompt="Only populate if the matter involves an incident with a date."),
    F("accident_location", "Where the incident happened - road, intersection, city, or place.", examples=("I-95 near Exit 12", "Preston Road, Dallas"), conditional_prompt="Only populate if the matter involves an incident with a location."),
    F("accident_description", "A short factual description of what happened, in one or two sentences. Do not editorialise or assess the strength of the case.", examples=("Rear-ended at a red light", "Slipped on an unmarked wet floor in a supermarket"), conditional_prompt="Only populate if the caller described an incident or dispute."),
    F("accident_injuries", "Any injuries or physical symptoms the caller mentioned, and whether they are ongoing.", examples=("Neck and back pain, still ongoing", "Broken wrist, healing"), conditional_prompt="Only populate if the caller mentioned an injury or physical symptoms."),
    F("accident_treatment", "True if the caller said they received any medical treatment or saw a doctor for this. False if they said they have not.", type="boolean", conditional_prompt="Only populate if medical treatment was discussed."),
    F("life_impact", "How the caller said this has affected their work, finances, or day-to-day life, in their own words.", examples=("Missed a week of work, worried about bills", "Cannot lift her toddler", "Anxious driving since"), conditional_prompt="Only populate if the caller described an impact on their life or work."),
    F("accident_missed_work", "True if the caller said they have missed work or lost income because of the incident.", type="boolean", conditional_prompt="Only populate if work or income was discussed."),
    F("accident_passengers", "True if anyone else was in the vehicle with the caller, or was also involved and affected. False if they were alone.", type="boolean", conditional_prompt="Only populate if this was discussed."),
    F("witnesses", "True if the caller said anyone else saw what happened, false if they said there were no witnesses.", type="boolean", conditional_prompt="Only populate if witnesses were discussed."),
    F("police_report", "True if the caller said a police report was filed, false if they said there was none.", type="boolean", conditional_prompt="Only populate if a police report was discussed."),
    F("other_party_insured", "True if the caller believes the other party is insured, false if they said the other party is uninsured.", type="boolean", conditional_prompt="Only populate if the other party's insurance was discussed."),
    F("insurance_claim_opened", "True if a claim has already been opened with any insurer for this incident.", type="boolean", conditional_prompt="Only populate if an insurance claim was discussed."),
    F("liability_status", "What the caller said about fault and contact from the other side - whether anyone has accepted responsibility, or whether the other party or their insurer has been in touch.", examples=("No one has reached out to the caller", "Their insurer called and accepted fault"), conditional_prompt="Only populate if fault or contact from the other side was discussed."),
    F("has_documents", "True if the caller said they already have paperwork relating to this - a police report, medical records, a contract, photos or messages. Do not record what the documents say, only whether they exist.", type="boolean", conditional_prompt="Only populate if documents or paperwork were discussed."),
    F("written_evidence", "Any contract, agreement, messages or other written material the caller says exists. For dispute and contract matters.", examples=("Signed service agreement from June", "Email thread with the contractor"), conditional_prompt="Only populate for a contract or dispute matter where written material was discussed."),
    F("urgency", "Any deadline or time pressure the caller mentioned.", examples=("Court date in two weeks", "Needs help within a week"), conditional_prompt="Only populate if the caller mentioned a deadline or urgency."),
    F("has_other_attorney", "True if the caller said they are already represented by another attorney or law firm for this matter.", type="boolean", conditional_prompt="Only populate if representation by another attorney was discussed."),
)

# ---------------------------------------------------------------------------
# Employment  (Retell agent_df71d82842c2299cc238be6819)
# ---------------------------------------------------------------------------
EMPLOYMENT_FIELDS: tuple[AnalysisField, ...] = (
    F("user_fname", "The caller's first name, exactly as they gave or spelled it. Leave empty if you are not confident what was said.", required=True, examples=("Ahmed", "Sarah")),
    F("user_lname", "The caller's last name or surname, exactly as they gave or spelled it. Leave empty if you are not confident what was said.", required=True, examples=("Khan", "Nguyen")),
    F("user_phone", "The caller's callback phone number, digits only. Must be a valid 10-digit US number.", required=True, examples=("5551234567",)),
    F("other_party_name", "The employer's name - the business the caller worked for. Used for the firm's conflict check.", required=True, examples=("ABC Manufacturing Inc", "Retail Solutions LLC")),
    F("user_email", "The caller's email address, if they gave one.", conditional_prompt="Only populate if the caller actually gave an email address."),
    F("preferred_contact", "How the caller said they prefer to be contacted back.", type="enum", choices=("phone", "email", "text"), conditional_prompt="Only populate if the caller expressed a preference."),
    F("employment_best_contact_time", "When the caller said is the best time to reach them.", conditional_prompt="Only populate if mentioned."),
    F("employment_position_title", "The caller's job title or role at the employer.", conditional_prompt="Only populate if mentioned."),
    F("employment_current_status", "Whether the caller is still employed, terminated, resigned, or on leave.", type="enum", choices=("Employed", "Terminated", "Resigned", "On leave"), conditional_prompt="Only populate if mentioned."),
    F("employment_start_date", "Roughly when the caller started working there.", conditional_prompt="Only populate if mentioned."),
    F("employment_end_date", "Roughly when employment ended, if it did.", conditional_prompt="Only populate if the employment ended and a date/timeframe was given."),
    F("employment_supervisor_names", "Names of supervisors, HR staff, or coworkers involved in the issue.", conditional_prompt="Only populate if names were given."),
    F("employment_issue_description", "A short factual description of the workplace issue - wrongful termination, discrimination, wage/hour, retaliation, FMLA/leave issue, safety violation.", conditional_prompt="Only populate if the caller described the issue."),
    F("employment_wrongful_termination", "True if the issue involves being fired or let go unfairly.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_harassment_or_discrimination", "True if the issue involves harassment or discrimination based on a protected characteristic.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_wage_hour_violations", "True if the issue involves unpaid wages, overtime, or missed breaks.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_retaliation", "True if the caller said they faced retaliation for reporting or complaining.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_fmla_issues", "True if the issue involves FMLA or medical/family leave.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_reported_internally", "True if the caller reported the issue internally (to HR, a supervisor, etc.).", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_documentation_available", "True if the caller has emails, texts, pay stubs, or other written records.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("employment_effects_financial", "Financial impact the caller described - lost income, unpaid wages, etc.", conditional_prompt="Only populate if discussed."),
    F("employment_effects_emotional", "Emotional impact the caller described.", conditional_prompt="Only populate if discussed."),
    F("client_goal", "What the caller said they are hoping to achieve, in their own words.", conditional_prompt="Only populate if expressed."),
    F("urgency", "Any deadline or time pressure the caller mentioned.", conditional_prompt="Only populate if mentioned."),
    F("has_other_attorney", "True if the caller said they are already represented by another attorney for this matter.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("referral_source", "How the caller heard about the firm.", conditional_prompt="Only populate if mentioned."),
)

# ---------------------------------------------------------------------------
# Premises liability  (Retell agent_75f4b247f8be5eb0f33d723960)
# ---------------------------------------------------------------------------
PREMISES_FIELDS: tuple[AnalysisField, ...] = (
    F("user_fname", "The caller's first name, exactly as they gave or spelled it. Leave empty if you are not confident what was said.", required=True, examples=("Ahmed", "Sarah")),
    F("user_lname", "The caller's last name or surname, exactly as they gave or spelled it.", required=True, examples=("Khan", "Nguyen")),
    F("user_phone", "The caller's callback phone number, digits only. Must be a valid 10-digit US number.", required=True, examples=("5551234567",)),
    F("other_party_name", "The name of the property or business where the incident happened. Used for the firm's conflict check.", required=True, examples=("Kroger on Preston Rd", "Riverside Apartments")),
    F("user_email", "The caller's email address, if they gave one.", conditional_prompt="Only populate if the caller actually gave an email address."),
    F("preferred_contact", "How the caller said they prefer to be contacted back.", type="enum", choices=("phone", "email", "text"), conditional_prompt="Only populate if the caller expressed a preference."),
    F("premises_best_contact_time", "When the caller said is the best time to reach them.", conditional_prompt="Only populate if mentioned."),
    F("premises_incident_date", "The date the incident happened, in YYYY-MM-DD format if determinable.", conditional_prompt="Only populate if mentioned."),
    F("premises_incident_location", "Where exactly on the property the incident happened.", conditional_prompt="Only populate if mentioned."),
    F("premises_incident_description", "A short factual description of what happened.", conditional_prompt="Only populate if described."),
    F("premises_hazard_condition", "The hazard that caused the fall - wet floor, uneven surface, poor lighting, ice, etc.", conditional_prompt="Only populate if mentioned."),
    F("premises_incident_reported", "True if the incident was reported to staff or management at the property.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("premises_witnesses", "Whether anyone witnessed the incident.", conditional_prompt="Only populate if discussed."),
    F("premises_photos_available", "True if photos were taken of the hazard, scene, or injuries.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("premises_physical_injuries", "Injuries the caller sustained.", conditional_prompt="Only populate if mentioned."),
    F("premises_medical_treatment", "True if the caller received medical treatment for this.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("premises_missed_work", "True if the caller missed work because of this.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("premises_financial_hardship", "Financial impact the caller described.", conditional_prompt="Only populate if discussed."),
    F("premises_owner_responsibility", "True if the caller believes the property owner is responsible / at fault.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("premises_property_owner_insurance", "True if the caller knows the property owner has insurance.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("client_goal", "What the caller said they are hoping to achieve, in their own words.", conditional_prompt="Only populate if expressed."),
    F("has_other_attorney", "True if the caller said they are already represented by another attorney for this matter.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("referral_source", "How the caller heard about the firm.", conditional_prompt="Only populate if mentioned."),
)

# ---------------------------------------------------------------------------
# Medical malpractice  (Retell agent_fc28f007dc909ed98663bc8296)
# ---------------------------------------------------------------------------
MALPRACTICE_FIELDS: tuple[AnalysisField, ...] = (
    F("user_fname", "The caller's first name, exactly as they gave or spelled it.", required=True, examples=("Ahmed", "Sarah")),
    F("user_lname", "The caller's last name.", required=True, examples=("Khan", "Nguyen")),
    F("user_phone", "The caller's callback phone number, digits only. Must be a valid 10-digit US number.", required=True, examples=("5551234567",)),
    F("other_party_name", "The name of the doctor, hospital, clinic, or medical facility involved. Used for the firm's conflict check.", required=True, examples=("Dr. James Patel", "St. Luke's Medical Center")),
    F("mm_caller_is_patient", "True if the caller is the patient, false if calling on someone else's behalf.", type="boolean", conditional_prompt="Populate as soon as clear."),
    F("mm_patient_name", "The patient's name, if the caller is not the patient.", conditional_prompt="Only populate if the caller is calling on someone else's behalf."),
    F("mm_patient_relation", "The caller's relationship to the patient.", conditional_prompt="Only populate if the caller is not the patient."),
    F("user_email", "The caller's email address, if given.", conditional_prompt="Only populate if given."),
    F("preferred_contact", "How the caller prefers to be contacted back.", type="enum", choices=("phone", "email", "text"), conditional_prompt="Only populate if expressed."),
    F("mm_incident_date", "Roughly when the incident happened.", conditional_prompt="Only populate if mentioned."),
    F("mm_incident_location", "Where the incident happened - hospital, clinic, private practice, etc.", conditional_prompt="Only populate if mentioned."),
    F("mm_incident_description", "A short factual description of what happened - misdiagnosis, surgical error, medication error, delayed treatment, birth injury, etc.", conditional_prompt="Only populate if described."),
    F("mm_injuries", "Injuries or health consequences the caller described.", conditional_prompt="Only populate if mentioned."),
    F("mm_symptom_onset", "When symptoms or complications first appeared.", conditional_prompt="Only populate if mentioned."),
    F("mm_additional_treatment", "True if additional treatment was needed because of this.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("mm_additional_hospitalization", "True if additional hospitalization was needed because of this.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("mm_complaint_filed", "True if a complaint was filed with the hospital, clinic, or a medical board.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("mm_records_available", "True if medical records or discharge papers are available.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("mm_witnesses", "Whether there were witnesses - nurses, staff, family.", conditional_prompt="Only populate if discussed."),
    F("mm_impact_on_life", "How this has affected the caller's or patient's health, daily life, or work.", conditional_prompt="Only populate if discussed."),
    F("client_goal", "What the caller said they are hoping to achieve, in their own words.", conditional_prompt="Only populate if expressed."),
    F("has_other_attorney", "True if the caller said they are already represented by another attorney for this matter.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("mm_contact_consent", "True if the caller confirmed they are comfortable being contacted about this matter.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("referral_source", "How the caller heard about the firm.", conditional_prompt="Only populate if mentioned."),
)

# ---------------------------------------------------------------------------
# Sexual harassment  (Retell agent_2faa4e69b3d4d679bf06c52579)
# ---------------------------------------------------------------------------
HARASSMENT_FIELDS: tuple[AnalysisField, ...] = (
    F("user_fname", "The caller's first name, exactly as they gave or spelled it. Leave empty if unsure.", required=True, examples=("Ahmed", "Sarah")),
    F("user_lname", "The caller's last name.", required=True, examples=("Khan", "Nguyen")),
    F("user_phone", "The caller's callback phone number, digits only. Must be a valid 10-digit US number.", required=True, examples=("5551234567",)),
    F("sh_caller_is_affected", "True if the caller is the person affected, false if calling on behalf of someone else.", type="boolean", conditional_prompt="Populate as soon as this is clear."),
    F("sh_affected_person_name", "Name of the affected person, if the caller is calling on someone else's behalf.", conditional_prompt="Only populate if the caller is not the affected person."),
    F("sh_affected_person_relation", "The caller's relationship to the affected person.", conditional_prompt="Only populate if the caller is not the affected person."),
    F("other_party_name", "The name of the person or employer involved in the matter, if the caller shared it. Used for the firm's conflict check. Do not push hard for this if the caller is distressed.", conditional_prompt="Only populate if the caller shared this without being pressured."),
    F("user_email", "The caller's email address, if given.", conditional_prompt="Only populate if given."),
    F("preferred_contact", "How the caller prefers to be contacted back.", type="enum", choices=("phone", "email", "text"), conditional_prompt="Only populate if expressed."),
    F("sh_issue_type", "Type of matter - harassment, assault, discrimination, hostile work environment.", conditional_prompt="Only populate if mentioned."),
    F("sh_nature_of_incidents", "Nature of the incidents - verbal, physical, written, digital.", conditional_prompt="Only populate if mentioned."),
    F("sh_incident_dates", "When the incidents occurred, roughly.", conditional_prompt="Only populate if mentioned."),
    F("sh_incident_location", "Where the incidents occurred - workplace, remote/online, other.", conditional_prompt="Only populate if mentioned."),
    F("sh_witnesses", "Whether anyone witnessed the incidents.", conditional_prompt="Only populate if discussed."),
    F("sh_reported_to_hr", "True if this was reported to HR or a supervisor.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("sh_filed_with_agency", "True if a complaint was filed with an outside agency (EEOC, state agency).", type="boolean", conditional_prompt="Only populate if discussed."),
    F("sh_evidence_details", "Whether evidence exists - messages, emails, recordings, HR records. Do not record content, only existence.", conditional_prompt="Only populate if discussed."),
    F("sh_missed_work", "True if the caller missed work because of this.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("sh_retaliation", "True if the caller experienced retaliation for reporting or speaking up.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("client_goal", "What the caller said they are hoping to achieve, in their own words.", conditional_prompt="Only populate if expressed."),
    F("has_other_attorney", "True if the caller said they are already represented by another attorney for this matter.", type="boolean", conditional_prompt="Only populate if discussed."),
    F("sh_additional_notes", "Any other important context the caller shared that doesn't fit elsewhere.", conditional_prompt="Only populate if relevant."),
)

FIELDS_BY_CASE_TYPE: dict[str, tuple[AnalysisField, ...]] = {
    "accident": ACCIDENT_FIELDS,
    "employment": EMPLOYMENT_FIELDS,
    "premises": PREMISES_FIELDS,
    "malpractice": MALPRACTICE_FIELDS,
    "harassment": HARASSMENT_FIELDS,
}


def json_schema_for(fields: tuple[AnalysisField, ...], schema_name: str) -> dict[str, Any]:
    """Build an OpenAI structured-output schema.

    Every property is nullable so the model can leave a conditional field
    unanswered - that is how Retell's `conditional_prompt` behaved.
    """
    properties: dict[str, Any] = {}
    for f in fields:
        if f.type == "boolean":
            inner: dict[str, Any] = {"type": ["boolean", "null"]}
        elif f.type == "number":
            inner = {"type": ["number", "null"]}
        elif f.type == "enum":
            # Nullable enums are expressed in the description rather than an
            # `enum` keyword: strict structured outputs reject an enum list
            # that has to also permit null.
            inner = {"type": ["string", "null"]}
        else:
            inner = {"type": ["string", "null"]}
        description = f.description
        if f.choices:
            description += f" Must be one of: {', '.join(f.choices)}."
        inner["description"] = description
        properties[f.name] = inner

    return {
        "name": schema_name,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": [f.name for f in fields],
            "additionalProperties": False,
        },
    }


def field_guide(fields: tuple[AnalysisField, ...]) -> str:
    return "\n".join(f.prompt_line() for f in fields)

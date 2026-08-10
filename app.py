from flask import Flask, render_template, request, jsonify
import re

app = Flask(__name__)

# ─── Shared Helpers ──────────────────────────────────────────────────────────

def empty(v):
    return v is None or v == ""


def split_segment(line):
    """Split an EDI segment line into element list."""
    line = line.strip().strip('|')
    if not line:
        return None
    parts = [p.strip() for p in line.split('*')]
    return {"segment_id": parts[0], "elements": parts[1:]}


def parse_segments(raw):
    """Parse raw EDI text into list of {segment_id, elements} dicts."""
    segments = []
    for line in re.split(r'[\n\r~]+', raw.strip()):
        seg = split_segment(line)
        if seg:
            segments.append(seg)
    return segments


def format_date(ds):
    if not ds or len(ds) != 8:
        return ds or ""
    return f"{ds[4:6]}/{ds[6:8]}/{ds[0:4]}"


# ═══════════════════════════════════════════════════════════════════════════
# EDI 278 PARSER
# ═══════════════════════════════════════════════════════════════════════════

# ─── Code Maps (278) ─────────────────────────────────────────────────────────

SEGMENT_LABELS_278 = {
    "ST":  "Transaction Set Header",
    "BHT": "Beginning of Hierarchical Transaction",
    "NM1": "Name",
    "PER": "Contact Information",
    "REF": "Reference Identification",
    "DTP": "Date/Time/Period",
    "INS": "Insured Benefit",
    "HI":  "Health Care Information Codes",
    "NTE": "Note / Special Instruction",
    "SVC": "Service Line Information",
    "HCR": "Health Care Services Decision",
}

ENTITY_CODES_278 = {
    "1B": "Subscriber",
    "1C": "Employer",
    "1D": "Provider",
    "2B": "Submitter",
    "6A": "Requester",
    "6B": "Requested-by",
    "4A": "Payer",
    "QC": "Patient",
}

RELATIONSHIP_CODES_278 = {
    "01": "Spouse", "02": "Child", "03": "Father or Mother",
    "18": "Self", "19": "Child (insured is not parent)",
    "21": "Unknown", "24": "Other adult", "29": "Stepchild",
    "32": "Self", "36": "Spouse", "39": "Employee", "41": "Parent",
    "53": "Life Partner", "G8": "Sibling",
}

DECISION_CODES_278 = {
    "A": "Approved", "D": "Denied", "P": "Pending",
    "R": "Rejected", "S": "Suspended", "X": "Pending Outside Timer",
    "C": "Cancelled", "I": "Inactive", "N": "Not Reviewed",
}

DECISION_REASONS_278 = {
    "AR": "Administrative Denial", "ET": "Evercare Team Determination",
    "IA": "Initiated Adjudication", "NA": "Not Administered",
    "RT": "Reviewed – Tamed", "TN": "Test Notification",
    "UC": "Use of Correct Codes", "XX": "Coordination of Benefits",
}

DECISION_OUTCOMES_278 = {
    "1": "Urgent/Emergent Admission Approved", "2": "Extended Stay Approved",
    "3": "Extended Stay Denied", "4": "Day Suspension",
    "5": "Date of Admission Denied – Not Eligible",
    "6": "Date of Admission Denied – Managed Care Plan",
    "7": "Date of Admission Denied – Benefit Limitations",
    "8": "Date of Admission Denied – Managed Care Restriction",
    "9": "Services Not Authorized – Member Not Eligible",
    "10": "Services Not Authorized – No Auth on File",
    "11": "Insufficient Clinical Information",
    "12": "Medical Director Review Required",
    "13": "Medical Necessity Not Met",
    "14": "Experimental/Investigational",
    "15": "Pre-existing Condition", "16": "Benefit Maximum Reached",
    "17": "Authorization Expired", "18": "Duplicate Authorization",
    "19": "Retroactive Authorization Not Permitted",
    "20": "Self-Administered Drug – Not Covered",
    "21": "Site of Service Not Authorized",
    "22": "Out-of-Network Provider",
    "23": "Required Generic Drug Not Tried",
    "24": "Step Therapy Not Completed",
    "25": "Quantity Limit Exceeded",
    "26": "Authorization Terminated – Fraud",
    "27": "Plan Declares Emergency",
}


def parse_edi278(raw):
    segments = parse_segments(raw)

    result = {
        "transaction_info": {}, "requestor": {}, "subscriber": {},
        "patient": {}, "payer": {}, "diagnosis_codes": [],
        "services": [], "authorization": {}, "notes": [],
        "references": [], "raw_segments": segments,
    }

    last_nm1_entity = None

    for seg in segments:
        sid, el, n = seg["segment_id"], seg["elements"], len(seg["elements"])

        if sid == "ST":
            result["transaction_info"] = {
                "transaction_set_id":   el[0] if n > 0 else None,
                "transaction_set_code": el[1] if n > 1 else None,
                "implementation_ref":   el[2] if n > 2 else None,
                "group_control_ver":    el[3] if n > 3 else None,
            }

        elif sid == "BHT":
            result["transaction_info"].update({
                "hierarchical_structure_code": el[0] if n > 0 else None,
                "transaction_set_purpose_code": "Original" if el[1] == "01" else (el[1] if n > 1 else None),
                "transaction_ref_id":  el[2] if n > 2 else None,
                "transaction_date":    el[3] if n > 3 else None,
                "transaction_time":    el[4] if n > 4 else None,
                "transaction_type":    el[5] if n > 5 else None,
            })

        elif sid == "NM1":
            entity_code = el[0] if n > 0 else None
            entity_name = ENTITY_CODES_278.get(entity_code, entity_code)
            record = {
                "entity_code":   entity_code, "entity_type": entity_name,
                "name_last":     el[2] if n > 2 else None,
                "name_first":    el[3] if n > 3 else None,
                "name_middle":   el[4] if n > 4 else None,
                "name_prefix":   el[5] if n > 5 else None,
                "name_suffix":   el[6] if n > 6 else None,
                "id_code_qual":  el[7] if n > 7 else None,
                "id_code":       el[8] if n > 8 else None,
            }
            if entity_code in ("6A", "2B"):
                result["requestor"].update(record)
            elif entity_code in ("1B", "PR"):
                result["subscriber"].update(record)
            elif entity_code == "QC":
                result["patient"].update(record)
            elif entity_code == "4A":
                result["payer"].update(record)
            last_nm1_entity = entity_code

        elif sid == "PER":
            if last_nm1_entity == "6A" and n > 1:
                result["requestor"]["contact_name"] = el[1] if n > 1 else None
                result["requestor"]["contact_phones"] = [p for p in (el[3] if n > 3 else None, el[5] if n > 5 else None) if p]

        elif sid == "INS":
            result["subscriber"]["relationship_code"] = el[1] if n > 1 else None
            result["subscriber"]["relationship"] = RELATIONSHIP_CODES_278.get(el[1], el[1])
            result["subscriber"]["benefit_status"] = "Active" if el[2] == "A" else el[2]

        elif sid == "HI":
            for e in el:
                if not e:
                    continue
                parts_colon = e.split(':')
                code_val = parts_colon[-1]
                qualifier = parts_colon[0] if len(parts_colon) > 1 else ""
                cd_map = {"ABK": "Principal Diagnosis", "ABF": "Diagnosis", "BK": "Diagnosis", "BF": "Diagnosis"}
                result["diagnosis_codes"].append({"code_type": cd_map.get(qualifier[:2]) or cd_map.get(qualifier[:1]) or "Diagnosis", "code": code_val})

        elif sid == "SVC":
            if n > 0:
                code_qual = el[0].split(":")[0] if ":" in el[0] else ""
                procedure_code = el[0].split(":")[1] if ":" in el[0] else el[0]
                result["services"].append({
                    "procedure_code": procedure_code,
                    "modifier":       el[1] if n > 1 else None,
                    "units":          el[3] if n > 3 else None,
                    "service_amount": el[4] if n > 4 else None,
                    "line_item_ref":  el[5] if n > 5 else None,
                })

        elif sid == "HCR":
            decision_code = el[1] if n > 1 else None
            result["authorization"] = {
                "decision_code": decision_code,
                "decision":      DECISION_CODES_278.get(decision_code, decision_code),
                "timing":        el[2] if n > 2 else None,
                "outcome":       DECISION_OUTCOMES_278.get(el[3], el[3]) if n > 3 else None,
                "reason":        DECISION_REASONS_278.get(el[4], el[4]) if n > 4 else None,
            }

        elif sid == "NTE":
            result["notes"].append({"type": el[1] if n > 1 else None, "text": " ".join(el[2:])})

        elif sid == "REF":
            ref = {"qualifier": el[0] if n > 0 else None, "value": el[1] if n > 1 else None}
            result["references"].append(ref)
            if ref["qualifier"] in ("1L", "23", "6O", "CE", "TJ"):
                result["subscriber"]["member_id"] = ref["value"]
            elif ref["qualifier"] in ("0B", "1C", "1D", "CT", "SY"):
                result["requestor"]["tax_id"] = ref["value"]

    return result


# ═══════════════════════════════════════════════════════════════════════════
# EDI 834 PARSER
# ═══════════════════════════════════════════════════════════════════════════

# ─── Code Maps (834) ──────────────────────────────────────────────────────────

INSURANCE_LINE_CODES = {
    "HLT": "Health", "DEN": "Dental", "VIS": "Vision",
    "LIF": "Life", "CKL": "Checkup", "AHL": "Health (ASC X12)",
}

COVERAGE_STATUS = {
    "A": "Active", "T": "Terminated", "C": "COBRA",
    "S": "Suspended", "N": "Not Covered", "X": "Expired",
}

RELATIONSHIP_CODES_834 = {
    "18": "Self", "01": "Spouse", "02": "Child", "03": "Father or Mother",
    "19": "Child (not parent)", "21": "Unknown", "24": "Other adult",
    "29": "Stepchild", "30": "Parent (step)", "32": "Self",
    "36": "Spouse", "39": "Employee", "41": "Parent", "53": "Life Partner",
    "G8": "Sibling", "60": "Emergency Contact",
}

RACE_CODES = {
    "7": "Native American", "8": "Asian", "9": "African American",
    "EV": "Asian Indian", "JV": "Japanese", "VN": "Vietnamese",
    "WH": "White", "IC": "American Indian/Alaska Native",
    "MP": "Multi-racial", "OT": "Other",
}

EMPLOYMENT_STATUS = {
    "AC": "Active", "AI": "Active – Full Time",
    "AO": "Active – Part Time", "DT": "Disabled",
    "FH": "COBRA", "PE": "Pension", "PT": "Part Time",
    "QE": "QME/FQE", "RD": "Retired",
    "TE": "Terminated", "UL": "Unknown",
}

GENDER_CODES = {"M": "Male", "F": "Female", "U": "Unknown"}

MEDICARE_STATUS_CODES = {
    "A": "Entitled to Medicare A", "B": "Entitled to Medicare B",
    "C": "Entitled to A & B", "D": "Entitled to Part D",
    "E": "ESRD Only", "F": "ESRD + A + B", "G": "ESRD + A + B + D",
    "H": "HMO Non-Systematic", "L": "Low Income Subsidy",
    "M": "Part A Only", "N": "Part B Only", "O": "Demonstration",
    "P": "Patient Protection", "Q": "QMB Only", "T": "QMB + Medicaid",
    "V": "Pharmacy Only", "X": "No Medicare",
}


def parse_edi834(raw):
    segments = parse_segments(raw)

    result = {
        "transaction_info": {}, "sponsor": {}, "plan": {},
        "subscriber": {}, "dependents": [], "addresses": [],
        "coverage_dates": [], "references": [], "notes": [],
        "raw_segments": segments,
    }

    last_nm1_entity = None
    current_subscriber = {}
    current_dependent = {}
    in_dependent_loop = False

    for seg in segments:
        sid, el, n = seg["segment_id"], seg["elements"], len(seg["elements"])

        # ── ST ──────────────────────────────────────────────────────────────
        if sid == "ST":
            result["transaction_info"] = {
                "transaction_set_id":   el[0] if n > 0 else None,
                "transaction_set_code": el[1] if n > 1 else None,
                "implementation_ref":   el[2] if n > 2 else None,
                "group_control_num":   el[3] if n > 3 else None,
            }

        # ── BGN ─────────────────────────────────────────────────────────────
        elif sid == "BGN":
            result["transaction_info"].update({
                "reference_id":          el[1] if n > 1 else None,
                "transaction_type_code": el[2] if n > 2 else None,
                "date":                  el[3] if n > 3 else None,
                "time":                  el[4] if n > 4 else None,
                "time_code":             el[5] if n > 5 else None,
                "reference_id_2":        el[6] if n > 6 else None,
                "action_code":           el[7] if n > 7 else None,
            })

        # ── N1 ──────────────────────────────────────────────────────────────
        elif sid == "N1":
            entity_code = el[0] if n > 0 else None
            if entity_code == "P5":  # Plan Sponsor
                result["sponsor"].update({
                    "entity_code":    entity_code,
                    "name":           el[2] if n > 2 else None,
                    "id_code_qual":   el[3] if n > 3 else None,
                    "id_code":        el[4] if n > 4 else None,
                })
            elif entity_code == "IN":  # Insurer
                result["sponsor"]["insurer_name"] = el[2] if n > 2 else None
                result["sponsor"]["insurer_id_qual"] = el[3] if n > 3 else None
                result["sponsor"]["insurer_id"] = el[4] if n > 4 else None
            elif entity_code == "IH":  # TPA/Insurer (alternate)
                result["sponsor"]["tpa_name"] = el[2] if n > 2 else None

        # ── N2 ───────────────────────────────────────────────────────────────
        elif sid == "N2":
            if in_dependent_loop:
                current_dependent["name_org"] = el[0] if n > 0 else None
            else:
                result["sponsor"]["name_2"] = el[0] if n > 0 else None

        # ── N3 / N4 ─────────────────────────────────────────────────────────
        elif sid == "N3":
            addr = {"address_line_1": el[0] if n > 0 else None}
            if in_dependent_loop:
                current_dependent.update(addr)
            else:
                result["sponsor"]["address"] = addr

        elif sid == "N4":
            addr_part = {"city": el[0] if n > 0 else None,
                         "state": el[1] if n > 1 else None,
                         "zip": el[2] if n > 2 else None,
                         "country": el[3] if n > 3 else None}
            if in_dependent_loop:
                current_dependent.update(addr_part)
            else:
                result["sponsor"]["address"].update(addr_part)

        # ── PER ─────────────────────────────────────────────────────────────
        elif sid == "PER":
            contact = {
                "contact_name": el[1] if n > 1 else None,
                "phone_1": el[3] if n > 3 else None,
                "phone_2": el[5] if n > 5 else None,
            }
            if in_dependent_loop:
                current_dependent.update(contact)
            else:
                result["sponsor"]["contact"] = contact

        # ── DMG ─────────────────────────────────────────────────────────────
        elif sid == "DMG":
            dmg = {
                "date_format": el[0] if n > 0 else None,
                "birth_date":  format_date(el[1]) if n > 1 else None,
                "gender":      GENDER_CODES.get(el[2], el[2]) if n > 2 else None,
                "race":        RACE_CODES.get(el[3], el[3]) if n > 3 else None,
                "marital":     el[4] if n > 4 else None,
            }
            if in_dependent_loop:
                current_dependent.update(dmg)
            else:
                current_subscriber.update(dmg)

        # ── HD ───────────────────────────────────────────────────────────────
        elif sid == "HD":
            maintenance_type = el[0] if n > 0 else None
            benefit_status = el[1] if n > 1 else None
            result["plan"]["maintenance_type"] = maintenance_type
            result["plan"]["coverage_status"] = COVERAGE_STATUS.get(benefit_status, benefit_status)
            result["plan"]["insurance_line"] = INSURANCE_LINE_CODES.get(el[3], el[3]) if n > 3 else None
            result["plan"]["plan_coverage_desc"] = el[4] if n > 4 else None
            result["plan"]["insurance_line_code"] = el[3] if n > 3 else None

        # ── DTP ─────────────────────────────────────────────────────────────
        elif sid == "DTP":
            date_qual = el[0] if n > 0 else None
            date_fmt  = el[1] if n > 1 else None
            date_val  = format_date(el[2]) if n > 2 else None
            qual_label = {
                "348": "Benefit Begin", "349": "Benefit End",
                "336": "Employment Begin", "337": "Employment End",
                "343": "COBRA Qualifying Event Date",
                "346": "Plan Enrollment Date", "347": "Plan Termination Date",
                "350": "Premium Paid Through Date", "351": "Eligibility Begin",
                "353": "Eligibility End", "541": "Coverage Expiration",
            }.get(date_qual, date_qual)
            result["coverage_dates"].append({
                "qualifier": qual_label, "format": date_fmt, "date": date_val
            })

        # ── REF ─────────────────────────────────────────────────────────────
        elif sid == "REF":
            ref = {"qualifier": el[0] if n > 0 else None, "value": el[1] if n > 1 else None, "description": el[2] if n > 2 else None}
            result["references"].append(ref)
            # also attach to current subscriber
            q = ref["qualifier"]
            if q in ("0F", "1L", "23", "CE", "CI", "CT", "EH", "F6", "GE", "GO", "HP", "LU", "MR", "N6", "N7", "N9", "NB", "NQ", "NR", "PH", "PP", "Q4", "RL", "SJ", "ST", "TJ", "TN", "Y5", "ZH"):
                current_subscriber[f"ref_{q}"] = ref["value"]
            if q == "1L":
                current_subscriber["member_id"] = ref["value"]
            elif q == "0F":
                current_subscriber["ssn"] = ref["value"]

        # ── NM1 ─────────────────────────────────────────────────────────────
        elif sid == "NM1":
            entity_code = el[0] if n > 0 else None
            entity_name = {
                "IL": "Subscriber", "IN": "Insured", "1B": "Subscriber",
                "2B": "Plan Sponsor", "6A": "Member", "QD": "Dependent",
            }.get(entity_code, entity_code)

            record = {
                "entity_code":  entity_code,
                "entity_type":  entity_name,
                "name_last":    el[2] if n > 2 else None,
                "name_first":   el[3] if n > 3 else None,
                "name_middle":  el[4] if n > 4 else None,
                "name_prefix":  el[5] if n > 5 else None,
                "name_suffix":  el[6] if n > 6 else None,
                "id_code_qual": el[7] if n > 7 else None,
                "id_code":      el[8] if n > 8 else None,
            }

            if entity_code == "IL":
                # Save any prior subscriber
                if current_subscriber.get("name_first") or current_subscriber.get("name_last"):
                    if current_dependent:
                        result["dependents"].append(dict(current_dependent))
                    else:
                        result["subscriber"] = dict(current_subscriber)
                current_subscriber = dict(record)
                current_dependent = {}
                in_dependent_loop = False
            elif entity_code == "QD":
                # Save any prior dependent
                if current_dependent.get("name_first") or current_dependent.get("name_last"):
                    result["dependents"].append(dict(current_dependent))
                current_dependent = dict(record)
                in_dependent_loop = True

            last_nm1_entity = entity_code

        # ── INS ─────────────────────────────────────────────────────────────
        elif sid == "INS":
            ins_record = {
                "relationship_code":  el[1] if n > 1 else None,
                "relationship":       RELATIONSHIP_CODES_834.get(el[1], el[1]),
                "benefit_status":     COVERAGE_STATUS.get(el[2], el[2]) if n > 2 else None,
                "employment_status": EMPLOYMENT_STATUS.get(el[6], el[6]) if n > 6 else None,
                "cob":                "COBRA Enrollee" if el[8] == "H" else el[8],
                "medicare_status":    MEDICARE_STATUS_CODES.get(el[12], el[12]) if n > 12 else None,
                "date_of_death":      format_date(el[17]) if n > 17 and el[17] else None,
            }
            if in_dependent_loop:
                current_dependent.update(ins_record)
            else:
                current_subscriber.update(ins_record)

        # ── MPI ─────────────────────────────────────────────────────────────
        elif sid == "MPI":
            mpi_record = {
                "medicare_status":   MEDICARE_STATUS_CODES.get(el[0], el[0]) if n > 0 else None,
                "medicare_number":   el[1] if n > 1 else None,
                "medicare_claim":    el[2] if n > 2 else None,
            }
            if in_dependent_loop:
                current_dependent.update(mpi_record)
            else:
                current_subscriber.update(mpi_record)

        # ── NTE ─────────────────────────────────────────────────────────────
        elif sid == "NTE":
            note = {"type": el[0] if n > 0 else None, "text": " ".join(el[1:])}
            if in_dependent_loop:
                current_dependent.setdefault("notes", []).append(note)
            else:
                result["notes"].append(note)

    # Flush last member
    if current_subscriber.get("name_first") or current_subscriber.get("name_last"):
        if in_dependent_loop and (current_dependent.get("name_first") or current_dependent.get("name_last")):
            result["dependents"].append(dict(current_dependent))
        else:
            result["subscriber"] = dict(current_subscriber)

    if current_dependent.get("name_first") or current_dependent.get("name_last"):
        result["dependents"].append(dict(current_dependent))

    return result


# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/parse", methods=["POST"])
def parse():
    raw = request.form.get("edi", "").strip()
    fmt = request.form.get("format", "278").strip()

    if not raw:
        return jsonify({"error": "No EDI data provided."}), 400

    if fmt not in ("278", "834"):
        return jsonify({"error": f"Unsupported format: {fmt}"}), 400

    try:
        parsed = parse_edi278(raw) if fmt == "278" else parse_edi834(raw)
        parsed["_format"] = fmt
        return jsonify(parsed)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)

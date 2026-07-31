from flask import Flask, render_template, request, jsonify
import re

app = Flask(__name__)

# ─── Code Maps ────────────────────────────────────────────────────────────────

SEGMENT_LABELS = {
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
    "LS":  "Loop Start",
    "LE":  "Loop End",
}

ENTITY_CODES = {
    "1B": "Subscriber",
    "1C": "Employer",
    "1D": "Provider",
    "2B": "Submitter",
    "3B": "Utilization Management Organization (UMO)",
    "4A": "Insurer",
    "4B": "Insurance Agent",
    "4C": "Insurance Broker",
    "6A": "Requester",
    "6B": "Requested-by",
    "6C": "Sender's Broker",
    "DD": "Discharge Location",
    "EW": "Entered-by",
    "EX": "Examine Location",
    "IL": "Injury Location",
    "P5": "Provider of Service",
    "PR": "Payer",
    "PW": "Approving / Reviewing UTI",
    "QC": "Patient",
    "TN": "Trauma Registry",
    "TR": "Treatment Location",
    "TTP": "Treating Physician",
    "X3": "Facility",
}

RELATIONSHIP_CODES = {
    "01": "Spouse",
    "02": "Child",
    "03": "Father or Mother",
    "04": "Grandfather or Grandmother",
    "05": "Grandchild",
    "18": "Self",
    "19": "Child (insured is not parent)",
    "21": "Unknown",
    "24": "Other adult relationship",
    "29": "Stepchild",
    "30": "Father or Mother (stepparent)",
    "32": "Self",
    "34": "Spouse (destitute)",
    "36": "Spouse",
    "39": "Employee",
    "40": "Unknown",
    "41": "Parent",
    "43": "Spouse",
    "53": "Life Partner",
    "60": "Emergency Contact",
    "G8": "Sibling",
}

DECISION_CODES = {
    "A":  "Approved",
    "D":  "Denied",
    "P":  "Pending",
    "R":  "Rejected",
    "S":  "Suspended",
    "X":  "Pending Outside Timer",
    "C":  "Cancelled",
    "I":  "Inactive",
    "N":  "Not Reviewed",
}

DECISION_REASONS = {
    "AR": "Administrative Denial – Requires Authorization Prior to Service",
    "ET": "Evercare Team Determination",
    "IA": "Initiated Adjudication",
    "NA": "Not Administered",
    "RT": "Reviewed – Tamed",
    "TN": "Test Notification",
    "UC": "Use of Correct Codes",
    "XX": "Coordination of Benefits Information",
}

DECISION_OUTCOMES = {
    "1":  "Urgent / Emergent Admission Approved",
    "2":  "Extended Stay Approved",
    "3":  "Extended Stay Denied",
    "4":  "Day Suspension",
    "5":  "Date of Admission Denied – Not Eligible",
    "6":  "Date of Admission Denied – Managed Care Plan",
    "7":  "Date of Admission Denied – Benefit Limitations",
    "8":  "Date of Admission Denied – Managed Care Restriction",
    "9":  "Services Not Authorized – Member Not Eligible",
    "10": "Services Not Authorized – No Auth on File",
    "11": "Insufficient Clinical Information Provided",
    "12": "Medical Director Review Required",
    "13": "Medical Necessity Not Met",
    "14": "Experimental / Investigational",
    "15": "Pre-existing Condition",
    "16": " Benefit Maximum Reached",
    "17": "Authorization Expired",
    "18": "Duplicate Authorization Request",
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

# ─── Parser ──────────────────────────────────────────────────────────────────

def empty(v):
    return v is None or v == ""


def get_segment_code(elements, codes):
    for e in elements:
        if e in codes:
            return codes[e]
    return None


def parse_edi278(raw):
    segments = []
    for line in re.split(r'[\n\r~]+', raw.strip()):
        line = line.strip().strip('|')
        if not line:
            continue
        parts = [p.strip() for p in line.split('|')]
        seg_id = parts[0] if parts else ""
        elements = parts[1:] if len(parts) > 1 else []
        segments.append({"segment_id": seg_id, "elements": elements})

    result = {
        "transaction_info": {},
        "requestor":       {},
        "subscriber":      {},
        "patient":         {},
        "payer":           {},
        "diagnosis_codes": [],
        "services":        [],
        "authorization":   {},
        "notes":           [],
        "references":      [],
        "raw_segments":    segments,
    }

    current_loop = None
    last_nm1_entity = None

    for seg in segments:
        sid  = seg["segment_id"]
        el   = seg["elements"]
        n    = len(el)

        # ── ST ──────────────────────────────────────────────────────────────
        if sid == "ST":
            result["transaction_info"] = {
                "transaction_set_id":   el[0] if n > 0 else None,
                "transaction_set_code": el[1] if n > 1 else None,
                "implementation_ref":  el[2] if n > 2 else None,
                "group_control_ver":   el[3] if n > 3 else None,
            }

        # ── BHT ──────────────────────────────────────────────────────────────
        elif sid == "BHT":
            result["transaction_info"]["hierarchical_structure_code"] = el[1] if n > 1 else None
            result["transaction_info"]["transaction_set_purpose_code"] = "Original" if el[2] == "01" else el[2] if n > 2 else None
            result["transaction_info"]["transaction_ref_id"]          = el[3] if n > 3 else None
            result["transaction_info"]["transaction_date"]            = el[4] if n > 4 else None
            result["transaction_info"]["transaction_time"]           = el[5] if n > 5 else None
            result["transaction_info"]["transaction_type"]            = el[6] if n > 6 else None

        # ── NM1 ─────────────────────────────────────────────────────────────
        elif sid == "NM1":
            entity_code = el[1] if n > 1 else None
            entity_name = ENTITY_CODES.get(entity_code, entity_code)
            record = {
                "entity_code":      entity_code,
                "entity_type":      entity_name,
                "name_last":        el[3] if n > 3 else None,
                "name_first":       el[4] if n > 4 else None,
                "name_middle":      el[5] if n > 5 else None,
                "name_prefix":      el[6] if n > 6 else None,
                "name_suffix":      el[7] if n > 7 else None,
                "id_code_qual":     el[8] if n > 8 else None,
                "id_code":          el[9] if n > 9 else None,
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

        # ── PER ────────────────────────────────────────────────────────────
        elif sid == "PER":
            if last_nm1_entity == "6A" and n > 2:
                result["requestor"]["contact_name"]  = el[2] if n > 2 else None
                result["requestor"]["contact_phones"] = [p for p in (el[4] if n > 4 else None, el[6] if n > 6 else None) if p]

        # ── INS ─────────────────────────────────────────────────────────────
        elif sid == "INS":
            result["subscriber"]["relationship_code"] = el[2] if n > 2 else None
            result["subscriber"]["relationship"]      = RELATIONSHIP_CODES.get(el[2], el[2])
            result["subscriber"]["benefit_status"]    = "Active" if el[3] == "A" else el[3]

        # ── HI ─────────────────────────────────────────────────────────────
        elif sid == "HI":
            for i, e in enumerate(el):
                if e and e[0] in "ABCDEFGHIJKLMN":
                    code_type_map = {
                        "ABK": "Principal Diagnosis",
                        "ABF": "Diagnosis",
                        "BK":  "Diagnosis",
                        "BF":  "Diagnosis",
                    }
                    cd = get_segment_code([e[:2]], code_type_map)
                    if not cd:
                        cd = get_segment_code([e[:1]], code_type_map)
                    code_val = e[2:] if len(e) > 2 else e
                    result["diagnosis_codes"].append({"code_type": cd or "Diagnosis", "code": code_val})

        # ── SVC ─────────────────────────────────────────────────────────────
        elif sid == "SVC":
            if n > 1:
                code_qual = el[0] if ":" not in el[0] else el[0].split(":")[0]
                procedure_code = el[0].split(":")[1] if ":" in el[0] else (el[1] if n > 1 else None)
                result["services"].append({
                    "procedure_code":  procedure_code,
                    "modifier":        el[2] if n > 2 else None,
                    "units":           el[4] if n > 4 else None,
                    "service_amount":  el[5] if n > 5 else None,
                    "line_item_ref":  el[6] if n > 6 else None,
                })

        # ── HCR ─────────────────────────────────────────────────────────────
        elif sid == "HCR":
            decision_code = el[1] if n > 1 else None
            result["authorization"]["decision_code"] = decision_code
            result["authorization"]["decision"]      = DECISION_CODES.get(decision_code, decision_code)
            result["authorization"]["timing"]        = el[2] if n > 2 else None
            result["authorization"]["outcome"]        = DECISION_OUTCOMES.get(el[3], el[3]) if n > 3 else None
            result["authorization"]["reason"]         = DECISION_REASONS.get(el[4], el[4]) if n > 4 else None

        # ── NTE ─────────────────────────────────────────────────────────────
        elif sid == "NTE":
            result["notes"].append({"type": el[1] if n > 1 else None, "text": " ".join(el[2:])})

        # ── REF ─────────────────────────────────────────────────────────────
        elif sid == "REF":
            ref = {"qualifier": el[0] if n > 0 else None, "value": el[1] if n > 1 else None}
            result["references"].append(ref)
            if ref["qualifier"] in ("1L", "23", "6O", "CE", "TJ"):
                result["subscriber"]["member_id"] = ref["value"]
            elif ref["qualifier"] in ("0B", "1C", "1D", "CT", "SY"):
                result["requestor"]["tax_id"] = ref["value"]

    return result


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/parse", methods=["POST"])
def parse():
    raw = request.form.get("edi", "").strip()
    if not raw:
        return jsonify({"error": "No EDI 278 data provided."}), 400

    try:
        parsed = parse_edi278(raw)
        return jsonify(parsed)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)

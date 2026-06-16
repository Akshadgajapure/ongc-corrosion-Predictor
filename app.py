from __future__ import annotations
import math
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt(value: float, digits: int = 4) -> str:
    if not math.isfinite(value):
        return "—"
    return f"{value:,.{digits}f}"

def fmt_rate(value: float) -> str:
    if not math.isfinite(value):
        return "—"
    if abs(value) >= 100:
        return f"{value:.1f}"
    if abs(value) >= 10:
        return f"{value:.3f}"
    return f"{value:.4f}"

# ── Corrosion Calculator ──────────────────────────────────────────────────────

def calculate(data: dict) -> dict:
    T  = float(data["temperature"])
    P  = float(data["total_pressure"])
    x  = float(data["co2_fraction"])
    a  = float(data["fugacity_coeff"])
    D  = float(data["pipe_diameter"])
    WC = float(data["water_cut"])
    QL = float(data["liquid_rate"])
    Rc = float(data["condensation_rate"])
    W  = float(data["glycol_content"])
    Ag = float(data["glycol_a"])
    Ei = float(data["inhibitor_eff"])
    Vg = 0.0

    # Validation
    if T + 273 <= 0:   raise ValueError("Temperature too low.")
    if D <= 0:          raise ValueError("Pipe inner diameter must be > 0.")
    if not 0 <= WC <= 100: raise ValueError("Water cut must be 0–100%.")
    if not 0 <= Ei <= 100: raise ValueError("Inhibitor efficiency must be 0–100%.")
    if not 0 < x <= 1:    raise ValueError("CO₂ fraction must be between 0 and 1.")
    if W <= 0:             raise ValueError("Glycol water content must be > 0.")

    TK   = T + 273
    pco2 = x * P
    fco2 = a * pco2
    if pco2 <= 0 or fco2 <= 0:
        raise ValueError("CO₂ partial pressure and fugacity must be positive.")

    dm        = D * 0.0254
    pipe_area = math.pi * dm * dm / 4
    oil_rate  = QL * (1 - WC / 100)
    oil_vel   = (oil_rate / 86400) / pipe_area if pipe_area > 0 else float("nan")

    vnom  = 10 ** (5.8 - 1710 / TK + 0.67 * math.log10(pco2))
    fsys  = 10 ** (0.67 * (0.0031 - 1.4 / TK) * P)

    tscale_k = 2400 / (6.7 + 0.6 * math.log10(fco2))
    tscale_c = tscale_k - 273
    fscale   = 1 if (T <= 60 or Vg > 20 or TK <= tscale_k) else 10 ** (2400 * (1/TK - 1/tscale_k))

    phact  = 3.71 + 0.00417 * T - 0.5 * math.log10(fco2)
    phsat1 = 1.36 + 1307 / TK - 0.17 * math.log10(fco2)
    phsat2 = 5.4 - 0.66 * math.log10(fco2)
    phsat  = min(phsat1, phsat2)
    fph    = (10 ** (0.32 * (phsat - phact)) if phsat > phact
              else 10 ** (-0.13 * (phact - phsat) ** 1.6))

    foil      = 0 if (WC < 30 and oil_vel > 1) else 1
    fcond     = 0.1 if Rc < 0.25 else 0.4 * Rc if Rc < 2.5 else 1
    fglyc_log = Ag * math.log10(W) - 2 * Ag
    fglyc     = 10 ** fglyc_log
    finh      = 1 - Ei / 100

    pre_rate  = vnom * fsys * fscale * fph * foil * fcond * fglyc
    corr_rate = pre_rate * finh

    if corr_rate >= 10:   risk_label, risk_style = "High Risk",     "high"
    elif corr_rate >= 3:  risk_label, risk_style = "Moderate Risk", "moderate"
    elif corr_rate >= 0.1:risk_label, risk_style = "Low Risk",      "low"
    else:                  risk_label, risk_style = "Negligible",    "negligible"

    factors = [
        {"name": "V nom",   "value": fmt_rate(vnom),   "desc": "Base corrosion rate",        "formula": "10^(5.8 − 1710/T_K + 0.67·log pCO₂)"},
        {"name": "F sys",   "value": fmt_rate(fsys),   "desc": "System pressure correction", "formula": "10^(0.67·(0.0031−1.4/T_K)·P)"},
        {"name": "F scale", "value": fmt_rate(fscale), "desc": "FeCO₃ scale protection",     "formula": "10^(2400·(1/T_K − 1/T_scale))" if fscale != 1 else "= 1 (T ≤ 60°C or no scale)"},
        {"name": "F pH",    "value": fmt_rate(fph),    "desc": "pH correction factor",       "formula": "10^(0.32·(pH_sat−pH_act))  or  10^(−0.13·(pH_act−pH_sat)^1.6)"},
        {"name": "F oil",   "value": str(foil),        "desc": "Oil wetting factor",         "formula": "0 if WC<30% and oil velocity>1 m/s, else 1"},
        {"name": "F cond",  "value": fmt_rate(fcond),  "desc": "Water condensation factor",  "formula": "0.1 (Rc<0.25)  /  0.4·Rc (Rc<2.5)  /  1.0"},
        {"name": "F glyc",  "value": fmt_rate(fglyc),  "desc": "Glycol correction factor",   "formula": "10^(A·log(W%)−2A)"},
        {"name": "F inh",   "value": fmt_rate(finh),   "desc": "Inhibitor factor",           "formula": "1 − E/100"},
        {"name": "T scale", "value": f"{fmt(tscale_c,3)} °C", "desc": "FeCO₃ scaling threshold", "formula": "2400/(6.7+0.6·log fCO₂) − 273"},
    ]

    trace = [
        ("pCO₂",          "x × P",                                     f"{fmt(pco2,4)} bar",        "CO₂ partial pressure"),
        ("fCO₂",          "α × pCO₂",                                  f"{fmt(fco2,4)} bar",        "Fugacity of CO₂"),
        ("Pipe area",      "π·D²/4  (D in metres)",                    f"{fmt(pipe_area,6)} m²",    ""),
        ("Oil rate",       "Q_L × (1 − WC/100)",                       f"{fmt(oil_rate,2)} m³/day", ""),
        ("Oil velocity",   "Q_oil ÷ (A × 86400)",                      f"{fmt(oil_vel,4)} m/s",     ""),
        ("V nom",          "10^(5.8 − 1710/T_K + 0.67·log pCO₂)",    f"{fmt_rate(vnom)} mm/yr",   "CO₂ corrosion; excludes scale, H₂S, inhibitors"),
        ("F sys",          "10^(0.67·(0.0031−1.4/T_K)·P)",           fmt_rate(fsys),               "Gas non-ideality at high pressure"),
        ("T scale (K)",    "2400 / (6.7 + 0.6·log fCO₂)",            f"{fmt(tscale_k,2)} K",       "FeCO₃ scaling onset temperature"),
        ("T scale (°C)",   "T_scale(K) − 273",                         f"{fmt(tscale_c,2)} °C",     ""),
        ("F scale",        "10^(2400·(1/T_K − 1/T_scale))  or  1",   fmt_rate(fscale),             "Not valid if scale is mechanically removed"),
        ("pH actual",      "3.71 + 0.00417·T − 0.5·log fCO₂",       fmt(phact,4),                 "Water + CO₂ only"),
        ("pH sat 1",       "1.36 + 1307/T_K − 0.17·log fCO₂",       fmt(phsat1,4),                ""),
        ("pH sat 2",       "5.4 − 0.66·log fCO₂",                    fmt(phsat2,4),                ""),
        ("pH sat (used)",  "min(pH_sat1, pH_sat2)",                    fmt(phsat,4),                 "Conservative selection"),
        ("F pH",           "Case-dependent pH correction",             fmt_rate(fph),                "Valid mainly at low CO₂ pressures"),
        ("F oil",          "Oil wetting activation",                    str(foil),                    "Applicable to crude oil only"),
        ("F cond",         "R_c-based condensation factor",            fmt_rate(fcond),              "Developed for wet-gas systems"),
        ("log F glyc",     "A·log(W%) − 2·A",                         fmt(fglyc_log,4),             "Valid mainly for MEG/DEG systems"),
        ("F glyc",         "10^(log F glyc)",                          fmt_rate(fglyc),              f"W% = {fmt(W,2)}%"),
        ("Pre-inh. CR",    "V_nom·F_sys·F_scale·F_pH·F_oil·F_cond·F_glyc", f"{fmt_rate(pre_rate)} mm/yr", ""),
        ("F inhibitor",    "1 − (Efficiency / 100)",                   fmt_rate(finh),               "Direct multiplier"),
        ("Final CR",       "Pre-inh. CR × F_inhibitor",               f"{fmt_rate(corr_rate)} mm/yr","Final result — de Waard–Milliams"),
    ]

    return {
        "pre_rate":   fmt_rate(pre_rate),
        "corr_rate":  fmt_rate(corr_rate),
        "risk_label": risk_label,
        "risk_style": risk_style,
        "factors":    factors,
        "trace":      [{"param": t[0], "formula": t[1], "value": t[2], "notes": t[3]} for t in trace],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/calculate", methods=["POST"])
def calculate_route():
    try:
        result = calculate(request.json)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


if __name__ == "__main__":
    app.run(debug=True, port=5050)

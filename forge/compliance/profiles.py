"""Compliance domains: what a project is built for decides which checks gate it and which standards it cites.

A person picks a domain once (`forge comply set avionics --level A`). From then
on FORGE:

- attaches a compliance gate to new work, so a task cannot complete until the
  gate's checks are met on the exact snapshot, or a person waives them;
- reports, control by control, what the evidence shows against the domain's
  catalogs, citing the catalog's source URL and the SHA-256 of the exact file.

Three kinds of reference appear here, and they are kept apart on purpose:

1. Bundled catalogs (NIST SP 800-53 Rev. 5, SP 800-171 Rev. 3, SP 800-218
   SSDF): public domain, imported from NIST's official OSCAL files, cited by
   control label.
2. Industry standards (DO-178C, IEC 62304, PCI DSS, ISO 26262, ...): listed by
   name, publisher and how to obtain them. Most are copyrighted and sold, so
   FORGE does not ship their text or invent their clause numbers. Import your
   licensed copy (OSCAL or CSV) and it is cited like the NIST catalogs.
3. Gate checks: FORGE's own choice of which automated checks must pass before
   work in this domain is complete. They are evidence, not certification.

Every mapping from a check to a control below was checked against the bundled
catalog (tests/test_compliance.py fails if a label is missing or withdrawn).
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from pydantic import Field

from forge.compliance.oscal import CompactCatalog
from forge.contracts.base import StrictModel
from forge.contracts.errors import ErrorCode, ForgeError

BUNDLED_CATALOGS = ("nist-800-53r5", "nist-800-171r3", "nist-800-218")
COVERAGE_CRITERIA = ("statement", "decision", "mcdc")

# Which FORGE check supplies evidence for which control. A mapped control is
# "supported" by FORGE's evidence; its organizational parts (policies,
# training, staffing) still need a person's attestation.
MAPPINGS: dict[str, dict[str, list[str]]] = {
    "nist-800-53r5": {
        "SA-11": ["tests_pass", "static_analysis", "dynamic_analysis"],
        "SA-11(1)": ["static_analysis"],
        "SA-11(4)": ["code_review"],
        "SA-11(8)": ["dynamic_analysis"],
        "SA-15(7)": ["vulnerability_scan"],
        "SA-10": ["change_control"],
        "CM-3": ["change_control"],
        "CM-3(2)": ["tests_pass", "change_control"],
        "CM-8": ["component_inventory"],
        "SI-2": ["flaw_remediation"],
        "SI-7": ["release_integrity"],
        "SI-16": ["build_hardening"],
        "AU-9": ["audit_integrity"],
        "RA-5": ["vulnerability_scan"],
        "IA-5(7)": ["no_embedded_secrets"],
        "SR-4": ["component_inventory"],
    },
    "nist-800-171r3": {
        "03.03.08": ["audit_integrity"],
        "03.04.03": ["change_control"],
        "03.04.10": ["component_inventory"],
        "03.11.02": ["vulnerability_scan"],
        "03.14.01": ["flaw_remediation"],
    },
    "nist-800-218": {
        "PS.1": ["change_control", "audit_integrity"],
        "PS.2": ["release_integrity"],
        "PS.3": ["release_integrity", "component_inventory"],
        "PW.4": ["component_inventory", "vulnerability_scan"],
        "PW.5": ["static_analysis", "no_embedded_secrets"],
        "PW.6": ["build_hardening"],
        "PW.7": ["code_review", "static_analysis"],
        "PW.8": ["tests_pass", "dynamic_analysis"],
        "RV.1": ["vulnerability_scan", "static_analysis"],
        "RV.2": ["flaw_remediation"],
    },
}


class Standard(StrictModel):
    """An industry standard FORGE can point to but does not ship."""

    standard_id: str
    name: str
    publisher: str
    access: str                         # "licensed" (bought) | "public" | "registration" (free, not redistributable)
    obtain: str                         # where a person gets the authoritative text
    topics: dict[str, list[str]] = Field(default_factory=dict)   # FORGE's topic wording -> supporting checks
    note: str = ""


class CatalogUse(StrictModel):
    catalog_id: str
    baseline: str | None = None         # a named selection (e.g. NIST "moderate"); None = every current control


class OptionSpec(StrictModel):
    values: list[str]
    default: str
    meaning: str


class Profile(StrictModel):
    profile_id: str
    title: str
    summary: str
    catalogs: list[CatalogUse]
    gate_checks: list[str]
    options: dict[str, OptionSpec] = Field(default_factory=dict)
    standards: list[str] = Field(default_factory=list)


class ResolvedProfile(StrictModel):
    """A profile with its options applied: exactly what the gate and the report use."""

    profile_id: str
    title: str
    options: dict[str, str]
    catalogs: list[CatalogUse]
    gate_checks: list[str]
    check_options: dict[str, dict[str, Any]]
    standards: list[Standard]
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- standards

_SECURE_DEV = {"secure development practices": ["static_analysis", "code_review", "no_embedded_secrets"],
               "verification by testing": ["tests_pass", "dynamic_analysis"],
               "configuration and change management": ["change_control", "audit_integrity"],
               "third-party components": ["component_inventory", "vulnerability_scan"]}
_SAFETY = {"requirements-based testing": ["tests_pass", "requirements_traceability"],
           "structural coverage analysis": ["structural_coverage"],
           "traceability": ["requirements_traceability"],
           "configuration management": ["change_control", "release_integrity", "audit_integrity"],
           "reviews and analyses": ["code_review", "static_analysis"]}

STANDARDS: dict[str, Standard] = {s.standard_id: s for s in (
    # Avionics
    Standard(standard_id="do-178c", name="DO-178C / ED-12C: Software Considerations in Airborne Systems and Equipment "
             "Certification", publisher="RTCA / EUROCAE", access="licensed", obtain="purchase from RTCA or EUROCAE",
             topics=_SAFETY, note="Software levels A-E. Structural coverage by level: A MC/DC, B decision, "
             "C statement. Confirm every objective against your copy."),
    Standard(standard_id="do-330", name="DO-330: Software Tool Qualification Considerations", publisher="RTCA",
             access="licensed", obtain="purchase from RTCA",
             topics={"tool qualification data": ["release_integrity", "audit_integrity"]},
             note="FORGE records every tool's executable digest with its evidence; qualifying FORGE itself as a "
             "verification tool is your applicant's decision."),
    Standard(standard_id="do-326a", name="DO-326A / ED-202A: Airworthiness Security Process Specification",
             publisher="RTCA / EUROCAE", access="licensed", obtain="purchase from RTCA or EUROCAE",
             topics={"security verification": ["static_analysis", "dynamic_analysis", "vulnerability_scan"]}),
    Standard(standard_id="arp4754b", name="ARP4754B: Guidelines for Development of Civil Aircraft and Systems",
             publisher="SAE International", access="licensed", obtain="purchase from SAE International",
             topics={"requirements traceability": ["requirements_traceability"]}),
    Standard(standard_id="faa-ac-20-115d", name="FAA Advisory Circular 20-115D (recognizes DO-178C)",
             publisher="US Federal Aviation Administration", access="public", obtain="faa.gov advisory circulars"),
    # Medical
    Standard(standard_id="iec-62304", name="IEC 62304: Medical device software - Software life cycle processes",
             publisher="IEC", access="licensed", obtain="purchase from IEC or a national standards body",
             topics={**_SAFETY, "software of unknown provenance (SOUP)": ["component_inventory",
                                                                          "vulnerability_scan"]},
             note="Software safety classes A, B, C."),
    Standard(standard_id="iso-14971", name="ISO 14971: Application of risk management to medical devices",
             publisher="ISO", access="licensed", obtain="purchase from ISO or a national standards body"),
    Standard(standard_id="iec-81001-5-1", name="IEC 81001-5-1: Health software - Security - Activities in the "
             "product life cycle", publisher="IEC", access="licensed", obtain="purchase from IEC", topics=_SECURE_DEV),
    Standard(standard_id="fda-premarket-cyber", name="FDA guidance: Cybersecurity in Medical Devices - Quality "
             "System Considerations and Content of Premarket Submissions", publisher="US FDA", access="public",
             obtain="fda.gov guidance documents", topics={"software bill of materials": ["component_inventory"],
                                                          "vulnerability assessment": ["vulnerability_scan"]}),
    Standard(standard_id="fdca-524b", name="FD&C Act section 524B: Ensuring cybersecurity of devices",
             publisher="US Congress", access="public", obtain="US Code, title 21",
             topics={"software bill of materials for cyber devices": ["component_inventory"]}),
    Standard(standard_id="21-cfr-820", name="21 CFR Part 820: Quality Management System Regulation",
             publisher="US FDA", access="public", obtain="ecfr.gov",
             topics={"design verification and change control": ["tests_pass", "change_control"]}),
    Standard(standard_id="21-cfr-11", name="21 CFR Part 11: Electronic Records; Electronic Signatures",
             publisher="US FDA", access="public", obtain="ecfr.gov",
             topics={"audit trails": ["audit_integrity"]}),
    # Banking and payments
    Standard(standard_id="pci-dss", name="PCI DSS v4.0.1: Payment Card Industry Data Security Standard",
             publisher="PCI Security Standards Council", access="registration",
             obtain="pcisecuritystandards.org document library (free, under its license agreement)",
             topics={"bespoke and custom software security": ["code_review", "static_analysis",
                                                              "no_embedded_secrets"],
                     "vulnerability management": ["vulnerability_scan", "flaw_remediation"],
                     "change control": ["change_control"], "software inventory": ["component_inventory"]}),
    Standard(standard_id="pci-sss", name="PCI Secure Software Standard", publisher="PCI Security Standards Council",
             access="registration", obtain="pcisecuritystandards.org document library", topics=_SECURE_DEV),
    Standard(standard_id="ffiec-it", name="FFIEC IT Examination Handbook", publisher="Federal Financial Institutions "
             "Examination Council", access="public", obtain="ithandbook.ffiec.gov", topics=_SECURE_DEV),
    Standard(standard_id="nydfs-500", name="23 NYCRR Part 500: Cybersecurity Requirements for Financial Services "
             "Companies", publisher="New York State Department of Financial Services", access="public",
             obtain="dfs.ny.gov", topics={"application security": ["static_analysis", "code_review"],
                                          "audit trail": ["audit_integrity"]}),
    Standard(standard_id="eu-dora", name="Regulation (EU) 2022/2554: Digital Operational Resilience Act (DORA)",
             publisher="European Union", access="public", obtain="eur-lex.europa.eu",
             topics={"ICT change management": ["change_control"], "testing": ["tests_pass", "dynamic_analysis"]}),
    Standard(standard_id="sox-itgc", name="Sarbanes-Oxley Act section 404: IT general controls",
             publisher="US Congress / PCAOB", access="public", obtain="your auditors define the control set",
             topics={"program change management": ["change_control", "code_review"],
                     "audit evidence": ["audit_integrity"]}),
    # Defense and federal
    Standard(standard_id="cmmc", name="Cybersecurity Maturity Model Certification (32 CFR Part 170)",
             publisher="US Department of Defense", access="public", obtain="ecfr.gov; dodcio.defense.gov/cmmc",
             note="CMMC Level 2 assesses NIST SP 800-171 Rev. 2. FORGE bundles Rev. 3, the current revision; "
             "import Rev. 2 if your contract cites it."),
    Standard(standard_id="dfars-7012", name="DFARS 252.204-7012: Safeguarding Covered Defense Information",
             publisher="US Department of Defense", access="public", obtain="acquisition.gov"),
    Standard(standard_id="disa-asd-stig", name="Application Security and Development STIG",
             publisher="DISA", access="public", obtain="public.cyber.mil STIG library (XCCDF; import as CSV)",
             topics=_SECURE_DEV),
    Standard(standard_id="mil-std-882e", name="MIL-STD-882E: System Safety", publisher="US Department of Defense",
             access="public", obtain="DoD ASSIST database", topics={"software safety analysis": ["tests_pass",
                                                                                                "code_review"]}),
    Standard(standard_id="fedramp", name="FedRAMP Rev. 5 baselines (built on NIST SP 800-53 Rev. 5)",
             publisher="US General Services Administration", access="public", obtain="fedramp.gov (OSCAL available)"),
    Standard(standard_id="fips-199", name="FIPS 199: Standards for Security Categorization", publisher="NIST",
             access="public", obtain="csrc.nist.gov", note="Chooses the SP 800-53 baseline: low, moderate or high."),
    # Automotive
    Standard(standard_id="iso-26262", name="ISO 26262: Road vehicles - Functional safety", publisher="ISO",
             access="licensed", obtain="purchase from ISO", topics=_SAFETY, note="ASIL QM, A-D."),
    Standard(standard_id="iso-sae-21434", name="ISO/SAE 21434: Road vehicles - Cybersecurity engineering",
             publisher="ISO / SAE International", access="licensed", obtain="purchase from ISO or SAE",
             topics=_SECURE_DEV),
    Standard(standard_id="misra-c", name="MISRA C:2023 coding guidelines", publisher="The MISRA Consortium",
             access="licensed", obtain="purchase from misra.org.uk",
             topics={"coding guideline compliance": ["static_analysis"]},
             note="Needs a MISRA-capable analyzer; import its findings as analyzer results."),
    Standard(standard_id="unece-r155", name="UN Regulation No. 155 (cybersecurity) and No. 156 (software updates)",
             publisher="UNECE WP.29", access="public", obtain="unece.org",
             topics={"software update integrity": ["release_integrity"]}),
    # Industrial, energy, rail, nuclear, space
    Standard(standard_id="iec-61508", name="IEC 61508: Functional safety of E/E/PE safety-related systems",
             publisher="IEC", access="licensed", obtain="purchase from IEC", topics=_SAFETY, note="SIL 1-4."),
    Standard(standard_id="iec-62443-4-1", name="IEC 62443-4-1: Secure product development lifecycle requirements",
             publisher="IEC / ISA", access="licensed", obtain="purchase from IEC or ISA", topics=_SECURE_DEV),
    Standard(standard_id="nerc-cip", name="NERC Critical Infrastructure Protection reliability standards",
             publisher="NERC", access="public", obtain="nerc.com",
             topics={"configuration change management": ["change_control"],
                     "vulnerability assessment": ["vulnerability_scan"]}),
    Standard(standard_id="en-50716", name="EN 50716: Railway applications - Requirements for software development "
             "(succeeds EN 50128)", publisher="CENELEC", access="licensed", obtain="purchase from a national "
             "standards body", topics=_SAFETY, note="SIL 0-4."),
    Standard(standard_id="iec-60880", name="IEC 60880: Nuclear power plants - Software for category A functions",
             publisher="IEC", access="licensed", obtain="purchase from IEC", topics=_SAFETY),
    Standard(standard_id="nasa-npr-7150-2", name="NASA NPR 7150.2: NASA Software Engineering Requirements",
             publisher="NASA", access="public", obtain="nodis3.gsfc.nasa.gov", topics=_SAFETY,
             note="Software classes A-F; safety-critical software has added requirements."),
    Standard(standard_id="ecss-e-st-40c", name="ECSS-E-ST-40C: Space engineering - Software",
             publisher="European Cooperation for Space Standardization", access="registration",
             obtain="ecss.nl (free registration)", topics=_SAFETY),
    # Cross-industry
    Standard(standard_id="owasp-asvs", name="OWASP Application Security Verification Standard",
             publisher="OWASP Foundation", access="public", obtain="owasp.org (CC BY-SA)", topics=_SECURE_DEV),
    Standard(standard_id="sei-cert-c", name="SEI CERT C Coding Standard", publisher="Carnegie Mellon SEI",
             access="public", obtain="wiki.sei.cmu.edu", topics={"coding rules": ["static_analysis",
                                                                                  "dynamic_analysis"]}),
)}


# ----------------------------------------------------------------- profiles

_SSDF = CatalogUse(catalog_id="nist-800-218")
_CORE = ["tests_pass", "static_analysis", "no_embedded_secrets"]
_SECURE = [*_CORE, "dynamic_analysis", "flaw_remediation", "change_control", "code_review", "component_inventory",
           "audit_integrity"]
_SAFETY_GATE = ["tests_pass", "requirements_traceability", "static_analysis", "change_control", "code_review"]
_COVERAGE = OptionSpec(values=["none", *COVERAGE_CRITERIA], default="none",
                       meaning="structural coverage the gate requires (set it from your plan, not from FORGE)")

PROFILES: dict[str, Profile] = {p.profile_id: p for p in (
    Profile(profile_id="none", title="No compliance domain", summary="No gate; reports still available on request.",
            catalogs=[], gate_checks=[]),
    Profile(profile_id="general", title="General software (NIST SSDF)",
            summary="Secure development baseline for any software.", catalogs=[_SSDF], gate_checks=_CORE,
            standards=["owasp-asvs", "sei-cert-c"]),
    Profile(profile_id="federal", title="US federal information system (FISMA / FedRAMP)",
            summary="NIST SP 800-53 Rev. 5 baseline chosen by FIPS 199 impact level, plus the SSDF.",
            catalogs=[CatalogUse(catalog_id="nist-800-53r5", baseline="moderate"), _SSDF], gate_checks=_SECURE,
            options={"baseline": OptionSpec(values=["low", "moderate", "high"], default="moderate",
                                            meaning="FIPS 199 impact level")},
            standards=["fips-199", "fedramp"]),
    Profile(profile_id="defense", title="US defense (CUI, DFARS, CMMC)",
            summary="NIST SP 800-171 Rev. 3 for controlled unclassified information, SP 800-53 baseline and the "
            "SSDF.", catalogs=[CatalogUse(catalog_id="nist-800-171r3"),
                               CatalogUse(catalog_id="nist-800-53r5", baseline="moderate"), _SSDF],
            gate_checks=[*_SECURE, "vulnerability_scan"],
            options={"baseline": OptionSpec(values=["low", "moderate", "high"], default="moderate",
                                            meaning="SP 800-53 impact level for the system")},
            standards=["cmmc", "dfars-7012", "disa-asd-stig", "mil-std-882e"]),
    Profile(profile_id="avionics", title="Airborne software (DO-178C)",
            summary="Requirements-based testing, traceability and structural coverage by software level.",
            catalogs=[_SSDF], gate_checks=_SAFETY_GATE,
            options={"level": OptionSpec(values=["A", "B", "C", "D", "E"], default="A",
                                         meaning="DO-178C software level (A: catastrophic failure condition)")},
            standards=["do-178c", "do-330", "do-326a", "arp4754b", "faa-ac-20-115d"]),
    Profile(profile_id="medical", title="Medical device software (IEC 62304, FDA)",
            summary="Life-cycle, SOUP and SBOM evidence for device software.", catalogs=[_SSDF],
            gate_checks=[*_SAFETY_GATE, "component_inventory", "vulnerability_scan", "no_embedded_secrets"],
            options={"safety_class": OptionSpec(values=["A", "B", "C"], default="C",
                                                meaning="IEC 62304 software safety class"),
                     "coverage": _COVERAGE},
            standards=["iec-62304", "iso-14971", "iec-81001-5-1", "fda-premarket-cyber", "fdca-524b", "21-cfr-820",
                       "21-cfr-11"]),
    Profile(profile_id="banking", title="Banking and payments (PCI DSS, FFIEC, DORA, SOX)",
            summary="Secure development, change control and audit evidence for financial software.",
            catalogs=[CatalogUse(catalog_id="nist-800-53r5", baseline="moderate"), _SSDF],
            gate_checks=[*_SECURE, "vulnerability_scan"],
            options={"baseline": OptionSpec(values=["low", "moderate", "high"], default="moderate",
                                            meaning="SP 800-53 baseline used as the control reference")},
            standards=["pci-dss", "pci-sss", "ffiec-it", "nydfs-500", "eu-dora", "sox-itgc"]),
    Profile(profile_id="automotive", title="Road vehicles (ISO 26262, ISO/SAE 21434)",
            summary="Functional safety and cybersecurity engineering evidence.", catalogs=[_SSDF],
            gate_checks=_SAFETY_GATE,
            options={"asil": OptionSpec(values=["QM", "A", "B", "C", "D"], default="D", meaning="ISO 26262 ASIL"),
                     "coverage": _COVERAGE},
            standards=["iso-26262", "iso-sae-21434", "misra-c", "unece-r155"]),
    Profile(profile_id="industrial", title="Industrial control and energy (IEC 61508, IEC 62443, NERC CIP)",
            summary="Safety integrity and secure product development evidence.", catalogs=[_SSDF],
            gate_checks=[*_SAFETY_GATE, "vulnerability_scan"],
            options={"sil": OptionSpec(values=["1", "2", "3", "4"], default="3", meaning="IEC 61508 SIL"),
                     "coverage": _COVERAGE},
            standards=["iec-61508", "iec-62443-4-1", "nerc-cip"]),
    Profile(profile_id="rail", title="Railway software (EN 50716 / EN 50128)", summary="Railway software safety.",
            catalogs=[_SSDF], gate_checks=_SAFETY_GATE,
            options={"sil": OptionSpec(values=["0", "1", "2", "3", "4"], default="4", meaning="software SIL"),
                     "coverage": _COVERAGE},
            standards=["en-50716"]),
    Profile(profile_id="nuclear", title="Nuclear instrumentation and control (IEC 60880)",
            summary="Category A safety software.", catalogs=[_SSDF], gate_checks=_SAFETY_GATE,
            options={"coverage": _COVERAGE}, standards=["iec-60880"]),
    Profile(profile_id="space", title="Space flight software (NASA NPR 7150.2, ECSS)",
            summary="Flight and ground software engineering requirements.", catalogs=[_SSDF],
            gate_checks=_SAFETY_GATE,
            options={"software_class": OptionSpec(values=["A", "B", "C", "D", "E", "F"], default="A",
                                                  meaning="NASA software class"), "coverage": _COVERAGE},
            standards=["nasa-npr-7150-2", "ecss-e-st-40c"]),
)}

# DO-178C structural coverage by software level (levels D and E have no structural coverage objective).
AVIONICS_COVERAGE = {"A": "mcdc", "B": "decision", "C": "statement"}


def resolve(profile_id: str, options: dict[str, str] | None = None, *,
            extra_catalogs: list[str] | None = None) -> ResolvedProfile:
    """Apply a person's option choices to a profile. Unknown names or values are refused, never guessed."""
    profile = PROFILES.get(profile_id)
    if profile is None:
        raise ForgeError(ErrorCode.INVALID_REQUEST,
                         f"unknown compliance domain {profile_id!r}; choose one of {sorted(PROFILES)}")
    given = dict(options or {})
    unknown = sorted(set(given) - set(profile.options))
    if unknown:
        raise ForgeError(ErrorCode.INVALID_REQUEST, f"{profile_id} has no option {unknown}; options: "
                         f"{ {name: spec.values for name, spec in profile.options.items()} }")
    chosen: dict[str, str] = {}
    for name, spec in profile.options.items():
        value = str(given.get(name, spec.default))
        if value not in spec.values:
            raise ForgeError(ErrorCode.INVALID_REQUEST, f"{name} must be one of {spec.values}, not {value!r}")
        chosen[name] = value
    catalogs = [CatalogUse(catalog_id=use.catalog_id, baseline=chosen.get("baseline", use.baseline)
                           if use.baseline else None) for use in profile.catalogs]
    catalogs += [CatalogUse(catalog_id=catalog_id) for catalog_id in extra_catalogs or []]
    gate = list(profile.gate_checks)
    check_options: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    coverage = AVIONICS_COVERAGE.get(chosen.get("level", ""), "none") if profile_id == "avionics" \
        else chosen.get("coverage", "none")
    if profile_id == "avionics" and chosen["level"] in ("D", "E"):
        notes.append(f"level {chosen['level']} has no structural coverage objective")
        if chosen["level"] == "E":
            gate, notes = ["tests_pass"], [*notes, "level E software has no DO-178C objectives; only tests gate"]
    if coverage != "none":
        gate.append("structural_coverage")
        check_options["structural_coverage"] = {"coverage": coverage, "minimum_percent": 100.0}
        notes.append(f"structural coverage required: {coverage} at 100% (justify any uncovered code with a "
                     "waiver that names it)")
    return ResolvedProfile(profile_id=profile_id, title=profile.title, options=chosen, catalogs=catalogs,
                           gate_checks=gate, check_options=check_options,
                           standards=[STANDARDS[s] for s in profile.standards], notes=notes)


@cache
def bundled_catalog(catalog_id: str) -> CompactCatalog:
    if catalog_id not in BUNDLED_CATALOGS:
        raise KeyError(catalog_id)
    text = resources.files("forge.compliance").joinpath(f"catalogs/{catalog_id}.json").read_text("utf-8")
    return CompactCatalog.model_validate_json(text)


@cache
def bundled_baselines() -> dict[str, dict[str, Any]]:
    text = resources.files("forge.compliance").joinpath("catalogs/nist-800-53r5-baselines.json").read_text("utf-8")
    return json.loads(text)

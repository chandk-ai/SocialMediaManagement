"""Compliance scanner — Niche #4.

Per-industry rule packs that scan every Executor draft for forbidden
language patterns BEFORE the Critique agent decides whether to publish.
Each violation gets appended to ``EvaluationReport.flags`` for the matching
draft, which the existing Critique agent already treats as an automatic
escalate-to-human. The downstream effect: regulated orgs can't accidentally
publish a non-compliant post; everything that trips a rule pauses for a
human reviewer with the specific violation in the message.

This is an honest first pass — regex + required-presence checks. It catches
the obvious mistakes (the kind that get junior team members in trouble) but
it is **not a substitute for legal review**. The ``--llm`` mode adds a
second-pass LLM check; we surface that as a configurable extra so orgs can
trade latency vs catch-rate.

Profiles included out of the box:
  * **finra**       — broker-dealer / RIA marketing (no past-performance,
                      no guarantees, mandatory disclaimers)
  * **hipaa**       — healthcare (no PHI patterns, no diagnosis claims)
  * **fda**         — pharma / supplements (no off-label, no efficacy
                      claims without disclaimer)
  * **crypto**      — Web3 / digital asset marketing (no guaranteed
                      returns, mandatory "not financial advice")
  * **gdpr**        — generic privacy (no email-in-the-clear)
  * **none**        — explicit no-op (same as omitting compliance_profile)

A profile is just data — adding more is a one-file change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CompliancePattern:
    """One forbidden pattern with the message we want to surface."""
    pattern: re.Pattern[str]
    message: str
    severity: str = "violation"      # "violation" | "warning"


@dataclass(frozen=True, slots=True)
class ComplianceProfile:
    """A named pack of rules. Lookup is by case-insensitive name."""
    name: str
    label: str
    description: str
    forbidden: tuple[CompliancePattern, ...] = ()
    required_any: tuple[CompliancePattern, ...] = ()    # at least one must match
    docs_url: str = ""

    def scan(self, text: str) -> list[str]:
        """Return human-readable violation messages, or empty list if clean."""
        out: list[str] = []
        body = text or ""
        for rule in self.forbidden:
            m = rule.pattern.search(body)
            if m:
                snippet = body[max(0, m.start() - 12):m.end() + 12]
                out.append(
                    f"[{rule.severity}] {rule.message} "
                    f"(matched '…{snippet.strip()}…')"
                )
        # Required-any: at least ONE pattern in this group must match.
        if self.required_any and not any(r.pattern.search(body) for r in self.required_any):
            example = self.required_any[0].message
            out.append(f"[required] {example}")
        return out


# ── helpers ────────────────────────────────────────────────────────────────
def _ci(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ── profiles ──────────────────────────────────────────────────────────────
_PROFILES: dict[str, ComplianceProfile] = {
    "finra": ComplianceProfile(
        name="finra",
        label="FINRA / RIA marketing",
        description=(
            "Broker-dealer and registered investment advisor compliance. "
            "Blocks unqualified return promises, performance guarantees, and "
            "marketing without the standard disclaimer."
        ),
        forbidden=(
            CompliancePattern(_ci(r"\bguarantee[ds]?\s+(returns?|profits?|gains?)"),
                              "Guaranteed returns/profits are prohibited under FINRA Rule 2210."),
            CompliancePattern(_ci(r"\b(no|zero)\s+risk\b"),
                              "'No risk' / 'zero risk' claims violate FINRA marketing rules."),
            CompliancePattern(_ci(r"\bget rich\s+(quick|fast)\b"),
                              "'Get rich quick' framing is prohibited."),
            CompliancePattern(_ci(r"\b\d+%\s+(annual|monthly|guaranteed)\b"),
                              "Specific return claims without the required disclaimers are prohibited."),
            CompliancePattern(_ci(r"\bbest\s+(stock|investment|fund)\b"),
                              "Superlative claims ('best…') require substantiation per FINRA 2210(d)."),
            CompliancePattern(_ci(r"\bpast\s+performance.{0,40}guarantee"),
                              "Cannot link past performance to guaranteed future results."),
        ),
        required_any=(
            # When the post talks about returns/performance, at least one of
            # these disclaimer phrases must be present somewhere in the post.
            CompliancePattern(
                _ci(r"(past performance.{0,40}(not|no).{0,20}guarantee|"
                    r"investments?\s+may\s+lose|"
                    r"not\s+investment\s+advice)"),
                "Posts mentioning returns/performance must include a "
                "disclaimer like 'Past performance does not guarantee future "
                "results' or 'Not investment advice'.",
            ),
        ),
        docs_url="https://www.finra.org/rules-guidance/rulebooks/finra-rules/2210",
    ),

    "hipaa": ComplianceProfile(
        name="hipaa",
        label="HIPAA / healthcare",
        description=(
            "Healthcare marketing compliance. Blocks Protected Health "
            "Information (PHI) patterns and unqualified diagnostic / "
            "outcome claims."
        ),
        forbidden=(
            # Direct PHI patterns. These are blunt — false positives are
            # tolerable since the cost of leaking PHI is high.
            CompliancePattern(_ci(r"\bMRN[:\s]*\d{4,}"),
                              "Medical record numbers must never appear in marketing posts."),
            CompliancePattern(_ci(r"\bSSN[:\s]*\d{3}-?\d{2}-?\d{4}"),
                              "Social security numbers must never appear in marketing posts."),
            CompliancePattern(_ci(r"\bDOB[:\s]*\d{1,2}/\d{1,2}/\d{2,4}"),
                              "Patient date-of-birth values must never appear in marketing posts."),
            CompliancePattern(_ci(r"\bpatient\s+\w+\s+(was diagnosed|had|received)"),
                              "Specific patient narratives may be PHI even if names are altered."),
            CompliancePattern(_ci(r"\b(cure|cures)\s+(cancer|diabetes|alzheimer)"),
                              "Unqualified disease-cure claims violate FTC and FDA rules."),
            CompliancePattern(_ci(r"\b100%\s+(effective|safe|cure)"),
                              "Absolute efficacy/safety claims are prohibited in healthcare marketing."),
        ),
        docs_url="https://www.hhs.gov/hipaa/for-professionals/privacy/index.html",
    ),

    "fda": ComplianceProfile(
        name="fda",
        label="FDA / pharma + supplements",
        description=(
            "Pharma, biotech, and dietary-supplement marketing. Blocks "
            "off-label use claims and unqualified efficacy statements."
        ),
        forbidden=(
            CompliancePattern(_ci(r"\bcures?\s+(?!the common cold)"),
                              "Disease-cure claims require FDA approval evidence."),
            CompliancePattern(_ci(r"\bclinically\s+proven\s+to\s+(cure|treat|prevent)"),
                              "'Clinically proven' efficacy claims must cite the trial and FDA approval."),
            CompliancePattern(_ci(r"\bFDA[\s-]*approved"),
                              "FDA-approval claims require substantiation; if true, link the approval letter."),
            CompliancePattern(_ci(r"\boff[\s-]*label\b"),
                              "Direct mention of off-label use is prohibited in promotional content."),
            CompliancePattern(_ci(r"\b(miracle|breakthrough)\s+(cure|drug|treatment)"),
                              "Hyperbolic efficacy claims trigger FDA enforcement letters."),
        ),
        required_any=(
            CompliancePattern(
                _ci(r"(consult\s+your\s+(doctor|physician|pharmacist)|"
                    r"these statements have not been evaluated|"
                    r"see prescribing information|"
                    r"talk\s+to\s+a\s+healthcare\s+provider)"),
                "Pharma/supplement posts must include a 'consult your "
                "doctor' or 'these statements have not been evaluated' "
                "disclaimer.",
            ),
        ),
        docs_url="https://www.fda.gov/regulatory-information/search-fda-guidance-documents",
    ),

    "crypto": ComplianceProfile(
        name="crypto",
        label="Crypto / digital-asset marketing",
        description=(
            "Web3 and digital-asset marketing. Blocks guaranteed-return "
            "language and requires 'not financial advice' disclaimer."
        ),
        forbidden=(
            CompliancePattern(_ci(r"\bguaranteed\s+(returns?|gains?|yield|apy)"),
                              "Guaranteed crypto returns are SEC enforcement bait."),
            CompliancePattern(_ci(r"\b(\d+)x\s+(returns?|gains?|profit)"),
                              "Multiplier-return claims are prohibited."),
            CompliancePattern(_ci(r"\b(moon|to the moon|10x|100x)\b"),
                              "Speculative price-target language is high-risk under SEC scrutiny."),
            CompliancePattern(_ci(r"\bget\s+rich\b"),
                              "'Get rich' framing is prohibited."),
            CompliancePattern(_ci(r"\brisk[\s-]*free\b"),
                              "Any claim of 'risk-free' returns is prohibited."),
        ),
        required_any=(
            CompliancePattern(
                _ci(r"(not\s+(financial|investment)\s+advice|"
                    r"\bnfa\b|\bdyor\b|"
                    r"do\s+your\s+own\s+research)"),
                "Crypto posts must include 'Not financial advice', 'NFA', "
                "or 'DYOR' / 'Do your own research'.",
            ),
        ),
        docs_url="https://www.sec.gov/spotlight/cybersecurity-enforcement-actions",
    ),

    "gdpr": ComplianceProfile(
        name="gdpr",
        label="GDPR / privacy hygiene",
        description=(
            "Catch-all privacy hygiene check. Blocks email addresses and "
            "phone numbers that look like leaked personal data, plus a few "
            "common doxxing patterns."
        ),
        forbidden=(
            # Specific email addresses in marketing copy are usually a leak.
            CompliancePattern(_ci(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b"),
                              "Email addresses in posts are usually accidental data leaks."),
            CompliancePattern(_ci(r"\b\+?\d[\d\s().-]{8,}\b\s*(phone|cell|mobile)"),
                              "Phone numbers in posts are usually accidental data leaks."),
        ),
        docs_url="https://gdpr.eu/art-5-personal-data-processing/",
    ),

    "none": ComplianceProfile(
        name="none", label="None — disable compliance scanning",
        description="No-op profile. Same as omitting compliance_profile.",
    ),
}


def list_profiles() -> list[ComplianceProfile]:
    """Sorted profile list for the UI picker. ``none`` is filtered out
    because it's just the absence of a profile."""
    return [p for k, p in _PROFILES.items() if k != "none"]


def get_profile(name: str | None) -> ComplianceProfile | None:
    if not name:
        return None
    p = _PROFILES.get(name.lower().strip())
    if p is None or p.name == "none":
        return None
    return p


def scan_drafts(
    profile_name: str | None,
    drafts: Iterable[tuple[str, str]],          # iterable of (platform, text)
) -> dict[str, list[str]]:
    """Run the profile against every draft. Returns {platform: [violations]}.

    Empty dict when no profile is configured. Per-platform keys so the
    Critique agent can attach violations to the right ``EvaluationReport``.
    """
    profile = get_profile(profile_name)
    if profile is None:
        return {}
    out: dict[str, list[str]] = {}
    for platform, text in drafts:
        violations = profile.scan(text)
        if violations:
            out[platform] = violations
    return out

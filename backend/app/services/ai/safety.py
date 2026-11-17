"""P24-B — Safety : protection PII + défense anti prompt-injection.

redact_pii(text) : remplace emails, numéros de téléphone, cartes de crédit, SSN par des tokens
[EMAIL], [PHONE], [CARD], [SSN] AVANT l'envoi au provider. Les données personnelles des prospects
ne quittent jamais RevPulse en clair — le provider reçoit une version redactée.

detect_injection(text) : détecte les patterns classiques de prompt injection (ignore previous
instructions, system:, <|im_start|>, role:, etc.). Retourne (suspicious, reasons). Si suspicious,
le gateway marque la génération comme nécessitant validation humaine (require_human_validation=True)
— l'action externe (envoi e-mail, publication LinkedIn) est bloquée jusqu'à validation.

Ces défenses sont defense-in-depth — elles ne remplacent pas la validation humaine avant toute
action externe (principe P24-B + P25 agents).
"""
from __future__ import annotations

import re

# Patterns PII (regex). Ordre : cartes (plus spécifique) avant phone/email.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")  # 13-19 chiffres (Visa/MC/Amex)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){3,5}\d{2,4}")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")  # US SSN

# Patterns d'instruction suspecte (case-insensitive). Anti prompt-injection.
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions|prompts)", re.I),
    re.compile(r"you\s+are\s+(?:now|actually)\s+(?:a|an)\s+", re.I),  # role hijack
    re.compile(r"(?:^|\s)system\s*:", re.I),  # tentative de role system
    re.compile(r"<\|im_start\|>|<\|im_end\|>|<\|endoftext\|>"),  # tokens OpenAI
    re.compile(r"(?:reveal|show|print|dump)\s+(?:the|your)\s+(?:system\s+)?prompt", re.I),
    re.compile(r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a|an)\s+(?:jailbreak|root|admin)", re.I),
    re.compile(r"do\s+anything\s+now|DAN\s+mode", re.I),  # DAN attack
]


def redact_pii(text: str) -> tuple[str, int]:
    """Redacte les PII d'un texte. Retourne (texte_redacté, nb_redactions). Ordre : carte (plus
    spécifique) → email → phone → SSN (les replacements successifs ne se chevaillent pas car les
    patterns sont disjoints)."""
    if not text:
        return text, 0
    count = 0
    out = text
    out, n1 = _CARD_RE.subn("[CARD]", out)
    count += n1
    out, n2 = _EMAIL_RE.subn("[EMAIL]", out)
    count += n2
    out, n3 = _PHONE_RE.subn("[PHONE]", out)
    count += n3
    out, n4 = _SSN_RE.subn("[SSN]", out)
    count += n4
    return out, count


def detect_injection(text: str) -> tuple[bool, list[str]]:
    """Détecte les patterns de prompt injection. Retourne (suspicious, reasons). Si suspicious,
    le gateway require_human_validation=True (l'action externe est bloquée jusqu'à validation)."""
    if not text:
        return False, []
    reasons = []
    for pat in _INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            reasons.append(f"pattern suspecte : '{m.group(0)[:50]}'")
    return bool(reasons), reasons


def render_prompt(template: str, variables: dict) -> str:
    r"""Rend un template de prompt avec {{variables}} (P24-B). Remplace {{key}} par la valeur dans
    variables. Les variables manquantes sont laissées telles quelles ({{unknown}} → {{unknown}},
    pas d'erreur). Sécurisé : regex substitution (pas d'exec/eval). Les clés sont \w+ (alphanum +
    underscore) — anti-injection via nom de variable arbitraire."""
    if not template:
        return template
    def _replace(m):
        key = m.group(1)
        if key in (variables or {}):
            return str(variables[key])
        return m.group(0)  # laisse {{key}} tel quel si manquant
    return re.sub(r"\{\{(\w+)\}\}", _replace, template)

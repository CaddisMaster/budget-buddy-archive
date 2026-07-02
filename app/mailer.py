"""v10.8 — outbound email via Resend (the Weekly Email Digest's send path).

Deliberately named `mailer` (NOT `email`) so it never shadows Python's stdlib
`email` package. Mirrors app/ai.py's shape: a single isolated network seam
(`_call_resend`) that tests monkeypatch, a `mail_enabled()` gate twinned with
helpers.ai_enabled(), and a MailError that callers catch so a send failure never
breaks a batch or a request. The module NEVER touches the DB.
"""
import os

# From-address for every outbound mail. A verified Resend SUBDOMAIN sender
# (isolates the app's sending reputation from seandesmet.com). Env-overridable so
# it can change without a code edit.
DIGEST_FROM = os.getenv('DIGEST_FROM', 'Budget Buddy <noreply@budget.seandesmet.com>')


class MailError(Exception):
    """Raised on any send failure (no key, package missing, API error). Callers
    catch it so one failed send never aborts the digest batch or a request."""


def mail_enabled():
    """True when outbound email is configured — i.e. a RESEND_API_KEY is present.
    Templates/routes gate the opt-in UI on this so the feature stays invisible
    until the key is set (the same pattern as helpers.ai_enabled())."""
    return bool(os.getenv('RESEND_API_KEY'))


def send_email(to, subject, html, *, reply_to=None):
    """Send one HTML email via Resend. Returns the provider's message id.

    Raises MailError on any failure (no key, package missing, API error) so the
    caller can skip-and-continue. `to` is a single address string.
    """
    api_key = os.getenv('RESEND_API_KEY')
    if not api_key:
        raise MailError('RESEND_API_KEY is not set')
    to = (to or '').strip()
    if not to:
        raise MailError('No recipient')

    result = _call_resend(api_key, to, subject, html, reply_to)
    # Resend returns {"id": "..."} on success; anything else is a failure we
    # surface as MailError rather than silently "succeeding".
    msg_id = (result or {}).get('id') if isinstance(result, dict) else getattr(result, 'id', None)
    if not msg_id:
        raise MailError('Resend returned no message id')
    return msg_id


def _call_resend(api_key, to, subject, html, reply_to):
    """The single network call to Resend — isolated so tests stub it without
    hitting the API (CI has no key). Returns the provider response (dict-like);
    wraps any SDK, network, or missing-package error in MailError."""
    try:
        import resend
        resend.api_key = api_key
        params = {
            'from': DIGEST_FROM,
            'to': [to],
            'subject': subject,
            'html': html,
        }
        if reply_to:
            params['reply_to'] = reply_to
        return resend.Emails.send(params)
    except Exception as e:  # network, auth, malformed response, missing package
        raise MailError(str(e)) from e

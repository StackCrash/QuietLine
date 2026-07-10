# Deletion / Opt-Out Request Email Template

Used by `render_email.py` to produce a deletion request email for brokers that use the `email-request` mechanism.

Template variables (replaced at render time):
- `{broker_name}` — display name of the data broker
- `{full_name}` — user's full legal name
- `{phone_numbers}` — comma-separated list of phone numbers to remove
- `{listing_url}` — URL of the discovered listing (omit line if null)
- `{statutory_language}` — jurisdiction-specific statutory sentence from data/jurisdictions.json
- `{submitted_date}` — date of this request (YYYY-MM-DD)
- `{user_email}` — opt-out email address the user is sending from

---

**Subject:** Personal Data Deletion Request — {full_name}

To Whom It May Concern at {broker_name},

I am writing to request the deletion of all personal information your company holds about me.

**My identifying information:**
- Full name: {full_name}
- Phone number(s): {phone_numbers}
- Listing URL (if applicable): {listing_url}

{statutory_language}

Please:
1. Delete all personal information associated with me from your active database and any backups within the legally required timeframe.
2. Confirm deletion by replying to this email or sending a confirmation to {user_email}.
3. Cease any further sale, sharing, or processing of my personal information.

If you require additional information to process this request, please contact me at {user_email}. Do not use this contact information for any purpose other than processing this deletion request.

Date of request: {submitted_date}

Sincerely,
{full_name}

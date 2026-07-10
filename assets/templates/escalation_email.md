# Escalation / Follow-Up Email Template

Used by `render_email.py` when `deadlines.py` flags a case past its statutory response deadline.

Template variables:
- `{broker_name}` — display name of the data broker
- `{full_name}` — user's full legal name
- `{phone_numbers}` — comma-separated list of phone numbers
- `{listing_url}` — URL of the listing (omit line if null)
- `{submitted_date}` — date of the original request (YYYY-MM-DD)
- `{deadline_date}` — date the statutory response was due (YYYY-MM-DD)
- `{days_overdue}` — number of days past deadline
- `{escalation_language}` — jurisdiction-specific escalation sentence from data/jurisdictions.json
- `{user_email}` — opt-out email address
- `{attorney_general_url}` — URL to file complaint with the relevant state AG (if known)

---

**Subject:** FOLLOW-UP: Personal Data Deletion Request — {full_name} — Response Overdue

To Whom It May Concern at {broker_name},

On {submitted_date}, I submitted a formal request for deletion of all personal information your company holds about me. As of today, {days_overdue} days have passed since your required response deadline of {deadline_date}. I have not received a confirmation of deletion.

{escalation_language}

**My identifying information:**
- Full name: {full_name}
- Phone number(s): {phone_numbers}
- Original listing URL: {listing_url}

I am reiterating my request that you:
1. Immediately delete all personal information associated with me from your database.
2. Confirm deletion by replying to this email or sending confirmation to {user_email}.
3. Explain in writing why my original request was not processed within the required timeframe.

If I do not receive confirmation of deletion within 10 business days of this follow-up, I will file a formal complaint with the relevant regulatory authority.

Date of original request: {submitted_date}
Date of this follow-up: {followup_date}

Sincerely,
{full_name}
{user_email}

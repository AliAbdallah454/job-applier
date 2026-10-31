# Mail

Connects your mailbox to the app, for two things:

- **Application tracking:** confirmations, updates, interview requests, offers and rejections
  become entries on the **Applications** page, each with its history of emails.
- **Job emails:** LinkedIn job alert emails become groups of jobs on the **Job emails** page,
  and any of those jobs can be tailored to like one from the catalogue.

It runs inside the local API: every 15 minutes while the API is running, and when you choose
**Check mail now**.

## Setup

1. Turn on 2-Step Verification for your Google account, then create an
   [app password](https://myaccount.google.com/apppasswords).
2. Add these to `.env` in the repository root. It is ignored by Git, so never put them anywhere
   else:

   ```sh
   MAIL_ADDRESS=you@gmail.com
   MAIL_APP_PASSWORD=your app password
   # MAIL_IMAP_HOST=imap.gmail.com   # Only for a provider other than Gmail.
   ```

3. Deploy the `Application` stack, which adds the `Mail` table, and restart the API.

Only the `MAIL_*` values are read from `.env`. Until both the settings and the table exist, the
API runs normally and the two pages say what is missing. To disconnect, remove the two lines or
revoke the app password.

## What happens to your mail

1. The sync opens `INBOX` read-only, so it cannot change, delete or mark anything. Mail that
   Gmail filters skip the inbox for is not seen.
2. **Tracking starts at the first sync.** That sync reads nothing: it notes when it ran and where
   the inbox ends, and mail already there is never read, even if the mail server later renumbers
   the folder. The Applications and Job emails pages show that date. An application from before
   then appears only when a new email about it arrives, at whatever stage that email gives.
3. Each later sync reads the sender, subject and date of new mail. `triage.py` decides from those
   alone which messages are worth opening. Other mail is never downloaded.
4. Possible application mail is sent to Amazon Bedrock (Claude Haiku), at most 6,000 characters
   of it, and the model says whether it is about an application, and its employer, role and
   stage. Email is treated as untrusted data; the model can only return those fields.
5. Job alerts are parsed in `linkedin.py`, with no model involved.

**Message bodies are never stored.** The table keeps the subject, date and stage of
each application event, the jobs listed in alerts and a hash of each message read.

A failed message stops the sync, which continues from that message next time. Each message is
applied once, so running a sync again changes nothing.

## Matching applications

Emails join an existing application when the employer's name matches (ignoring case, accents and
words like Inc or SAS) and the role shares most of its words, or the email names no role.
`matching.py` holds these rules, which can get it wrong: two roles at one employer can merge, and
an email naming no employer becomes its own "Unknown company" entry. The status is always that of
the newest event, so any status can be corrected on the page. Removing an entry keeps its emails
marked as read, so it does not come back.

## Jobs you opened

Opening a job post from the Jobs, Job emails or Tailored CVs pages adds it to a pool of jobs you
are interested in. That is not an application, and it never creates one by itself. When an email
arrives about an application, it is compared with the pool by the same employer and role rules,
using jobs opened before the email. A match:

- links the application to that job, so the job shows its status wherever it is listed
- fills in the role when the email does not name one
- takes the job out of the pool

This also catches jobs you applied to without tailoring a CV.

A job opened at least a day ago, up to 30 days, that no email has matched appears under **Did you
apply?** on the Applications page. **I applied** creates the application, and **No** removes the
job from the pool. Only links opened inside the app can be noted, so a job you found elsewhere is
known only from its emails.

An application links to the tailored CV for its job, or else to one with the same employer and a
matching role, and that CV then shows the application's status.

## LinkedIn

An alert email gives each job's title, company and place. The description is read from the
public job page, without logging in, only when you choose **Tailor my CV**, and once per job.
Only the job's number is taken from the email: the address is built from it, so a link in an
email is never followed.

LinkedIn's terms restrict automated access, so nothing here fetches jobs in bulk, and the page can
refuse the request. When it does, paste the description from the job's page into the box that
opens. If LinkedIn changes its alert layout, a group shows that no jobs could be read.
